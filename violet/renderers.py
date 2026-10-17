"""Turn the model's JSON (or, failing that, plain text) into polished Discord embeds.

Every renderer is defensive: the model may omit keys, return the wrong types, or ignore the format
entirely. Whatever comes back, the user receives a well-formed embed within Discord's size limits.
"""
from __future__ import annotations

from typing import Any

import discord

from .persona import error_line
from .theme import Palette, add_field, certainty_bar, fit_embed, new_embed, topic_for
from .utils import as_str, as_str_list, neutralize_mentions, truncate

BULLET = "◈"
CAVEAT_FOOTER = {"gaming", "pc", "mobile"}


def _clean(text: str) -> str:
    return neutralize_mentions(text)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"{BULLET} {_clean(i)}" for i in items)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"`{n}` {_clean(i)}" for n, i in enumerate(items, 1))


def _certainty(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    level = as_str(data.get("confidence")).lower()
    return f"Certainty {certainty_bar(level)}" if level else None


def _plain_fallback(raw: str, *, title: str, color: int, avatar_url: str | None) -> discord.Embed:
    return fit_embed(new_embed(title=title, description=_clean(raw), color=color, avatar_url=avatar_url))


# ---------------------------------------------------------------------------- /ask + mentions
def render_answer(data: dict[str, Any] | None, raw: str, *, topic_hint: str | None, avatar_url: str | None) -> discord.Embed:
    hinted = topic_hint if topic_hint and topic_hint != "general" else None
    topic = topic_for(hinted or as_str((data or {}).get("topic")))

    if not data or not as_str(data.get("answer")):
        return _plain_fallback(raw, title=f"{topic.emoji} A reply, written with care", color=topic.color, avatar_url=avatar_url)

    headline = as_str(data.get("headline")) or "A reply, written with care"
    description = _clean(as_str(data["answer"]))
    curious = as_str(data.get("curious"))
    if curious:
        description = f"{description}\n\n*{_clean(truncate(curious, 300))}*"

    embed = new_embed(
        title=f"{topic.emoji} {_clean(truncate(headline, 80))}",
        description=description,
        color=topic.color,
        avatar_url=avatar_url,
        footer_extra=_certainty(data),
    )
    add_field(embed, "✦ Key points", _bullets(as_str_list(data.get("key_points"), limit=4, item_limit=220)))
    add_field(embed, "🛠️ Steps", _numbered(as_str_list(data.get("steps"), limit=6, item_limit=220)))
    caveat = as_str(data.get("caveat"))
    if caveat:
        add_field(embed, "🔎 Worth verifying", _clean(truncate(caveat, 400)))
    elif topic.key in CAVEAT_FOOTER:
        pass  # no caveat needed when the model had nothing to flag
    return fit_embed(embed)


# ---------------------------------------------------------------------------- /troubleshoot
def render_troubleshoot(data: dict[str, Any] | None, raw: str, *, subject: str, avatar_url: str | None) -> discord.Embed:
    color = topic_for("pc").color
    if not data or not (as_str(data.get("diagnosis")) or data.get("steps")):
        return _plain_fallback(raw, title=f"🩺 Notes on {truncate(subject, 60)}", color=color, avatar_url=avatar_url)

    headline = as_str(data.get("headline")) or f"Notes on {truncate(subject, 50)}"
    embed = new_embed(
        title=f"🩺 {_clean(truncate(headline, 80))}",
        description=_clean(truncate(as_str(data.get("diagnosis")), 700)),
        color=color,
        avatar_url=avatar_url,
        footer_extra=_certainty(data),
    )

    causes = []
    raw_causes = data.get("likely_causes")
    for item in raw_causes if isinstance(raw_causes, list) else []:
        if isinstance(item, dict):
            cause, why = as_str(item.get("cause")), as_str(item.get("why"))
            if cause:
                causes.append(f"**{_clean(truncate(cause, 90))}**" + (f" — {_clean(truncate(why, 200))}" if why else ""))
        elif isinstance(item, str) and item.strip():
            causes.append(_clean(truncate(item.strip(), 280)))
        if len(causes) >= 4:
            break
    add_field(embed, "🔎 Likely causes", "\n".join(f"{BULLET} {c}" for c in causes))
    add_field(embed, "🛠️ Steps — gentlest first", _numbered(as_str_list(data.get("steps"), limit=7, item_limit=220)))
    risk = as_str(data.get("risk_note"))
    if risk:
        add_field(embed, "⚠️ Handle with care", _clean(truncate(risk, 400)))
    escalate = as_str(data.get("escalate_if"))
    if escalate:
        add_field(embed, "🚩 Seek further help if", _clean(truncate(escalate, 400)))
    return fit_embed(embed)


# ---------------------------------------------------------------------------- /compare
def _side(side: Any, fallback: str) -> tuple[str, str]:
    if not isinstance(side, dict):
        return fallback, ""
    name = as_str(side.get("name")) or fallback
    pros = as_str_list(side.get("strengths"), limit=3, item_limit=140)
    cons = as_str_list(side.get("weaknesses"), limit=3, item_limit=140)
    lines = [f"✅ {_clean(p)}" for p in pros] + [f"⚠️ {_clean(c)}" for c in cons]
    return name, "\n".join(lines)


def render_compare(
    data: dict[str, Any] | None, raw: str, *, first: str, second: str, avatar_url: str | None
) -> discord.Embed:
    color = topic_for("gaming").color
    if not data or not as_str(data.get("verdict")):
        return _plain_fallback(raw, title=f"⚖️ {truncate(first, 40)} vs {truncate(second, 40)}", color=color, avatar_url=avatar_url)

    headline = as_str(data.get("headline")) or f"{truncate(first, 40)} vs {truncate(second, 40)}"
    embed = new_embed(title=f"⚖️ {_clean(truncate(headline, 80))}", color=color, avatar_url=avatar_url, footer_extra=_certainty(data))
    for side, fb in ((data.get("first"), first), (data.get("second"), second)):
        name, body = _side(side, fb)
        add_field(embed, f"◈ {_clean(truncate(name, 80))}", body or "—", inline=True)
    add_field(embed, "📜 Verdict", _clean(truncate(as_str(data.get("verdict")), 700)))

    best = data.get("best_for")
    if isinstance(best, dict):
        lines = []
        for key, fb in (("first", first), ("second", second)):
            text = as_str(best.get(key))
            if text:
                lines.append(f"**{_clean(truncate(fb, 40))}** — {_clean(truncate(text, 200))}")
        add_field(embed, "🎯 Best for", "\n".join(lines))
    caveat = as_str(data.get("caveat"))
    if caveat:
        add_field(embed, "🔎 Worth verifying", _clean(truncate(caveat, 400)))
    return fit_embed(embed)


# ---------------------------------------------------------------------------- /recommend
def render_recommend(data: dict[str, Any] | None, raw: str, *, category: str, avatar_url: str | None) -> discord.Embed:
    color = topic_for("entertainment").color
    picks_raw = data.get("picks") if data else None
    if not isinstance(picks_raw, list) or not picks_raw:
        return _plain_fallback(raw, title=f"💌 Recommendations: {truncate(category, 60)}", color=color, avatar_url=avatar_url)

    headline = as_str(data.get("headline")) or f"Recommendations: {truncate(category, 50)}"
    embed = new_embed(
        title=f"💌 {_clean(truncate(headline, 80))}",
        description=_clean(truncate(as_str(data.get("advice")), 500)) or None,
        color=color,
        avatar_url=avatar_url,
        footer_extra=_certainty(data),
    )
    count = 0
    for pick in picks_raw:
        if not isinstance(pick, dict) or not as_str(pick.get("name")):
            continue
        count += 1
        why, note = as_str(pick.get("why")), as_str(pick.get("note"))
        body = _clean(truncate(why, 300)) + (f"\n*{_clean(truncate(note, 200))}*" if note else "")
        add_field(embed, f"`{count}` · {_clean(truncate(as_str(pick['name']), 80))}", body or "—")
        if count >= 5:
            break
    caveat = as_str(data.get("caveat"))
    if caveat:
        add_field(embed, "🔎 Worth verifying", _clean(truncate(caveat, 400)))
    return fit_embed(embed)


# ---------------------------------------------------------------------------- /letter
def render_letter(data: dict[str, Any] | None, raw: str, *, recipient: str, avatar_url: str | None) -> discord.Embed:
    letter = as_str((data or {}).get("letter")) or raw
    sign_off = as_str((data or {}).get("sign_off")) or "With care"
    body = ">>> " + _clean(truncate(letter, 1500)) + f"\n\n*— {_clean(truncate(sign_off, 80))}, Violet*"
    embed = new_embed(
        title=f"✒️ A letter, written for {_clean(truncate(recipient, 60))}",
        description=body,
        color=Palette.LILAC,
        avatar_url=avatar_url,
        footer_extra="Ghostwritten by an Auto Memory Doll",
    )
    return fit_embed(embed)


# ---------------------------------------------------------------------------- errors & notices
def error_embed(key: str, *, avatar_url: str | None = None, **fmt: object) -> discord.Embed:
    color = Palette.GOLD if key in {"cooldown", "hourly", "daily", "rate_limited", "not_yours"} else Palette.ROSE
    return new_embed(description=f"🕯️ {error_line(key, **fmt)}", color=color, avatar_url=avatar_url)


def throttle_embed(kind: str, retry_after: float, *, avatar_url: str | None = None) -> discord.Embed:
    key = kind if kind in {"cooldown", "hourly", "daily"} else "cooldown"
    secs = max(1, int(retry_after + 0.999))
    return error_embed(key, avatar_url=avatar_url, secs=secs, mins=max(1, (secs + 59) // 60))
