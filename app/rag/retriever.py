"""Retrieval with citation enforcement (FR-2.1, FR-2.2, FR-2.3).

Retrieval here is not just a context-fetching step; it is the gate that
decides whether the agent is permitted to answer at all. FR-2.3 is emphatic:
if there is no legal basis, the agent refuses. So this module owns three
responsibilities the prompt cannot be trusted with —

1. fetch the candidate articles,
2. decide whether what came back is strong enough to ground an answer,
3. check afterwards that the answer cited only what was actually retrieved.

Step 3 is the one that catches the failure mode that matters. A model asked
for an article number will happily supply a plausible one. Checking the
produced citations against the retrieved set turns that from a silent error
into a caught one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.audit.models import Citation
from app.config import get_settings
from app.core.exceptions import UngroundedAnswerError
from app.core.logging import get_logger
from app.rag.store import SearchHit, VectorStore, build_store, load_corpus
from app.pii.validators import normalize_arabic, normalize_digits

logger = get_logger("rased.rag.retriever")

_ARTICLE_LEAD = re.compile(r"المادة\s*\(?\s*")
_DIGIT_RUN = re.compile(r"[\d]{1,3}")
_WORD = re.compile(r"[ء-ٕٱ-ۓ]+")

# Arabic ordinals as they appear in article names. The mention parser consumes
# words only while they belong to this vocabulary, which is what stops it from
# swallowing the preposition that follows — an earlier pattern that took "up to
# three more words" turned «المادة الرابعة والثلاثون من اللائحة» into the
# article name "المادة الرابعة والثلاثون من", so a correctly cited report was
# reported as carrying a fabricated citation.
_ORDINAL_STEMS = frozenset(
    {
        "اولي", "ثانيه", "ثانيه", "ثالثه", "رابعه", "خامسه", "سادسه", "سابعه",
        "ثامنه", "تاسعه", "عاشره", "حاديه", "ثانيه", "عشره", "عشر",
        "عشرون", "عشرين", "ثلاثون", "ثلاثين", "اربعون", "اربعين",
        "خمسون", "خمسين", "ستون", "ستين", "سبعون", "سبعين",
        "ثمانون", "ثمانين", "تسعون", "تسعين", "مئه", "مائه", "مئتان", "مئتين",
        "الف", "اولي",
    }
)


def _ordinal_stem(word: str) -> str:
    """Fold a word to the form the ordinal vocabulary is written in."""
    folded = normalize_arabic(word)
    if folded.startswith("و"):
        folded = folded[1:]
    if folded.startswith("ال"):
        folded = folded[2:]
    return folded.replace("ة", "ه")


def _is_ordinal_word(word: str) -> bool:
    return _ordinal_stem(word) in _ORDINAL_STEMS


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    hits: tuple[SearchHit, ...]
    is_grounded: bool
    top_score: float

    def citations(self) -> list[Citation]:
        return [
            Citation(
                document=hit.chunk.document,
                article=hit.chunk.article,
                excerpt=hit.chunk.text[:240],
                score=round(hit.score, 4),
            )
            for hit in self.hits
        ]

    def context_block(self) -> str:
        """The retrieved articles, formatted for the prompt.

        Each passage is labelled with the exact citation string the agent is
        required to reproduce, so citing correctly is the path of least effort
        rather than an instruction the model has to remember.
        """
        parts: list[str] = []
        for index, hit in enumerate(self.hits, start=1):
            parts.append(
                f"[مرجع {index}]\n"
                f"المستند: {hit.chunk.document}\n"
                f"المادة: {hit.chunk.article} ({hit.chunk.article_number})\n"
                f"العنوان: {hit.chunk.title}\n"
                f"النص: {hit.chunk.text}"
            )
        return "\n\n".join(parts)

    def allowed_articles(self) -> set[str]:
        allowed: set[str] = set()
        for hit in self.hits:
            allowed.add(normalize_arabic(hit.chunk.article))
            allowed.add(normalize_digits(hit.chunk.article_number))
        return allowed


class Retriever:
    """Wraps a vector store with the grounding rules FR-2 imposes."""

    def __init__(self, store: VectorStore | None = None) -> None:
        if store is None:
            store = build_store()
            chunks, self.corpus_metadata = load_corpus()
            store.add(chunks)
        else:
            _, self.corpus_metadata = load_corpus()
        self.store = store

    def retrieve(self, query: str, *, top_k: int | None = None, min_score: float | None = None) -> RetrievalResult:
        settings = get_settings()
        top_k = top_k if top_k is not None else settings.retrieval_top_k
        min_score = min_score if min_score is not None else settings.retrieval_min_score

        hits = [h for h in self.store.search(query, top_k) if h.score >= min_score]
        top_score = hits[0].score if hits else 0.0

        logger.info(
            "regulation retrieval",
            extra={"hit_count": len(hits), "top_score": round(top_score, 4)},
        )
        return RetrievalResult(
            query=query,
            hits=tuple(hits),
            is_grounded=bool(hits),
            top_score=top_score,
        )

    def require_grounding(self, result: RetrievalResult) -> None:
        """FR-2.3 — refuse rather than answer without a legal basis."""
        if not result.is_grounded:
            raise UngroundedAnswerError(
                "تعذر العثور على سند نظامي في قاعدة اللوائح لهذا الاستفسار، "
                "ولا يجوز إصدار رأي نظامي دون سند. يرجى إحالة الحالة للمدقق النظامي.",
                query_top_score=result.top_score,
            )


def extract_article_mentions(text: str) -> set[str]:
    """Every article reference the generated text claims, in folded form.

    After the word المادة, ordinal words are consumed for as long as they are
    ordinals and no further, so the reference ends where the article name ends
    rather than running into the rest of the sentence.
    """
    mentions: set[str] = set()

    for lead in _ARTICLE_LEAD.finditer(text):
        cursor = lead.end()

        digits = _DIGIT_RUN.match(text, cursor)
        if digits:
            mentions.add(normalize_digits(digits.group(0)))
            continue

        words: list[str] = []
        while True:
            word_match = _WORD.match(text, cursor)
            if not word_match or not _is_ordinal_word(word_match.group(0)):
                break
            words.append(word_match.group(0))
            cursor = word_match.end()
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1

        if words:
            mentions.add(normalize_arabic("المادة " + " ".join(words)))

    return mentions


def find_unsupported_citations(text: str, result: RetrievalResult) -> set[str]:
    """Article references in ``text`` that were not in the retrieved set.

    A non-empty return means the model invented a citation, which under FR-2.3
    must not reach a reviewer as though it were sourced.
    """
    allowed = result.allowed_articles()
    claimed = extract_article_mentions(text)
    return {mention for mention in claimed if mention not in allowed}
