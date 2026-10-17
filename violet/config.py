"""Settings, read once from environment variables (or a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # harmless if there is no .env; real environment variables always win


def _int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return max(minimum, int(raw)) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return max(minimum, float(raw)) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _ids(name: str) -> frozenset[int]:
    out: set[int] = set()
    for part in os.getenv(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return frozenset(out)


@dataclass(frozen=True)
class Settings:
    # --- Discord ---
    discord_token: str
    guild_id: int | None
    sync_mode: str  # "auto" (only when commands changed) | "always" | "never"
    enable_message_content: bool
    allowed_channel_ids: frozenset[int]  # empty = Violet may answer mentions everywhere
    allow_dms: bool

    # --- LLM (any OpenAI-compatible /chat/completions API) ---
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout: float
    llm_max_tokens: int
    llm_temperature: float
    llm_max_concurrent: int

    # --- Fair use / protection of your free API quota ---
    cooldown_seconds: float
    hourly_limit: int
    daily_limit: int

    # --- Memory & presence ---
    memory_turns: int
    memory_ttl_seconds: int
    status_interval_minutes: int
    log_level: str

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)

    @property
    def chat_url(self) -> str:
        return f"{self.llm_base_url.rstrip('/')}/chat/completions"


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip().strip("\"'")
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Set it in your .env file or the host's variables panel.")
    if token.count(".") != 2:
        raise SystemExit(
            "DISCORD_TOKEN does not look like a bot token (it should have 3 parts separated by dots). "
            "Use the token from Developer Portal > Bot > Reset Token, not the Client ID or Client Secret."
        )

    guild_raw = os.getenv("GUILD_ID", "").strip()
    sync_mode = os.getenv("SYNC_COMMANDS", "auto").strip().lower()
    if sync_mode not in {"auto", "always", "never"}:
        sync_mode = "auto"

    return Settings(
        discord_token=token,
        guild_id=int(guild_raw) if guild_raw.isdigit() else None,
        sync_mode=sync_mode,
        enable_message_content=_bool("ENABLE_MESSAGE_CONTENT", False),
        allowed_channel_ids=_ids("ALLOWED_CHANNEL_IDS"),
        allow_dms=_bool("ALLOW_DMS", True),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_timeout=_float("LLM_TIMEOUT_SECONDS", 45.0, minimum=5.0),
        llm_max_tokens=_int("LLM_MAX_TOKENS", 900, minimum=100),
        llm_temperature=_float("LLM_TEMPERATURE", 0.6),
        llm_max_concurrent=_int("LLM_MAX_CONCURRENT", 2, minimum=1),
        cooldown_seconds=_float("USER_COOLDOWN_SECONDS", 8.0),
        hourly_limit=_int("USER_HOURLY_LIMIT", 30, minimum=1),
        daily_limit=_int("DAILY_REQUEST_LIMIT", 600, minimum=1),
        memory_turns=_int("MEMORY_TURNS", 6, minimum=0),
        memory_ttl_seconds=_int("MEMORY_TTL_SECONDS", 900, minimum=60),
        status_interval_minutes=_int("STATUS_INTERVAL_MINUTES", 5, minimum=1),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
