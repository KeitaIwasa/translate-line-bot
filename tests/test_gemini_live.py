from datetime import datetime, timezone
import os

import pytest
from dotenv import load_dotenv

from src.domain.models import ContextMessage, TranslationRequest
from src.infra.gemini_translation import GeminiTranslationAdapter


load_dotenv(dotenv_path=".env")


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"


@pytest.mark.skipif(not GEMINI_API_KEY, reason="GEMINI_API_KEY is required for live Gemini test")
def test_gemini_live_translation():
    client = GeminiTranslationAdapter(api_key=GEMINI_API_KEY, model=GEMINI_MODEL, timeout_seconds=10)

    timestamp = datetime.now(tz=timezone.utc)
    targets = ["ja", "fr"]
    request = TranslationRequest(
        sender_name="Tester",
        message_text="Hello world",
        timestamp=timestamp,
        candidate_languages=targets,
        context_messages=[ContextMessage(sender_name="Alice", text="This is context", timestamp=timestamp)],
    )

    results = client.translate(request)

    assert results, "Gemini should return translations"
    langs = {item.lang.lower() for item in results}
    for target in targets:
        assert target in langs, f"missing translation for {target}"
