import asyncio
import io
import re
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import UKCRBot
from core.config import BRAND_COLOUR, config
from core.console import get_logger
from core.constants import LOG_BLUE, TICKET_CATEGORIES
from core.helpers import discord_ts, is_staff, now_iso, reply
from core.logging_utils import log_embed, send_log
from core.ui import SafeView

log = get_logger("tickets")

opening_ticket: set = set()


async def send_transcript(bot: UKCRBot, channel: discord.TextChannel, row: sqlite3.Row, closed_by: discord.abc.User) -> None:
    guild = channel.guild
    opener = guild.get_member(row["opener_id"])
    lines = []
    async for m in channel.history(limit=None, oldest_first=True):
        text = m.content or ""
        if m.attachments:
            text += " " + " ".join(f"[attachment: {a.url}]" for a in m.attachments)
        if m.embeds and not text.strip():
            text = "[embed]"
        lines.append(f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC] {m.author} ({m.author.id}): {text.strip()}")

    header = [
        f"Transcript of #{channel.name}",
        f"Category: {row['category']}",
        f"Opened by: {opener or row['opener_id']} ({row['opener_id']})",
        f"Closed by: {closed_by} ({closed_by.id})",
        f"Messages: {len(lines)}",
        "-" * 60,
    ]
    data = "\n".join(header + (lines or ["No messages."]))
    file = discord.File(io.BytesIO(data.encode("utf-8")), filename=f"transcript-{channel.name}.txt")
    embed = log_embed(
        "Ticket transcript",
        LOG_BLUE,
        fields=[
            ("Ticket", f"#{channel.name}", True),
            ("Category", row["category"], True),
            ("Messages", len(lines), True),
            ("Opened by", f"<@{row['opener_id']}>", True),
            ("Closed by", closed_by.mention, True),
            ("Opened", discord_ts(row["created_at"]), True),
        ],
    )
    await send_log(bot, guild, "transcripts", embed, file=file)


async def close_ticket(interaction: discord.Interaction, channel: discord.abc.GuildChannel) -> None:
    bot: UKCRBot = interaction.client
    row = await bot.db.one("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (channel.id,))
    if row is None:
        await reply(interaction, "This isn't an open ticket channel.")
        return
    if interaction.user.id != row["opener_id"] and not is_staff(interaction.user):
        await reply(interaction, "Only the ticket opener or staff can close this ticket.")
        return

    cur = await bot.db.run(
        "UPDATE tickets SET status='closed', closed_at=?, closed_by=? WHERE channel_id=? AND status='open'",
        (now_iso(), interaction.user.id, channel.id),
    )
    if cur.rowcount == 0:
        await reply(interaction, "This ticket is already being closed.")
        return

    log.info("Ticket #%s (%s) closed by %s", row["id"], channel.name, interaction.user)
    await interaction.response.send_message(f"Ticket closed by {interaction.user.mention}. Deleting this channel in 5 seconds…")
    await asyncio.sleep(5)
    try:
        await send_transcript(bot, channel, row, interaction.user)
    except Exception:
        log.exception("Failed to save transcript for ticket channel %s", channel.id)
    try:
        await channel.delete(reason=f"Ticket closed by {interaction.user}")
    except discord.HTTPException:
        log.exception("Failed to delete ticket channel %s", channel.id)


class TicketSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [discord.SelectOption(label=label, description=desc, emoji=emoji) for label, desc, emoji in TICKET_CATEGORIES]
        super().__init__(
            custom_id="ukcr:ticket_select",
            placeholder="Select a ticket category…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: UKCRBot = interaction.client
        guild = interaction.guild
        user = interaction.user
        category = self.values[0]
        await interaction.response.defer(ephemeral=True)

        try:
            if interaction.message:
                await interaction.message.edit(view=TicketPanelView())
        except discord.HTTPException:
            pass

        if guild is None:
            return
        if user.id in opening_ticket:
            await interaction.followup.send("Your ticket is already being created.", ephemeral=True)
            return
        opening_ticket.add(user.id)
        try:
            existing = await bot.db.one(
                "SELECT channel_id FROM tickets WHERE guild_id=? AND opener_id=? AND status='open'",
                (guild.id, user.id),
            )
            if existing:
                existing_channel = guild.get_channel(existing["channel_id"])
                if existing_channel:
                    await interaction.followup.send(f"You already have an open ticket: {existing_channel.mention}", ephemeral=True)
                    return
                await bot.db.run(
                    "UPDATE tickets SET status='closed', closed_at=? WHERE channel_id=?",
                    (now_iso(), existing["channel_id"]),
                )

            perms = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                user: perms,
                guild.me: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                    manage_channels=True,
                ),
            }
            staff_role = guild.get_role(config.staff_role_id) if config.staff_role_id else None
            if staff_role:
                overwrites[staff_role] = perms

            parent = guild.get_channel(config.ticket_category_id) if config.ticket_category_id else None
            if not isinstance(parent, discord.CategoryChannel):
                parent = None

            safe_name = re.sub(r"[^a-z0-9]+", "-", user.name.lower()).strip("-") or str(user.id)
            try:
                channel = await guild.create_text_channel(
                    name=f"ticket-{safe_name}"[:90],
                    category=parent,
                    overwrites=overwrites,
                    topic=f"{category} ticket opened by {user} ({user.id})",
                    reason=f"Ticket opened by {user}",
                )
            except discord.Forbidden:
                log.error("Can't create ticket channels: give the bot Manage Channels")
                await interaction.followup.send(
                    "I don't have permission to create ticket channels. Please tell a staff member.", ephemeral=True
                )
                return
            except discord.HTTPException:
                log.exception("Failed to create a ticket channel for %s", user)
                await interaction.followup.send("Discord wouldn't let me create the ticket. Please try again shortly.", ephemeral=True)
                return

            await bot.db.run(
                "INSERT INTO tickets (channel_id, guild_id, opener_id, category, created_at) VALUES (?, ?, ?, ?, ?)",
                (channel.id, guild.id, user.id, category, now_iso()),
            )
            log.info("Ticket opened by %s in %s (%s)", user, channel.name, category)

            embed = discord.Embed(
                title=f"{category} ticket",
                description=(
                    f"Hello {user.mention}, thanks for contacting UKCR.\n"
                    "A member of staff will be with you shortly. Please describe your issue below.\n\n"
                    "Press **Close Ticket** or use `/close` when you're finished."
                ),
                colour=BRAND_COLOUR,
                timestamp=datetime.now(timezone.utc),
            )
            ping = f"{user.mention} {staff_role.mention}" if staff_role else user.mention
            await channel.send(
                content=ping,
                embed=embed,
                view=CloseTicketView(),
                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
            )
            await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)
        finally:
            opening_ticket.discard(user.id)


class TicketPanelView(SafeView):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class CloseTicketView(SafeView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ukcr:ticket_close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await close_ticket(interaction, interaction.channel)


class Tickets(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(CloseTicketView())

    @app_commands.command(name="ticketpanel", description="Publishes the ticket dropdown menu")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticketpanel(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="UKCR Support Tickets",
            description="Need help? Pick a category from the dropdown below and a private ticket channel will be created for you.",
            colour=BRAND_COLOUR,
        )
        await interaction.channel.send(embed=embed, view=TicketPanelView())
        await interaction.response.send_message("Ticket panel published.", ephemeral=True)
        log.info("Ticket panel published in #%s by %s", interaction.channel, interaction.user)

    @app_commands.command(name="close", description="Closes the current ticket")
    @app_commands.guild_only()
    async def close(self, interaction: discord.Interaction) -> None:
        await close_ticket(interaction, interaction.channel)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Tickets(bot))
