<p align="center"><b>Turns the local Claude Code CLI into an OpenAI-compatible server.</b></p>

<p align="center">
<a href="LICENSE"><img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

---

# claude-openai-server

Turns the locally installed **Claude Code CLI** into a tiny **OpenAI-compatible
server**. Any OpenAI-compatible client (OpenAI python SDK, opencode, Continue,
curl) can use Claude Code — including its agent tools — through standard
`/v1/chat/completions`.

Stdlib-only Python (3.10+), no dependencies. Works with the CLI's existing
auth (OAuth subscription from `claude` login, or `ANTHROPIC_API_KEY`). No
extra credentials needed.

## Why

- Existing off-the-shelf wrappers (RichardAtCT/claude-code-openai-wrapper,
  betthington/claude-code-api, cabinlab/claude-code-api) either require a
  raw `ANTHROPIC_API_KEY`, or assume CLI-auth flows we don't match, or pull
  in heavy deps. This one is one file, runs headless, and reuses the
  `claude` CLI's own credentials — so it works on a Max subscription.
- `claude -p` (print mode) is the whole trick: headless, JSON/stream output,
  `--resume` for session continuity, `--model` aliases.

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness + CLI check |
| `GET /v1/models` | Model list (`sonnet`, `opus`, `haiku`, `fable`, `default`) |
| `POST /v1/chat/completions` | OpenAI chat completions, streaming (SSE) + non-streaming |

### Request extras (all optional)

| Field | Default | Meaning |
|---|---|---|
| `mode` | `chat` | `chat` = plain completion (caller's system prompt *replaces* Claude's agent prompt, tools denied). `agent` = full Claude Code agent (default system prompt kept, caller system prompt appended, tools enabled with `bypassPermissions`). |
| `timeout` | `900` | Seconds allowed for the CLI run (max 3600). |
| `max_budget_usd` | `2.0` | Per-run spend cap. |

### Session continuity

- Non-streaming response includes `session_id` (and `X-Session-Id` header).
- Streaming: the final chunk includes `session_id`.
- Resume by sending the `X-Session-Id` request header, or as a model suffix:
  `"model": "sonnet:<session-id>"`. On resume with `mode=chat`, only the
  last user turn is re-sent (the CLI keeps prior context).

## Run

```bash
python3 claude_openai.py
# env: HOST (127.0.0.1), PORT (8910), WORKDIR (~/HydrocarbonForge/sandbox/claude-openai),
#      CLAUDE_OAI_TOKEN (optional bearer token), CLAUDE_BIN, DEFAULT_TIMEOUT
```

Binds localhost only by default. `agent` mode runs Claude Code with tools and
bypassed permissions in `WORKDIR` — keep `WORKDIR` a sandbox dir.

## Client configs

OpenAI python SDK:

```python
from openai import OpenAI
client = OpenAI(api_key="***", base_url="http://127.0.0.1:8910/v1")
r = client.chat.completions.create(
    model="haiku",
    messages=[{"role": "user", "content": "hi"}],
    extra_body={"timeout": 120},  # optional extras via extra_body
)
```

opencode (`opencode.json`):

```json
{
  "provider": {
    "claude-code": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://127.0.0.1:8910/v1" },
      "models": {
        "haiku": { "name": "Claude Code (haiku)" },
        "sonnet": { "name": "Claude Code (sonnet)" },
        "opus": { "name": "Claude Code (opus)" }
      }
    }
  }
}
```

curl:

```bash
curl http://127.0.0.1:8910/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"haiku","messages":[{"role":"user","content":"hi"}]}'
```

## Verified (2026-10-12)

- `/health`, `/v1/models` OK.
- Non-streaming completion via real `openai` python SDK: correct answer,
  usage tokens, `cost_usd`.
- Streaming via real SDK: text deltas + final chunk with usage/cost/session.
- Session continuity: resumed session remembered earlier context.
- `mode=agent`: agent actually used the Write tool and created a file.
- Pitfall found: `claude -p --output-format stream-json` requires
  `--verbose` (already handled in the server).

## Notes

- One CLI process per request (no worker pool). Fine for interactive/agent
  use; not a high-throughput proxy.
- Streaming uses `--include-partial-messages`; text deltas are forwarded as
  OpenAI chunks. Tool-use turns stream as well (each turn's text blocks).
- `default` model = the CLI's configured default (`~/.claude/settings.json`).
- Cost tracking comes from the CLI's `total_cost_usd` (subscription usage,
  listed cost basis).
