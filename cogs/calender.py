import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.bot import UKCRBot
from core.config import BRAND_COLOUR, config
from core.console import get_logger
from core.constants import PLATFORMS
from core.helpers import CONTENT_TZ, clip, now_iso, parse_when, reply, staff_check

log = get_logger("calendar")


def schedule_line(row: sqlite3.Row) -> str:
    stamp = int(datetime.fromisoformat(row["due_at"]).timestamp())
    owner = f" · <@{row['owner_id']}>" if row["owner_id"] else ""
    return f"**#{row['id']}** · <t:{stamp}:f> (<t:{stamp}:R>) · {row['platform']} · {clip(row['title'], 60)}{owner}"


def build_schedule_embed(row: sqlite3.Row, heading: Optional[str] = None) -> discord.Embed:
    stamp = int(datetime.fromisoformat(row["due_at"]).timestamp())
    title = f"{heading} · #{row['id']}" if heading else f"📅 Scheduled post #{row['id']}"
    embed = discord.Embed(title=title, description=clip(row["title"], 300), colour=BRAND_COLOUR)
    embed.add_field(name="Platform", value=row["platform"], inline=True)
    embed.add_field(name="When", value=f"<t:{stamp}:F>\n<t:{stamp}:R>", inline=True)
    embed.add_field(name="Owner", value=f"<@{row['owner_id']}>" if row["owner_id"] else "Unassigned", inline=True)
    if row["notes"]:
        embed.add_field(name="Notes", value=clip(row["notes"]), inline=False)
    return embed


class Calendar(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.calendar_loop.start()

    async def cog_unload(self) -> None:
        self.calendar_loop.cancel()

    async def post_to_calendar(self, guild: discord.Guild, **kwargs) -> bool:
        channel_id = await self.bot.db.setting_get(guild.id, "calendar_channel")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            return False
        try:
            await channel.send(**kwargs)
            return True
        except discord.HTTPException:
            log.exception("Failed to post to the content calendar channel")
            return False

    @tasks.loop(minutes=5)
    async def calendar_loop(self) -> None:
        try:
            await self.remind_calendar()
            await self.maybe_send_digest()
        except Exception:
            log.exception("Content calendar loop failed")

    @calendar_loop.before_loop
    async def before_calendar_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def remind_calendar(self) -> None:
        now = datetime.now(timezone.utc)
        horizon = (now + timedelta(minutes=60)).isoformat()
        rows = await self.bot.db.all(
            "SELECT * FROM content_schedule WHERE status='scheduled' AND reminded=0 AND due_at <= ? ORDER BY due_at",
            (horizon,),
        )
        for row in rows:
            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                continue
            overdue = datetime.fromisoformat(row["due_at"]) < now
            embed = build_schedule_embed(row, heading="⚠️ Overdue" if overdue else "⏰ Due soon")
            sent = await self.post_to_calendar(
                guild,
                content=f"<@{row['owner_id']}>" if row["owner_id"] else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            if sent:
                await self.bot.db.run("UPDATE content_schedule SET reminded=1 WHERE id=?", (row["id"],))
                log.info("Calendar reminder sent for post #%s", row["id"])

    async def maybe_send_digest(self) -> None:
        guild = self.bot.get_guild(config.guild_id)
        if guild is None:
            return
        now_local = datetime.now(CONTENT_TZ)
        if not (config.digest_hour <= now_local.hour < config.digest_hour + 6):
            return
        today_key = int(now_local.strftime("%Y%m%d"))
        if await self.bot.db.setting_get(guild.id, "last_digest") == today_key:
            return
        await self.bot.db.setting_set(guild.id, "last_digest", today_key)

        start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start.astimezone(timezone.utc).isoformat()
        end_utc = (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()
        today = await self.bot.db.all(
            "SELECT * FROM content_schedule WHERE guild_id=? AND status='scheduled' AND due_at >= ? AND due_at < ? ORDER BY due_at",
            (guild.id, start_utc, end_utc),
        )
        overdue = await self.bot.db.all(
            "SELECT * FROM content_schedule WHERE guild_id=? AND status='scheduled' AND due_at < ? ORDER BY due_at LIMIT 10",
            (guild.id, start_utc),
        )
        if not today and not overdue:
            return

        embed = discord.Embed(title="📅 Content for today", colour=BRAND_COLOUR, timestamp=datetime.now(timezone.utc))
        embed.description = "\n".join(schedule_line(r) for r in today) or "Nothing scheduled for today."
        if overdue:
            embed.add_field(name="⚠️ Overdue", value=clip("\n".join(schedule_line(r) for r in overdue)), inline=False)
        embed.set_footer(text="Mark posts as done with /schedule done")
        await self.post_to_calendar(guild, embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
        log.info("Daily content digest posted")

    group = app_commands.Group(name="schedule", description="Plan and track social media posts (staff)", guild_only=True)

    @group.command(name="add", description="Add a post to the content calendar")
    @app_commands.describe(
        title="What's being posted?",
        platform="Where it's going",
        when=f"Date and time, e.g. 05/11/2026 18:00 ({config.content_tz})",
        owner="Who's responsible (defaults to you)",
        notes="Optional notes",
    )
    @app_commands.choices(platform=[app_commands.Choice(name=p, value=p) for p in PLATFORMS])
    @app_commands.check(staff_check)
    async def add(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 3, 100],
        platform: str,
        when: str,
        owner: Optional[discord.Member] = None,
        notes: Optional[str] = None,
    ) -> None:
        due = parse_when(when)
        if due is None:
            await reply(interaction, "I couldn't read that date. Use `DD/MM/YYYY HH:MM`, for example `05/11/2026 18:00`.")
            return
        if due < datetime.now(timezone.utc) - timedelta(minutes=1):
            await reply(interaction, "That time is in the past.")
            return

        owner = owner or interaction.user
        cur = await self.bot.db.run(
            """INSERT INTO content_schedule (guild_id, title, platform, owner_id, due_at, notes, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                interaction.guild_id,
                title.strip(),
                platform,
                owner.id,
                due.isoformat(),
                (notes or "").strip()[:300] or None,
                interaction.user.id,
                now_iso(),
            ),
        )
        row = await self.bot.db.one("SELECT * FROM content_schedule WHERE id=?", (cur.lastrowid,))
        embed = build_schedule_embed(row)
        embed.set_footer(text=f"Added by {interaction.user.display_name}")
        log.info("Post #%s scheduled by %s for %s", row["id"], interaction.user, row["due_at"])

        calendar_id = await self.bot.db.setting_get(interaction.guild_id, "calendar_channel")
        if calendar_id and calendar_id != interaction.channel_id:
            await self.post_to_calendar(interaction.guild, embed=embed)
        await reply(interaction, "Added to the content calendar.", embed=embed)

    @group.command(name="list", description="Show upcoming scheduled posts")
    @app_commands.check(staff_check)
    async def list_posts(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.all(
            "SELECT * FROM content_schedule WHERE guild_id=? AND status='scheduled' ORDER BY due_at LIMIT 15",
            (interaction.guild_id,),
        )
        if not rows:
            await reply(interaction, "Nothing scheduled. Add a post with `/schedule add`.")
            return
        now = datetime.now(timezone.utc)
        lines = [("⚠️ " if datetime.fromisoformat(r["due_at"]) < now else "") + schedule_line(r) for r in rows]
        embed = discord.Embed(title="📅 Upcoming content", description="\n".join(lines), colour=BRAND_COLOUR)
        embed.set_footer(text="/schedule done <id> when it's posted · /schedule remove <id> to delete")
        await reply(interaction, embed=embed)

    @group.command(name="done", description="Mark a scheduled post as done")
    @app_commands.describe(post_id="The post number from /schedule list")
    @app_commands.check(staff_check)
    async def done(self, interaction: discord.Interaction, post_id: int) -> None:
        cur = await self.bot.db.run(
            "UPDATE content_schedule SET status='done' WHERE id=? AND guild_id=? AND status='scheduled'",
            (post_id, interaction.guild_id),
        )
        if cur.rowcount == 0:
            await reply(interaction, "I couldn't find an open scheduled post with that number.")
            return
        log.info("Post #%s marked done by %s", post_id, interaction.user)
        await reply(interaction, f"Marked post **#{post_id}** as done. ✅")

    @group.command(name="remove", description="Delete a scheduled post")
    @app_commands.describe(post_id="The post number from /schedule list")
    @app_commands.check(staff_check)
    async def remove(self, interaction: discord.Interaction, post_id: int) -> None:
        cur = await self.bot.db.run("DELETE FROM content_schedule WHERE id=? AND guild_id=?", (post_id, interaction.guild_id))
        if cur.rowcount == 0:
            await reply(interaction, "I couldn't find a scheduled post with that number.")
            return
        log.info("Post #%s removed by %s", post_id, interaction.user)
        await reply(interaction, f"Removed post **#{post_id}**.")


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Calendar(bot))
