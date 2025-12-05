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
class MessageEvent(BaseEvent):
    text: str = ""


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
class FollowEvent(BaseEvent):
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
    action: str  # language_settings | howto | pause | resume | unknown
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
