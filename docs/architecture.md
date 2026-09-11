# Architecture

## Trust boundaries

Three boundaries matter, and nearly every design decision in the codebase is
an answer to one of them.

```
┌─────────────────────────────────────────────────────────────────────┐
│ (1) The entity's network                                            │
│                                                                     │
│   Employee ──TLS 1.3──► Ingress ──► Rased API                       │
│                                       │                             │
│   ┌───────────────────────────────────┼──────────────────────────┐  │
│   │ (2) Application trust boundary    │                          │  │
│   │     real identifiers live here    ▼                          │  │
│   │                            ┌─────────────┐                   │  │
│   │                            │  PII gate   │  FR-1             │  │
│   │                            └──────┬──────┘                   │  │
│   │   ┌──────────┐  ┌──────────┐      │      ┌───────────────┐   │  │
│   │   │  Tools   │  │  Rules   │◄─────┤      │ Session vault │   │  │
│   │   │  (ERP,   │  │  engine  │      │      │ AES-256-GCM   │   │  │
│   │   │  Etimad) │  │ verdicts │      │      │ memory only   │   │  │
│   │   └──────────┘  └──────────┘      │      └───────────────┘   │  │
│   │                                   │                          │  │
│   │   ┌───────────────────────────────▼───────────────────────┐  │  │
│   │   │ (3) Inference boundary - only masked text crosses     │  │  │
│   │   │                                                       │  │  │
│   │   │       vLLM (Qwen2.5 / Llama-3.1) - egress: none       │  │  │
│   │   └───────────────────────────────────────────────────────┘  │  │
│   └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                     no route to the public internet
```

**Boundary 2 - the application.** Real CR numbers and identity numbers exist
here legitimately. `check_vendor_eligibility` cannot query the ERP with a
placeholder. This is why `app/agent/state.py` keeps `raw_request` and
`masked_request` as separately named fields, and why the API re-masks tool
arguments on the way out. Inside is fine; outside is not.

**Boundary 3 - inference.** Only masked text crosses. It is enforced three
times over, deliberately: the masking engine at the gate,
`assert_prompt_is_masked` on the fully assembled prompt, and
`assert_sovereign_endpoint` on the destination. The first two guard content;
the third guards where that content is going.

## Request flow

| Stage | Module | Requirement |
|---|---|---|
| Authenticate, resolve role | `app/api/deps.py`, `app/core/security.py` | FR-3.2 |
| Mask entities, store mapping | `app/pii/engine.py`, `app/pii/vault.py` | FR-1.1-1.3 |
| Extract facts | `app/agent/extraction.py` | - |
| Retrieve articles, enforce grounding | `app/rag/retriever.py` | FR-2.1-2.3 |
| Run compliance tools | `app/tools/*` via `registry.py` | FR-3.1-3.2 |
| Compute verdict | `app/agent/rules.py` | FR-4.1 |
| Write report, verify citations | `app/agent/report.py` | FR-2.2, NFR-2.2 |
| Suspend for approval | `app/agent/nodes.py` | FR-4.1-4.2 |
| Record everything | `app/audit/trail.py` | FR-5.1, NFR-3.1 |

## Why the model does not decide

The single most consequential choice in this design. `app/agent/rules.py`
computes verdicts from tool output; the model narrates them.

A 7B-14B model reading Arabic and producing a readable report sits well within
what such models do reliably. Deciding, identically on every run and across
model versions, whether 120,000 exceeds 100,000 is not a property worth
delegating to sampling. Four things follow:

- **Reproducibility.** Same input, same verdict, every time.
- **Upgrade safety.** Swapping Qwen for Llama changes prose, never findings.
- **Auditability.** A reviewer can recompute a verdict with no GPU.
- **Injection resistance.** Text in the request cannot reach the verdict. At
  worst it corrupts prose wrapped around a conclusion it cannot touch.

## The refusal path

FR-2.3 makes "no legal basis" a terminal state rather than a fallback to
general knowledge. When retrieval returns nothing above threshold the
transaction ends at `REFUSED` with a stated reason. The graph skips the tool,
evaluation and approval nodes but still reaches the report node, so the
refusal is written up and recorded instead of returning an empty result.

## Degradation

| Failure | Behaviour | Compliance result |
|---|---|---|
| Inference unreachable | Template report | Correct, less readable |
| Model cites an unretrieved article | Report discarded, template used | Correct |
| Prompt still contains PII | Send aborted, template used | Correct, logged as a defect |
| ChromaDB absent | In-memory index | Correct |
| LangGraph absent | In-tree graph runner | Correct |
| Corpus unretrievable | Refusal | No opinion, which is the right answer |

Every degraded path ends in a correct compliance outcome. That is the property
which makes the optional stack genuinely optional rather than nominally so.

## Production bindings still required

The repository is complete as a system and incomplete as a deployment. Four
substitutions stand between it and production.

1. **Regulation corpus** to the gazetted text. See `corpus-governance.md`.
   This is the one that changes whether reports mean anything.

2. **Transaction store** to Redis or Postgres. `app/agent/service.py` holds
   suspended runs in process memory, so with more than one replica a
   Director's approval can land on a pod that never saw the review. The
   interface is `get` / `put` / `pending`, so this is a substitution rather
   than a rewrite.

3. **Audit trail** to append-only storage with a shared sequence. Each replica
   currently writes its own chain: individually verifiable, not globally
   ordered. A WORM bucket, or an append-only table with a monotonic sequence,
   yields one chain across replicas.

4. **Tool backends** to the entity's ERP, منصة اعتماد, and the Zakat, GOSI and
   Saudisation certificate services, replacing `app/tools/backends.py`.

## Scaling

The API is stateless apart from item 2 above. Once the transaction store moves
out of process, replicas scale horizontally with no coordination - masking,
retrieval and rule evaluation are per-request and CPU-bound. A full review on
a warm process measures about 18 ms excluding inference, so the API tier is
not where capacity planning will be spent.

vLLM scales separately and is the expensive tier. Because the rule engine
already holds the verdict, inference can be throttled, batched, or taken
offline for maintenance without affecting a single compliance outcome - only
the readability of the reports produced while it is down.
