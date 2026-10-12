#!/usr/bin/env python3
"""
claude-openai-server: expose the Claude Code CLI as an OpenAI-compatible API.

Wraps `claude -p` (headless mode) so any OpenAI-compatible client (opencode,
Continue, OpenAI python SDK, curl) can use the locally installed Claude Code —
including its agent tools — through /v1/chat/completions.

Stdlib only. Runs with the claude CLI's existing auth (OAuth subscription or
ANTHROPIC_API_KEY), so no extra credentials are needed.

Endpoints:
  GET  /health                liveness + claude CLI check
  GET  /v1/models             list available models (CLI aliases)
  POST /v1/chat/completions   OpenAI chat completions (stream + non-stream)

Session continuity:
  - Response header X-Session-Id carries the Claude Code session id.
  - Pass it back via the X-Session-Id request header (or model="name:sid")
    to continue the same session (claude --resume).

Request extras (all optional, ignored by strict clients):
  "mode": "chat" (default)  | plain model, caller's system prompt replaces
                            | Claude's default agent prompt, tools denied
          "agent"           | full Claude Code agent: default system prompt,
                            | caller system prompt appended, tools allowed
                            | (permission mode bypass)
  "timeout": seconds for the CLI run (default 900, max 3600)

Security:
  - Binds 127.0.0.1 by default.
  - Set CLAUDE_OAI_TOKEN to require "Authorization: Bearer <token>".
  - agent mode runs Claude Code with tools and bypassed permissions in
    WORKDIR — keep WORKDIR a sandbox directory and bind localhost.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8910"))
WORKDIR = os.environ.get("WORKDIR", os.path.expanduser("~/HydrocarbonForge/sandbox/claude-openai"))
TOKEN = os.environ.get("CLAUDE_OAI_TOKEN", "")
DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "900"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or (
    shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
)

# OpenAI model name -> claude CLI --model value. Aliases are resolved by the
# CLI itself; unknown names are passed through (full model names work too).
MODELS = ["sonnet", "opus", "haiku", "fable", "default"]

lock = threading.Lock()


def build_prompt(messages):
    """Build (system, prompt_text) from an OpenAI message list."""
    system_parts = []
    history = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):  # content parts: keep text only
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if content is None:
            content = ""
        if role == "system":
            system_parts.append(content)
        else:
            prefix = {"user": "", "assistant": "Assistant: ", "tool": "Tool: "}.get(role, f"{role}: ")
            history.append(f"{prefix}{content}")
    system = "\n\n".join(p for p in system_parts if p) or None
    return system, "\n\n".join(history)


def run_claude(args, prompt, timeout):
    """Run claude -p with prompt on stdin; return (exit_code, stdout, stderr)."""
    proc = subprocess.run(
        [CLAUDE_BIN, *args],
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=WORKDIR,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def run_claude_stream(args, prompt, timeout):
    """Run claude -p with stream-json output; yield (event_json_dict) per line."""
    proc = subprocess.Popen(
        [CLAUDE_BIN, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=WORKDIR,
    )
    proc.stdin.write(prompt.encode("utf-8"))
    proc.stdin.close()
    try:
        deadline = time.time() + timeout
        for raw in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                raise TimeoutError
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
    finally:
        if proc.poll() is None:
            proc.kill()


class Handler(BaseHTTPRequestHandler):
    server_version = "claude-openai-server/1.0"

    # ---------- helpers ----------
    def _send(self, code, body, headers=None, content_type="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj, headers=None):
        self._send(code, json.dumps(obj), headers=headers)

    def _error_json(self, code, message, etype="invalid_request_error"):
        self._json(code, {"error": {"message": message, "type": etype, "code": code}})

    def _auth_ok(self):
        if not TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _sse(self, chunk_id, obj, done=False):
        line = f"data: {json.dumps(obj)}\n\n"
        if done:
            line += "data: [DONE]\n\n"
        self.wfile.write(line.encode("utf-8"))
        self.wfile.flush()

    def log_message(self, fmt, *args):
        sys.stderr.write("[claude-openai] %s\n" % (fmt % args))

    # ---------- GET ----------
    def do_GET(self):
        if not self._auth_ok():
            return self._error_json(401, "unauthorized")
        if self.path == "/health":
            return self._json(200, {
                "status": "ok",
                "claude_bin": CLAUDE_BIN,
                "claude_found": os.path.exists(CLAUDE_BIN),
                "workdir": WORKDIR,
                "token_required": bool(TOKEN),
            })
        if self.path in ("/v1/models", "/models"):
            now = int(time.time())
            data = [{"id": m, "object": "model", "created": now, "owned_by": "claude-code"}
                    for m in MODELS]
            return self._json(200, {"object": "list", "data": data})
        self._error_json(404, "not found")

    # ---------- POST ----------
    def do_POST(self):
        if not self._auth_ok():
            return self._error_json(401, "unauthorized")
        if self.path.rstrip("/") in ("/v1/chat/completions", "/chat/completions"):
            return self._chat_completions()
        self._error_json(404, "not found")

    def _chat_completions(self):
        try:
            req = self._read_body()
        except json.JSONDecodeError:
            return self._error_json(400, "invalid JSON body")
        messages = req.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._error_json(400, "messages must be a non-empty list")

        model = req.get("model", "default")
        # optional session via model suffix: "sonnet:abc123"
        session_id = self.headers.get("X-Session-Id", "")
        if isinstance(model, str) and ":" in model:
            model, suffix = model.split(":", 1)
            if suffix and not session_id:
                session_id = suffix

        mode = req.get("mode", "chat")
        if mode not in ("chat", "agent"):
            return self._error_json(400, "mode must be 'chat' or 'agent'")
        stream = bool(req.get("stream"))
        timeout = min(int(req.get("timeout", DEFAULT_TIMEOUT)), 3600)

        system, prompt = build_prompt(messages)
        # On resume, the CLI keeps prior context; resend only the new user turn.
        if session_id and mode == "chat":
            last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
            if last_user is not None:
                content = last_user.get("content")
                if isinstance(content, list):
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                prompt = content or ""

        args = ["-p", "--output-format", "json" if not stream else "stream-json"]
        if stream:
            # stream-json in --print mode requires --verbose
            args += ["--include-partial-messages", "--verbose"]
        if session_id:
            args += ["--resume", session_id]
        if model != "default":
            args += ["--model", model]
        if system:
            args += ["--append-system-prompt" if mode == "agent" else "--system-prompt", system]
        if mode == "agent":
            args += ["--permission-mode", "bypassPermissions"]
        else:
            args += ["--permission-mode", "dontAsk"]
        # keep runs focused and cheap by default
        args += ["--max-budget-usd", str(req.get("max_budget_usd", 2.0))]

        base = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion" if not stream else "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
        }

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            finished = False
            stream_sid = ""
            try:
                for ev in run_claude_stream(args, prompt, timeout):
                    etype = ev.get("type")
                    if etype == "system" and ev.get("subtype") == "init":
                        stream_sid = ev.get("session_id", stream_sid)
                    elif etype == "stream_event":
                        inner = ev.get("event", {})
                        if inner.get("type") == "content_block_delta":
                            delta = inner.get("delta", {})
                            if delta.get("type") == "text_delta":
                                self._sse(base["id"], {**base, "choices": [{
                                    "index": 0, "delta": {"content": delta.get("text", "")},
                                    "finish_reason": None,
                                }]})
                    elif etype == "result":
                        if finished:
                            continue
                        finished = True
                        sid = ev.get("session_id") or stream_sid
                        choice = {"index": 0, "delta": {}, "finish_reason": "stop" if ev.get("subtype") == "success" else "error"}
                        chunk = {**base, "choices": [choice]}
                        if ev.get("usage"):
                            u = ev["usage"]
                            chunk["usage"] = {
                                "prompt_tokens": u.get("input_tokens", 0),
                                "completion_tokens": u.get("output_tokens", 0),
                                "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0),
                            }
                        if sid:
                            chunk["session_id"] = sid
                        if ev.get("total_cost_usd") is not None:
                            chunk["cost_usd"] = ev["total_cost_usd"]
                        self._sse(base["id"], chunk, done=True)
                        if sid:
                            sys.stderr.write(f"[claude-openai] session {sid} (stream)\n")
            except (TimeoutError, subprocess.TimeoutExpired):
                self._error_json(504, f"claude CLI timed out after {timeout}s", "timeout")
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[claude-openai] stream error: {e}\n")
            if not finished:
                # close stream gracefully if the CLI died mid-stream
                self._sse(base["id"], {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}]}, done=True)
            return

        # ---- non-streaming ----
        try:
            code, out, err = run_claude(args, prompt, timeout)
        except subprocess.TimeoutExpired:
            return self._error_json(504, f"claude CLI timed out after {timeout}s", "timeout")
        except FileNotFoundError:
            return self._error_json(500, f"claude CLI not found at {CLAUDE_BIN}")
        data = {}
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            if code != 0:
                return self._error_json(502, f"claude CLI failed (exit {code}): {err.strip()[:2000]}", "upstream_error")
            return self._error_json(502, f"unparseable claude output: {out[:1000]}")

        sid = data.get("session_id", "")
        headers = {"X-Session-Id": sid} if sid else None
        if data.get("is_error"):
            return self._json(502, {"error": {"message": data.get("result", "claude error"), "type": "upstream_error"}}, headers)

        resp = {**base, "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": data.get("result", "")},
            "finish_reason": "stop",
        }]}
        u = data.get("usage") or {}
        if u:
            resp["usage"] = {
                "prompt_tokens": u.get("input_tokens", 0),
                "completion_tokens": u.get("output_tokens", 0),
                "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0),
            }
        if data.get("total_cost_usd") is not None:
            resp["cost_usd"] = data["total_cost_usd"]
        if sid:
            resp["session_id"] = sid
        self._json(200, resp, headers)


def main():
    os.makedirs(WORKDIR, exist_ok=True)
    if not os.path.exists(CLAUDE_BIN):
        print(f"WARNING: claude CLI not found at {CLAUDE_BIN}", file=sys.stderr)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"claude-openai-server on http://{HOST}:{PORT} (workdir={WORKDIR}, token={'on' if TOKEN else 'off'})", file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    main()
