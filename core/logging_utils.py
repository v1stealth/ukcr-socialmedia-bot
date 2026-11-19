import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import discord

from core.console import get_logger
from core.helpers import clip

if TYPE_CHECKING:
    from core.bot import UKCRBot

log = get_logger("serverlog")


def log_embed(
    title: str,
    colour: discord.Colour,
    description: Optional[str] = None,
    *,
    user: Optional[discord.abc.User] = None,
    fields: Optional[list] = None,
    footer: Optional[str] = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, colour=colour, timestamp=datetime.now(timezone.utc))
    if user is not None:
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
    for name, value, inline in fields or []:
        embed.add_field(name=name, value=clip(str(value)) or "None", inline=inline)
    if footer:
        embed.set_footer(text=footer)
    return embed


async def send_log(
    bot: "UKCRBot",
    guild: discord.Guild,
    key: str,
    embed: discord.Embed,
    *,
    file: Optional[discord.File] = None,
    content: Optional[str] = None,
) -> bool:
    channel_id = await bot.db.log_channel_id(guild.id, key)
    if channel_id is None:
        return False
    channel = guild.get_channel(channel_id)
    if channel is None:
        return False
    kwargs = {
        "embed": embed,
        "allowed_mentions": discord.AllowedMentions(roles=True) if content else discord.AllowedMentions.none(),
    }
    if content:
        kwargs["content"] = content
    if file is not None:
        kwargs["file"] = file
    try:
        await channel.send(**kwargs)
        return True
    except discord.HTTPException:
        log.exception("Failed to send %s log", key)
        return False


async def recent_audit_entry(guild: discord.Guild, actions, target_id: Optional[int] = None, within: int = 15):
    if isinstance(actions, discord.AuditLogAction):
        actions = (actions,)
    try:
        async for entry in guild.audit_logs(limit=20):
            if entry.action not in actions:
                continue
            if target_id is not None and getattr(entry.target, "id", None) != target_id:
                continue
            if (datetime.now(timezone.utc) - entry.created_at).total_seconds() <= within:
                return entry
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


async def audit_fields(guild: discord.Guild, actions, target_id: Optional[int] = None) -> list:
    await asyncio.sleep(1)
    entry = await recent_audit_entry(guild, actions, target_id)
    if entry is None:
        return []
    fields = [("Done by", entry.user.mention if entry.user else "Unknown", True)]
    if entry.reason:
        fields.append(("Reason", entry.reason, False))
    return fields


def mod_fields(entry) -> list:
    moderator = entry.user.mention if entry is not None and entry.user else "Unknown"
    reason = (entry.reason if entry is not None else None) or "No reason provided"
    return [("Moderator", moderator, True), ("Reason", reason, False)]


def diff_fields(before, after, attrs: list) -> list:
    fields = []
    for label, attr in attrs:
        old, new = getattr(before, attr, None), getattr(after, attr, None)
        if old != new:
            old_text = "None" if old in (None, "") else str(old)
            new_text = "None" if new in (None, "") else str(new)
            fields.append((label, f"{old_text} → {new_text}", False))
    return fields
