import discord

from core.console import get_logger
from core.helpers import reply

log = get_logger("ui")


class SafeView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.error("View error in %s (%s)", type(self).__name__, getattr(item, "custom_id", "?"), exc_info=error)
        await reply(interaction, "Something went wrong handling that button.")


class SafeModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("Modal error in %s", type(self).__name__, exc_info=error)
        await reply(interaction, "Something went wrong submitting that form.")
