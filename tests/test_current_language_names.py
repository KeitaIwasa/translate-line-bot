"""現在の翻訳言語の一覧が、コードではなく言語名になること。"""

import re

import langdetect.detector_factory as detector_factory

from src.domain.services.current_languages_question import (
    current_languages_base_text,
    current_languages_localized_text,
)
from src.domain.services.language_detection_service import _LATIN_LANGUAGES, _SCRIPT_LANGUAGES
from src.domain.services.language_names import (
    ENGLISH_LANGUAGE_NAMES,
    JAPANESE_LANGUAGE_NAMES,
    UNKNOWN_LANGUAGE_NAME,
    english_language_name,
)
from src.infra.neon_repositories import NeonMessageRepository
from tests.test_unknown_reply_language import _Line, _Repo, _Translator, _event, _handler

_LISTED = ("sa", "ne", "my", "tl", "uk", "bn", "xyz")
_CODE_WORD = re.compile(r"(?<![A-Za-z])(?:sa|ne|my|tl|uk|bn|xyz)(?![A-Za-z])", re.IGNORECASE)


class _Cursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params):
        self.executed.append((str(query), params))

    def fetchall(self):
        return list(self.rows)


class _Client:
    def __init__(self, rows=None):
        self.cursor_obj = _Cursor(rows=rows)

    def cursor(self):
        return self.cursor_obj


class _NamedRepo(_Repo):
    def __init__(self, records):
        super().__init__([code for code, _name in records])
        self.records = list(records)

    def fetch_group_language_records(self, _group_id):
        return list(self.records)


def test_name_table_covers_langdetect_and_app_languages():
    detector_factory.init_factory()
    codes = set(detector_factory._factory.langlist)
    codes.update(_LATIN_LANGUAGES)
    for group in _SCRIPT_LANGUAGES.values():
        codes.update(group)
    assert set(ENGLISH_LANGUAGE_NAMES) == set(JAPANESE_LANGUAGE_NAMES)
    for code, name in ENGLISH_LANGUAGE_NAMES.items():
        assert name.casefold() != code.casefold()
    for code in codes:
        name = english_language_name(code)
        assert name != UNKNOWN_LANGUAGE_NAME
        assert name.casefold() != code.casefold()


def test_unmapped_codes_use_english_names_and_never_the_code():
    assert english_language_name("sa") == "Sanskrit"
    assert english_language_name("ne") == "Nepali"
    assert english_language_name("my") == "Burmese"
    assert english_language_name("tl") == "Tagalog"
    assert english_language_name("uk") == "Ukrainian"
    assert english_language_name("bn") == "Bengali"
    assert english_language_name("ms") == "Malay"
    assert english_language_name("xyz") == UNKNOWN_LANGUAGE_NAME

    text = current_languages_base_text(_LISTED)
    assert text == (
        "The current translation languages are "
        "Sanskrit, Nepali, Burmese, Tagalog, Ukrainian, Bengali, Unknown language."
    )
    assert _CODE_WORD.search(text) is None


def test_stored_language_name_wins_when_it_is_not_a_code():
    text = current_languages_base_text([("sa", "Vedic Sanskrit"), ("ne", "ne"), ("xyz", "Klingon")])
    assert text == "The current translation languages are Vedic Sanskrit, Nepali, Klingon."
    assert _CODE_WORD.search(text) is None


def test_japanese_and_english_lists_are_deterministic():
    japanese = current_languages_localized_text(_LISTED, "ja")
    english = current_languages_localized_text(_LISTED, "en-US")
    assert japanese == (
        "現在の翻訳言語はサンスクリット語、ネパール語、ミャンマー語、タガログ語、ウクライナ語、ベンガル語、不明な言語です。"
    )
    assert english == current_languages_base_text(_LISTED)
    assert current_languages_localized_text(_LISTED, "ko") is None
    assert current_languages_localized_text([], "ja") == "翻訳言語はまだ設定されていません。"
    assert _CODE_WORD.search(japanese) is None


def test_show_languages_uses_stored_names_and_translates_only_outside_ja_en():
    records = [
        ("sa", "sa"),
        ("ne", "Nepali"),
        ("my", "my"),
        ("tl", "tl"),
        ("uk", "Ukrainian"),
        ("bn", "bn"),
        ("xyz", ""),
    ]
    repo = _NamedRepo(records)
    translator = _Translator()
    handler = _handler(repo, translator)

    handler._handle_show_languages(_event("今の翻訳言語は何?"), "ja")
    assert translator.calls == []
    assert "サンスクリット語" in handler._line.last_text
    assert "ネパール語" in handler._line.last_text
    assert "ミャンマー語" in handler._line.last_text
    assert "タガログ語" in handler._line.last_text
    assert "ウクライナ語" in handler._line.last_text
    assert "ベンガル語" in handler._line.last_text
    assert "不明な言語" in handler._line.last_text
    assert _CODE_WORD.search(handler._line.last_text) is None

    line = _Line()
    handler._line = line
    handler._handle_show_languages(_event("what languages are set?"), "ko")
    assert translator.calls
    translated = translator.calls[0]["message_text"]
    assert translator.calls[0]["candidate_languages"] == ["ko"]
    assert "Sanskrit" in translated
    assert "Nepali" in translated
    assert "Burmese" in translated
    assert "Tagalog" in translated
    assert "Ukrainian" in translated
    assert "Bengali" in translated
    assert "Unknown language" in translated
    assert _CODE_WORD.search(translated) is None


def test_repository_reads_stored_language_names():
    repo = NeonMessageRepository(_Client(rows=[("sa", "Sanskrit"), ("xyz", None), ("ne", "ネパール語")]))

    assert repo.fetch_group_language_records("G") == [("sa", "Sanskrit"), ("xyz", ""), ("ne", "ネパール語")]
    query, params = repo._client.cursor_obj.executed[0]
    assert "lang_name" in query
    assert params == ("G",)
