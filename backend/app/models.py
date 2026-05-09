"""Pydantic models for the Telegram Intent Processor."""

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class Intent(str, Enum):
    PRICING = "pricing"
    PRODUCT = "product"
    HELP = "help"
    COMPLAINT = "complaint"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Group state
# ---------------------------------------------------------------------------

class GroupState(str, Enum):
    IDLE = "IDLE"
    PRODUCT_DISCUSSION = "PRODUCT_DISCUSSION"
    PRICING_DISCUSSION = "PRICING_DISCUSSION"
    SUPPORT_NEEDED = "SUPPORT_NEEDED"
    COMPLAINT_ESCALATED = "COMPLAINT_ESCALATED"


# ---------------------------------------------------------------------------
# Processing status
# ---------------------------------------------------------------------------

class ProcessingStatus(str, Enum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    INVALID = "invalid"
    FALLBACK = "fallback"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Incoming message
# ---------------------------------------------------------------------------

class IncomingMessage(BaseModel):
    message_id: str = Field(..., min_length=1, description="Unique message identifier")
    group_id: str = Field(..., min_length=1, description="Telegram group or chat id")
    user_id: str = Field(..., min_length=1, description="Sender user id")
    text: str = Field(..., min_length=1, description="Message text content")

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text must not be empty or whitespace only")
        return stripped


# ---------------------------------------------------------------------------
# Processing result
# ---------------------------------------------------------------------------

class ProcessingResult(BaseModel):
    message_id: str
    group_id: str
    user_id: str
    text: str
    intent: Intent
    tags: list[str] = Field(default_factory=list)
    status: ProcessingStatus
    reason: str = ""
    group_state_after: GroupState | None = None
    fallback_used: bool = False


# ---------------------------------------------------------------------------
# Group snapshot (what the dashboard sees)
# ---------------------------------------------------------------------------

class GroupSnapshot(BaseModel):
    group_id: str
    current_state: GroupState = GroupState.IDLE
    message_count: int = 0
    intent_counts: dict[str, int] = Field(default_factory=dict)
    last_intent: Intent | None = None
    recent_tags: list[str] = Field(default_factory=list)
    needs_human_attention: bool = False


# ---------------------------------------------------------------------------
# API request/response wrappers
# ---------------------------------------------------------------------------

class MessageRequest(BaseModel):
    message: IncomingMessage


class BatchResult(BaseModel):
    total: int
    processed: int
    invalid: int
    duplicates: int
    fallback_writes: int
    groups: int
    duration_ms: int


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------

class SystemHealth(BaseModel):
    redis: str  # "healthy" | "degraded"
    processed_messages: int = 0
    invalid_messages: int = 0
    duplicate_messages: int = 0
    fallback_writes: int = 0
    in_flight_messages: int = 0
    max_in_flight_limit: int = 50
