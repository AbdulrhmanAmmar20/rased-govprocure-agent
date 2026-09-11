# Security posture and control mapping

> **Status of this document.** The mappings below are an *engineering*
> account of which control each mechanism is intended to serve. They are not a
> compliance certification and were not produced by an accredited assessor.
> The entity's cybersecurity function must validate them against the current
> text of each framework before any accreditation is claimed. Where a control
> is only partially met, this document says so rather than rounding up.

## Threat model

What this system is actually defending against, in rough order of likelihood.

| # | Threat | Primary control |
|---|---|---|
| 1 | Procurement data reaching a public model API through misconfiguration | `assert_sovereign_endpoint` + NetworkPolicy with no internet egress |
| 2 | PII surviving into a prompt via a later code change | `assert_prompt_is_masked` re-scans the assembled prompt |
| 3 | A specialist obtaining award authority they do not hold | Permissions derived from role at token verification, never read from the token body |
| 4 | Prompt injection in submitted text altering a verdict | Verdicts computed by the rule engine; the model cannot reach them |
| 5 | A hallucinated article number presented as a legal basis | Citations verified against the retrieved set; report discarded if unsupported |
| 6 | A violation being erased after the fact | Hash-chained audit records; altering one breaks every later link |
| 7 | An operator reading case data they have no business seeing | Admin role denied `pii:reveal`; disclosure is a separate audited call |
| 8 | Original identifiers leaking through a response field | Tool arguments re-masked at the HTTP boundary |

Threat 8 was a real defect found by a test during development, not a
hypothetical. See the commit that added `MaskingEngine.remask`.

## PDPL — نظام حماية البيانات الشخصية

| Principle | Where it is implemented | Status |
|---|---|---|
| Data minimisation | Only masked text reaches inference; audit records store masked prompts only | Met |
| Purpose limitation | Personal data used solely for the compliance review that produced it; the session mapping is destroyed when the transaction is decided | Met |
| Storage limitation | `SessionVault` is memory-only with a TTL; `close_session` destroys it on decision | Met |
| Security of processing | AES-256-GCM in memory, AES-256 at rest via the storage class, TLS 1.3 in transit | Met at the application layer; the storage class and ingress are deployment responsibilities |
| Cross-border transfer restriction | Residency check fails closed; cluster egress denied by default | Met |
| Accountability | Every access to personal data is an audit event, including disclosure | Met |
| Data subject rights (access, correction, erasure) | **Not implemented.** No subject-request workflow exists | **Gap** |
| Records of processing activities | Partially served by the audit trail; no formal RoPA register | **Partial** |

The two gaps are organisational as much as technical, but they are gaps and
are listed as such.

## NCA ECC — الضوابط الأساسية للأمن السيبراني

Indicative mapping to ECC-1:2018 domains.

| Area | Control intent | Implementation |
|---|---|---|
| Identity and access management | Role-based access, least privilege | `app/core/rbac.py`; no role holds every permission, asserted by test |
| | Separation of duties | Admin denied `pii:reveal`; submitter cannot approve their own transaction |
| Data protection | Classification and handling of sensitive data | `app/pii/` — eight entity classes, masked before crossing any boundary |
| Cryptography | Approved algorithms, key management | AES-256-GCM; ephemeral per-process keys by default, so no long-lived key exists to be compromised |
| System protection | Hardened runtime | Non-root, read-only root filesystem, all capabilities dropped, restricted PSS |
| Network security | Segmentation, default deny | Default-deny NetworkPolicy; vLLM with no egress at all |
| Event logs and monitoring | Tamper-evident logging of security events | `app/audit/trail.py`; denied tool attempts and disclosures are recorded events |
| Cryptographic transit protection | TLS 1.3 | Terminated at ingress; HSTS asserted by the application as well |
| Cloud cybersecurity (CCC-1:2020) | In-Kingdom hosting and data residency | Residency validator at startup; fails closed in production |
| Vulnerability management | Dependency and static analysis | `pip-audit` and `bandit` in CI |
| Backup and recovery | **Not implemented.** No backup or restore procedure for the audit trail | **Gap** |
| Incident response | **Not implemented.** No runbook or alerting integration | **Gap** |

## Cryptography

| Use | Algorithm | Note |
|---|---|---|
| Masking vault | AES-256-GCM, 96-bit nonce per value | Not Fernet: Fernet is AES-128-CBC and would sit below the NFR-1.2 bar |
| Placeholder binding | GCM additional authenticated data | A ciphertext cannot be moved to another placeholder without failing the tag check |
| Token signing | HMAC-SHA256 (JWT HS256) | An asymmetric algorithm is preferable once tokens are issued by a separate IAM; see below |
| Audit chain | SHA-256 over canonical JSON | Reproducible by a third party from the file alone |
| Placeholder allocation index | HMAC-SHA256 | So the unencrypted index never holds plaintext PII |

**On HS256.** A shared secret is adequate while Rased both mints and verifies
its own tokens. Once the entity's IAM becomes the issuer, this should move to
RS256 or ES256 with JWKS verification, so Rased holds only a public key and a
compromise of the API cannot be used to forge a Director token. The change is
confined to `app/core/security.py`.

## Deliberate design decisions

**Ephemeral vault keys are the default, not a fallback.** An empty
`RASED_VAULT_KEY` makes each process mint its own AES-256 key. A restart
therefore destroys every outstanding masking table cryptographically, and
there is no long-lived key to steal, escrow, or rotate. Setting a fixed key is
supported but is the weaker posture.

**Denials are recorded, not just refused.** A denied tool attempt is the
evidence that FR-3.2 fired. A system that logged only successful calls would
discard exactly the events the control exists to produce.

**Disclosure is an event.** Reading an identity number is something a
supervisor may later have to account for, so `pii.revealed` is written with
the placeholders disclosed — and never with what they contained, since
recording the leak would be its own disclosure.

**Detection over prevention for the audit trail.** File permissions cannot
make a log immutable, because whoever rotates it can rewrite it. The hash
chain instead makes any alteration detectable and locatable.

## Residual risks

1. **The corpus is a paraphrase.** The largest risk in the system, and not a
   security one: a report can be structurally perfect and legally wrong. See
   `corpus-governance.md`.
2. **Name detection has limited recall.** A personal name written without an
   honorific or legal-form trigger may pass unmasked. Installing Presidio
   raises recall; it does not make it complete.
3. **The development token endpoint exists.** Gated on
   `RASED_ENVIRONMENT != production`, but a misconfigured environment variable
   would expose an endpoint that mints any role on request.
4. **No rate limiting.** Nothing prevents a valid token from being used to
   enumerate vendor eligibility across many CR numbers. This belongs at the
   ingress and is not implemented here.
5. **Per-replica audit chains.** Verifiable individually; not globally ordered.
   A record could in principle be dropped from one replica's chain by
   discarding that chain wholesale.
6. **No data-subject rights workflow**, and **no backup, restore or incident
   response procedure**, as noted above.

## Reporting a vulnerability

Report privately to the entity's cybersecurity function. Do not open a public
issue. If this repository is deployed outside its originating entity, the
operator is responsible for defining a disclosure route before go-live.
