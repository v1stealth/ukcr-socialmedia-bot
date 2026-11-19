import asyncio
import platform
import sys

import discord

from core.config import config
from core.console import banner, get_logger, setup_logging

setup_logging(config.log_level, config.log_dir)
log = get_logger("main")


def preflight() -> bool:
    problems = []
    if not config.token:
        problems.append("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in.")
    if sys.version_info < (3, 9):
        problems.append("Python 3.9 or newer is required.")
    for message in problems:
        log.critical(message)
    return not problems


async def run() -> None:
    from core.bot import UKCRBot

    bot = UKCRBot()
    async with bot:
        await bot.start(config.token)


def main() -> None:
    print(
        banner(
            [
                "UKCR Discord Bot",
                f"Python {platform.python_version()}  ·  discord.py {discord.__version__}",
                f"Guild {config.guild_id}",
            ]
        )
    )
    if not preflight():
        raise SystemExit(1)
    try:
        asyncio.run(run())
    except discord.LoginFailure:
        log.critical("Discord rejected the token. Reset it in the Developer Portal and update .env")
        raise SystemExit(1)
    except discord.PrivilegedIntentsRequired:
        log.critical("Enable Server Members Intent and Message Content Intent in the Developer Portal, or set MESSAGE_CONTENT_INTENT=0")
        raise SystemExit(1)
    except KeyboardInterrupt:
        log.info("Stopped by keyboard interrupt")


if __name__ == "__main__":
    main()
