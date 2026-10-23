"""メンション文から言語設定の意図を、モデルに頼らず読む。

区切りのない言語名、上限を超える「全言語」、案内文そのもの、未対応言語は
ここで確定させる。UI文の原文は英語で、ja/zh/ko/th は固定訳を持つ。
翻訳関数が渡されたときは各グループ言語へ訳し、失敗したら固定訳か英語に戻す。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from ...infra.jev_language_reader import LANGUAGE_CATALOG, LANGUAGE_NAMES
from .explicit_language_reset import is_explicit_language_reset
from .language_names import (
    ENGLISH_LANGUAGE_NAMES,
    JAPANESE_LANGUAGE_NAMES,
    english_language_name,
    japanese_language_name,
    localized_language_name,
)

# カタログへ寄せる表記ゆれ。繁体字はカタログの「中国語」とは別なので寄せない。
_REMAP = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-sg": "zh",
    "fil": "tl",
    "nb": "no",
    "nn": "no",
}

# 日本語名の表に無い、実際の依頼でよく出る表記
_EXTRA_ALIASES: tuple[tuple[str, str], ...] = (
    ("中文", "zh"),
    ("简体中文", "zh"),
    ("简体中国语", "zh"),
    ("簡体中国語", "zh"),
    ("簡体字中国語", "zh"),
    ("簡体字", "zh"),
    ("简体字", "zh"),
    ("simplified chinese", "zh"),
    ("繁体中国語", "zh-tw"),
    ("繁體中文", "zh-tw"),
    ("繁体字中国語", "zh-tw"),
    ("繁体字", "zh-tw"),
    ("traditional chinese", "zh-tw"),
    ("韓国語", "ko"),
    ("かんこくご", "ko"),
    ("한국어", "ko"),
    ("タイ語", "th"),
    ("たいご", "th"),
    ("ไทย", "th"),
    ("にほんご", "ja"),
    ("えいご", "en"),
    ("フィリピン語", "tl"),
    ("タガログ語", "tl"),
    ("filipino", "tl"),
    ("日文", "ja"),
    ("日語", "ja"),
    ("英文", "en"),
    ("汉语", "zh"),
    ("漢語", "zh"),
    ("韩语", "ko"),
    ("韓語", "ko"),
    ("韩文", "ko"),
    ("韓文", "ko"),
)

# 韓国語・簡体字・繁体字・タイ語・自称。カタログに無いコードへは寄せない。
_CROSS_SCRIPT_ALIASES: tuple[tuple[str, str], ...] = (
    ("일본어", "ja"),
    ("영어", "en"),
    ("중국어", "zh"),
    ("한국어", "ko"),
    ("태국어", "th"),
    ("베트남어", "vi"),
    ("인도네시아어", "id"),
    ("말레이어", "ms"),
    ("필리핀어", "tl"),
    ("타갈로그어", "tl"),
    ("프랑스어", "fr"),
    ("독일어", "de"),
    ("스페인어", "es"),
    ("포르투갈어", "pt"),
    ("이탈리아어", "it"),
    ("러시아어", "ru"),
    ("아랍어", "ar"),
    ("힌디어", "hi"),
    ("벵골어", "bn"),
    ("타밀어", "ta"),
    ("우르두어", "ur"),
    ("페르시아어", "fa"),
    ("터키어", "tr"),
    ("튀르키예어", "tr"),
    ("폴란드어", "pl"),
    ("네덜란드어", "nl"),
    ("스웨덴어", "sv"),
    ("덴마크어", "da"),
    ("핀란드어", "fi"),
    ("노르웨이어", "no"),
    ("우크라이나어", "uk"),
    ("체코어", "cs"),
    ("루마니아어", "ro"),
    ("헝가리어", "hu"),
    ("그리스어", "el"),
    ("히브리어", "he"),
    ("미얀마어", "my"),
    ("버마어", "my"),
    ("크메르어", "km"),
    ("라오어", "lo"),
    ("네팔어", "ne"),
    ("싱할라어", "si"),
    ("스와힐리어", "sw"),
    ("日语", "ja"),
    ("英语", "en"),
    ("中文", "zh"),
    ("韩语", "ko"),
    ("泰语", "th"),
    ("越南语", "vi"),
    ("印尼语", "id"),
    ("印度尼西亚语", "id"),
    ("马来语", "ms"),
    ("菲律宾语", "tl"),
    ("他加禄语", "tl"),
    ("法语", "fr"),
    ("德语", "de"),
    ("西班牙语", "es"),
    ("葡萄牙语", "pt"),
    ("意大利语", "it"),
    ("俄语", "ru"),
    ("阿拉伯语", "ar"),
    ("印地语", "hi"),
    ("孟加拉语", "bn"),
    ("泰米尔语", "ta"),
    ("乌尔都语", "ur"),
    ("波斯语", "fa"),
    ("土耳其语", "tr"),
    ("波兰语", "pl"),
    ("荷兰语", "nl"),
    ("瑞典语", "sv"),
    ("丹麦语", "da"),
    ("芬兰语", "fi"),
    ("挪威语", "no"),
    ("乌克兰语", "uk"),
    ("捷克语", "cs"),
    ("罗马尼亚语", "ro"),
    ("匈牙利语", "hu"),
    ("希腊语", "el"),
    ("希伯来语", "he"),
    ("缅甸语", "my"),
    ("高棉语", "km"),
    ("老挝语", "lo"),
    ("尼泊尔语", "ne"),
    ("僧伽罗语", "si"),
    ("斯瓦希里语", "sw"),
    ("日語", "ja"),
    ("英語", "en"),
    ("韓語", "ko"),
    ("泰語", "th"),
    ("越南語", "vi"),
    ("印尼語", "id"),
    ("印度尼西亞語", "id"),
    ("馬來語", "ms"),
    ("菲律賓語", "tl"),
    ("他加祿語", "tl"),
    ("法語", "fr"),
    ("德語", "de"),
    ("德文", "de"),
    ("西班牙語", "es"),
    ("葡萄牙語", "pt"),
    ("義大利語", "it"),
    ("意大利語", "it"),
    ("俄語", "ru"),
    ("阿拉伯語", "ar"),
    ("印地語", "hi"),
    ("孟加拉語", "bn"),
    ("坦米爾語", "ta"),
    ("泰米爾語", "ta"),
    ("烏爾都語", "ur"),
    ("波斯語", "fa"),
    ("土耳其語", "tr"),
    ("波蘭語", "pl"),
    ("荷蘭語", "nl"),
    ("瑞典語", "sv"),
    ("丹麥語", "da"),
    ("芬蘭語", "fi"),
    ("挪威語", "no"),
    ("烏克蘭語", "uk"),
    ("捷克語", "cs"),
    ("羅馬尼亞語", "ro"),
    ("匈牙利語", "hu"),
    ("希臘語", "el"),
    ("希伯來語", "he"),
    ("緬甸語", "my"),
    ("高棉語", "km"),
    ("寮語", "lo"),
    ("老撾語", "lo"),
    ("尼泊爾語", "ne"),
    ("僧伽羅語", "si"),
    ("斯瓦希里語", "sw"),
    ("ญี่ปุ่น", "ja"),
    ("อังกฤษ", "en"),
    ("จีน", "zh"),
    ("เกาหลี", "ko"),
    ("เวียดนาม", "vi"),
    ("อินโดนีเซีย", "id"),
    ("มาเลย์", "ms"),
    ("ฟิลิปปินส์", "tl"),
    ("ฝรั่งเศส", "fr"),
    ("เยอรมัน", "de"),
    ("สเปน", "es"),
    ("โปรตุเกส", "pt"),
    ("อิตาลี", "it"),
    ("รัสเซีย", "ru"),
    ("อาหรับ", "ar"),
    ("ฮินดี", "hi"),
    ("เบงกาลี", "bn"),
    ("ทมิฬ", "ta"),
    ("อูรดู", "ur"),
    ("เปอร์เซีย", "fa"),
    ("ตุรกี", "tr"),
    ("โปแลนด์", "pl"),
    ("ดัตช์", "nl"),
    ("สวีเดน", "sv"),
    ("เดนมาร์ก", "da"),
    ("ฟินแลนด์", "fi"),
    ("นอร์เวย์", "no"),
    ("ยูเครน", "uk"),
    ("เช็ก", "cs"),
    ("โรมาเนีย", "ro"),
    ("ฮังการี", "hu"),
    ("กรีก", "el"),
    ("ฮิบรู", "he"),
    ("พม่า", "my"),
    ("เขมร", "km"),
    ("ลาว", "lo"),
    ("เนปาล", "ne"),
    ("สิงหล", "si"),
    ("สวาฮีลี", "sw"),
    ("español", "es"),
    ("espanol", "es"),
    ("français", "fr"),
    ("francais", "fr"),
    ("deutsch", "de"),
    ("italiano", "it"),
    ("português", "pt"),
    ("portugues", "pt"),
    ("русский", "ru"),
    ("العربية", "ar"),
    ("हिन्दी", "hi"),
    ("tiếng việt", "vi"),
    ("tieng viet", "vi"),
    ("bahasa indonesia", "id"),
    ("bahasa melayu", "ms"),
)

# 「語」で終わるが言語名ではない語
_META_GO = {
    "言語",
    "何語",
    "なん語",
    "外国語",
    "母語",
    "母国語",
    "多言語",
    "他言語",
    "使用言語",
    "対応言語",
    "翻訳語",
    "設定言語",
    "共通語",
    "標準語",
    "方言",
    "各国語",
    "自国語",
}

_SETTINGS_HELP = re.compile(
    r"^(?:"
    r"言語設定の変更"
    r"|言語設定を変更(?:して(?:ください|ね)?)?"
    r"|言語設定変更"
    r"|言語の変更"
    r"|言語を変更(?:して(?:ください|ね)?)?"
    r"|change language settings"
    r"|change the language settings"
    r"|言語設定"
    r"|設定"
    r"|settings"
    r"|language settings"
    r"|languages?"
    r"|言語"
    r"|语言设置|語言設定|语言|語言|设置|設置"
    r"|언어 설정|언어설정|언어|설정"
    r"|การตั้งค่าภาษา|ตั้งค่าภาษา|ตั้งค่า|ภาษา"
    r"|cài đặt ngôn ngữ|cai dat ngon ngu|ngôn ngữ|ngon ngu|cài đặt|cai dat"
    r"|configuraci[oó]n(?: de idioma)?|idioma"
    r"|param[eè]tres(?: de langue)?|langue"
    r"|einstellungen|sprache"
    r"|impostazioni|lingua"
    r"|configura[cç][oõ]es(?: de idioma)?|idioma"
    r"|настройки|язык"
    r"|pengaturan bahasa|pengaturan|bahasa"
    r"|الإعدادات|اللغة"
    r")$"
)
_HOWTO = re.compile(
    r"^(?:"
    r"使い方(?:を教えて(?:ください|ね)?|教えて)?"
    r"|ヘルプ"
    r"|help"
    r"|how to use"
    r"|usage"
    r"|帮助|幫助|ヘルプ"
    r"|도움말|도움"
    r"|วิธีใช้|ช่วยเหลือ|ช่วย"
    r"|trợ giúp|tro giup"
    r"|ayuda"
    r"|aide"
    r"|hilfe"
    r"|aiuto"
    r"|ajuda"
    r"|помощь|справка"
    r"|bantuan|tolong"
    r"|مساعدة"
    r")$"
)
_CATALOG_QUESTION = re.compile(
    r"(?:"
    r"何語"
    r"|なん語"
    r"|知って(?:いる|る)(?:言語|言葉)"
    r"|対応(?:している|してる|の)?言語"
    r"|使える言語"
    r"|サポート(?:している|してる)?言語"
    r"|what languages do you (?:know|support|speak)"
    r"|which languages (?:do you|can you)"
    r"|languages you (?:know|support)"
    r"|all (?:the )?languages you (?:know|support)"
    r")",
    re.IGNORECASE,
)
_TRANSLATE_ALL = re.compile(
    r"(?:"
    r"(?:全|すべて|全て|全部).{0,8}言語"
    r"|言語.{0,6}(?:全|すべて|全て|全部)"
    r"|every language"
    r"|all (?:the )?languages"
    r")",
    re.IGNORECASE,
)
_TRANSLATE_VERB = re.compile(r"翻訳|訳して|translate|interpret", re.IGNORECASE)
_ADD = re.compile(r"追加|加えて|足して|\badd\b", re.IGNORECASE)
_REMOVE = re.compile(r"削除|外して|消して|抜いて|\b(?:remove|delete)\b", re.IGNORECASE)
# 漢字・カタカナ・ひらがなで「語」が付く未知の言語名。既知の別名は先に消費する
_UNKNOWN_GO = re.compile(r"(?:[一-龯々]{1,8}|[ァ-ヶー]{2,16}|[ぁ-ん]{2,12})語")
_PUNCT = re.compile(r"[、,，。.!！?？()（）\[\]「」『』・/／\-—–:：\"'“”⇔↔⇄⇆↔=＝~～]+")
_HOWTO_LOOP = re.compile(
    r"使い方を教えてと送|使い方を聞いてみて|send\s+.{0,40}how to use",
    re.IGNORECASE,
)

_LATIN_FILLERS = (
    "please",
    "translate",
    "translation",
    "languages",
    "language",
    "enable",
    "remove",
    "delete",
    "please",
    "want",
    "into",
    "only",
    "just",
    "add",
    "set",
    "and",
    "or",
    "et",
    "und",
    "y",
    "the",
    "for",
    "to",
    "a",
    "an",
)
_KANA_FILLERS = (
    "に翻訳して",
    "を翻訳して",
    "へ翻訳して",
    "翻訳して",
    "を追加して",
    "に追加して",
    "追加して",
    "を削除して",
    "削除して",
    "を外して",
    "外して",
    "を消して",
    "消して",
    "抜いて",
    "に設定して",
    "を設定して",
    "設定して",
    "にして",
    "してください",
    "してほしい",
    "してね",
    "お願い",
    "ください",
    "訳して",
    "翻訳",
    "追加",
    "削除",
    "設定",
    "言語",
    "して",
    "だけ",
    "のみ",
    "全部",
    "すべて",
    "全て",
    "ほしい",
    "欲しい",
    "に",
    "を",
    "と",
    "や",
    "へ",
    "で",
    "も",
    "の",
    "は",
    "が",
    "ね",
    "よ",
    "か",
)

# 言語名を除いたあとに残る、翻訳依頼の助詞や動詞。長いものから消す。
_REQUEST_FILLERS = (
    "상호번역해줘",
    "번역해주세요",
    "번역해줘",
    "해주세요",
    "해 줘",
    "해줘",
    "양방향",
    "서로",
    "상호",
    "번역",
    "통역",
    "이랑",
    "으로",
    "하고",
    "를",
    "을",
    "로",
    "와",
    "과",
    "랑",
    "翻譯成",
    "翻译成",
    "翻譯",
    "翻译",
    "互相",
    "相互",
    "請把",
    "请把",
    "幫我",
    "帮我",
    "一下",
    "請",
    "请",
    "把",
    "ภาษา",
    "และ",
    "กับ",
)


@dataclass(frozen=True)
class MentionRead:
    """メンション文の読み取り結果。kind が none のときはモデルの判定に任せる。"""

    kind: str = "none"  # none | catalog | settings_help | howto | translate_all | languages | unsupported | swap_unclear
    languages: tuple[tuple[str, str], ...] = ()  # (code, surface)
    unsupported: tuple[str, ...] = ()
    mode: str = "set"  # set | add | remove | swap
    swap_from: str = ""
    swap_to: str = ""


def is_nonlinguistic(text: str) -> bool:
    """絵文字だけ、数字だけ、記号だけなら True。"""

    folded = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s\d\W_]+", "", folded, flags=re.UNICODE) == ""


def is_howto_loop(text: str) -> bool:
    """使い方の答えが、もう一度使い方と送れ、で終わっているとき True。"""

    stripped = (text or "").strip()
    if not stripped or len(stripped) > 80:
        return False
    return _HOWTO_LOOP.search(stripped) is not None


def ui_family(code: str) -> str:
    """固定文の言語。ja/zh/ko/th 以外は英語の原文を返し、翻訳は呼び出し側が行う。"""

    lowered = (code or "").strip().lower().replace("_", "-")
    for prefix in ("ja", "zh", "ko", "th"):
        if lowered.startswith(prefix):
            return prefix
    return "en"


def family_for(instruction_lang: str, text: str = "") -> str:
    """指示言語が空のときだけ、本文の文字から固定文の言語を決める。"""

    if (instruction_lang or "").strip():
        return ui_family(instruction_lang)
    if re.search(r"[ぁ-んァ-ン]", text or ""):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text or ""):
        return "ko"
    if re.search(r"[\u0e00-\u0e7f]", text or ""):
        return "th"
    if re.search(r"[\u4e00-\u9fff]", text or ""):
        return "zh"
    return "en"


def read_mention(text: str) -> MentionRead:
    """言語設定として確定できる文だけを返す。曖昧な文は kind=none。"""

    raw = (text or "").strip()
    if not raw or is_nonlinguistic(raw):
        return MentionRead()
    squeezed = _squeeze(raw)
    if _SETTINGS_HELP.match(squeezed):
        return MentionRead(kind="settings_help")
    if _HOWTO.match(squeezed):
        return MentionRead(kind="howto")

    catalog = _CATALOG_QUESTION.search(raw) is not None
    translate_all = _TRANSLATE_ALL.search(raw) is not None
    translate_verb = _TRANSLATE_VERB.search(raw) is not None
    # 「すべての言語を削除」は全言語パターンに当たるが、消去なので選択にはしない
    resetting_all = translate_all and is_explicit_language_reset(raw)
    # 「知ってる言語すべて」は一覧。「全言語を翻訳して」は上限超えの選択
    if not resetting_all and catalog and not (translate_all and translate_verb):
        return MentionRead(kind="catalog")
    if translate_all and not resetting_all:
        return MentionRead(kind="translate_all")

    directed = _read_directed_replace(raw)
    if directed is not None:
        return directed

    normalized = _normalize(raw)
    found, unsupported, spans = _scan(normalized)
    if _ADD.search(normalized) and _REMOVE.search(normalized):
        return MentionRead(unsupported=tuple(unsupported))
    if found and _residual_empty(normalized, spans):
        return MentionRead(
            kind="languages",
            languages=tuple(found),
            unsupported=tuple(unsupported),
            mode=_mode(normalized),
        )
    if unsupported and not found and _residual_empty(normalized, spans):
        return MentionRead(kind="unsupported", unsupported=tuple(unsupported))
    return MentionRead()


def language_label(code: str, family: str, surface: str = "", stored: str = "") -> str:
    """表示名。依頼の表記が同じ文字体系ならそれを使い、英語名は各言語の定訳に戻す。"""

    shown = (surface or stored or "").strip()
    if family == "ja":
        if shown and re.search(r"[ぁ-んァ-ン一-龯]", shown):
            return shown
        return japanese_language_name(code, "")
    if family in {"zh", "ko", "th"}:
        return localized_language_name(code, family, "" if _is_foreign_surface(shown, family) else shown)
    if shown and re.search(r"[ぁ-んァ-ン一-龯\uac00-\ud7af\u0e00-\u0e7f]", shown):
        return english_language_name(code, "")
    return english_language_name(code, shown)


def _is_foreign_surface(shown: str, family: str) -> bool:
    """他の文字体系の依頼表記は、その言語の固定名に戻す。"""

    if not shown:
        return False
    if family == "zh":
        return bool(re.search(r"[ぁ-んァ-ン\uac00-\ud7af\u0e00-\u0e7f]", shown))
    if family == "ko":
        return not bool(re.search(r"[\uac00-\ud7af]", shown))
    if family == "th":
        return not bool(re.search(r"[\u0e00-\u0e7f]", shown))
    return False


def catalog_labels(family: str) -> list[str]:
    return [language_label(code, family) for code, _name in LANGUAGE_CATALOG]


def unique_language_codes(codes: Sequence[str]) -> list[str]:
    """空と重複を除いた言語コード。順序は最初に出た順。"""

    ordered: list[str] = []
    seen: set[str] = set()
    for code in codes:
        lowered = (code or "").strip().lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(lowered)
    return ordered


def project_language_codes(
    current: Sequence[str],
    incoming: Sequence[str],
    *,
    mode: str,
    remove: Sequence[str] = (),
) -> list[str]:
    """保存後の言語コード。未知のモードは追加や置換に落とさない。"""

    if mode in {"set", "replace"}:
        return unique_language_codes(incoming)
    if mode == "remove":
        removed = set(unique_language_codes(remove or incoming))
        return [code for code in unique_language_codes(current) if code not in removed]
    if mode == "add":
        removed = set(unique_language_codes(remove))
        kept = [code for code in unique_language_codes(current) if code not in removed]
        return unique_language_codes([*kept, *incoming])
    raise ValueError(f"unknown language mode: {mode}")


def exceeds_language_limit(codes: Sequence[str], *, limit: int) -> bool:
    """プラン上限を超えるか。1未満の上限は1として数える。"""

    resolved = limit if isinstance(limit, int) and limit >= 1 else 1
    return len(unique_language_codes(codes)) > resolved


def selection_prompt(labels: Sequence[str], *, family: str, limit: int) -> str:
    """上限を超えたとき。設定は変えず、どれにするか聞く。"""

    header = {
        "ja": f"最大{limit}つまでだよ。どれにする？",
        "zh": f"最多只能设置{limit}种语言。要哪几种？",
        "ko": f"언어는 최대 {limit}개까지야. 어떤 걸로 할까?",
        "th": f"ตั้งได้สูงสุด {limit} ภาษา จะใช้อะไร?",
        "en": f"You can set up to {limit} translation languages. Which ones should I use?",
    }.get(family, f"You can set up to {limit} translation languages. Which ones should I use?")
    body = "\n".join(label for label in labels if label)
    if not body:
        return header
    return f"{header}\n{body}"


def swap_prompt(
    current: Sequence[str],
    wanted: Sequence[str],
    *,
    family: str,
    limit: int,
) -> str:
    """追加すると上限を超えるとき。今の言語を見せて、どれを外すか聞く。"""

    current_labels = [label for label in current if label]
    wanted_labels = [label for label in wanted if label]
    if family == "ja":
        lines = [
            f"今は{'・'.join(current_labels)}の{len(current_labels)}つだよ。",
            f"最大{limit}つまでだから、どれを外す？",
        ]
        if wanted_labels:
            lines.append(f"追加したいのは{'・'.join(wanted_labels)}。")
        return "\n".join(lines)
    if family == "zh":
        lines = [
            f"现在是{'、'.join(current_labels)}，已经{len(current_labels)}种。",
            f"最多{limit}种。要换掉哪一种？",
        ]
        if wanted_labels:
            lines.append(f"想加上{'、'.join(wanted_labels)}。")
        return "\n".join(lines)
    if family == "ko":
        lines = [
            f"지금은 {', '.join(current_labels)} {len(current_labels)}개야.",
            f"최대 {limit}개라서, 어떤 걸 뺄까?",
        ]
        if wanted_labels:
            lines.append(f"추가하려는 건 {', '.join(wanted_labels)}.")
        return "\n".join(lines)
    if family == "th":
        lines = [
            f"ตอนนี้คือ {', '.join(current_labels)} ({len(current_labels)} ภาษา)",
            f"ได้สูงสุด {limit} ภาษา จะเอาภาษาไหนออก?",
        ]
        if wanted_labels:
            lines.append(f"อยากเพิ่ม {', '.join(wanted_labels)}")
        return "\n".join(lines)
    lines = [
        f"You can set up to {limit} translation languages.",
        f"Current languages: {_english_join(current_labels)}.",
        "Which ones should I swap out?",
    ]
    if wanted_labels:
        lines.append(f"You asked to add: {_english_join(wanted_labels)}.")
    return "\n".join(lines)


def over_limit_notice(
    *,
    family: str,
    limit: int,
    requested: Sequence[tuple[str, str]],
    current: Sequence[str] = (),
    mode: str = "replace",
    remove: Sequence[str] = (),
) -> str | None:
    """上限を超える書き込みなら案内を返す。超えなければ None。保存はしない。

    初回の列挙も追加も同じ関数で見る。今の言語がある追加だけ、外す言語を聞く。
    """

    if mode == "remove":
        raise ValueError("remove is not an over-limit write")
    if mode not in {"add", "replace", "set"}:
        raise ValueError(f"unknown language mode: {mode}")
    incoming = [(code or "").strip().lower() for code, _name in requested if (code or "").strip()]
    project_mode = "replace" if mode in {"replace", "set"} else "add"
    projected = project_language_codes(current, incoming, mode=project_mode, remove=remove)
    if not exceeds_language_limit(projected, limit=limit):
        return None
    wanted = [
        language_label(code, family, surface=surface)
        for code, surface in requested
        if (code or "").strip()
    ]
    if mode == "add" and unique_language_codes(current):
        current_labels = [language_label(code, family) for code in unique_language_codes(current)]
        return swap_prompt(current_labels, wanted, family=family, limit=limit)
    return selection_prompt(wanted, family=family, limit=limit)


def unsupported_prompt(names: Sequence[str], family: str) -> str:
    cleaned = [name for name in names if name]
    if not cleaned:
        return ""
    if family == "ja":
        return f"{'・'.join(cleaned)}は対応していないよ。"
    if family == "zh":
        return f"{'、'.join(cleaned)}不支持。"
    if family == "ko":
        return f"{', '.join(cleaned)}는 지원하지 않아."
    if family == "th":
        return f"ยังไม่รองรับ {', '.join(cleaned)}"
    if len(cleaned) == 1:
        return f"{cleaned[0]} isn't supported."
    return f"{_english_join(cleaned)} aren't supported."


def settings_anytime_sentence(family: str) -> str:
    """設定ページの案内。原文は英語。リンクは24時間で切れるので、「設定」で取り直すと書く。"""

    if family == "ja":
        return (
            "このページから、いつでも言語設定を変えられるよ。"
            "期限が切れていたら、メンションして「設定」と送ってね。新しいリンクを送るよ。"
        )
    if family == "zh":
        return "你可以随时从这个页面更改语言设置。如果链接过期了，在群里提及我并发送「設定」，我会发一个新链接。"
    if family == "ko":
        return "이 페이지에서 언제든 언어 설정을 바꿀 수 있어. 링크가 만료되면 멘션하고 「設定」을 보내 줘. 새 링크를 보낼게."
    if family == "th":
        return 'เปลี่ยนการตั้งค่าภาษาได้ทุกเมื่อจากหน้านี้ ถ้าลิงก์หมดอายุ ให้กล่าวถึงฉันแล้วส่ง「設定」 แล้วฉันจะส่งลิงก์ใหม่'
    return (
        'You can change your language settings from this page anytime! '
        'If this link expires, mention me and send 「設定」 for a fresh link.'
    )


def settings_page_line(family: str, url: str) -> str:
    """設定ページへの一行。原文は英語で、ja/zh/ko/th だけ静的に訳す。"""

    if not url:
        return ""
    return f"{settings_anytime_sentence(family)}\n{url}"


def setup_confirmation(
    pairs: Sequence[tuple[str, str]],
    *,
    url: str = "",
    translate=None,
) -> str:
    """設定できた言語の組を、グループの各言語で一度ずつ言う。

    原文は英語。翻訳できればその言語、失敗したら ja/zh/ko/th の固定文、それ以外は英語。
    リンクがあるときだけ「いつでも変えられる」と書き、期限切れは「設定」で取り直す。
    URL は翻訳文の外に一度だけ添える。
    """

    codes = [code for code, _name in pairs if code]
    english_lines = [completion_prompt(pairs, family="en")]
    static: dict[str, str] = {}
    for family in ("ja", "zh", "ko", "th"):
        lines = [completion_prompt(pairs, family=family)]
        if url:
            lines.append(settings_anytime_sentence(family))
        static[family] = "\n".join(line for line in lines if line)
    if url:
        english_lines.append(settings_anytime_sentence("en"))
    english = "\n".join(line for line in english_lines if line)
    text = texts_for_group_languages(codes, english=english, static=static, translate=translate)
    if url:
        return f"{text}\n\n{url}" if text else url
    return text


def need_both_languages_prompt(names: Sequence[str], *, family: str) -> str:
    """片側だけだと双方向に訳せない。保存せず、もう一方を聞く。"""

    shown = [name for name in names if name]
    named = shown[0] if len(shown) == 1 else ""
    if family == "ja":
        head = f"{named}は受け取ったよ。" if named else ""
        body = "双方向に訳すには、あなたの言語も必要だよ。\n例: 英語と日本語"
        return f"{head}\n{body}" if head else body
    if family == "zh":
        head = f"已收到{named}。" if named else ""
        body = "双向翻译需要你的语言。\n例如：英语和日语"
        return f"{head}\n{body}" if head else body
    if family == "ko":
        head = f"{named}는 받았어." if named else ""
        body = "양방향으로 번역하려면 네 언어도 필요해.\n예: 영어랑 일본어"
        return f"{head}\n{body}" if head else body
    if family == "th":
        head = f"ได้รับ {named} แล้ว" if named else ""
        body = "การแปลสองทางต้องมีภาษาของคุณด้วย\nเช่น อังกฤษกับญี่ปุ่น"
        return f"{head}\n{body}" if head else body
    head = f"I got {named}." if named else ""
    body = "Two-way translation needs your language too.\nExample: English and Japanese"
    return f"{head}\n{body}" if head else body


_QUICK_LABELS = {
    "en": {"open": "Language page", "current": "Current", "help": "Help"},
    "ja": {"open": "言語ページ", "current": "今の言語", "help": "使い方"},
    "zh": {"open": "語言頁面", "current": "目前語言", "help": "用法"},
    "ko": {"open": "언어 페이지", "current": "현재 언어", "help": "도움말"},
    "th": {"open": "หน้าภาษา", "current": "ภาษาตอนนี้", "help": "วิธีใช้"},
}


def language_quick_replies(family: str, url: str) -> list[dict]:
    """グループでも使える Quick Reply。言語ページ、今の言語、使い方。"""

    labels = _QUICK_LABELS.get(family) or _QUICK_LABELS["en"]
    items: list[dict] = []
    if url and len(url) <= 1000:
        items.append(
            {
                "type": "action",
                "action": {"type": "uri", "label": labels["open"], "uri": url},
            }
        )
    for key, data in (("current", "langmenu=current"), ("help", "langmenu=help")):
        items.append(
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": labels[key],
                    "data": data,
                    "displayText": labels[key],
                },
            }
        )
    return items


def menu_family(codes: Sequence[str]) -> str:
    """クイック返信の表示言語。ja/zh/ko/th がグループにあればそれを、無ければ英語。"""

    present = {ui_family(code) for code in codes if code}
    for family in ("ja", "zh", "ko", "th"):
        if family in present:
            return family
    return "en"


def join_settings_page_note(url: str) -> str:
    """参加直後の多言語案内。リンクは一度だけ添える。"""

    if not url:
        return ""
    return (
        "You can also choose languages on this page.\n"
        "言語はこのページでも選べるよ。\n"
        "也可以在这个页面选择语言。\n"
        "เลือกภาษาในหน้านี้ก็ได้\n"
        f"{url}"
    )


def texts_for_group_languages(
    codes: Sequence[str],
    *,
    english: str,
    static: dict[str, str],
    translate=None,
) -> str:
    """グループの各言語へ同じ案内を一度ずつ出す。

    英語が原文。ja/zh/ko/th は固定文を使う。それ以外だけ translate を試し、
    空や原文と同じ、例外なら英語を一度だけ使う。
    """

    ordered: list[str] = []
    seen_codes: set[str] = set()
    for code in codes:
        lowered = (code or "").strip().lower()
        if not lowered or lowered in seen_codes:
            continue
        seen_codes.add(lowered)
        ordered.append(lowered)
    if not ordered:
        ordered = ["en"]

    # ja/zh/ko/th は固定文を先に使う。Gemini は固定文が無い言語だけ
    translatable = [
        code
        for code in ordered
        if ui_family(code) == "en" and code != "en" and not code.startswith("en-")
    ]
    found = _translations_for_codes(translatable, english, translate)
    blocks: list[str] = []
    seen_text: set[str] = set()
    for code in ordered:
        family = ui_family(code)
        static_text = "" if family == "en" else (static.get(family) or "").strip()
        if static_text:
            text = static_text
        elif code == "en" or code.startswith("en-"):
            text = english.strip()
        else:
            text = _lookup_translation(found, code) or english.strip()
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        blocks.append(text)
    return "\n\n".join(blocks)


def _translations_for_codes(codes: Sequence[str], english: str, translate) -> dict[str, str]:
    if not callable(translate):
        return {}
    base = (english or "").strip()
    targets = [code for code in codes if code != "en" and not code.startswith("en-")]
    if not base or not targets:
        return {}
    try:
        results = translate(base, targets) or []
        items = list(results)
    except Exception:
        return {}
    found: dict[str, str] = {}
    for item in items:
        lang = str(getattr(item, "lang", "") or "").strip().lower().replace("_", "-")
        text = str(getattr(item, "text", "") or "").strip()
        if not lang or not text or text == base:
            continue
        found[lang] = text
    return found


def _lookup_translation(found: dict[str, str], code: str) -> str:
    if code in found:
        return found[code]
    prefix = code.split("-", 1)[0]
    for lang, text in found.items():
        if lang.split("-", 1)[0] == prefix:
            return text
    return ""


_STALE_CONFIRMATION_ENGLISH = (
    "Those language choices are out of date. "
    "The group's languages were saved again after that confirmation."
)

_STALE_CONFIRMATION_STATIC = {
    "ja": "その確認は古いよ。そのあとで言語が保存されている。",
    "zh": "这个确认已经过期了。之后语言又被保存过。",
    "ko": "그 확인은 오래됐어. 그 뒤에 언어가 다시 저장됐어.",
    "th": "การยืนยันนั้นเก่าแล้ว หลังจากนั้นมีการบันทึกภาษาอีกครั้ง",
}


def stale_confirmation_notice(codes: Sequence[str], *, translate=None) -> str:
    """保存より前に出た確認ボタンを押したときの短い断り。"""

    return texts_for_group_languages(
        codes,
        english=_STALE_CONFIRMATION_ENGLISH,
        static=_STALE_CONFIRMATION_STATIC,
        translate=translate,
    )


def web_settings_notice(pairs: Sequence[tuple[str, str]], *, translate=None) -> str:
    """ウェブで保存したあとにグループへ送る短い確認。同じ内容の連投は呼び出し側で避ける。"""

    codes = [code for code, _name in pairs if code]
    english = _web_settings_line(pairs, "en")
    static = {family: _web_settings_line(pairs, family) for family in ("ja", "zh", "ko", "th")}
    return texts_for_group_languages(codes, english=english, static=static, translate=translate)


def _web_settings_line(pairs: Sequence[tuple[str, str]], family: str) -> str:
    names = [language_label(code, family, stored=name) for code, name in pairs if code]
    if family == "ja":
        head = "設定ページから更新したよ"
        return f"{head}: {'・'.join(names)}" if names else head
    if family == "zh":
        head = "已从设置页面更新"
        return f"{head}：{'、'.join(names)}" if names else head
    if family == "ko":
        head = "설정 페이지에서 업데이트했어"
        return f"{head}: {', '.join(names)}" if names else head
    if family == "th":
        head = "อัปเดตจากหน้าการตั้งค่าแล้ว"
        return f"{head}: {', '.join(names)}" if names else head
    head = "Updated from the settings page"
    return f"{head}: {_english_join(names)}." if names else f"{head}."


def settings_help_prompt(current: Sequence[str], *, family: str, limit: int) -> str:
    """言語設定の変更、のように案内文そのものが来たとき。"""

    labels = [label for label in current if label]
    if family == "ja":
        head = f"今の翻訳言語は{'・'.join(labels)}だよ。" if labels else "翻訳言語はまだ設定されていません。"
        return (
            f"{head}\n"
            "\n"
            "変えるときは、メンションして送ってね。\n"
            "例:\n"
            "英語と日本語にして\n"
            "タイ語を追加して\n"
            "韓国語を外して"
        )
    if family == "zh":
        head = f"现在的翻译语言是{'、'.join(labels)}。" if labels else "还没有设置翻译语言。"
        return f"{head}\n\n要改的话，提我并发送：\n英语和日语\n添加泰语\n去掉韩语"
    if family == "ko":
        head = f"지금 번역 언어는 {', '.join(labels)}야." if labels else "아직 번역 언어가 없어."
        return f"{head}\n\n바꾸려면 멘션해서 보내 줘.\n영어랑 일본어로 해 줘\n태국어 추가해 줘\n한국어 빼 줘"
    if family == "th":
        head = f"ภาษาตอนนี้คือ {', '.join(labels)}" if labels else "ยังไม่ได้ตั้งภาษาแปล"
        return f"{head}\n\nจะเปลี่ยน ให้เมนชันแล้วส่ง เช่น\nอังกฤษกับญี่ปุ่น\nเพิ่มไทย\nเอากาหลีออก"
    head = (
        f"Current translation languages: {_english_join(labels)}."
        if labels
        else "No translation languages are set yet."
    )
    return (
        f"{head}\n"
        "\n"
        "To change them, mention me and send one of these:\n"
        "English and Japanese\n"
        "Add Thai\n"
        "Remove Korean\n"
        f"You can set up to {limit}."
    )


def howto_prompt(*, family: str) -> str:
    """使い方。初回設定だけでなく、メンションで言語を変える例も入れる。"""

    if family == "ja":
        return (
            "グループにメッセージを送ると、設定した言語へ翻訳するよ。\n"
            "\n"
            "言語を変えるときは、メンションして送ってね。\n"
            "例:\n"
            "英語と日本語にして\n"
            "タイ語を追加して\n"
            "韓国語を外して"
        )
    if family == "zh":
        return "在群里发消息，我会译成已设置的语言。\n\n要改语言，提我并发送：\n英语和日语\n添加泰语\n去掉韩语"
    if family == "ko":
        return "그룹에 메시지를 보내면, 설정한 언어로 번역해.\n\n언어를 바꿀 때는 멘션해서 보내 줘.\n영어랑 일본어로 해 줘\n태국어 추가해 줘\n한국어 빼 줘"
    if family == "th":
        return "ส่งข้อความในกลุ่ม แล้วฉันจะแปลเป็นภาษาที่ตั้งไว้\n\nจะเปลี่ยนภาษา ให้เมนชันแล้วส่ง เช่น\nอังกฤษกับญี่ปุ่น\nเพิ่มไทย\nเอากาหลีออก"
    return (
        "Send a message in the group and I'll translate it into the languages you set.\n"
        "\n"
        "To change languages, mention me and send one of these:\n"
        "English and Japanese\n"
        "Add Thai\n"
        "Remove Korean"
    )


def catalog_prompt(*, family: str, limit: int) -> str:
    """対応言語の一覧。グループの現在設定ではない。"""

    body = "\n".join(catalog_labels(family))
    if family == "ja":
        return f"対応している言語はこれだよ。\n{body}\n最大{limit}つまで選べるよ。"
    if family == "zh":
        return f"我能翻译这些语言。\n{body}\n最多选{limit}种。"
    if family == "ko":
        return f"번역할 수 있는 언어는 여기야.\n{body}\n최대 {limit}개까지 고를 수 있어."
    if family == "th":
        return f"ภาษาที่แปลได้มีเหล่านี้\n{body}\nเลือกได้สูงสุด {limit} ภาษา"
    return f"These are the languages I can translate.\n{body}\nYou can choose up to {limit}."


def completion_prompt(pairs: Sequence[tuple[str, str]], *, family: str) -> str:
    """更新後の言語を言う。英語は既存の確認文と同じ形。"""

    if family == "ja":
        names = [language_label(code, "ja", stored=name) for code, name in pairs if code]
        if not names:
            return "翻訳言語は未設定になったよ。"
        return "・".join(names) + "になったよ"
    if family == "zh":
        names = [language_label(code, "zh", stored=name) for code, name in pairs if code]
        if not names:
            return "还没有设置翻译语言。"
        return "已设置为" + "、".join(names) + "。"
    if family == "ko":
        names = [language_label(code, "ko", stored=name) for code, name in pairs if code]
        if not names:
            return "번역 언어가 비었어."
        return ", ".join(names) + "로 설정했어."
    if family == "th":
        names = [language_label(code, "th", stored=name) for code, name in pairs if code]
        if not names:
            return "ยังไม่ได้ตั้งภาษาแปล"
        return "ตั้งเป็น " + ", ".join(names) + " แล้ว"
    names = [language_label(code, "en", stored=name) for code, name in pairs if code]
    if not names:
        return "Translation languages have been updated."
    if len(names) == 1:
        return f"{names[0]} has been set as the translation language."
    return f"{_english_join(names)} have been set as the translation languages."


def reply_family(text: str, group_languages: Sequence[str] = ()) -> str:
    """返信の言語。本文から分かる言語、無ければグループの先頭、それも無ければ日本語。"""

    detected = _detect_reply_family(text)
    if detected:
        return detected
    for code in group_languages:
        family = ui_family(code)
        if family:
            return family
    return "ja"


_EXACT_REPLY_FAMILY = {
    "設定": "ja",
    "言語": "ja",
    "言語設定": "ja",
    "ヘルプ": "ja",
    "使い方": "ja",
    "settings": "en",
    "setting": "en",
    "language": "en",
    "languages": "en",
    "help": "en",
    "语言": "zh",
    "語言": "zh",
    "设置": "zh",
    "設置": "zh",
    "语言设置": "zh",
    "語言設定": "zh",
    "帮助": "zh",
    "幫助": "zh",
    "언어": "ko",
    "설정": "ko",
    "언어설정": "ko",
    "언어 설정": "ko",
    "도움말": "ko",
    "ภาษา": "th",
    "ตั้งค่า": "th",
    "การตั้งค่าภาษา": "th",
}


def _detect_reply_family(text: str) -> str:
    squeezed = _squeeze(text or "")
    exact = _EXACT_REPLY_FAMILY.get(squeezed)
    if exact:
        return exact
    raw = text or ""
    if re.search(r"[ぁ-んァ-ン]", raw):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", raw):
        return "ko"
    if re.search(r"[\u0e00-\u0e7f]", raw):
        return "th"
    if re.search(r"换成|換成|翻译|翻譯|语言|語言|设置|設置|请把|請把|英语|英語|日语|日語|中文", raw):
        return "zh"
    if re.search(r"[A-Za-z]", raw) and not re.search(r"[\u4e00-\u9fff]", raw):
        return "en"
    return ""


def enrollment_prompt(family: str) -> str:
    """言語が読めなかったとき。失敗ではなく、設定の案内。"""

    if family == "ja":
        return "翻訳したい言語を教えてね。\n例: 英語と日本語"
    if family == "zh":
        return "告诉我要翻译的语言。\n例如：英语和日语"
    if family == "ko":
        return "번역할 언어를 알려 줘.\n예: 영어랑 일본어"
    if family == "th":
        return "บอกภาษาที่อยากให้แปล\nเช่น อังกฤษกับญี่ปุ่น"
    return "Tell me the languages you want translated.\nExample: English and Japanese"


def language_not_in_group_prompt(missing: Sequence[str], current: Sequence[str], *, family: str) -> str:
    """外そうとした言語がグループに無いとき。保存しない。"""

    missing_text = _join_labels(missing, family)
    current_text = _join_labels(current, family)
    if family == "ja":
        current_line = f"今の翻訳言語は{current_text}だよ。" if current_text else "翻訳言語はまだ設定されていません。"
        return f"{missing_text}は設定されていないよ。\n{current_line}"
    if family == "zh":
        current_line = f"现在的翻译语言是{current_text}。" if current_text else "还没有设置翻译语言。"
        return f"还没有设置{missing_text}。\n{current_line}"
    if family == "ko":
        current_line = f"지금 번역 언어는 {current_text}야." if current_text else "아직 번역 언어가 없어."
        return f"{missing_text}는 설정되어 있지 않아.\n{current_line}"
    if family == "th":
        current_line = f"ภาษาตอนนี้คือ {current_text}" if current_text else "ยังไม่ได้ตั้งภาษาแปล"
        return f"ยังไม่ได้ตั้ง{missing_text}\n{current_line}"
    current_line = f"Current languages: {current_text}." if current_text else "No translation languages are set yet."
    verb = "isn't" if len([name for name in missing if name]) == 1 else "aren't"
    return f"{missing_text} {verb} set.\n{current_line}"


def swap_clarify_prompt(current: Sequence[str], *, family: str) -> str:
    """置換の向きが文面か今の設定と合わないとき。推測して入れ替えない。"""

    current_text = _join_labels(current, family)
    if family == "ja":
        current_line = f"今の翻訳言語は{current_text}だよ。\n" if current_text else ""
        return f"どちらを外して、どちらを入れる？\n{current_line}例: 英語を中国語に変えて"
    if family == "zh":
        current_line = f"现在的翻译语言是{current_text}。\n" if current_text else ""
        return f"要换掉哪一种，换成哪一种？\n{current_line}例如：把英语换成中文"
    if family == "ko":
        current_line = f"지금 번역 언어는 {current_text}야.\n" if current_text else ""
        return f"어떤 언어를 빼고, 어떤 언어로 바꿀까?\n{current_line}예: 영어를 중국어로 바꿔"
    if family == "th":
        current_line = f"ภาษาตอนนี้คือ {current_text}\n" if current_text else ""
        return f"จะเอาภาษาไหนออก แล้วใส่ภาษาอะไร?\n{current_line}เช่น เปลี่ยนอังกฤษเป็นจีน"
    current_line = f"Current languages: {current_text}.\n" if current_text else ""
    return f"Which language should I replace, and with what?\n{current_line}Example: change English to Chinese"


def _join_labels(labels: Sequence[str], family: str) -> str:
    items = [label for label in labels if label]
    if family == "ja":
        return "・".join(items)
    if family == "zh":
        return "、".join(items)
    if family in {"ko", "th"}:
        return ", ".join(items)
    return _english_join(items)


def no_languages_yet_prompt(family: str) -> str:
    """言語が無いグループで削除だけ来たとき。保存しない。"""

    if family == "ja":
        return "翻訳言語はまだ設定されていません。"
    if family == "zh":
        return "还没有设置翻译语言。"
    if family == "ko":
        return "아직 번역 언어가 없어."
    if family == "th":
        return "ยังไม่ได้ตั้งภาษาแปล"
    return "No translation languages are set yet."


def unrecognized_prompt(family: str) -> str:
    if family == "ja":
        return "言語が読み取れなかったよ。\n例: 日本語、英語、韓国語"
    if family == "zh":
        return "没看懂要哪种语言。\n例如：日语、英语、韩语"
    if family == "ko":
        return "어떤 언어인지 모르겠어.\n예: 일본어, 영어, 한국어"
    if family == "th":
        return "อ่านภาษาที่ต้องการไม่ออก\nเช่น ญี่ปุ่น อังกฤษ เกาหลี"
    return "I couldn't tell which languages you meant.\nExample: English, Japanese, Korean"


def _english_join(names: Sequence[str]) -> str:
    items = [name for name in names if name]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _squeeze(text: str) -> str:
    squeezed = _PUNCT.sub(" ", unicodedata.normalize("NFKC", text).casefold())
    return re.sub(r"\s+", " ", squeezed).strip()


def _normalize(text: str) -> str:
    return _squeeze(text)


_POLITE = r"(?:ください|下さい|くれ|ね)?"
_DIRECTED_REPLACE = (
    # AをBに変えて。をが外す言語、にが入れる言語
    re.compile(
        rf"^(?P<src>.+?)を(?P<dst>.+?)に(?:変え|置き換え|変更し|入れ替え|かえ)て?{_POLITE}$"
    ),
    # BにAを置き換え。にが入れる言語、をが外す言語
    re.compile(
        rf"^(?P<dst>.+?)に(?P<src>.+?)を(?:置き換え|変え|変更し|入れ替え|かえ)て?{_POLITE}$"
    ),
    re.compile(r"^replace (?P<src>.+?) with (?P<dst>.+?)$"),
    re.compile(r"^change (?P<src>.+?) (?:to|into) (?P<dst>.+?)$"),
    re.compile(r"^(?:把|将|將)?(?P<src>.+?)(?:换成|換成|改成|替换成|替換成)(?P<dst>.+?)$"),
    re.compile(
        r"^(?P<src>.+?)(?:를|을)\s*(?P<dst>.+?)(?:로|으로)\s*(?:바꿔|바꿔줘|바꾸|변경|교체해|교체).*$"
    ),
    re.compile(
        r"^(?P<dst>.+?)(?:로|으로)\s*(?P<src>.+?)(?:를|을)\s*(?:바꿔|바꿔줘|바꾸|교체해|교체|대체).*$"
    ),
)


def _read_directed_replace(raw: str) -> MentionRead | None:
    """AをBに変える、を向き付きで読む。曖昧なら入れ替えず聞き返す。"""

    normalized = _normalize(raw)
    for pattern in _DIRECTED_REPLACE:
        match = pattern.match(normalized)
        if not match:
            continue
        source = _one_language(match.group("src"))
        target = _one_language(match.group("dst"))
        if not source or not target or source[0] == target[0]:
            return MentionRead(kind="swap_unclear", mode="swap")
        return MentionRead(
            kind="languages",
            languages=(source, target),
            mode="swap",
            swap_from=source[0],
            swap_to=target[0],
        )
    return None


def _one_language(fragment: str) -> tuple[str, str] | None:
    normalized = _normalize(fragment)
    if not normalized:
        return None
    found, unsupported, spans = _scan(normalized)
    if unsupported or len(found) != 1:
        return None
    if not _residual_empty(normalized, spans):
        return None
    return found[0]


def _mode(normalized: str) -> str:
    if _ADD.search(normalized) and not _REMOVE.search(normalized):
        return "add"
    if _REMOVE.search(normalized) and not _ADD.search(normalized):
        return "remove"
    return "set"


def _scan(normalized: str) -> tuple[list[tuple[str, str]], list[str], list[tuple[int, int]]]:
    supported, unsupported_aliases = _alias_tables()
    found: list[tuple[str, str]] = []
    unsupported: list[str] = []
    seen_codes: set[str] = set()
    seen_unsupported: set[str] = set()
    spans: list[tuple[int, int]] = []
    consumed = [False] * len(normalized)
    index = 0
    while index < len(normalized):
        if normalized[index].isspace() or consumed[index]:
            index += 1
            continue
        match = _longest_at(normalized, index, supported, unsupported_aliases)
        if match is None:
            index += 1
            continue
        start, end, code, surface = match
        for cursor in range(start, end):
            consumed[cursor] = True
        spans.append((start, end))
        if code:
            if code not in seen_codes:
                seen_codes.add(code)
                found.append((code, surface))
        elif surface not in seen_unsupported:
            seen_unsupported.add(surface)
            unsupported.append(surface)
        index = end

    for match in _UNKNOWN_GO.finditer(normalized):
        if any(consumed[cursor] for cursor in range(match.start(), match.end())):
            continue
        token = match.group(0)
        if token in _META_GO or token in seen_unsupported:
            continue
        seen_unsupported.add(token)
        unsupported.append(token)
        spans.append((match.start(), match.end()))
        for cursor in range(match.start(), match.end()):
            consumed[cursor] = True
    return found, unsupported, spans


def _longest_at(
    text: str,
    index: int,
    supported: Sequence[tuple[str, str]],
    unsupported: Sequence[str],
) -> tuple[int, int, str, str] | None:
    best: tuple[int, int, str, str] | None = None
    best_rank: tuple[int, int] | None = None
    for surface, code in supported:
        end = index + len(surface)
        if text.startswith(surface, index) and _bounded(text, index, end, surface.isascii()):
            rank = (len(surface), 1)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = (index, end, code, surface)
    for surface in unsupported:
        end = index + len(surface)
        if text.startswith(surface, index) and _bounded(text, index, end, surface.isascii()):
            rank = (len(surface), 0)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = (index, end, "", surface)
    return best


def _bounded(text: str, start: int, end: int, ascii_alias: bool) -> bool:
    if not ascii_alias:
        return True
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if before.isascii() and before.isalpha():
        return False
    if after.isascii() and after.isalpha():
        return False
    return True


def _residual_empty(normalized: str, spans: Sequence[tuple[int, int]]) -> bool:
    chars = list(normalized)
    for start, end in spans:
        for index in range(start, min(end, len(chars))):
            chars[index] = " "
    return _strip_filler("".join(chars)) == ""


def _strip_filler(text: str) -> str:
    leftover = text
    for word in _LATIN_FILLERS:
        leftover = re.sub(rf"\b{word}\b", " ", leftover, flags=re.IGNORECASE)
    leftover = re.sub(r"\s+", "", leftover)
    for token in _REQUEST_FILLERS:
        leftover = leftover.replace(token, "")
    for token in _KANA_FILLERS:
        leftover = leftover.replace(token, "")
    # 絵文字・数字・記号は言語名の残りではない
    return re.sub(r"[\s\d\W_]+", "", leftover, flags=re.UNICODE)


def _alias_tables() -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    global _SUPPORTED_ALIASES, _UNSUPPORTED_ALIASES
    if _SUPPORTED_ALIASES is None or _UNSUPPORTED_ALIASES is None:
        _SUPPORTED_ALIASES, _UNSUPPORTED_ALIASES = _build_alias_tables()
    return _SUPPORTED_ALIASES, _UNSUPPORTED_ALIASES


_SUPPORTED_ALIASES: tuple[tuple[str, str], ...] | None = None
_UNSUPPORTED_ALIASES: tuple[str, ...] | None = None


def _build_alias_tables() -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    supported: list[tuple[str, str]] = []
    unsupported: list[str] = []
    seen_supported: set[str] = set()
    seen_unsupported: set[str] = set()

    def add_supported(surface: str, code: str) -> None:
        folded = unicodedata.normalize("NFKC", surface).casefold().strip()
        mapped = _REMAP.get(code, code)
        if not folded:
            return
        if mapped not in LANGUAGE_NAMES:
            add_unsupported(folded)
            return
        if folded in seen_supported:
            return
        seen_supported.add(folded)
        supported.append((folded, mapped))

    def add_unsupported(surface: str) -> None:
        folded = unicodedata.normalize("NFKC", surface).casefold().strip()
        if not folded or folded in seen_supported or folded in seen_unsupported:
            return
        seen_unsupported.add(folded)
        unsupported.append(folded)

    for code, english in ENGLISH_LANGUAGE_NAMES.items():
        add_supported(english, code)
    for code, japanese in JAPANESE_LANGUAGE_NAMES.items():
        add_supported(japanese, code)
        if "（" in japanese:
            add_supported(japanese.split("（", 1)[0], code)
    for surface, code in _EXTRA_ALIASES:
        add_supported(surface, code)
    for surface, code in _CROSS_SCRIPT_ALIASES:
        add_supported(surface, code)

    supported.sort(key=lambda item: len(item[0]), reverse=True)
    unsupported.sort(key=len, reverse=True)
    return tuple(supported), tuple(unsupported)
