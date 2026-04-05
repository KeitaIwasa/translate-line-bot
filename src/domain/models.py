from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence


# === Webhook domain events ===
@dataclass(frozen=True)
class BaseEvent:
    event_type: str
    reply_token: Optional[str]
    group_id: Optional[str]
    user_id: Optional[str]
    sender_type: str
    timestamp: int = 0


@dataclass(frozen=True)
class Mentionee:
    index: int
    length: int
    mention_type: str = ""
    user_id: Optional[str] = None
    is_self: bool = False


@dataclass(frozen=True)
class MessageEvent(BaseEvent):
    text: str = ""
    destination: Optional[str] = None
    mentionees: List[Mentionee] = field(default_factory=list)


@dataclass(frozen=True)
class PostbackEvent(BaseEvent):
    data: str = ""


@dataclass(frozen=True)
class JoinEvent(BaseEvent):
    pass


@dataclass(frozen=True)
class MemberJoinedEvent(BaseEvent):
    joined_user_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MemberLeftEvent(BaseEvent):
    left_user_ids: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class FollowEvent(BaseEvent):
    pass


@dataclass(frozen=True)
class LeaveEvent(BaseEvent):
    pass


# === Translation domain ===
@dataclass(frozen=True)
class TranslationRequest:
    sender_name: str
    message_text: str
    timestamp: datetime
    candidate_languages: Sequence[str]
    context_messages: Sequence["ContextMessage"]


@dataclass(frozen=True)
class ContextMessage:
    sender_name: str
    text: str
    timestamp: datetime


@dataclass(frozen=True)
class TranslationResult:
    lang: str
    text: str


# === Command routing ===
@dataclass(frozen=True)
class CommandDecision:
    action: str  # language_settings | howto | pause | resume | unknown | error | subscription_menu | subscription_cancel | subscription_upgrade
    operation: str = ""  # reset_all | add | remove | add_and_remove (for language_settings)
    languages_to_add: List[LanguageChoice] = field(default_factory=list)
    languages_to_remove: List[LanguageChoice] = field(default_factory=list)
    instruction_language: str = ""
    ack_text: str = ""


# === Group language settings ===
@dataclass(frozen=True)
class LanguageChoice:
    code: str
    name: str


@dataclass(frozen=True)
class LanguagePreference:
    supported: List[LanguageChoice]
    unsupported: List[LanguageChoice] = field(default_factory=list)
    confirm_label: str = "OK"
    cancel_label: str = "Cancel"
    primary_language: str = ""


@dataclass(frozen=True)
class StoredMessage:
    group_id: str
    user_id: str
    sender_name: str
    text: str
    timestamp: datetime
    message_role: str = "user"
    is_encrypted: bool = False
    encrypted_body: Optional[str] = None
    encryption_version: Optional[str] = None


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    sender_name: str
    text: str
    timestamp: datetime


@dataclass(frozen=True)
class TranslationRuntimeState:
    translation_enabled: bool
    group_languages: List[str]
    subscription_status: Optional[str]
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    period_key: str
    usage: int
    limit_notice_plan: Optional[str]
    entitlement_plan: str = "free"
    billing_interval: str = "month"
    is_grandfathered: bool = False
    quota_anchor_day: Optional[int] = None
    scheduled_target_price_id: Optional[str] = None
    scheduled_effective_at: Optional[datetime] = None


@dataclass(frozen=True)
class PrivateChatResponse:
    output_text: str
    safe_input_text: str
    safe_output_text: str
    guardrails_failed: bool = False


# === Reply DTO ===
@dataclass(frozen=True)
class ReplyBundle:
    """送信用メッセージを束ねるシンプルな DTO。"""

    texts: Sequence[str] = field(default_factory=tuple)
    messages: Sequence[dict] = field(default_factory=tuple)
