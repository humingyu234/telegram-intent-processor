"""Intent classifier with keyword-rule matching. No LLM dependency."""

import re

from app.models import GroupSnapshot, GroupState, Intent

# ---------------------------------------------------------------------------
# Keyword tables — ordered by specificity (more specific patterns checked first)
# ---------------------------------------------------------------------------

PRICING_KEYWORDS: list[str] = [
    "多少钱", "价格", "报价", "报价单", "多少钱一", "收费",
    "怎么收费", "费用", "价格表", "收费标准", "定价",
    "price", "pricing", "how much", "cost", "fee", "quote",
]

PRODUCT_KEYWORDS: list[str] = [
    "有卖", "有没有", "产品", "功能", "支持吗", "能不能",
    "可以做", "企业版", "个人版", "免费版", "试用",
    "介绍", "版本", "有什么区别", "规格", "参数",
    "product", "feature", "edition", "version", "spec",
]

HELP_KEYWORDS: list[str] = [
    "怎么用", "怎么弄", "怎么搞", "不知道怎么", "教",
    "帮忙", "帮我看", "帮一下", "求助", "救命",
    "登录不了", "登不上", "报错", "出错了", "错误",
    "失败", "连不上", "打不开", "闪退", "卡住了", "卡死",
    "help", "how to", "support", "error", "fail", "bug", "issue",
    "stuck", "broken", "not working", "doesn't work",
]

COMPLAINT_KEYWORDS: list[str] = [
    "投诉", "举报", "垃圾", "骗", "差评", "太差了",
    "不好用", "太难用", "真难", "烂", "坑",
    "客服", "不理", "不回", "一直不回", "没人管", "没人回",
    "退款", "退钱", "赔偿", "投诉你们", "差劲",
    "complaint", "refund", "terrible", "awful", "scam",
    "nobody", "no one", "ignore", "never reply",
]

COMPLAINT_STRONG_PATTERNS: list[re.Pattern] = [
    re.compile(r"客服.{0,5}(不|没|无).{0,3}(回|理|管|应)"),
    re.compile(r"太.{0,3}(差|烂|坑|垃圾)"),
    re.compile(r"(refund|scam|terrible|awful)", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Tag generation
# ---------------------------------------------------------------------------

INTENT_TAG_MAP: dict[Intent, list[str]] = {
    Intent.PRICING: ["sales_lead", "needs_reply"],
    Intent.PRODUCT: ["product_interest", "needs_reply"],
    Intent.HELP: ["needs_reply", "urgent"],
    Intent.COMPLAINT: ["urgent", "negative", "needs_human"],
    Intent.OTHER: [],
}


def _match_keywords(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in keywords)


def _match_patterns(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_intent(text: str) -> Intent:
    """Classify a message into one of five intents.

    Order matters: complaint first (highest priority), then help, pricing,
    product, and everything else falls into OTHER.
    """
    if _match_patterns(text, COMPLAINT_STRONG_PATTERNS):
        return Intent.COMPLAINT
    if _match_keywords(text, COMPLAINT_KEYWORDS):
        return Intent.COMPLAINT
    if _match_keywords(text, HELP_KEYWORDS):
        return Intent.HELP
    if _match_keywords(text, PRICING_KEYWORDS):
        return Intent.PRICING
    if _match_keywords(text, PRODUCT_KEYWORDS):
        return Intent.PRODUCT
    return Intent.OTHER


def generate_tags(intent: Intent, text: str = "") -> list[str]:
    """Produce business tags for a classified intent."""
    tags = list(INTENT_TAG_MAP.get(intent, []))
    if len(text) > 200:
        tags.append("long_message")
    return tags


def classify_message(
    text: str,
    group_context: dict | GroupSnapshot | None = None,
) -> tuple[Intent, list[str]]:
    """Classify a message and generate tags, with optional group-context awareness.

    group_context is either a GroupSnapshot (from GroupStateMachine.snapshot()),
    a plain dict with 'current_state', or None when no prior state exists.

    Context-aware rule (lightweight, no LLM):
    - If the group is COMPLAINT_ESCALATED and the text matches pricing
      keywords, the pricing intent is kept but tags gain 'billing_dispute'
      and 'complaint_context' — reflecting that this is not a fresh sales
      inquiry but a pricing question inside an active complaint.
    - Other intents inside a COMPLAINT_ESCALATED group also gain
      'complaint_context'.
    """
    intent = classify_intent(text)
    tags = generate_tags(intent, text)

    if group_context is None:
        return intent, tags

    if isinstance(group_context, GroupSnapshot):
        current_state = group_context.current_state
    else:
        raw = group_context.get("current_state")
        current_state = GroupState(raw) if raw else None

    if current_state == GroupState.COMPLAINT_ESCALATED:
        tags.append("complaint_context")
        if intent == Intent.PRICING:
            tags.append("billing_dispute")

    return intent, tags
