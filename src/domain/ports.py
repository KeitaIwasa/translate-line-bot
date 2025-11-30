from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import (
    ContextMessage,
    LanguageChoice,
    CommandDecision,
    LanguagePreference,
    StoredMessage,
    TranslationRequest,
    TranslationResult,
)


class LinePort:
    def reply_text(self, reply_token: str, text: str) -> None: ...

    def reply_messages(self, reply_token: str, messages) -> None: ...  # type: ignore[override]

    def get_display_name(self, source_type: str, container_id: Optional[str], user_id: str) -> Optional[str]: ...


class TranslationPort:
    def translate(self, request: TranslationRequest) -> List[TranslationResult]: ...


class CommandRouterPort:
    def decide(self, text: str) -> CommandDecision: ...


class LanguagePreferencePort:
    def analyze(self, text: str) -> LanguagePreference | None: ...


class MessageRepositoryPort:
    def ensure_group_member(self, group_id: str, user_id: str) -> None: ...

    def fetch_group_languages(self, group_id: str) -> List[str]: ...

    def fetch_recent_messages(self, group_id: str, limit: int) -> List[ContextMessage]: ...

    def insert_message(self, message: StoredMessage) -> None: ...

    def record_language_prompt(self, group_id: str) -> None: ...

    def try_complete_group_languages(self, group_id: str, languages: Sequence[Tuple[str, str]]) -> bool: ...

    def try_cancel_language_prompt(self, group_id: str) -> bool: ...

    def reset_group_language_settings(self, group_id: str) -> None: ...

    def add_group_languages(self, group_id: str, languages: Sequence[Tuple[str, str]]) -> None: ...

    def remove_group_languages(self, group_id: str, lang_codes: Sequence[str]) -> None: ...

    def set_translation_enabled(self, group_id: str, enabled: bool) -> None: ...

    def is_translation_enabled(self, group_id: str) -> bool: ...

    def record_bot_joined_at(self, group_id: str, joined_at: datetime) -> None: ...

    def fetch_bot_joined_at(self, group_id: str) -> Optional[datetime]: ...
