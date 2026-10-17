"""Violet — entry point. Run with:  python main.py

Beyond starting the bot, this file makes startup resilient: if the host has no network yet or Discord has a
brief outage, Violet keeps retrying with a growing delay instead of crashing, and she shuts down cleanly on
SIGTERM/SIGINT (what hosting panels send when you press Stop).
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):  # checked before anything else is imported
    raise SystemExit(f"Violet needs Python 3.10 or newer (this is {sys.version.split()[0]}). Pick a newer Python in your host's Startup settings.")

import asyncio
import logging
import signal

import aiohttp
import discord

from violet.bot import VioletBot
from violet.config import Settings, load_settings

log = logging.getLogger("violet.main")


async def run(settings: Settings) -> int:
    stop = asyncio.Event()
    current: dict[str, VioletBot | None] = {"bot": None}

    def request_stop() -> None:
        log.info("Shutdown requested — closing gracefully…")
        stop.set()
        bot = current["bot"]
        if bot is not None:
            asyncio.get_running_loop().create_task(bot.close())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):  # e.g. Windows
            pass

    delay = 5.0
    while not stop.is_set():
        bot = VioletBot(settings)
        current["bot"] = bot
        try:
            async with bot:
                await bot.start(settings.discord_token)
            return 0  # closed normally
        except discord.LoginFailure:
            log.critical("Discord rejected the token. Reset it in the Developer Portal and update DISCORD_TOKEN.")
            return 1
        except discord.PrivilegedIntentsRequired:
            log.critical(
                "A privileged intent is enabled in code but not in the Developer Portal. "
                "Either enable MESSAGE CONTENT INTENT there, or set ENABLE_MESSAGE_CONTENT=false."
            )
            return 1
        except (aiohttp.ClientError, discord.HTTPException, discord.GatewayNotFound, OSError, asyncio.TimeoutError) as exc:
            if stop.is_set():
                break
            log.error("Could not reach Discord (%s: %s). Retrying in %.0fs…", type(exc).__name__, exc, delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 120.0)
        finally:
            current["bot"] = None
    return 0


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)  # keep the console readable
    # Violet has no voice features, so silence the "PyNaCl / davey not installed" notices
    for flag in ("warn_nacl", "warn_dave"):
        if hasattr(discord.VoiceClient, flag):
            setattr(discord.VoiceClient, flag, False)
    try:
        sys.exit(asyncio.run(run(settings)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
