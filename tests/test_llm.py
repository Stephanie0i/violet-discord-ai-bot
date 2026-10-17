import asyncio
import unittest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from tests.helpers import make_settings
from violet.llm import (
    BREAKER_COOLDOWN,
    LLMAuthError,
    LLMBadResponse,
    LLMClient,
    LLMNotConfigured,
    LLMRateLimited,
    LLMUnavailable,
)

MSGS = [{"role": "user", "content": "hi"}]


def ok_body(text="Hello there"):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class FakeProvider:
    """A scripted OpenAI-compatible server: each request pops the next behaviour from the script."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.last_json = None
        self.last_auth = None

    async def handler(self, request: web.Request) -> web.StreamResponse:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.last_json = await request.json()
            self.last_auth = request.headers.get("Authorization")
            step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            kind = step[0]
            if kind == "ok":
                return web.json_response(ok_body(step[1]))
            if kind == "status":
                headers = step[2] if len(step) > 2 else {}
                return web.Response(status=step[1], text="provider says no", headers=headers)
            if kind == "raw":
                return web.Response(status=200, text=step[1], content_type="application/json")
            if kind == "json":
                return web.json_response(step[1])
            if kind == "slow":
                await asyncio.sleep(step[1])
                return web.json_response(ok_body())
            raise AssertionError(kind)
        finally:
            self.active -= 1


class LLMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sleeps = []
        self.clock_now = 0.0

    async def make(self, script, **settings):
        self.provider = FakeProvider(script)
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.provider.handler)
        self.server = TestServer(app)
        await self.server.start_server()
        self.session = aiohttp.ClientSession()
        s = make_settings(llm_base_url=str(self.server.make_url("/v1")), **settings)

        async def fake_sleep(d):
            self.sleeps.append(d)

        self.client = LLMClient(s, self.session, sleep=fake_sleep, clock=lambda: self.clock_now)
        self.addAsyncCleanup(self.session.close)
        self.addAsyncCleanup(self.server.close)
        return self.client

    async def test_success_sends_correct_request(self):
        c = await self.make([("ok", "Hello there")])
        self.assertEqual(await c.chat(MSGS), "Hello there")
        self.assertEqual(self.provider.last_auth, "Bearer test-key")
        body = self.provider.last_json
        self.assertEqual((body["model"], body["stream"], body["messages"]), ("test-model", False, MSGS))

    async def test_reasoning_blocks_are_removed(self):
        c = await self.make([("ok", "<think>secret planning</think>The answer")])
        self.assertEqual(await c.chat(MSGS), "The answer")

    async def test_content_as_list_of_parts(self):
        c = await self.make([("json", {"choices": [{"message": {"content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]}}]})])
        self.assertEqual(await c.chat(MSGS), "AB")

    async def test_429_retries_using_retry_after_then_succeeds(self):
        c = await self.make([("status", 429, {"Retry-After": "2"}), ("ok", "finally")])
        self.assertEqual(await c.chat(MSGS), "finally")
        self.assertEqual(self.provider.calls, 2)
        self.assertEqual(self.sleeps, [2.0])

    async def test_retry_after_is_capped(self):
        c = await self.make([("status", 429, {"Retry-After": "999"}), ("ok", "x")])
        await c.chat(MSGS)
        self.assertLessEqual(self.sleeps[0], 8.0)

    async def test_persistent_429_raises_rate_limited(self):
        c = await self.make([("status", 429)])
        with self.assertRaises(LLMRateLimited):
            await c.chat(MSGS)
        self.assertEqual(self.provider.calls, 3)

    async def test_server_errors_retry_then_raise_unavailable(self):
        c = await self.make([("status", 503)])
        with self.assertRaises(LLMUnavailable):
            await c.chat(MSGS)
        self.assertEqual(self.provider.calls, 3)
        self.assertEqual(len(self.sleeps), 2, "backs off between attempts, not after the last")
        self.assertLess(self.sleeps[0], self.sleeps[1])

    async def test_transient_500_then_ok(self):
        c = await self.make([("status", 500), ("ok", "recovered")])
        self.assertEqual(await c.chat(MSGS), "recovered")

    async def test_auth_error_fails_fast_and_does_not_trip_breaker(self):
        c = await self.make([("status", 401)])
        for _ in range(8):
            with self.assertRaises(LLMAuthError):
                await c.chat(MSGS)
        self.assertEqual(self.provider.calls, 8, "exactly one attempt each, no retries")
        self.assertFalse(c.breaker_open)

    async def test_bad_request_fails_fast(self):
        c = await self.make([("status", 400)])
        with self.assertRaises(LLMBadResponse):
            await c.chat(MSGS)
        self.assertEqual(self.provider.calls, 1)

    async def test_unreadable_and_empty_responses(self):
        c = await self.make([("raw", "this is not json")])
        with self.assertRaises(LLMBadResponse):
            await c.chat(MSGS)
        c2 = await self.make([("json", {"choices": []})])
        with self.assertRaises(LLMBadResponse):
            await c2.chat(MSGS)
        c3 = await self.make([("ok", "<think>only thoughts</think>")])
        with self.assertRaises(LLMBadResponse):
            await c3.chat(MSGS)

    async def test_timeout_is_retried_then_unavailable(self):
        c = await self.make([("slow", 5)], llm_timeout=0.15)
        with self.assertRaises(LLMUnavailable):
            await c.chat(MSGS)
        self.assertEqual(self.provider.calls, 3)

    async def test_not_configured(self):
        c = await self.make([("ok", "x")], llm_api_key="")
        with self.assertRaises(LLMNotConfigured):
            await c.chat(MSGS)
        self.assertEqual(self.provider.calls, 0)

    async def test_circuit_breaker_opens_then_recovers(self):
        c = await self.make([("status", 500)])
        for _ in range(5):
            with self.assertRaises(LLMUnavailable):
                await c.chat(MSGS)
        self.assertTrue(c.breaker_open)
        calls_before = self.provider.calls
        with self.assertRaises(LLMUnavailable) as ctx:
            await c.chat(MSGS)
        self.assertIn("cooling down", str(ctx.exception))
        self.assertEqual(self.provider.calls, calls_before, "no request is sent while the breaker is open")

        self.clock_now += BREAKER_COOLDOWN + 1
        self.provider.script = [("ok", "back again")]
        self.assertEqual(await c.chat(MSGS), "back again")
        self.assertFalse(c.breaker_open)

    async def test_success_resets_failure_streak(self):
        c = await self.make([("status", 500)])
        for _ in range(4):
            with self.assertRaises(LLMUnavailable):
                await c.chat(MSGS)
        self.provider.script = [("ok", "fine")]
        await c.chat(MSGS)
        self.provider.script = [("status", 500)]
        for _ in range(4):
            with self.assertRaises(LLMUnavailable):
                await c.chat(MSGS)
        self.assertFalse(c.breaker_open, "4 + success + 4 failures is not 5 in a row")

    async def test_concurrency_is_bounded(self):
        c = await self.make([("slow", 0.1)], llm_max_concurrent=2)
        results = await asyncio.gather(*(c.chat(MSGS) for _ in range(6)))
        self.assertEqual(len(results), 6)
        self.assertLessEqual(self.provider.max_active, 2)


if __name__ == "__main__":
    unittest.main()
