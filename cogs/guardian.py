import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.bot import UKCRBot
from core.config import config
from core.console import get_logger, log_throttled
from core.helpers import reply

log = get_logger("guardian")

BULLETIN_DENY = dict(
    send_messages=False,
    send_messages_in_threads=False,
    create_public_threads=False,
    create_private_threads=False,
)

CHANNEL_DANGEROUS = [
    "manage_channels", "manage_roles", "manage_webhooks", "manage_messages",
    "manage_threads", "manage_events", "mention_everyone", "send_tts_messages",
    "mute_members", "deafen_members", "move_members",
]
MEMBER_ALLOWED = ["create_instant_invite"]
ROLE_DANGEROUS = CHANNEL_DANGEROUS + [
    "administrator", "manage_guild", "manage_nicknames", "manage_expressions",
    "kick_members", "ban_members", "moderate_members", "view_audit_log", "view_guild_insights",
]


class Guardian(commands.Cog):
    def __init__(self, bot: UKCRBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.citizen_role_loop.start()
        self.bulletin_lock_loop.start()
        self.permission_guard_loop.start()

    async def cog_unload(self) -> None:
        self.citizen_role_loop.cancel()
        self.bulletin_lock_loop.cancel()
        self.permission_guard_loop.cancel()

    async def ensure_citizen_role(self, guild: discord.Guild) -> int:
        role = guild.get_role(config.member_role_id) if config.member_role_id else None
        if role is None:
            log_throttled(log, "citizen-missing", logging.WARNING, "Citizen role %s not found", config.member_role_id)
            return 0
        if not role.is_assignable():
            log_throttled(
                log, "citizen-unassignable", logging.ERROR,
                "Can't assign %s: give the bot Manage Roles and move its role above UKCR Citizen", role.name,
            )
            return 0

        fixed = 0
        for member in list(guild.members):
            if member.bot or role in member.roles:
                continue
            try:
                await member.add_roles(role, reason="UKCR citizen role sweep")
                fixed += 1
                await asyncio.sleep(0.5)
            except discord.Forbidden:
                log_throttled(log, "citizen-forbidden", logging.ERROR, "Missing permission to give the Citizen role to %s", member)
                break
            except discord.HTTPException:
                log_throttled(log, "citizen-http", logging.ERROR, "Failed to give the Citizen role to %s", member)
        if fixed:
            log.info("Citizen role sweep: gave the role to %d member(s)", fixed)
        return fixed

    @tasks.loop(seconds=60)
    async def citizen_role_loop(self) -> None:
        try:
            guild = self.bot.get_guild(config.guild_id)
            if guild is not None:
                await self.ensure_citizen_role(guild)
        except Exception:
            log_throttled(log, "citizen-loop", logging.ERROR, "Citizen role sweep failed")

    @citizen_role_loop.before_loop
    async def before_citizen_role_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def lock_bulletin(self, guild: discord.Guild) -> int:
        category = guild.get_channel(config.bulletin_category_id) if config.bulletin_category_id else None
        if not isinstance(category, discord.CategoryChannel):
            log_throttled(log, "bulletin-missing", logging.WARNING, "Bulletin category %s not found", config.bulletin_category_id)
            return 0

        updated = 0
        for target in [category, *category.channels]:
            async with self.bot.channel_lock:
                overwrites = target.overwrites
                changed = False

                subjects = {guild.default_role}
                citizen = guild.get_role(config.member_role_id) if config.member_role_id else None
                if citizen is not None:
                    subjects.add(citizen)
                subjects.update(k for k in overwrites if k.id != guild.me.id)

                for subject in subjects:
                    ow = overwrites.get(subject, discord.PermissionOverwrite())
                    if any(getattr(ow, name) is not False for name in BULLETIN_DENY):
                        ow.update(**BULLETIN_DENY)
                        overwrites[subject] = ow
                        changed = True

                me_ow = overwrites.get(guild.me, discord.PermissionOverwrite())
                if me_ow.send_messages is not True:
                    me_ow.update(view_channel=True, send_messages=True, embed_links=True)
                    overwrites[guild.me] = me_ow
                    changed = True

                if changed:
                    try:
                        await target.edit(overwrites=overwrites, reason="UKCR Bulletin lock")
                        updated += 1
                        await asyncio.sleep(1)
                    except discord.Forbidden:
                        log_throttled(log, "bulletin-forbidden", logging.ERROR, "Missing Manage Channels / Manage Roles to lock %s", target.name)
                        return updated
                    except discord.HTTPException:
                        log_throttled(log, f"bulletin-http-{target.id}", logging.ERROR, "Failed to lock %s", target.name)
        return updated

    @tasks.loop(seconds=60)
    async def bulletin_lock_loop(self) -> None:
        try:
            guild = self.bot.get_guild(config.guild_id)
            if guild is not None:
                updated = await self.lock_bulletin(guild)
                if updated:
                    log.info("Bulletin lock: updated %d channel(s)", updated)
        except Exception:
            log_throttled(log, "bulletin-loop", logging.ERROR, "Bulletin lock failed")

    @bulletin_lock_loop.before_loop
    async def before_bulletin_lock_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def lock_dangerous_permissions(self, guild: discord.Guild) -> int:
        citizen = guild.get_role(config.member_role_id) if config.member_role_id else None
        staff = guild.get_role(config.staff_role_id) if config.staff_role_id else None
        me = guild.me
        members_roles = [r for r in (guild.default_role, citizen) if r is not None]

        for role in members_roles:
            perms = role.permissions
            bad = [n for n in ROLE_DANGEROUS if getattr(perms, n)]
            if not bad:
                continue
            perms.update(**{n: False for n in bad})
            try:
                await role.edit(permissions=perms, reason="UKCR dangerous permission lock")
                log.warning("Removed dangerous permissions from %s: %s", role.name, ", ".join(bad))
            except discord.Forbidden:
                log_throttled(log, f"role-forbidden-{role.id}", logging.ERROR, "Can't edit role %s: give the bot Manage Roles and move its role above it", role.name)
            except discord.HTTPException:
                log_throttled(log, f"role-http-{role.id}", logging.ERROR, "Failed to edit role %s", role.name)

        if citizen is not None:
            perms = citizen.permissions
            lacking = [n for n in MEMBER_ALLOWED if not getattr(perms, n)]
            if lacking:
                perms.update(**{n: True for n in lacking})
                try:
                    await citizen.edit(permissions=perms, reason="UKCR members can create invites")
                    log.info("Gave %s: %s", citizen.name, ", ".join(lacking))
                except discord.Forbidden:
                    log_throttled(log, f"role-forbidden-{citizen.id}", logging.ERROR, "Can't edit role %s: give the bot Manage Roles and move its role above it", citizen.name)
                except discord.HTTPException:
                    log_throttled(log, f"role-http-{citizen.id}", logging.ERROR, "Failed to edit role %s", citizen.name)

        keep = []
        if staff is not None and not staff.permissions.administrator:
            keep.append((staff, [n for n in CHANNEL_DANGEROUS if getattr(staff.permissions, n)]))
        if not me.guild_permissions.administrator:
            keep.append((me, [n for n in CHANNEL_DANGEROUS if getattr(me.guild_permissions, n)]))

        updated = 0
        for target in list(guild.channels):
            async with self.bot.channel_lock:
                overwrites = target.overwrites
                changed = False

                for subject in members_roles:
                    ow = overwrites.get(subject, discord.PermissionOverwrite())
                    if any(getattr(ow, n) is not False for n in CHANNEL_DANGEROUS):
                        ow.update(**{n: False for n in CHANNEL_DANGEROUS})
                        overwrites[subject] = ow
                        changed = True

                for subject in members_roles:
                    ow = overwrites.get(subject)
                    if ow is not None and any(getattr(ow, n) is False for n in MEMBER_ALLOWED):
                        ow.update(**{n: None for n in MEMBER_ALLOWED})
                        overwrites[subject] = ow
                        changed = True

                for subject, perms in keep:
                    ow = overwrites.get(subject, discord.PermissionOverwrite())
                    need = [n for n in perms if getattr(ow, n) is None]
                    if need:
                        ow.update(**{n: True for n in need})
                        overwrites[subject] = ow
                        changed = True

                if changed:
                    try:
                        await target.edit(overwrites=overwrites, reason="UKCR dangerous permission lock")
                        updated += 1
                        await asyncio.sleep(1)
                    except discord.Forbidden:
                        log_throttled(log, "guard-forbidden", logging.ERROR, "Missing Manage Channels / Manage Roles to edit %s", target.name)
                        return updated
                    except discord.HTTPException:
                        log_throttled(log, f"guard-http-{target.id}", logging.ERROR, "Failed to edit %s", target.name)
        return updated

    @tasks.loop(seconds=60)
    async def permission_guard_loop(self) -> None:
        try:
            guild = self.bot.get_guild(config.guild_id)
            if guild is not None:
                updated = await self.lock_dangerous_permissions(guild)
                if updated:
                    log.info("Permission lock: updated %d channel(s)", updated)
        except Exception:
            log_throttled(log, "guard-loop", logging.ERROR, "Dangerous permission lock failed")

    @permission_guard_loop.before_loop
    async def before_permission_guard_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="syncroles", description="Gives the UKCR Citizen role to every member who is missing it right now")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def syncroles(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        role = guild.get_role(config.member_role_id) if config.member_role_id else None
        if role is None:
            await reply(interaction, f"I can't find the UKCR Citizen role (`{config.member_role_id}`). Check `MEMBER_ROLE_ID`.")
            return
        if not role.is_assignable():
            await reply(interaction, f"I can't assign {role.mention}. Give my role **Manage Roles** and move it above {role.mention}.")
            return

        await interaction.response.defer(ephemeral=True)
        missing = [m for m in guild.members if not m.bot and role not in m.roles]
        fixed = await self.ensure_citizen_role(guild)
        remaining = len([m for m in guild.members if not m.bot and role not in m.roles])
        text = f"Checked **{len(guild.members)}** members. {role.mention} was missing on **{len(missing)}**; fixed **{fixed}**."
        if remaining:
            text += f"\n⚠️ **{remaining}** still don't have it - check my permissions and role position."
        else:
            text += "\nEveryone has the role now ✅"
        await interaction.followup.send(text, ephemeral=True)


async def setup(bot: UKCRBot) -> None:
    await bot.add_cog(Guardian(bot))
