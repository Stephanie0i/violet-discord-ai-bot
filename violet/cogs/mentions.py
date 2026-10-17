"""Answer when someone mentions Violet (or replies to her) — no slash command needed."""
from __future__ import annotations

import logging
import random

import discord
from discord.ext import commands

from ..persona import GREETINGS
from ..responder import Request
from ..theme import Palette, new_embed
from ..utils import strip_bot_mention
from ..views import AnswerView

log = logging.getLogger("violet.mentions")
NO_MENTIONS = discord.AllowedMentions.none()
MAX_QUESTION = 600


class MentionsCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    def _addressed_to_me(self, message: discord.Message) -> bool:
        me = self.bot.user
        if me is None:
            return False
        if message.guild is None:
            return self.bot.settings.allow_dms
        if message.mention_everyone:
            return False
        if any(user.id == me.id for user in message.mentions):
            return True
        ref = message.reference.resolved if message.reference else None
        return isinstance(ref, discord.Message) and ref.author.id == me.id

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        try:
            await self._handle(message)
        except Exception:  # a listener must never crash the bot
            log.exception("Unhandled error while answering a mention")

    async def _handle(self, message: discord.Message) -> None:
        if message.author.bot or not self._addressed_to_me(message):
            return
        settings = self.bot.settings
        if settings.allowed_channel_ids and message.guild and message.channel.id not in settings.allowed_channel_ids:
            return

        question = strip_bot_mention(message.content, self.bot.user.id if self.bot.user else None)
        responder = self.bot.responder

        if not question:  # a bare mention (or content unavailable)
            embed = new_embed(
                title="✒️ At your service",
                description=f"{random.choice(GREETINGS)}\n\nTry `/help`, or simply write your question after my name.",
                color=Palette.INDIGO,
                avatar_url=responder.avatar_url,
            )
            await message.reply(embed=embed, mention_author=False, allowed_mentions=NO_MENTIONS)
            return

        verdict = responder.begin(message.author.id)
        if not verdict.ok:
            await message.reply(embed=responder.throttle_embed(verdict), mention_author=False, allowed_mentions=NO_MENTIONS)
            return

        request = Request(
            kind="answer",
            user_id=message.author.id,
            channel_id=message.channel.id,
            prompt=f"{message.author.display_name} says: {question[:MAX_QUESTION]}",
            remember=True,
        )
        async with message.channel.typing():
            embed, ok = await responder.generate(request)

        view = AnswerView(self.bot, request, owner_name=message.author.display_name) if ok else discord.utils.MISSING
        sent = await message.reply(embed=embed, view=view, mention_author=False, allowed_mentions=NO_MENTIONS)
        if ok:
            view.message = sent


async def setup(bot) -> None:
    await bot.add_cog(MentionsCog(bot))
