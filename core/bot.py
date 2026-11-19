import asyncio
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.config import config
from core.console import get_logger
from core.database import Database
from core.helpers import is_owner, is_staff

log = get_logger("bot")

EXTENSIONS = (
    "cogs.members",
    "cogs.tickets",
    "cogs.submissions",
    "cogs.ideas",
    "cogs.calendar",
    "cogs.hashtags",
    "cogs.serverlogs",
    "cogs.guardian",
    "cogs.status",
    "cogs.general",
    "cogs.setup",
    "cogs.moderation",
    "cogs.power",
    "cogs.diagnostics",
)


class UKCRTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        channel_id = config.commands_channel_id
        if (
            channel_id is None
            or interaction.type is not discord.InteractionType.application_command
            or interaction.guild is None
            or interaction.channel_id is None
        ):
            return True

        channel_ids = {interaction.channel_id}
        parent_id = getattr(interaction.channel, "parent_id", None)
        if parent_id:
            channel_ids.add(parent_id)
        if channel_id in channel_ids:
            return True
        if (config.commands_staff_bypass and is_staff(interaction.user)) or is_owner(interaction.user):
            return True

        data = interaction.data or {}
        name = data.get("name", "")
        options = data.get("options") or []
        while options and options[0].get("type") in (1, 2):
            name += " " + options[0]["name"]
            options = options[0].get("options") or []

        if name == "close":
            bot: UKCRBot = interaction.client
            if await bot.db.one("SELECT 1 FROM tickets WHERE channel_id=?", (interaction.channel_id,)):
                return True

        embed = discord.Embed(
            title="🚫 Wrong channel",
            description=f"Bot commands only work in <#{channel_id}>.\nHead over there and run the command again.",
            colour=discord.Colour.red(),
        )
        if name:
            embed.add_field(name="You tried", value=f"`/{name}`", inline=False)
        embed.set_footer(text="Only you can see this message")
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.exception("Couldn't send the wrong-channel notice")
        return False

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        command = interaction.command.qualified_name if interaction.command else "unknown"
        original = getattr(error, "original", error)

        if isinstance(error, app_commands.MissingPermissions):
            message = "You need the **Manage Server** permission to use this command."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Slow down - you can use that again in {int(error.retry_after) + 1}s."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command can only be used inside the server."
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            message = f"I'm missing permissions to do that: **{missing}**."
        elif isinstance(error, app_commands.CheckFailure):
            message = "You don't have permission to use this command."
        elif isinstance(original, discord.Forbidden):
            log.error("/%s failed: Discord refused the request (missing permissions or role hierarchy)", command)
            message = "Discord refused that action. Check my permissions and that my role is high enough."
        elif isinstance(original, discord.NotFound):
            log.warning("/%s failed: a target no longer exists (%s)", command, original)
            message = "Something that command needed no longer exists."
        else:
            reference = uuid.uuid4().hex[:8]
            trace = "".join(traceback.format_exception(type(original), original, original.__traceback__))
            log.error("/%s crashed [ref %s] for %s (%s)\n%s", command, reference, interaction.user, interaction.user.id, trace)
            message = f"Something went wrong running that command. Reference: `{reference}`"

        embed = discord.Embed(description=f"❌ {message}", colour=discord.Colour.red())
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            log.warning("Couldn't deliver the error message for /%s", command)


class UKCRBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = config.message_content_intent
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=UKCRTree,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.db = Database(config.db_path)
        self.started_at = datetime.now(timezone.utc)
        self.channel_lock = asyncio.Lock()
        self.loaded_extensions: list = []
        self.failed_extensions: dict = {}

    @property
    def home_guild(self) -> Optional[discord.Guild]:
        return self.get_guild(config.guild_id)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.seed_defaults()

        for extension in EXTENSIONS:
            if not Path(extension.replace(".", "/") + ".py").exists():
                continue
            try:
                await self.load_extension(extension)
                self.loaded_extensions.append(extension)
                log.info("Loaded %s", extension)
            except Exception as exc:
                self.failed_extensions[extension] = repr(exc)
                log.error("Failed to load %s: %r", extension, exc, exc_info=True)

        guild = discord.Object(id=config.guild_id)
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d slash command(s) to guild %s", len(synced), config.guild_id)
        except discord.HTTPException:
            log.error("Slash command sync failed", exc_info=True)

    async def seed_defaults(self) -> None:
        from core.constants import DEFAULT_HASHTAG_PACKS

        if await self.db.setting_get(config.guild_id, "hashtags_seeded"):
            return
        for name, tags in DEFAULT_HASHTAG_PACKS.items():
            await self.db.run(
                "INSERT OR IGNORE INTO hashtag_packs (guild_id, name, tags) VALUES (?, ?, ?)",
                (config.guild_id, name, tags),
            )
        await self.db.setting_set(config.guild_id, "hashtags_seeded", 1)
        log.info("Seeded %d starter hashtag packs", len(DEFAULT_HASHTAG_PACKS))

    async def on_ready(self) -> None:
        guild = self.home_guild
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        if guild is None:
            log.error("The bot is not in guild %s. Invite it, or fix GUILD_ID in .env", config.guild_id)
            return
        log.info("Serving %s with %d members, latency %d ms", guild.name, guild.member_count or 0, round(self.latency * 1000))

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        log.exception("Unhandled error in event %s", event_method)

    async def close(self) -> None:
        log.info("Shutting down")
        await super().close()
        await self.db.close()
