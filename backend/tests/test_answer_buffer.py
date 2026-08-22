"""Self-check for ``_AnswerBuffer`` across the multi-round agent loop.

Retrieval now happens only through tool calls, so a technical question always produces at
least two assistant rounds: one that calls ``search_docs`` and one that answers. Prompt
rule ۸ tells the model to end an answer with the ``@@@`` suggestions line, and models do
emit it early — which used to set ``_found`` for the rest of the turn and divert the whole
real answer into the suggestions tail.

A round that ends in tool calls is now normally DROPPED rather than released: its text is a
preamble, and holding the first released byte back until the real answer starts is what lets
a turn switch models without ever retracting anything. The one exception is a preamble that
already reached the client, which cannot be unsent and is kept and separated instead.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_answer_buffer
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.services.chat_service import _AnswerBuffer  # noqa: E402
from app.shared.constants import SUGGESTION_MARKER  # noqa: E402

ANSWER = "برای استقرار Node.js فایل liara.json بساز [1]."


def _round(buffer: _AnswerBuffer, text: str) -> str:
    """Feed one round's text through the buffer and return what reached the client."""
    return buffer.feed(text)


def _tool_round(buffer: _AnswerBuffer, text: str, drop: bool = True) -> str:
    """Run one round that ends in a tool call, the way ``_attempt`` does."""
    mark = buffer.begin_round()
    buffer.feed(text)
    return buffer.end_round(mark, drop=drop)


def test_marker_in_preamble_does_not_swallow_the_answer() -> None:
    """A ``@@@`` line in a tool round must not divert the next round's answer."""
    buffer = _AnswerBuffer()
    _tool_round(buffer, f"بررسی می‌کنم.\n{SUGGESTION_MARKER} الف | ب | ج")
    _round(buffer, ANSWER)
    buffer.flush()
    assert ANSWER in buffer.text, buffer.text
    assert SUGGESTION_MARKER not in buffer.text, buffer.text


def test_bare_marker_round_still_answers() -> None:
    """A tool round that is nothing but the marker must not empty the turn."""
    buffer = _AnswerBuffer()
    _tool_round(buffer, f"{SUGGESTION_MARKER} الف | ب | ج")
    _round(buffer, ANSWER)
    buffer.flush()
    assert buffer.text.strip() == ANSWER, buffer.text


def test_preamble_is_dropped_so_the_answer_starts_the_stream() -> None:
    """Nothing released yet: the preamble leaves no trace, in the buffer or on the wire.

    This is the property model escalation rests on — while no answer byte has been sent the
    turn is still free to start over on another model.
    """
    buffer = _AnswerBuffer()
    released = _tool_round(buffer, "الان جست‌وجو می‌کنم.")
    _round(buffer, ANSWER)
    buffer.flush()
    assert released == "", released
    assert "جست‌وجو می‌کنم" not in buffer.text, buffer.text
    assert buffer.text.strip() == ANSWER, buffer.text


def test_a_preamble_already_sent_is_kept_and_separated() -> None:
    """What the client has already seen cannot be unsent, so it stays and gets a gap."""
    buffer = _AnswerBuffer()
    preamble = "الان جست‌وجو می‌کنم."
    released = _tool_round(buffer, preamble, drop=False)
    _round(buffer, ANSWER)
    buffer.flush()
    assert "می‌کنم.برای" not in buffer.text, buffer.text
    assert released.startswith(preamble), released
    # Everything the client was sent is exactly what is persisted.
    assert buffer.text == released + ANSWER, buffer.text


def test_final_round_suggestions_still_parse() -> None:
    """The suggestions of the last round survive an earlier round's reset."""
    buffer = _AnswerBuffer()
    _tool_round(buffer, "می‌گردم.")
    _round(buffer, f"{ANSWER}\n{SUGGESTION_MARKER} اتصال دامنه | تنظیم دیسک")
    buffer.flush()
    assert buffer.suggestions == ["اتصال دامنه", "تنظیم دیسک"], buffer.suggestions
    assert SUGGESTION_MARKER not in buffer.text, buffer.text


def test_single_round_answer_is_unchanged() -> None:
    """The no-tool path (a greeting) behaves exactly as before."""
    buffer = _AnswerBuffer()
    _round(buffer, f"سلام! چطور می‌توانم کمک کنم؟\n{SUGGESTION_MARKER} الف | ب")
    buffer.flush()
    assert buffer.text.strip() == "سلام! چطور می‌توانم کمک کنم؟", buffer.text
    assert buffer.suggestions == ["الف", "ب"], buffer.suggestions


def main() -> int:
    """Run every check and report."""
    checks = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        check()
        print(f"ok  {check.__name__}")
    print(f"\n{len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
