"""Violet's look: palette, topics and embed helpers. Edit the constants to restyle the whole bot."""
from __future__ import annotations

from dataclasses import dataclass

import discord

from .utils import truncate

FOOTER_TEXT = "Violet · Auto Memory Doll Service"


class Palette:
    INDIGO = 0x7C8CF8  # primary: soft periwinkle-indigo
    SKY = 0xA9C7FF  # pastel blue
    LILAC = 0xB9A7F5
    ROSE = 0xE9A3B8
    MINT = 0x9ADBC8  # success
    GOLD = 0xEBD59A  # notices
    SLATE = 0x8E9AAF  # neutral


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    emoji: str
    color: int
    blurb: str


TOPICS: dict[str, Topic] = {
    "gaming": Topic("gaming", "Gaming", "🎮", Palette.INDIGO, "games, settings, performance and strategy"),
    "pc": Topic("pc", "PC Hardware", "🖥️", Palette.SKY, "parts, builds, drivers and troubleshooting"),
    "mobile": Topic("mobile", "Mobile Tech", "📱", Palette.LILAC, "phones, tablets, apps and accessories"),
    "entertainment": Topic("entertainment", "Entertainment", "🎬", Palette.ROSE, "films, series, anime and music"),
    "general": Topic("general", "Letters & Curiosities", "✒️", Palette.INDIGO, "anything you wish to understand"),
}


def topic_for(key: str | None) -> Topic:
    return TOPICS.get((key or "").strip().lower(), TOPICS["general"])


def certainty_bar(level: str | None) -> str:
    return {"high": "◆◆◆", "medium": "◆◆◇", "low": "◆◇◇"}.get((level or "").strip().lower(), "◆◆◇")


# Discord hard limits
TITLE_MAX, DESC_MAX, FIELD_NAME_MAX, FIELD_VALUE_MAX, EMBED_TOTAL_MAX = 256, 4096, 256, 1024, 6000


def new_embed(
    *,
    title: str | None = None,
    description: str | None = None,
    color: int = Palette.INDIGO,
    avatar_url: str | None = None,
    footer_extra: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(title, TITLE_MAX) if title else None,
        description=truncate(description, DESC_MAX) if description else None,
        color=color,
    )
    embed.set_author(name="Violet", icon_url=avatar_url) if avatar_url else embed.set_author(name="Violet")
    footer = FOOTER_TEXT if not footer_extra else f"{FOOTER_TEXT} • {footer_extra}"
    embed.set_footer(text=truncate(footer, 2048))
    return embed


def add_field(embed: discord.Embed, name: str, value: str, *, inline: bool = False) -> None:
    """Add a field only if it has content, clamped to Discord's limits."""
    value = (value or "").strip()
    if not value:
        return
    embed.add_field(name=truncate(name, FIELD_NAME_MAX), value=truncate(value, FIELD_VALUE_MAX), inline=inline)


def fit_embed(embed: discord.Embed, budget: int = 5800) -> discord.Embed:
    """Guarantee the whole embed stays under Discord's 6000-character cap (drop/trim from the end)."""
    while len(embed) > budget:
        if embed.fields:
            embed.remove_field(len(embed.fields) - 1)
        elif embed.description and len(embed.description) > 400:
            embed.description = truncate(embed.description, int(len(embed.description) * 0.8))
        else:
            break
    return embed
