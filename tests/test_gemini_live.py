from datetime import datetime, timezone
import os

import pytest
from dotenv import load_dotenv

from translator.gemini_client import ContextMessage, GeminiClient, SourceMessage


load_dotenv(dotenv_path=".env")


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"


@pytest.mark.skipif(not GEMINI_API_KEY, reason="GEMINI_API_KEY is required for live Gemini test")
def test_gemini_live_translation():
    client = GeminiClient(api_key=GEMINI_API_KEY, model=GEMINI_MODEL, timeout_seconds=10)

    timestamp = datetime.now(tz=timezone.utc)
    source = SourceMessage(sender_name="Tester", text="Hello world", timestamp=timestamp)
    context = [ContextMessage(sender_name="Alice", text="This is context", timestamp=timestamp)]

    targets = ["ja", "fr"]
    results = client.translate(source, context, targets)

    assert results, "Gemini should return translations"
    langs = {item.lang.lower() for item in results}
    for target in targets:
        assert target in langs, f"missing translation for {target}"
