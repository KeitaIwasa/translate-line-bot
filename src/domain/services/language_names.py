from __future__ import annotations

import re

# 言語コードそのものを文に出さない。langdetect のプロファイルと、
# 文字体系の判定で使う言語を英語名で持つ。表示名の正はグループの lang_name。
ENGLISH_LANGUAGE_NAMES = {
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ms": "Malay",
    "my": "Burmese",
    "nb": "Norwegian Bokmål",
    "ne": "Nepali",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "ps": "Pashto",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sa": "Sanskrit",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-tw": "Traditional Chinese",
}

# ja / en の一覧は翻訳せず、ここから組み立てる
JAPANESE_LANGUAGE_NAMES = {
    "af": "アフリカーンス語",
    "am": "アムハラ語",
    "ar": "アラビア語",
    "be": "ベラルーシ語",
    "bg": "ブルガリア語",
    "bn": "ベンガル語",
    "ca": "カタルーニャ語",
    "cs": "チェコ語",
    "cy": "ウェールズ語",
    "da": "デンマーク語",
    "de": "ドイツ語",
    "el": "ギリシャ語",
    "en": "英語",
    "es": "スペイン語",
    "et": "エストニア語",
    "eu": "バスク語",
    "fa": "ペルシア語",
    "fi": "フィンランド語",
    "fr": "フランス語",
    "ga": "アイルランド語",
    "gu": "グジャラート語",
    "he": "ヘブライ語",
    "hi": "ヒンディー語",
    "hr": "クロアチア語",
    "hu": "ハンガリー語",
    "hy": "アルメニア語",
    "id": "インドネシア語",
    "it": "イタリア語",
    "ja": "日本語",
    "ka": "ジョージア語",
    "km": "クメール語",
    "kn": "カンナダ語",
    "ko": "韓国語",
    "lo": "ラオ語",
    "lt": "リトアニア語",
    "lv": "ラトビア語",
    "mk": "マケドニア語",
    "ml": "マラヤーラム語",
    "mr": "マラーティー語",
    "ms": "マレー語",
    "my": "ミャンマー語",
    "nb": "ノルウェー語（ブークモール）",
    "ne": "ネパール語",
    "nl": "オランダ語",
    "nn": "ノルウェー語（ニーノシュク）",
    "no": "ノルウェー語",
    "pa": "パンジャブ語",
    "pl": "ポーランド語",
    "ps": "パシュトー語",
    "pt": "ポルトガル語",
    "ro": "ルーマニア語",
    "ru": "ロシア語",
    "sa": "サンスクリット語",
    "si": "シンハラ語",
    "sk": "スロバキア語",
    "sl": "スロベニア語",
    "so": "ソマリ語",
    "sq": "アルバニア語",
    "sr": "セルビア語",
    "sv": "スウェーデン語",
    "sw": "スワヒリ語",
    "ta": "タミル語",
    "te": "テルグ語",
    "th": "タイ語",
    "tl": "タガログ語",
    "tr": "トルコ語",
    "uk": "ウクライナ語",
    "ur": "ウルドゥー語",
    "vi": "ベトナム語",
    "zh": "中国語",
    "zh-cn": "中国語",
    "zh-tw": "繁体字中国語",
}

UNKNOWN_LANGUAGE_NAME = "Unknown language"
UNKNOWN_LANGUAGE_NAME_JA = "不明な言語"
UNKNOWN_LANGUAGE_NAME_ZH = "未知语言"
UNKNOWN_LANGUAGE_NAME_KO = "알 수 없는 언어"
UNKNOWN_LANGUAGE_NAME_TH = "ภาษาที่ไม่รู้จัก"

# 固定文の言語名。Gemini には渡さない。表に無いコードは英語名に戻す。
_LOCAL_LANGUAGE_NAMES = {
    "zh": {
        "ar": "阿拉伯语",
        "bn": "孟加拉语",
        "de": "德语",
        "en": "英语",
        "es": "西班牙语",
        "fr": "法语",
        "hi": "印地语",
        "id": "印尼语",
        "it": "意大利语",
        "ja": "日语",
        "ko": "韩语",
        "ms": "马来语",
        "my": "缅甸语",
        "ne": "尼泊尔语",
        "pt": "葡萄牙语",
        "ru": "俄语",
        "sa": "梵语",
        "th": "泰语",
        "tl": "他加禄语",
        "uk": "乌克兰语",
        "vi": "越南语",
        "zh": "中文",
        "zh-cn": "中文",
        "zh-tw": "繁体中文",
    },
    "ko": {
        "ar": "아랍어",
        "bn": "벵골어",
        "de": "독일어",
        "en": "영어",
        "es": "스페인어",
        "fr": "프랑스어",
        "hi": "힌디어",
        "id": "인도네시아어",
        "it": "이탈리아어",
        "ja": "일본어",
        "ko": "한국어",
        "ms": "말레이어",
        "my": "미얀마어",
        "ne": "네팔어",
        "pt": "포르투갈어",
        "ru": "러시아어",
        "sa": "산스크리트어",
        "th": "태국어",
        "tl": "타갈로그어",
        "uk": "우크라이나어",
        "vi": "베트남어",
        "zh": "중국어",
        "zh-cn": "중국어",
        "zh-tw": "번체 중국어",
    },
    "th": {
        "ar": "อาหรับ",
        "bn": "เบงกาลี",
        "de": "เยอรมัน",
        "en": "อังกฤษ",
        "es": "สเปน",
        "fr": "ฝรั่งเศส",
        "hi": "ฮินดี",
        "id": "อินโดนีเซีย",
        "it": "อิตาลี",
        "ja": "ญี่ปุ่น",
        "ko": "เกาหลี",
        "ms": "มลายู",
        "my": "พม่า",
        "ne": "เนปาล",
        "pt": "โปรตุเกส",
        "ru": "รัสเซีย",
        "sa": "สันสกฤต",
        "th": "ไทย",
        "tl": "ตากาล็อก",
        "uk": "ยูเครน",
        "vi": "เวียดนาม",
        "zh": "จีน",
        "zh-cn": "จีน",
        "zh-tw": "จีนตัวเต็ม",
    },
}

_UNKNOWN_BY_FAMILY = {
    "zh": UNKNOWN_LANGUAGE_NAME_ZH,
    "ko": UNKNOWN_LANGUAGE_NAME_KO,
    "th": UNKNOWN_LANGUAGE_NAME_TH,
}

# 2〜3文字や zh-tw のようなコードを、言語名として採用しない
_CODE_TOKEN = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
_ENGLISH_NAME_FOLDS = {name.casefold() for name in ENGLISH_LANGUAGE_NAMES.values()}


def english_language_name(code: str, stored_name: str = "") -> str:
    """保存された表示名、なければ英語名。コードのままは返さない。"""

    normalized = _normalize_code(code)
    usable = _usable_stored_name(normalized, stored_name)
    canonical = ENGLISH_LANGUAGE_NAMES.get(normalized, "")
    if usable and canonical and usable.casefold() == canonical.casefold():
        return canonical
    if usable:
        return usable
    return canonical or UNKNOWN_LANGUAGE_NAME


def localized_language_name(code: str, family: str, stored_name: str = "") -> str:
    """ja/en/zh/ko/th の固定名。英語の保存名は表の名前に戻し、独自の保存名はそのまま使う。"""

    if family == "ja":
        return japanese_language_name(code, stored_name)
    if family not in _LOCAL_LANGUAGE_NAMES:
        return english_language_name(code, stored_name)
    normalized = _normalize_code(code)
    usable = _usable_stored_name(normalized, stored_name)
    canonical = ENGLISH_LANGUAGE_NAMES.get(normalized, "")
    japanese = JAPANESE_LANGUAGE_NAMES.get(normalized, "")
    local = _LOCAL_LANGUAGE_NAMES[family].get(normalized)
    # 英語名・日本語名は表の名前に戻す。表に無い独自の保存名だけ残す
    if usable and usable.casefold() != canonical.casefold() and usable != japanese and usable != local:
        return usable
    if local:
        return local
    if canonical:
        return canonical
    return _UNKNOWN_BY_FAMILY[family]


def japanese_language_name(code: str, stored_name: str = "") -> str:
    """日本語の一覧用。英語の保存名は日本語名に置き、独自の保存名はそのまま使う。"""

    normalized = _normalize_code(code)
    usable = _usable_stored_name(normalized, stored_name)
    canonical = ENGLISH_LANGUAGE_NAMES.get(normalized, "")
    if usable and usable.casefold() != canonical.casefold():
        return usable
    japanese = JAPANESE_LANGUAGE_NAMES.get(normalized)
    if japanese:
        return japanese
    if usable:
        return usable
    if canonical:
        return canonical
    return UNKNOWN_LANGUAGE_NAME_JA


def _normalize_code(code: str) -> str:
    return (code or "").strip().lower().replace("_", "-")


def _usable_stored_name(code: str, stored_name: str) -> str:
    """言語コードや空文字は表示名にしない。"""

    name = (stored_name or "").strip()
    if not name:
        return ""
    folded = name.casefold().replace("_", "-")
    if folded == code:
        return ""
    if folded in ENGLISH_LANGUAGE_NAMES:
        return ""
    if _CODE_TOKEN.fullmatch(folded) and folded not in _ENGLISH_NAME_FOLDS:
        return ""
    return name
