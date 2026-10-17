from __future__ import annotations

import os

os.environ.setdefault("DISCORD_TOKEN", "A" * 24 + "." + "B" * 6 + "." + "C" * 27)

from violet.config import Settings


def make_settings(**overrides) -> Settings:
    base = dict(
        discord_token="A" * 24 + "." + "B" * 6 + "." + "C" * 27,
        guild_id=None,
        sync_mode="never",
        enable_message_content=False,
        allowed_channel_ids=frozenset(),
        allow_dms=True,
        llm_api_key="test-key",
        llm_base_url="http://127.0.0.1:0/v1",
        llm_model="test-model",
        llm_timeout=5.0,
        llm_max_tokens=500,
        llm_temperature=0.5,
        llm_max_concurrent=2,
        cooldown_seconds=0.0,
        hourly_limit=100,
        daily_limit=1000,
        memory_turns=4,
        memory_ttl_seconds=600,
        status_interval_minutes=5,
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(**base)
