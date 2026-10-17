"""Small, dependency-free helpers (all unit-tested)."""
from __future__ import annotations

import json
import re
from typing import Any

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ROLE_MENTION = re.compile(r"<@&\d+>")
_MENTION_PREFIX = re.compile(r"^(?:\s*<@!?\d+>\s*)+")


def truncate(text: str, limit: int) -> str:
    """Cut to `limit` characters (ellipsis included) without leaving trailing spaces."""
    text = text or ""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> blocks that reasoning models sometimes include."""
    if not text:
        return ""
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text.lower():  # opening tag was omitted by the model
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    return text.strip()


def neutralize_mentions(text: str) -> str:
    """Make sure model output can never ping @everyone / @here / roles."""
    text = text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    return _ROLE_MENTION.sub("(role)", text)


def clean_model_text(text: str) -> str:
    return neutralize_mentions(strip_reasoning(text))


def strip_bot_mention(content: str, bot_id: int | None = None) -> str:
    """Remove the leading @Violet mention(s) from a message."""
    content = _MENTION_PREFIX.sub("", content or "")
    if bot_id:
        content = re.sub(rf"<@!?{bot_id}>", "", content)
    return content.strip()


def extract_json(text: str) -> dict[str, Any] | None:
    """Find the first valid JSON object in model output (tolerates code fences and chatter)."""
    if not text:
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()

    start = candidate.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(candidate[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict):
                        return obj
                    break
        start = candidate.find("{", start + 1)
    return None


def as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return default


def as_str_list(value: Any, *, limit: int = 8, item_limit: int = 300) -> list[str]:
    """Accept a list of strings (or a single string) and return a clean, bounded list."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = as_str(item)
        if s:
            out.append(truncate(s, item_limit))
        if len(out) >= limit:
            break
    return out


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = [f"{days}d" if days else "", f"{hours}h" if hours else "", f"{minutes}m" if minutes else ""]
    if not any(parts):
        parts.append(f"{secs}s")
    return " ".join(p for p in parts if p)


def read_rss_mb() -> float | None:
    """Current memory use in MB (Linux only; returns None elsewhere)."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None
