"""When to stop answering and point the user at a human.

A documentation assistant can be perfectly correct and still be no use: the page it cites
does not cover this deployment, and the user says so, and says so again. Past some number of
those the helpful move is not another answer — it is telling them where a person is. That
number, whether the policy runs at all, the wording of the invitation and the wording it
triggers on are all the operator's to set; none of them is knowable from here.

**Not to be confused with escalation.** In this codebase *escalation* is the model ladder —
`chat_service._Escalate`, `AgentSettings.escalation`, `tests/test_escalation` — a silent
retry on the next model that the user must never learn happened. This is the opposite: a
deliberate, visible handoff out of the assistant. The two never share a name.

The detection is deliberately a substring match over a phrase list rather than a sentiment
model. It runs on every turn, it must never be the reason an answer is slow, and an operator
has to be able to read it and predict it — «why did it offer support here and not there» is
a question a list answers and a classifier does not.
"""

from __future__ import annotations

from app.shared.enums import MessageRole
from app.shared.persian import ZWNJ, normalize

#: Phrases shorter than this are dropped from the list. A two-character entry matches inside
#: unrelated words — «بد» is in «بدون» — and one over-broad phrase silently turns the policy
#: into "always on", which reads as the assistant nagging every user about support tickets.
MIN_PHRASE_CHARS = 4


def _flat(text: str) -> str:
    """Normalize for matching: Arabic letter forms, Persian digits, and ZWNJ as a space.

    The same treatment ``_claims_limit`` gives an answer before screening it, for the same
    reason — «مشكلم» typed with an Arabic ك and «مشکلم» with a Persian one are one word to a
    user and two strings to Python.
    """
    return normalize(text or "").replace(ZWNJ, " ")


def phrases(text: str) -> tuple[str, ...]:
    """Parse the operator's phrase list, one per line.

    Args:
        text: The stored ``handoff_phrases`` text.

    Returns:
        Normalized phrases, blanks and too-short entries dropped, deduped. Empty when nothing
        usable survives — which the caller must read as "never fires", not as "always fires".
    """
    seen: list[str] = []
    for line in text.splitlines():
        phrase = _flat(line).strip()
        if len(phrase) >= MIN_PHRASE_CHARS and phrase not in seen:
            seen.append(phrase)
    return tuple(seen)


def _is_unhappy(text: str, needles: tuple[str, ...]) -> bool:
    """Whether one message carries any of the phrases."""
    flat = _flat(text)
    return any(needle in flat for needle in needles)


def unhappy_messages(history: list[dict], question: str, needles: tuple[str, ...]) -> int:
    """Count the user's unhappy messages in this branch, including the one just asked.

    Args:
        history: The branch window already loaded for this turn, in provider wire shape.
        question: The question being answered. It is already the last row of ``history`` —
            the user's message is persisted before the answer starts — so it is counted from
            there and not again.
        needles: Normalized phrases from :func:`phrases`.

    Returns:
        How many user messages in the window match. Counts over the window rather than the
        whole conversation, which is why the threshold is capped at 5: ``HISTORY_WINDOW`` is
        10 messages, so five user turns is everything this can ever see.
    """
    if not needles:
        return 0
    return sum(
        1
        for message in history
        if message.get("role") == MessageRole.USER.value
        and _is_unhappy(str(message.get("content", "")), needles)
    )


def triggered(cfg, texts, history: list[dict], question: str) -> bool:
    """Whether this turn should invite the user to contact a human.

    Args:
        cfg: The effective :class:`AgentSettings`.
        texts: The effective :class:`Prompts`.
        history: The branch window loaded for this turn.
        question: The question being answered.

    Returns:
        True when the policy is on and the user has been unhappy at least ``handoff_after``
        times in this branch. Fails closed on an empty or unusable phrase list.
    """
    if not cfg.handoff_enabled:
        return False
    return unhappy_messages(history, question, phrases(texts.handoff_phrases)) >= cfg.handoff_after
