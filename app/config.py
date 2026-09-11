"""Central configuration.

Every knob that a security reviewer would want to see is surfaced here rather
than being scattered through the code. Values come from the environment so the
container image itself stays free of secrets (NFR-1.2).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RASED_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Service identity -------------------------------------------------
    app_name: str = "Rased GovProcure Agent"
    environment: str = "development"
    debug: bool = False

    # ---- Data sovereignty (NFR-1.1) --------------------------------------
    # Refuses to start if the deployment claims a region outside the Kingdom.
    data_residency_region: str = "sa-riyadh-1"
    allowed_residency_prefixes: tuple[str, ...] = ("sa-",)

    # ---- Authentication (FR-3.2) -----------------------------------------
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "rased.gov.local"
    jwt_ttl_seconds: int = 3600

    # ---- PII vault (FR-1.3, NFR-1.2) -------------------------------------
    # AES-256 key material for the in-memory reverse-mapping table. Left empty
    # a fresh random key is generated per process, which is the correct default:
    # mappings must never outlive the session that created them.
    vault_key: str = ""
    vault_ttl_seconds: int = 1800
    pii_latency_budget_ms: int = 150  # NFR-2.1

    # ---- Procurement thresholds (FR-3.1, FR-4.1) -------------------------
    # Direct-purchase ceiling in SAR. Article 34 of the Implementing Regulation
    # of the Government Tenders and Procurement Law.
    direct_purchase_ceiling_sar: float = 100_000.0
    # Minimum mandated local-content ratio before a transaction may proceed.
    min_local_content_ratio: float = 0.30

    # ---- Inference engine (NFR-1.3, NFR-2.2) -----------------------------
    llm_base_url: str = "http://vllm.rased.svc.cluster.local:8000/v1"
    llm_model: str = "Qwen2.5-14B-Instruct"
    llm_api_key: str = "sovereign-local"
    llm_timeout_seconds: float = 5.0  # NFR-2.2
    llm_temperature: float = 0.0  # determinism matters for audit reproducibility
    llm_max_tokens: int = 1024
    # When the sovereign endpoint is unreachable the agent degrades to the
    # deterministic rule engine instead of silently calling anything external.
    llm_allow_rule_fallback: bool = True

    # ---- Retrieval (FR-2.1) ----------------------------------------------
    vector_backend: str = "memory"  # "memory" | "chroma"
    vector_path: Path = REPO_ROOT / "data" / "chroma"
    retrieval_top_k: int = 4
    retrieval_min_score: float = 0.10

    # ---- Audit trail (FR-5.1) --------------------------------------------
    audit_log_path: Path = REPO_ROOT / "data" / "audit" / "audit.jsonl"

    @field_validator("data_residency_region")
    @classmethod
    def _must_be_in_kingdom(cls, value: str) -> str:
        """NFR-1.1 — fail closed rather than serve from a foreign region."""
        if not value.startswith(("sa-",)):
            raise ValueError(
                f"data_residency_region {value!r} is outside the Kingdom; "
                "Rased refuses to start outside an in-Kingdom data centre."
            )
        return value

    @field_validator("min_local_content_ratio")
    @classmethod
    def _ratio_is_a_ratio(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("min_local_content_ratio must be between 0 and 1")
        return value


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
