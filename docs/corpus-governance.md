# Corpus governance — حوكمة قاعدة اللوائح

## Status of the shipped corpus

`app/rag/corpus/gtpl.json` carries `"source_status": "seed_paraphrase"`.

**The entries are development-time paraphrases written to exercise the
retrieval and grounding pipeline. They are not authoritative legal text.**

This matters more here than in a normal RAG project. FR-2.2 requires the agent
to cite an article number on every finding, and FR-2.3 forbids it from
answering without one. An auditor reading a Rased report is therefore being
invited to rely on the citation. A paraphrase that is 90% right produces a
report that is confidently wrong in a way that is very hard to spot — the
citation looks correct, the article number is real, and only the substance is
off.

## Before production use

1. Obtain the official text of نظام المنافسات والمشتريات الحكومية and its
   اللائحة التنفيذية as published in أم القرى and on منصة اعتماد.
2. Replace every `text` field with the verbatim article text.
3. Set `"source_status": "official"` and record the gazette issue and date in
   `corpus_version`.
4. Have a Compliance Auditor (`مدقق نظامي`) sign off on the diff. The role
   exists in the RBAC matrix for exactly this.
5. Re-run `python -m scripts.seed_regulations --verify`.

Until step 3 is done, `/api/v1/health/ready` reports `corpus_status:
seed_paraphrase` and every generated report carries a visible disclaimer
banner. That is deliberate: a seed corpus should be impossible to mistake for
a certified one.

## Amending the corpus

Corpus updates are a `MANAGE_VECTOR_DB` operation, held only by the System
Admin role — but an admin cannot read masked case data, so a corpus change
can never be used as a route to transaction contents.

Every amendment must:

- keep the `id` stable when an article is amended rather than replaced, so
  historical audit records still resolve their citations;
- add a new `id` when an article is superseded, and mark the old one
  `"superseded_by"`, never delete it — a report issued in 2026 must remain
  explicable against the text that was in force when it was issued.

## Thresholds

Numeric limits appear twice on purpose: in `app/config.py` as operational
settings, and in the corpus `thresholds` block as the legal basis for them.
`tests/test_rag.py` asserts the two agree. If Article 34's ceiling changes,
both must move together or the test fails — which is the point.
