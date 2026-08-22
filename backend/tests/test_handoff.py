"""Self-check for the handoff policy: when a stuck user gets pointed at a human.

Not to be confused with ``test_escalation``, which covers the *model ladder* — a silent
retry the user must never learn about. This is the opposite move, and the four things
asserted here are the ones that would fail quietly:

* the threshold means what it says — one complaint is not "repeatedly";
* the phrase list survives the spelling a real user types, Arabic ك and all;
* an empty or unusable phrase list turns the policy OFF, never on;
* the compiled invitation cannot be eaten by ``_claims_limit`` — a text that trips the
  never-say-limit guard would escalate the whole ladder and deliver nothing, which looks
  from the outside exactly like the policy being switched off.

No network and no database: everything here is pure functions over a message window.

Usage (from ``backend/``)::

    .venv-uv/bin/python -m tests.test_handoff
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.services import handoff  # noqa: E402
from app.domain.services.agent_settings import BOUNDS, _defaults  # noqa: E402
from app.domain.services.chat_service import _claims_limit  # noqa: E402
from app.domain.services.prompt_settings import _defaults as _prompt_defaults  # noqa: E402
from app.shared.constants import HISTORY_WINDOW  # noqa: E402

ON = dataclasses.replace(_defaults(), handoff_enabled=True, handoff_after=2)
TEXTS = _prompt_defaults()


def _branch(*turns: str) -> list[dict]:
    """A branch window: each user message followed by an answer, as the model sees it."""
    window: list[dict] = []
    for text in turns:
        window.append({"role": "user", "content": text})
        window.append({"role": "assistant", "content": "پاسخ."})
    return window


def test_one_complaint_is_not_repeatedly() -> None:
    """The whole point of the threshold: a first "it didn't work" is a normal turn."""
    branch = _branch("چطور دیتابیس بسازم؟", "جواب نداد")
    assert handoff.unhappy_messages(branch, "جواب نداد", handoff.phrases(TEXTS.handoff_phrases)) == 1
    assert not handoff.triggered(ON, TEXTS, branch, "جواب نداد")


def test_the_second_one_fires_it() -> None:
    branch = _branch("چطور دیتابیس بسازم؟", "جواب نداد", "باز هم همون خطا")
    assert handoff.triggered(ON, TEXTS, branch, "باز هم همون خطا")


def test_the_policy_is_off_until_an_operator_turns_it_on() -> None:
    """A deploy must not start telling users to open tickets on its own."""
    assert _defaults().handoff_enabled is False
    branch = _branch("سلام", "حل نشد", "بازم حل نشد")
    assert not handoff.triggered(_defaults(), TEXTS, branch, "بازم حل نشد")


def test_a_user_typing_arabic_letters_still_counts() -> None:
    """«مشكلم» with an Arabic ك is one word to the person typing it and two strings to us."""
    branch = _branch("سلام", "مشكلم حل نشد", "هنوز مشكل دارم")
    assert handoff.triggered(ON, TEXTS, branch, "هنوز مشكل دارم")


def test_an_emptied_phrase_list_switches_the_policy_off() -> None:
    """Fail closed. An operator who clears the box must not get an invitation on every turn."""
    blank = dataclasses.replace(TEXTS, handoff_phrases="   \n\n  ")
    branch = _branch("سلام", "حل نشد", "بازم حل نشد")
    assert handoff.phrases(blank.handoff_phrases) == ()
    assert not handoff.triggered(ON, blank, branch, "بازم حل نشد")


def test_a_too_short_phrase_cannot_match_every_message() -> None:
    """One over-broad entry would turn the policy into "always on", which reads as nagging."""
    loose = dataclasses.replace(TEXTS, handoff_phrases="بد\nا\nحل نشد")
    assert handoff.phrases(loose.handoff_phrases) == ("حل نشد",)


def test_the_assistant_never_repeats_the_question_itself() -> None:
    """The question is already the last row of the window; counting it twice would halve
    the effective threshold without anything on screen saying so."""
    branch = _branch("سلام", "حل نشد")
    needles = handoff.phrases(TEXTS.handoff_phrases)
    assert handoff.unhappy_messages(branch, "حل نشد", needles) == 1


def test_the_invitation_cannot_be_swallowed_by_the_never_say_limit_guard() -> None:
    """The one failure mode that looks identical to the feature being off.

    The invitation can reach a user's screen verbatim on the ladder-exhausted path, and the
    model is asked to paraphrase it everywhere else. A wording that trips `_claims_limit`
    would be discarded, the turn would escalate through every rung, and no invitation would
    ever arrive — with nothing in the log naming the invitation as the cause.
    """
    assert not _claims_limit(TEXTS.handoff)
    assert not _claims_limit(TEXTS.handoff_phrases)


def test_the_threshold_can_never_outrun_the_history_window() -> None:
    """`unhappy_messages` counts over the loaded window, so a threshold larger than the
    number of user turns that window can hold would be unreachable — the policy would look
    switched on and never fire."""
    low, high = BOUNDS["handoff_after"]
    assert low >= 2, "below 2 the policy could fire on a cacheable first turn"
    assert high <= HISTORY_WINDOW // 2, (high, HISTORY_WINDOW)


def main() -> None:
    """Run every check in this module."""
    checks = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        check()
        print("ok ", check.__name__)
    print(f"\n{len(checks)} checks passed")


if __name__ == "__main__":
    main()
