# ✒️ Violet — an Auto Memory Doll for your Discord server

Violet is a calm, precise, quietly curious Discord bot who helps your community with **gaming, PC hardware,
mobile tech and entertainment** — and answers in a refined, letter-writing voice, with polished embeds and buttons.

Built with `discord.py` 2.x. Lightweight (no database), asynchronous, and designed to stay up on free hosting.

## Features

| | |
|---|---|
| 💬 **Ask anywhere** | `/ask` — or just `@Violet your question` (also works as a reply to her, and in DMs) |
| 🩺 **Troubleshoot** | `/troubleshoot` — diagnosis, likely causes, steps from gentlest to most decisive, risk notes |
| ⚖️ **Compare** | `/compare` — two games / devices / parts / shows side by side with a verdict |
| 💌 **Recommend** | `/recommend` — up to 5 thoughtful picks for games, films, anime, gear, music |
| ✒️ **Letters** | `/letter` — Violet ghostwrites a heartfelt message for you |
| 🪶 **Buttons** | Under every answer: Regenerate · Simpler · Dismiss |
| 🕯️ **Housekeeping** | `/help` (interactive menu), `/status`, `/ping`, `/about`, `/forget` |

Every answer is rendered as a structured embed (topic colour and emoji, key points, steps, "worth verifying"
notes and a certainty indicator) — and always falls back gracefully to plain text if the model ignores the format.

## Quick start

1. **Create the bot** — [Discord Developer Portal](https://discord.com/developers/applications) → *New Application*
   → name it **Violet** → *Bot* → *Reset Token* (copy it). Use a **new** application, not another bot's token.
   No privileged intents are required — leave them all off.
2. **Pick a language service** — any OpenAI-compatible API works (see `.env.example` for base URLs). You need
   an API key, the base URL and a model name from your provider's dashboard.
3. **Invite her** (replace `YOUR_CLIENT_ID` with the Application ID):

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot+applications.commands&permissions=274877991936
   ```
   (View Channels, Send Messages, Send Messages in Threads, Embed Links, Read Message History.)
4. **Configure** — copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`, `LLM_API_KEY`, `LLM_BASE_URL`,
   `LLM_MODEL` (and `GUILD_ID` for instant slash commands).
5. **Run** (Python 3.10+, 3.12 recommended):

   ```bash
   python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   python main.py
   ```
   You should see `✒️ Violet is online as …`.

## Hosting on Wispbyte (free)

1. Create a **Python** server. In *Startup*, choose Python **3.11 or newer** and set the main file to `main.py`.
2. Upload `main.py`, `requirements.txt` and the whole `violet/` folder (the `tests/` folder is optional).
3. Create a file named `.env` in the server's root with your values (no quotes).
4. Make sure the requirements get installed: if the Startup page has a requirements-file field, keep it as
   `requirements.txt`; if it has a *Packages* field, enter `discord.py aiohttp python-dotenv`.
5. Press **Start**. In the console you should see `Synced … slash command(s)` (first run only) and
   `Violet is online`.

Memory use is typically well under 100 MB, comfortably inside a 512 MB plan. Remember Wispbyte's free servers
need a login about once a month to stay active.

## Configuration

All settings are environment variables (see `.env.example` for the full, commented list).

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | — | **Required.** Bot token. |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — | Language service. Without them Violet starts but replies that she is not configured. |
| `GUILD_ID` | empty | Sync slash commands to one server instantly (empty = global). |
| `ALLOWED_CHANNEL_IDS` | empty | Limit where she answers mentions. |
| `USER_COOLDOWN_SECONDS` | 8 | Pause between one user's requests. |
| `USER_HOURLY_LIMIT` | 30 | Requests per user per hour. |
| `DAILY_REQUEST_LIMIT` | 600 | Whole-bot daily budget — protects free API quotas. |
| `MEMORY_TURNS` / `MEMORY_TTL_SECONDS` | 6 / 900 | Short in-RAM conversation memory (`0` disables). |
| `SYNC_COMMANDS` | auto | `auto` syncs only when commands changed. |

## How it stays stable

* **One shared HTTP session**, bounded concurrency (`LLM_MAX_CONCURRENT`), timeouts on every request.
* **Retries with backoff** for 429/5xx/timeouts (honouring `Retry-After`), and a **circuit breaker** that rests
  for 60 s after 5 failed requests in a row instead of hammering a struggling provider.
* **Fair-use guard**: per-user cooldown, hourly cap and a global daily budget; failed requests are refunded.
* **Nothing can crash her**: every failure becomes a gentle in-character message; listeners and views catch
  their own errors; one broken cog never stops the others from loading.
* **Safe output**: model text can never ping `@everyone`/`@here`/roles; `<think>` blocks are stripped;
  all embeds are clamped to Discord's size limits.
* **Startup resilience**: if Discord or the network is unreachable, she retries with growing delays; Stop
  (SIGTERM) shuts her down cleanly.
* **Commands sync only when they change** (hash check), so restart loops can never hit Discord's sync limits.
* **Lean footprint**: no privileged intents, no member cache, no message cache.

## Make her yours

* **Personality & wording** — `violet/persona.py` (system prompt, status lines, error messages).
* **Colours & topics** — `violet/theme.py` (`Palette`, `TOPICS`).
* **Embeds** — `violet/renderers.py`.

## Project layout

```
main.py                 entry point (resilient start, clean shutdown)
violet/
  bot.py                bot class, safe command sync, global error handler
  config.py             environment settings
  persona.py            Violet's voice and prompts
  theme.py              palette, topics, embed helpers
  llm.py                OpenAI-compatible client (retries, breaker)
  limits.py             cooldown / hourly / daily guard
  memory.py             short RAM-only conversation memory
  responder.py          guard → memory → model → embed pipeline
  renderers.py          JSON → polished embeds (defensive)
  views.py              Regenerate / Simpler / Dismiss buttons
  cogs/                 ask · tools · general · mentions
tests/                  72 unit tests (python -m unittest discover -s tests -t .)
```

## Troubleshooting

* **Slash commands don't appear** — set `GUILD_ID`, restart, then restart your Discord app (Ctrl+R). Check the
  invite included `applications.commands`.
* **"The language service is not configured"** — set all three `LLM_*` variables.
* **"I could not be admitted…"** — the provider rejected the key (HTTP 401/403): check `LLM_API_KEY`.
* **She ignores my mention** — ping her with `@` (autocomplete the name), or enable `ENABLE_MESSAGE_CONTENT`
  (and the matching intent in the Developer Portal) so she can see un-pinged replies.
* **Old buttons stopped working after a restart** — expected: buttons expire after 10 minutes or on restart.
  Ask again.
* **Answers feel wrong or dated** — models can be mistaken about specs, prices and recent releases; Violet is
  instructed to say so, but always verify purchases and risky repairs with official sources.
