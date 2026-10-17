"""Interactive buttons shown under Violet's replies."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from .persona import error_line
from .responder import Request
from .theme import Palette, new_embed

if TYPE_CHECKING:
    from .bot import VioletBot

log = logging.getLogger("violet.views")
NO_MENTIONS = discord.AllowedMentions.none()


class AnswerView(discord.ui.View):
    """🔄 Regenerate · 🪶 Simpler · ✖ Dismiss. Buttons expire after 10 minutes so none are ever left dead."""

    def __init__(self, bot: "VioletBot", request: Request, *, owner_name: str) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.request = request
        self.owner_name = owner_name
        self.message: discord.Message | discord.WebhookMessage | None = None

    # Only the person who asked (or a moderator) may use the buttons.
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.request.user_id:
            return True
        perms = getattr(interaction, "permissions", None)
        if perms is not None and perms.manage_messages:
            return True
        embed = new_embed(
            description=f"🕯️ {error_line('not_yours', name=self.owner_name)}",
            color=Palette.GOLD,
            avatar_url=self.bot.responder.avatar_url,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass  # message deleted, or the interaction token expired

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.exception("Error in AnswerView button", exc_info=error)
        await _safe_notice(interaction, self.bot)

    # ------------------------------------------------------------------ buttons
    @discord.ui.button(label="Regenerate", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redo(interaction, mode="normal")

    @discord.ui.button(label="Simpler", emoji="🪶", style=discord.ButtonStyle.secondary)
    async def simpler(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redo(interaction, mode="simple")

    @discord.ui.button(label="Dismiss", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        try:
            await interaction.response.defer()
            await interaction.delete_original_response()
        except discord.HTTPException:
            if interaction.message is not None:
                try:
                    await interaction.message.delete()
                except discord.HTTPException:
                    pass

    # ------------------------------------------------------------------ shared
    async def _redo(self, interaction: discord.Interaction, *, mode: str) -> None:
        responder = self.bot.responder
        verdict = responder.begin(interaction.user.id)  # the clicker spends their own quota
        if not verdict.ok:
            await interaction.response.send_message(embed=responder.throttle_embed(verdict), ephemeral=True)
            return

        # Acknowledge by disabling the buttons while Violet writes again
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)

        embed, ok = await responder.generate(self.request, mode=mode)

        for child in self.children:
            child.disabled = False  # type: ignore[attr-defined]
        try:
            await interaction.edit_original_response(embed=embed, view=self if ok else None, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            log.warning("Could not update the reply (message deleted or token expired)")


async def _safe_notice(interaction: discord.Interaction, bot: "VioletBot") -> None:
    embed = new_embed(
        description=f"🕯️ {error_line('unexpected')}", color=Palette.ROSE, avatar_url=bot.responder.avatar_url
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass
