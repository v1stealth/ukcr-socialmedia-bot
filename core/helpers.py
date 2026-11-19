import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlsplit

import discord

from core.config import config
from core.console import get_logger

log = get_logger("helpers")

URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
WHEN_FORMATS = ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%y %H:%M")


def load_timezone(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        log.warning("Couldn't load timezone %r (on Windows run: pip install tzdata). Falling back to UTC.", name)
        return timezone.utc


CONTENT_TZ = load_timezone(config.content_tz)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_staff(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    return any(role.id == config.staff_role_id for role in member.roles)


def is_owner(user: discord.abc.User) -> bool:
    if config.owner_id is None:
        return False
    if user.id == config.owner_id:
        return True
    return isinstance(user, discord.Member) and any(role.id == config.owner_id for role in user.roles)


def discord_ts(iso: str, style: str = "F") -> str:
    return f"<t:{int(datetime.fromisoformat(iso).timestamp())}:{style}>"


def clip(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def normalise_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = ""
    if host.endswith("youtube.com"):
        video_id = parse_qs(parts.query).get("v")
        if video_id:
            query = f"?v={video_id[0]}"
    return f"{host}{parts.path.rstrip('/')}{query}"


def fmt_duration(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def message_link(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def parse_when(text: str) -> Optional[datetime]:
    text = " ".join(text.split())
    for fmt in WHEN_FORMATS:
        try:
            local = datetime.strptime(text, fmt).replace(tzinfo=CONTENT_TZ)
        except ValueError:
            continue
        return local.astimezone(timezone.utc)
    return None


def normalise_tags(text: str) -> str:
    seen, tags = set(), []
    for token in re.split(r"[\s,]+", text.strip()):
        token = re.sub(r"[^\w]", "", token)
        if not token:
            continue
        tag = f"#{token}"
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return " ".join(tags)


def staff_check(interaction: discord.Interaction) -> bool:
    return is_staff(interaction.user)


def brand_embed(title: str, description: Optional[str] = None, colour: Optional[discord.Colour] = None) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        colour=colour or discord.Colour.blurple(),
        timestamp=datetime.now(timezone.utc),
    )


async def reply(interaction: discord.Interaction, *args, **kwargs) -> None:
    kwargs.setdefault("ephemeral", True)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(*args, **kwargs)
        else:
            await interaction.response.send_message(*args, **kwargs)
    except discord.HTTPException:
        log.exception("Failed to reply to interaction")


async def dm_user(bot: discord.Client, user_id: int, embed: discord.Embed) -> bool:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.NotFound):
        return False
    except discord.HTTPException:
        log.exception("Failed to DM user %s", user_id)
        return False
