"""Prompt construction, plus the last gate before the boundary (FR-1.2, FR-2).

Two things live here.

The system prompt states the grounding rules in Arabic, the language the
corpus and the reports are in. Instructing a model in one language to reason
over evidence in another adds a translation step nobody asked for and that
nobody reviews.

``assert_prompt_is_masked`` is the belt to the masking engine's braces. It
re-runs detection over the fully assembled prompt — after retrieval context,
tool output and fact summaries have been concatenated in — and refuses to send
it if anything sensitive is found. The masking engine guards the request; this
guards everything that was joined to it afterwards, which is where a leak
would realistically be introduced by a later change.
"""

from __future__ import annotations

from app.core.exceptions import MaskingError
from app.core.logging import get_logger
from app.pii.engine import get_engine

logger = get_logger("rased.prompts")

SYSTEM_PROMPT_AR = """أنت «راصد»، وكيل تدقيق امتثال للمشتريات الحكومية في المملكة العربية السعودية.
مهمتك فحص طلبات الشراء ومطابقتها لنظام المنافسات والمشتريات الحكومية ولائحته التنفيذية.

قواعد إلزامية لا يجوز مخالفتها:

١. الاستناد إلى المراجع فقط: لا تُصدر أي حكم نظامي إلا استناداً إلى نصوص المواد
   الواردة في قسم «المراجع النظامية» أدناه. لا تستخدم معرفة خارجية عن هذه النصوص.

٢. ذكر السند: كل ملاحظة أو مخالفة تذكرها يجب أن تقترن برقم المادة واسم المستند
   كما وردا حرفياً في المراجع. لا تخترع أرقام مواد، ولا تذكر مادة غير موجودة في
   المراجع المعطاة.

٣. الامتناع عند غياب السند: إذا لم تجد في المراجع ما يغطي المسألة، اكتب صراحةً:
   «لا يوجد سند نظامي في المراجع المتاحة» ولا تُخمّن.

٤. البيانات المحجوبة: النص يحتوي على حقول محجوبة مثل <CR_NUM_1> و<SAUDI_ID_1>
   و<ORG_1>. تعامل معها كمعرّفات ثابتة. لا تحاول تخمين قيمتها الأصلية، ولا تطلبها،
   ولا تُنشئ قيماً بديلة لها. أعد استخدام الحقل نفسه كما ورد.

٥. الحياد: أنت أداة فحص ولست صاحب صلاحية. لا تعتمد ولا تُرسي ولا تأمر بالصرف.
   اقتصر على الرصد والتوصية، واذكر متى تلزم موافقة صاحب الصلاحية.

صيغة المخرج:
- «الخلاصة»: سطر واحد يوضح ما إذا كانت المعاملة مطابقة أو بها مخالفة.
- «الملاحظات»: قائمة مرقمة، كل بند يذكر الملاحظة ثم السند النظامي بين قوسين.
- «التوصية»: الإجراء المطلوب نظاماً.
"""

USER_PROMPT_TEMPLATE_AR = """## المراجع النظامية

{context}

## معطيات المعاملة

{facts}

## نتائج أدوات الفحص

{tool_results}

## نص الطلب (بعد حجب البيانات الحساسة)

{masked_request}

اكتب تقرير التدقيق النظامي وفق الصيغة المحددة، مستنداً إلى المراجع أعلاه فقط.
"""


def build_user_prompt(
    *,
    context: str,
    facts: str,
    tool_results: str,
    masked_request: str,
) -> str:
    return USER_PROMPT_TEMPLATE_AR.format(
        context=context or "(لا توجد مراجع مسترجعة)",
        facts=facts or "(لا توجد معطيات مستخرجة)",
        tool_results=tool_results or "(لم تُستدعَ أدوات)",
        masked_request=masked_request,
    )


def assert_prompt_is_masked(prompt: str) -> None:
    """Refuse to send a prompt that still contains detectable sensitive data.

    Raises MaskingError naming the entity types found, never the values —
    an exception message ends up in logs, and logging the leak while
    reporting it would be its own disclosure.
    """
    findings = get_engine().detect(prompt)
    if not findings:
        return

    kinds = sorted({f.entity_type.value for f in findings})
    logger.error(
        "assembled prompt still contains sensitive entities; send aborted",
        extra={"entity_types": kinds, "count": len(findings)},
    )
    raise MaskingError(
        "تم إيقاف إرسال الطلب لمحرك الاستدلال لاحتوائه على بيانات حساسة غير محجوبة.",
        entity_types=kinds,
        count=len(findings),
    )
