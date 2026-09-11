"""FR-1 - masking, the vault, and the latency budget."""

from __future__ import annotations

import unittest

from app.config import get_settings
from app.core.exceptions import AuthorizationError, MaskingError
from app.core.rbac import Role
from app.pii.disclosure import reveal
from app.pii.engine import MaskingEngine
from app.pii.entities import EntityType
from app.pii.vault import SessionVault
from app.pii.validators import is_valid_iban, is_valid_saudi_id, normalize_arabic
from tests.support import SCENARIO_CR, SCENARIO_REQUEST, principal, temp_trail, valid_saudi_id


class TestValidators(unittest.TestCase):
    def test_national_id_checksum(self) -> None:
        self.assertTrue(is_valid_saudi_id(valid_saudi_id()))
        self.assertFalse(is_valid_saudi_id("1234567890"))
        self.assertFalse(is_valid_saudi_id("123"))

    def test_cr_number_is_not_a_national_id(self) -> None:
        """The scenario's CR must never be classified as an identity number."""
        self.assertFalse(is_valid_saudi_id(SCENARIO_CR))

    def test_iban_mod97(self) -> None:
        self.assertTrue(is_valid_iban("SA0380000000608010167519"))
        self.assertTrue(is_valid_iban("SA03 8000 0000 6080 1016 7519"))
        self.assertFalse(is_valid_iban("SA0380000000608010167518"))

    def test_arabic_folding(self) -> None:
        self.assertEqual(normalize_arabic("عرضاً"), normalize_arabic("عرضا"))


class TestMasking(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MaskingEngine()

    def test_scenario_cr_is_masked(self) -> None:
        """SRS section 6 step 1 - the CR is replaced by <CR_NUM_1>."""
        result = self.engine.mask(SCENARIO_REQUEST, session_id="t-cr")
        self.assertIn("<CR_NUM_1>", result.masked_text)
        self.assertNotIn(SCENARIO_CR, result.masked_text)

    def test_identity_number_is_masked(self) -> None:
        text = f"هوية المفوض {valid_saudi_id()} مرفقة."
        result = self.engine.mask(text, session_id="t-id")
        self.assertIn("<SAUDI_ID_1>", result.masked_text)

    def test_arabic_indic_digits_are_masked(self) -> None:
        """An identifier written in Arabic numerals must not slip through."""
        result = self.engine.mask("السجل التجاري ١٠١٠٩٩٨٨٧٧", session_id="t-ar")
        self.assertIn("<CR_NUM_1>", result.masked_text)
        self.assertNotIn("١٠١٠٩٩٨٨٧٧", result.masked_text)

    def test_same_value_reuses_placeholder(self) -> None:
        text = f"السجل {SCENARIO_CR} ثم السجل {SCENARIO_CR} مرة أخرى."
        result = self.engine.mask(text, session_id="t-reuse")
        self.assertEqual(result.masked_text.count("<CR_NUM_1>"), 2)
        self.assertNotIn("<CR_NUM_2>", result.masked_text)

    def test_round_trip_is_lossless(self) -> None:
        result = self.engine.mask(SCENARIO_REQUEST, session_id="t-rt")
        self.assertEqual(
            self.engine.unmask(result.masked_text, session_id="t-rt"), SCENARIO_REQUEST
        )

    def test_masking_meets_latency_budget(self) -> None:
        """NFR-2.1 - under 150ms for a single request."""
        budget = get_settings().pii_latency_budget_ms
        result = self.engine.mask(SCENARIO_REQUEST * 5, session_id="t-perf")
        self.assertLessEqual(result.duration_ms, budget, f"took {result.duration_ms:.1f}ms")

    def test_entity_types_detected(self) -> None:
        text = (
            f"المهندس خالد العتيبي، هوية {valid_saudi_id()}، جوال 0551234567، "
            f"بريد a.k@example.com، سجل تجاري {SCENARIO_CR}، "
            "آيبان SA0380000000608010167519"
        )
        found = {e.entity_type for e in self.engine.detect(text)}
        for expected in (
            EntityType.SAUDI_ID,
            EntityType.PHONE,
            EntityType.EMAIL,
            EntityType.CR_NUMBER,
            EntityType.IBAN,
            EntityType.PERSON,
        ):
            self.assertIn(expected, found, f"missed {expected}")


class TestVault(unittest.TestCase):
    def test_store_and_resolve(self) -> None:
        vault = SessionVault("t-vault")
        vault.store("<CR_NUM_1>", SCENARIO_CR)
        self.assertEqual(vault.resolve("<CR_NUM_1>"), SCENARIO_CR)

    def test_unknown_placeholder_returns_none(self) -> None:
        self.assertIsNone(SessionVault("t-vault2").resolve("<NOPE_1>"))

    def test_ciphertext_is_bound_to_its_placeholder(self) -> None:
        """Moving an envelope between keys must fail the GCM tag check."""
        vault = SessionVault("t-vault3")
        vault.store("<CR_NUM_1>", SCENARIO_CR)
        vault._entries["<ORG_1>"] = vault._entries["<CR_NUM_1>"]
        with self.assertRaises(MaskingError):
            vault.resolve("<ORG_1>")

    def test_purge_clears_everything(self) -> None:
        vault = SessionVault("t-vault4")
        vault.store("<CR_NUM_1>", SCENARIO_CR)
        vault.purge()
        self.assertEqual(vault.size, 0)


class TestDisclosure(unittest.TestCase):
    def test_authorised_caller_reveals(self) -> None:
        engine = MaskingEngine()
        masked = engine.mask(SCENARIO_REQUEST, session_id="t-disc")
        revealed = reveal(
            masked.masked_text,
            session_id="t-disc",
            principal=principal(Role.SPECIALIST),
            trail=temp_trail(),
        )
        self.assertIn(SCENARIO_CR, revealed)

    def test_admin_cannot_reveal(self) -> None:
        """Separation of duty: the platform engineer has no window onto PII."""
        engine = MaskingEngine()
        masked = engine.mask(SCENARIO_REQUEST, session_id="t-disc2")
        with self.assertRaises(AuthorizationError):
            reveal(
                masked.masked_text,
                session_id="t-disc2",
                principal=principal(Role.ADMIN),
                trail=temp_trail(),
            )

    def test_disclosure_is_audited(self) -> None:
        engine = MaskingEngine()
        trail = temp_trail()
        masked = engine.mask(SCENARIO_REQUEST, session_id="t-disc3")
        reveal(
            masked.masked_text,
            session_id="t-disc3",
            principal=principal(Role.AUDITOR),
            trail=trail,
        )
        self.assertIn("pii.revealed", [r.event.value for r in trail])


if __name__ == "__main__":
    unittest.main()
