# Traceability matrix

Every requirement in the SRS, the code that implements it, and the test that
proves it. A requirement with no test row is not claimed as done.

## FR-1 — PII and sensitive entity masking

| Req | Requirement | Implementation | Test |
|---|---|---|---|
| FR-1.1 | Detect national IDs and Iqama numbers (10 digits) | `app/pii/recognizers/identity.py` — Luhn-validated | `test_pii.TestMasking.test_identity_number_is_masked` |
| FR-1.1 | Detect CR numbers and IBANs | `app/pii/recognizers/commercial.py` — region prefixes, ISO 13616 mod-97 | `test_pii.TestValidators.test_iban_mod97` |
| FR-1.1 | Detect phones, emails, personal names | `app/pii/recognizers/contact.py`, `names.py` | `test_pii.TestMasking.test_entity_types_detected` |
| FR-1.2 | Replace with placeholders before any LLM call | `app/pii/engine.py::MaskingEngine.mask`; `app/agent/prompts.py::assert_prompt_is_masked` | `test_pii.TestMasking.test_scenario_cr_is_masked` |
| FR-1.3 | Encrypted in-memory reverse mapping | `app/pii/vault.py::SessionVault` — AES-256-GCM, memory only | `test_pii.TestVault.*` |
| FR-1.3 | Restore originals for authorised users | `app/pii/disclosure.py::reveal` | `test_pii.TestDisclosure.test_authorised_caller_reveals` |
| FR-1.3 | Deny unauthorised restoration | Permission `pii:reveal`; Admin excluded | `test_pii.TestDisclosure.test_admin_cannot_reveal` |

## FR-2 — Procurement RAG engine

| Req | Requirement | Implementation | Test |
|---|---|---|---|
| FR-2.1 | Vector database of the law, local content rules and direct-purchase regulations | `app/rag/corpus/gtpl.json`, `app/rag/store.py` | `test_rag.TestCorpus.test_corpus_loads` |
| FR-2.2 | Cite the legal basis and article number in every report | `app/rag/retriever.py::RetrievalResult.citations`; `Finding.legal_basis` | `test_acceptance.test_step2_finding_carries_its_legal_basis` |
| FR-2.3 | Restrict answers to retrieved context | `app/agent/prompts.py::SYSTEM_PROMPT_AR`; `find_unsupported_citations` | `test_rag.TestCitationVerification.test_fabricated_citation_is_caught` |
| FR-2.3 | Refuse when there is no legal basis | `Retriever.require_grounding`; `TransactionStatus.REFUSED` | `test_rag.TestRetrieval.test_refuses_when_nothing_is_grounded` |

## FR-3 — Tool execution gateway and guardrails

| Req | Requirement | Implementation | Test |
|---|---|---|---|
| FR-3.1 | `check_vendor_eligibility(cr_number)` | `app/tools/vendor.py` | `test_tools.TestVendorEligibility.*` |
| FR-3.1 | `verify_budget_ceiling(department_id, amount)` | `app/tools/budget.py` | `test_tools.TestBudgetCeiling.*` |
| FR-3.1 | `create_procurement_order(vendor_id, amount, contract_terms)` | `app/tools/procurement.py` | `test_tools.TestOrderCreation.*` |
| FR-3.2 | Enforce RBAC in the code layer | `app/tools/registry.py::ToolGateway.invoke` | `test_tools.TestGatewayGuardrails.test_specialist_is_denied_order_creation` |
| FR-3.2 | Order creation requires Director | `Permission.CREATE_PROCUREMENT_ORDER`, held only by Director | `test_rbac.TestRoleMatrix.test_specialist_cannot_create_orders` |

## FR-4 — Human-in-the-loop

| Req | Requirement | Implementation | Test |
|---|---|---|---|
| FR-4.1 | Suspend to `PENDING_APPROVAL` above the ceiling | `app/agent/rules.py::requires_human_approval`; `approval_gate_node` | `test_acceptance.test_step4_transaction_is_suspended` |
| FR-4.1 | Suspend when local content is below the floor | `requires_human_approval` | `test_tools.TestBudgetCeiling`, `app/agent/rules.py` |
| FR-4.2 | Approver route with approve/reject and notes | `app/agent/service.py::decide_approval`; `app/api/routes/approvals.py` | `test_acceptance.TestApprovalRoute.*` |

## FR-5 — Audit trail

| Req | Requirement | Implementation | Test |
|---|---|---|---|
| FR-5.1 | Immutable JSON record per transaction | `app/audit/trail.py` — JSONL, hash-chained | `test_audit.TestChain.test_clean_chain_verifies` |
| FR-5.1 | Employee id, role, UTC timestamp | `app/audit/models.py::ActorRef`, `AuditRecord` | `test_audit.TestRecordContent.test_records_identify_actor_and_role` |
| FR-5.1 | The masked prompt | `mask_node` writes `masked_prompt` | `test_acceptance.test_step1_audit_trail_never_stores_the_raw_request` |
| FR-5.1 | Attempted tools with their arguments | `ToolInvocation`, recorded whether allowed or denied | `test_audit.TestRecordContent.test_denied_tool_attempts_are_recorded` |
| FR-5.1 | Approval state and final outcome | `ApprovalDecision`, `AuditRecord.outcome` | `test_acceptance.TestApprovalRoute.test_decision_records_the_approver_identity` |
| FR-5.1 | Non-modifiable | SHA-256 chain; tampering detected and located | `test_audit.TestTamperDetection.*` |

## NFR-1 — Security and data sovereignty

| Req | Requirement | Implementation | Status |
|---|---|---|---|
| NFR-1.1 | All services in in-Kingdom data centres | `app/config.py` residency validator (fails closed); `assert_sovereign_endpoint`; NetworkPolicy with no internet egress | Enforced in code; hosting is a deployment responsibility |
| NFR-1.2 | TLS 1.3 in transit | Terminated at ingress; HSTS asserted by the app | Deployment responsibility, asserted by the app |
| NFR-1.2 | AES-256 at rest | `SessionVault` AES-256-GCM; `storageClassName: encrypted-ssd` for the audit PVC | Met in memory; at-rest depends on the storage class |
| NFR-1.3 | Zero data retention at inference | `X-Data-Retention: none`; vLLM with usage reporting disabled and `egress: []` | Met |

## NFR-2 — Performance and reliability

| Req | Target | Implementation | Evidence |
|---|---|---|---|
| NFR-2.1 | PII masking under 150 ms | `MaskingEngine.mask` records and logs its own duration | `test_pii.test_masking_meets_latency_budget`; measured ~0.2–1.3 ms |
| NFR-2.2 | Initial audit report within 5 s | Inference timeout bound to the budget; liveness probe capped at 1.5 s and cached | Measured 1,626 ms cold and 18 ms warm with inference down |
| NFR-2.3 | 99.5% availability, horizontal scaling | 3 replicas, `maxUnavailable: 0`, topology spread, PDB, HPA 3–12 | **Partial** — blocked by the process-local transaction store; see `architecture.md` |

## NFR-3 — Explainability

| Req | Requirement | Implementation | Test |
|---|---|---|---|
| NFR-3.1 | Chain-of-thought logging | `AgentState.trace`; persisted on the `report.issued` record; exposed at `/audit/transaction/{id}` | `test_acceptance.test_step4_reasoning_trace_is_available` |

## Section 5 — Target stack

| Component | Specified | Status |
|---|---|---|
| Backend and API | Python / FastAPI | Implemented |
| Agent framework | LangGraph | `build_langgraph()` provided; in-tree runner is the default and is what the suite exercises |
| PII engine | Presidio + custom regex | Custom regex implemented and always active; Presidio is an optional recall improvement |
| Inference | vLLM, Qwen2.5-7B/14B or Llama-3.1-8B | OpenAI-compatible client with a residency guard |
| Vector DB | ChromaDB or PGvector | Chroma backend implemented; in-memory default. PGvector **not implemented** |
| Deployment | Docker and Kubernetes, private VPC | Implemented, including default-deny NetworkPolicy |

## Section 6 — Acceptance scenario

Each step is a named test in `tests/test_acceptance.py`.

| Step | Scenario requirement | Test |
|---|---|---|
| 1 | CR masked to `<CR_NUM_1>` | `test_step1_cr_number_is_masked` |
| 1 | Original never persisted in the audit trail | `test_step1_audit_trail_never_stores_the_raw_request` |
| 2 | Direct-purchase article retrieved | `test_step2_direct_purchase_article_is_retrieved` |
| 2 | 120,000 exceeds the 100,000 ceiling | `test_step2_ceiling_violation_is_detected` |
| 2 | Alternative route identified | `test_step2_alternative_route_is_identified` |
| 3 | Agent genuinely attempts the order | `test_step3_agent_actually_attempted_the_order` |
| 3 | RBAC denies the Specialist | `test_step3_rbac_denied_the_specialist` |
| 4 | Report names the violation and the approver | `test_step4_report_names_the_ceiling_violation`, `test_step4_report_requires_the_approver` |
| 4 | Status becomes `PENDING_APPROVAL` | `test_step4_transaction_is_suspended` |
| 4 | Incident documented in the audit trail | `test_step4_incident_is_in_the_audit_trail` |

## Not implemented

Listed so that absence is a recorded decision rather than an oversight.

- **PGvector backend** — Chroma and in-memory only.
- **Data-subject rights workflow** (PDPL access, correction, erasure).
- **Backup and restore** for the audit trail.
- **Incident response** runbook and alerting integration.
- **Rate limiting** — belongs at the ingress.
- **Shared transaction store**, which is what NFR-2.3 is blocked on.
