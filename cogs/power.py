import asyncio
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import UKCRBot
from core.console import get_logger
from core.helpers import is_owner, reply

log = get_logger("power")


class PowerConfirmView(discord.ui.View):
    def __init__(self, bot: UKCRBot, action: str, user_id: int) -> None:
        super().__init__(timeout=30)
        self.bot = bot
        self.action = action
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id or not is_owner(interaction.user):
            await reply(interaction, "This confirmation isn't yours.")
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.error("Power confirmation failed", exc_info=error)
        await reply(interaction, "Something went wrong.")

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        text = "🔄 Restarting now… I'll be back in a few seconds." if self.action == "restart" else "🛑 Shutting down. Goodbye!"
        await interaction.response.edit_message(content=text, view=None)
        self.stop()
        await power_action(self.bot, self.action, interaction.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=None)
        self.stop()


async def power_action(bot: UKCRBot, action: str, user: discord.abc.User) -> None:
    log.warning("%s requested by %s (%s)", "Restart" if action == "restart" else "Shutdown", user, user.id)
    await asyncio.sleep(1)
    await bot.close()
    if action == "restart":
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except OSError:
            log.exception("Couldn't restart in place. Exiting so your host can start the bot again")
            raise SystemExit(1)


class Power(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    async def ask(self, interaction: discord.Interaction, action: str) -> None:
        if not is_owner(interaction.user):
            await reply(interaction, "Only the bot owner can use this command.")
            return
        verb = "restart" if action == "restart" else "shut down"
        extra = "" if action == "restart" else "\nIt will stay offline until someone starts it again from the host."
        await interaction.response.send_message(
            f"⚠️ Are you sure you want to **{verb}** the bot?{extra}",
            view=PowerConfirmView(self.bot, action, interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="stop", description="Shuts the bot down (owner only)")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        await self.ask(interaction, "stop")

    @app_commands.command(name="restart", description="Restarts the bot (owner only)")
    @app_commands.guild_only()
    async def restart(self, interaction: discord.Interaction) -> None:
        await self.ask(interaction, "restart")


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Power(bot))
