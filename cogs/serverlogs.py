import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import UKCRBot
from core.config import NEW_ACCOUNT_DAYS, config
from core.console import get_logger
from core.constants import LOG_BLUE, LOG_CHANNELS, LOG_GOLD, LOG_GREEN, LOG_ORANGE, LOG_RED
from core.helpers import clip, is_staff, reply
from core.logging_utils import audit_fields, diff_fields, log_embed, mod_fields, recent_audit_entry, send_log

log = get_logger("serverlogs")


class ServerLogs(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    def in_ticket_category(self, channel) -> bool:
        return config.ticket_category_id is not None and getattr(channel, "category_id", None) == config.ticket_category_id

    async def skip_message_log(self, guild: discord.Guild, channel_id: int) -> bool:
        if channel_id in await self.bot.db.log_channel_ids(guild.id):
            return True
        return self.in_ticket_category(guild.get_channel(channel_id))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != config.guild_id:
            return
        guild = member.guild
        if member.bot:
            fields = [("Bot", member.mention, True), ("Bot ID", member.id, True)]
            fields += await audit_fields(guild, discord.AuditLogAction.bot_add, member.id)
            await send_log(self.bot, guild, "safety", log_embed("Bot added", LOG_GOLD, user=member, fields=fields))
            return
        await send_log(
            self.bot,
            guild,
            "member",
            log_embed(
                "Member joined",
                LOG_GREEN,
                f"{member.mention} joined the server.",
                user=member,
                fields=[
                    ("Account created", discord.utils.format_dt(member.created_at, "R"), True),
                    ("Member count", guild.member_count, True),
                    ("User ID", member.id, True),
                ],
            ),
        )
        age_days = (datetime.now(timezone.utc) - member.created_at).days
        if age_days < NEW_ACCOUNT_DAYS:
            await send_log(
                self.bot,
                guild,
                "safety",
                log_embed(
                    "New account joined",
                    LOG_GOLD,
                    f"{member.mention} joined with an account only **{age_days}** day(s) old.",
                    user=member,
                    fields=[
                        ("Account created", discord.utils.format_dt(member.created_at, "F"), False),
                        ("User ID", member.id, True),
                    ],
                ),
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != config.guild_id or member.bot:
            return
        guild = member.guild
        roles = [r.mention for r in reversed(member.roles) if r != guild.default_role]
        await send_log(
            self.bot,
            guild,
            "member",
            log_embed(
                "Member left",
                LOG_RED,
                f"{member.mention} left the server.",
                user=member,
                fields=[
                    ("Joined", discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown", True),
                    ("Member count", guild.member_count, True),
                    ("User ID", member.id, True),
                    ("Roles", ", ".join(roles) or "None", False),
                ],
            ),
        )
        await asyncio.sleep(1)
        entry = await recent_audit_entry(guild, discord.AuditLogAction.kick, member.id)
        if entry is not None:
            await send_log(
                self.bot,
                guild,
                "moderations",
                log_embed(
                    "Member kicked",
                    LOG_ORANGE,
                    user=member,
                    fields=[("Member", f"{member.mention} ({member.id})", False)] + mod_fields(entry),
                ),
            )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        if guild.id != config.guild_id:
            return
        await asyncio.sleep(1)
        entry = await recent_audit_entry(guild, discord.AuditLogAction.ban, user.id)
        await send_log(
            self.bot,
            guild,
            "moderations",
            log_embed(
                "Member banned",
                LOG_RED,
                user=user,
                fields=[("User", f"{user.mention} ({user.id})", False)] + mod_fields(entry),
            ),
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        if guild.id != config.guild_id:
            return
        await asyncio.sleep(1)
        entry = await recent_audit_entry(guild, discord.AuditLogAction.unban, user.id)
        await send_log(
            self.bot,
            guild,
            "moderations",
            log_embed(
                "Member unbanned",
                LOG_GREEN,
                user=user,
                fields=[("User", f"{user.mention} ({user.id})", False)] + mod_fields(entry),
            ),
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        if guild.id != config.guild_id or after.bot:
            return

        if before.nick != after.nick:
            await send_log(
                self.bot,
                guild,
                "member",
                log_embed(
                    "Nickname changed",
                    LOG_BLUE,
                    f"{after.mention} changed their nickname.",
                    user=after,
                    fields=[("Before", before.nick or "None", True), ("After", after.nick or "None", True)],
                ),
            )

        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if added or removed:
            just_joined = after.joined_at is not None and (datetime.now(timezone.utc) - after.joined_at).total_seconds() < 30
            auto_role_only = just_joined and not removed and {r.id for r in added} == {config.member_role_id}
            if not auto_role_only:
                fields = []
                if added:
                    fields.append(("Roles added", ", ".join(r.mention for r in added), False))
                if removed:
                    fields.append(("Roles removed", ", ".join(r.mention for r in removed), False))
                audit = await audit_fields(guild, discord.AuditLogAction.member_role_update, after.id)
                fields += audit
                await send_log(
                    self.bot,
                    guild,
                    "member",
                    log_embed("Roles updated", LOG_BLUE, f"Roles changed for {after.mention}.", user=after, fields=fields),
                )
                for role in added:
                    if role.permissions.administrator:
                        await send_log(
                            self.bot,
                            guild,
                            "safety",
                            log_embed(
                                "Administrator role given",
                                LOG_GOLD,
                                f"{after.mention} was given {role.mention}, which has **Administrator**.",
                                user=after,
                                fields=audit,
                            ),
                        )

        if before.timed_out_until != after.timed_out_until:
            now = datetime.now(timezone.utc)
            if after.timed_out_until and after.timed_out_until > now:
                title, colour = "Member timed out", LOG_ORANGE
                fields = [
                    ("Member", after.mention, True),
                    (
                        "Until",
                        f"{discord.utils.format_dt(after.timed_out_until, 'F')} ({discord.utils.format_dt(after.timed_out_until, 'R')})",
                        False,
                    ),
                ]
            else:
                title, colour = "Timeout removed", LOG_GREEN
                fields = [("Member", after.mention, True)]
            await asyncio.sleep(1)
            entry = await recent_audit_entry(guild, discord.AuditLogAction.member_update, after.id)
            await send_log(self.bot, guild, "moderations", log_embed(title, colour, user=after, fields=fields + mod_fields(entry)))

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        guild = self.bot.get_guild(config.guild_id)
        if guild is None or after.bot or guild.get_member(after.id) is None:
            return
        fields = []
        if before.name != after.name:
            fields.append(("Username", f"{before.name} → {after.name}", False))
        if before.global_name != after.global_name:
            fields.append(("Display name", f"{before.global_name or 'None'} → {after.global_name or 'None'}", False))
        if before.avatar != after.avatar:
            fields.append(("Avatar", "Profile picture changed", False))
        if fields:
            embed = log_embed("Profile updated", LOG_BLUE, f"{after.mention} updated their profile.", user=after, fields=fields)
            embed.set_thumbnail(url=after.display_avatar.url)
            await send_log(self.bot, guild, "member", embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id != config.guild_id:
            return
        guild = self.bot.get_guild(config.guild_id)
        if guild is None or await self.skip_message_log(guild, payload.channel_id):
            return
        message = payload.cached_message
        if message is not None:
            if message.author.bot:
                return
            fields = [
                ("Author", f"{message.author.mention} ({message.author.id})", True),
                ("Channel", f"<#{payload.channel_id}>", True),
                ("Message ID", message.id, True),
            ]
            if message.attachments:
                fields.append(("Attachments", "\n".join(a.url for a in message.attachments), False))
            embed = log_embed(
                "Message deleted", LOG_RED, clip(message.content or "*No text content*", 4000), user=message.author, fields=fields
            )
        else:
            embed = log_embed(
                "Message deleted",
                LOG_RED,
                "This message wasn't in my cache, so the author and content are unknown.",
                fields=[("Channel", f"<#{payload.channel_id}>", True), ("Message ID", payload.message_id, True)],
            )
        await send_log(self.bot, guild, "general", embed)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id != config.guild_id:
            return
        guild = self.bot.get_guild(config.guild_id)
        if guild is None or await self.skip_message_log(guild, payload.channel_id):
            return
        await send_log(
            self.bot,
            guild,
            "general",
            log_embed(
                "Messages bulk deleted",
                LOG_RED,
                fields=[("Channel", f"<#{payload.channel_id}>", True), ("Messages deleted", len(payload.message_ids), True)],
            ),
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.guild.id != config.guild_id or after.author.bot:
            return
        if before.content == after.content or await self.skip_message_log(after.guild, after.channel.id):
            return
        await send_log(
            self.bot,
            after.guild,
            "general",
            log_embed(
                "Message edited",
                LOG_ORANGE,
                f"[Jump to message]({after.jump_url})",
                user=after.author,
                fields=[
                    ("Before", before.content or "*empty*", False),
                    ("After", after.content or "*empty*", False),
                    ("Author", f"{after.author.mention} ({after.author.id})", True),
                    ("Channel", after.channel.mention, True),
                ],
            ),
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.guild.id != config.guild_id or member.bot or before.channel == after.channel:
            return
        if before.channel is None:
            title, colour, text = "Joined voice", LOG_GREEN, f"{member.mention} joined {after.channel.mention}."
        elif after.channel is None:
            title, colour, text = "Left voice", LOG_RED, f"{member.mention} left {before.channel.mention}."
        else:
            title, colour = "Moved voice channel", LOG_BLUE
            text = f"{member.mention} moved from {before.channel.mention} to {after.channel.mention}."
        await send_log(self.bot, member.guild, "general", log_embed(title, colour, text, user=member))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id != config.guild_id or self.in_ticket_category(channel) or channel.category_id == config.log_category_id:
            return
        fields = [
            ("Channel", channel.mention, True),
            ("Type", channel.type.name.replace("_", " ").title(), True),
            ("Category", channel.category.name if channel.category else "None", True),
        ]
        fields += await audit_fields(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        await send_log(self.bot, channel.guild, "audit", log_embed("Channel created", LOG_GREEN, fields=fields))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id != config.guild_id or self.in_ticket_category(channel):
            return
        fields = [
            ("Channel", f"#{channel.name}", True),
            ("Type", channel.type.name.replace("_", " ").title(), True),
            ("Category", channel.category.name if channel.category else "None", True),
        ]
        fields += await audit_fields(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        await send_log(self.bot, channel.guild, "audit", log_embed("Channel deleted", LOG_RED, fields=fields))

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if after.guild.id != config.guild_id or self.in_ticket_category(after):
            return
        fields = diff_fields(
            before,
            after,
            [("Name", "name"), ("Topic", "topic"), ("Slowmode", "slowmode_delay"), ("NSFW", "nsfw"), ("Category", "category")],
        )
        if before.overwrites != after.overwrites:
            fields.append(("Permissions", "Permission overwrites were changed", False))
        if not fields:
            return
        fields.insert(0, ("Channel", after.mention, True))
        fields += await audit_fields(
            after.guild,
            (
                discord.AuditLogAction.channel_update,
                discord.AuditLogAction.overwrite_create,
                discord.AuditLogAction.overwrite_update,
                discord.AuditLogAction.overwrite_delete,
            ),
            after.id,
        )
        await send_log(self.bot, after.guild, "audit", log_embed("Channel updated", LOG_ORANGE, fields=fields))

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        if role.guild.id != config.guild_id:
            return
        fields = [("Role", f"{role.mention} ({role.name})", True), ("Colour", str(role.colour), True)]
        fields += await audit_fields(role.guild, discord.AuditLogAction.role_create, role.id)
        await send_log(self.bot, role.guild, "audit", log_embed("Role created", LOG_GREEN, fields=fields))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if role.guild.id != config.guild_id:
            return
        fields = [("Role", role.name, True), ("Colour", str(role.colour), True)]
        fields += await audit_fields(role.guild, discord.AuditLogAction.role_delete, role.id)
        await send_log(self.bot, role.guild, "audit", log_embed("Role deleted", LOG_RED, fields=fields))

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if after.guild.id != config.guild_id:
            return
        fields = diff_fields(
            before, after, [("Name", "name"), ("Colour", "colour"), ("Shown separately", "hoist"), ("Mentionable", "mentionable")]
        )
        if before.permissions != after.permissions:
            gained = [n.replace("_", " ") for n, v in after.permissions if v and not getattr(before.permissions, n)]
            lost = [n.replace("_", " ") for n, v in before.permissions if v and not getattr(after.permissions, n)]
            if gained:
                fields.append(("Permissions added", ", ".join(gained), False))
            if lost:
                fields.append(("Permissions removed", ", ".join(lost), False))
        if not fields:
            return
        fields.insert(0, ("Role", after.mention, True))
        fields += await audit_fields(after.guild, discord.AuditLogAction.role_update, after.id)
        await send_log(self.bot, after.guild, "audit", log_embed("Role updated", LOG_ORANGE, fields=fields))

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if after.id != config.guild_id:
            return
        fields = diff_fields(
            before,
            after,
            [
                ("Name", "name"),
                ("Owner ID", "owner_id"),
                ("Verification level", "verification_level"),
                ("Content filter", "explicit_content_filter"),
                ("System channel", "system_channel"),
                ("Rules channel", "rules_channel"),
                ("AFK channel", "afk_channel"),
            ],
        )
        if before.icon != after.icon:
            fields.append(("Icon", "Server icon changed", False))
        if not fields:
            return
        fields += await audit_fields(after, discord.AuditLogAction.guild_update)
        await send_log(self.bot, after, "audit", log_embed("Server updated", LOG_ORANGE, fields=fields))

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild = self.bot.get_guild(config.guild_id)
        if guild is None or invite.guild is None or invite.guild.id != config.guild_id:
            return
        await send_log(
            self.bot,
            guild,
            "audit",
            log_embed(
                "Invite created",
                LOG_GREEN,
                fields=[
                    ("Code", invite.code, True),
                    ("Channel", getattr(invite.channel, "mention", str(invite.channel)), True),
                    ("Created by", invite.inviter.mention if invite.inviter else "Unknown", True),
                    ("Max uses", invite.max_uses or "Unlimited", True),
                    ("Expires", discord.utils.format_dt(invite.expires_at, "R") if invite.expires_at else "Never", True),
                ],
            ),
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        guild = self.bot.get_guild(config.guild_id)
        if guild is None or invite.guild is None or invite.guild.id != config.guild_id:
            return
        await send_log(
            self.bot,
            guild,
            "audit",
            log_embed(
                "Invite deleted",
                LOG_RED,
                fields=[("Code", invite.code, True), ("Channel", getattr(invite.channel, "mention", str(invite.channel)), True)],
            ),
        )

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel) -> None:
        if channel.guild.id != config.guild_id:
            return
        fields = [("Channel", channel.mention, True)]
        fields += await audit_fields(
            channel.guild,
            (
                discord.AuditLogAction.webhook_create,
                discord.AuditLogAction.webhook_update,
                discord.AuditLogAction.webhook_delete,
            ),
        )
        await send_log(
            self.bot,
            channel.guild,
            "safety",
            log_embed("Webhooks changed", LOG_GOLD, "A webhook was created, edited or deleted.", fields=fields),
        )

    @commands.Cog.listener()
    async def on_automod_action_execution(self, execution: discord.AutoModAction) -> None:
        guild = execution.guild
        if guild.id != config.guild_id:
            return
        rule_name = f"Rule ID {execution.rule_id}"
        try:
            rule = await guild.fetch_automod_rule(execution.rule_id)
            rule_name = rule.name
        except discord.HTTPException:
            pass
        member = execution.member
        fields = [
            ("Member", member.mention if member else f"<@{execution.member_id}>", True),
            ("Rule", rule_name, True),
            ("Trigger", execution.rule_trigger_type.name.replace("_", " ").title(), True),
            ("Action", execution.action.type.name.replace("_", " ").title(), True),
        ]
        if execution.channel_id:
            fields.append(("Channel", f"<#{execution.channel_id}>", True))
        if execution.matched_keyword:
            fields.append(("Matched keyword", execution.matched_keyword, True))
        if execution.content:
            fields.append(("Message content", execution.content, False))
        await send_log(self.bot, guild, "automod", log_embed("AutoMod triggered", LOG_ORANGE, user=member, fields=fields))

    @commands.Cog.listener()
    async def on_automod_rule_create(self, rule: discord.AutoModRule) -> None:
        if rule.guild.id != config.guild_id:
            return
        await send_log(
            self.bot,
            rule.guild,
            "automod",
            log_embed("AutoMod rule created", LOG_GREEN, fields=[("Rule", rule.name, True), ("Created by", f"<@{rule.creator_id}>", True)]),
        )

    @commands.Cog.listener()
    async def on_automod_rule_update(self, rule: discord.AutoModRule) -> None:
        if rule.guild.id != config.guild_id:
            return
        await send_log(
            self.bot,
            rule.guild,
            "automod",
            log_embed("AutoMod rule updated", LOG_ORANGE, fields=[("Rule", rule.name, True), ("Enabled", "Yes" if rule.enabled else "No", True)]),
        )

    @commands.Cog.listener()
    async def on_automod_rule_delete(self, rule: discord.AutoModRule) -> None:
        if rule.guild.id != config.guild_id:
            return
        await send_log(self.bot, rule.guild, "automod", log_embed("AutoMod rule deleted", LOG_RED, fields=[("Rule", rule.name, True)]))


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(ServerLogs(bot))
