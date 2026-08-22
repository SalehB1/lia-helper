"""Self-check for the two pure helpers behind the cost report and the log diagnosis.

Both are parsers of hostile-ish input — a JSON blob written by an older version of this app,
and a build log pasted by a user — so what is asserted here is mostly the failure modes:
nothing may raise, and nothing may silently return a plausible wrong number.

The token reader is the regression guard for a bug that shipped and went unnoticed for
months: the stored keys are camelCase and the reader looked for snake_case, so every token
counter in the app read zero and nobody could tell the difference between "no usage" and
"not reading the usage".

No database, no network, no framework.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_usage
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.services.tools_service import extract_error_signature  # noqa: E402
from app.shared.usage import (  # noqa: E402
    UNKNOWN_MODEL,
    TokenCount,
    cost_usd,
    label_for,
    read_usage,
)

# --------------------------------------------------------------------------- the reader


def test_the_stored_camelcase_shape_is_read() -> None:
    """What `chat_service` actually writes today."""
    blob = json.dumps(
        {"promptTokens": 2950, "completionTokens": 1123, "totalTokens": 4073,
         "model": "gpt-5-mini"}
    )
    assert read_usage(blob) == {"gpt-5-mini": TokenCount(2950, 1123)}


def test_the_snake_case_shape_reads_identically() -> None:
    """The regression guard. These two spellings must never disagree again."""
    camel = json.dumps({"promptTokens": 10, "completionTokens": 4, "model": "gpt-5-mini"})
    snake = json.dumps({"prompt_tokens": 10, "completion_tokens": 4, "model": "gpt-5-mini"})
    assert read_usage(camel) == read_usage(snake) != {}


def test_an_unusable_blob_is_empty_rather_than_an_exception() -> None:
    """One corrupt row must not take down the whole report."""
    for raw in (None, "", "   ", "{oops", "[]", "3", '"text"', "null"):
        assert read_usage(raw) == {}, raw


def test_a_non_numeric_count_is_zero_not_a_crash() -> None:
    """A hand-edited row cannot inject a type error into the aggregate."""
    assert read_usage(json.dumps({"promptTokens": "x", "completionTokens": None})) == {}


def test_a_boolean_is_not_a_token() -> None:
    """`bool` is a subclass of `int`, so `true` would otherwise count as one token."""
    parsed = read_usage(json.dumps({"promptTokens": True, "completionTokens": 5}))
    assert parsed == {UNKNOWN_MODEL: TokenCount(0, 5)}, parsed


def test_a_row_with_no_model_lands_in_the_unknown_bucket() -> None:
    """Every row written before the model was recorded — priced by nobody, counted by all."""
    parsed = read_usage(json.dumps({"promptTokens": 7, "completionTokens": 3}))
    assert parsed == {UNKNOWN_MODEL: TokenCount(7, 3)}


def test_an_escalated_turn_splits_across_the_models_it_billed() -> None:
    """The reason `byModel` exists: one answer, two models, two prices."""
    blob = json.dumps(
        {
            "promptTokens": 30,
            "completionTokens": 12,
            "model": "gpt-4.1-mini",
            "byModel": {
                "gpt-5-mini": {"promptTokens": 20, "completionTokens": 8},
                "gpt-4.1-mini": {"promptTokens": 10, "completionTokens": 4},
            },
        }
    )
    parsed = read_usage(blob)
    assert parsed == {
        "gpt-5-mini": TokenCount(20, 8),
        "gpt-4.1-mini": TokenCount(10, 4),
    }, parsed
    # The split must add up to the flat totals, or the report and the turn disagree.
    assert sum(t.prompt for t in parsed.values()) == 30
    assert sum(t.completion for t in parsed.values()) == 12


def test_a_model_outside_the_allowlist_is_bucketed_not_echoed() -> None:
    """The blob is database content; an arbitrary string must not reach an API response."""
    blob = json.dumps(
        {"byModel": {"<script>evil</script>": {"promptTokens": 5, "completionTokens": 1}}}
    )
    parsed = read_usage(blob)
    assert parsed == {UNKNOWN_MODEL: TokenCount(5, 1)}, parsed


# --------------------------------------------------------------------------- pricing


def test_a_million_tokens_costs_the_published_rate() -> None:
    """gpt-5-mini is $0.25 in and $2.00 out per million."""
    assert cost_usd("gpt-5-mini", TokenCount(1_000_000, 1_000_000)) == 2.25


def test_an_unpriceable_model_is_none_and_never_zero() -> None:
    """Zero is a claim that the tokens were free. They were not; we just cannot price them."""
    assert cost_usd(UNKNOWN_MODEL, TokenCount(1000, 1000)) is None
    assert cost_usd("gpt-9-imaginary", TokenCount(1000, 1000)) is None


def test_an_unknown_model_gets_an_honest_label() -> None:
    assert label_for("gpt-5-mini") == "GPT-5 Mini"
    assert label_for(UNKNOWN_MODEL) == "نامشخص"


# --------------------------------------------------------------------------- signatures

_NEXT_LOG = """> next build
 ✓ Compiled successfully
Failed to compile.
./src/app/page.tsx:12:20
Type error: Property 'foo' does not exist on type 'Props'.
error Command failed with exit code 1."""

_NPM_LOG = """npm ERR! code ELIFECYCLE
npm ERR! Error: Cannot find module 'express'
npm ERR! Failed at the app@1.0.0 start script.
error Command failed with exit code 1."""

_PIP_LOG = """Collecting foo
ERROR: No matching distribution found for foo==9.9.9
error: subprocess-exited-with-error"""

_DISK_LOG = """writing files
ENOSPC: no space left on device, write
error Command failed with exit code 1."""

# The four fixtures above are 3-6 lines and end *on* the error, which is why they passed
# while every realistic log failed: a real PaaS log puts the cause in the middle and fills
# the tail with shutdown noise, a package manager's own footer, or — when the user pastes
# a log into a question — the user's own sentence. Everything below ends on that noise.

_GUNICORN_LOG = """2026-08-19T10:02:20.104Z  [INFO] Starting gunicorn 23.0.0
2026-08-19T10:02:20.141Z  [INFO] Using worker: uvicorn.workers.UvicornWorker
2026-08-19T10:02:20.612Z  [ERROR] Exception in worker process
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/gunicorn/arbiter.py", line 609, in spawn
    worker.init_process()
  File "/app/main.py", line 8, in <module>
    import redis
ModuleNotFoundError: No module named 'redis'
2026-08-19T10:02:20.700Z  [INFO] Worker exiting (pid: 7)
2026-08-19T10:02:21.010Z  [ERROR] Shutting down: Master
2026-08-19T10:02:21.011Z  [ERROR] Reason: Worker failed to boot."""

_NPM_MISSING_SCRIPT_LOG = """Step 6/9 : RUN npm run build
 ---> Running in 3c11ef88aa02
npm ERR! Missing script: "build"
npm ERR!
npm ERR! To see a list of scripts, run:
npm ERR!   npm run
npm ERR! A complete log of this run can be found in:
npm ERR!     /root/.npm/_logs/2026-08-19T09_12_44_120Z-debug-0.log
The command '/bin/sh -c npm run build' returned a non-zero code: 1
DEPLOY FAILED"""

_NODE_STACK_LOG = """node:events:497
      throw er; // Unhandled 'error' event
      ^

Error: listen EADDRINUSE: address already in use :::3000
    at Server.setupListenHandle [as _listen2] (node:net:1898:16)
    at listenInCluster (node:net:1955:12)
Emitted 'error' event on Server instance at:
    at emitErrorNT (node:net:1934:8)
Node.js v20.11.1"""

_DJANGO_STATIC_LOG = """2026-08-19T12:05:48.310Z  "GET / HTTP/1.1" 200 4821
2026-08-19T12:05:48.402Z  Not Found: /static/css/main.css
2026-08-19T12:05:48.403Z  "GET /static/css/main.css HTTP/1.1" 404 179
2026-08-19T12:05:49.100Z  WARNING You have not run collectstatic; static files \
will not be served."""

_PASTED_INTO_A_QUESTION = """سلام، برنامه‌ام روی لیارا بالا نمی‌آید. \
این آخرین چیزی است که در لاگ می‌بینم:
npm ERR! Missing script: "start"
لطفاً بگویید مشکل از کجاست و چه کار کنم؟"""


def test_the_closing_wrapper_line_never_wins() -> None:
    """The whole bug: every failed build ended with the same content-free line."""
    signature = extract_error_signature(_NEXT_LOG)
    assert "exit code" not in signature, signature
    assert signature.startswith("Type error: Property 'foo'"), signature


def test_a_package_manager_prefix_does_not_make_a_line_generic() -> None:
    """npm tags every line it prints, informative ones included."""
    signature = extract_error_signature(_NPM_LOG)
    assert "Cannot find module" in signature, signature


def test_an_already_good_signature_is_left_alone() -> None:
    signature = extract_error_signature(_PIP_LOG)
    assert signature.startswith("ERROR: No matching distribution"), signature


def test_a_disk_full_log_names_the_disk() -> None:
    """ENOSPC is the failure; the exit code that follows it is not."""
    assert "ENOSPC" in extract_error_signature(_DISK_LOG)


def test_shutdown_noise_after_the_error_does_not_win() -> None:
    """The signature is the only search query the wizard makes, so this one is the product.

    Every line after `ModuleNotFoundError` here is an error line by any pattern, and the
    last of them used to be the query — which retrieved WORKER TIMEOUT pages and made the
    model report a missing module as "not in the documentation".
    """
    signature = extract_error_signature(_GUNICORN_LOG)
    assert signature.startswith("ModuleNotFoundError: No module named"), signature


def test_a_package_manager_footer_never_outranks_what_it_reports() -> None:
    """`Missing script` is 7 lines from the end, behind npm's own boilerplate."""
    signature = extract_error_signature(_NPM_MISSING_SCRIPT_LOG)
    assert "Missing script" in signature, signature
    assert "DEPLOY FAILED" not in signature, signature


def test_a_stack_frame_is_never_the_signature() -> None:
    """`at emitErrorNT (…)` matched on the "Error" buried inside the identifier."""
    signature = extract_error_signature(_NODE_STACK_LOG)
    assert signature.startswith("Error: listen EADDRINUSE"), signature


def test_the_scrubbed_path_token_keeps_its_closing_bracket() -> None:
    """`>` was in the strip set, so a scrubbed «Not Found: <path>» reached the user cut."""
    signature = extract_error_signature(_DJANGO_STATIC_LOG)
    assert "collectstatic" in signature, signature
    assert signature.count("<") == signature.count(">"), signature


def test_a_log_pasted_into_a_question_searches_the_log_not_the_question() -> None:
    """The fallback used to take the last line, which for this user is what they asked."""
    signature = extract_error_signature(_PASTED_INTO_A_QUESTION)
    assert "Missing script" in signature, signature


def test_prose_with_no_error_line_yields_nothing_to_search() -> None:
    """Better the "send me the end of the log" note than searching the user's sentence."""
    assert extract_error_signature("سلام، سایتم بالا نمی‌آید. چه کار کنم؟") == ""


def test_an_all_generic_log_still_yields_something() -> None:
    """A vague signature beats telling the user no error was found."""
    assert extract_error_signature("building\nerror Command failed with exit code 1.")


def test_the_window_covers_the_whole_documented_input() -> None:
    """The old 40-line window was smaller than the 6000-char cap the endpoint accepts."""
    padded = "\n".join(["Requirement already satisfied: idna"] * 120)
    log = f"Error: pg_config executable not found.\n{padded}\nDEPLOY FAILED"
    assert len(log) < 6000, len(log)
    assert extract_error_signature(log).startswith("Error: pg_config"), log[:40]


def test_an_empty_log_is_empty() -> None:
    assert extract_error_signature("") == ""
    assert extract_error_signature("   \n  \n") == ""


def main() -> int:
    """Run every ``test_*`` in this module, reporting the first failure."""
    checks = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for name, check in checks:
        try:
            check()
        except AssertionError as exc:
            print(f"FAIL  {name}\n      {exc}")
            return 1
        print(f"ok  {name}")
    print(f"\n{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
