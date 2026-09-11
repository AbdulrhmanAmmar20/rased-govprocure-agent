"""Index the regulation corpus into the configured vector store (FR-2.1).

Run after amending app/rag/corpus/gtpl.json, and in CI with --verify to catch
a corpus that would break grounding before it is deployed rather than when a
report comes back unciteable.

    python -m scripts.seed_regulations --verify
    python -m scripts.seed_regulations --backend chroma
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.rag.embeddings import HashingEmbedder
from app.rag.store import InMemoryVectorStore, build_store, load_corpus

# Queries a usable corpus must be able to answer, with the article each should
# return. These are the lookups the compliance rules actually depend on, so a
# corpus that fails one of them will produce ungrounded refusals in production.
SMOKE_QUERIES: tuple[tuple[str, str], ...] = (
    ("سقف الشراء المباشر مئة ألف ريال", "IR-ART-34"),
    ("نسبة المحتوى المحلي المطلوبة", "IR-ART-96"),
    ("شهادة الزكاة والتأمينات والسعودة", "GTPL-ART-10"),
    ("الاعتماد المالي المسبق في الميزانية", "GTPL-ART-20"),
    ("تجزئة المشتريات للتحايل على الحد المالي", "GTPL-ART-72"),
)


def validate_corpus() -> list[str]:
    """Structural checks. Returns a list of problems, empty when healthy."""
    chunks, metadata = load_corpus()
    problems: list[str] = []

    seen: set[str] = set()
    for chunk in chunks:
        if chunk.id in seen:
            problems.append(f"duplicate article id: {chunk.id}")
        seen.add(chunk.id)

        for field in ("document", "article", "article_number", "title", "text"):
            if not getattr(chunk, field, "").strip():
                problems.append(f"{chunk.id}: empty {field}")

        # FR-2.2 requires a citable article on every finding, so a chunk that
        # cannot be cited is worse than absent: it can ground an answer that
        # then has nothing to point at.
        if len(chunk.text) < 40:
            problems.append(f"{chunk.id}: text too short to be a usable basis")

    if metadata["source_status"] != "official":
        problems.append(
            f"corpus source_status is {metadata['source_status']!r}, not 'official' "
            "(informational; see docs/corpus-governance.md)"
        )
    return problems


def run_smoke_queries() -> list[str]:
    """Confirm each critical lookup still returns its expected article."""
    chunks, _ = load_corpus()
    store = InMemoryVectorStore(embedder=HashingEmbedder())
    store.add(chunks)

    failures: list[str] = []
    for query, expected in SMOKE_QUERIES:
        hits = store.search(query, top_k=1)
        if not hits:
            failures.append(f"{query!r} returned nothing (expected {expected})")
        elif hits[0].chunk.id != expected:
            failures.append(f"{query!r} returned {hits[0].chunk.id}, expected {expected}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed and validate the regulation corpus.")
    parser.add_argument("--verify", action="store_true", help="validate only, index nothing")
    parser.add_argument("--backend", default=None, help="override the configured vector backend")
    args = parser.parse_args(argv)

    chunks, metadata = load_corpus()
    print(f"corpus       : {metadata['corpus_version']} ({metadata['source_status']})")
    print(f"documents    : {len(chunks)}")

    problems = validate_corpus()
    # The seed-corpus notice is informational, not a failure - it would
    # otherwise make CI red for a condition the project ships in knowingly.
    blocking = [p for p in problems if "informational" not in p]
    for problem in problems:
        print(f"  ! {problem}")

    failures = run_smoke_queries()
    for failure in failures:
        print(f"  x retrieval: {failure}")
    print(f"smoke queries: {len(SMOKE_QUERIES) - len(failures)}/{len(SMOKE_QUERIES)} passed")

    if blocking or failures:
        print("\nFAILED")
        return 1

    if not args.verify:
        settings = get_settings()
        if args.backend:
            settings.vector_backend = args.backend
        store = build_store()
        store.add(chunks)
        print(f"indexed      : {store.count()} chunks into {type(store).__name__}")

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
