import asyncio
import math
import os
import platform
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.guardian import CHANNEL_DANGEROUS, ROLE_DANGEROUS
from cogs.ideas import IdeaReviewView
from cogs.setup import DASHBOARD_SECTIONS, build_dashboard_embeds
from cogs.submissions import ReviewView, VideoPanelView, approved_count, creator_role
from cogs.tickets import CloseTicketView, TicketPanelView
from core.bot import UKCRBot
from core.config import BRAND_COLOUR, config
from core.console import console_stats, get_logger
from core.constants import CREATOR_TIERS, LOG_CHANNELS
from core.helpers import CONTENT_TZ, clip, discord_ts, fmt_duration, is_staff, load_timezone, now_iso

try:
    import psutil
except ImportError:
    psutil = None

log = get_logger("diagnostics")

OK, WARN, FAIL = "✅", "⚠️", "❌"
GROUPS = ["Core", "Permissions", "Roles", "Channels", "Database", "Background tasks", "Commands & functions", "Extensions"]
DB_TABLES = [
    "tickets", "video_submissions", "member_events", "log_channels",
    "bot_settings", "content_schedule", "content_ideas", "hashtag_packs",
]
REQUIRED_PERMISSIONS = [
    ("view_channel", "see channels", True),
    ("send_messages", "post messages", True),
    ("embed_links", "send embeds", True),
    ("manage_roles", "give the Citizen and creator roles", True),
    ("manage_channels", "lock Bulletin and create channels", True),
    ("read_message_history", "refresh the dashboard", False),
    ("view_audit_log", "show who made a change in the logs", False),
    ("add_reactions", "idea voting reactions", False),
    ("attach_files", "ticket transcripts", False),
]
LOOPS = [
    ("Pending video reminders", "Submissions", "pending_reminder_loop"),
    ("Content calendar", "Calendar", "calendar_loop"),
    ("Citizen role sweep", "Guardian", "citizen_role_loop"),
    ("Bulletin lock", "Guardian", "bulletin_lock_loop"),
    ("Dangerous permission lock", "Guardian", "permission_guard_loop"),
    ("Live status", "Status", "status_loop"),
]


class Report:
    def __init__(self) -> None:
        self.groups: dict = {name: [] for name in GROUPS}

    def add(self, group: str, status: str, name: str, detail: str = "") -> None:
        self.groups[group].append((status, name, detail))

    def count(self, status: str) -> int:
        return sum(1 for items in self.groups.values() for item in items if item[0] == status)

    def worst(self, group: Optional[str] = None) -> str:
        items = self.groups[group] if group else [i for v in self.groups.values() for i in v]
        statuses = {i[0] for i in items}
        if FAIL in statuses:
            return FAIL
        if WARN in statuses:
            return WARN
        return OK


def fmt_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def can_post(channel: discord.abc.GuildChannel, me: discord.Member) -> bool:
    perms = channel.permissions_for(me)
    return perms.view_channel and perms.send_messages and perms.embed_links


class Diagnostics(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    async def check_core(self, report: Report, guild: discord.Guild, interaction: discord.Interaction) -> None:
        g = "Core"
        latency = self.bot.latency
        if math.isnan(latency) or math.isinf(latency):
            report.add(g, FAIL, "Gateway connection", "No heartbeat latency - the bot isn't connected properly.")
        else:
            ms = round(latency * 1000)
            report.add(g, WARN if ms > 300 else OK, "Gateway latency", f"{ms} ms")

        start = time.perf_counter()
        await self.bot.fetch_guild(guild.id)
        rest_ms = round((time.perf_counter() - start) * 1000)
        report.add(g, WARN if rest_ms > 1500 else OK, "Discord API round trip", f"{rest_ms} ms")

        if interaction.guild_id != config.guild_id:
            report.add(g, WARN, "Server ID", f"This server (`{interaction.guild_id}`) isn't the configured `GUILD_ID` (`{config.guild_id}`).")
        else:
            report.add(g, OK, "Server ID", "Matches `GUILD_ID`")

        intents = self.bot.intents
        report.add(g, OK if intents.members else FAIL, "Server Members intent", "On" if intents.members else "Off - auto role, joins/leaves and the role sweep won't work.")
        if intents.message_content:
            report.add(g, OK, "Message Content intent", "On")
        else:
            report.add(g, WARN, "Message Content intent", "Off - edit/delete logs and ticket transcripts can't show message text.")

        cached, total = len(guild.members), guild.member_count or 0
        if total and cached < total:
            report.add(g, WARN, "Member cache", f"{cached}/{total} members cached - the role sweep can only see cached members.")
        else:
            report.add(g, OK, "Member cache", f"{cached}/{total or cached} members cached")

        if CONTENT_TZ is timezone.utc and config.content_tz.upper() != "UTC":
            report.add(g, WARN, "Timezone", f"Couldn't load `{config.content_tz}`, using UTC (Windows: `pip install tzdata`).")
        else:
            report.add(g, OK, "Timezone", config.content_tz)

    async def check_permissions(self, report: Report, guild: discord.Guild) -> None:
        g = "Permissions"
        perms = guild.me.guild_permissions
        if perms.administrator:
            report.add(g, OK, "Administrator", "The bot has every permission.")
            return
        missing = 0
        for name, purpose, critical in REQUIRED_PERMISSIONS:
            if not getattr(perms, name):
                missing += 1
                report.add(g, FAIL if critical else WARN, f"Missing {name.replace('_', ' ').title()}", f"Needed to {purpose}.")
        if not missing:
            report.add(g, OK, "Bot permissions", f"All {len(REQUIRED_PERMISSIONS)} required permissions present")

    async def check_roles(self, report: Report, guild: discord.Guild) -> None:
        g = "Roles"
        citizen = guild.get_role(config.member_role_id) if config.member_role_id else None
        if citizen is None:
            report.add(g, FAIL, "UKCR Citizen role", f"Not found (`{config.member_role_id}`). Check `MEMBER_ROLE_ID`.")
        else:
            if citizen.is_assignable():
                report.add(g, OK, "UKCR Citizen role", f"{citizen.mention} found and the bot can assign it")
            else:
                report.add(g, FAIL, "UKCR Citizen role", f"{citizen.mention} found but the bot can't assign it - move the bot's role above it and give it Manage Roles.")
            missing = [m for m in guild.members if not m.bot and citizen not in m.roles]
            if missing:
                report.add(g, WARN, "Citizen coverage", f"{len(missing)} member(s) don't have the role yet (the sweep or `/syncroles` fixes this).")
            else:
                report.add(g, OK, "Citizen coverage", "Every member has the role")

        staff = guild.get_role(config.staff_role_id) if config.staff_role_id else None
        if staff is None:
            report.add(g, FAIL, "Staff role", f"Not found (`{config.staff_role_id}`). Check `STAFF_ROLE_ID`.")
        else:
            report.add(g, OK, "Staff role", staff.mention)

        missing_tiers = []
        for threshold, name, _ in CREATOR_TIERS:
            if await creator_role(self.bot, guild, threshold, name) is None:
                missing_tiers.append(name)
        if missing_tiers:
            report.add(g, WARN, "Creator roles", f"Missing: {', '.join(missing_tiers)}. Run `/setupcontent`.")
        else:
            report.add(g, OK, "Creator roles", ", ".join(name for _, name, _ in CREATOR_TIERS))

    async def check_channels(self, report: Report, guild: discord.Guild) -> None:
        g = "Channels"
        me = guild.me
        db = self.bot.db

        video = guild.get_channel(config.video_channel_id) if config.video_channel_id else None
        if video is None:
            report.add(g, FAIL, "Video channel", f"Not found (`{config.video_channel_id}`).")
        elif not can_post(video, me):
            report.add(g, FAIL, "Video channel", f"{video.mention} - the bot can't post embeds there.")
        else:
            report.add(g, OK, "Video channel", video.mention)

        for label, cid in (("Ticket category", config.ticket_category_id), ("Log category", config.log_category_id)):
            ch = guild.get_channel(cid) if cid else None
            if not isinstance(ch, discord.CategoryChannel):
                report.add(g, FAIL, label, f"Not found (`{cid}`).")
            elif not ch.permissions_for(me).manage_channels:
                report.add(g, WARN, label, f"**{ch.name}** found but the bot can't manage channels in it.")
            else:
                report.add(g, OK, label, f"**{ch.name}** ({len(ch.channels)} channels)")

        missing, blocked = [], []
        for key in LOG_CHANNELS:
            cid = await db.log_channel_id(guild.id, key)
            ch = guild.get_channel(cid) if cid else None
            if ch is None:
                missing.append(key)
            elif not can_post(ch, me):
                blocked.append(ch.name)
        if missing:
            report.add(g, WARN, "Log channels", f"{len(LOG_CHANNELS) - len(missing)}/{len(LOG_CHANNELS)} set up. Missing: {', '.join(missing)}. Run `/setuplogs`.")
        elif blocked:
            report.add(g, FAIL, "Log channels", f"The bot can't post in: {', '.join(blocked)}")
        else:
            report.add(g, OK, "Log channels", f"All {len(LOG_CHANNELS)} present and writable")

        content_status, content_missing = [], False
        for label, key in (("showcase", "showcase_channel"), ("ideas", "ideas_channel"), ("calendar", "calendar_channel")):
            cid = await db.setting_get(guild.id, key)
            ch = guild.get_channel(cid) if cid else None
            ok = ch is not None and can_post(ch, me)
            content_missing = content_missing or not ok
            content_status.append(f"{label} {OK if ok else WARN}")
        report.add(g, WARN if content_missing else OK, "Content channels", " · ".join(content_status) + (" - run `/setupcontent`" if content_missing else ""))

        dash_id = await db.setting_get(guild.id, "dashboard_channel")
        dash = guild.get_channel(dash_id) if dash_id else None
        report.add(g, OK if dash else WARN, "Dashboard channel", dash.mention if dash else "Not created yet. Run `/setupdashboard`.")

        commands_channel = guild.get_channel(config.commands_channel_id) if config.commands_channel_id else None
        if commands_channel is None:
            report.add(g, FAIL, "Commands channel", f"Not found (`{config.commands_channel_id}`). Commands are blocked everywhere else.")
        elif not can_post(commands_channel, me):
            report.add(g, WARN, "Commands channel", f"{commands_channel.mention} - the bot can't post embeds there.")
        else:
            report.add(g, OK, "Commands channel", f"{commands_channel.mention} (staff bypass {'on' if config.commands_staff_bypass else 'off'})")

        exposed = []
        citizen = guild.get_role(config.member_role_id) if config.member_role_id else None
        for role in filter(None, (guild.default_role, citizen)):
            role_bad = [n for n in ROLE_DANGEROUS if getattr(role.permissions, n)]
            if role_bad:
                exposed.append(f"{role.name} (role): {', '.join(role_bad[:4])}")
            for target in guild.channels:
                chan_bad = [n for n in CHANNEL_DANGEROUS if getattr(target.permissions_for(role), n)]
                if chan_bad:
                    exposed.append(f"{role.name} in {target.name}: {', '.join(chan_bad[:3])}")
                    break
        if exposed:
            report.add(g, WARN, "Dangerous permissions", "Still exposed: " + "; ".join(exposed[:4]) + " (the lock re-applies every 60s).")
        else:
            report.add(g, OK, "Dangerous permissions", "@everyone and Citizens have none, in any channel")

        bulletin = guild.get_channel(config.bulletin_category_id) if config.bulletin_category_id else None
        if not isinstance(bulletin, discord.CategoryChannel):
            report.add(g, FAIL, "Bulletin category", f"Not found (`{config.bulletin_category_id}`).")
        else:
            unlocked = []
            for target in [bulletin, *bulletin.channels]:
                for role in filter(None, (guild.default_role, citizen)):
                    if target.permissions_for(role).send_messages:
                        unlocked.append(target.name)
                        break
            if unlocked:
                report.add(g, WARN, "Bulletin lock", f"Still writable: {', '.join(unlocked[:6])} (the lock loop re-applies every 60s).")
            else:
                report.add(g, OK, "Bulletin lock", f"Read-only for @everyone and Citizens ({len(bulletin.channels)} channels)")

    async def check_database(self, report: Report, guild: discord.Guild) -> None:
        g = "Database"
        db = self.bot.db
        path = config.db_path
        if not path.exists():
            report.add(g, FAIL, "Database file", "Not found.")
            return
        writable = os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)
        report.add(g, OK if writable else FAIL, "Database file", f"`{path.name}` · {fmt_bytes(path.stat().st_size)}" + ("" if writable else " · not writable!"))

        row = await db.one("PRAGMA integrity_check")
        result = row[0] if row else "no result"
        report.add(g, OK if result == "ok" else FAIL, "Integrity check", "ok" if result == "ok" else str(result))

        counts, broken = [], []
        for table in DB_TABLES:
            try:
                counts.append(f"{table} **{await db.scalar(f'SELECT COUNT(*) FROM {table}')}**")
            except Exception as exc:
                broken.append(f"{table} ({exc})")
        if broken:
            report.add(g, FAIL, "Tables", "Problem with: " + ", ".join(broken))
        else:
            report.add(g, OK, "Tables", f"All {len(DB_TABLES)} readable · " + " · ".join(counts))

        columns = [r["name"] for r in await db.all("PRAGMA table_info(video_submissions)")]
        has = "last_reminded_at" in columns
        report.add(g, OK if has else FAIL, "Schema migration", "`last_reminded_at` column present" if has else "`last_reminded_at` column missing - restart the bot to run the migration.")

        start = time.perf_counter()
        await db.setting_set(guild.id, "debug_probe", 42)
        wrote = await db.setting_get(guild.id, "debug_probe") == 42
        await db.run("DELETE FROM bot_settings WHERE guild_id=? AND key=?", (guild.id, "debug_probe"))
        cleaned = await db.setting_get(guild.id, "debug_probe") is None
        ms = round((time.perf_counter() - start) * 1000)
        report.add(g, OK if wrote and cleaned else FAIL, "Write/read/delete test", f"{ms} ms" if wrote and cleaned else "Failed")

        open_tickets = await db.all("SELECT channel_id FROM tickets WHERE guild_id=? AND status='open'", (guild.id,))
        stale = [r for r in open_tickets if guild.get_channel(r["channel_id"]) is None]
        if stale:
            report.add(g, WARN, "Open tickets", f"{len(stale)} of {len(open_tickets)} point at channels that no longer exist.")
        else:
            report.add(g, OK, "Open tickets", f"{len(open_tickets)} open, all channels exist")

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=config.pending_reminder_hours)).isoformat()
        old = await db.scalar("SELECT COUNT(*) FROM video_submissions WHERE guild_id=? AND status='pending' AND submitted_at <= ?", (guild.id, cutoff))
        report.add(g, WARN if old else OK, "Pending videos", f"{old} waiting longer than {config.pending_reminder_hours}h" if old else "Nothing overdue for review")

        overdue = await db.scalar("SELECT COUNT(*) FROM content_schedule WHERE guild_id=? AND status='scheduled' AND due_at < ?", (guild.id, now_iso()))
        report.add(g, WARN if overdue else OK, "Content calendar", f"{overdue} scheduled post(s) overdue" if overdue else "Nothing overdue")

    async def check_tasks(self, report: Report) -> None:
        g = "Background tasks"
        for name, cog_name, attr in LOOPS:
            cog = self.bot.get_cog(cog_name)
            loop = getattr(cog, attr, None) if cog else None
            if loop is None:
                report.add(g, FAIL, name, f"The {cog_name} extension isn't loaded.")
            elif loop.failed():
                report.add(g, FAIL, name, "The loop crashed and stopped. Restart the bot.")
            elif not loop.is_running():
                report.add(g, FAIL, name, "Not running.")
            else:
                nxt = loop.next_iteration
                report.add(g, OK, name, f"Running · next run {discord.utils.format_dt(nxt, 'R')}" if nxt else "Running")
        views = len(self.bot.persistent_views)
        report.add(g, OK if views >= 5 else WARN, "Persistent views", f"{views} registered (buttons and dropdowns survive restarts)")

    async def check_commands(self, report: Report, guild: discord.Guild) -> None:
        g = "Commands & functions"
        tree = self.bot.tree
        leaves = [c for c in tree.walk_commands() if isinstance(c, app_commands.Command)]
        top_level = {c.name for c in tree.get_commands()}
        report.add(g, OK, "Loaded commands", f"{len(top_level)} top-level, {len(leaves)} runnable (including subcommands)")

        bad_description = [c.qualified_name for c in leaves if not 1 <= len(c.description) <= 100]
        not_async = [c.qualified_name for c in leaves if not asyncio.iscoroutinefunction(c.callback)]
        if bad_description or not_async:
            problems = []
            if bad_description:
                problems.append("bad description length: " + ", ".join(bad_description))
            if not_async:
                problems.append("not async: " + ", ".join(not_async))
            report.add(g, FAIL, "Command definitions", "; ".join(problems))
        else:
            report.add(g, OK, "Command definitions", f"All {len(leaves)} have valid descriptions and async handlers")

        remote = await tree.fetch_commands(guild=discord.Object(id=guild.id))
        remote_names = {c.name for c in remote}
        not_synced = sorted(top_level - remote_names)
        stale = sorted(remote_names - top_level)
        if not_synced:
            report.add(g, FAIL, "Registered with Discord", f"Not synced: {', '.join('/' + n for n in not_synced)}. Restart the bot.")
        elif stale:
            report.add(g, WARN, "Registered with Discord", f"Discord still has removed command(s): {', '.join('/' + n for n in stale)}")
        else:
            report.add(g, OK, "Registered with Discord", f"All {len(top_level)} commands are live in this server")

        listed = {entry.split()[0].lstrip("/") for _, _, entries in DASHBOARD_SECTIONS for entry, _ in entries}
        not_listed = sorted(top_level - listed)
        not_real = sorted(listed - top_level)
        if not_listed or not_real:
            parts = []
            if not_listed:
                parts.append("missing from the dashboard: " + ", ".join("/" + n for n in not_listed))
            if not_real:
                parts.append("listed but don't exist: " + ", ".join("/" + n for n in not_real))
            report.add(g, WARN if not not_real else FAIL, "Dashboard list", "; ".join(parts))
        else:
            report.add(g, OK, "Dashboard list", "Matches the real commands exactly")

        try:
            embeds = build_dashboard_embeds()
            too_big = [e for e in embeds if len(e) > 6000 or len(e.description or "") > 4096]
            report.add(g, FAIL if too_big else OK, "Dashboard embeds", "An embed exceeds Discord's size limits." if too_big else f"{len(embeds)} embeds build correctly")
        except Exception as exc:
            report.add(g, FAIL, "Dashboard embeds", f"{type(exc).__name__}: {exc}")

        view_classes = [TicketPanelView, CloseTicketView, VideoPanelView, ReviewView, IdeaReviewView]
        broken_views = []
        for cls in view_classes:
            try:
                cls()
            except Exception as exc:
                broken_views.append(f"{cls.__name__} ({type(exc).__name__})")
        report.add(g, FAIL if broken_views else OK, "Panels & buttons", ("Failed: " + ", ".join(broken_views)) if broken_views else f"All {len(view_classes)} views build correctly")

        helpers = [
            ("is_staff", lambda: is_staff(guild.me)),
            ("discord_ts", lambda: discord_ts(now_iso())),
            ("fmt_duration", lambda: fmt_duration(timedelta(hours=1, minutes=5))),
            ("clip", lambda: clip("x" * 2000)),
            ("load_timezone", lambda: load_timezone(config.content_tz)),
        ]
        failed = []
        for name, func in helpers:
            try:
                func()
            except Exception as exc:
                failed.append(f"{name} ({type(exc).__name__})")
        try:
            await self.bot.db.setting_get(guild.id, "dashboard_channel")
            await approved_count(self.bot, guild.id, 0)
            await self.bot.db.log_channel_ids(guild.id)
        except Exception as exc:
            failed.append(f"database helpers ({type(exc).__name__})")
        report.add(g, FAIL if failed else OK, "Helper functions", ("Failed: " + ", ".join(failed)) if failed else "All ran without errors")

    async def check_extensions(self, report: Report) -> None:
        g = "Extensions"
        loaded = self.bot.loaded_extensions
        report.add(g, OK if loaded else FAIL, "Loaded", f"{len(loaded)}: " + ", ".join(e.split('.')[-1] for e in loaded))
        for extension, error in self.bot.failed_extensions.items():
            report.add(g, FAIL, extension, clip(error, 200))
        report.add(g, OK, "Cogs", ", ".join(sorted(self.bot.cogs)) or "none")

    async def run_checks(self, guild: discord.Guild, interaction: discord.Interaction) -> Report:
        report = Report()
        sections = [
            ("Core", lambda: self.check_core(report, guild, interaction)),
            ("Permissions", lambda: self.check_permissions(report, guild)),
            ("Roles", lambda: self.check_roles(report, guild)),
            ("Channels", lambda: self.check_channels(report, guild)),
            ("Database", lambda: self.check_database(report, guild)),
            ("Background tasks", lambda: self.check_tasks(report)),
            ("Commands & functions", lambda: self.check_commands(report, guild)),
            ("Extensions", lambda: self.check_extensions(report)),
        ]
        for group, func in sections:
            try:
                await func()
            except Exception as exc:
                log.exception("Debug section %r crashed", group)
                report.add(group, FAIL, "Check crashed", f"{type(exc).__name__}: {exc}")
        return report

    def console_embed(self, guild: discord.Guild, cpu: Optional[float]) -> discord.Embed:
        now = datetime.now(timezone.utc)
        bot = self.bot
        embed = discord.Embed(title="🖥️ Console statistics", colour=BRAND_COLOUR, timestamp=now)
        embed.add_field(name="Uptime", value=f"{fmt_duration(now - bot.started_at)}\nSince {discord.utils.format_dt(bot.started_at, 'f')}", inline=True)
        latency = bot.latency
        embed.add_field(name="Latency", value="n/a" if math.isnan(latency) or math.isinf(latency) else f"{round(latency * 1000)} ms", inline=True)
        embed.add_field(name="Runtime", value=f"Python {platform.python_version()}\ndiscord.py {discord.__version__}\n{platform.system()} {platform.release()}", inline=True)

        process = [f"PID {os.getpid()}", f"Threads {threading.active_count()}", f"Async tasks {len(asyncio.all_tasks())}"]
        if psutil is not None:
            proc = psutil.Process()
            process.insert(1, f"RAM {fmt_bytes(proc.memory_info().rss)}")
            if cpu is not None:
                process.insert(2, f"CPU {cpu:.1f}%")
        else:
            process.append("Install `psutil` for RAM/CPU")
        embed.add_field(name="Process", value="\n".join(process), inline=True)
        embed.add_field(
            name="Cache",
            value=f"Servers {len(bot.guilds)}\nMembers {len(guild.members)}\nChannels {len(guild.channels)}\nRoles {len(guild.roles)}\nMessages {len(bot.cached_messages)}",
            inline=True,
        )
        db_size = fmt_bytes(config.db_path.stat().st_size) if config.db_path.exists() else "n/a"
        c = console_stats.counts
        embed.add_field(
            name="Console log (since start)",
            value=f"Info {c.get('INFO', 0)}\nWarnings {c.get('WARNING', 0)}\nErrors {c.get('ERROR', 0) + c.get('CRITICAL', 0)}\nDatabase {db_size}",
            inline=True,
        )
        recent = list(console_stats.recent)[-6:]
        if recent:
            text = "\n".join(
                f"{discord.utils.format_dt(ts, 'T')} **{level}** `{name}` {clip(message.replace(chr(10), ' '), 110)}"
                for ts, level, name, message in recent
            )
        else:
            text = "No warnings or errors since the bot started 🎉"
        embed.add_field(name="Recent warnings & errors", value=clip(text), inline=False)
        return embed

    async def send_batches(self, interaction: discord.Interaction, embeds: list) -> None:
        batch, size = [], 0
        for embed in embeds:
            length = len(embed)
            if batch and (size + length > 5500 or len(batch) >= 10):
                await interaction.followup.send(embeds=batch, ephemeral=True)
                batch, size = [], 0
            batch.append(embed)
            size += length
        if batch:
            await interaction.followup.send(embeds=batch, ephemeral=True)

    @app_commands.command(name="debug", description="Full bot health check: console statistics and a test of every system (admin)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: i.guild_id)
    async def debug(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        started = time.perf_counter()
        if psutil is not None:
            psutil.Process().cpu_percent(None)

        report = await self.run_checks(guild, interaction)

        cpu = psutil.Process().cpu_percent(None) if psutil is not None else None
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        ok, warn, fail = report.count(OK), report.count(WARN), report.count(FAIL)
        log.info("Debug run by %s: %d ok, %d warnings, %d failures in %d ms", interaction.user, ok, warn, fail, elapsed_ms)

        worst = report.worst()
        colour = {OK: discord.Colour.green(), WARN: discord.Colour.orange(), FAIL: discord.Colour.red()}[worst]
        headline = {
            OK: "Everything checks out.",
            WARN: "Working, but a few things need attention.",
            FAIL: "Something is broken - see the failures below.",
        }[worst]
        summary = discord.Embed(title=f"{worst} UKCR Bot - Debug report", description=f"**{headline}**", colour=colour, timestamp=datetime.now(timezone.utc))
        summary.add_field(name="Passed", value=str(ok), inline=True)
        summary.add_field(name="Warnings", value=str(warn), inline=True)
        summary.add_field(name="Failures", value=str(fail), inline=True)
        problems = [f"{status} **{group}: {name}**" for group, items in report.groups.items() for status, name, _ in items if status != OK]
        problems.sort(key=lambda line: 0 if line.startswith(FAIL) else 1)
        if problems:
            extra = f"\n…and {len(problems) - 10} more" if len(problems) > 10 else ""
            summary.add_field(name="Needs attention", value=clip("\n".join(problems[:10]) + extra), inline=False)
        summary.set_footer(text=f"Completed in {elapsed_ms} ms")

        embeds = [summary, self.console_embed(guild, cpu)]
        for group, items in report.groups.items():
            lines = [f"{status} **{name}**" + (f"\n└ {detail}" if detail else "") for status, name, detail in items]
            embeds.append(discord.Embed(title=f"{report.worst(group)} {group}", description=clip("\n".join(lines) or "Nothing to check.", 4000), colour=colour))
        await self.send_batches(interaction, embeds)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Diagnostics(bot))
