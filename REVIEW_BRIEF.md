# Review brief: claude_openai.py

You are reviewing `claude_openai.py` — a single-file, stdlib-only HTTP server
that wraps the `claude -p` (headless) CLI to expose an OpenAI-compatible API
at `http://127.0.0.1:8910/v1`. It reuses the local `claude` CLI's existing
subscription (OAuth) auth. A live instance is RUNNING at 8910 (pre-review
code; see rules).

Already verified working by the author: non-stream completions, SSE streaming,
session continuity via `--resume` (X-Session-Id header or `model: "name:sid"`
suffix), agent mode with tools, OpenAI python SDK compatibility.

## Task 1: context continuation — continuing vs one-off
Verify and, if wrong, fix:
- Resume with `mode=chat` re-sends ONLY the last user turn (CLI keeps prior
  context via `--resume`). Resume with `mode=agent` re-sends the FULL message
  list — is double-sending the final turn a problem for resumed agent
  context? Test it: start a session, resume it, ask "what did I just say?"
  both ways, compare.
- Bogus/expired `X-Session-Id`: what does the CLI actually do (error? fresh
  session? hang?) and does the server surface it as a clean OpenAI-style
  error rather than a hang or crash? Test with a made-up session id.
- One-off requests (no session header/suffix): confirm each gets a fresh
  session and nothing leaks between them. Note whether sessions pile up in
  `~/.claude/projects` (disk growth) and whether `--no-session-persistence`
  for one-off requests is worth adding (weigh: clients may want to resume a
  one-off later — recommend a request field like `"persist": false`).
- Streaming responses only expose `session_id` in the final chunk. Confirm
  that's sound (OpenAI protocol has no header channel for it) and that the
  final chunk's fields (usage, session_id, cost) don't break strict SDK
  parsers (extra top-level fields are allowed, but check the chunk's
  `choices[].delta` shape on the final chunk is `{}`).

## Task 2: BAN / rate-limit safety — the big one
The account is a subscription with rolling 5-hour and 7-day usage windows; a
live `rate_limit_event` during testing showed the 7-day window already at
~77% utilization with overage disabled. Sustained automated traffic through
this server is a real account risk. Current gaps:
- `ThreadingHTTPServer` = unbounded concurrent requests; each spawns a full
  `claude` CLI process (its own session + system-prompt cache warmup). A
  burst of N requests = N parallel claude sessions.
- No request queue, no concurrency cap, no rate limiting of any kind.
- Default per-request budget $2 / timeout 900s: one stuck client holds a
  slot for 15 minutes.
Assess and implement:
- Max concurrent CLI processes (semaphore; env `MAX_CONCURRENT`, default 2)
  with a bounded queue and queue timeout (env-tunable), returning 503
  OpenAI-style error when the queue is full — not a hang.
- A simple per-client rate limit (env-tunable, sensible default, e.g.
  6 req/min/IP) — or at minimum document the danger explicitly.
- State in REVIEW_RESULTS.md: does `--max-budget-usd` even apply on
  subscription auth (we saw `costBasis: "list"`; check what it does), and
  give a direct, honest answer to "if used normally, will this get us banned
  or rate limited?" Distinguish (a) quota consumption (windows burn faster)
  from (b) detection/ToS risk (headless CLI use patterns). We cannot fully
  control (b) from here — say what we CAN control and set the defaults so
  normal use (a handful of requests/day, no loops) is safe.
- IMPORTANT: do not test with burst/loop traffic yourself beyond a small
  bounded concurrency test (max 3 concurrent, once). The account's rolling
  windows are already partially used; don't burn them for testing.

## Task 3: robustness
- Error paths: CLI non-zero exit, timeout mid-stream, JSON parse failure,
  unknown session id, unknown model — each must return a clean OpenAI-style
  error object (4xx/5xx with `error.message`), never crash the handler
  thread or leave a dangling subprocess. Audit every path.
- `run_claude_stream`: confirm stdin close semantics are correct for
  `claude -p` (does it wait for more input? we close after writing the
  prompt — verify empirically, it worked, but confirm no edge case).
- The module-level `lock` is defined but unused — remove or put it to work.
- `send_header` after `end_headers` on the stream path would be a bug —
  confirm the late X-Session-Id header attempt was already removed (it was,
  but double-check nothing else writes headers after end_headers).
- Anything else you find (threading, resource leaks, signal handling).

## Rules
- Fix what you find in `claude_openai.py` (keep it single-file, stdlib-only).
  New env vars get documented in the module docstring AND the README.
- Git: the repo is initialized; commit as you go (`review: <what>`).
- Test live against port 8910 where behavior validation helps, BUT it runs
  the pre-review code: if a fix requires a server restart to validate, do
  NOT restart it yourself — note it under "Requires restart (C6)" in
  REVIEW_RESULTS.md and validate the changed functions by direct
  unit-style invocation instead.
- Write `REVIEW_RESULTS.md` at the end: findings (severity-ranked), what you
  changed, tests run with observed outputs, and the direct
  banned/rate-limited answer.
- NEVER print OAuth tokens, API keys, or contents of credential files —
  not in commits, not in the results file, not in your output.
- Keep public behavior compatible (endpoints, request extras, response
  shapes) unless a bug forces a change — call out any change explicitly.
