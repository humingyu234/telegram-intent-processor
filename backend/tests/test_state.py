"""Tests for group state machine — Skill 3, test #2."""

from app.models import Intent, GroupState
from app.state import GroupStateMachine


class TestGroupStateMachineTransitions:
    """Contract: the state machine must transition correctly across the
    full lifecycle: PRODUCT -> PRICING -> HELP -> COMPLAINT, with complaint
    always winning and setting needs_human_attention."""

    def test_starts_idle(self):
        sm = GroupStateMachine(group_id="g1")
        assert sm.current_state == GroupState.IDLE
        assert sm.message_count == 0
        assert sm.needs_human_attention is False

    def test_product_transitions_to_product_discussion(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.PRODUCT, ["product_interest"])
        assert sm.current_state == GroupState.PRODUCT_DISCUSSION
        assert sm.message_count == 1

    def test_pricing_transitions_to_pricing_discussion(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.PRICING, ["sales_lead"])
        assert sm.current_state == GroupState.PRICING_DISCUSSION

    def test_help_transitions_to_support_needed(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.HELP, ["urgent"])
        assert sm.current_state == GroupState.SUPPORT_NEEDED

    def test_complaint_transitions_to_complaint_escalated(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.COMPLAINT, ["negative"])
        assert sm.current_state == GroupState.COMPLAINT_ESCALATED

    def test_complaint_sets_human_attention(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.COMPLAINT, ["negative"])
        assert sm.needs_human_attention is True

    def test_other_does_not_change_state(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.PRODUCT, ["product_interest"])
        assert sm.current_state == GroupState.PRODUCT_DISCUSSION

        sm.apply(Intent.OTHER, [])
        # state stays PRODUCT_DISCUSSION; OTHER is neutral
        assert sm.current_state == GroupState.PRODUCT_DISCUSSION
        assert sm.message_count == 2

    def test_full_lifecycle_product_to_complaint(self):
        """Simulate a group going through: product -> pricing -> help -> complaint."""
        sm = GroupStateMachine(group_id="g1")

        sm.apply(Intent.PRODUCT, ["product_interest"])
        assert sm.current_state == GroupState.PRODUCT_DISCUSSION
        assert sm.needs_human_attention is False

        sm.apply(Intent.PRICING, ["sales_lead"])
        assert sm.current_state == GroupState.PRICING_DISCUSSION

        sm.apply(Intent.HELP, ["urgent"])
        assert sm.current_state == GroupState.SUPPORT_NEEDED

        sm.apply(Intent.COMPLAINT, ["negative", "needs_human"])
        assert sm.current_state == GroupState.COMPLAINT_ESCALATED
        assert sm.needs_human_attention is True
        assert sm.message_count == 4

    def test_intent_counts_are_tracked(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.PRODUCT, [])
        sm.apply(Intent.PRODUCT, [])
        sm.apply(Intent.PRICING, [])
        sm.apply(Intent.OTHER, [])

        assert sm.intent_counts == {
            "product": 2,
            "pricing": 1,
            "other": 1,
        }

    def test_complaint_wins_regardless_of_prior_state(self):
        """Complaint is the highest business priority — it must win even
        from IDLE without intermediate states."""
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.COMPLAINT, ["negative"])
        assert sm.current_state == GroupState.COMPLAINT_ESCALATED
        assert sm.needs_human_attention is True

    def test_snapshot_reflects_current_state(self):
        sm = GroupStateMachine(group_id="g1")
        sm.apply(Intent.HELP, ["urgent", "needs_reply"])

        snap = sm.snapshot()
        assert snap["group_id"] == "g1"
        assert snap["current_state"] == "SUPPORT_NEEDED"
        assert snap["message_count"] == 1
        assert snap["intent_counts"] == {"help": 1}
        assert snap["recent_tags"] == ["urgent", "needs_reply"]
        assert snap["needs_human_attention"] is False
