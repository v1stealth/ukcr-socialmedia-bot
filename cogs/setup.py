import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import UKCRBot
from core.config import BRAND_COLOUR, config
from core.console import get_logger
from core.constants import CONTENT_CATEGORY_NAME, CONTENT_CHANNELS, CREATOR_TIERS, LOG_BLUE, LOG_CHANNELS
from core.helpers import reply
from core.logging_utils import log_embed

from cogs.submissions import creator_role, grant_creator_roles

log = get_logger("setup")

DASHBOARD_SECTIONS = [
    (
        "🎬 Video submissions",
        discord.Colour.blurple(),
        [
            ("/videosubmission", "Opens the form to submit a video for the UKCR socials."),
            ("/mysubmissions", "Shows the status of your recent submissions and your progress to the next creator role."),
            ("/resubmit", "Opens your video again, pre-filled, when the team has asked for changes."),
            ("/leaderboard", "Shows the top video contributors."),
            ("/hashtags get", "Gives you a ready-made hashtag pack to paste into your submission."),
            ("/hashtags list", "Lists every hashtag pack."),
        ],
    ),
    (
        "💬 Community",
        discord.Colour.green(),
        [
            ("/idea", "Suggest a content idea. It's posted for the community to vote on."),
            ("/report", "Privately report a member to the staff team, with optional evidence."),
            ("/close", "Closes the ticket you're in."),
            ("/userinfo", "Shows info about a member: join date, roles and more."),
            ("/serverinfo", "Shows UKCR server information."),
            ("/ping", "Shows the bot's ping, uptime and who made it."),
            ("/help", "A quick command guide, just for you."),
        ],
    ),
    (
        "🛠️ Social Media Team",
        discord.Colour.orange(),
        [
            ("/queue", "Approved videos that are waiting to be posted."),
            ("/markposted", "Marks a video as live and adds the link."),
            ("/submissionstats", "Submission totals, approval rate and review times."),
            ("/schedule add|list|done|remove", "The content calendar, with reminders and a daily digest."),
            ("/hashtags set|remove", "Create, edit or delete hashtag packs."),
            ("/status", "Bot health plus pending videos, open tickets and content due."),
            ("/announce", "Posts an announcement embed (defaults to the Bulletin channel) with an optional ping."),
        ],
    ),
    (
        "⚙️ Server management",
        discord.Colour.red(),
        [
            ("/ticketpanel", "Posts the support ticket dropdown."),
            ("/videosubmissionpanel", "Posts the Submit a Video button."),
            ("/rulespanel", "Posts the server rules."),
            ("/setuplogs", "Creates the log channels."),
            ("/setupcontent", "Creates the showcase, ideas and calendar channels and the creator roles."),
            ("/synccreatorroles", "Gives creator roles to everyone who has earned them."),
            ("/syncroles", "Gives the UKCR Citizen role to anyone missing it right now."),
            ("/stop", "Shuts the bot down (owner only)."),
            ("/restart", "Restarts the bot (owner only)."),
            ("/debug", "Full health check: console statistics plus a test of permissions, roles, channels, database, tasks and commands."),
            ("/setupdashboard", "Creates or refreshes this channel."),
        ],
    ),
]


def build_dashboard_embeds() -> list:
    intro = discord.Embed(
        title="🤖 UKCR Bot - Command Dashboard",
        description=(
            f"Everything the UKCR bot can do. Use commands in <#{config.commands_channel_id}>: type `/` and pick a command.\n"
            "Commands in the last two sections are only for staff and will say so if you try them."
        ),
        colour=BRAND_COLOUR,
    )
    embeds = [intro]
    for title, colour, entries in DASHBOARD_SECTIONS:
        embed = discord.Embed(title=title, colour=colour)
        embed.description = "\n".join(f"**`{name}`**\n└ {text}" for name, text in entries)
        embeds.append(embed)
    embeds[-1].set_footer(text="Updated by /setupdashboard")
    return embeds


def content_overwrites(guild: discord.Guild, access: str, staff_role: Optional[discord.Role]) -> dict:
    if access == "public":
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, add_reactions=True, read_message_history=True)
        }
    else:
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    overwrites[guild.me] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, embed_links=True, add_reactions=True, read_message_history=True
    )
    if staff_role is not None:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, add_reactions=True, read_message_history=True
        )
    return overwrites


class Setup(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    @app_commands.command(name="setuplogs", description="Creates the log channels and starts logging to them")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setuplogs(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        category = guild.get_channel(config.log_category_id) if config.log_category_id else None
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                f"I can't find the log category (`{config.log_category_id}`). Check `LOG_CATEGORY_ID` and that I can see it.",
                ephemeral=True,
            )
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True
            ),
        }
        staff_role = guild.get_role(config.staff_role_id) if config.staff_role_id else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)

        created, reused = [], []
        mentions = {}
        for key, (name, topic) in LOG_CHANNELS.items():
            channel_id = await self.bot.db.log_channel_id(guild.id, key)
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel is None:
                channel = discord.utils.get(category.text_channels, name=name)

            if channel is None:
                try:
                    channel = await guild.create_text_channel(
                        name=name,
                        category=category,
                        overwrites=overwrites,
                        topic=topic,
                        reason=f"Log channel created by {interaction.user}",
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        "I don't have permission to create channels in the log category. Give me **Manage Channels**.",
                        ephemeral=True,
                    )
                    return
                created.append(channel)
                log.info("Created log channel #%s", name)
                try:
                    await channel.send(embed=log_embed("Logging enabled", LOG_BLUE, f"This channel logs: {topic}."))
                except discord.HTTPException:
                    pass
            else:
                reused.append(channel)

            await self.bot.db.run(
                "INSERT OR REPLACE INTO log_channels (guild_id, log_key, channel_id) VALUES (?, ?, ?)",
                (guild.id, key, channel.id),
            )
            mentions[key] = channel.mention

        summary = discord.Embed(
            title="Log channels ready",
            description=f"Created **{len(created)}**, reused **{len(reused)}** existing.\n\n"
            + "\n".join(f"{mentions[key]} - {desc}" for key, (_, desc) in LOG_CHANNELS.items()),
            colour=BRAND_COLOUR,
        )
        await interaction.followup.send(embed=summary, ephemeral=True)

    @app_commands.command(name="setupcontent", description="Creates the showcase, ideas and calendar channels and the creator roles")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setupcontent(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        created, reused = [], []

        try:
            for threshold, name, colour in CREATOR_TIERS:
                role = await creator_role(self.bot, guild, threshold, name)
                if role is None:
                    role = await guild.create_role(name=name, colour=colour, reason=f"Creator role created by {interaction.user}")
                    created.append(f"Role **{name}**")
                    log.info("Created creator role %s", name)
                else:
                    reused.append(f"Role **{name}**")
                await self.bot.db.setting_set(guild.id, f"creator_role_{threshold}", role.id)

            category = discord.utils.get(guild.categories, name=CONTENT_CATEGORY_NAME)
            if category is None:
                category = await guild.create_category(CONTENT_CATEGORY_NAME, reason=f"Created by {interaction.user}")
                created.append(f"Category **{CONTENT_CATEGORY_NAME}**")

            staff_role = guild.get_role(config.staff_role_id) if config.staff_role_id else None
            for key, (name, topic, access, intro_title, intro_text) in CONTENT_CHANNELS.items():
                channel_id = await self.bot.db.setting_get(guild.id, f"{key}_channel")
                channel = guild.get_channel(channel_id) if channel_id else None
                if channel is None:
                    channel = discord.utils.get(category.text_channels, name=name)
                if channel is None:
                    channel = await guild.create_text_channel(
                        name=name,
                        category=category,
                        overwrites=content_overwrites(guild, access, staff_role),
                        topic=topic,
                        reason=f"Created by {interaction.user}",
                    )
                    created.append(f"Channel {channel.mention}")
                    log.info("Created content channel #%s", name)
                    try:
                        await channel.send(embed=discord.Embed(title=intro_title, description=intro_text, colour=BRAND_COLOUR))
                    except discord.HTTPException:
                        pass
                else:
                    reused.append(f"Channel {channel.mention}")
                await self.bot.db.setting_set(guild.id, f"{key}_channel", channel.id)
        except discord.Forbidden:
            log.error("/setupcontent failed: missing Manage Channels or Manage Roles")
            await interaction.followup.send(
                "I'm missing a permission. Give my role **Manage Channels** and **Manage Roles**, then run `/setupcontent` again "
                "(anything already created will be reused).",
                ephemeral=True,
            )
            return

        tier_text = "\n".join(f"**{name}** - {need} approved video{'s' if need != 1 else ''}" for need, name, _ in CREATOR_TIERS)
        embed = discord.Embed(
            title="Content features ready",
            description=(
                f"Created **{len(created)}**, reused **{len(reused)}** existing.\n\n"
                + ("\n".join(created + reused) or "Nothing to do.")
                + f"\n\n**Creator roles**\n{tier_text}\n\n"
                "Run `/synccreatorroles` to give roles to people who already have approved videos."
            ),
            colour=BRAND_COLOUR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="synccreatorroles", description="Gives creator roles to everyone with enough approved videos")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def synccreatorroles(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        roles = [await creator_role(self.bot, guild, need, name) for need, name, _ in CREATOR_TIERS]
        if not any(roles):
            await reply(interaction, "The creator roles don't exist yet - run `/setupcontent` first.")
            return
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.db.all(
            "SELECT DISTINCT user_id FROM video_submissions WHERE guild_id=? AND status IN ('approved', 'posted')", (guild.id,)
        )
        updated = 0
        for row in rows:
            if await grant_creator_roles(self.bot, guild, row["user_id"]):
                updated += 1
            await asyncio.sleep(0.3)
        log.info("Creator role sync: checked %d, updated %d", len(rows), updated)
        await interaction.followup.send(f"Checked **{len(rows)}** creator(s) and gave new roles to **{updated}**.", ephemeral=True)

    @app_commands.command(name="setupdashboard", description="Creates (or refreshes) the command dashboard channel for UKCR Citizens")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setupdashboard(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        citizen_role = guild.get_role(config.member_role_id) if config.member_role_id else None
        if citizen_role is None:
            await interaction.followup.send(f"I can't find the UKCR Citizen role (`{config.member_role_id}`). Check `MEMBER_ROLE_ID`.", ephemeral=True)
            return
        staff_role = guild.get_role(config.staff_role_id) if config.staff_role_id else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            citizen_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, add_reactions=False, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True),
        }
        if staff_role is not None:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        channel_id = await self.bot.db.setting_get(guild.id, "dashboard_channel")
        channel = guild.get_channel(channel_id) if channel_id else None
        created = False
        try:
            if channel is None:
                category = discord.utils.get(guild.categories, name=CONTENT_CATEGORY_NAME)
                channel = await guild.create_text_channel(
                    name="bot-dashboard",
                    category=category,
                    overwrites=overwrites,
                    topic="What every UKCR bot command does",
                    reason=f"Dashboard created by {interaction.user}",
                )
                created = True
            else:
                await channel.edit(overwrites=overwrites, reason=f"Dashboard refreshed by {interaction.user}")
            await self.bot.db.setting_set(guild.id, "dashboard_channel", channel.id)

            async for message in channel.history(limit=50):
                if message.author.id == guild.me.id:
                    await message.delete()
            await channel.send(embeds=build_dashboard_embeds())
        except discord.Forbidden:
            log.error("/setupdashboard failed: missing permissions")
            await interaction.followup.send(
                "I'm missing a permission. Give my role **Manage Channels** (and View/Send/Embed in that channel), then try again.",
                ephemeral=True,
            )
            return

        log.info("Dashboard %s in #%s", "created" if created else "refreshed", channel)
        verb = "Created" if created else "Refreshed"
        await interaction.followup.send(f"{verb} the dashboard in {channel.mention}. Only {citizen_role.mention} and staff can see it.", ephemeral=True)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Setup(bot))
