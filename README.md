<h1 align="center">claude-openai-server</h1>

<p align="center"><b>Turns the local Claude Code CLI into an OpenAI-compatible server.</b></p>

<p align="center">
Any OpenAI-compatible client can call Claude Code, including its agent tools, through <code>/v1/chat/completions</code>.
</p>

<p align="center">
<img alt="language: Python" src="https://img.shields.io/badge/language-Python-3776AB">
<a href="LICENSE"><img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

<p align="center">
<a href="#what-it-is">What it is</a> ·
<a href="#endpoints">Endpoints</a> ·
<a href="#quick-start">Quick start</a> ·
<a href="#configuration">Configuration</a> ·
<a href="#how-it-works">How it works</a> ·
<a href="#license">License</a>
</p>

---

## What it is

`claude_openai.py` is a single-file HTTP server that wraps the Claude Code CLI in headless mode (`claude -p`). Clients such as the OpenAI Python SDK, opencode, Continue, or curl send standard OpenAI chat requests, and the server runs them through the local `claude` CLI.

- **Stdlib only.** Python 3 with no third-party dependencies.
- **Uses the CLI's existing auth.** Works with the OAuth login from `claude` or with `ANTHROPIC_API_KEY`. No extra credentials are needed.
- **Two modes.** `chat` gives a plain completion. `agent` gives the full Claude Code agent with its tools enabled.
- **One CLI process per request.** Suited to interactive and agent use, not high-throughput traffic.

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Liveness and CLI check. Returns `status`, `claude_bin`, `claude_found`, `workdir`, `token_required`. |
| `GET /v1/models` | Lists the models `sonnet`, `opus`, `haiku`, `fable`, and `default`. |
| `POST /v1/chat/completions` | OpenAI chat completions, streaming (SSE) and non-streaming. |

The server also accepts `/models` and `/chat/completions` without the `/v1` prefix.

### Request extras

All are optional. Strict OpenAI clients can pass them with `extra_body`.

| Field | Default | Meaning |
|---|---|---|
| `mode` | `chat` | `chat`: the caller's system prompt replaces Claude's default system prompt, and permission mode is `dontAsk`. `agent`: the default Claude Code system prompt is kept, the caller's system prompt is appended, and permission mode is `bypassPermissions`. |
| `timeout` | `900` | Seconds allowed for one CLI run. Capped at 3600. |
| `max_budget_usd` | `2.0` | Passed to the CLI as `--max-budget-usd` for each run. |

### Session continuity

- Non-streaming responses include `session_id` in the body and an `X-Session-Id` response header.
- Streaming responses include `session_id` in the final chunk. The header is not sent on streams.
- To resume, send the `X-Session-Id` request header, or append the id to the model name: `"model": "sonnet:<session-id>"`. The header takes precedence over the suffix.
- When resuming with `mode: "chat"`, only the last user message is sent, because the CLI keeps prior context.

## Quick start

Requirements: Python 3 and the Claude Code CLI (`claude`), signed in with `claude` or with `ANTHROPIC_API_KEY` set.

```bash
git clone https://github.com/Hydrocarbon-Forge/claude-openai-server.git
cd claude-openai-server
python3 claude_openai.py
```

The server starts on `http://127.0.0.1:8910`. Check it, then send a request:

```bash
curl http://127.0.0.1:8910/health

curl http://127.0.0.1:8910/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"haiku","messages":[{"role":"user","content":"hi"}]}'
```

Use it from the OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(api_key="unused", base_url="http://127.0.0.1:8910/v1")
r = client.chat.completions.create(
    model="haiku",
    messages=[{"role": "user", "content": "hi"}],
    extra_body={"timeout": 120},
)
print(r.choices[0].message.content)
```

If `CLAUDE_OAI_TOKEN` is set, send it as the API key, for example `api_key="<your token>"`. The server then requires `Authorization: Bearer <token>`.

## Configuration

Environment variables read by `claude_openai.py`:

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | Address to bind. |
| `PORT` | `8910` | Port to listen on. |
| `WORKDIR` | `~/HydrocarbonForge/sandbox/claude-openai` | Working directory for CLI runs. The directory is created if missing. |
| `CLAUDE_OAI_TOKEN` | unset (no auth) | If set, all endpoints require `Authorization: Bearer <token>`. |
| `CLAUDE_BIN` | `claude` from `PATH`, else `~/.local/bin/claude` | Path to the Claude Code CLI. |
| `DEFAULT_TIMEOUT` | `900` | Default per-request timeout in seconds. |

**Security.** The server binds to localhost by default. `agent` mode runs Claude Code with tools and bypassed permissions inside `WORKDIR`, so keep `WORKDIR` a sandbox directory and keep the server on localhost unless you set `CLAUDE_OAI_TOKEN`.

## How it works

| File | Role |
|---|---|
| `claude_openai.py` | The server. Parses OpenAI requests, builds the `claude -p` command, and converts the CLI's JSON or stream-json output back into OpenAI responses. |
| `start.sh` | Starts the vendored `claude-code-openai-wrapper` with uvicorn. This is a different server from `claude_openai.py`. |
| `SETUP.md` | Deployment notes for the vendored wrapper, including its auth and rate-limit settings. |
| `vendor/claude-code-openai-wrapper/` | Git submodule pinned to commit `aaad0b96a4cd63833003ac340bbcc37f89418acc`. |

## License

Released under the MIT License. See [LICENSE](LICENSE): "Copyright (c) 2026 Hydrocarbon Forge".
