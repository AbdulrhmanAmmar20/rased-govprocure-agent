"""FR-2 - retrieval, citation and the refusal to answer ungrounded."""

from __future__ import annotations

import unittest

from app.config import get_settings
from app.core.exceptions import UngroundedAnswerError
from app.rag.embeddings import HashingEmbedder
from app.rag.retriever import (
    Retriever,
    extract_article_mentions,
    find_unsupported_citations,
)
from app.rag.store import InMemoryVectorStore, load_corpus


class TestCorpus(unittest.TestCase):
    def test_corpus_loads(self) -> None:
        chunks, meta = load_corpus()
        self.assertGreaterEqual(len(chunks), 10)
        self.assertEqual(meta["document_count"], len(chunks))

    def test_every_article_carries_a_citation(self) -> None:
        """FR-2.2 depends on every chunk being citable."""
        chunks, _ = load_corpus()
        for chunk in chunks:
            self.assertTrue(chunk.document.strip(), chunk.id)
            self.assertTrue(chunk.article.strip(), chunk.id)
            self.assertTrue(chunk.article_number.strip(), chunk.id)

    def test_article_ids_are_unique(self) -> None:
        chunks, _ = load_corpus()
        ids = [c.id for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_configured_ceiling_matches_its_stated_legal_basis(self) -> None:
        """The operational limit and the article it rests on must agree.

        If Article 34's ceiling is amended, config and corpus have to move
        together. This test is what makes that a build failure rather than a
        report that cites an article which no longer says what it claims.
        """
        chunks, _ = load_corpus()
        article = next(c for c in chunks if c.id == "IR-ART-34")
        self.assertEqual(
            article.thresholds["direct_purchase_ceiling_sar"],
            get_settings().direct_purchase_ceiling_sar,
        )

    def test_configured_local_content_matches_its_basis(self) -> None:
        chunks, _ = load_corpus()
        article = next(c for c in chunks if c.id == "IR-ART-96")
        self.assertAlmostEqual(
            article.thresholds["min_local_content_ratio"],
            get_settings().min_local_content_ratio,
            places=4,
        )


class TestRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        chunks, _ = load_corpus()
        store = InMemoryVectorStore(embedder=HashingEmbedder())
        store.add(chunks)
        cls.retriever = Retriever(store=store)

    def test_ceiling_query_finds_article_34(self) -> None:
        result = self.retriever.retrieve("سقف الشراء المباشر مئة ألف ريال")
        self.assertTrue(result.is_grounded)
        self.assertEqual(result.hits[0].chunk.id, "IR-ART-34")

    def test_local_content_query_finds_article_96(self) -> None:
        result = self.retriever.retrieve("نسبة المحتوى المحلي المطلوبة")
        self.assertEqual(result.hits[0].chunk.id, "IR-ART-96")

    def test_eligibility_query_finds_article_10(self) -> None:
        result = self.retriever.retrieve("شهادة الزكاة والتأمينات والسعودة للمورد")
        self.assertEqual(result.hits[0].chunk.id, "GTPL-ART-10")

    def test_context_block_carries_article_names(self) -> None:
        result = self.retriever.retrieve("سقف الشراء المباشر")
        self.assertIn("المادة", result.context_block())

    def test_refuses_when_nothing_is_grounded(self) -> None:
        """FR-2.3 - no legal basis means no opinion."""
        result = self.retriever.retrieve("وصفة الكبسة", min_score=0.99)
        self.assertFalse(result.is_grounded)
        with self.assertRaises(UngroundedAnswerError):
            self.retriever.require_grounding(result)


class TestCitationVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        chunks, _ = load_corpus()
        store = InMemoryVectorStore(embedder=HashingEmbedder())
        store.add(chunks)
        cls.retriever = Retriever(store=store)
        cls.result = cls.retriever.retrieve("تجاوز سقف الشراء المباشر")

    def test_extracts_ordinal_article_names(self) -> None:
        mentions = extract_article_mentions("وفق المادة الرابعة والثلاثون من اللائحة")
        self.assertTrue(any("الرابعه" in m or "الرابعة" in m for m in mentions))

    def test_stops_at_the_end_of_the_article_name(self) -> None:
        """The preposition after the name must not be absorbed into it."""
        mentions = extract_article_mentions("وفق المادة الرابعة والثلاثون من اللائحة")
        self.assertFalse(any(m.endswith("من") for m in mentions), mentions)

    def test_extracts_numeric_article_references(self) -> None:
        self.assertIn("34", extract_article_mentions("بموجب المادة 34"))

    def test_correct_citation_is_accepted(self) -> None:
        text = "تم رصد مخالفة للسقف وفق المادة الرابعة والثلاثون من اللائحة التنفيذية."
        self.assertEqual(find_unsupported_citations(text, self.result), set())

    def test_fabricated_citation_is_caught(self) -> None:
        """A model inventing an article number must not pass as sourced."""
        text = "المعاملة مخالفة بموجب المادة المئة والخمسون من النظام."
        self.assertTrue(find_unsupported_citations(text, self.result))


if __name__ == "__main__":
    unittest.main()
