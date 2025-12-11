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
from ..domain.services.quota_service import QuotaService
from ..domain.services.language_settings_service import LanguageSettingsService
from ..domain.services.translation_flow_service import TranslationFlowService
from .dispatcher import Dispatcher
from .handlers.follow_handler import FollowHandler
from .handlers.join_handler import JoinHandler
from .handlers.leave_handler import LeaveHandler
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
    quota_service = QuotaService(repo)
    lang_settings_service = LanguageSettingsService(
        repo,
        language_pref_service,
        interface_translation,
        settings.max_group_languages,
    )
    translation_flow_service = TranslationFlowService(
        repo,
        translation_service,
        interface_translation,
        quota_service,
        max_context_messages=settings.max_context_messages,
        translation_retry=settings.translation_retry,
    )
    # サブスク関連の共通サービス
    from ..domain.services.subscription_service import SubscriptionService

    subscription_service = SubscriptionService(
        repo,
        stripe_secret_key=settings.stripe_secret_key,
        stripe_price_monthly_id=settings.stripe_price_monthly_id,
        subscription_frontend_base_url=settings.subscription_frontend_base_url,
        checkout_api_base_url=settings.checkout_api_base_url,
    )

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
        stripe_secret_key=settings.stripe_secret_key,
        stripe_price_monthly_id=settings.stripe_price_monthly_id,
        free_quota_per_month=settings.free_quota_per_month,
        pro_quota_per_month=settings.pro_quota_per_month,
        subscription_frontend_base_url=settings.subscription_frontend_base_url,
        checkout_api_base_url=settings.checkout_api_base_url,
        subscription_service=subscription_service,
        quota_service=quota_service,
        translation_flow_service=translation_flow_service,
        language_settings_service=lang_settings_service,
    )
    postback_handler = PostbackHandler(
        line_client,
        repo,
        max_group_languages=settings.max_group_languages,
        interface_translation=interface_translation,
        subscription_service=subscription_service,
        language_settings_service=lang_settings_service,
    )
    join_handler = JoinHandler(line_client, repo)
    leave_handler = LeaveHandler(subscription_service, repo)
    member_joined_handler = MemberJoinedHandler(line_client, repo)
    follow_handler = FollowHandler(line_client)

    handlers = {
        "message": message_handler,
        "postback": postback_handler,
        "join": join_handler,
        "leave": leave_handler,
        "memberJoined": member_joined_handler,
        "follow": follow_handler,
    }
    return Dispatcher(handlers)
