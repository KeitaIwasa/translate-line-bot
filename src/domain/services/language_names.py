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
UNKNOWN_LANGUAGE_NAME_ZH_TW = "未知語言"
UNKNOWN_LANGUAGE_NAME_KO = "알 수 없는 언어"
UNKNOWN_LANGUAGE_NAME_TH = "ภาษาที่ไม่รู้จัก"

# 固定文の言語名。Gemini には渡さない。
# zh は簡体字（LINE の zh / zh-CN）。zh-tw は繁体字（設定ページの中文と zh-TW / zh-Hant）。
# 既存の訳は変えない。表に無いコードは英語名、表に無い UI 言語も英語名に戻す。
_LOCAL_LANGUAGE_NAMES = {
    "zh": {
        "af": "南非荷兰语",
        "am": "阿姆哈拉语",
        "ar": "阿拉伯语",
        "be": "白俄罗斯语",
        "bg": "保加利亚语",
        "bn": "孟加拉语",
        "ca": "加泰罗尼亚语",
        "cs": "捷克语",
        "cy": "威尔士语",
        "da": "丹麦语",
        "de": "德语",
        "el": "希腊语",
        "en": "英语",
        "es": "西班牙语",
        "et": "爱沙尼亚语",
        "eu": "巴斯克语",
        "fa": "波斯语",
        "fi": "芬兰语",
        "fr": "法语",
        "ga": "爱尔兰语",
        "gu": "古吉拉特语",
        "he": "希伯来语",
        "hi": "印地语",
        "hr": "克罗地亚语",
        "hu": "匈牙利语",
        "hy": "亚美尼亚语",
        "id": "印尼语",
        "it": "意大利语",
        "ja": "日语",
        "ka": "格鲁吉亚语",
        "km": "高棉语",
        "kn": "卡纳达语",
        "ko": "韩语",
        "lo": "老挝语",
        "lt": "立陶宛语",
        "lv": "拉脱维亚语",
        "mk": "马其顿语",
        "ml": "马拉雅拉姆语",
        "mr": "马拉地语",
        "ms": "马来语",
        "my": "缅甸语",
        "nb": "书面挪威语",
        "ne": "尼泊尔语",
        "nl": "荷兰语",
        "nn": "新挪威语",
        "no": "挪威语",
        "pa": "旁遮普语",
        "pl": "波兰语",
        "ps": "普什图语",
        "pt": "葡萄牙语",
        "ro": "罗马尼亚语",
        "ru": "俄语",
        "sa": "梵语",
        "si": "僧伽罗语",
        "sk": "斯洛伐克语",
        "sl": "斯洛文尼亚语",
        "so": "索马里语",
        "sq": "阿尔巴尼亚语",
        "sr": "塞尔维亚语",
        "sv": "瑞典语",
        "sw": "斯瓦希里语",
        "ta": "泰米尔语",
        "te": "泰卢固语",
        "th": "泰语",
        "tl": "他加禄语",
        "tr": "土耳其语",
        "uk": "乌克兰语",
        "ur": "乌尔都语",
        "vi": "越南语",
        "zh": "中文",
        "zh-cn": "中文",
        "zh-tw": "繁体中文",
    },
    "zh-tw": {
        "af": "南非荷蘭語",
        "am": "阿姆哈拉語",
        "ar": "阿拉伯語",
        "be": "白俄羅斯語",
        "bg": "保加利亞語",
        "bn": "孟加拉語",
        "ca": "加泰蘭語",
        "cs": "捷克語",
        "cy": "威爾斯語",
        "da": "丹麥語",
        "de": "德語",
        "el": "希臘語",
        "en": "英語",
        "es": "西班牙語",
        "et": "愛沙尼亞語",
        "eu": "巴斯克語",
        "fa": "波斯語",
        "fi": "芬蘭語",
        "fr": "法語",
        "ga": "愛爾蘭語",
        "gu": "古吉拉特語",
        "he": "希伯來語",
        "hi": "印地語",
        "hr": "克羅埃西亞語",
        "hu": "匈牙利語",
        "hy": "亞美尼亞語",
        "id": "印尼語",
        "it": "義大利語",
        "ja": "日語",
        "ka": "喬治亞語",
        "km": "高棉語",
        "kn": "卡納達語",
        "ko": "韓語",
        "lo": "寮語",
        "lt": "立陶宛語",
        "lv": "拉脫維亞語",
        "mk": "馬其頓語",
        "ml": "馬拉雅拉姆語",
        "mr": "馬拉地語",
        "ms": "馬來語",
        "my": "緬甸語",
        "nb": "書面挪威語",
        "ne": "尼泊爾語",
        "nl": "荷蘭語",
        "nn": "新挪威語",
        "no": "挪威語",
        "pa": "旁遮普語",
        "pl": "波蘭語",
        "ps": "普什圖語",
        "pt": "葡萄牙語",
        "ro": "羅馬尼亞語",
        "ru": "俄語",
        "sa": "梵語",
        "si": "僧伽羅語",
        "sk": "斯洛伐克語",
        "sl": "斯洛維尼亞語",
        "so": "索馬利語",
        "sq": "阿爾巴尼亞語",
        "sr": "塞爾維亞語",
        "sv": "瑞典語",
        "sw": "史瓦希里語",
        "ta": "坦米爾語",
        "te": "泰盧固語",
        "th": "泰語",
        "tl": "他加祿語",
        "tr": "土耳其語",
        "uk": "烏克蘭語",
        "ur": "烏爾都語",
        "vi": "越南語",
        "zh": "中文",
        "zh-cn": "簡體中文",
        "zh-tw": "繁體中文",
    },
    "ko": {
        "af": "아프리칸스어",
        "am": "암하라어",
        "ar": "아랍어",
        "be": "벨라루스어",
        "bg": "불가리아어",
        "bn": "벵골어",
        "ca": "카탈루냐어",
        "cs": "체코어",
        "cy": "웨일스어",
        "da": "덴마크어",
        "de": "독일어",
        "el": "그리스어",
        "en": "영어",
        "es": "스페인어",
        "et": "에스토니아어",
        "eu": "바스크어",
        "fa": "페르시아어",
        "fi": "핀란드어",
        "fr": "프랑스어",
        "ga": "아일랜드어",
        "gu": "구자라트어",
        "he": "히브리어",
        "hi": "힌디어",
        "hr": "크로아티아어",
        "hu": "헝가리어",
        "hy": "아르메니아어",
        "id": "인도네시아어",
        "it": "이탈리아어",
        "ja": "일본어",
        "ka": "조지아어",
        "km": "크메르어",
        "kn": "칸나다어",
        "ko": "한국어",
        "lo": "라오어",
        "lt": "리투아니아어",
        "lv": "라트비아어",
        "mk": "마케도니아어",
        "ml": "말라얄람어",
        "mr": "마라티어",
        "ms": "말레이어",
        "my": "미얀마어",
        "nb": "노르웨이어(보크몰)",
        "ne": "네팔어",
        "nl": "네덜란드어",
        "nn": "노르웨이어(뉘노르스크)",
        "no": "노르웨이어",
        "pa": "펀자브어",
        "pl": "폴란드어",
        "ps": "파슈토어",
        "pt": "포르투갈어",
        "ro": "루마니아어",
        "ru": "러시아어",
        "sa": "산스크리트어",
        "si": "싱할라어",
        "sk": "슬로바키아어",
        "sl": "슬로베니아어",
        "so": "소말리어",
        "sq": "알바니아어",
        "sr": "세르비아어",
        "sv": "스웨덴어",
        "sw": "스와힐리어",
        "ta": "타밀어",
        "te": "텔루구어",
        "th": "태국어",
        "tl": "타갈로그어",
        "tr": "터키어",
        "uk": "우크라이나어",
        "ur": "우르두어",
        "vi": "베트남어",
        "zh": "중국어",
        "zh-cn": "중국어",
        "zh-tw": "번체 중국어",
    },
    "th": {
        "af": "อาฟรีกานส์",
        "am": "อัมฮารา",
        "ar": "อาหรับ",
        "be": "เบลารุส",
        "bg": "บัลแกเรีย",
        "bn": "เบงกาลี",
        "ca": "คาตาลัน",
        "cs": "เช็ก",
        "cy": "เวลส์",
        "da": "เดนมาร์ก",
        "de": "เยอรมัน",
        "el": "กรีก",
        "en": "อังกฤษ",
        "es": "สเปน",
        "et": "เอสโตเนีย",
        "eu": "บาสก์",
        "fa": "เปอร์เซีย",
        "fi": "ฟินแลนด์",
        "fr": "ฝรั่งเศส",
        "ga": "ไอริช",
        "gu": "คุชราต",
        "he": "ฮิบรู",
        "hi": "ฮินดี",
        "hr": "โครเอเชีย",
        "hu": "ฮังการี",
        "hy": "อาร์เมเนีย",
        "id": "อินโดนีเซีย",
        "it": "อิตาลี",
        "ja": "ญี่ปุ่น",
        "ka": "จอร์เจีย",
        "km": "เขมร",
        "kn": "กันนาดา",
        "ko": "เกาหลี",
        "lo": "ลาว",
        "lt": "ลิทัวเนีย",
        "lv": "ลัตเวีย",
        "mk": "มาซิโดเนีย",
        "ml": "มาลายาลัม",
        "mr": "มราฐี",
        "ms": "มลายู",
        "my": "พม่า",
        "nb": "นอร์เวย์บุคมอล",
        "ne": "เนปาล",
        "nl": "ดัตช์",
        "nn": "นอร์เวย์นีนอร์สก์",
        "no": "นอร์เวย์",
        "pa": "ปัญจาบ",
        "pl": "โปแลนด์",
        "ps": "พัชโต",
        "pt": "โปรตุเกส",
        "ro": "โรมาเนีย",
        "ru": "รัสเซีย",
        "sa": "สันสกฤต",
        "si": "สิงหล",
        "sk": "สโลวัก",
        "sl": "สโลวีเนีย",
        "so": "โซมาลี",
        "sq": "แอลเบเนีย",
        "sr": "เซอร์เบีย",
        "sv": "สวีเดน",
        "sw": "สวาฮีลี",
        "ta": "ทมิฬ",
        "te": "เตลูกู",
        "th": "ไทย",
        "tl": "ตากาล็อก",
        "tr": "ตุรกี",
        "uk": "ยูเครน",
        "ur": "อูรดู",
        "vi": "เวียดนาม",
        "zh": "จีน",
        "zh-cn": "จีน",
        "zh-tw": "จีนตัวเต็ม",
    },
}

_UNKNOWN_BY_FAMILY = {
    "zh": UNKNOWN_LANGUAGE_NAME_ZH,
    "zh-tw": UNKNOWN_LANGUAGE_NAME_ZH_TW,
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


def _local_family(family: str) -> str:
    """固定名の表キー。簡体字と繁体字は分け、未対応はそのまま返して英語へ落とす。"""

    lowered = (family or "").strip().lower().replace("_", "-")
    if lowered in {"zh-tw", "zh-hant", "zh-hk", "zh-mo"} or lowered.startswith(
        ("zh-hant-", "zh-tw-", "zh-hk-", "zh-mo-")
    ):
        return "zh-tw"
    if lowered in {"zh", "zh-cn", "zh-hans", "zh-sg"} or lowered.startswith(("zh-hans-", "zh-cn-")):
        return "zh"
    if lowered == "ja" or lowered.startswith("ja-"):
        return "ja"
    if lowered == "ko" or lowered.startswith("ko-"):
        return "ko"
    if lowered == "th" or lowered.startswith("th-"):
        return "th"
    if lowered == "en" or lowered.startswith("en-"):
        return "en"
    return lowered


def localized_language_name(code: str, family: str, stored_name: str = "") -> str:
    """ja/en/zh/zh-tw/ko/th の固定名。英語の保存名は表の名前に戻し、独自の保存名はそのまま使う。"""

    resolved = _local_family(family)
    if resolved == "ja":
        return japanese_language_name(code, stored_name)
    if resolved not in _LOCAL_LANGUAGE_NAMES:
        return english_language_name(code, stored_name)
    normalized = _normalize_code(code)
    usable = _usable_stored_name(normalized, stored_name)
    canonical = ENGLISH_LANGUAGE_NAMES.get(normalized, "")
    japanese = JAPANESE_LANGUAGE_NAMES.get(normalized, "")
    local = _LOCAL_LANGUAGE_NAMES[resolved].get(normalized)
    # 英語名・日本語名は表の名前に戻す。表に無い独自の保存名だけ残す
    if usable and usable.casefold() != canonical.casefold() and usable != japanese and usable != local:
        return usable
    if local:
        return local
    if canonical:
        return canonical
    return _UNKNOWN_BY_FAMILY[resolved]


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
