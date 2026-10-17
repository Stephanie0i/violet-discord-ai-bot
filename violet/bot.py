"""The bot itself: lean intents, one shared HTTP session, safe command syncing, friendly global errors."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from .config import Settings
from .limits import UsageGuard
from .llm import LLMClient
from .memory import ConversationMemory
from .persona import error_line
from .responder import Responder
from .theme import Palette, new_embed

log = logging.getLogger("violet.bot")

EXTENSIONS = ("violet.cogs.ask", "violet.cogs.tools", "violet.cogs.general", "violet.cogs.mentions")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))


class VioletBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        # Mentions and DMs carry their text even without this privileged intent, so it is OFF by default.
        # Turn it on only if you want Violet to also see replies sent without pinging her.
        intents.message_content = settings.enable_message_content
        intents.typing = False

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),  # model output can never ping anyone
            max_messages=None,  # we never read the message cache: saves RAM
            chunk_guilds_at_startup=False,
            member_cache_flags=discord.MemberCacheFlags.none(),
            activity=discord.CustomActivity(name="✒️ Preparing the ink…"),
        )
        self.settings = settings
        self.session: aiohttp.ClientSession | None = None
        self.llm: LLMClient
        self.guard: UsageGuard
        self.memory: ConversationMemory
        self.responder: Responder
        self.tree.on_error = self.on_app_command_error

    # ------------------------------------------------------------------ lifecycle
    async def setup_hook(self) -> None:
        s = self.settings
        self.session = aiohttp.ClientSession(headers={"User-Agent": "Violet-DiscordBot/1.0"})
        self.llm = LLMClient(s, self.session)
        self.guard = UsageGuard(s.cooldown_seconds, s.hourly_limit, s.daily_limit)
        self.memory = ConversationMemory(s.memory_turns, s.memory_ttl_seconds)
        self.responder = Responder(s, self.llm, self.guard, self.memory)

        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
            except Exception:  # one broken cog must not stop the others from loading
                log.exception("Could not load %s", ext)

        if not s.llm_configured:
            log.warning("LLM_API_KEY / LLM_BASE_URL / LLM_MODEL are not all set: AI replies are disabled until they are.")
        await self._sync_commands()

    async def close(self) -> None:
        await super().close()
        if self.session and not self.session.closed:
            await self.session.close()

    async def on_ready(self) -> None:
        if self.user:
            self.responder.avatar_url = self.user.display_avatar.url
        log.info("✒️ Violet is online as %s in %d server(s)", self.user, len(self.guilds))

    # ------------------------------------------------------------------ command sync
    async def _sync_commands(self) -> None:
        """Sync slash commands only when they changed: restart loops can never hit Discord's sync rate limits."""
        mode = self.settings.sync_mode
        if mode == "never":
            return

        guild = discord.Object(id=self.settings.guild_id) if self.settings.guild_id else None
        if guild:
            self.tree.copy_global_to(guild=guild)  # guild commands appear instantly
        commands_now = self.tree.get_commands(guild=guild)
        digest = hashlib.sha256(
            json.dumps([c.to_dict(self.tree) for c in commands_now], sort_keys=True).encode()
        ).hexdigest()

        marker = DATA_DIR / "commands.sha256"
        previous = marker.read_text().strip() if marker.exists() else ""
        if mode == "auto" and digest == previous:
            log.info("Slash commands unchanged — skipping sync.")
            return

        try:
            synced = await self.tree.sync(guild=guild)
        except discord.HTTPException:
            log.exception("Command sync failed (will retry on next start)")
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(digest)
        log.info("Synced %d slash command(s) %s", len(synced), f"to guild {guild.id}" if guild else "globally")

    # ------------------------------------------------------------------ errors
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            key, kwargs = "cooldown", {"secs": max(1, round(error.retry_after))}
        elif isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            key, kwargs = "not_yours", {"name": "someone else"}
        else:
            log.error("Unhandled app command error in /%s", getattr(interaction.command, "name", "?"), exc_info=error)
            key, kwargs = "unexpected", {}
        embed = new_embed(
            description=f"🕯️ {error_line(key, **kwargs)}", color=Palette.ROSE, avatar_url=self.responder.avatar_url
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass  # the interaction expired; nothing more can be done
