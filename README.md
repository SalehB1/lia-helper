# Liara Docs Assistant

A Persian, right-to-left assistant over the official [Liara](https://liara.ir) cloud
documentation. It answers deployment, configuration and troubleshooting questions **with a
citation behind every claim**, and ships two standalone wizards: a `liara.json` generator and
a build-log diagnoser.

> **Persian version of this document:** [`docs/README.fa.md`](docs/README.fa.md) ·
> **Architecture page (Persian, open in a browser):** [`docs/architecture.fa.html`](docs/architecture.fa.html)

---

## Table of contents

- [Run it locally](#run-it-locally) — start here
- [What problem it solves](#what-problem-it-solves)
- [Architecture](#architecture)
- [The panel — features and architecture](#the-panel--features-and-architecture)
- [Why these decisions](#why-these-decisions)
- [Citations and abstention are enforced in code](#citations-and-abstention-are-enforced-in-code)
- [Retrieval quality — measured](#retrieval-quality--measured)
- [Containing prompt injection from untrusted docs](#containing-prompt-injection-from-untrusted-docs)
- [Accounts, roles and registration](#accounts-roles-and-registration)
- [Other security decisions](#other-security-decisions)
- [Environment variables](#environment-variables)
- [Tests](#tests)
- [Rebuilding the corpus (ingest)](#rebuilding-the-corpus-ingest)
- [Deploying to Liara](#deploying-to-liara)
- [Operator-controlled model selection](#operator-controlled-model-selection)
- [The answer is not tied to the client connection](#the-answer-is-not-tied-to-the-client-connection)
- [Conversations name themselves](#conversations-name-themselves)
- [Handing a stuck user to a human](#handing-a-stuck-user-to-a-human)
- [Cost control](#cost-control)
- [Code map](#code-map)
- [What we would build next](#what-we-would-build-next)

---

## Run it locally

Prerequisites: **Python 3.12** and **Node 20+**. Nothing else — no Docker, no database
server, no vector database. The retrieval corpus (3,502 chunks + their embedding matrix) is
committed to this repository, so there is no ingest step to run before the app works.

```bash
git clone https://github.com/SalehB1/lia-helper.git
cd lia-helper
```

### 1. Backend

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

cd backend
cp .env.example .env
```

Now open `backend/.env` and set **three** values:

| Variable | What to put there |
|---|---|
| `AUTH_SECRET` | any random 32+ char string — `openssl rand -hex 32` |
| `BOOTSTRAP_ADMIN_PASSWORD` | your admin password, **at least 12 characters** |
| `AVALAI_API_KEY` | an [AvalAI](https://avalai.ir) key — see the note below |

Then start it:

```bash
../.venv/bin/python run-server.py --reload
```

The startup banner tells you the state of everything the app depends on:

```
==============================================================
  Liara Docs Assistant API — DEVELOPMENT (auto-reload)
==============================================================
  REST      → http://0.0.0.0:8000
  Swagger   → http://localhost:8000/docs
  Health    → http://localhost:8000/healthz
--------------------------------------------------------------
  corpus    : OK
  retrieval : hybrid — BM25 + text-embedding-3-small (3502 vectors)
  chat model: gpt-5-mini
  CORS      : http://localhost:3000
  database  : .../backend/storage/app.db
--------------------------------------------------------------
```

Health check: `curl http://127.0.0.1:8000/healthz` →

```json
{"status":"ok","chunks":3502,"embeddings":true,"db":true,"disk":true,
 "ingestedAt":"2026-08-21T18:13:14+00:00","corpusCommit":"dbb7430…","liveRuns":0}
```

### 2. Panel (second terminal)

```bash
cd base-panel
npm install
cp .env.example .env      # NEXT_PUBLIC_API_BASE=http://localhost:8000
npm run dev               # http://localhost:3000
```

Open <http://localhost:3000>, sign in as `admin` with the `BOOTSTRAP_ADMIN_PASSWORD` you set.
That account is created on first boot, only while the users table is empty.

### About the API key

**The app boots, indexes and searches with no key at all** — but chat needs a model, so
without one every answer is a Persian "the model is not configured" message. One key from
[avalai.ir](https://avalai.ir) covers both chat and embeddings; it is an OpenAI-compatible
endpoint reachable from Iranian infrastructure without a proxy.

Two things degrade gracefully rather than failing, and both are normal states, not bugs:

- **No key** → retrieval still runs, in BM25-only mode (the query cannot be embedded).
  Every one of the 3,502 chunks is still indexed and citations still work; only ranking on
  conversational questions gets weaker.
- **No `data/embeddings.npz`** → same BM25-only mode, logged as `embeddings_missing
  mode=bm25_only`. The file *is* committed here, so this is not the state you will see.

### Checking it without a browser

Everything except `/healthz` is behind the session cookie, so log in first:

```bash
curl -c /tmp/liara.cookies -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<BOOTSTRAP_ADMIN_PASSWORD>"}'

curl -N -b /tmp/liara.cookies -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"content":"چطور یک اپ nodejs را روی لیارا دیپلوی کنم؟"}'
```

Interactive API docs are at <http://localhost:8000/docs> (automatically disabled whenever
`COOKIE_SECURE=true`, i.e. in production).

### What to try in the UI

| Page | What it does | Try |
|---|---|---|
| `/chat` | The assistant. Streamed answer, numbered sources, follow-up chips | «چطور یک اپ nodejs را روی لیارا دیپلوی کنم؟» |
| `/config` | `liara.json` generator | pick a platform, fill the form, copy the JSON |
| `/diagnose` | Build-log diagnoser | paste [`docs/sample-build-failure.log`](docs/sample-build-failure.log) |
| `/admin` | Agent settings — model, ladder, budgets (superuser only) | switch `modelPrimary`, ask again |
| `/admin/users` | Approve pending registrations (superuser only) | register a second account, activate it |
| `/admin/usage` | Token and real USD cost per model (superuser only) | after a few answers |

### Verified

Every command above was run against a clean checkout of exactly what this repository
contains, on macOS with Python 3.12.9 / Node 23.11:

- dependency install from `backend/requirements.txt` — OK
- server boots, `/healthz` reports `chunks: 3502, embeddings: true, db: true, disk: true`
- bootstrap admin login — OK
- all 15 test modules pass (see [Tests](#tests))
- panel `npx tsc --noEmit` and `npm run build` — OK, 14 routes

---

## What problem it solves

Liara's documentation is complete, but a Persian-speaking user hits three problems:

1. **Finding the right page.** Keyword search over Persian is weak: the user types «دیپلوی»
   and the page says «استقرار», or «دیتابیس» against «پایگاه‌داده».
2. **Not trusting the answer.** A general chatbot invents `liara.json` keys, CLI flags that
   do not exist and plans that were never offered — confidently.
3. **The gap between an answer and the work.** The user reads the answer and still has to
   write the config file or decode the build error themselves.

The answer here: retrieval that understands Persian, an answer where **every claim carries a
source number**, and two tools that produce directly usable output.

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 1) INGEST  (offline, manual — NEVER runs at deploy time)                  │
 │                                                                          │
 │  github.com/liara-cloud/docs → public/llms/**/*.md   (llms.txt format)    │
 │        │  extract Original link / title / detect language                 │
 │        │  chunk on ## and ### headings (merge <200, split >1500 chars)    │
 │        ▼                                                                 │
 │  data/chunks.jsonl   3,502 chunks from 1,143 pages                        │
 │        │  (optional) AvalAI /embeddings — 100 texts per request           │
 │        ▼                                                                 │
 │  data/embeddings.npz  vectors: L2-normalised float16 (N,1536) + ids       │
 └──────────────────────────────────────────────────────────────────────────┘
                                    │  both files ship inside the API image
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 2) RETRIEVAL  (in-process, no side-car vector service)                    │
 │                                                                          │
 │   query ──┬─► embed_query (AvalAI) ──────► matrix @ q ─► 50 dense cands   │
 │           ├─► tokenize + expand_query ───► BM25Okapi  ─► 50 word cands    │
 │           └─► char 4-grams           ───► BM25Okapi  ─► 20 ngram cands    │
 │                          └────────► RRF: Σ 1/(60+rank) ────► top 8        │
 │   no npz or no key ⇒ BM25 only, no error                                  │
 └──────────────────────────────────────────────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 3) AGENT LOOP  (chat_service)                                            │
 │                                                                          │
 │   mandatory pre-retrieval ─► CitationRegistry hands out the numbers       │
 │        ▼                                                                 │
 │   system prompt + history (10 msgs) + <docs source="untrusted"> block     │
 │        ▼                                                                 │
 │   at most 3 tool rounds  {search_docs, read_page,                         │
 │        │                  generate_config, diagnose_log}                  │
 │        ▼  the closing round is forced with tool_choice="none"             │
 │   stream tokens (holding back the tail until the @@@ suggestions line)    │
 └──────────────────────────────────────────────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 4) SSE UI  (Next.js 16, RTL)                                             │
 │   meta → tool(start/end) → token… → sources → suggestions → done | error  │
 │   ping every 15s so a proxy does not close the connection                 │
 └──────────────────────────────────────────────────────────────────────────┘
```

| Part | Path | Technology |
|---|---|---|
| API | `backend/` | FastAPI 0.141, SQLAlchemy 2 async + aiosqlite, structlog, slowapi |
| Ingest | `backend/ingest/ingest.py` | standalone script, local only |
| Panel | `base-panel/` | Next.js 16 (App Router), React 19, Tailwind v4 |
| Models | — | AvalAI (primary — chat + embeddings on one key) → OpenRouter / Gemini (optional chat fallback) |

No GPU and no vector database: the whole index lives in the API process's memory and the
server only runs numpy dot products and BM25.

---

## The panel — features and architecture

`base-panel/` is the whole product surface: there is no second UI and no CLI. Next.js 16 App
Router, React 19, Tailwind v4, Persian and RTL by default, light and dark.

### What it does

| Screen | Route | What the user gets |
|---|---|---|
| Assistant | `/chat`, `/chat/<uuid>` | the streamed answer, numbered sources, follow-up chips, code side panel, edit/regenerate with versions |
| Config wizard | `/config` | pick a platform, fill a form, get a valid `liara.json` with a copy button |
| Log diagnoser | `/diagnose` | paste a failed build log, get the cause and the fix, cited |
| Profile | `/settings` | your stack, in three short fields — the assistant reads it and shapes answers to it |
| Sign in / register | `/login`, `/register` | rendered outside the app chrome; registration lands in an approval queue |
| Accounts | `/admin/users` | activate pending registrations, promote, reset a password, delete — pending sort to the top |
| Cost & usage | `/admin/usage` | tokens and real USD, in total and per model |
| Assistant settings | `/admin` | the model, the fallback ladder, tool rounds, time and call budgets, the handoff policy |
| Assistant prompt texts | `/admin/prompts` | the three prompts plus the handoff copy and its trigger phrases, each revertible to the shipped default |

The four operator screens are visible only to a superuser — the nav does not render them, and
the API returns 403 regardless of what the nav renders.

### The chat surface, in detail

- **Streaming with the work shown.** SSE events arrive as `meta → tool(start/end) → token… →
  sources → suggestions → done`. `ToolStatus` renders what the agent is doing right now as
  plain shimmering text at the end of the answer it belongs to — no chip, no spinner.
- **Citations are links only when they are real.** `Markdown.tsx` turns `[n]` into a link only
  for numbers present in the `sources` map the server sent. A model-invented `[9]` stays plain
  text. Every href goes through `safeHref()`, which drops anything that is not `https://`.
- **A refresh does not kill the answer.** The conversation payload carries `activeRun`; when it
  is true the panel re-attaches to `GET /api/v1/chat/{uuid}/stream`, which replays from the
  first event and then follows live. Deep link, refresh and the back button all agree because
  *which* conversation is open is read from the URL, never from component state.
- **Edit and regenerate produce versions, not overwrites.** Both create a sibling in the
  conversation tree, and `MessageVersions` renders `‹ ۲ / ۳ ›` under the message. Each version
  owns the branch below it, so switching asks the server to switch rather than swapping text
  locally.
- **Code goes to a side panel.** `ArtifactProvider` lifts a fenced block out of the transcript
  into a sibling column (`ArtifactPanel`) — a real `<dialog>`, landing on the inline-end edge,
  which in RTL is the left — labelled with the language and line count and carrying a copy
  button, so a long `liara.json` never pushes the conversation off screen. Below `lg` it covers
  the transcript rather than squeezing it. Syntax highlighting itself lives in `Markdown.tsx`
  (GFM + rehype-highlight), which also renders the citation links.
- **Stop is a real stop.** It ends the turn cooperatively on the server; the partial answer is
  kept, not discarded.
- **Conversations name themselves** and are renamable inline from the pencil in the sidebar
  (Enter saves, Escape cancels); deletion is optimistic and restores the row if the API refuses.

### Architecture

```
src/
  app/layout.tsx            <html lang="fa" dir="rtl">, ThemeProvider, MeProvider, AppShell
  app/<route>/page.tsx      one folder per screen + a co-located layout.tsx exporting metadata
  app/globals.css           every color token, light and dark, Tailwind v4 @theme
  app/fonts/                Vazirmatn variable woff2 — bundled, never fetched
  components/layout/        AppShell (the only <main> + the sign-in gate), Sidebar, Header, nav.ts
  components/chat/          ChatView, MessageList, Composer, SourcesList, Markdown, ArtifactPanel…
  components/wizards/       PlatformPicker, LogInput, ConfigResult, DiagnosisResult
  components/ui/            button, card — hand-rolled with cva + tailwind-merge, no UI dependency
  hooks/useChat.ts          the turn: send, stop, retry, edit, switchVersion, re-attach
  hooks/useConversations.ts the sidebar list and the active uuid
  lib/api.ts                the only place fetch is called
```

Four decisions carry the rest:

1. **`lib/api.ts` is the single HTTP seam.** It parses SSE frames (tolerating a frame split
   across two reads), throws `ApiError` carrying the backend's Persian message, bounces a 401 to
   `/login` — except on the auth endpoints and the public pages, or it would loop — and exports
   `safeHref()`. No component ever calls `fetch`.
2. **The sign-in gate here is UX, not security.** The session cookie belongs to the API's
   origin, so the Next server never receives it and no `proxy.ts` or `middleware.ts` gate exists
   or can. `AppShell` sending an anonymous visitor to `/login` is presentation; the backend's
   per-endpoint dependency is what actually refuses.
3. **Pages orchestrate, hooks mutate, components render.** `useChat` is a `useReducer` over the
   whole turn — one SSE event, one dispatch — because a dozen related fields change together.
4. **State lives where the truth is.** `ConversationsProvider` reads the open conversation from
   the pathname, so there is no second copy to drift; `MeProvider` holds the signed-in operator
   and a `loading` flag, so nothing flashes signed-out and then signed-in.

### RTL and theming, as rules rather than intentions

- **Logical properties only**: `ps-`/`pe-`, `ms-`/`me-`, `text-start`/`text-end`, `border-e`.
  Never `pl-`/`pr-`/`ml-`/`mr-`/`left-`/`right-` — they do not flip. Directional icons flip with
  `rtl:-scale-x-100`.
- **Code and config stay LTR** — `dir="ltr"` plus its own horizontal scroll on every `<pre>`,
  inline code and URL, so a shell command never renders backwards inside a Persian sentence.
- **Numbers shown to users go through `toLocaleString('fa-IR')`.**
- **No hex, rgb or hsl literal in any `.ts`/`.tsx`.** Every color is a token defined for *both*
  themes in `globals.css` and re-exported in the `@theme inline` block, which is what makes the
  theme toggle a one-line change rather than an audit.
- **The font is bundled, not fetched.** `vazirmatn-variable.woff2` (111 KB, weights 100–900, SIL
  OFL) via `next/font/local`. `next/font/google` fails *silently* when the compile-time download
  does not succeed — Next emits the metric fallback and no `@font-face` at all, so Persian
  renders in whatever the system picks, with nothing logged. The app deploys to an Iranian
  platform where reaching fonts.googleapis.com at build time is a coin toss; a file in the
  repository is not.
- **Client-side input limits mirror the server exactly** (message 4,000, log 6,000, profile
  fields ≤80, title ≤120) so no user ever eats a 422. They are duplicated constants, not
  generated — both sides change together.
- The chat height chain is what prevents a double scrollbar: `h-dvh` > `overflow-hidden` >
  `main flex-1 overflow-y-auto` > `PageContainer h-full min-h-0 flex-col` > list
  `flex-1 min-h-0 overflow-y-auto` + a `shrink-0` composer.

Accessibility basics are not optional here: every `<label>` is linked to its control with
`htmlFor` + `id`, and `aria-live` sits on a small purpose-built region rather than around the
transcript — otherwise a screen reader re-announces the whole conversation on every token.

---

## Why these decisions

### Why BM25 *and* embeddings (hybrid)

Neither is enough alone:

- **Dense only** fails on exact names. A user typing `ENOENT`, `liara.json` or `DISK_MOUNT`
  wants a term match, not semantic similarity; an embedding loses those in a cloud of
  neighbouring concepts.
- **BM25 only** fails on conversational Persian. «برنامه‌ام بالا نمیاد» shares no word with
  the page that answers it.

So both lists are taken and merged with **RRF** (`Σ 1/(60+rank)`) — not a weighted sum of
scores, because a BM25 score and a cosine live on two unrelated scales and weighting them
requires an arbitrary magic number. RRF only looks at **rank**.

A third list was added later: **character 4-grams**, which buy morphology and typos. Measured
on the 100-question set:

| variant | literal@5 | paraphrase@5 |
|---|---|---|
| dense + word BM25 | 51/55 | 14/30 |
| + Persian suffix stemming only | 51/55 | 14/30 |
| + char n-grams only | 51/55 | 17/30 |
| both (shipped) | 51/55 | 18/30 |

The stemming was predicted to be the big win and measured as **zero on its own**. It survives
only because it is additive — `tokenize` emits the surface token *and* its stem, so a wrong
stem can only add a term that collides with nothing.

The Persian side also has real preprocessing (`app/shared/persian.py`): normalising `ي→ی`
and `ك→ک`, stripping diacritics and kashida, converting Arabic/Persian digits to ASCII,
handling ZWNJ — plus 217 Persian stopwords and a domain synonym table
(`دیپلوی/استقرار/deploy`, `دیتابیس/پایگاه‌داده/database`, …) expanded on the **query** side
only, never on the document side.

### Why AvalAI

One provider, one key, one OpenAI-compatible endpoint (`https://api.avalai.ir/v1`) serving
both chat and `/embeddings`, reachable from Iranian infrastructure without a proxy — which for
a project meant to run on Liara is a practical requirement, not a preference.

The architectural consequence was that the model layer got *simpler*: the hand-rolled REST
path for Gemini embeddings was deleted, and the same `openai.AsyncOpenAI` client that streams
chat also calls `client.embeddings.create`.

OpenRouter and Gemini are still supported as **chat** fallbacks if their keys are set, but
embeddings come from AvalAI only — a stored vector and a query vector must come from the same
model.

### Why llms.txt and not a crawler

The corpus comes from `public/llms/**/*.md` in the official `liara-cloud/docs` repository:

- **It is clean markdown, not HTML.** No nav menu, footer, cookie banner or script to strip
  later; chunk quality is directly better.
- **The headings are real.** Chunking on `##`/`###` means semantic boundaries, not a blind
  sliding window.
- **Every file carries an `Original link:` line**, so the citation URL comes from the source
  itself rather than being constructed.
- **Reproducible and polite.** One `git clone --depth 1` instead of thousands of HTTP
  requests; refreshing the corpus is re-running one script.

---

## Citations and abstention are enforced in code

The prompt says what to do; the code decides what is *possible*. Four layers:

**1. Only the registry mints numbers.** `CitationRegistry.assign(chunk)` is the single place
a number is ever attached to a source, and it is called before any snippet is formatted
(`tools_service.execute_tool`). The `sources` event sent to the panel is built from
`registry.used_sources(answer)` — from the **registry**, never from the model's text. If the
model writes `[9]` and source 9 does not exist, no source is created and the marker does not
become a link (`Markdown.tsx` only links numbers present in the map).

**2. URLs are locked to an allowlist.** `RetrievalService._read_chunks` drops any chunk whose
URL does not start with `https://docs.liara.ir/` and logs the count; on the ingest side an
`Original link:` is accepted only when it points at that domain, otherwise the URL is derived
from the file path. In the panel, `safeHref()` strips any non-`https://` href. There is no
path by which a poisoned page in the corpus can get `javascript:` into an `<a href>`.

**3. Abstention is a prompt rule, not a code gate.** Retrieval no longer runs before the model
is contacted, so there is no cosine to threshold and no empty retrieval to block on; system
prompt rule 5 says that if, after searching (and retrying with different wording), the topic
is not in the docs, the model must say exactly that.

**4. A source appears only if it was actually cited.** `CitationRegistry.used_sources` returns
only the sources the answer referenced with `[n]`; an answer with no references shows no source
list. Now that abstention is textual, "a search ran but the answer does not rest on it" is the
normal shape of a refusal — and showing real doc chips under it would claim a groundedness that
is not there.

> Honest about the limit: layer 3 *steers* model behaviour and is not a hard guarantee. What
> **is** guaranteed is that no source outside the registry and no URL outside the docs domain
> can ever reach the user.

### Why no abstention threshold on BM25

It was tempting to add a score threshold for BM25-only mode. It was measured and rejected. On
this corpus, both the raw BM25 score and "query term coverage" overlap between in-domain and
out-of-domain questions:

| signal | min on a valid question | max on an unrelated one |
|---|---|---|
| raw BM25 score | 11.7 («دیتابیسم … پاک میشه») | 22.1 («قیمت بیت کوین امروز چند است؟») |
| term coverage | 0.40 | 1.00 («سلام خوبی») |

Any threshold would refuse exactly the hardest real user questions — the short, conversational
ones — while answering "what is the price of bitcoin". The second row settles it: «سلام خوبی»
scores 1.00 coverage, so no lexical signal can separate a greeting from a technical question.
That dead end is what led to the current architecture: the "does this even need a search"
decision was handed to the model itself (`tool_choice="auto"`), which unlike any numeric
threshold understands what the sentence means.

---

## Retrieval quality — measured

85 real Persian questions against the full corpus, produced by a runnable harness rather
than asserted. Run in **BM25-only mode** (no API key set, so the query is not embedded):

| question class | recall@1 | recall@5 |
|---|---|---|
| simple (deploy, env, domain, database…) | 28/35 | 32/35 (91%) |
| complex / multi-part | 5/10 | 9/10 (90%) |
| troubleshooting | 7/10 | 7/10 (70%) |
| **literal wording** | **40/55** | **48/55 (87%)** |
| **paraphrased wording** | **10/30** | **18/30 (60%)** |
| **overall** | **50/85** | **66/85 (78%)** |

Search latency: median **23.6 ms**, max 368.9 ms.

```bash
cd backend && ../.venv/bin/python -m tests.eval_retrieval
```

The question set lives in `backend/tests/eval_questions.json`. The 27-point gap between
literal and paraphrased wording is the honest headline number: this is what dense retrieval
closes, and why an API key is worth setting.

The troubleshooting class was 2/5 before the `_SYMPTOM_HINTS` table in `app/shared/fa_words.py`
was added: the user writes «پاک میشه» and «بالا نمیاد» while the doc says «دیسک» and
«هلث‌چک». That table is a **one-way** symptom → concept mapping — the reverse is deliberately
not applied, so a precise question is never polluted.

**A reranker is deferred, and the numbers say why.** In the paraphrase class recall@1 equals
recall@5: when retrieval misses, the right page is not in the candidate pool at all, and
reranking a pool that lacks the answer cannot help.

---

## Containing prompt injection from untrusted docs

The corpus is third-party content, and it genuinely contains instruction-shaped text: the page
`ai/ai-sdk-errors/ai-api-call-error.md` is effectively a leftover content brief telling the
reader to "rewrite the links so they start with …". This threat is not hypothetical.

- Every snippet is wrapped in `<docs source="untrusted">`, and system prompt rule 1 says the
  content of that block is **data, not instructions**.
- `tools_service._neutralize` escapes any `<docs …>`/`</docs>` inside untrusted text, so the
  envelope cannot be closed early to forge "trusted" text.
- The same function breaks a leading `[n]` in untrusted text into `&#91;n]`, so a poisoned page
  cannot produce a line byte-identical to a server-generated citation header and sell the model
  a fake source.
- Tool arguments coming from the model are all clamped/whitelisted: `query` ≤ 300 chars, `k`
  between 1 and 8, `platform` through an enum — and `read_page` **makes no network request at
  all**, reading only URLs already in `retrieval_service.known_urls()` from memory.
- Tools never throw into the loop; an error becomes one explanatory line.

A fifth, weaker layer, `ingest.injection_scan`, is deliberately **report-only, exit 0**: it
prints every instruction-shaped line it finds (47 on the current corpus) so contamination stays
visible run to run. It is not a mitigation, and it must never gain the power to fail an ingest.

Runnable self-check: `cd backend && ../.venv/bin/python -m tests.test_untrusted_corpus`

---

## Accounts, roles and registration

**The whole product is behind login.** The only public route is `/healthz` (the platform polls
it without a cookie); chat, conversations, profile and both wizards return 401 without a session
cookie. The `X-Session-Id` header is gone — from the server, from the panel and from
`localStorage`. It was a **device** identifier behaving like an identity: guessing one UUID
meant reading someone else's conversations and profile.

The `sessions` table now has a `user_id` column (UNIQUE, `ON DELETE CASCADE`) with exactly one
row per user. Because of that, every `session_id` filter that was already **inside the query**
became an **ownership filter** without a single query being rewritten
(`get_scoped`/`delete_scoped`/`list_for_session`). Each user sees only their own conversations,
and a stranger's UUID gets exactly the 404 a nonexistent UUID gets.

Two roles, and only these two (`users.is_superuser`):

| Role | Sees |
|---|---|
| regular user | the assistant, their own conversations, their profile. **403** on every `/api/v1/admin/*` route and on `/api/v1/users` |
| superuser | the same, plus agent settings, the accounts screen, and the usage & cost screen |

**Public registration, manual activation.** `POST /api/v1/auth/register` is public (202, capped
at `3/hour`) and creates the account with `is_active=false`. An admin activates it with the
existing `PATCH /api/v1/users/{uuid}` `{isActive: true}` on the `/admin/users` screen; pending
accounts sort to the top of the list. There is no separate "approvals" endpoint or screen, and
there should not be one. Logging into an unapproved account is deliberately **indistinguishable**
from a wrong password — same message, same ~100 ms cost — so the user list cannot be enumerated.

**Usage and cost.** `GET /api/v1/admin/usage` (superuser only) and the `/admin/usage` screen:
grand total plus a per-model breakdown, with real USD cost from the prices in
`app/shared/model_catalog.py`.

```json
{"totalCostUsd":0.029737,"promptTokens":66813,"completionTokens":6517,"turns":5}
```

---

## Other security decisions

- **CSRF has two layers and the authoritative one is server-side.** `main.py` refuses any
  non-GET/HEAD/OPTIONS request whose `Origin` falls outside `ALLOWED_ORIGINS` with a 403
  `BAD_ORIGIN`. This is not belt-and-braces: **`liara.run` is on the Public Suffix List**, so
  the panel and the API count as two separate *sites*, the cookie needs `SameSite=none`, and
  the browser's own CSRF protection is therefore gone. `Origin` is a forbidden header name, so
  a page cannot forge it; an absent one (curl, the platform probe) is allowed.
- **Interactive API docs** (`/docs`, `/redoc`, `/openapi.json`) are switched off whenever
  `COOKIE_SECURE=true`.
- **A request body cap** (`MAX_BODY_BYTES = 256 KB`) is enforced in middleware before the body
  is read; the rate limiter cannot cover this, because FastAPI parses the body before the
  endpoint runs.
- 422 responses no longer echo the caller's `input`.
- Logs write only the first 8 characters of a session id, and slowapi's own logger is silenced.
- SQLAlchemy, aiosqlite, openai, httpx and httpcore loggers are pinned to WARNING. SQLAlchemy
  logs statements *and their bound parameters* at INFO — inheriting the root level would write
  `users.password_hash` and every chat message into the platform log.
- **The session token carries a uuid and a password fingerprint, nothing else.** Privilege is
  re-read from the database on every request, so a user deactivated a minute ago loses access
  now, not in twelve hours; and a password change or a forced reset is an actual eviction
  rather than a cosmetic one.
- **`X-Forwarded-For` is believed only behind a proxy** — the immediate peer must be
  private/loopback/link-local, and the check fails closed on anything it cannot parse. Read
  unconditionally, one rotating header would make every unauthenticated limit unenforceable at
  once.

---

## Environment variables

### Backend — `backend/.env` (template: `backend/.env.example`)

| Variable | Default | Meaning |
|---|---|---|
| `AVALAI_API_KEY` | `""` | **The main key** — chat *and* embeddings. The only thing that turns on dense retrieval |
| `AVALAI_BASE_URL` | `https://api.avalai.ir/v1` | OpenAI-compatible endpoint |
| `MODEL_PRIMARY` | `gpt-5-mini` | Chat model on AvalAI |
| `EMBED_MODEL` | `text-embedding-3-small` | Must match the vectors in `embeddings.npz` |
| `EMBED_DIM` | `1536` | Vector width; a mismatch with the matrix ⇒ safe fall back to BM25 |
| `OPENROUTER_API_KEY` | `""` | Optional chat fallback. **Not** used for embeddings |
| `GEMINI_API_KEY` | `""` | Optional chat fallback. **Not** used for embeddings |
| `MODEL_FALLBACK` | `google/gemini-2.5-flash` | Model on the fallback provider |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS, comma-separated. **Add the deployed panel origin** |
| `DATABASE_PATH` | `./storage/app.db` | On Liara keep this **relative** (the disk mounts to `storage`) |
| `DATA_DIR` | `./data` | Where `chunks.jsonl` and `embeddings.npz` live |
| `AGENT_MODE` | `true` | `false` ⇒ no tool loop, pre-retrieval only |
| `REASONING_EFFORT` | `low` | GPT-5 family only. **The latency dial** — see the note below |
| `STREAM_ENABLED` | `true` | `false` ⇒ the same events delivered as one JSON blob |
| `AGENT_MODEL_LADDER` | `gpt-5-mini,claude-haiku-4-5,gpt-4.1-mini` | If one model does not deliver, the next is tried silently, in this order. Cross-family on purpose; unknown ids are dropped |
| `AGENT_TOOL_ROUNDS` | `3` | How many times the first model may search the docs |
| `AGENT_RETRY_TOOL_ROUNDS` | `1` | How many times later models may — fewer, because the previous model's documents are still in hand |
| `AGENT_MAX_MODEL_ATTEMPTS` | `3` | How many models one question may cost, including the first |
| `AGENT_TURN_BUDGET_SECONDS` | `120` | Wall-clock cap for producing an answer. Checked before each call, never mid-sentence |
| `AGENT_MAX_LLM_CALLS` | `8` | Total model calls for one answer, across every model tried |
| `AGENT_ESCALATION` | `true` | `false` ⇒ only the selected model is tried |
| `LLM_ATTEMPT_TIMEOUT_SECONDS` | `45` | Per-call timeout, below the turn budget so one stuck provider cannot eat it all |
| `AUTH_SECRET` | `""` | Session cookie signing key. **Empty ⇒ nobody can sign in**; with `COOKIE_SECURE=true` the app deliberately **refuses to start** |
| `COOKIE_SECURE` | `false` | Must be `true` over HTTPS. `true` also disables the interactive docs |
| `COOKIE_SAMESITE` | `lax` | Only `lax\|strict\|none`; anything else ⇒ the app will not start. `none` without `COOKIE_SECURE=true` is also refused |
| `BOOTSTRAP_ADMIN_USER` | `admin` | First boot only, and only while the users table is empty |
| `BOOTSTRAP_ADMIN_PASSWORD` | `""` | At least 12 characters, or no account is created. **Do not unset it after first boot** |
| `NO_PROXY_HOSTS` | `api.avalai.ir` | Hosts that must be reached directly, merged into the process `NO_PROXY` |
| `LOG_LEVEL` | `INFO` | `DEBUG\|INFO\|WARNING\|ERROR` |

> **These values are only read the first time the database is created.** After that the source
> of truth is what is stored in the panel, and editing this file has no effect. The exceptions —
> never migrated into the database — are the API keys, `AUTH_SECRET`, the cookie settings,
> `ALLOWED_ORIGINS`, `DATABASE_PATH` and `EMBED_*`. Each of those is either a secret, or
> circular (a wrong value locks the operator out of the only screen that could fix it), or a
> property of a file on disk rather than a preference.

**`REASONING_EFFORT` is the latency dial and it dominates everything else.** A turn calls the
model two to three times, so its per-call cost is multiplied. Measured on this corpus with
`gpt-5-mini` on AvalAI: the model's own default (`medium`) took **92 s** end to end for one
documentation question; `low` takes **11 s** — with identical retrieval quality.

**No model key is mandatory**: without one the app boots, retrieval works, and chat composes a
grounded fallback answer. But there are three cookie configurations the app deliberately
**refuses to start** on, because all three used to be silent failures that surfaced as "the
session is broken":

1. `COOKIE_SAMESITE` outside `lax|strict|none`;
2. `COOKIE_SAMESITE=none` without `COOKIE_SECURE=true` (the browser silently drops such a cookie);
3. `COOKIE_SECURE=true` with an empty `AUTH_SECRET` (an app nobody can sign into).

Across two `*.liara.run` domains the only working combination is `COOKIE_SECURE=true` +
`COOKIE_SAMESITE=none`, because `liara.run` is on the Public Suffix List and the panel and API
count as cross-**site**. On localhost `false` + `lax` is correct (different port, same site).

### Panel — `base-panel/.env` (template: `base-panel/.env.example`)

| Variable | Local | Production |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | `https://liara-docs-api.liara.run` |

> ⚠️ **The single most important deployment detail.** Next inlines `NEXT_PUBLIC_*` into the
> bundle at **build** time. If this variable is not set in the panel app's environment before
> `npm run build` runs, the bundle ships with `API_BASE=''`, every request goes to the panel's
> own origin, and everything 404s. `src/lib/api.ts` logs a loud `console.error` in that case.

---

## Tests

There is no pytest and no test framework. Every test is a standalone module you run directly,
which is the whole point — nothing to install, and each one prints what it checked.

```bash
cd backend
../.venv/bin/python -m tests.test_auth               # authorization boundary, written as attacks
../.venv/bin/python -m tests.test_untrusted_corpus   # poisoned-corpus defences
../.venv/bin/python -m tests.test_prompt_contract    # prompt / tool-payload invariants
../.venv/bin/python -m tests.test_detached_turn      # refresh must not kill the answer
../.venv/bin/python -m tests.test_escalation         # the model ladder never leaks to the user

# all of them:
for t in tests/test_*.py; do ../.venv/bin/python -m "tests.$(basename $t .py)"; done
```

Current state on a clean checkout — all 15 modules pass:

| module | result | what it pins |
|---|---|---|
| `test_admin_prompts` | 10 checks | operator-editable prompt texts, validate on read *and* write |
| `test_admin_settings` | 8 checks | settings live in the DB, allowlists enforced twice |
| `test_answer_buffer` | 6 checks | multi-round streaming, suggestion marker held back |
| `test_auth` | 16 checks | 401/403/404 boundaries, forged cookies, forged `X-Forwarded-For` |
| `test_config_context` | 3 checks | the `liara.json` wizard's context |
| `test_detached_turn` | 9 checks | a disconnect does not lose the answer; re-attach replays |
| `test_escalation` | 8 checks | a rejected attempt's text never survives to persistence |
| `test_handoff` | 9 checks | the default invitation does not trip the never-say-limit guard |
| `test_message_tree` | 17 checks | edit/regenerate branches |
| `test_model_allowlist` | ok | every allowlisted model id exists on the provider |
| `test_prompt_contract` | 5 checks | every tool in the payload is named in the system prompt |
| `test_resume_freshness` | ok | `--resume` can never keep a stale vector |
| `test_title` | 5 checks | auto-titling, compare-and-set against a rename |
| `test_untrusted_corpus` | ok | all four injection layers |
| `test_usage` | 24 checks | token accounting and USD cost |

Three further harnesses cost real LLM calls, so they are not part of the sweep:
`tests.eval_retrieval` (free, no key needed), `tests.eval_agentic` and `tests.eval_faithfulness`.

Frontend check — there is no lint script (`next lint` was removed in Next 16):

```bash
cd base-panel && npx tsc --noEmit && npm run build
```

---

## Rebuilding the corpus (ingest)

**You do not need to run this.** `data/chunks.jsonl`, `data/embeddings.npz` and
`data/corpus_meta.json` are committed, and the app cannot answer without them. Ingest exists to
*refresh* the corpus, never at deploy time.

```bash
cd backend

# chunks only — free, no key, no network beyond one shallow clone
../.venv/bin/python ingest/ingest.py --skip-embeddings

# with an existing docs checkout
../.venv/bin/python ingest/ingest.py --repo-dir /path/to/docs --skip-embeddings

# regenerate the vectors (costs money — this is the only step that does)
AVALAI_API_KEY=<key> ../.venv/bin/python ingest/ingest.py --resume
```

The output is `data/embeddings.npz` (float16, L2-normalised, shape `(3502, 1536)`). Restart the
service; `/healthz` should report `"embeddings": true`.

Every ingest also writes `data/corpus_meta.json` (docs commit, ingest time, sha256 over the
`chunks.jsonl` bytes, embedding model and dimension) and stamps the same metadata inside the
npz. At load the app compares them against the file on disk: an npz built for a different
corpus is logged as `embeddings_meta_mismatch` and retrieval falls back to BM25-only.
`ingestedAt` and `corpusCommit` are reported in `/healthz`.

**`--resume` reuses a vector by the sha of the exact text that was embedded, never by chunk
id.** Ids are page-local positions (`{slug}#{0000}`), so an upstream edit that leaves a page's
chunk count unchanged would otherwise keep a vector built from text that no longer exists —
while the freshly stamped corpus hash reported a clean run. Content addressing also *widens*
reuse: text that merely moved keeps its vector.

Periodic refresh without a new service — `ingest/reingest.sh` drops the clone cache, re-ingests
with `--resume` and runs `tests.eval_retrieval` (falling below the floor exits non-zero). One
line in `crontab -e`:

```cron
17 4 * * 1 /path/to/lia-helper/backend/ingest/reingest.sh >>/tmp/liara-reingest.log 2>&1
```

---

## Deploying to Liara

Two apps: `liara-docs-api` on the python platform, `liara-docs-panel` on the next platform.

### The order is not symmetric — use exactly this one

On an already-running deployment (a fresh install starts at step 2):

**1) Back up the database first.**

```bash
liara shell --app liara-docs-api
sqlite3 storage/app.db ".backup storage/app.db.pre-auth"
```

The first boot after the auth change **permanently deletes** every conversation whose session
has no owner — i.e. all older conversations. There is no way back.

**2) Set the auth variables on both sides before deploying** (the `liara env:set` block below).

**3) Panel first, then backend.** Panel-first degrades politely: the gate sends a signed-out
visitor to `/login`, and only `/register` and `/admin/usage` are idle until the backend lands.
Backend-first is the wrong order: every request against a panel that has no login gate yet gets
a 401 and the user is stranded on an error card with no visible route to the sign-in form.

**4) Keep `"X-Session-Id"` in `ALLOWED_HEADERS` in `main.py` forever.** Nothing reads it any
more, but removing it breaks the CORS preflight for every browser tab open during a deploy, and
keeping it costs nothing.

### a) Backend — `liara-docs-api`

```bash
cd backend

# 1) create the app and the disk (the disk before the first deploy)
liara app:create --app liara-docs-api --platform python
liara disk:create --app liara-docs-api --name data --size 1

# 2) variables — without the last four you get an app nobody can sign into
liara env:set --app liara-docs-api \
  DATABASE_PATH=storage/app.db \
  ALLOWED_ORIGINS=https://liara-docs-panel.liara.run \
  AVALAI_API_KEY=<key> \
  AUTH_SECRET=$(openssl rand -hex 32) \
  COOKIE_SECURE=true \
  COOKIE_SAMESITE=none \
  BOOTSTRAP_ADMIN_PASSWORD=<at least 12 characters>

# 3) deploy — liara.json mounts the "data" disk onto the relative folder `storage`
liara deploy --port 80 --platform python

# 4) check
curl https://liara-docs-api.liara.run/healthz
```

**Why the disk is mandatory:** the container filesystem is ephemeral. SQLite is a **file**; if
`DATABASE_PATH` points anywhere outside the mount, `app.db` is created inside the container
layer and every conversation and profile is wiped on each deploy or restart. `backend/liara.json`
mounts the `data` disk onto the **relative** path `storage`, and `DATABASE_PATH` must stay
`storage/app.db`. Liara's own Python documentation explicitly requires a **relative** path
([use-disk](https://docs.liara.ir/paas/python/how-tos/use-disk/),
[SQLite](https://docs.liara.ir/paas/python/how-tos/connect-to-db/sqlite/)); an absolute path
like `/app/storage` is not reliable on this platform. `/healthz` also writes and reads a 32-byte
marker file next to the database to report that the mount is correct (`"disk": true`).

`backend/liara.json` uses `"platform": "python"` with `backend/Dockerfile` beside it — this is
exactly what [Liara's official FastAPI guide](https://docs.liara.ir/paas/python/related-apps/fastapi/)
recommends (Dockerfile + `liara deploy --port 80 --platform python`), not a workaround.
`liara.json` also declares a `healthCheck` hitting `/healthz`, so a broken deploy fails loudly
instead of being served. The Dockerfile runs with `-w 1` deliberately: the retrieval index and
the rate-limit counters are both in-process, per-process state.

**Keep `BOOTSTRAP_ADMIN_PASSWORD` set after the first boot.** It is completely inert while at
least one user exists, and it is the only recovery path if the disk is lost — with public
registration, the first person to sign up waits in the approval queue and there is nobody to
approve them.

**Backups.** The database runs in WAL mode, so the `.db` file alone is not a snapshot.
Checkpoint the WAL before copying:

```bash
sqlite3 storage/app.db "PRAGMA wal_checkpoint(TRUNCATE)"
# now copy app.db
```

That file now also holds **user credentials**; treat a backup of it as a secret.

### b) Panel — `liara-docs-panel`

```bash
cd base-panel

liara app:create --app liara-docs-panel --platform next

# ⚠️ before the first deploy — Next bakes this into the bundle at build time
liara env:set --app liara-docs-panel \
  NEXT_PUBLIC_API_BASE=https://liara-docs-api.liara.run

liara deploy
```

Then set the backend's `ALLOWED_ORIGINS` to the panel's final domain. If you change
`NEXT_PUBLIC_API_BASE` after a build you **must deploy again** — changing the env var without a
rebuild does nothing.

---

## Operator-controlled model selection

One model answers for everyone, and only a superuser changes it — «تنظیمات دستیار» →
«مدل اصلی», which is a dropdown over the eight allowlisted ids, not a text box someone can
typo a model id into. The value is stored in the database and takes effect from the very next
answer.

```
GET  /api/v1/admin/settings  →  {"settings":[{key:"modelPrimary", value, options:[...]}]}
PUT  /api/v1/admin/settings  ←  {"modelPrimary":"gemini-2.5-flash"}
```

**Users used to pick their own model, and deleting that path is the security story.** The model
id arrived in a request body — an arbitrary string that could become an expensive request or an
unintended provider — and three separate layers existed to validate it. Now the input does not
exist at all: `model` was removed from `PROFILE_KEYS` (so it cannot be stored),
`ChatService._resolve_model` is gone, and `GET /api/v1/models` was deleted along with the picker
that fed it. Removing an input always beats validating it.

`ChatModel` is still an allowlist and is still enforced in **two** places, because
`modelPrimary` and the ladder are themselves operator input:

1. `agent_settings.clean_overrides` — on write *and* on read, so a row written by hand with
   `sqlite3` cannot inject an arbitrary id;
2. `LLMService._providers` — the last gate before the network call.

It fails closed: anything unrecognised silently degrades to the compiled default, and the
rejected value is never logged.

The override applies to AvalAI only; the fallback providers use a different model namespace and
would 404 on an AvalAI id.

> The eight ids are checked against the provider's live list, not guessed from a docs page:
> `../.venv/bin/python -m tests.test_model_allowlist`. That test caught a real mistake —
> `claude-haiku-4` does not exist on the provider and the correct id is `claude-haiku-4-5`.

---

## The answer is not tied to the client connection

The HTTP response itself used to drive the turn. A user refreshing the page closed Starlette's
cancel scope, the model call was abandoned, and the emergency write in `_persist_partial` died
at its own first `await`: the in-flight flush was cancelled, the session went to
`PendingRollbackError` and it was swallowed there. Measured, not hypothetical: 270 characters
had reached the user's screen and zero answer rows were stored. **The answer was not truncated;
it was lost outright.**

`chat_runs.py` owns the turn now. A task with **its own session** iterates `stream_answer` and
appends every event to a list; the HTTP response is only a *follower* over that list. A follower
leaving is a non-event: the turn finishes and commits on a healthy, uncancelled path. And
because that list is a replay log, coming back is free —
`GET /api/v1/chat/{conversation_uuid}/stream` replays from the first event and then continues
live. Which is exactly what Claude and ChatGPT do: refresh, come back, the answer is still
being written.

Real test against a local server:

```
[tab 1] walked away after 286 chars
[reload] activeRun=True messages=1
[tab 2] re-attached, received 651 chars, events: ['meta', 'tool'] … ['suggestions', 'done']
[stored] assistant chars=649
```

Six load-bearing points:

1. **`stream_answer` itself is untouched, and that is the whole point.** Every invariant
   documented above — the commit point, the 240-character window, `_Escalate` never reaching the
   user — holds structurally, and every existing test still drives the same generator unchanged.
2. **Stopping is cooperative, never `task.cancel()`.** An `asyncio.Event` is checked between
   chunks and the turn ends exactly as it does when a provider dies mid-answer: `cut_short`, then
   the normal persist. Cancelling would interrupt the very write this change exists to save.
3. **The caps refuse; they never queue and never evict.** Evicting a live turn would drop the
   only strong reference to its task, and asyncio holds tasks weakly — the answer would vanish
   mid-sentence.
4. **The drain needs a worker that lets it run.** `UvicornWorker` never sets
   `timeout_graceful_shutdown`, so uvicorn waits forever on open SSE connections and the lifespan
   shutdown never executes. `worker.GracefulUvicornWorker` bounds that wait at 10 s.
5. **`activeRun` reports only unfinished runs, and is computed before the branch is read.** That
   ordering makes the race resolve in the safe direction.
6. **The answer is on disk before the turn ends.** At the commit point — the earliest moment the
   text may legally be stored — the answer row is inserted and the branch repointed at it; then
   that same row is rewritten every 240 published characters (floored at one write per second).
   `_persist` UPDATEs that row rather than inserting a second one, so a phantom "2 / 2" version
   never appears under a question. **Every intermediate write carries the «تا همین‌جا توانستم
   ادامه بدهم…» sentence**, and that one detail removes the need for a `pending` column, a boot
   sweep and a filter on every tree walk: text on disk is never partial text presented as whole.

A crash, a redeploy or a SIGKILL no longer takes the answer — at most the last 240 characters.

---

## Conversations name themselves

`_resolve` still writes `question[:60]` when the conversation is created, so a readable name
exists from the first instant and every later failure just means "keep the name it already has".
Then, only for the turn that created the conversation — the first turn, once ever —
`write_title` runs *alongside* the answer, not after it, so time-to-first-token never sees it.

| before | after |
|---|---|
| «تفاوت دیسک و بکاپ در لیارا چیست و چطور دیسک بسازم؟» | «دیسک و بکاپ لیارا» |
| «چطور برای یک برنامهٔ Node.js روی لیارا متغیر محیطی تعریف کنم؟» | «تعریف متغیر محیطی در لیارا برای Node.js» |

- **Two short sessions with the model call between them, not one held across it.** The second
  writes only if the stored title still equals what the first read. That compare-and-set is the
  entire reason there is no `title_edited` column: nothing ever regenerates a title, so the only
  possible race is a rename landing mid-flight, and existing data closes it.
- **`reasoning="minimal"` is not a cosmetic optimisation.** The operator's reasoning setting is
  tuned for answering a documentation question; on a GPT-5 model it would spend more reasoning
  tokens deliberating over six words than the words themselves cost.
- **`_clean_title` is the single sanitiser for all three writers** — placeholder, model, manual
  rename. It keeps ZWNJ (stripping it turns «گفت‌وگو» into «گفتوگو») and drops control characters
  and bidi overrides, which would otherwise reorder the whole sidebar.
- Renaming: `PATCH /api/v1/conversations/{uuid}` with `{"title": "..."}`, from the pencil next to
  each row in the sidebar. Enter saves, Escape cancels.

---

## Handing a stuck user to a human

A documentation assistant can be perfectly correct and still useless: the page it cites does not
cover this particular deployment, the user says it did not help, and says it again. After a few
rounds the right move is not another answer — it is telling them where a human is.

Three things are configurable, all from the panel:

| Where | What |
|---|---|
| Agent settings | whether the policy is on, and after **how many** dissatisfied messages it fires (2–5) |
| Assistant prompt texts | **the invitation copy** — exactly what the user is told |
| Assistant prompt texts | **the dissatisfaction phrases** — one per line |

The default is **off**: this policy changes what the assistant says to users, so switching it on
is the operator's decision, not something a deploy does underneath them.

Four things that are not visible from the outside:

1. **The invitation is copy, not an instruction.** It is written in the assistant's own voice.
   That is what lets one text serve both paths: normally the model is asked to say it in its own
   words, and on the path where every model has failed — precisely where a stuck user needs it
   most — there is no model left to paraphrase anything, so it is printed verbatim.
2. **The text must not trip the never-say-limit guard.** It can reach a user's screen unchanged;
   copy that trips `_claims_limit` is discarded, the whole ladder escalates and no invitation ever
   arrives — indistinguishable from the policy being off. `tests.test_handoff` checks the default
   text against the real guard.
3. **The floor of 2 is not arbitrary.** The answer cache key rejects any conversation longer than
   one message, so a policy that cannot fire before the user's second message can never collide
   with the cache. The ceiling of 5 is half the history window (10 messages), i.e. the most
   detection can see.
4. **Detection is a phrase list, not a sentiment model.** It runs on every question and must never
   be why an answer is slow; and an operator has to be able to predict it — "why did it offer
   support here and not there" is a question a list answers and a classifier does not.

> Do not confuse this with **escalation**. In this codebase escalation means the model ladder: a
> silent retry on the next model that the user must never learn about. This is the opposite move,
> which is why it never shares that word — it is `handoff` everywhere.

---

## Cost control

The app is built so that the cost of a question is bounded and visible:

| Measure | Detail |
|---|---|
| flash-class model | the cheapest tier appropriate for cited RAG |
| embedding LRU cache | `EMBED_CACHE_SIZE = 512`, keyed on `normalize(text)` — a repeated question costs nothing |
| batched ingest | 100 texts per embedding request + `--resume` for vector reuse |
| bounded tool loop | search capped by `AGENT_TOOL_ROUNDS`, and the final call is sent with no tools at all |
| pre-retrieval once | the mandatory first search goes through the same `execute_tool` path, so it never searches twice |
| input bounds | message 4,000 chars, log 6,000, history window 10 messages |
| token accounting | each answer's `usage` (summed over every call) is stored in `messages.usage_json` and reported with real USD cost at `GET /api/v1/admin/usage` |
| per-account daily caps | chat `20/minute;200/day`, wizards `30/minute;60/day` — keyed on the **account** (`user_rate_limit_key`); every other route is `100/minute` per IP |
| free retrieval | BM25 and the matrix multiply are local; in BM25-only mode retrieval costs exactly zero |
| body cap | 256 KB, enforced before parsing |

The **daily** cap is keyed on the account rather than the IP deliberately: with open
registration, an LLM endpoint without a per-user daily cap is an open tap — 20 turns per minute
sustained on `gpt-5-mini` is roughly $34/day, and several times that on a top-tier model. IP is
the wrong key for this: an office behind NAT counts as one user, while a phone changes address
every few minutes.

---

## Code map

```
backend/
  main.py                     app factory, middleware, lifespan
  app/shared/                 persian.py (normalize/tokenize), fa_words.py, sse.py, constants.py
  app/core/                   config, database, deps, logging, exceptions + handlers
  app/domain/models|repositories|schemas
  app/domain/services/        retrieval_service, citations, llm_service, tools_service,
                              prompts, chat_service, chat_runs, handoff, agent_settings
  app/api/v1/endpoints/       chat, conversations, profile, tools, auth, users, admin, health
  ingest/ingest.py            corpus builder
  tests/                      15 standalone self-check modules + 3 eval harnesses
  data/                       chunks.jsonl, embeddings.npz, corpus_meta.json (committed)
base-panel/src/
  app/{,chat,config,diagnose,settings,login,register}/   page + metadata layout
  app/admin/{,users,usage,prompts}/                      superuser screens
  components/chat|wizards|layout|ui
  hooks/useChat.ts, hooks/useConversations.ts
  lib/api.ts                  the only HTTP seam: apiFetch, streamChat, safeHref, 401 → /login
docs/
  README.fa.md                this document in Persian
  architecture.fa.html        architecture write-up as a standalone page (Persian)
  sample-build-failure.log    paste this into /diagnose
```

---

## What we would build next

1. **A real ranking measurement now that `embeddings.npz` exists.** The table above measured
   BM25-only; the same 85 questions should be reported for dense-only and RRF too.
2. **Title boosting and down-weighting the `/ai/` subtree.** In BM25-only mode «دیپلوی nodejs»
   ranks `related-apps/*` above `quick-start`; all relevant, not the ideal order.
3. **Validating the config wizard's output** against a real `liara.json` schema, so an invented
   key never reaches the user even if the model produces one.
4. **👍/👎 feedback on every answer**, storing the retrieved chunks as evaluation data.
5. **Automatic corpus refresh** via a scheduled action on the docs repository instead of a manual
   ingest.
6. **A configurable per-account cost cap** — today the daily cap is one fixed number for everyone;
   a per-user USD budget set by a superuser from `/admin/usage` would be more precise.
