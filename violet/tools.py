"""Specialised commands: /troubleshoot, /compare, /recommend, /letter."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..responder import Request
from ..views import AnswerView

NO_MENTIONS = discord.AllowedMentions.none()

CATEGORY_CHOICES = [
    app_commands.Choice(name="🎮 Games", value="video games"),
    app_commands.Choice(name="🎬 Films & series", value="films and TV series"),
    app_commands.Choice(name="🌸 Anime", value="anime"),
    app_commands.Choice(name="🖥️ PC parts & peripherals", value="PC parts and peripherals"),
    app_commands.Choice(name="📱 Phones & accessories", value="phones and mobile accessories"),
    app_commands.Choice(name="🎧 Music", value="music"),
]


class ToolsCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def _run(self, interaction: discord.Interaction, request: Request, *, with_buttons: bool = True) -> None:
        responder = self.bot.responder
        verdict = responder.begin(interaction.user.id)
        if not verdict.ok:
            await interaction.response.send_message(embed=responder.throttle_embed(verdict), ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        embed, ok = await responder.generate(request)
        view = (
            AnswerView(self.bot, request, owner_name=interaction.user.display_name)
            if ok and with_buttons
            else discord.utils.MISSING
        )
        message = await interaction.followup.send(embed=embed, view=view, wait=True, allowed_mentions=NO_MENTIONS)
        if view is not discord.utils.MISSING:
            view.message = message

    @app_commands.command(name="troubleshoot", description="Describe a gaming, PC or phone problem and receive a careful plan")
    @app_commands.describe(
        subject="The device or game (e.g. 'RTX 3060 laptop', 'Elden Ring', 'Galaxy S21')",
        problem="What is happening? Include error messages and what you already tried",
    )
    async def troubleshoot(
        self,
        interaction: discord.Interaction,
        subject: app_commands.Range[str, 2, 100],
        problem: app_commands.Range[str, 5, 800],
    ) -> None:
        request = Request(
            kind="troubleshoot",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            prompt=f"Subject: {subject}\nProblem: {problem}",
            topic="pc",
            subject=str(subject),
        )
        await self._run(interaction, request)

    @app_commands.command(name="compare", description="Compare two games, devices, parts or shows")
    @app_commands.describe(first="The first option", second="The second option", focus="Optional: what matters to you (budget, battery, story...)")
    async def compare(
        self,
        interaction: discord.Interaction,
        first: app_commands.Range[str, 2, 80],
        second: app_commands.Range[str, 2, 80],
        focus: app_commands.Range[str, 0, 200] = "",
    ) -> None:
        prompt = f"Compare: A = {first}  versus  B = {second}."
        if focus:
            prompt += f" What matters most to the person: {focus}"
        request = Request(
            kind="compare",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            prompt=prompt,
            first=str(first),
            second=str(second),
        )
        await self._run(interaction, request)

    @app_commands.command(name="recommend", description="Receive thoughtful recommendations for games, shows, anime, gear and more")
    @app_commands.describe(category="What kind of thing?", preferences="Tastes, platform, budget, what you enjoyed before")
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def recommend(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        preferences: app_commands.Range[str, 3, 500],
    ) -> None:
        request = Request(
            kind="recommend",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            prompt=f"Category: {category.value}\nThe person's preferences: {preferences}",
            category=category.name,
        )
        await self._run(interaction, request)

    @app_commands.command(name="letter", description="Violet ghostwrites a heartfelt letter for you or someone you care about")
    @app_commands.describe(
        recipient="Who is it for? (a friend, a teammate, a name)",
        occasion="What is it about? (thanks, apology, encouragement, an invitation to play...)",
        details="Optional: things to mention, in your own words",
    )
    async def letter(
        self,
        interaction: discord.Interaction,
        recipient: app_commands.Range[str, 1, 60],
        occasion: app_commands.Range[str, 3, 200],
        details: app_commands.Range[str, 0, 400] = "",
    ) -> None:
        prompt = f"Write a letter from {interaction.user.display_name} to {recipient}. Occasion: {occasion}."
        if details:
            prompt += f" Details to include: {details}"
        request = Request(
            kind="letter",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            prompt=prompt,
            recipient=str(recipient),
        )
        await self._run(interaction, request)


async def setup(bot) -> None:
    await bot.add_cog(ToolsCog(bot))
