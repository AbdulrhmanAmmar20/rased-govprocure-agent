# راصد — Rased GovProcure Agent

> نظام وكيل ذكاء اصطناعي للامتثال وتدقيق المشتريات الحكومية
> An AI compliance and audit agent for Saudi government procurement, built to
> run entirely inside an in-Kingdom sovereign cloud.

[![CI](https://github.com/OWNER/rased-govprocure-agent/actions/workflows/ci.yml/badge.svg)](../../actions)

---

## ما هو راصد؟

يفحص «راصد» طلبات الشراء الحكومي ويطابقها مع **نظام المنافسات والمشتريات
الحكومية ولائحته التنفيذية**، ثم يصدر تقرير تدقيق نظامياً مسنداً إلى المواد،
ويعلّق المعاملة عند الحاجة لموافقة صاحب الصلاحية.

يعمل النظام بالكامل داخل شبكة خاصة معزولة: لا تغادر أي بيانات شخصية أو تجارية
حدود الجهة، ولا يُستدعى أي نموذج ذكاء اصطناعي عام.

**ما الذي يميزه عملياً:**

| | |
|---|---|
| **يحجب قبل أن يفكر** | تُستبدل الهويات والسجلات التجارية والآيبان بحقول مشفرة قبل أي استدعاء للنموذج |
| **لا رأي بلا سند** | كل ملاحظة مقترنة برقم المادة، ويمتنع الوكيل عن الإجابة إذا غاب السند |
| **الصلاحية في الكود** | لا يستطيع الوكيل إصدار أمر شراء مهما طُلب منه، ما لم يحمل التوكن رتبة صاحب صلاحية |
| **سجل لا يُعدَّل** | سلسلة تجزئة مترابطة تكشف أي عبث بالسجل وتحدد موضعه |

---

## Quick start

Requires Python 3.11+. Nothing else — no model, no vector database, no network.

```bash
pip install -r requirements.txt
```

Walk the SRS section 6 acceptance scenario in the terminal:

```bash
python -m app.cli demo --quiet
```

Run the test suite (125 tests):

```bash
python -m unittest discover -s tests
```

Start the API:

```bash
uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000/docs`.

On Linux/macOS `make demo`, `make test` and `make run` do the same things.

### The local sovereign stack

```bash
cp .env.example .env      # set RASED_JWT_SECRET
docker compose up -d --build
```

This brings up the API alongside vLLM on an **internal** Docker network that
has no route off the host, so the inference engine cannot reach the internet
even in principle.

---

## What happens to a request

The scenario from SRS section 6 — a Specialist asking to award a 120,000 SAR
direct purchase:

```
"أرغب في ترسية شراء مباشر لكاميرات مراقبة على مؤسسة الأفق
 سجل تجاري 1010998877 بمبلغ 120,000 ريال وفق نظام المنافسات."

  │
  ▼  ① mask (FR-1)                     ~1 ms
     "... على مؤسسة <ORG_1> سجل تجاري <CR_NUM_1> بمبلغ 120,000 ريال ..."
     originals held only in an AES-256-GCM in-memory table
  │
  ▼  ② ground (FR-2)
     retrieves اللائحة التنفيذية — المادة الرابعة والثلاثون
     "لا يجوز أن تتجاوز قيمة ... الشراء المباشر مئة ألف ريال"
  │
  ▼  ③ check (FR-3.1)
     check_vendor_eligibility  → eligible
     verify_budget_ceiling     → 120,000 > 100,000, route = limited_competition
  │
  ▼  ④ attempt (FR-3.2)
     create_procurement_order  → DENIED
     "role 'specialist' lacks 'order:create'"
  │
  ▼  ⑤ evaluate + report
     VIOLATION: DIRECT_PURCHASE_CEILING_EXCEEDED  (IR-ART-34, GTPL-ART-28)
     status → PENDING_APPROVAL                                    (FR-4.1)
  │
  ▼  ⑥ audit (FR-5.1)
     9 hash-chained records, verifiable by a third party from the file alone
```

The agent **attempts** the order it was asked for rather than skipping a call
it would obviously be refused. The refusal has to be a recorded event for the
audit trail to show that the control fired.

---

## Design decisions worth knowing

**The model does not decide compliance.** Verdicts come from
[`app/agent/rules.py`](app/agent/rules.py) — arithmetic and lookups over tool
results. The model writes the Arabic prose around a conclusion already
reached. So the same transaction always produces the same verdict, that verdict
survives a model upgrade, an auditor can reproduce it with no GPU, and prompt
injection in the submitted text cannot reach it.

**Everything degrades to a correct answer.** With no inference endpoint
reachable, reports fall back to a deterministic template that still carries
every finding and its legal basis. An inference outage slows procurement
review; it does not stop it.

**The optional stack is genuinely optional.** LangGraph, Presidio and ChromaDB
are all supported and none are required. CI runs without them on purpose, so a
hard dependency cannot creep in unnoticed.

**Sovereignty is enforced, not promised.** The app refuses to send to a
non-private endpoint, and the Kubernetes NetworkPolicy gives vLLM `egress: []`
— it cannot open a connection to anything. A process that cannot open a socket
cannot exfiltrate a prompt.

---

## Known limitations

Stated plainly, because each one matters before production use:

1. **The regulation corpus is a paraphrase, not law.** It carries
   `source_status: seed_paraphrase` and must be replaced with the gazetted
   text. See [docs/corpus-governance.md](docs/corpus-governance.md) — this is
   the single most important item on the list.
2. **Suspended transactions are held in process memory.** Fine for one
   instance; wrong for the horizontal scaling NFR-2.3 asks for, because a
   Director's approval must land on the pod that ran the review. Needs Redis
   or Postgres.
3. **Each replica writes its own audit chain.** The chains are individually
   verifiable but not globally ordered.
4. **Name detection is high-precision, low-recall.** Trigger-driven regex
   catches names introduced by an honorific or a legal form; installing
   Presidio raises recall.
5. **The development token endpoint** mints any role on request. It is gated
   on `RASED_ENVIRONMENT != production`, but it exists.
6. **The vendor and budget backends are fixtures**, standing in for the ERP
   and منصة اعتماد integrations.

---

## Documentation

| Document | What it covers |
|---|---|
| [docs/SRS-ar.md](docs/SRS-ar.md) | The source requirements specification |
| [docs/architecture.md](docs/architecture.md) | Components, data flow, trust boundaries |
| [docs/security.md](docs/security.md) | PDPL and NCA ECC control mapping |
| [docs/traceability.md](docs/traceability.md) | Every requirement → code → test |
| [docs/corpus-governance.md](docs/corpus-governance.md) | Replacing the seed corpus |

## Licence

MIT — see [LICENSE](LICENSE).
