from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import UKCRBot
from core.config import BRAND_COLOUR, config
from core.console import get_logger
from core.constants import SERVER_RULES
from core.helpers import clip, fmt_duration, reply, staff_check

log = get_logger("general")

ANNOUNCE_PINGS = [
    app_commands.Choice(name="No ping", value="none"),
    app_commands.Choice(name="@everyone", value="everyone"),
    app_commands.Choice(name="UKCR Citizens", value="citizens"),
]


class General(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Displays the bot's command guide")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="UKCR Bot - Command Guide", colour=BRAND_COLOUR)
        embed.add_field(
            name="Videos",
            value=(
                "`/videosubmission` - open the video submission form\n"
                "`/mysubmissions` - check the status of your videos\n"
                "`/resubmit` - send back a video the team asked you to change\n"
                "`/leaderboard` - top contributors\n"
                "`/hashtags get` - ready-made hashtag packs\n"
                "`/hashtags list` - list every hashtag pack\n"
                "`/help` - this guide"
            ),
            inline=False,
        )
        embed.add_field(
            name="Community",
            value=(
                "`/idea` - suggest a content idea\n"
                "`/report` - report a member to the staff team\n"
                "`/ping` - bot ping, uptime and who made it\n"
                "`/close` - close the current ticket\n"
                "`/userinfo` - information about a member\n"
                "`/serverinfo` - UKCR server information"
            ),
            inline=False,
        )
        embed.add_field(
            name="Social Media Team (staff role)",
            value=(
                "`/queue` - approved videos waiting to be posted\n"
                "`/markposted` - mark a video as live and add the link\n"
                "`/submissionstats` - submission statistics\n"
                "`/schedule add|list|done|remove` - content calendar\n"
                "`/hashtags set|remove` - manage hashtag packs\n"
                "`/status` - bot, ticket and pipeline dashboard\n"
                "`/announce` - post an announcement embed"
            ),
            inline=False,
        )
        embed.add_field(
            name="Staff (Manage Server)",
            value=(
                "`/ticketpanel` - publish the ticket dropdown\n"
                "`/videosubmissionpanel` - publish the video submission button\n"
                "`/rulespanel` - publish the server rules\n"
                "`/setuplogs` - create the log channels\n"
                "`/setupcontent` - create the showcase, ideas and calendar channels and creator roles\n"
                "`/synccreatorroles` - give creator roles to existing approved creators\n"
                "`/syncroles` - give the UKCR Citizen role to anyone missing it\n"
                "`/debug` - full bot health check and console statistics\n"
                "`/stop` - shut the bot down (owner only)\n"
                "`/restart` - restart the bot (owner only)\n"
                "`/setupdashboard` - create or refresh the command dashboard channel"
            ),
            inline=False,
        )
        await reply(interaction, embed=embed)

    @app_commands.command(name="ping", description="Shows the bot's ping, who made it and how long it's been online")
    async def ping(self, interaction: discord.Interaction) -> None:
        now = datetime.now(timezone.utc)
        embed = discord.Embed(title="🏓 Pong!", colour=BRAND_COLOUR, timestamp=now)
        embed.add_field(name="Ping", value=f"**{round(self.bot.latency * 1000)} ms**", inline=True)
        embed.add_field(name="Uptime", value=f"**{fmt_duration(now - self.bot.started_at)}**", inline=True)
        embed.add_field(name="Online since", value=discord.utils.format_dt(self.bot.started_at, "R"), inline=True)
        embed.add_field(name="Made by", value=config.bot_author, inline=False)
        embed.set_footer(text=f"UKCR Bot · discord.py {discord.__version__}")
        await reply(interaction, embed=embed, ephemeral=False)

    @app_commands.command(name="userinfo", description="Displays information about a member")
    @app_commands.guild_only()
    @app_commands.describe(member="The member to look up (defaults to you)")
    async def userinfo(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        member = member or interaction.user
        embed = discord.Embed(title=str(member), colour=member.colour)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "F"), inline=False)
        if member.joined_at:
            embed.add_field(name="Joined UKCR", value=discord.utils.format_dt(member.joined_at, "F"), inline=False)
        roles = [r.mention for r in reversed(member.roles) if r != interaction.guild.default_role]
        embed.add_field(name=f"Roles ({len(roles)})", value=clip(", ".join(roles) or "None"), inline=False)
        await reply(interaction, embed=embed, ephemeral=False)

    @app_commands.command(name="serverinfo", description="Displays UKCR server information")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        embed = discord.Embed(title=guild.name, colour=BRAND_COLOUR)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "F"), inline=False)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Boosts", value=str(guild.premium_subscription_count), inline=True)
        embed.set_footer(text=f"Server ID: {guild.id}")
        await reply(interaction, embed=embed, ephemeral=False)

    @app_commands.command(name="rulespanel", description="Publishes the server rules in this channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rulespanel(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=f"{interaction.guild.name} - Server Rules",
            description="Please read and follow these rules. By staying in the server you agree to them.",
            colour=BRAND_COLOUR,
        )
        for number, (title, text) in enumerate(SERVER_RULES, start=1):
            embed.add_field(name=f"{number}. {title}", value=text, inline=False)
        embed.set_footer(text="Breaking the rules may result in a warning, mute, kick or ban at staff discretion.")
        await interaction.channel.send(embed=embed)
        await reply(interaction, "Rules published.")
        log.info("Rules published in #%s by %s", interaction.channel, interaction.user)

    @app_commands.command(name="announce", description="Post an announcement embed (staff). Defaults to the Bulletin category.")
    @app_commands.guild_only()
    @app_commands.check(staff_check)
    @app_commands.describe(
        title="Announcement title",
        message="The announcement text. Type \\n for a new line.",
        channel="Where to post it (default: the first channel in Bulletin)",
        ping="Who to ping with it (default: nobody)",
    )
    @app_commands.choices(ping=ANNOUNCE_PINGS)
    async def announce(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 256],
        message: app_commands.Range[str, 1, 4000],
        channel: Optional[discord.TextChannel] = None,
        ping: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        guild = interaction.guild
        if channel is None:
            category = guild.get_channel(config.bulletin_category_id) if config.bulletin_category_id else None
            if isinstance(category, discord.CategoryChannel) and category.text_channels:
                channel = category.text_channels[0]
            else:
                await reply(interaction, "I couldn't find a channel in the Bulletin category. Pick one with the `channel` option.")
                return

        if not channel.permissions_for(guild.me).send_messages:
            await reply(interaction, f"I can't post in {channel.mention}.")
            return

        ping_value = ping.value if ping else "none"
        content = None
        allowed = discord.AllowedMentions.none()
        if ping_value == "everyone":
            content = "@everyone"
            allowed = discord.AllowedMentions(everyone=True)
        elif ping_value == "citizens" and config.member_role_id:
            content = f"<@&{config.member_role_id}>"
            allowed = discord.AllowedMentions(roles=[discord.Object(id=config.member_role_id)])

        embed = discord.Embed(
            title=f"📢 {title}",
            description=message.replace("\\n", "\n"),
            colour=BRAND_COLOUR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Announced by {interaction.user.display_name}")

        try:
            await channel.send(content=content, embed=embed, allowed_mentions=allowed)
        except discord.HTTPException:
            log.exception("Failed to send announcement")
            await reply(interaction, "Discord wouldn't let me post that. Check my permissions in that channel.")
            return
        log.info("Announcement %r posted in #%s by %s", title, channel, interaction.user)
        await reply(interaction, f"Announcement posted in {channel.mention} ✅")


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(General(bot))
