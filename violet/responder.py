"""The brain that connects everything: guard -> memory -> LLM -> renderer. It never raises to the caller."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import discord

from . import renderers
from .config import Settings
from .limits import UsageGuard, Verdict
from .llm import (
    LLMAuthError,
    LLMBadResponse,
    LLMClient,
    LLMNotConfigured,
    LLMRateLimited,
    LLMUnavailable,
)
from .memory import ConversationMemory
from .persona import build_system_prompt
from .utils import clean_model_text, extract_json, truncate

log = logging.getLogger("violet.responder")


@dataclass(frozen=True)
class Request:
    kind: str  # answer | troubleshoot | compare | recommend | letter
    user_id: int
    channel_id: int
    prompt: str  # the full text sent to the model
    topic: str = "general"
    remember: bool = False
    # Used only for presentation
    subject: str = ""
    first: str = ""
    second: str = ""
    category: str = ""
    recipient: str = ""


class Responder:
    def __init__(self, settings: Settings, llm: LLMClient, guard: UsageGuard, memory: ConversationMemory) -> None:
        self.settings = settings
        self.llm = llm
        self.guard = guard
        self.memory = memory
        self.avatar_url: str | None = None  # set once the bot is ready

    # ------------------------------------------------------------------ fair use
    def begin(self, user_id: int) -> Verdict:
        """Check and count a request. If not ok, show `throttle_embed` and do not call generate()."""
        return self.guard.begin(user_id)

    def throttle_embed(self, verdict: Verdict) -> discord.Embed:
        return renderers.throttle_embed(verdict.kind, verdict.retry_after, avatar_url=self.avatar_url)

    # ------------------------------------------------------------------ generation
    async def generate(self, req: Request, *, mode: str = "normal") -> tuple[discord.Embed, bool]:
        """Returns (embed, ok). On failure the quota is refunded and a gentle in-character embed is returned."""
        key = (req.channel_id, req.user_id)
        messages: list[dict[str, str]] = [{"role": "system", "content": build_system_prompt(req.kind, req.topic, mode)}]
        if req.remember:
            messages.extend(self.memory.history(key))
        messages.append({"role": "user", "content": req.prompt})

        try:
            raw = await self.llm.chat(messages)
        except LLMNotConfigured:
            return self._fail(req, "not_configured")
        except LLMAuthError:
            log.error("The language service rejected the API key (LLM_API_KEY).")
            return self._fail(req, "auth")
        except LLMRateLimited:
            return self._fail(req, "rate_limited")
        except LLMUnavailable:
            return self._fail(req, "unavailable")
        except LLMBadResponse:
            return self._fail(req, "bad")
        except Exception:  # a bug must never take the bot down
            log.exception("Unexpected failure while generating a reply")
            return self._fail(req, "unexpected")

        try:
            text = clean_model_text(raw)
            data = extract_json(text)
            embed = self._render(req, data, text)
        except Exception:
            log.exception("Failed to render a reply")
            return self._fail(req, "unexpected")

        if req.remember and self.memory.enabled:
            reply_text = (data or {}).get("answer") if isinstance((data or {}).get("answer"), str) else text
            self.memory.add(key, "user", req.prompt)
            self.memory.add(key, "assistant", truncate(str(reply_text), 900))
        return embed, True

    # ------------------------------------------------------------------ helpers
    def _fail(self, req: Request, key: str) -> tuple[discord.Embed, bool]:
        self.guard.refund(req.user_id)
        return renderers.error_embed(key, avatar_url=self.avatar_url), False

    def _render(self, req: Request, data: dict | None, text: str) -> discord.Embed:
        av = self.avatar_url
        if req.kind == "troubleshoot":
            return renderers.render_troubleshoot(data, text, subject=req.subject, avatar_url=av)
        if req.kind == "compare":
            return renderers.render_compare(data, text, first=req.first, second=req.second, avatar_url=av)
        if req.kind == "recommend":
            return renderers.render_recommend(data, text, category=req.category, avatar_url=av)
        if req.kind == "letter":
            return renderers.render_letter(data, text, recipient=req.recipient, avatar_url=av)
        return renderers.render_answer(data, text, topic_hint=req.topic, avatar_url=av)
