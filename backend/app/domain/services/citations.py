"""Per-answer citation numbering.

A registry is created for each answer. Every chunk placed in front of the model gets a
stable 1-based number, and after the answer is produced the `[n]` markers it actually used
are mapped back to the source list shown to the user.
"""

from __future__ import annotations

import re

from app.domain.services.retrieval_service import Chunk
from app.shared.persian import normalize

# Matches a bracketed citation: [3], [1, 2], [۱٬۲]. Persian/Arabic digits and separators
# are folded to ASCII by normalize() before this runs.
_MARKER_RE = re.compile(r"\[\s*(\d{1,3}(?:\s*[,،؛;]\s*\d{1,3})*)\s*\]")
_NUMBER_RE = re.compile(r"\d{1,3}")


class CitationRegistry:
    """Assigns and resolves the `[n]` source numbers of a single answer."""

    def __init__(self) -> None:
        self._numbers: dict[str, int] = {}
        self._sources: list[dict] = []

    def assign(self, chunk: Chunk) -> int:
        """Return the citation number of a chunk, assigning a new one on first sight.

        Args:
            chunk: The chunk about to be shown to the model.

        Returns:
            Its 1-based citation number; the same chunk id always maps to the same number.
        """
        existing = self._numbers.get(chunk.id)
        if existing is not None:
            return existing
        number = len(self._sources) + 1
        self._numbers[chunk.id] = number
        self._sources.append(
            {
                "n": number,
                "title": chunk.title,
                "url": chunk.url,
                "heading": chunk.heading or None,
            }
        )
        return number

    def all_sources(self) -> list[dict]:
        """Return every assigned source, in citation order."""
        return list(self._sources)

    def used_sources(self, answer: str) -> list[dict]:
        """Return the sources the answer actually cited.

        Parses `[n]` markers, including runs such as ``[1][2]``, lists such as ``[1, 2]``
        and Persian-digit markers such as ``[۱]``.

        An answer that cites nothing returns nothing. Showing every retrieved source under
        an uncited answer used to be a courtesy; now that abstention is a prompt rule
        rather than a pre-retrieval gate, "the search ran but the answer is not grounded in
        it" is the ordinary shape of an abstention — and a row of real docs.liara.ir chips
        under «پاسخ این پرسش را پیدا نکردم» claims a grounding that is not there.

        Args:
            answer: The assistant's full answer text.

        Returns:
            The cited sources ordered by citation number; empty when the answer cited none.
        """
        if not self._sources:
            return []
        cited: set[int] = set()
        for match in _MARKER_RE.finditer(normalize(answer or "")):
            for number in _NUMBER_RE.findall(match.group(1)):
                value = int(number)
                if 1 <= value <= len(self._sources):
                    cited.add(value)
        return [source for source in self._sources if source["n"] in cited]

    @property
    def count(self) -> int:
        """How many sources have been assigned."""
        return len(self._sources)
