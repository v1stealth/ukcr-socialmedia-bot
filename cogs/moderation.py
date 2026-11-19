from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import UKCRBot
from core.config import config
from core.console import get_logger
from core.constants import LOG_RED
from core.helpers import reply
from core.logging_utils import log_embed, send_log

log = get_logger("moderation")


class Moderation(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    @app_commands.command(name="report", description="Report a member to the staff team")
    @app_commands.guild_only()
    @app_commands.describe(member="The member you're reporting", reason="What happened?", evidence="Optional screenshot or file")
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: i.user.id)
    async def report(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 5, 1000],
        evidence: Optional[discord.Attachment] = None,
    ) -> None:
        if member.id == interaction.user.id:
            await reply(interaction, "You can't report yourself.")
            return
        if member.bot:
            await reply(interaction, "You can't report a bot. Please tell a staff member instead.")
            return

        fields = [
            ("Reported member", f"{member.mention} ({member.id})", False),
            ("Reported by", f"{interaction.user.mention} ({interaction.user.id})", False),
            ("Channel", interaction.channel.mention if interaction.channel else "Unknown", True),
            ("Reason", reason, False),
        ]
        if evidence is not None:
            fields.append(("Evidence", evidence.url, False))
        embed = log_embed("New user report", LOG_RED, user=interaction.user, fields=fields)
        if evidence is not None and (evidence.content_type or "").startswith("image/"):
            embed.set_image(url=evidence.url)

        ping = f"<@&{config.staff_role_id}>" if config.staff_role_id else None
        sent = await send_log(self.bot, interaction.guild, "reports", embed, content=ping)
        if sent:
            log.info("Report filed by %s against %s", interaction.user, member)
            await reply(interaction, "Thanks - your report has been sent to the staff team.")
        else:
            log.warning("Report by %s could not be delivered: user-reports channel not set up", interaction.user)
            await reply(interaction, "Reports aren't set up yet. Please open a ticket or tell a staff member.")


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Moderation(bot))
