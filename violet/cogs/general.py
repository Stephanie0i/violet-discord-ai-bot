"""/help, /about, /status, /ping, /forget and the rotating status."""
from __future__ import annotations

import itertools
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..persona import STATUSES
from ..theme import Palette, TOPICS, add_field, new_embed
from ..utils import format_duration, read_rss_mb

log = logging.getLogger("violet.general")


def _help_embed(page: str, avatar_url: str | None) -> discord.Embed:
    if page == "commands":
        embed = new_embed(title="📜 Commands", color=Palette.INDIGO, avatar_url=avatar_url)
        add_field(embed, "💬 Ask", "`/ask` — any question about gaming, PC, mobile or entertainment\nOr simply **mention me**: `@Violet why does my game stutter?`")
        add_field(embed, "🩺 Solve", "`/troubleshoot` — describe a problem, receive a careful plan")
        add_field(embed, "⚖️ Decide", "`/compare` — two options side by side\n`/recommend` — thoughtful suggestions")
        add_field(embed, "✒️ Create", "`/letter` — I ghostwrite a heartfelt letter for you")
        add_field(embed, "🕯️ Housekeeping", "`/forget` — erase what I remember of our chat\n`/status` · `/ping` · `/about`")
        return embed
    if page == "tips":
        embed = new_embed(title="🪻 Getting the best of me", color=Palette.LILAC, avatar_url=avatar_url)
        embed.description = (
            f"{'◈'} Be specific: name the **exact model**, game, OS version and what you already tried.\n"
            f"{'◈'} Paste the **error message** instead of describing it.\n"
            f"{'◈'} Use 🪶 **Simpler** under a reply if it was too technical.\n"
            f"{'◈'} Specs, prices and release dates change — I will tell you when something deserves verification.\n"
            f"{'◈'} I speak the language you write in."
        )
        return embed
    if page == "limits":
        embed = new_embed(title="🕯️ Good manners & limits", color=Palette.GOLD, avatar_url=avatar_url)
        embed.description = (
            f"{'◈'} A short pause between questions keeps the ink flowing for everyone.\n"
            f"{'◈'} I cannot help with piracy, cheats, hacking, or harming others.\n"
            f"{'◈'} I remember our recent chat for a few minutes only, never permanently — `/forget` erases it at once.\n"
            f"{'◈'} I can be wrong. For purchases and risky repairs, confirm with official sources."
        )
        return embed

    embed = new_embed(
        title="✒️ Violet — Auto Memory Doll",
        description=(
            "I once wrote letters for others; now I translate the language of **games, hardware, phones and "
            "stories** into words that can be understood. Ask me anything, and I will try to find the right ones."
        ),
        color=Palette.INDIGO,
        avatar_url=avatar_url,
    )
    for t in TOPICS.values():
        if t.key != "general":
            add_field(embed, f"{t.emoji} {t.label}", t.blurb, inline=True)
    add_field(embed, "💌 Begin", "Use `/ask`, or mention me in any channel.")
    return embed


class HelpView(discord.ui.View):
    def __init__(self, avatar_url: str | None) -> None:
        super().__init__(timeout=300)
        self.avatar_url = avatar_url
        self.message: discord.Message | discord.WebhookMessage | None = None

    @discord.ui.select(
        placeholder="Browse the pages…",
        options=[
            discord.SelectOption(label="Overview", value="overview", emoji="✒️", description="Who I am and what I do"),
            discord.SelectOption(label="Commands", value="commands", emoji="📜", description="Everything I can do"),
            discord.SelectOption(label="Tips", value="tips", emoji="🪻", description="How to get the best answers"),
            discord.SelectOption(label="Good manners & limits", value="limits", emoji="🕯️", description="What to expect"),
        ],
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        await interaction.response.edit_message(embed=_help_embed(select.values[0], self.avatar_url), view=self)

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class GeneralCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._started = time.monotonic()
        self._statuses = itertools.cycle(STATUSES)
        self.rotate_status.change_interval(minutes=bot.settings.status_interval_minutes)

    async def cog_load(self) -> None:
        self.rotate_status.start()

    async def cog_unload(self) -> None:
        self.rotate_status.cancel()

    # ------------------------------------------------------------------ status rotation
    @tasks.loop(minutes=5)
    async def rotate_status(self) -> None:
        try:
            await self.bot.change_presence(activity=discord.CustomActivity(name=next(self._statuses)))
        except Exception:  # never let a cosmetic task raise
            log.warning("Could not update presence", exc_info=True)

    @rotate_status.before_loop
    async def _before_rotate(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commands
    @app_commands.command(name="help", description="Meet Violet and learn what she can do")
    async def help(self, interaction: discord.Interaction) -> None:
        av = self.bot.responder.avatar_url
        view = HelpView(av)
        await interaction.response.send_message(embed=_help_embed("overview", av), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @app_commands.command(name="about", description="About Violet")
    async def about(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_help_embed("overview", self.bot.responder.avatar_url), ephemeral=True)

    @app_commands.command(name="ping", description="See how quickly Violet's letters travel")
    async def ping(self, interaction: discord.Interaction) -> None:
        ms = round(self.bot.latency * 1000)
        embed = new_embed(
            title="📮 Delivered",
            description=f"Gateway latency: `{ms} ms`",
            color=Palette.MINT if ms < 250 else Palette.GOLD,
            avatar_url=self.bot.responder.avatar_url,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status", description="Violet's current condition")
    async def status(self, interaction: discord.Interaction) -> None:
        bot = self.bot
        rss = read_rss_mb()
        llm = bot.llm
        if not llm.configured:
            ink = "⚪ not configured"
        elif llm.breaker_open:
            ink = "🟠 resting after errors"
        else:
            ink = "🟢 ready"
        embed = new_embed(title="🩺 Condition report", color=Palette.SKY, avatar_url=bot.responder.avatar_url)
        add_field(embed, "💓 Gateway", f"`{round(bot.latency * 1000)} ms`", inline=True)
        add_field(embed, "⏱️ Uptime", f"`{format_duration(time.monotonic() - self._started)}`", inline=True)
        add_field(embed, "🧠 Memory", f"`{rss:.0f} MB`" if rss is not None else "`n/a`", inline=True)
        add_field(embed, "🖋️ Language service", ink, inline=True)
        add_field(embed, "📨 Requests today", f"`{bot.guard.daily_used}/{bot.guard.daily_limit}`", inline=True)
        add_field(embed, "🏰 Servers", f"`{len(bot.guilds)}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="forget", description="Erase what Violet remembers of your recent conversation here")
    async def forget(self, interaction: discord.Interaction) -> None:
        cleared = self.bot.memory.clear((interaction.channel_id or 0, interaction.user.id))
        text = "🕯️ It is done. I no longer remember our recent words." if cleared else "🕯️ There was nothing to forget — our page is already blank."
        await interaction.response.send_message(
            embed=new_embed(description=text, color=Palette.LILAC, avatar_url=self.bot.responder.avatar_url), ephemeral=True
        )


async def setup(bot) -> None:
    await bot.add_cog(GeneralCog(bot))
