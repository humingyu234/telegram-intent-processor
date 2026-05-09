"""Group-level conversation state machine."""

from dataclasses import dataclass, field

from app.models import GroupState, Intent


# Intent -> resulting state mapping.
# COMPLAINT is highest priority and overrides any prior state.
INTENT_TO_STATE: dict[Intent, GroupState] = {
    Intent.PRICING: GroupState.PRICING_DISCUSSION,
    Intent.PRODUCT: GroupState.PRODUCT_DISCUSSION,
    Intent.HELP: GroupState.SUPPORT_NEEDED,
    Intent.COMPLAINT: GroupState.COMPLAINT_ESCALATED,
    Intent.OTHER: GroupState.IDLE,  # OTHER does NOT change state
}


@dataclass
class GroupStateMachine:
    """Tracks a single group's conversation state over time."""

    group_id: str
    current_state: GroupState = GroupState.IDLE
    message_count: int = 0
    intent_counts: dict[str, int] = field(default_factory=dict)
    last_intent: Intent | None = None
    recent_tags: list[str] = field(default_factory=list)
    needs_human_attention: bool = False

    def apply(
        self,
        intent: Intent,
        tags: list[str],
    ) -> None:
        """Apply one classified message to this group's state.

        State transition rules:
        - COMPLAINT always wins (highest business priority).
        - OTHER does NOT change the current state.
        - Other intents move the state to the corresponding stage.
        """
        self.message_count += 1

        # Update intent counts
        key = intent.value
        self.intent_counts[key] = self.intent_counts.get(key, 0) + 1

        self.last_intent = intent
        self.recent_tags = tags

        # State transition
        if intent == Intent.OTHER:
            return  # OTHER is neutral — does not move state

        new_state = INTENT_TO_STATE[intent]
        self.current_state = new_state

        # Human attention flag — only raised by complaints
        if intent == Intent.COMPLAINT:
            self.needs_human_attention = True

    def snapshot(self) -> dict:
        return {
            "group_id": self.group_id,
            "current_state": self.current_state.value,
            "message_count": self.message_count,
            "intent_counts": self.intent_counts,
            "last_intent": self.last_intent.value if self.last_intent else None,
            "recent_tags": self.recent_tags[-5:],
            "needs_human_attention": self.needs_human_attention,
        }
