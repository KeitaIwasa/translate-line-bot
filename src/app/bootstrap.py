from __future__ import annotations

import logging

from ..config import get_settings
from ..domain.services.translation_service import TranslationService
from ..domain.services.interface_translation_service import InterfaceTranslationService
from ..domain.services.language_detection_service import LanguageDetectionService
from ..infra.gemini_translation import GeminiTranslationAdapter
from ..infra.language_pref_client import LanguagePreferenceAdapter
from ..infra.command_router import GeminiCommandRouter
from ..infra.line_api import LineApiAdapter
from ..infra.neon_client import get_client
from ..infra.neon_repositories import NeonMessageRepository
from .dispatcher import Dispatcher
from .handlers.follow_handler import FollowHandler
from .handlers.join_handler import JoinHandler
from .handlers.member_joined_handler import MemberJoinedHandler
from .handlers.message_handler import MessageHandler
from .handlers.postback_handler import PostbackHandler


def build_dispatcher() -> Dispatcher:
    settings = get_settings()

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, force=True)

    line_client = LineApiAdapter(settings.line_channel_access_token)
    translation_adapter = GeminiTranslationAdapter(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
    translation_service = TranslationService(translation_adapter)
    interface_translation = InterfaceTranslationService(translation_adapter)
    language_detector = LanguageDetectionService()
    language_pref_service = LanguagePreferenceAdapter(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
    command_router = GeminiCommandRouter(
        api_key=settings.gemini_api_key,
        model=settings.command_model,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
    db_client = get_client(settings.neon_database_url)
    repo = NeonMessageRepository(db_client, max_group_languages=settings.max_group_languages)

    message_handler = MessageHandler(
        line_client=line_client,
        translation_service=translation_service,
        language_pref_service=language_pref_service,
        command_router=command_router,
        repo=repo,
        max_context_messages=settings.max_context_messages,
        max_group_languages=settings.max_group_languages,
        translation_retry=settings.translation_retry,
        bot_mention_name=settings.bot_mention_name,
        interface_translation=interface_translation,
        language_detector=language_detector,
    )
    postback_handler = PostbackHandler(
        line_client,
        repo,
        max_group_languages=settings.max_group_languages,
        interface_translation=interface_translation,
    )
    join_handler = JoinHandler(line_client, repo)
    member_joined_handler = MemberJoinedHandler(line_client, repo)
    follow_handler = FollowHandler(line_client)

    handlers = {
        "message": message_handler,
        "postback": postback_handler,
        "join": join_handler,
        "memberJoined": member_joined_handler,
        "follow": follow_handler,
    }
    return Dispatcher(handlers)
