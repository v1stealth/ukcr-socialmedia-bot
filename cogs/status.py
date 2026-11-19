from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.bot import UKCRBot
from core.config import BRAND_COLOUR, config
from core.console import get_logger
from core.constants import LOG_CHANNELS
from core.helpers import discord_ts, fmt_duration, reply, staff_check

log = get_logger("status")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class Status(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot
        self.index = 0

    async def cog_load(self) -> None:
        self.status_loop.start()

    async def cog_unload(self) -> None:
        self.status_loop.cancel()

    async def build_options(self, guild) -> list:
        if guild is None:
            return ["UKCR"]
        db = self.bot.db
        now = datetime.now(timezone.utc)
        members = guild.member_count or len(guild.members)
        tickets = await db.scalar("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (guild.id,))
        videos = await db.scalar("SELECT COUNT(*) FROM video_submissions WHERE guild_id=? AND status='pending'", (guild.id,))
        ideas = await db.scalar("SELECT COUNT(*) FROM content_ideas WHERE guild_id=? AND status='pending'", (guild.id,))
        due = await db.scalar(
            "SELECT COUNT(*) FROM content_schedule WHERE guild_id=? AND status='scheduled' AND due_at <= ?",
            (guild.id, (now + timedelta(hours=24)).isoformat()),
        )
        return [
            plural(members, "member"),
            plural(tickets, "open ticket"),
            f"{plural(videos, 'video')} awaiting review",
            f"{plural(ideas, 'idea')} awaiting review",
            f"{plural(due, 'post')} due in 24h",
        ]

    @tasks.loop(seconds=30)
    async def status_loop(self) -> None:
        try:
            options = await self.build_options(self.bot.get_guild(config.guild_id))
            text = options[self.index % len(options)]
            self.index += 1
            await self.bot.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(type=discord.ActivityType.watching, name=text[:128]),
            )
        except Exception:
            log.exception("Status update failed")

    @status_loop.before_loop
    async def before_status_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="status", description="Bot health and a snapshot of tickets and the content pipeline (staff)")
    @app_commands.guild_only()
    @app_commands.check(staff_check)
    async def status(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        guild = interaction.guild
        gid = guild.id
        now = datetime.now(timezone.utc)
        now_text = now.isoformat()

        pending = await db.scalar("SELECT COUNT(*) FROM video_submissions WHERE guild_id=? AND status='pending'", (gid,))
        oldest = await db.scalar("SELECT MIN(submitted_at) FROM video_submissions WHERE guild_id=? AND status='pending'", (gid,), None)
        oldest_text = f" (oldest {fmt_duration(now - datetime.fromisoformat(oldest))})" if pending and oldest else ""
        awaiting = await db.scalar("SELECT COUNT(*) FROM video_submissions WHERE guild_id=? AND status='approved'", (gid,))
        changes = await db.scalar("SELECT COUNT(*) FROM video_submissions WHERE guild_id=? AND status='changes'", (gid,))

        open_tickets = await db.all(
            "SELECT channel_id, created_at FROM tickets WHERE guild_id=? AND status='open' ORDER BY created_at LIMIT 5", (gid,)
        )
        ticket_total = await db.scalar("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (gid,))
        ticket_text = f"Open: **{ticket_total}**"
        if open_tickets:
            ticket_text += "\n" + "\n".join(f"<#{t['channel_id']}> · opened {discord_ts(t['created_at'], 'R')}" for t in open_tickets)
            if ticket_total > len(open_tickets):
                ticket_text += f"\n…and {ticket_total - len(open_tickets)} more"

        upcoming = await db.scalar("SELECT COUNT(*) FROM content_schedule WHERE guild_id=? AND status='scheduled' AND due_at >= ?", (gid, now_text))
        overdue = await db.scalar("SELECT COUNT(*) FROM content_schedule WHERE guild_id=? AND status='scheduled' AND due_at < ?", (gid, now_text))
        ideas = await db.scalar("SELECT COUNT(*) FROM content_ideas WHERE guild_id=? AND status='pending'", (gid,))
        log_count = len(await db.log_channel_ids(gid))

        async def tick(key: str) -> str:
            channel_id = await db.setting_get(gid, key)
            return "✅" if channel_id and guild.get_channel(channel_id) else "❌"

        embed = discord.Embed(title="UKCR Bot status", colour=BRAND_COLOUR, timestamp=now)
        embed.add_field(
            name="🟢 Bot",
            value=f"Uptime **{fmt_duration(now - self.bot.started_at)}**\nLatency **{round(self.bot.latency * 1000)} ms**\nMembers **{guild.member_count}**",
            inline=True,
        )
        embed.add_field(
            name="🎬 Video pipeline",
            value=f"Pending review: **{pending}**{oldest_text}\nAwaiting posting: **{awaiting}**\nChanges requested: **{changes}**",
            inline=True,
        )
        embed.add_field(name="🎫 Tickets", value=ticket_text, inline=False)
        embed.add_field(
            name="📅 Content",
            value=f"Scheduled: **{upcoming}**\nOverdue: **{overdue}**\nIdeas to review: **{ideas}**",
            inline=True,
        )
        embed.add_field(
            name="⚙️ Setup",
            value=(
                f"{await tick('showcase_channel')} Showcase  {await tick('ideas_channel')} Ideas  {await tick('calendar_channel')} Calendar\n"
                f"Log channels: **{log_count}/{len(LOG_CHANNELS)}**"
            ),
            inline=True,
        )
        embed.set_footer(text=f"discord.py {discord.__version__}")
        await reply(interaction, embed=embed)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Status(bot))
