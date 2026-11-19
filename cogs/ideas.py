import asyncio
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.bot import UKCRBot
from core.config import BRAND_COLOUR
from core.console import get_logger
from core.helpers import clip, dm_user, is_staff, now_iso, reply
from core.ui import SafeView

log = get_logger("ideas")

IDEA_STYLE = {
    "pending": ("💡 Open for voting", BRAND_COLOUR),
    "accepted": ("✅ Accepted", discord.Colour.green()),
    "declined": ("❌ Declined", discord.Colour.red()),
}


def build_idea_embed(row: sqlite3.Row) -> discord.Embed:
    status_text, colour = IDEA_STYLE[row["status"]]
    embed = discord.Embed(title=f"Content idea #{row['id']}", description=clip(row["idea"], 1000), colour=colour)
    embed.add_field(name="Suggested by", value=f"<@{row['user_id']}>", inline=True)
    embed.add_field(name="Status", value=status_text, inline=True)
    if row["reviewer_id"]:
        embed.add_field(name="Reviewed by", value=f"<@{row['reviewer_id']}>", inline=True)
    embed.set_footer(text="Vote with 👍 / 👎" if row["status"] == "pending" else "Decision made - voting is closed")
    return embed


async def review_idea(interaction: discord.Interaction, new_status: str) -> None:
    bot: UKCRBot = interaction.client
    if not is_staff(interaction.user):
        await reply(interaction, "Only the Social Media Team staff can review ideas.")
        return
    message = interaction.message
    cur = await bot.db.run(
        "UPDATE content_ideas SET status=?, reviewer_id=? WHERE message_id=? AND status='pending'",
        (new_status, interaction.user.id, message.id),
    )
    row = await bot.db.one("SELECT * FROM content_ideas WHERE message_id=?", (message.id,))
    if row is None:
        await reply(interaction, "I couldn't find this idea in the database.")
        return
    if cur.rowcount == 0:
        await reply(interaction, f"This idea has already been **{row['status']}**.")
        try:
            await message.edit(embed=build_idea_embed(row), view=None)
        except discord.HTTPException:
            pass
        return

    log.info("Idea #%s %s by %s", row["id"], new_status, interaction.user)
    await interaction.response.edit_message(embed=build_idea_embed(row), view=None)
    try:
        await message.clear_reactions()
    except discord.HTTPException:
        pass
    if new_status == "accepted":
        embed = discord.Embed(
            title="Your idea was accepted 🎉",
            description=f"The team liked your content idea **#{row['id']}**:\n> {clip(row['idea'], 300)}",
            colour=discord.Colour.green(),
        )
        embed.set_footer(text=interaction.guild.name if interaction.guild else "UKCR")
        await dm_user(bot, row["user_id"], embed)


class IdeaReviewView(SafeView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="ukcr:idea_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await review_idea(interaction, "accepted")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary, custom_id="ukcr:idea_decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await review_idea(interaction, "declined")


class Ideas(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(IdeaReviewView())
        self.idea_cleanup_loop.start()

    async def cog_unload(self) -> None:
        self.idea_cleanup_loop.cancel()

    @tasks.loop(count=1)
    async def idea_cleanup_loop(self) -> None:
        try:
            await self.reconcile_ideas()
        except Exception:
            log.exception("Idea clean-up failed")

    @idea_cleanup_loop.before_loop
    async def before_idea_cleanup_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def reconcile_ideas(self) -> None:
        rows = await self.bot.db.all(
            """SELECT * FROM content_ideas
               WHERE status IN ('accepted', 'declined') AND channel_id IS NOT NULL AND message_id IS NOT NULL
               ORDER BY id DESC LIMIT 50"""
        )
        fixed = 0
        for row in rows:
            guild = self.bot.get_guild(row["guild_id"])
            channel = guild.get_channel(row["channel_id"]) if guild else None
            if channel is None:
                continue
            try:
                message = await channel.fetch_message(row["message_id"])
                shown = None
                if message.embeds:
                    shown = next((f.value for f in message.embeds[0].fields if f.name == "Status"), None)
                if message.components or shown != IDEA_STYLE[row["status"]][0]:
                    await message.edit(embed=build_idea_embed(row), view=None)
                    try:
                        await message.clear_reactions()
                    except discord.HTTPException:
                        pass
                    fixed += 1
                await asyncio.sleep(0.5)
            except discord.HTTPException:
                continue
        if fixed:
            log.info("Idea clean-up: closed %d already-decided idea(s)", fixed)

    @app_commands.command(name="idea", description="Suggest a video or content idea for the UKCR socials")
    @app_commands.guild_only()
    @app_commands.describe(idea="Describe your idea")
    @app_commands.checks.cooldown(1, 60.0, key=lambda i: i.user.id)
    async def idea(self, interaction: discord.Interaction, idea: app_commands.Range[str, 10, 500]) -> None:
        guild = interaction.guild
        channel_id = await self.bot.db.setting_get(guild.id, "ideas_channel")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            await reply(interaction, "The ideas channel isn't set up yet - ask a server manager to run `/setupcontent`.")
            return

        await interaction.response.defer(ephemeral=True)
        cur = await self.bot.db.run(
            "INSERT INTO content_ideas (guild_id, user_id, idea, created_at) VALUES (?, ?, ?, ?)",
            (guild.id, interaction.user.id, idea.strip(), now_iso()),
        )
        idea_id = cur.lastrowid
        row = await self.bot.db.one("SELECT * FROM content_ideas WHERE id=?", (idea_id,))
        try:
            message = await channel.send(embed=build_idea_embed(row), view=IdeaReviewView())
        except discord.HTTPException:
            log.exception("Failed to post idea %s", idea_id)
            await self.bot.db.run("DELETE FROM content_ideas WHERE id=?", (idea_id,))
            await interaction.followup.send("I couldn't post your idea. Please tell a staff member.", ephemeral=True)
            return

        await self.bot.db.run(
            "UPDATE content_ideas SET channel_id=?, message_id=? WHERE id=?", (message.channel.id, message.id, idea_id)
        )
        for emoji in ("👍", "👎"):
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                break
        log.info("Idea #%s suggested by %s", idea_id, interaction.user)
        await interaction.followup.send(f"Your idea **#{idea_id}** was posted in {channel.mention}!", ephemeral=True)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Ideas(bot))
