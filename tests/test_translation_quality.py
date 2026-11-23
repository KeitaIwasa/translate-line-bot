"""翻訳品質シグナル。本文を出さず、壊れた訳だけ suspect にする。"""

import json
from datetime import datetime, timezone

import pytest

from src.domain.models import TranslationRequest
from src.infra.gemini_translation import GeminiTranslationAdapter
from src.infra.provider_failure import METRIC_NAMESPACE
from src.infra.translation_quality import (
    LETTER_RUN_MIN,
    LETTER_RUN_MIN_SHORT_SOURCE,
    MARK_RUN_MIN,
    NGRAM_MIN_COVERAGE,
    NGRAM_MIN_REPEATS,
    RATIO_HIGH,
    RATIO_HIGH_MIN_SOURCE,
    RATIO_LOW,
    RATIO_LOW_MIN_SOURCE,
    SHORT_REPETITIVE_MAX_CHARS,
    WORD_REPEAT_MIN,
    assess_translation_quality,
    emit_translation_quality,
)

EN_SOURCE = "Please meet at the station tomorrow morning."
JA_SOURCE = "明日の朝、駅で会いましょう。"


def _assess(source: str, translations: list[dict], *, targets=None, body=None, model="gemini-3.5-flash-lite"):
    langs = targets if targets is not None else [item["lang"] for item in translations]
    return assess_translation_quality(
        model=model,
        response_body=body if body is not None else {"candidates": [{"finishReason": "STOP"}]},
        source_text=source,
        raw_items=translations,
        target_languages=langs,
    )


def _reasons(report: dict) -> set[str]:
    return set(filter(None, report["reasons"].split(",")))


# 文字数は空白を含む。比のしきい値より短い英語は ratio を見ない長さにしてある
NORMAL_PAIRS = [
    ("ja", EN_SOURCE, "明日の朝、駅で会いましょう。"),
    ("en", JA_SOURCE, "Let's meet at the station tomorrow morning."),
    ("ko", EN_SOURCE, "내일 아침 역에서 만나요."),
    ("th", EN_SOURCE, "พรุ่งนี้เช้าเจอกันที่สถานี"),
    ("zh-TW", EN_SOURCE, "明天早上在車站見面。"),
    ("zh-CN", EN_SOURCE, "明天早上在车站见面。"),
    ("yue", EN_SOURCE, "聽日朝早喺車站見。"),
    ("vi", EN_SOURCE, "Sáng mai gặp nhau ở ga."),
    ("ne", EN_SOURCE, "भोलि बिहान स्टेशनमा भेटौँला।"),
    ("my", EN_SOURCE, "မနက်ဖြန် မနက် ဘူတာရုံမှာ တွေ့မယ်။"),
    ("id", EN_SOURCE, "Besok pagi kita bertemu di stasiun."),
    ("es", EN_SOURCE, "Mañana por la mañana nos vemos en la estación."),
    ("fr", EN_SOURCE, "On se voit demain matin à la gare."),
    ("ar", EN_SOURCE, "نراك صباح الغد في المحطة."),
    ("hi", EN_SOURCE, "कल सुबह स्टेशन पर मिलते हैं।"),
    ("ru", EN_SOURCE, "Завтра утром встретимся на станции."),
]


@pytest.mark.parametrize("lang,source,text", NORMAL_PAIRS)
def test_natural_translations_are_not_suspect(lang, source, text):
    report = _assess(source, [{"lang": lang, "text": text}])
    assert report["suspect"] is False, report
    assert report["reasons"] == ""
    assert report["sourceKind"] in {"text", "short"}
    assert text not in json.dumps(report, ensure_ascii=False)
    assert source not in json.dumps(report, ensure_ascii=False)


def test_thresholds_match_the_examples_they_were_chosen_for():
    """しきい値は、観測した壊れ方と正当な笑いを分けられる位置に固定する。"""

    # 「ててててて」は5連続。笑いの3〜4連続はここまで届かない
    assert LETTER_RUN_MIN == 5
    # www などを違う文字へ引き伸ばしても、12未満は壊れていない
    assert LETTER_RUN_MIN_SHORT_SOURCE == 12
    assert SHORT_REPETITIVE_MAX_CHARS == 16
    # 「!!!!」や「555」は記号・数字の8連続未満
    assert MARK_RUN_MIN == 8
    # 「テスト」4回で12文字。3回（9文字）は自然な繰り返しの範囲
    assert NGRAM_MIN_REPEATS == 4
    assert NGRAM_MIN_COVERAGE == 12
    # 同一語の退化は10回以上
    assert WORD_REPEAT_MIN == 10
    # 短文は訳が数倍になっても普通。12文字以上で8倍、40文字以上で0.2倍未満
    assert RATIO_HIGH == 8.0
    assert RATIO_HIGH_MIN_SOURCE == 12
    assert RATIO_LOW == 0.15
    assert RATIO_LOW_MIN_SOURCE == 40


def test_simplified_only_han_in_japanese_is_suspect():
    report = _assess(EN_SOURCE, [{"lang": "ja", "text": "冈朝、駅で会いましょう。"}])
    assert report["suspect"] is True
    assert "rare_han" in _reasons(report)

    # 再が冈に置き換わった形。原文に冈は無い
    again = _assess("Please try again tomorrow.", [{"lang": "ja", "text": "冈度試してください。"}])
    assert "rare_han" in _reasons(again)


def test_japanese_kanji_outside_cp932_is_not_rare_han():
    """JIS X 0213 の嚙や𠮷は cp932 に無いが、簡体字の混入ではない。"""

    report = _assess(EN_SOURCE, [{"lang": "ja", "text": "駅で嚙み合って、𠮷田さんに会う。"}])
    assert "rare_han" not in _reasons(report)
    assert report["suspect"] is False


def test_kana_run_and_repeated_word_are_suspect():
    stutter = _assess(EN_SOURCE, [{"lang": "ja", "text": "冈てててててステスト"}])
    assert stutter["suspect"] is True
    assert {"rare_han", "repeat_run"} <= _reasons(stutter)
    assert stutter["maxRun"] >= LETTER_RUN_MIN

    word = "hello "
    repeated = _assess(EN_SOURCE, [{"lang": "en", "text": word * WORD_REPEAT_MIN}])
    assert "ngram_repeat" in _reasons(repeated)
    assert repeated["suspect"] is True

    # 3回の「テスト」は12文字に届かず、4回で初めて ngram
    three = _assess(EN_SOURCE, [{"lang": "ja", "text": "テスト" * 3}])
    four = _assess(EN_SOURCE, [{"lang": "ja", "text": "テスト" * 4}])
    assert "ngram_repeat" not in _reasons(three)
    assert "ngram_repeat" in _reasons(four)


def test_very_long_output_is_ratio_high_without_repeats():
    words = [
        "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
        "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    ]
    long_text = " ".join(words * 4)
    report = _assess("Please call me back today.", [{"lang": "en", "text": long_text}])
    assert "ratio_high" in _reasons(report)
    assert "ngram_repeat" not in _reasons(report)
    assert "repeat_run" not in _reasons(report)
    assert report["maxRatio"] >= RATIO_HIGH


def test_very_short_output_is_ratio_low():
    source = " ".join(f"clause {index} stays distinct in this note" for index in range(6))
    report = _assess(source, [{"lang": "ja", "text": "短い"}])
    assert len(source) >= RATIO_LOW_MIN_SOURCE
    assert "ratio_low" in _reasons(report)
    assert report["suspect"] is True


def test_unexpected_script_for_korean_and_thai():
    korean = _assess(EN_SOURCE, [{"lang": "ko", "text": "내일 역에서 て 만나요"}])
    assert "unexpected_script" in _reasons(korean)

    thai = _assess(EN_SOURCE, [{"lang": "th", "text": "พรุ่งนี้ 駅"}])
    assert "unexpected_script" in _reasons(thai)

    # 原文にある文字体系は、名前の写しとして残ってよい
    copied = _assess("駅で待ってて", [{"lang": "th", "text": "รอที่ 駅"}])
    assert "unexpected_script" not in _reasons(copied)


def test_max_tokens_is_suspect_even_when_the_text_looks_fine():
    report = _assess(
        EN_SOURCE,
        [{"lang": "ja", "text": "明日の朝、駅で会いましょう。"}],
        body={"candidates": [{"finishReason": "MAX_TOKENS"}]},
    )
    assert report["finishReason"] == "MAX_TOKENS"
    assert report["suspect"] is True
    assert _reasons(report) == {"finish_not_stop"}
    assert report["reason_finish_not_stop"] == 1


def test_source_repetition_is_not_a_broken_translation():
    cases = [
        ("www", "ja", "wwwwww"),
        ("www", "ja", "笑笑笑"),
        ("555555", "th", "555555555"),
        ("555555", "ja", "ごごご"),
        ("ㅋㅋㅋㅋ", "ko", "ㅋㅋㅋㅋㅋㅋ"),
        ("ㅋㅋㅋㅋ", "ja", "笑笑笑"),
        ("笑笑笑", "en", "hahaha"),
        ("笑笑笑", "ja", "笑笑笑笑"),
        ("！！！", "en", "!!!!"),
        ("!!!!", "ja", "！！！！"),
    ]
    for source, lang, text in cases:
        report = _assess(source, [{"lang": lang, "text": text}])
        assert report["suspect"] is False, (source, lang, text, report)
        assert "source_repeat" in report["sourceFlags"]


def test_source_shapes_are_classified_without_false_suspect():
    numbered = "1. apples\n2. bread\n3. milk"
    long_source = " ".join(f"sentence {index} talks about the station" for index in range(30))
    long_translation = " ".join(f"文{index}は駅の話です" for index in range(80))
    cases = [
        ("ca", "Demà al matí ens veiem a l'estació.", "Fins demà al matí a l'estació central.", "text"),
        ("ja", "明日 meet at the station please", "明日の朝に駅で会いましょう。", "mixed"),
        ("ja", "😀😀", "笑顔です", "emoji_only"),
        ("en", "20261123", "twenty twenty six", "numeric_only"),
        ("en", "?!?!", "Really", "symbol_only"),
        ("en", "https://example.com/path", "https://example.com/path", "url_only"),
        ("en", numbered, "apples\nbread\nmilk", "list"),
        ("ja", long_source, long_translation, "long"),
        ("ja", "", "明日", "empty"),
        ("ja", "   \n", "明日", "whitespace"),
        ("ja", "hello\u200bthere", "こんにちは、そこに。", "short"),
    ]
    for lang, source, text, kind in cases:
        report = _assess(source, [{"lang": lang, "text": text}])
        assert report["sourceKind"] == kind, (source, report)
        assert report["suspect"] is False, (kind, report)
    zero = _assess("hello\u200bthere", [{"lang": "en", "text": "こんにちは、そこに。"}])
    assert "zero_width" in zero["sourceFlags"]
    assert "\u200b" not in json.dumps(zero)


def test_missing_usage_and_finish_reason_do_not_fail():
    report = _assess(
        EN_SOURCE,
        [{"lang": "ja", "text": "明日の朝、駅で会いましょう。"}],
        body={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
    )
    assert report["finishReason"] is None
    assert report["promptTokenCount"] is None
    assert report["candidatesTokenCount"] is None
    assert report["thoughtsTokenCount"] is None
    assert report["suspect"] is False

    partial = _assess(
        EN_SOURCE,
        [{"lang": "en", "text": "See you at the station tomorrow."}],
        body={"usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4}},
    )
    assert partial["promptTokenCount"] == 12
    assert partial["candidatesTokenCount"] == 4
    assert partial["thoughtsTokenCount"] is None


def test_echo_cleanup_marks_emptied_without_putting_text_in_the_report():
    source = "Please meet at the station tomorrow morning."
    report = _assess(source, [{"lang": "ja", "text": source}])
    assert report["outputs"][0]["emptied"] is True
    assert "emptied_by_cleanup" in _reasons(report)
    assert report["suspect"] is True
    dumped = json.dumps(report, ensure_ascii=False)
    assert source not in dumped
    assert "Please" not in dumped


def test_url_echo_is_not_suspect():
    url = "https://example.com/docs/setup"
    report = _assess(url, [{"lang": "ja", "text": url}])
    assert report["sourceKind"] == "url_only"
    assert report["outputs"][0]["emptied"] is True
    assert report["suspect"] is False


def test_assess_ignores_target_languages_outside_the_group():
    report = _assess(
        EN_SOURCE,
        [
            {"lang": "ja", "text": "明日の朝、駅で会いましょう。"},
            {"lang": "xx", "text": "冈" * 8},
        ],
        targets=["ja"],
    )
    assert [item["lang"] for item in report["outputs"]] == ["ja"]
    assert report["suspect"] is False


def test_long_input_stays_fast_and_is_not_suspect():
    import time

    source = " ".join(f"sentence {index} talks about tomorrow" for index in range(200))
    text = " ".join(f"文{index}は明日の駅の話です" for index in range(500))
    started = time.perf_counter()
    report = _assess(source, [{"lang": "ja", "text": text}])
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 100, elapsed_ms
    assert report["sourceKind"] == "long"
    assert report["suspect"] is False


def test_emit_is_one_json_line_with_metrics_and_no_body(capsys, monkeypatch):
    monkeypatch.setenv("KOTORI_STAGE", "prod")
    source = "UNIQUE_SOURCE_SENTINEL_qx7 meets at the station."
    text = "UNIQUE_TARGET_SENTINEL_qm3 は駅です"
    payload = emit_translation_quality(
        model="gemini-3.5-flash-lite",
        response_body={
            "candidates": [{"finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 8,
                "thoughtsTokenCount": 3,
            },
        },
        source_text=source,
        raw_items=[{"lang": "ja", "text": text}],
        target_languages=["ja", "ko"],
    )
    line = capsys.readouterr().out
    assert line.count("\n") == 1
    logged = json.loads(line)
    assert logged == payload
    assert source not in line
    assert text not in line
    assert "SENTINEL" not in line
    assert logged["message"] == "translation_quality"
    assert logged["Stage"] == "prod"
    assert logged["Suspect"] == "false"
    assert logged["targetCount"] == 2
    assert logged["targetLangs"] == "ja,ko"
    assert logged["promptTokenCount"] == 20
    assert logged["thoughtsTokenCount"] == 3
    assert logged["calls_ja"] == 1
    assert logged["suspect_ja"] == 0
    metric = logged["_aws"]["CloudWatchMetrics"][0]
    assert metric["Namespace"] == METRIC_NAMESPACE
    assert metric["Dimensions"] == [["Stage"], ["Stage", "Suspect"]]
    assert {item["Name"] for item in metric["Metrics"]} == {"TranslationQuality", "TranslationSuspect"}
    assert logged["TranslationQuality"] == 1
    assert logged["TranslationSuspect"] == 0


class _Response:
    def __init__(self, data: dict):
        self._data = data

    def json(self) -> dict:
        return self._data


class _Session:
    def __init__(self, data: dict):
        self._data = data

    def post(self, url, params=None, json=None, timeout=None):
        return _Response(self._data)


def test_adapter_logs_quality_for_a_thought_response_without_text(monkeypatch, capsys):
    source = "UNIQUE_SOURCE_SENTINEL_qx7 at the station tomorrow."
    thought = "UNIQUE_THOUGHT_SENTINEL_qt9"
    translated = "UNIQUE_TARGET_SENTINEL_qm3"
    body = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"thought": True, "text": thought},
                        {"text": json.dumps({"translations": [{"lang": "ja", "text": translated}]})},
                    ]
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 30,
            "candidatesTokenCount": 11,
            "thoughtsTokenCount": 7,
        },
    }
    monkeypatch.setattr("infra.gemini_translation.requests.Session", lambda: _Session(body))
    client = GeminiTranslationAdapter(api_key="api-key", model="gemini-3.5-flash-lite")
    result = client.translate(
        TranslationRequest(
            sender_name="Bob",
            message_text=source,
            timestamp=datetime(2026, 11, 22, tzinfo=timezone.utc),
            candidate_languages=["ja"],
            context_messages=[],
        )
    )
    assert result[0].text == translated
    logged = json.loads(capsys.readouterr().out.strip())
    blob = json.dumps(logged, ensure_ascii=False)
    assert source not in blob
    assert thought not in blob
    assert translated not in blob
    assert "SENTINEL" not in blob
    assert logged["thoughtsTokenCount"] == 7
    assert logged["finishReason"] == "STOP"
    assert logged["model"] == "gemini-3.5-flash-lite"


def test_quality_failure_does_not_change_the_translation(monkeypatch, caplog):
    source = "UNIQUE_SOURCE_SENTINEL_qx7 at the station tomorrow."
    translated = "駅で会いましょう"
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps({"translations": [{"lang": "ja", "text": translated}]})}
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr("infra.gemini_translation.requests.Session", lambda: _Session(body))

    def _boom(**_kwargs):
        raise RuntimeError("UNIQUE_ERROR_SENTINEL_qe1")

    monkeypatch.setattr("src.infra.gemini_translation.emit_translation_quality", _boom)
    client = GeminiTranslationAdapter(api_key="api-key", model="gemini-3.5-flash-lite")
    with caplog.at_level("WARNING"):
        result = client.translate(
            TranslationRequest(
                sender_name="Bob",
                message_text=source,
                timestamp=datetime(2026, 11, 22, tzinfo=timezone.utc),
                candidate_languages=["ja"],
                context_messages=[],
            )
        )
    assert result[0].lang == "ja"
    assert result[0].text == translated
    warning = " ".join(record.message for record in caplog.records if record.levelname == "WARNING")
    assert "Translation quality signal failed" in warning
    assert "RuntimeError" in warning
    assert "UNIQUE_SOURCE" not in warning
    assert "UNIQUE_ERROR" not in warning
    assert translated not in warning
