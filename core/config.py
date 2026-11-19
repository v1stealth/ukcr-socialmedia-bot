import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    value = env_str(name)
    return int(value) if value.isdigit() else default


def env_bool(name: str, default: bool = True) -> bool:
    value = env_str(name).lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int
    video_channel_id: Optional[int]
    staff_role_id: Optional[int]
    member_role_id: Optional[int]
    ticket_category_id: Optional[int]
    log_category_id: Optional[int]
    bulletin_category_id: Optional[int]
    commands_channel_id: Optional[int]
    commands_staff_bypass: bool
    owner_id: Optional[int]
    message_content_intent: bool
    max_pending_per_user: int
    pending_reminder_hours: int
    digest_hour: int
    content_tz: str
    bot_author: str
    log_level: str
    db_path: Path
    log_dir: Path


def load_config() -> Config:
    return Config(
        token=env_str("DISCORD_TOKEN"),
        guild_id=env_int("GUILD_ID", 1553441408495517727),
        video_channel_id=env_int("VIDEO_CHANNEL_ID", 1565354583666991104),
        staff_role_id=env_int("STAFF_ROLE_ID", 1565823968399728830),
        member_role_id=env_int("MEMBER_ROLE_ID", 1565726057049493625),
        ticket_category_id=env_int("TICKET_CATEGORY_ID", 1565734366670491768),
        log_category_id=env_int("LOG_CATEGORY_ID", 1565780078967787682),
        bulletin_category_id=env_int("BULLETIN_CATEGORY_ID", 1565727139305627778),
        commands_channel_id=env_int("COMMANDS_CHANNEL_ID", 1565815607008559134),
        commands_staff_bypass=env_bool("COMMANDS_STAFF_BYPASS", True),
        owner_id=env_int("OWNER_ID", 1553441523679494174),
        message_content_intent=env_bool("MESSAGE_CONTENT_INTENT", True),
        max_pending_per_user=env_int("MAX_PENDING_PER_USER", 3),
        pending_reminder_hours=env_int("PENDING_REMINDER_HOURS", 24),
        digest_hour=min(env_int("DIGEST_HOUR", 9), 23),
        content_tz=env_str("CONTENT_TZ") or "Europe/London",
        bot_author=env_str("BOT_AUTHOR") or "UKCR Development Team",
        log_level=(env_str("LOG_LEVEL") or "INFO").upper(),
        db_path=Path(env_str("DB_PATH") or BASE_DIR / "data" / "ukcr_bot.sqlite3"),
        log_dir=Path(env_str("LOG_DIR") or BASE_DIR / "logs"),
    )


config = load_config()

BRAND_COLOUR = discord.Colour.blurple()
NEW_ACCOUNT_DAYS = 7
