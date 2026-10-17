import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from tests.helpers import make_settings
from violet import bot as bot_module
from violet.llm import LLMAuthError, LLMUnavailable
from violet.views import AnswerView

ANSWER_JSON = json.dumps(
    {
        "topic": "gaming", "headline": "Smoothing out stutter", "answer": "Shader compilation is the usual cause.",
        "key_points": ["Let shaders compile once"], "steps": ["Restart the game", "Update the driver"],
        "caveat": "", "confidence": "high", "curious": "Which game troubles you?",
    }
)
BOT_ID = 555000111


def fake_interaction(user_id=1, name="Sam", channel_id=42):
    i = MagicMock()
    i.user = SimpleNamespace(id=user_id, display_name=name)
    i.channel_id = channel_id
    i.permissions = SimpleNamespace(manage_messages=False)
    i.response.defer = AsyncMock()
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.is_done = MagicMock(return_value=False)
    i.followup.send = AsyncMock(return_value=MagicMock(spec=discord.WebhookMessage))
    i.edit_original_response = AsyncMock()
    i.delete_original_response = AsyncMock()
    return i


def fake_message(content, *, author_id=7, guild=True, channel_id=99, mentions_bot=True, everyone=False):
    m = MagicMock()
    m.author = SimpleNamespace(id=author_id, bot=False, display_name="Mira")
    m.content = content
    m.guild = object() if guild else None
    m.mention_everyone = everyone
    m.mentions = [SimpleNamespace(id=BOT_ID)] if mentions_bot else []
    m.reference = None
    m.channel = MagicMock()
    m.channel.id = channel_id
    typing_cm = MagicMock()
    typing_cm.__aenter__ = AsyncMock()
    typing_cm.__aexit__ = AsyncMock(return_value=False)
    m.channel.typing = MagicMock(return_value=typing_cm)
    m.reply = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return m


class BotTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self, **settings):
        self.bot = bot_module.VioletBot(make_settings(**settings))
        await self.bot._async_setup_hook()  # what login() does just before setup_hook() in real life
        await self.bot.setup_hook()
        fake_user = SimpleNamespace(
            id=BOT_ID, display_avatar=SimpleNamespace(url="https://cdn.example/violet.png"), name="Violet", __str__=lambda s: "Violet#0001"
        )
        self.bot._connection.user = fake_user
        self.bot.responder.avatar_url = fake_user.display_avatar.url
        self.bot.llm.chat = AsyncMock(return_value=ANSWER_JSON)
        self.addAsyncCleanup(self.bot.close)

    def cog(self, name):
        return self.bot.get_cog(name)


class RegistrationTests(BotTestCase):
    async def test_all_cogs_and_commands_load_with_valid_definitions(self):
        names = {c.name for c in self.bot.tree.get_commands()}
        self.assertEqual(names, {"ask", "troubleshoot", "compare", "recommend", "letter", "help", "about", "ping", "status", "forget"})
        for cmd in self.bot.tree.get_commands():
            payload = cmd.to_dict(self.bot.tree)
            self.assertTrue(1 <= len(payload["description"]) <= 100, cmd.name)
            for opt in payload.get("options", []):
                self.assertTrue(1 <= len(opt["description"]) <= 100, f"{cmd.name}.{opt['name']}")
        self.assertEqual({"AskCog", "ToolsCog", "GeneralCog", "MentionsCog"}, {c for c in self.bot.cogs})


class SlashFlowTests(BotTestCase):
    async def test_ask_happy_path(self):
        i = fake_interaction()
        await self.cog("AskCog").ask.callback(self.cog("AskCog"), i, "Why does my game stutter?", None)
        i.response.defer.assert_awaited_once()
        kwargs = i.followup.send.await_args.kwargs
        self.assertTrue(kwargs["embed"].title.startswith("🎮"))
        self.assertIsInstance(kwargs["view"], AnswerView)
        self.assertTrue(kwargs["allowed_mentions"].everyone is False)
        sent = self.bot.llm.chat.await_args.args[0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("Why does my game stutter?", sent[-1]["content"])

    async def test_topic_choice_is_forwarded_to_the_prompt(self):
        i = fake_interaction()
        choice = discord.app_commands.Choice(name="PC", value="pc")
        await self.cog("AskCog").ask.callback(self.cog("AskCog"), i, "fan noise", choice)
        self.assertIn("'pc'", self.bot.llm.chat.await_args.args[0][0]["content"])

    async def test_follow_up_questions_get_conversation_history(self):
        a = self.cog("AskCog")
        await a.ask.callback(a, fake_interaction(), "first question", None)
        await a.ask.callback(a, fake_interaction(), "second question", None)
        msgs = self.bot.llm.chat.await_args.args[0]
        roles = [m["role"] for m in msgs]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertIn("first question", msgs[1]["content"])

    async def test_forget_clears_history(self):
        a, g = self.cog("AskCog"), self.cog("GeneralCog")
        await a.ask.callback(a, fake_interaction(), "remember this", None)
        await g.forget.callback(g, fake_interaction())
        await a.ask.callback(a, fake_interaction(), "new topic", None)
        self.assertEqual([m["role"] for m in self.bot.llm.chat.await_args.args[0]], ["system", "user"])

    async def test_cooldown_blocks_second_request_without_calling_the_model(self):
        self.bot.guard._cooldown = 8.0
        a = self.cog("AskCog")
        await a.ask.callback(a, fake_interaction(), "one", None)
        i2 = fake_interaction()
        await a.ask.callback(a, i2, "two", None)
        self.assertEqual(self.bot.llm.chat.await_count, 1)
        i2.response.defer.assert_not_awaited()
        self.assertTrue(i2.response.send_message.await_args.kwargs["ephemeral"])

    async def test_failed_generation_shows_gentle_error_and_refunds_quota(self):
        self.bot.guard._cooldown = 8.0
        self.bot.llm.chat = AsyncMock(side_effect=LLMUnavailable("down"))
        a = self.cog("AskCog")
        i = fake_interaction()
        await a.ask.callback(a, i, "anything", None)
        kwargs = i.followup.send.await_args.kwargs
        self.assertIn("🕯️", kwargs["embed"].description)
        self.assertIs(kwargs["view"], discord.utils.MISSING, "no buttons on an error")
        self.assertTrue(self.bot.guard.check(1).ok, "the user is not punished for an outage")
        self.assertEqual(self.bot.guard.daily_used, 0)

    async def test_auth_failure_and_unexpected_bug_are_both_contained(self):
        a = self.cog("AskCog")
        for exc in (LLMAuthError("bad key"), RuntimeError("a bug")):
            self.bot.llm.chat = AsyncMock(side_effect=exc)
            i = fake_interaction()
            await a.ask.callback(a, i, "anything", None)
            self.assertIn("🕯️", i.followup.send.await_args.kwargs["embed"].description)

    async def test_model_returning_plain_text_still_renders(self):
        self.bot.llm.chat = AsyncMock(return_value="Just a plain reply, no JSON at all.")
        a = self.cog("AskCog")
        i = fake_interaction()
        await a.ask.callback(a, i, "hello", None)
        self.assertEqual(i.followup.send.await_args.kwargs["embed"].description, "Just a plain reply, no JSON at all.")

    async def test_tools_commands(self):
        t = self.cog("ToolsCog")
        cases = [
            (t.troubleshoot, ("RTX 3060", "black screen on boot"), json.dumps({"headline": "Black screen", "diagnosis": "Check cables.", "steps": ["Reseat the GPU"], "likely_causes": [{"cause": "Cable", "why": "loose"}]}), "🩺"),
            (t.compare, ("PS5", "Xbox Series X"), json.dumps({"headline": "PS5 vs Xbox", "verdict": "Depends on exclusives.", "first": {"name": "PS5"}, "second": {"name": "Xbox"}}), "⚖️"),
            (t.recommend, (discord.app_commands.Choice(name="🌸 Anime", value="anime"), "cozy slice of life"), json.dumps({"headline": "Cozy anime", "picks": [{"name": "Yuru Camp", "why": "relaxing"}]}), "💌"),
            (t.letter, ("Sam", "thank you for carrying our raid"), json.dumps({"letter": "Dear Sam,\nThank you.", "sign_off": "Gratefully"}), "✒️"),
        ]
        for command, args, reply, emoji in cases:
            self.bot.guard._last.clear()
            self.bot.llm.chat = AsyncMock(return_value=reply)
            i = fake_interaction()
            await command.callback(t, i, *args)
            embed = i.followup.send.await_args.kwargs["embed"]
            self.assertTrue(embed.title.startswith(emoji), (command.name, embed.title))

    async def test_status_help_ping(self):
        g = self.cog("GeneralCog")
        self.bot._connection._ready = None
        for cmd in (g.status, g.help, g.about, g.ping):
            i = fake_interaction()
            i.original_response = AsyncMock(return_value=MagicMock())
            with patch.object(type(self.bot), "latency", new=0.05), patch.object(type(self.bot), "guilds", new=[object()]):
                await cmd.callback(g, i)
            i.response.send_message.assert_awaited_once()


class ViewTests(BotTestCase):
    def make_view(self, owner=1):
        from violet.responder import Request

        req = Request(kind="answer", user_id=owner, channel_id=42, prompt="Sam asks: stutter?", remember=True)
        return AnswerView(self.bot, req, owner_name="Sam")

    async def test_strangers_are_politely_refused(self):
        view = self.make_view(owner=1)
        stranger = fake_interaction(user_id=2)
        self.assertFalse(await view.interaction_check(stranger))
        self.assertTrue(stranger.response.send_message.await_args.kwargs["ephemeral"])
        self.assertTrue(await view.interaction_check(fake_interaction(user_id=1)))
        mod = fake_interaction(user_id=3)
        mod.permissions = SimpleNamespace(manage_messages=True)
        self.assertTrue(await view.interaction_check(mod))

    async def test_regenerate_edits_the_same_message(self):
        view = self.make_view()
        i = fake_interaction()
        await view.regenerate.callback(i)
        i.response.edit_message.assert_awaited_once()
        kwargs = i.edit_original_response.await_args.kwargs
        self.assertIn("embed", kwargs)
        self.assertFalse(any(c.disabled for c in view.children), "buttons are re-enabled afterwards")

    async def test_simpler_asks_the_model_for_a_simple_mode_prompt(self):
        view = self.make_view()
        await view.simpler.callback(fake_interaction())
        self.assertIn("simpler explanation", self.bot.llm.chat.await_args.args[0][0]["content"])

    async def test_redo_respects_the_clickers_quota(self):
        self.bot.guard._cooldown = 8.0
        view = self.make_view()
        self.bot.guard.begin(1)  # the owner just asked something
        i = fake_interaction()
        await view.regenerate.callback(i)
        self.assertEqual(self.bot.llm.chat.await_count, 0)
        self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"])

    async def test_dismiss_deletes_the_message(self):
        view = self.make_view()
        i = fake_interaction()
        await view.dismiss.callback(i)
        i.delete_original_response.assert_awaited_once()

    async def test_timeout_removes_buttons(self):
        view = self.make_view()
        view.message = MagicMock()
        view.message.edit = AsyncMock()
        await view.on_timeout()
        view.message.edit.assert_awaited_once_with(view=None)


class MentionTests(BotTestCase):
    async def test_mention_gets_an_answer_without_the_mention_text(self):
        m = fake_message(f"<@{BOT_ID}> why does my GPU hit 90C?")
        await self.cog("MentionsCog")._handle(m)
        m.reply.assert_awaited_once()
        self.assertTrue(m.reply.await_args.kwargs["mention_author"] is False)
        self.assertIsInstance(m.reply.await_args.kwargs["view"], AnswerView)
        prompt = self.bot.llm.chat.await_args.args[0][-1]["content"]
        self.assertIn("why does my GPU hit 90C?", prompt)
        self.assertNotIn(str(BOT_ID), prompt)

    async def test_bare_mention_gets_a_greeting_and_no_model_call(self):
        m = fake_message(f"<@{BOT_ID}>")
        await self.cog("MentionsCog")._handle(m)
        self.assertEqual(self.bot.llm.chat.await_count, 0)
        self.assertIn("At your service", m.reply.await_args.kwargs["embed"].title)

    async def test_ignored_messages(self):
        c = self.cog("MentionsCog")
        for msg in (
            fake_message("hello", mentions_bot=False),
            fake_message(f"<@{BOT_ID}> hi", everyone=True),
        ):
            await c._handle(msg)
            msg.reply.assert_not_awaited()
        bot_msg = fake_message(f"<@{BOT_ID}> hi")
        bot_msg.author.bot = True
        await c._handle(bot_msg)
        bot_msg.reply.assert_not_awaited()
        self.assertEqual(self.bot.llm.chat.await_count, 0)

    async def test_dm_policy(self):
        c = self.cog("MentionsCog")
        dm = fake_message("hello violet", guild=False, mentions_bot=False)
        await c._handle(dm)
        dm.reply.assert_awaited_once()
        self.bot.settings = make_settings(allow_dms=False)
        dm2 = fake_message("hello violet", guild=False, mentions_bot=False)
        await c._handle(dm2)
        dm2.reply.assert_not_awaited()

    async def test_channel_restriction(self):
        self.bot.settings = make_settings(allowed_channel_ids=frozenset({1234}))
        c = self.cog("MentionsCog")
        outside = fake_message(f"<@{BOT_ID}> hi there", channel_id=99)
        await c._handle(outside)
        outside.reply.assert_not_awaited()
        inside = fake_message(f"<@{BOT_ID}> hi there", channel_id=1234)
        await c._handle(inside)
        inside.reply.assert_awaited_once()

    async def test_listener_never_raises_even_if_discord_fails(self):
        m = fake_message(f"<@{BOT_ID}> hi there")
        m.reply = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "boom"))
        await self.cog("MentionsCog").on_message(m)  # must not raise

    async def test_reply_to_violet_counts_as_addressing_her(self):
        resolved = MagicMock(spec=discord.Message)
        resolved.author = SimpleNamespace(id=BOT_ID)
        m = fake_message("and what about AMD?", mentions_bot=False)
        m.reference = SimpleNamespace(resolved=resolved)
        await self.cog("MentionsCog")._handle(m)
        m.reply.assert_awaited_once()


class SyncTests(BotTestCase):
    async def test_sync_only_when_commands_change(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bot_module, "DATA_DIR", Path(tmp)):
            self.bot.settings = make_settings(sync_mode="auto")
            self.bot.tree.sync = AsyncMock(return_value=[1, 2, 3])
            await self.bot._sync_commands()
            self.assertEqual(self.bot.tree.sync.await_count, 1)
            await self.bot._sync_commands()
            self.assertEqual(self.bot.tree.sync.await_count, 1, "unchanged commands are not re-synced")
            self.bot.settings = make_settings(sync_mode="always")
            await self.bot._sync_commands()
            self.assertEqual(self.bot.tree.sync.await_count, 2)
            self.bot.settings = make_settings(sync_mode="never")
            await self.bot._sync_commands()
            self.assertEqual(self.bot.tree.sync.await_count, 2)

    async def test_failed_sync_does_not_crash_and_retries_next_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bot_module, "DATA_DIR", Path(tmp)):
            self.bot.settings = make_settings(sync_mode="auto")
            self.bot.tree.sync = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "boom"))
            await self.bot._sync_commands()
            self.assertFalse((Path(tmp) / "commands.sha256").exists())

    async def test_guild_sync_targets_the_guild(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bot_module, "DATA_DIR", Path(tmp)):
            self.bot.settings = make_settings(sync_mode="always", guild_id=123)
            self.bot.tree.sync = AsyncMock(return_value=[])
            await self.bot._sync_commands()
            self.assertEqual(self.bot.tree.sync.await_args.kwargs["guild"].id, 123)


class ErrorHandlerTests(BotTestCase):
    async def test_global_error_handler_replies_gently(self):
        i = fake_interaction()
        await self.bot.on_app_command_error(i, discord.app_commands.CommandOnCooldown(MagicMock(), 3.0))
        self.assertIn("🕯️", i.response.send_message.await_args.kwargs["embed"].description)
        i2 = fake_interaction()
        i2.response.is_done = MagicMock(return_value=True)
        await self.bot.on_app_command_error(i2, discord.app_commands.AppCommandError("boom"))
        i2.followup.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
