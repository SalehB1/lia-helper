"""Persian text normalization and tokenization shared by retrieval, caching and BM25.

The corpus is bilingual: Persian prose interleaved with latin CLI tokens
(``liara deploy --platform node``), JSON keys, version strings and URLs. Normalization
therefore only touches Arabic/Persian codepoints, digits and invisible formatting marks —
latin text, punctuation and URL structure are left byte-identical.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

from app.shared.fa_words import STOPWORDS, SYNONYMS

ZWNJ = "‌"

# Arabic/Persian letter unification. Targets are never themselves keys, which keeps
# normalize() idempotent.
_LETTER_MAP: dict[int, str] = {
    ord("ي"): "ی",  # U+064A arabic yeh
    ord("ى"): "ی",  # U+0649 alef maksura
    ord("ئ"): "ی",  # U+0626 yeh with hamza
    ord("ك"): "ک",  # U+0643 arabic kaf
    ord("ة"): "ه",  # U+0629 teh marbuta
    ord("أ"): "ا",
    ord("إ"): "ا",
    ord("آ"): "ا",
    ord("ٱ"): "ا",
    ord("ؤ"): "و",
}

# Tatweel, Arabic diacritics and invisible bidi/joiner marks are dropped outright.
_DELETE_CHARS: str = (
    "ـ"  # tatweel
    "ًٌٍَُِّْ"  # ً ٌ ٍ َ ُ ِ ّ ْ
    "ٰٕٓٔ"  # maddah / hamza above-below / superscript alef
    "​‍‎‏؜﻿"  # ZWSP, ZWJ, LRM, RLM, ALM, BOM
)

_DIGIT_MAP: dict[int, str] = {}
for _base in (0x0660, 0x06F0):  # Arabic-Indic ٠-٩ and Extended Arabic-Indic ۰-۹
    for _offset in range(10):
        _DIGIT_MAP[_base + _offset] = str(_offset)

_TRANSLATION: dict[int, str | None] = {
    **_LETTER_MAP,
    **_DIGIT_MAP,
    **{ord(_c): None for _c in _DELETE_CHARS},
}

_ZWNJ_RUN = re.compile(ZWNJ + "+")
# Any run of whitespace/ZWNJ containing at least one whitespace char collapses to a single
# space: this both drops ZWNJ adjacent to whitespace and collapses whitespace runs, in one
# pass, so no new " ZWNJ" adjacency can survive (which would break idempotency).
_SPACE_RUN = re.compile(r"[\s" + ZWNJ + r"]*\s[\s" + ZWNJ + r"]*")
_ZWNJ_EDGE = re.compile(r"^" + ZWNJ + r"+|" + ZWNJ + r"+$")

# Word characters (any script) plus ZWNJ, but only as an internal joiner: `پایگاه‌داده`
# stays one token while `Node.js` splits into `node` and `js`.
_TOKEN_RE = re.compile(r"[^\W_]+(?:" + ZWNJ + r"[^\W_]+)*")

# Productive Persian suffixes, longest first so `هایی` is never mistaken for `ها`+`یی`.
# The candidate set was plural + comparative + possessive (`شان تان مان تر ام ات اش` too);
# each of those was measured on tests/eval_retrieval and cut because it lost recall it did
# not buy back. Literal recall@1 over 55 questions: the full set 36, dropping `ام/ات/اش` 37,
# dropping `شان/تان/مان` as well 38 — the baseline. `تر` cost a paraphrase instead
# (`کلاستر`→`کلاس`, `کامپیوتر`→`کامپیو`: a bad cut landing on a *real* word is the one case
# additivity does not cover, because then it collides with something). What survives is
# unambiguous — nothing but a plural or a superlative ends this way with three letters left.
_SUFFIXES: tuple[str, ...] = ("ترین", "هایی", "های", "ها")
#: Below this, a "stem" is a fragment rather than a word.
_MIN_STEM = 3


def _stem(token: str) -> str:
    """Strip at most one Persian inflectional suffix from an already-normalized token.

    Never a replacement for the surface form — :func:`tokenize` emits both, so a wrong
    cut (``دیتاسنتر`` → ``دیتاسن``) can only add a term that the query side produces
    identically and that therefore collides with nothing real. No whitelist needed.

    Args:
        token: A lowercased token as produced by :func:`_surface_tokens`.

    Returns:
        The stem, or the token unchanged when no suffix applies.
    """
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
            return token[: -len(suffix)].rstrip(ZWNJ)
    return token


def normalize(text: str) -> str:
    """Normalize Persian text to a canonical, comparison-safe form.

    Applies NFC, unifies Arabic letter variants to Persian ones, strips tatweel,
    diacritics and invisible marks, converts Arabic-Indic digits to ASCII, tidies ZWNJ
    and collapses whitespace runs. Latin words, digits inside version strings and URL
    structure are preserved.

    Args:
        text: Raw text, possibly empty or bilingual.

    Returns:
        The normalized text. The function is idempotent:
        ``normalize(normalize(x)) == normalize(x)``.
    """
    if not text:
        return ""
    result = unicodedata.normalize("NFC", text).translate(_TRANSLATION)
    result = _ZWNJ_RUN.sub(ZWNJ, result)
    result = _SPACE_RUN.sub(" ", result).strip()
    return _ZWNJ_EDGE.sub("", result)


def _surface_tokens(text: str) -> Iterator[str]:
    """Yield the kept surface tokens of ``text`` — no stemming, no synonyms."""
    for match in _TOKEN_RE.finditer(normalize(text)):
        token = match.group(0).lower().strip(ZWNJ)
        if len(token) < 2 or token in STOPWORDS:
            continue
        yield token


def tokenize(text: str) -> list[str]:
    """Split text into normalized BM25 terms.

    Latin/technical tokens (``nodejs``, ``postgres``) are kept whole and lowercased;
    ``liara.json`` yields ``liara`` and ``json``. Stopwords and single-character tokens
    are dropped. Stemming is **additive**: a token whose stem differs contributes both
    forms, so `دیسک‌های` in a page still matches a query saying `دیسک` without the surface
    match ever being weakened. Used on both the query and the indexing side, which is what
    makes the two vocabularies identical.

    Args:
        text: Raw text in any of the corpus languages.

    Returns:
        The kept tokens, in order of appearance (duplicates included).
    """
    tokens: list[str] = []
    for token in _surface_tokens(text):
        tokens.append(token)
        stem = _stem(token)
        if stem != token and stem not in STOPWORDS:
            tokens.append(stem)
    return tokens


def char_ngrams(text: str, n: int = 4) -> list[str]:
    """Split text into character n-grams of its surface tokens.

    Sub-word matching for morphology and typos: `وبسرویس`/`وب‌سرویس` and `دیتابیس`/
    `دیتابیس‌ها` share most of their 4-grams. It bridges nothing across scripts — `داکر`
    and `docker` share no n-gram at all; that is :data:`app.shared.fa_words.SYNONYMS`' job.

    Args:
        text: Raw text in any of the corpus languages.
        n: Window width. Tokens shorter than it are emitted whole.

    Returns:
        The n-grams, in order of appearance (duplicates included).
    """
    grams: list[str] = []
    for token in _surface_tokens(text):
        if len(token) < n:
            grams.append(token)
        else:
            grams.extend(token[start : start + n] for start in range(len(token) - n + 1))
    return grams


def expand_query(tokens: list[str]) -> list[str]:
    """Append domain synonyms for any token present in the query.

    BM25 side only — embeddings must always see the untouched query.

    Args:
        tokens: Output of :func:`tokenize`.

    Returns:
        The original tokens followed by every new synonym alternate, de-duplicated.
    """
    expanded: list[str] = list(tokens)
    seen: set[str] = set(tokens)
    for token in tokens:
        for alternate in SYNONYMS.get(token, ()):
            if alternate not in seen:
                seen.add(alternate)
                expanded.append(alternate)
    return expanded
