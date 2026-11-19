import discord
from discord.ext import commands

from core.bot import UKCRBot
from core.config import config
from core.console import get_logger
from core.helpers import now_iso

log = get_logger("members")


class Members(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != config.guild_id or member.bot:
            return
        try:
            await self.bot.db.run(
                "INSERT INTO member_events (member_id, guild_id, event, event_date) VALUES (?, ?, 'join', ?)",
                (member.id, member.guild.id, now_iso()),
            )
        except Exception:
            log.exception("Failed to record join for %s", member)

        role = member.guild.get_role(config.member_role_id) if config.member_role_id else None
        if role is None:
            log.warning("Member role %s not found", config.member_role_id)
            return
        try:
            await member.add_roles(role, reason="UKCR automatic member role")
            log.info("Gave %s to %s", role.name, member)
        except discord.Forbidden:
            log.error("Can't assign the Member role: give the bot Manage Roles and move its role above the Member role")
        except discord.HTTPException:
            log.exception("Failed to assign the Member role to %s", member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != config.guild_id or member.bot:
            return
        try:
            await self.bot.db.run(
                "INSERT INTO member_events (member_id, guild_id, event, event_date) VALUES (?, ?, 'leave', ?)",
                (member.id, member.guild.id, now_iso()),
            )
        except Exception:
            log.exception("Failed to record leave for %s", member)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Members(bot))
