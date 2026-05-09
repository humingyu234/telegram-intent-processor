"""Tests for intent classifier — Skill 3, test #1."""

import pytest
from app.classifier import classify_intent, classify_message, generate_tags
from app.models import GroupState, Intent


class TestClassifierDetectsCoreIntents:
    """Contract: classify_intent must correctly identify pricing, product,
    help, complaint, and fallback to OTHER for unrelated messages."""

    # ── pricing ──────────────────────────────────────────────────────

    @pytest.mark.parametrize("text", [
        "这个多少钱",
        "报价多少",
        "收费标准是什么",
        "how much does it cost",
        "价格表发我一份",
    ])
    def test_pricing_intent(self, text: str):
        assert classify_intent(text) == Intent.PRICING

    # ── product ──────────────────────────────────────────────────────

    @pytest.mark.parametrize("text", [
        "有没有企业版",
        "这个功能支持吗",
        "能不能做定制开发",
        "介绍一下产品",
        "免费版和付费版有什么区别",
    ])
    def test_product_intent(self, text: str):
        assert classify_intent(text) == Intent.PRODUCT

    # ── help ─────────────────────────────────────────────────────────

    @pytest.mark.parametrize("text", [
        "怎么用不了",
        "登录不了怎么办",
        "报错了帮我看看",
        "打不开了",
        "help me fix this error",
    ])
    def test_help_intent(self, text: str):
        assert classify_intent(text) == Intent.HELP

    # ── complaint ────────────────────────────────────────────────────

    @pytest.mark.parametrize("text", [
        "客服一直不回太差了",
        "我要投诉",
        "垃圾产品",
        "退款",
        "refund please this is terrible",
    ])
    def test_complaint_intent(self, text: str):
        assert classify_intent(text) == Intent.COMPLAINT

    # ── other ────────────────────────────────────────────────────────

    @pytest.mark.parametrize("text", [
        "今天天气真好",
        "hello",
        "好的谢谢",
        "ok",
    ])
    def test_other_intent(self, text: str):
        assert classify_intent(text) == Intent.OTHER

    # ── complaint priority ───────────────────────────────────────────

    def test_complaint_pattern_matches_before_keyword(self):
        """Strong complaint patterns ("客服...不...回") trigger complaint
        even when the text also contains pricing keywords."""
        text = "客服一直不回，价格也贵"
        assert classify_intent(text) == Intent.COMPLAINT


class TestTagGeneration:
    """Contract: generate_tags must produce appropriate business tags
    for each intent, and add 'long_message' for texts > 200 chars."""

    def test_pricing_tags(self):
        tags = generate_tags(Intent.PRICING)
        assert "sales_lead" in tags
        assert "needs_reply" in tags

    def test_product_tags(self):
        tags = generate_tags(Intent.PRODUCT)
        assert "product_interest" in tags

    def test_help_tags(self):
        tags = generate_tags(Intent.HELP)
        assert "urgent" in tags

    def test_complaint_tags(self):
        tags = generate_tags(Intent.COMPLAINT)
        assert "needs_human" in tags
        assert "negative" in tags

    def test_other_tags(self):
        tags = generate_tags(Intent.OTHER)
        assert tags == []

    def test_long_message_tag(self):
        long_text = "a" * 201
        tags = generate_tags(Intent.OTHER, text=long_text)
        assert "long_message" in tags


class TestClassifierUsesGroupContextForBillingDispute:
    """Contract: when a group is COMPLAINT_ESCALATED and the user asks about
    pricing, the intent stays PRICING but tags gain 'billing_dispute' and
    'complaint_context' — reflecting that this is a complaint-driven pricing
    question, not a fresh sales inquiry."""

    def test_pricing_in_complaint_context_gets_billing_dispute(self):
        group_ctx = {"current_state": GroupState.COMPLAINT_ESCALATED.value}
        intent, tags = classify_message("这个多少钱", group_context=group_ctx)

        assert intent == Intent.PRICING
        assert "billing_dispute" in tags
        assert "complaint_context" in tags
        assert "sales_lead" in tags  # base pricing tags still present

    def test_non_pricing_in_complaint_context_only_gets_complaint_context(self):
        group_ctx = {"current_state": GroupState.COMPLAINT_ESCALATED.value}
        intent, tags = classify_message("打不开了", group_context=group_ctx)

        assert intent == Intent.HELP
        assert "complaint_context" in tags
        assert "billing_dispute" not in tags  # only for pricing

    def test_pricing_without_complaint_context_no_extra_tags(self):
        """No group context — behaves like plain classify_intent + generate_tags."""
        intent, tags = classify_message("这个多少钱")

        assert intent == Intent.PRICING
        assert "billing_dispute" not in tags
        assert "complaint_context" not in tags

    def test_other_state_context_no_complaint_tags(self):
        """Groups in non-complaint states get no complaint_context tagging."""
        group_ctx = {"current_state": GroupState.PRODUCT_DISCUSSION.value}
        intent, tags = classify_message("这个多少钱", group_context=group_ctx)

        assert intent == Intent.PRICING
        assert "billing_dispute" not in tags
        assert "complaint_context" not in tags
