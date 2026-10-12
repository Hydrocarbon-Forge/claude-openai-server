# claude-openai-server — deployment

Live service on `http://192.168.1.201:8910` (LAN; also `http://127.0.0.1:8910` from the host) is **RichardAtCT/claude-code-openai-wrapper**
(chosen over our v1 on 2026-10-12 because it is more complete: built-in rate
limiting, session store, tool manager, MCP, OpenAPI docs, subscription-CLI auth
out of the box).

Our own `claude_openai.py` (v1) is kept in this repo as a dependency-free
fallback / reference and is NOT running.

## What is live

| Item | Value |
|---|---|
| Upstream | `github.com/RichardAtCT/claude-code-openai-wrapper` |
| Pinned commit | `aaad0b96a4cd63833003ac340bbcc37f89418acc` (clone in `vendor/claude-code-openai-wrapper`) |
| Install | `python3 -m venv .venv && .venv/bin/pip install -e .` (poetry deps) |
| Run | `./start.sh` (env-driven: `.env` host 0.0.0.0, port 8910) |
| Claude auth | `claude_cli` — reuses the local `claude` CLI's subscription OAuth (`~/.claude/.credentials.json`). No extra API key needed. |
| Bind | `0.0.0.0:8910` (LAN-reachable since 2026-10-12; host LAN IP `192.168.1.201`). Bearer-token required on `/v1/*` — the token is the only credential. ufw inactive; private LAN only. |
| Request auth | Bearer token, env `API_KEY` (in `vendor/claude-code-openai-wrapper/.env`, chmod 600). No key set = open (we set one). |
| Rate limit | Built-in, enabled. chat `10/minute`, session `15/minute`, health `30/minute` (per IP). Tunable via `RATE_LIMIT_*_PER_MINUTE` env vars. |

## Config (in `vendor/claude-code-openai-wrapper/.env`)

```
CLAUDE_AUTH_METHOD=cli            # use local CLI subscription auth
CLAUDE_WRAPPER_HOST=0.0.0.0      # LAN-reachable (bearer-gated)
PORT=8910
API_KEY=<48 hex chars, secret>    # required Bearer token for /v1/*
RATE_LIMIT_ENABLED=true
```

Regenerate the key: `python3 -c "import secrets;print(secrets.token_hex(24))"`.
NEVER commit or paste the key.

## Verified behavior (2026-10-12, live on 8910)

- `GET /health` → `{"status":"healthy"}` (no auth).
- One-off completion → correct answer + `usage` tokens. (e.g. "pong", usage 10/1/11)
- Streaming → proper SSE chunks (`delta.content`, final `finish_reason:"stop"`, `data: [DONE]`).
- Session continuity: client supplies `session_id` in the request body; a second
  request with the same id carries prior context (verified: remembered "BANANA";
  prompt_tokens grew across turns, proving history re-sent). One-off = omit
  `session_id`.
- Rate limit: firing >10/min returns `429` with
  `{"error":{"type":"rate_limit_exceeded","code":"too_many_requests","retry_after":60}}`
  + `Retry-After: 60` header.
- Auth: request without Bearer → `401 {"error":{"code":"401","message":"Missing API key"}}`;
  with key → `200`.
- Model `haiku`/`sonnet`/`opus`/`fable` are passed through to the CLI (the wrapper
  logs a benign "not in known list" warning for short aliases but still attempts them).

## Client configs

OpenAI python SDK:
```python
from openai import OpenAI
client = OpenAI(api_key="<API_KEY from .env>", base_url="http://192.168.1.201:8910/v1")
r = client.chat.completions.create(model="haiku",
    messages=[{"role":"user","content":"hi"}],
    extra_body={"session_id": "my-conv"})   # optional continuity
```
curl:
```bash
curl http://192.168.1.201:8910/v1/chat/completions \
  -H "Authorization: Bearer <API_KEY>" -H "Content-Type: application/json" \
  -d '{"model":"haiku","session_id":"my-conv","messages":[{"role":"user","content":"hi"}]}'
```

## Ban / rate-limit safety — honest read

The wrapper's limiter caps **request rate** (10/min per IP) — good guardrail
against accidental bursts. It does NOT cap how much of the account's rolling
5-hour / 7-day subscription quota is consumed, and it cannot change the ToS
status of headless/SDK use on a subscription. So:

- Safe for: interactive agent work, modest request volumes, one-off + resumed
  conversations.
- Risky for: unattended high-frequency loops, parallel fan-out, background
  daemons hammering it all day — that burns the rolling windows fast and the
  sustained-automation pattern is the thing Anthropic flags for subscriptions.
- Guidance: keep it interactive / low-volume; watch the account usage windows;
  don't wire it into a tight automated loop without throttling.

## Fallback

If the wrapper ever breaks / gets rate-limited at the harness level, our
dependency-free `claude_openai.py` (port 8910-usable, same OpenAI surface,
CLI subscription auth) can be run instead:
```bash
python3 claude_openai.py   # env: HOST, PORT(8910), WORKDIR, CLAUDE_OAI_TOKEN
```
