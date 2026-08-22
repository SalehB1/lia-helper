"""Self-check for the prompt invariants that decide whether the model abstains.

The assistant answered «به من یک منیفست استقرار برای fastapi میدی؟» with «در مستندات لیارا
پاسخ این پرسش را پیدا نکردم» and then listed, as «نزدیک‌ترین موضوع‌ها», the very pages that
hold the answer — measured at 1 turn in 10 before this contract existed. Two prompt facts
caused it: ``generate_config`` and ``diagnose_log`` were never named in ``SYSTEM_PROMPT``
even though both ride in every tool payload, and ``FINAL_ROUND_INSTRUCTION`` reprinted the
rule ۵ abstention template inside the one call that writes the answer — the call that has no
tools left and therefore no way to do anything except take the exit it was just handed.

Nothing here checks wording. It checks that a tool the model is given is a tool the model is
told about, and that the closing call is not re-offered the abstention it exists to avoid.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_prompt_contract
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.services import prompt_settings, prompts  # noqa: E402
from app.domain.services.tools_service import TOOL_SCHEMAS  # noqa: E402

#: The canonical abstention wording, owned by rule ۵ and by nothing else.
ABSTENTION = "پیدا نکردم"

#: Ruff's line length, which the prompt text shares with the module holding it.
MAX_LINE = 100

#: The three editable texts, keyed as ``prompt_settings`` keys them.
_TEXTS = {
    "system": prompts.SYSTEM_PROMPT,
    "final_round": prompts.FINAL_ROUND_INSTRUCTION,
    "wizard": prompts.WIZARD_SYSTEM_PROMPT,
}


def _flat(text: str) -> str:
    """Collapse whitespace, so a fragment survives being rewrapped."""
    return re.sub(r"\s+", " ", text)


def test_every_tool_in_the_payload_is_named_in_the_prompt() -> None:
    """A tool the model is handed but never told about is a tool it will not call."""
    prompt = prompts.build_system_prompt(None, "")
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        assert name in prompt, f"{name} is in the tool payload but not in SYSTEM_PROMPT"


def test_the_closing_call_is_not_handed_the_abstention_template() -> None:
    """``FINAL_ROUND_INSTRUCTION`` must point at rule ۵, never restate it.

    The closing call carries no tools, so whatever it is told to do is the only thing it
    can do. Quoting the abstention sentence there makes abstaining the salient exit at the
    exact moment the answer is written.
    """
    assert ABSTENTION not in prompts.FINAL_ROUND_INSTRUCTION, prompts.FINAL_ROUND_INSTRUCTION
    assert "بند ۵" in prompts.FINAL_ROUND_INSTRUCTION, "the pointer to rule ۵ went missing"


def test_rule_five_still_forbids_inventing_facts() -> None:
    """Lowering the abstention rate must never raise the invention rate.

    Whitespace-normalized, because «از خودت نساز» sits across a line break in the source and
    a literal match here would refuse a legal reflow of the very sentence it protects.
    """
    flat = _flat(prompts.SYSTEM_PROMPT)
    for fragment in ("فلگ CLI", "کلید پیکربندی", "از خودت نساز"):
        assert fragment in flat, fragment


def test_the_defaults_satisfy_the_contract_an_operator_is_held_to() -> None:
    """The prompts are editable from the panel, and this is the table that gate enforces.

    Both directions matter. Someone editing ``prompts.py`` who drops a rule fails here rather
    than shipping it; someone editing the *table* to make their edit pass shows a reviewer a
    diff that deletes a safety rule, which is the only thing that keeps the list honest.
    """
    for key, text in _TEXTS.items():
        assert prompt_settings.check(key, text) == [], key
    assert set(prompts.REQUIRED_FRAGMENTS) <= set(prompt_settings.PROMPT_KEYS)
    assert set(prompts.FORBIDDEN_FRAGMENTS) <= set(prompt_settings.PROMPT_KEYS)


def test_the_prompt_is_a_valid_f_string_and_fits_the_line_length() -> None:
    """A stray brace is a KeyError at import; a long line is a ruff failure."""
    for name, text in (
        ("SYSTEM_PROMPT", prompts.SYSTEM_PROMPT),
        ("FINAL_ROUND_INSTRUCTION", prompts.FINAL_ROUND_INSTRUCTION),
        ("WIZARD_SYSTEM_PROMPT", prompts.WIZARD_SYSTEM_PROMPT),
    ):
        # The f-string is already interpolated by import time, so an unbalanced brace here
        # is one the author wrote as data rather than as a placeholder.
        assert "{" not in text and "}" not in text, f"{name} carries a literal brace"
        longest = max(len(line) for line in text.splitlines())
        assert longest <= MAX_LINE, f"{name} has a {longest}-char line"


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
