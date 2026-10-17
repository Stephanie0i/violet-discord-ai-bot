"""/ask — the main way to talk to Violet."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..responder import Request
from ..views import AnswerView

NO_MENTIONS = discord.AllowedMentions.none()

TOPIC_CHOICES = [
    app_commands.Choice(name="🎮 Gaming", value="gaming"),
    app_commands.Choice(name="🖥️ PC Hardware", value="pc"),
    app_commands.Choice(name="📱 Mobile Tech", value="mobile"),
    app_commands.Choice(name="🎬 Entertainment", value="entertainment"),
]


class AskCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="ask", description="Ask Violet about gaming, PC hardware, mobile tech or entertainment")
    @app_commands.describe(question="What would you like to understand?", topic="Optional: guide Violet's focus")
    @app_commands.choices(topic=TOPIC_CHOICES)
    async def ask(
        self,
        interaction: discord.Interaction,
        question: app_commands.Range[str, 3, 600],
        topic: app_commands.Choice[str] | None = None,
    ) -> None:
        responder = self.bot.responder
        verdict = responder.begin(interaction.user.id)
        if not verdict.ok:
            await interaction.response.send_message(embed=responder.throttle_embed(verdict), ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        request = Request(
            kind="answer",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            prompt=f"{interaction.user.display_name} asks: {question}",
            topic=topic.value if topic else "general",
            remember=True,
        )
        embed, ok = await responder.generate(request)
        view = AnswerView(self.bot, request, owner_name=interaction.user.display_name) if ok else discord.utils.MISSING
        message = await interaction.followup.send(embed=embed, view=view, wait=True, allowed_mentions=NO_MENTIONS)
        if ok:
            view.message = message


async def setup(bot) -> None:
    await bot.add_cog(AskCog(bot))
