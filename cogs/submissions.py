import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.bot import UKCRBot
from core.config import BRAND_COLOUR, config
from core.console import get_logger
from core.constants import CREATOR_TIERS, STATUS_STYLE
from core.helpers import (
    URL_RE,
    clip,
    discord_ts,
    dm_user,
    fmt_duration,
    is_staff,
    message_link,
    normalise_url,
    now_iso,
    reply,
    staff_check,
)
from core.ui import SafeModal, SafeView

log = get_logger("submissions")

STAFF_ONLY = "Only the Social Media Team staff can review submissions."


def build_submission_embed(row: sqlite3.Row) -> discord.Embed:
    status_text, colour = STATUS_STYLE[row["status"]]
    embed = discord.Embed(title=f"Video Submission #{row['id']}", colour=colour)
    embed.add_field(name="Submission ID", value=f"#{row['id']}", inline=True)
    embed.add_field(name="Submitted by", value=f"<@{row['user_id']}>", inline=True)
    embed.add_field(name="Submission date", value=discord_ts(row["submitted_at"]), inline=True)
    embed.add_field(name="Video link", value=clip(row["video_url"]), inline=False)
    embed.add_field(name="Caption", value=clip(row["caption"]), inline=False)
    embed.add_field(name="Description", value=clip(row["description"]), inline=False)
    embed.add_field(name="Hashtags", value=clip(row["hashtags"]), inline=False)
    embed.add_field(name="Status", value=status_text, inline=True)
    if row["resubmission_of"]:
        embed.add_field(name="Resubmission of", value=f"#{row['resubmission_of']}", inline=True)
    if row["reviewer_id"]:
        embed.add_field(name="Reviewed by", value=f"<@{row['reviewer_id']}>", inline=True)
        embed.add_field(name="Review date", value=discord_ts(row["reviewed_at"]), inline=True)
    if row["reject_reason"]:
        label = "Feedback" if row["status"] == "changes" else "Reason"
        embed.add_field(name=label, value=clip(row["reject_reason"]), inline=False)
    if row["posted_url"]:
        embed.add_field(name="Posted link", value=clip(row["posted_url"]), inline=False)
    if row["posted_at"]:
        embed.add_field(name="Posted", value=discord_ts(row["posted_at"]), inline=True)
    return embed


async def submission_block_reason(bot: UKCRBot, guild_id: int, user_id: int, url: Optional[str] = None) -> Optional[str]:
    if config.max_pending_per_user:
        pending = await bot.db.scalar(
            "SELECT COUNT(*) FROM video_submissions WHERE guild_id=? AND user_id=? AND status='pending'",
            (guild_id, user_id),
        )
        if pending >= config.max_pending_per_user:
            return (
                f"You already have **{pending}** submissions waiting for review (the limit is "
                f"{config.max_pending_per_user}). Please wait for the team to review them first - "
                "you can check on them with `/mysubmissions`."
            )
    if url:
        key = normalise_url(url)
        rows = await bot.db.all(
            "SELECT id, video_url, status FROM video_submissions WHERE guild_id=? AND status IN ('pending', 'approved', 'posted')",
            (guild_id,),
        )
        for row in rows:
            if normalise_url(row["video_url"]) == key:
                state = STATUS_STYLE[row["status"]][0]
                return f"That video has already been submitted (submission **#{row['id']}** - {state})."
    return None


async def approved_count(bot: UKCRBot, guild_id: int, user_id: int) -> int:
    return await bot.db.scalar(
        "SELECT COUNT(*) FROM video_submissions WHERE guild_id=? AND user_id=? AND status IN ('approved', 'posted')",
        (guild_id, user_id),
    )


async def creator_role(bot: UKCRBot, guild: discord.Guild, threshold: int, name: str) -> Optional[discord.Role]:
    role_id = await bot.db.setting_get(guild.id, f"creator_role_{threshold}")
    role = guild.get_role(role_id) if role_id else None
    return role or discord.utils.get(guild.roles, name=name)


async def grant_creator_roles(bot: UKCRBot, guild: discord.Guild, user_id: int) -> list:
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return []
    count = await approved_count(bot, guild.id, user_id)
    granted = []
    for threshold, name, _colour in CREATOR_TIERS:
        if count < threshold:
            continue
        role = await creator_role(bot, guild, threshold, name)
        if role is None or role in member.roles:
            continue
        try:
            await member.add_roles(role, reason=f"{count} approved video(s)")
            granted.append(role)
            log.info("Gave %s to %s (%d approved)", role.name, member, count)
        except discord.Forbidden:
            log.error("Can't give %s the %s role: give the bot Manage Roles and keep its role above it", member, name)
        except discord.HTTPException:
            log.exception("Failed to give creator role %s", name)
    return granted


async def post_showcase(bot: UKCRBot, guild: discord.Guild, row: sqlite3.Row) -> None:
    channel_id = await bot.db.setting_get(guild.id, "showcase_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return
    embed = discord.Embed(
        title="New approved video",
        description=clip(row["caption"], 500),
        colour=discord.Colour.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Creator", value=f"<@{row['user_id']}>", inline=True)
    embed.add_field(name="Approved videos", value=str(await approved_count(bot, guild.id, row["user_id"])), inline=True)
    embed.add_field(name="Hashtags", value=clip(row["hashtags"]), inline=False)
    embed.add_field(name="Watch", value=clip(row["video_url"]), inline=False)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        log.exception("Failed to post to the showcase channel")


async def after_review(bot: UKCRBot, guild: discord.Guild, row: sqlite3.Row) -> bool:
    status = row["status"]
    granted = []
    if status == "approved":
        granted = await grant_creator_roles(bot, guild, row["user_id"])
        await post_showcase(bot, guild, row)
        title = "Your video was approved ✅"
        text = f"Your video submission **#{row['id']}** has been approved! The team will post it to the UKCR socials soon."
    elif status == "changes":
        title = "Changes requested ✏️"
        text = (
            f"The team has asked for some changes to your video submission **#{row['id']}**.\n"
            f"Update it and send it back with `/resubmit submission_id:{row['id']}`."
        )
    else:
        title = "Your video wasn't approved ❌"
        text = f"Your video submission **#{row['id']}** wasn't approved this time."

    embed = discord.Embed(title=title, description=text, colour=STATUS_STYLE[status][1], timestamp=datetime.now(timezone.utc))
    if row["reject_reason"]:
        embed.add_field(name="Feedback" if status == "changes" else "Reason", value=clip(row["reject_reason"]), inline=False)
    if granted:
        embed.add_field(name="🎉 New role unlocked", value=", ".join(role.name for role in granted), inline=False)
    embed.set_footer(text=guild.name)
    return await dm_user(bot, row["user_id"], embed)


async def apply_review(bot: UKCRBot, reviewer_id: int, message_id: int, new_status: str, reason: Optional[str]):
    cur = await bot.db.run(
        """UPDATE video_submissions
           SET status=?, reviewer_id=?, reviewed_at=?, reject_reason=?
           WHERE message_id=? AND status='pending'""",
        (new_status, reviewer_id, now_iso(), reason, message_id),
    )
    row = await bot.db.one("SELECT * FROM video_submissions WHERE message_id=?", (message_id,))
    if row is None:
        return None, "missing"
    if cur.rowcount == 0:
        return row, "already"
    return row, "ok"


async def approve_submission(interaction: discord.Interaction) -> None:
    bot: UKCRBot = interaction.client
    if not is_staff(interaction.user):
        await reply(interaction, STAFF_ONLY)
        return
    message = interaction.message
    row, state = await apply_review(bot, interaction.user.id, message.id, "approved", None)
    if state == "missing":
        await reply(interaction, "I couldn't find this submission in the database.")
        return
    if state == "already":
        await reply(interaction, f"This submission has already been **{row['status']}**.")
        try:
            await message.edit(embed=build_submission_embed(row), view=None)
        except discord.HTTPException:
            pass
        return

    log.info("Submission #%s approved by %s", row["id"], interaction.user)
    await interaction.response.edit_message(content=None, embed=build_submission_embed(row), view=None)
    if not await after_review(bot, interaction.guild, row):
        await interaction.followup.send("Approved - but I couldn't DM the submitter (their DMs are closed).", ephemeral=True)


async def open_reason_modal(interaction: discord.Interaction, new_status: str) -> None:
    bot: UKCRBot = interaction.client
    if not is_staff(interaction.user):
        await reply(interaction, STAFF_ONLY)
        return
    message = interaction.message
    row = await bot.db.one("SELECT * FROM video_submissions WHERE message_id=?", (message.id,))
    if row is None:
        await reply(interaction, "I couldn't find this submission in the database.")
        return
    if row["status"] != "pending":
        await reply(interaction, f"This submission has already been **{row['status']}**.")
        try:
            await message.edit(embed=build_submission_embed(row), view=None)
        except discord.HTTPException:
            pass
        return
    await interaction.response.send_modal(ReviewReasonModal(new_status, message))


class ReviewReasonModal(SafeModal):
    def __init__(self, new_status: str, target_message: discord.Message) -> None:
        super().__init__(title="Reject submission" if new_status == "rejected" else "Request changes")
        self.new_status = new_status
        self.target_message = target_message
        self.reason_input = discord.ui.TextInput(
            label="Reason (sent to the submitter)" if new_status == "rejected" else "What needs changing? (sent to them)",
            style=discord.TextStyle.paragraph,
            placeholder="Be specific so they know what to do next time.",
            min_length=3,
            max_length=500,
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: UKCRBot = interaction.client
        if not is_staff(interaction.user):
            await reply(interaction, STAFF_ONLY)
            return
        await interaction.response.defer(ephemeral=True)

        row, state = await apply_review(bot, interaction.user.id, self.target_message.id, self.new_status, self.reason_input.value.strip())
        if state == "missing":
            await interaction.followup.send("I couldn't find this submission in the database.", ephemeral=True)
            return
        if state == "already":
            await interaction.followup.send(f"This submission has already been **{row['status']}**.", ephemeral=True)
            try:
                await self.target_message.edit(embed=build_submission_embed(row), view=None)
            except discord.HTTPException:
                pass
            return

        log.info("Submission #%s marked %s by %s", row["id"], self.new_status, interaction.user)
        try:
            await self.target_message.edit(content=None, embed=build_submission_embed(row), view=None)
        except discord.HTTPException:
            log.exception("Failed to update review message for submission %s", row["id"])
        dm_ok = await after_review(bot, interaction.guild, row)
        note = "Done - the submitter has been notified." if dm_ok else "Done - but I couldn't DM the submitter (their DMs are closed)."
        await interaction.followup.send(note, ephemeral=True)


class ReviewView(SafeView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="ukcr:video_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await approve_submission(interaction)

    @discord.ui.button(label="Request changes", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="ukcr:video_changes")
    async def request_changes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_reason_modal(interaction, "changes")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="ukcr:video_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_reason_modal(interaction, "rejected")


class VideoSubmissionModal(SafeModal, title="UKCR Video Submission"):
    def __init__(self, prefill: Optional[sqlite3.Row] = None) -> None:
        super().__init__()
        self.resubmission_of: Optional[int] = prefill["id"] if prefill else None
        if prefill:
            self.title = f"Resubmit video #{prefill['id']}"

        self.video_input = discord.ui.TextInput(
            label="Video File (shareable link)",
            placeholder="https://…",
            default=prefill["video_url"] if prefill else None,
            max_length=500,
        )
        self.caption_input = discord.ui.TextInput(
            label="Caption",
            style=discord.TextStyle.paragraph,
            placeholder="Caption for the social media post",
            default=prefill["caption"] if prefill else None,
            max_length=1000,
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            placeholder="What happens in the video?",
            default=prefill["description"] if prefill else None,
            max_length=1000,
        )
        self.hashtags_input = discord.ui.TextInput(
            label="Hashtags (#)",
            placeholder="#UKCR #ERLC  (tip: /hashtags get)",
            default=prefill["hashtags"] if prefill else None,
            max_length=300,
        )
        for item in (self.video_input, self.caption_input, self.description_input, self.hashtags_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: UKCRBot = interaction.client
        url = self.video_input.value.strip()
        if not URL_RE.match(url):
            await reply(interaction, "That doesn't look like a valid link. Paste a shareable URL starting with `https://`.")
            return
        guild = interaction.guild
        if guild is None:
            return

        blocked = await submission_block_reason(bot, guild.id, interaction.user.id, url)
        if blocked:
            await reply(interaction, blocked)
            return

        channel = guild.get_channel(config.video_channel_id) if config.video_channel_id else None
        if not isinstance(channel, discord.abc.Messageable):
            log.error("Submissions channel %s not found", config.video_channel_id)
            await reply(interaction, "The submissions channel couldn't be found. Please tell a staff member.")
            return

        await interaction.response.defer(ephemeral=True)
        cur = await bot.db.run(
            """INSERT INTO video_submissions
               (guild_id, user_id, video_url, caption, description, hashtags, submitted_at, resubmission_of)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                guild.id,
                interaction.user.id,
                url,
                self.caption_input.value.strip(),
                self.description_input.value.strip(),
                self.hashtags_input.value.strip(),
                now_iso(),
                self.resubmission_of,
            ),
        )
        submission_id = cur.lastrowid
        row = await bot.db.one("SELECT * FROM video_submissions WHERE id=?", (submission_id,))

        try:
            message = await channel.send(
                content=f"<@&{config.staff_role_id}> new video submission to review.",
                embed=build_submission_embed(row),
                view=ReviewView(),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException:
            log.exception("Failed to post submission %s", submission_id)
            await interaction.followup.send("Your submission was saved but I couldn't post it. Please tell a staff member.", ephemeral=True)
            return

        await bot.db.run(
            "UPDATE video_submissions SET channel_id=?, message_id=? WHERE id=?",
            (message.channel.id, message.id, submission_id),
        )
        log.info("Submission #%s received from %s", submission_id, interaction.user)
        await interaction.followup.send(
            f"Submission **#{submission_id}** sent to the team for review! "
            "Track it with `/mysubmissions` - I'll DM you when it's reviewed.",
            ephemeral=True,
        )


class VideoPanelView(SafeView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit a Video", style=discord.ButtonStyle.primary, emoji="🎬", custom_id="ukcr:video_panel")
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot: UKCRBot = interaction.client
        blocked = await submission_block_reason(bot, interaction.guild_id, interaction.user.id) if interaction.guild_id else None
        if blocked:
            await reply(interaction, blocked)
            return
        await interaction.response.send_modal(VideoSubmissionModal())


class Submissions(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(VideoPanelView())
        self.bot.add_view(ReviewView())
        self.pending_reminder_loop.start()

    async def cog_unload(self) -> None:
        self.pending_reminder_loop.cancel()

    @tasks.loop(minutes=30)
    async def pending_reminder_loop(self) -> None:
        try:
            await self.remind_pending()
        except Exception:
            log.exception("Pending submission reminder failed")

    @pending_reminder_loop.before_loop
    async def before_pending_reminder_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def remind_pending(self) -> None:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(hours=config.pending_reminder_hours)).isoformat()
        rows = await self.bot.db.all(
            """SELECT * FROM video_submissions
               WHERE status='pending' AND submitted_at <= ?
                 AND (last_reminded_at IS NULL OR last_reminded_at <= ?)
               ORDER BY submitted_at""",
            (cutoff, cutoff),
        )
        if not rows:
            return
        channel = self.bot.get_channel(config.video_channel_id)
        if channel is None:
            return

        lines = []
        for row in rows[:10]:
            waited = fmt_duration(now - datetime.fromisoformat(row["submitted_at"]))
            line = f"**#{row['id']}** · <@{row['user_id']}> · waiting **{waited}**"
            if row["channel_id"] and row["message_id"]:
                line += f" · [review]({message_link(row['guild_id'], row['channel_id'], row['message_id'])})"
            lines.append(line)
        if len(rows) > 10:
            lines.append(f"…and {len(rows) - 10} more")

        embed = discord.Embed(title="⏳ Submissions waiting for review", description="\n".join(lines), colour=discord.Colour.gold())
        try:
            await channel.send(
                content=f"<@&{config.staff_role_id}>",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException:
            log.exception("Failed to send the pending-submissions reminder")
            return
        for row in rows:
            await self.bot.db.run("UPDATE video_submissions SET last_reminded_at=? WHERE id=?", (now.isoformat(), row["id"]))
        log.info("Reminded staff about %d pending submission(s)", len(rows))

    @app_commands.command(name="videosubmissionpanel", description="Publishes the video submission button")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def videosubmissionpanel(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="UKCR Video Submissions",
            description=(
                "Got a video for the UKCR socials? Press the button below and fill in the form.\n"
                "You'll need a shareable link, a caption, a description and hashtags "
                "(use `/hashtags get` for ready-made packs).\n"
                "Track your videos with `/mysubmissions` and climb the `/leaderboard`!"
            ),
            colour=BRAND_COLOUR,
        )
        await interaction.channel.send(embed=embed, view=VideoPanelView())
        await interaction.response.send_message("Video submission panel published.", ephemeral=True)

    @app_commands.command(name="videosubmission", description="Opens the video submission form")
    @app_commands.guild_only()
    async def videosubmission(self, interaction: discord.Interaction) -> None:
        blocked = await submission_block_reason(self.bot, interaction.guild_id, interaction.user.id)
        if blocked:
            await reply(interaction, blocked)
            return
        await interaction.response.send_modal(VideoSubmissionModal())

    @app_commands.command(name="resubmit", description="Resubmit a video the team asked you to change")
    @app_commands.guild_only()
    @app_commands.describe(submission_id="The submission number (shown in the DM or /mysubmissions)")
    async def resubmit(self, interaction: discord.Interaction, submission_id: int) -> None:
        row = await self.bot.db.one("SELECT * FROM video_submissions WHERE id=? AND guild_id=?", (submission_id, interaction.guild_id))
        if row is None or row["user_id"] != interaction.user.id:
            await reply(interaction, "I couldn't find one of your submissions with that number.")
            return
        if row["status"] != "changes":
            await reply(
                interaction,
                f"Submission **#{submission_id}** is **{row['status']}**. You can only resubmit videos where the team requested changes.",
            )
            return
        blocked = await submission_block_reason(self.bot, interaction.guild_id, interaction.user.id)
        if blocked:
            await reply(interaction, blocked)
            return
        await interaction.response.send_modal(VideoSubmissionModal(prefill=row))

    @app_commands.command(name="mysubmissions", description="See the status of your video submissions")
    @app_commands.guild_only()
    async def mysubmissions(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.all(
            "SELECT * FROM video_submissions WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
            (interaction.guild_id, interaction.user.id),
        )
        if not rows:
            await reply(interaction, "You haven't submitted any videos yet. Use `/videosubmission` to send your first one!")
            return

        lines = []
        for row in rows:
            caption = clip(row["caption"].replace("\n", " "), 50)
            lines.append(f"**#{row['id']}** · {STATUS_STYLE[row['status']][0]} · {caption} · {discord_ts(row['submitted_at'], 'd')}")
        embed = discord.Embed(title="Your video submissions", description="\n".join(lines), colour=BRAND_COLOUR)

        count = await approved_count(self.bot, interaction.guild_id, interaction.user.id)
        upcoming = next(((need, name) for need, name, _ in CREATOR_TIERS if count < need), None)
        footer = f"Approved videos: {count}"
        footer += f" · {upcoming[0] - count} more for {upcoming[1]}" if upcoming else " · Top creator tier reached 🎉"
        embed.set_footer(text=footer)
        await reply(interaction, embed=embed)

    @app_commands.command(name="leaderboard", description="Top video contributors for the UKCR socials")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.all(
            """SELECT user_id, COUNT(*) AS n FROM video_submissions
               WHERE guild_id=? AND status IN ('approved', 'posted')
               GROUP BY user_id ORDER BY n DESC, MIN(reviewed_at) ASC LIMIT 10""",
            (interaction.guild_id,),
        )
        if not rows:
            await reply(interaction, "No approved videos yet - be the first! Use `/videosubmission`.")
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, row in enumerate(rows):
            marker = medals[index] if index < len(medals) else f"**{index + 1}.**"
            noun = "video" if row["n"] == 1 else "videos"
            lines.append(f"{marker} <@{row['user_id']}> - **{row['n']}** approved {noun}")
        embed = discord.Embed(title="🏆 UKCR Creator Leaderboard", description="\n".join(lines), colour=discord.Colour.gold())
        embed.set_footer(text="Earn creator roles by getting your videos approved!")
        await reply(interaction, embed=embed, ephemeral=False)

    @app_commands.command(name="queue", description="Approved videos that haven't been posted yet (staff)")
    @app_commands.guild_only()
    @app_commands.check(staff_check)
    async def queue(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.all(
            "SELECT * FROM video_submissions WHERE guild_id=? AND status='approved' ORDER BY reviewed_at ASC LIMIT 15",
            (interaction.guild_id,),
        )
        if not rows:
            await reply(interaction, "Nothing waiting - every approved video has been posted. 🎉")
            return
        lines = [
            f"**#{row['id']}** · <@{row['user_id']}> · approved {discord_ts(row['reviewed_at'], 'R')}\n└ {clip(row['video_url'], 90)}"
            for row in rows
        ]
        embed = discord.Embed(title="📣 Approved - waiting to be posted", description="\n".join(lines), colour=discord.Colour.green())
        embed.set_footer(text="Use /markposted submission_id link once a video is live")
        await reply(interaction, embed=embed)

    @app_commands.command(name="markposted", description="Mark an approved video as posted and add the live link (staff)")
    @app_commands.guild_only()
    @app_commands.check(staff_check)
    @app_commands.describe(submission_id="The submission number", link="Link to the live post")
    async def markposted(self, interaction: discord.Interaction, submission_id: int, link: str) -> None:
        link = link.strip()
        if not URL_RE.match(link):
            await reply(interaction, "That doesn't look like a valid link (it must start with `https://`).")
            return
        row = await self.bot.db.one("SELECT * FROM video_submissions WHERE id=? AND guild_id=?", (submission_id, interaction.guild_id))
        if row is None:
            await reply(interaction, "I couldn't find a submission with that number.")
            return
        if row["status"] not in ("approved", "posted"):
            await reply(interaction, f"Only approved submissions can be marked as posted (this one is **{row['status']}**).")
            return

        await interaction.response.defer(ephemeral=True)
        first_time = row["status"] == "approved"
        await self.bot.db.run(
            "UPDATE video_submissions SET status='posted', posted_url=?, posted_at=? WHERE id=?",
            (link, now_iso(), submission_id),
        )
        row = await self.bot.db.one("SELECT * FROM video_submissions WHERE id=?", (submission_id,))
        log.info("Submission #%s marked posted by %s", submission_id, interaction.user)

        channel = interaction.guild.get_channel(row["channel_id"]) if row["channel_id"] else None
        if channel is not None and row["message_id"]:
            try:
                message = await channel.fetch_message(row["message_id"])
                await message.edit(embed=build_submission_embed(row), view=None)
            except discord.HTTPException:
                log.warning("Couldn't update the review message for submission %s", submission_id)

        note = f"Submission **#{submission_id}** marked as posted."
        if first_time:
            embed = discord.Embed(
                title="Your video is live! 🎉",
                description=f"Your submission **#{submission_id}** has been posted on the UKCR socials.\n{link}",
                colour=discord.Colour.teal(),
            )
            embed.set_footer(text=interaction.guild.name)
            if not await dm_user(self.bot, row["user_id"], embed):
                note += " (I couldn't DM the creator - their DMs are closed.)"
        await interaction.followup.send(note, ephemeral=True)

    @app_commands.command(name="submissionstats", description="Video submission statistics (staff)")
    @app_commands.guild_only()
    @app_commands.check(staff_check)
    async def submissionstats(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        rows = await self.bot.db.all("SELECT status, COUNT(*) AS n FROM video_submissions WHERE guild_id=? GROUP BY status", (guild_id,))
        counts = {r["status"]: r["n"] for r in rows}
        total = sum(counts.values())
        if total == 0:
            await reply(interaction, "No submissions yet.")
            return

        approved_total = counts.get("approved", 0) + counts.get("posted", 0)
        decided = approved_total + counts.get("rejected", 0)
        approval_rate = f"{approved_total / decided * 100:.0f}%" if decided else "n/a"

        reviewed = await self.bot.db.all(
            "SELECT submitted_at, reviewed_at FROM video_submissions WHERE guild_id=? AND reviewed_at IS NOT NULL", (guild_id,)
        )
        if reviewed:
            waits = [datetime.fromisoformat(r["reviewed_at"]) - datetime.fromisoformat(r["submitted_at"]) for r in reviewed]
            average = fmt_duration(sum(waits, timedelta()) / len(waits))
        else:
            average = "n/a"

        top = await self.bot.db.all(
            """SELECT user_id, COUNT(*) AS n FROM video_submissions
               WHERE guild_id=? AND status IN ('approved', 'posted')
               GROUP BY user_id ORDER BY n DESC, MIN(reviewed_at) ASC LIMIT 5""",
            (guild_id,),
        )
        top_text = "\n".join(f"{i}. <@{r['user_id']}> - {r['n']}" for i, r in enumerate(top, start=1)) or "No approved videos yet"

        embed = discord.Embed(title="📊 Video submission stats", colour=BRAND_COLOUR)
        embed.add_field(name="Total", value=str(total), inline=True)
        embed.add_field(name="⏳ Pending", value=str(counts.get("pending", 0)), inline=True)
        embed.add_field(name="✏️ Changes requested", value=str(counts.get("changes", 0)), inline=True)
        embed.add_field(name="✅ Approved (not posted)", value=str(counts.get("approved", 0)), inline=True)
        embed.add_field(name="📣 Posted", value=str(counts.get("posted", 0)), inline=True)
        embed.add_field(name="❌ Rejected", value=str(counts.get("rejected", 0)), inline=True)
        embed.add_field(name="Approval rate", value=approval_rate, inline=True)
        embed.add_field(name="Average review time", value=average, inline=True)
        embed.add_field(name="Top contributors", value=top_text, inline=False)
        await reply(interaction, embed=embed)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Submissions(bot))
