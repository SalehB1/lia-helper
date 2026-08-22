"""Tunable constants shared by the API, retrieval and chat layers."""

from __future__ import annotations

# Rate limits (slowapi syntax; the ";" form is one limit per clause, both enforced)
# The daily halves are spend ceilings, not abuse ceilings, and they are keyed per USER
# (`api.user_rate_limit_key`), not per IP. Sustained 20 turns/minute against gpt-5-mini is
# roughly $34/day of tokens, and an order of magnitude more if the user picks a frontier
# model — with self-registration open, nothing else in the app bounds that.
RATE_LIMIT_CHAT = "20/minute;200/day"
RATE_LIMIT_TOOLS = "30/minute;60/day"
RATE_LIMIT_DEFAULT = "100/minute"
# Sign-in and password changes. Tighter than the rest because every attempt costs ~16 MiB
# and ~100 ms of threadpool time by design — this is a resource limit as much as it is a
# credential-stuffing limit.
RATE_LIMIT_LOGIN = "10/minute"
# Registration is deliberately far tighter than login. Every accepted request adds a row a
# human then has to review, so the limit bounds the approval queue, not just the CPU: at
# login's 10/minute one address could park ~14,000 pending accounts in a day.
RATE_LIMIT_REGISTER = "3/hour"

# Input clamps
MAX_MESSAGE_CHARS = 4000
MAX_LOG_CHARS = 6000
MAX_TITLE_CHARS = 120

# Conversation limits
HISTORY_WINDOW = 10
MAX_CONVERSATIONS_PER_USER = 100

# Upper bound on the sibling versions of one message a client may address by index.
# Nothing creates this many in practice; it bounds an attacker-supplied integer.
MAX_VERSIONS = 100

# Agent loop and retrieval
# Tool rounds are per MODEL ATTEMPT, not per turn: when a model runs out of rounds the
# turn escalates to the next rung of the ladder instead of hitting a wall. These are the
# defaults behind AGENT_TOOL_ROUNDS / AGENT_RETRY_TOOL_ROUNDS; read them through
# `agent_settings()`, never directly, so the admin overrides apply.
AGENT_TOOL_ROUNDS = 3
# Escalated attempts get fewer rounds: the docs the first model fetched are already in
# `messages`, so a retry usually needs one closing call and nothing else.
AGENT_RETRY_TOOL_ROUNDS = 1
AGENT_MAX_MODEL_ATTEMPTS = 3
AGENT_TURN_BUDGET_SECONDS = 120.0
AGENT_MAX_LLM_CALLS = 8
RETRIEVAL_TOP_K = 8
# Extra chunks fused in from the user's own wording alongside the model's rewritten query.
# The model writes documentation-style queries that can rank the right page out of the top
# k entirely; the raw question is a second, independent shot at it.
RAW_FUSE_K = 4
RETRIEVAL_CANDIDATES = 50
RRF_K = 60
# Ceiling on the corpus section map put in the system prompt (~5k chars for 1142 pages).
DOCS_MAP_MAX_CHARS = 8000

# Caches
EMBED_CACHE_SIZE = 512
ANSWER_CACHE_SIZE = 128
# A cached answer is already keyed by the corpus, the model and the exact system prompt, so
# the TTL only bounds staleness from what is NOT in the key — a retuned retrieval setting,
# a new docs page the corpus hash has not seen yet — and a day is short enough for that.
ANSWER_CACHE_TTL_SECONDS = 86400

# Text budgets and streaming
SNIPPET_CHARS = 600
PAGE_CHARS = 8000
SSE_PING_SECONDS = 15

# Detached chat turns
# A turn runs on its own task, not on the request's, so a client that hangs up no longer
# kills the answer. These bound what that costs the process.
#
# The pool is the binding constraint, not memory: `create_async_engine` takes the default
# AsyncAdaptedQueuePool — 5 connections plus 10 overflow, so 15 — and a run holds its
# session from the history read until `_persist` commits, because nothing commits in
# between. Eight runs therefore pin at most eight of the fifteen, leaving room for the
# request sessions and for `db_healthy()`, which the platform probes every 30s and whose
# failure restarts the container and loses every in-flight answer at once.
CHAT_MAX_LIVE_RUNS = 8
#: One live turn per tab, plus one that a refresh has orphaned but not yet finished.
CHAT_MAX_LIVE_RUNS_PER_ACCOUNT = 2
#: Open SSE sockets one run may fan out to. The real ceiling is per account: 2 x 3 = 6.
CHAT_MAX_FOLLOWERS_PER_RUN = 3
#: How long a finished run stays followable. A grace window for a reload that straddles the
#: commit, NOT a cache — the persisted message row is the artifact, and the panel falls back
#: to reading it whenever the run is gone.
CHAT_RUN_RETAIN_SECONDS = 120.0
#: Ceiling on the shutdown drain. Must stay under the worker's graceful timeout, or gunicorn
#: SIGKILLs the process mid-write and the drain was pointless.
CHAT_RUN_DRAIN_SECONDS = 8.0

# Conversation titles
#: The title call is a six-word answer with a human waiting on nothing — it must never
#: outlive the turn it names.
TITLE_TIMEOUT_SECONDS = 8.0
#: How much of the question the title model reads. A title is a summary of the ask, and the
#: first few lines carry it; sending 4000 characters would bill for context nobody reads.
TITLE_QUESTION_CHARS = 500

# Upstream timeouts (seconds)
LLM_TIMEOUT_SECONDS = 60.0
# Per-attempt ceiling inside a turn. Lower than LLM_TIMEOUT_SECONDS on purpose: a turn now
# spends its budget across several model attempts, so one stalled provider must not eat it.
LLM_ATTEMPT_TIMEOUT_SECONDS = 45.0
# Sized from the measured distribution, not guessed: over 150 successful calls (idle, under
# concurrent load, and 100 back to back) p50 was 333 ms, p99 881 ms and the worst 1263 ms.
# The old 20 s ceiling was ~16x the worst real call, so it only ever fired on a connection
# that was already dead — and charged the user 20 s of dead wait before silently dropping
# that query to BM25. Speculative retrieval puts this call ahead of the first model call on
# every turn, so its ceiling is now the floor of a bad turn's time-to-first-token.
EMBED_TIMEOUT_SECONDS = 3.0
# One immediate retry: the observed failures are transient (a fresh attempt succeeds), and
# without it a blip costs the turn its dense ranking with only a log line to say so. Two
# attempts at 3 s still cap the tail below a third of the old single attempt.
EMBED_RETRY_ATTEMPTS = 2

# Marker introducing the follow-up suggestions line of an answer
SUGGESTION_MARKER = "@@@"

# Answer prefix held back before anything reaches the client, so an attempt that blames a
# limit can still be thrown away and retried on another model with nothing streamed.
HEAD_HOLD_CHARS = 240

# Crash insurance for the answer in flight. Past the commit point the turn writes its
# assistant row and then rewrites it as text is released, so a SIGKILL, an OOM or a redeploy
# that outruns CHAT_RUN_DRAIN_SECONDS loses the last window instead of the whole answer.
#
# Sized from numbers already in this file, not guessed. HEAD_HOLD_CHARS is exactly the amount
# of answer this design already treats as not-yet-the-user's — text an escalation may still
# throw away — so accepting the same amount as a crash loss is the same bet twice. Measured
# streaming is ~100-150 chars/s, so 240 released characters is ~1.8s and the seconds floor
# does not bind on a normal answer; it only bites on a fast provider, where it stops a turn
# committing several times a second. Eight live runs x 1/s is a rate the single writer can
# take; unthrottled it is not.
CHAT_CHECKPOINT_CHARS = HEAD_HOLD_CHARS
CHAT_CHECKPOINT_SECONDS = 1.0

# Hard ceiling on a request body, rejected before it is read. The largest legitimate
# payload is a 6000-char log (~12 KB of UTF-8), so this leaves an order of magnitude.
MAX_BODY_BYTES = 256 * 1024
