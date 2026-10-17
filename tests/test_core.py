import unittest

import discord

from violet import renderers
from violet.limits import UsageGuard
from violet.memory import ConversationMemory
from violet.utils import (
    as_str_list,
    clean_model_text,
    extract_json,
    format_duration,
    neutralize_mentions,
    strip_bot_mention,
    strip_reasoning,
    truncate,
)


class UtilsTests(unittest.TestCase):
    def test_extract_json_plain_fenced_and_chatty(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('```json\n{"a": 2}\n```'), {"a": 2})
        self.assertEqual(extract_json('Sure! Here you go: {"a": 3} hope it helps'), {"a": 3})

    def test_extract_json_braces_inside_strings_and_nesting(self):
        text = '{"answer": "use {curly} braces } safely", "n": {"x": [1, 2, {"y": 3}]}}'
        self.assertEqual(extract_json(text)["n"]["x"][2]["y"], 3)
        self.assertEqual(extract_json(text)["answer"], "use {curly} braces } safely")

    def test_extract_json_skips_broken_first_object(self):
        self.assertEqual(extract_json('{broken {"ok": true}'), {"ok": True})

    def test_extract_json_failures(self):
        for bad in ("", "no json here", "{unterminated", "[1, 2, 3]"):
            self.assertIsNone(extract_json(bad), bad)

    def test_strip_reasoning(self):
        self.assertEqual(strip_reasoning("<think>hmm\nplanning</think>Hello"), "Hello")
        self.assertEqual(strip_reasoning("planning without opening tag</think>Hello"), "Hello")
        self.assertEqual(strip_reasoning("  plain  "), "plain")

    def test_mentions_are_neutralized(self):
        out = neutralize_mentions("hi @everyone and @here and <@&123456>")
        self.assertNotIn("@everyone", out)
        self.assertNotIn("@here", out)
        self.assertNotIn("<@&", out)
        self.assertNotIn("@everyone", clean_model_text("<think>x</think>ping @everyone"))

    def test_strip_bot_mention(self):
        self.assertEqual(strip_bot_mention("<@123> hello there", 123), "hello there")
        self.assertEqual(strip_bot_mention("<@!123>   hi", 123), "hi")
        self.assertEqual(strip_bot_mention("hey <@123> what's up", 123), "hey  what's up")
        self.assertEqual(strip_bot_mention("<@123>", 123), "")

    def test_truncate_and_lists(self):
        self.assertEqual(truncate("abcdef", 4), "abc…")
        self.assertEqual(truncate("abc", 10), "abc")
        self.assertEqual(truncate("abc", 0), "")
        self.assertEqual(as_str_list(["a", "", 5, None, "b"], limit=2), ["a", "5"])
        self.assertEqual(as_str_list("single"), ["single"])
        self.assertEqual(as_str_list({"x": 1}), [])

    def test_format_duration(self):
        self.assertEqual(format_duration(5), "5s")
        self.assertEqual(format_duration(3700), "1h 1m")
        self.assertEqual(format_duration(90000), "1d 1h")


class GuardTests(unittest.TestCase):
    def make(self, **kw):
        self.now = 1000.0
        self.day = "d1"
        args = dict(cooldown=5, hourly_limit=3, daily_limit=5)
        args.update(kw)
        return UsageGuard(args["cooldown"], args["hourly_limit"], args["daily_limit"], clock=lambda: self.now, utc_day=lambda: self.day)

    def test_cooldown(self):
        g = self.make()
        self.assertTrue(g.begin(1).ok)
        v = g.begin(1)
        self.assertFalse(v.ok)
        self.assertEqual(v.kind, "cooldown")
        self.assertAlmostEqual(v.retry_after, 5, places=1)
        self.assertTrue(g.begin(2).ok, "another user is unaffected")
        self.now += 5.1
        self.assertTrue(g.begin(1).ok)

    def test_hourly_limit_and_window_slides(self):
        g = self.make(cooldown=0)
        for _ in range(3):
            self.assertTrue(g.begin(1).ok)
        v = g.begin(1)
        self.assertEqual((v.ok, v.kind), (False, "hourly"))
        self.now += 3601
        self.assertTrue(g.begin(1).ok)

    def test_daily_budget_and_midnight_reset(self):
        g = self.make(cooldown=0, hourly_limit=100, daily_limit=2)
        self.assertTrue(g.begin(1).ok)
        self.assertTrue(g.begin(2).ok)
        self.assertEqual(g.begin(3).kind, "daily")
        self.day = "d2"
        self.assertTrue(g.begin(3).ok)
        self.assertEqual(g.daily_used, 1)

    def test_refund_gives_everything_back(self):
        g = self.make()
        g.begin(1)
        g.refund(1)
        self.assertTrue(g.begin(1).ok, "no cooldown after a refunded failure")
        self.assertEqual(g.daily_used, 1)

    def test_check_does_not_count(self):
        g = self.make()
        for _ in range(10):
            self.assertTrue(g.check(1).ok)
        self.assertEqual(g.daily_used, 0)


class MemoryTests(unittest.TestCase):
    def test_history_ttl_and_limit(self):
        t = [0.0]
        m = ConversationMemory(turns=2, ttl_seconds=100, clock=lambda: t[0])
        for i in range(10):
            m.add("k", "user", f"msg{i}")
        self.assertEqual(len(m.history("k")), 4, "keeps only the last 2 turns (4 messages)")
        self.assertEqual(m.history("k")[-1]["content"], "msg9")
        t[0] = 101
        self.assertEqual(m.history("k"), [])

    def test_lru_eviction_and_clear(self):
        m = ConversationMemory(turns=2, ttl_seconds=100, max_keys=2)
        m.add("a", "user", "1")
        m.add("b", "user", "2")
        m.history("a")  # touch a
        m.add("c", "user", "3")  # evicts b
        self.assertEqual(m.history("b"), [])
        self.assertNotEqual(m.history("a"), [])
        self.assertTrue(m.clear("a"))
        self.assertFalse(m.clear("a"))

    def test_disabled_when_zero_turns(self):
        m = ConversationMemory(turns=0, ttl_seconds=100)
        m.add("a", "user", "x")
        self.assertFalse(m.enabled)
        self.assertEqual(m.history("a"), [])


def embed_text(e: discord.Embed) -> str:
    parts = [e.title or "", e.description or "", (e.footer.text if e.footer else "")]
    for f in e.fields:
        parts += [f.name, f.value]
    return "\n".join(parts)


class RendererTests(unittest.TestCase):
    def assert_within_discord_limits(self, e: discord.Embed):
        self.assertLessEqual(len(e), 6000)
        self.assertLessEqual(len(e.title or ""), 256)
        self.assertLessEqual(len(e.description or ""), 4096)
        self.assertLessEqual(len(e.fields), 25)
        for f in e.fields:
            self.assertLessEqual(len(f.name), 256)
            self.assertLessEqual(len(f.value), 1024)
            self.assertTrue(f.name.strip() and f.value.strip(), "no empty fields (Discord rejects them)")
        self.assertLessEqual(len(e.footer.text or ""), 2048)

    def test_answer_full(self):
        data = {
            "topic": "pc", "headline": "Why your GPU runs hot", "answer": "Dust and fan curves are the usual culprits.",
            "key_points": ["Clean the fans", "Check airflow"], "steps": ["Open the case", "Use compressed air"],
            "caveat": "Temperature limits differ per model.", "confidence": "high", "curious": "Which GPU do you have?",
        }
        e = renderers.render_answer(data, "raw", topic_hint=None, avatar_url="https://x/y.png")
        self.assertTrue(e.title.startswith("🖥️"))
        self.assertIn("Which GPU do you have?", e.description)
        self.assertEqual([f.name for f in e.fields], ["✦ Key points", "🛠️ Steps", "🔎 Worth verifying"])
        self.assertIn("◆◆◆", e.footer.text)
        self.assert_within_discord_limits(e)

    def test_user_topic_wins_over_model_topic(self):
        e = renderers.render_answer({"topic": "pc", "answer": "x"}, "raw", topic_hint="gaming", avatar_url=None)
        self.assertTrue(e.title.startswith("🎮"))

    def test_answer_falls_back_to_plain_text(self):
        e = renderers.render_answer(None, "Just some plain words.", topic_hint=None, avatar_url=None)
        self.assertEqual(e.description, "Just some plain words.")
        e2 = renderers.render_answer({"headline": "no answer key"}, "fallback text", topic_hint=None, avatar_url=None)
        self.assertEqual(e2.description, "fallback text")

    def test_malformed_types_never_crash(self):
        weird = {
            "topic": 5, "headline": ["x"], "answer": "ok", "key_points": "just a string", "steps": {"a": 1},
            "caveat": None, "confidence": 3, "curious": 12,
        }
        e = renderers.render_answer(weird, "raw", topic_hint=None, avatar_url=None)
        self.assert_within_discord_limits(e)
        t = renderers.render_troubleshoot({"diagnosis": "d", "likely_causes": "oops", "steps": 7}, "raw", subject="s", avatar_url=None)
        self.assert_within_discord_limits(t)
        c = renderers.render_compare({"verdict": "v", "first": "str", "second": None, "best_for": []}, "raw", first="A", second="B", avatar_url=None)
        self.assert_within_discord_limits(c)
        r = renderers.render_recommend({"picks": [None, 3, {"name": ""}, {"name": "Real"}]}, "raw", category="Games", avatar_url=None)
        self.assertEqual(len(r.fields), 1)
        self.assertEqual(renderers.render_letter(None, "raw words", recipient="Sam", avatar_url=None).description.count("raw words"), 1)

    def test_troubleshoot_full(self):
        data = {
            "headline": "Stutter in Elden Ring", "diagnosis": "Likely shader compilation.",
            "likely_causes": [{"cause": "Shader cache", "why": "first run"}, "Background apps"],
            "steps": ["Restart", "Update driver"], "risk_note": "Back up saves first.", "escalate_if": "Crashes persist", "confidence": "medium",
        }
        e = renderers.render_troubleshoot(data, "raw", subject="Elden Ring", avatar_url=None)
        names = [f.name for f in e.fields]
        self.assertEqual(names, ["🔎 Likely causes", "🛠️ Steps — gentlest first", "⚠️ Handle with care", "🚩 Seek further help if"])
        self.assertIn("**Shader cache**", e.fields[0].value)
        self.assert_within_discord_limits(e)

    def test_compare_full(self):
        data = {
            "headline": "A vs B", "first": {"name": "A", "strengths": ["fast"], "weaknesses": ["loud"]},
            "second": {"name": "B", "strengths": ["quiet"], "weaknesses": ["slow"]}, "verdict": "Pick A for speed.",
            "best_for": {"first": "tinkerers", "second": "silence lovers"}, "caveat": "Check prices", "confidence": "low",
        }
        e = renderers.render_compare(data, "raw", first="A", second="B", avatar_url=None)
        self.assertEqual([f.inline for f in e.fields[:2]], [True, True])
        self.assertEqual(e.fields[2].name, "📜 Verdict")
        self.assert_within_discord_limits(e)

    def test_recommend_caps_at_five(self):
        data = {"picks": [{"name": f"Game {i}", "why": "good", "note": "n"} for i in range(9)], "advice": "Start small"}
        e = renderers.render_recommend(data, "raw", category="Games", avatar_url=None)
        self.assertEqual(len([f for f in e.fields if f.name.startswith("`")]), 5)
        self.assert_within_discord_limits(e)

    def test_letter_is_a_blockquote_with_sign_off(self):
        e = renderers.render_letter({"letter": "Dear Sam,\nThank you.", "sign_off": "Yours truly"}, "raw", recipient="Sam", avatar_url=None)
        self.assertTrue(e.description.startswith(">>> Dear Sam"))
        self.assertIn("Yours truly, Violet", e.description)

    def test_worst_case_sizes_stay_inside_discord_limits(self):
        big = "x" * 5000
        data = {
            "headline": big, "answer": big, "key_points": [big] * 20, "steps": [big] * 20, "caveat": big, "curious": big, "confidence": "high",
        }
        self.assert_within_discord_limits(renderers.render_answer(data, big, topic_hint="gaming", avatar_url=None))
        ts = {"headline": big, "diagnosis": big, "likely_causes": [{"cause": big, "why": big}] * 9, "steps": [big] * 20, "risk_note": big, "escalate_if": big}
        self.assert_within_discord_limits(renderers.render_troubleshoot(ts, big, subject=big, avatar_url=None))
        cp = {"headline": big, "first": {"name": big, "strengths": [big] * 5, "weaknesses": [big] * 5}, "second": {"name": big, "strengths": [big] * 5, "weaknesses": [big] * 5}, "verdict": big, "best_for": {"first": big, "second": big}, "caveat": big}
        self.assert_within_discord_limits(renderers.render_compare(cp, big, first=big, second=big, avatar_url=None))
        rc = {"headline": big, "advice": big, "picks": [{"name": big, "why": big, "note": big}] * 9, "caveat": big}
        self.assert_within_discord_limits(renderers.render_recommend(rc, big, category=big, avatar_url=None))
        self.assert_within_discord_limits(renderers.render_letter({"letter": big, "sign_off": big}, big, recipient=big, avatar_url=None))
        self.assert_within_discord_limits(renderers.render_answer(None, big, topic_hint=None, avatar_url=None))

    def test_no_mass_mentions_in_any_embed(self):
        bad = "hello @everyone and @here <@&999>"
        data = {"headline": bad, "answer": bad, "key_points": [bad], "steps": [bad], "caveat": bad, "curious": bad, "confidence": "high"}
        for e in (
            renderers.render_answer(data, bad, topic_hint=None, avatar_url=None),
            renderers.render_answer(None, bad, topic_hint=None, avatar_url=None),
            renderers.render_letter({"letter": bad, "sign_off": bad}, bad, recipient=bad, avatar_url=None),
            renderers.render_compare({"verdict": bad, "first": {"name": bad}, "second": {"name": bad}}, bad, first=bad, second=bad, avatar_url=None),
        ):
            text = embed_text(e)
            self.assertNotIn("@everyone", text)
            self.assertNotIn("@here", text)
            self.assertNotIn("<@&", text)

    def test_throttle_embeds(self):
        e = renderers.throttle_embed("cooldown", 3.2)
        self.assertIn("4s", e.description)
        h = renderers.throttle_embed("hourly", 125)
        self.assertIn("3 min", h.description)
        self.assertTrue(renderers.throttle_embed("daily", 0).description)
        self.assertTrue(renderers.throttle_embed("weird-kind", 1).description)


if __name__ == "__main__":
    unittest.main()
