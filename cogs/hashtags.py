import re

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import UKCRBot
from core.config import BRAND_COLOUR
from core.console import get_logger
from core.helpers import clip, normalise_tags, reply, staff_check

log = get_logger("hashtags")


class Hashtags(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    group = app_commands.Group(name="hashtags", description="Ready-made hashtag packs for your posts", guild_only=True)

    async def pack_autocomplete(self, interaction: discord.Interaction, current: str) -> list:
        rows = await self.bot.db.all("SELECT name FROM hashtag_packs WHERE guild_id=? ORDER BY name", (interaction.guild_id,))
        return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows if current.lower() in r["name"].lower()][:25]

    @group.command(name="get", description="Get a hashtag pack to paste into your submission")
    @app_commands.describe(pack="Which pack?")
    async def get(self, interaction: discord.Interaction, pack: str) -> None:
        name = pack.strip().lower()
        row = await self.bot.db.one("SELECT * FROM hashtag_packs WHERE guild_id=? AND name=?", (interaction.guild_id, name))
        if row is None:
            rows = await self.bot.db.all("SELECT name FROM hashtag_packs WHERE guild_id=? ORDER BY name", (interaction.guild_id,))
            names = ", ".join(f"`{r['name']}`" for r in rows) or "none yet"
            await reply(interaction, f"I couldn't find that pack. Available packs: {names}")
            return
        embed = discord.Embed(title=f"#{row['name']} hashtag pack", description=f"```{row['tags']}```", colour=BRAND_COLOUR)
        embed.set_footer(text="Copy and paste these into the Hashtags box when you submit a video")
        await reply(interaction, embed=embed)

    @group.command(name="list", description="List every hashtag pack")
    async def list_packs(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.all("SELECT * FROM hashtag_packs WHERE guild_id=? ORDER BY name", (interaction.guild_id,))
        if not rows:
            await reply(interaction, "No hashtag packs yet. Staff can add one with `/hashtags set`.")
            return
        embed = discord.Embed(title="Hashtag packs", colour=BRAND_COLOUR)
        for row in rows[:25]:
            embed.add_field(name=row["name"], value=clip(row["tags"]), inline=False)
        await reply(interaction, embed=embed)

    @group.command(name="set", description="Create or update a hashtag pack (staff)")
    @app_commands.describe(name="Pack name, e.g. pursuit", tags="The hashtags, separated by spaces")
    @app_commands.check(staff_check)
    async def set_pack(self, interaction: discord.Interaction, name: app_commands.Range[str, 2, 30], tags: app_commands.Range[str, 2, 300]) -> None:
        slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
        cleaned = normalise_tags(tags)
        if not slug or not cleaned:
            await reply(interaction, "I need a pack name and at least one hashtag.")
            return
        await self.bot.db.run(
            "INSERT OR REPLACE INTO hashtag_packs (guild_id, name, tags) VALUES (?, ?, ?)",
            (interaction.guild_id, slug, cleaned),
        )
        log.info("Hashtag pack %s saved by %s", slug, interaction.user)
        await reply(interaction, f"Saved pack **{slug}**:\n```{cleaned}```")

    @group.command(name="remove", description="Delete a hashtag pack (staff)")
    @app_commands.describe(pack="The pack to delete")
    @app_commands.check(staff_check)
    async def remove(self, interaction: discord.Interaction, pack: str) -> None:
        name = pack.strip().lower()
        cur = await self.bot.db.run("DELETE FROM hashtag_packs WHERE guild_id=? AND name=?", (interaction.guild_id, name))
        if cur.rowcount == 0:
            await reply(interaction, "I couldn't find a pack with that name.")
            return
        log.info("Hashtag pack %s deleted by %s", name, interaction.user)
        await reply(interaction, f"Deleted pack **{name}**.")

    @get.autocomplete("pack")
    async def get_autocomplete(self, interaction: discord.Interaction, current: str) -> list:
        return await self.pack_autocomplete(interaction, current)

    @remove.autocomplete("pack")
    async def remove_autocomplete(self, interaction: discord.Interaction, current: str) -> list:
        return await self.pack_autocomplete(interaction, current)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Hashtags(bot))
