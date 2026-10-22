"""メンション文から言語設定の意図を、モデルに頼らず読む。

区切りのない言語名、上限を超える「全言語」、案内文そのもの、未対応言語は
ここで確定させる。UI文は言語ごとの固定文で、その場の翻訳はしない。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from ...infra.jev_language_reader import LANGUAGE_CATALOG, LANGUAGE_NAMES
from .explicit_language_reset import is_explicit_language_reset
from .language_names import ENGLISH_LANGUAGE_NAMES, JAPANESE_LANGUAGE_NAMES, english_language_name, japanese_language_name

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
    r")$"
)
_HOWTO = re.compile(
    r"^(?:"
    r"使い方(?:を教えて(?:ください|ね)?|教えて)?"
    r"|ヘルプ"
    r"|help"
    r"|how to use"
    r"|usage"
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
_PUNCT = re.compile(r"[、,，。.!！?？()（）\[\]「」『』・/／\-—–:：\"'“”]+")
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


@dataclass(frozen=True)
class MentionRead:
    """メンション文の読み取り結果。kind が none のときはモデルの判定に任せる。"""

    kind: str = "none"  # none | catalog | settings_help | howto | translate_all | languages | unsupported
    languages: tuple[tuple[str, str], ...] = ()  # (code, surface)
    unsupported: tuple[str, ...] = ()
    mode: str = "set"  # set | add | remove


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
    """表示名。日本語の依頼表記はそのまま、英語名は各言語の定訳に戻す。"""

    shown = (surface or stored or "").strip()
    if family == "ja":
        if shown and re.search(r"[ぁ-んァ-ン一-龯]", shown):
            return shown
        return japanese_language_name(code, "")
    return english_language_name(code, "")


def catalog_labels(family: str) -> list[str]:
    return [language_label(code, family) for code, _name in LANGUAGE_CATALOG]


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


def settings_page_line(family: str, url: str) -> str:
    """設定ページへの一行。原文は英語で、ja/zh/ko/th だけ静的に訳す。"""

    if not url:
        return ""
    if family == "ja":
        return f"言語はこのページでも選べるよ。\n{url}"
    if family == "zh":
        return f"也可以在这个页面选择语言。\n{url}"
    if family == "ko":
        return f"이 페이지에서도 언어를 고를 수 있어.\n{url}"
    if family == "th":
        return f"เลือกภาษาในหน้านี้ก็ได้\n{url}"
    return f"You can also choose languages on this page.\n{url}"


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


def web_settings_notice(pairs: Sequence[tuple[str, str]]) -> str:
    """ウェブで保存したあとにグループへ送る短い確認。同じ内容の連投は呼び出し側で避ける。"""

    families: list[str] = []
    for code, _name in pairs:
        family = ui_family(code)
        if family not in families:
            families.append(family)
    if "en" not in families:
        families.insert(0, "en")
    return "\n\n".join(_web_settings_line(pairs, family) for family in families)


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
        names = [language_label(code, "en", stored=name) for code, name in pairs if code]
        if not names:
            return "还没有设置翻译语言。"
        return "已设置为" + "、".join(names) + "。"
    if family == "ko":
        names = [language_label(code, "en", stored=name) for code, name in pairs if code]
        if not names:
            return "번역 언어가 비었어."
        return ", ".join(names) + "로 설정했어."
    if family == "th":
        names = [language_label(code, "en", stored=name) for code, name in pairs if code]
        if not names:
            return "ยังไม่ได้ตั้งภาษาแปล"
        return "ตั้งเป็น " + ", ".join(names) + " แล้ว"
    names = [language_label(code, "en", stored=name) for code, name in pairs if code]
    if not names:
        return "Translation languages have been updated."
    if len(names) == 1:
        return f"{names[0]} has been set as the translation language."
    return f"{_english_join(names)} have been set as the translation languages."


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

    supported.sort(key=lambda item: len(item[0]), reverse=True)
    unsupported.sort(key=len, reverse=True)
    return tuple(supported), tuple(unsupported)
