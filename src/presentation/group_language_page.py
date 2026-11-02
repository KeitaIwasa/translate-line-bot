"""グループに紐づく言語設定ページ。原文は英語で、表示言語へ静的に訳す。"""

from __future__ import annotations

import html
from typing import Sequence
from urllib.parse import urlencode

from ..domain.services.language_mention import ui_family
from ..domain.services.language_names import english_language_name, japanese_language_name

UI_FAMILIES = ("ja", "en", "zh", "ko", "th")

# 英語が原文。ja / zh / ko / th は静的訳。それ以外の表示言語は英語のまま。
_COPY = {
    "en": {
        "html_lang": "en",
        "title": "Translation languages",
        "lead": "Choose the languages this group translates between. You can choose up to {limit}.",
        "both_ways": "Choose at least two languages so messages can be translated both ways.",
        "current_none": "No translation languages are set yet.",
        "current_label": "Current languages",
        "saved": "Saved.",
        "already": "These languages are already set.",
        "empty": "Choose at least one language. Nothing was changed.",
        "need_pair": "Choose at least two languages so messages can be translated both ways. Nothing was changed.",
        "unsupported": "These languages aren't supported: {codes}. Nothing was changed.",
        "too_many": "You can choose up to {limit} languages. Nothing was changed.",
        "search": "Search languages",
        "save": "Save",
        "count": "{n} / {limit} selected",
        "footer": "Anyone in this group can open this link and change the languages. It only applies to this group.",
        "missing_title": "The group link is missing",
        "missing_body": "Open the settings link that KOTORI sent in the LINE group.",
        "invalid_title": "This settings link has expired",
        "invalid_body": 'This link works for 24 hours after KOTORI sends it. Mention KOTORI in the group and send "設定" to get a new link.',
        "unavailable_title": "Language settings are temporarily unavailable",
        "unavailable_body": "Please try this link again in a moment.",
    },
    "ja": {
        "html_lang": "ja",
        "title": "翻訳言語",
        "lead": "このグループで翻訳する言語を選んでください。最大{limit}言語です。",
        "both_ways": "双方向に翻訳するには、2つ以上選んでください。",
        "current_none": "翻訳言語はまだ設定されていません。",
        "current_label": "今の翻訳言語",
        "saved": "保存しました。",
        "already": "この言語のままです。",
        "empty": "1つ以上選んでください。設定は変わっていません。",
        "need_pair": "双方向に翻訳するには、2つ以上選んでください。設定は変わっていません。",
        "unsupported": "対応していない言語があります: {codes}。設定は変わっていません。",
        "too_many": "選べるのは{limit}言語までです。設定は変わっていません。",
        "search": "言語を検索",
        "save": "保存",
        "count": "{n} / {limit} 選択中",
        "footer": "このグループのメンバーなら、誰でもこのリンクから言語を変えられます。このグループだけの設定です。",
        "missing_title": "グループ用リンクが見つかりません",
        "missing_body": "LINEグループでKOTORIが送った設定リンクから開き直してください。",
        "invalid_title": "この設定リンクは期限切れです",
        "invalid_body": "このリンクは、KOTORIが送ってから24時間だけ有効です。グループでKOTORIにメンションし、「設定」と送ると新しいリンクが届きます。",
        "unavailable_title": "言語設定を一時的に利用できません",
        "unavailable_body": "しばらく待ってから、同じリンクを開き直してください。",
    },
    "zh": {
        "html_lang": "zh-Hant",
        "title": "翻譯語言",
        "lead": "選擇這個群組要互譯的語言。最多 {limit} 種。",
        "both_ways": "要雙向翻譯，請至少選兩種語言。",
        "current_none": "還沒有設定翻譯語言。",
        "current_label": "現在的翻譯語言",
        "saved": "已儲存。",
        "already": "語言沒有變更。",
        "empty": "請至少選擇一種語言。設定沒有變更。",
        "need_pair": "要雙向翻譯，請至少選兩種語言。設定沒有變更。",
        "unsupported": "這些語言不受支援：{codes}。設定沒有變更。",
        "too_many": "最多可以選擇 {limit} 種語言。設定沒有變更。",
        "search": "搜尋語言",
        "save": "儲存",
        "count": "已選 {n} / {limit}",
        "footer": "這個群組的成員都可以用這個連結更改語言。連結只對這個群組有效。",
        "missing_title": "找不到群組連結",
        "missing_body": "請從 KOTORI 在 LINE 群組中傳送的設定連結重新開啟。",
        "invalid_title": "此設定連結已過期",
        "invalid_body": "此連結在 KOTORI 送出後 24 小時內有效。請在群組中提及 KOTORI 並傳送「設定」以取得新連結。",
        "unavailable_title": "暫時無法使用語言設定",
        "unavailable_body": "請稍候，再用同一個連結開啟。",
    },
    "ko": {
        "html_lang": "ko",
        "title": "번역 언어",
        "lead": "이 그룹에서 번역할 언어를 고르세요. 최대 {limit}개입니다.",
        "both_ways": "양방향으로 번역하려면 두 개 이상 고르세요.",
        "current_none": "아직 번역 언어가 없습니다.",
        "current_label": "현재 번역 언어",
        "saved": "저장했습니다.",
        "already": "언어가 그대로입니다.",
        "empty": "하나 이상 선택하세요. 설정은 바뀌지 않았습니다.",
        "need_pair": "양방향으로 번역하려면 두 개 이상 고르세요. 설정은 바뀌지 않았습니다.",
        "unsupported": "지원하지 않는 언어입니다: {codes}. 설정은 바뀌지 않았습니다.",
        "too_many": "최대 {limit}개까지 선택할 수 있습니다. 설정은 바뀌지 않았습니다.",
        "search": "언어 검색",
        "save": "저장",
        "count": "{n} / {limit} 선택",
        "footer": "이 그룹의 멤버라면 누구나 이 링크에서 언어를 바꿀 수 있습니다. 이 그룹에만 적용됩니다.",
        "missing_title": "그룹 링크가 없습니다",
        "missing_body": "LINE 그룹에서 KOTORI가 보낸 설정 링크를 다시 여세요.",
        "invalid_title": "이 설정 링크는 만료되었습니다",
        "invalid_body": "이 링크는 KOTORI가 보낸 뒤 24시간 동안 유효합니다. 그룹에서 KOTORI를 멘션하고 「설정」을 보내면 새 링크가 옵니다.",
        "unavailable_title": "언어 설정을 일시적으로 사용할 수 없습니다",
        "unavailable_body": "잠시 후 같은 링크를 다시 여세요.",
    },
    "th": {
        "html_lang": "th",
        "title": "ภาษาที่ใช้แปล",
        "lead": "เลือกภาษาที่กลุ่มนี้จะแปลให้กัน เลือกได้สูงสุด {limit} ภาษา",
        "both_ways": "ถ้าต้องการแปลสองทาง ให้เลือกอย่างน้อยสองภาษา",
        "current_none": "ยังไม่ได้ตั้งภาษาแปล",
        "current_label": "ภาษาปัจจุบัน",
        "saved": "บันทึกแล้ว",
        "already": "ภาษายังเหมือนเดิม",
        "empty": "เลือกอย่างน้อยหนึ่งภาษา การตั้งค่ายังไม่เปลี่ยน",
        "need_pair": "ถ้าต้องการแปลสองทาง ให้เลือกอย่างน้อยสองภาษา การตั้งค่ายังไม่เปลี่ยน",
        "unsupported": "ยังไม่รองรับภาษาเหล่านี้: {codes} การตั้งค่ายังไม่เปลี่ยน",
        "too_many": "เลือกได้สูงสุด {limit} ภาษา การตั้งค่ายังไม่เปลี่ยน",
        "search": "ค้นหาภาษา",
        "save": "บันทึก",
        "count": "เลือกแล้ว {n} / {limit}",
        "footer": "สมาชิกในกลุ่มนี้เปิดลิงก์นี้แล้วเปลี่ยนภาษาได้ ลิงก์นี้ใช้กับกลุ่มนี้เท่านั้น",
        "missing_title": "ไม่พบลิงก์ของกลุ่ม",
        "missing_body": "เปิดลิงก์การตั้งค่าที่ KOTORI ส่งในกลุ่ม LINE อีกครั้ง",
        "invalid_title": "ลิงก์การตั้งค่านี้หมดอายุแล้ว",
        "invalid_body": 'ลิงก์นี้ใช้ได้ 24 ชั่วโมงหลังจาก KOTORI ส่ง ในกลุ่มให้กล่าวถึง KOTORI แล้วส่ง "ตั้งค่า" เพื่อรับลิงก์ใหม่',
        "unavailable_title": "ยังไม่สามารถตั้งค่าภาษาได้ชั่วคราว",
        "unavailable_body": "รอสักครู่แล้วเปิดลิงก์เดิมอีกครั้ง",
    },
}

_SWITCH_LABELS = {
    "ja": "日本語",
    "en": "English",
    "zh": "中文",
    "ko": "한국어",
    "th": "ไทย",
}


def page_family(requested: str, group_codes: Sequence[str]) -> str:
    """表示言語。指定が無ければグループの言語、無ければ英語の原文。"""

    chosen = _family_prefix(requested)
    if chosen:
        return chosen
    present = {ui_family(code) for code in group_codes if code}
    for family in ("ja", "zh", "ko", "th"):
        if family in present:
            return family
    return "en"


def language_option_label(code: str, family: str, stored: str = "") -> str:
    if family == "ja":
        return japanese_language_name(code, stored)
    return english_language_name(code, stored)


def render_group_language_page(
    *,
    family: str,
    token: str,
    options: Sequence[tuple[str, str, str]],
    checked: Sequence[str],
    current_labels: Sequence[str],
    limit: int,
    error: str | None = None,
    notice: str | None = None,
    unsupported: Sequence[str] = (),
) -> str:
    copy = _copy(family)
    checked_set = {(code or "").lower() for code in checked}
    alert = _alert(copy, error=error, notice=notice, limit=limit, unsupported=unsupported)
    both = ""
    if len(checked_set) < 2:
        both = f"<p class=\"hint\">{_esc(copy['both_ways'])}</p>"
    if current_labels:
        current = (
            f"<p class=\"current\"><span>{_esc(copy['current_label'])}</span> "
            f"{_esc(' · '.join(current_labels))}</p>"
        )
    else:
        current = f"<p class=\"current\">{_esc(copy['current_none'])}</p>"
    cards = "\n".join(_option_card(code, label, search, code in checked_set) for code, label, search in options)
    switcher = _switcher(token, family)
    count = copy["count"].format(n=len(checked_set), limit=limit)
    return _document(
        lang=copy["html_lang"],
        title=copy["title"],
        body=(
            f"<main>"
            f"{switcher}"
            f"<h1>{_esc(copy['title'])}</h1>"
            f"<p class=\"lead\">{_esc(copy['lead'].format(limit=limit))}</p>"
            f"{both}"
            f"{current}"
            f"{alert}"
            f"<form method=\"post\" accept-charset=\"utf-8\">"
            f"<input type=\"hidden\" name=\"st\" value=\"{_esc(token)}\">"
            f"<input id=\"q\" type=\"search\" enterkeyhint=\"search\" autocomplete=\"off\" placeholder=\"{_esc(copy['search'])}\">"
            f"<div class=\"grid\">{cards}</div>"
            f"<div class=\"savebar\">"
            f"<p id=\"count\" data-template=\"{_esc(copy['count'].format(n='{n}', limit=limit))}\">{_esc(count)}</p>"
            f"<p id=\"limit-hint\" class=\"hint\" hidden>{_esc(copy['too_many'].format(limit=limit))}</p>"
            f"<button type=\"submit\">{_esc(copy['save'])}</button>"
            f"</div>"
            f"</form>"
            f"<p class=\"footer\">{_esc(copy['footer'])}</p>"
            f"</main>"
            f"<script>{_FILTER_SCRIPT}</script>"
        ),
        limit=limit,
    )


def render_language_link_error(reason: str) -> str:
    """トークンが読めないときは、グループ言語が分からないので各言語を並べる。"""

    sections = []
    title = _COPY["en"]["invalid_title"]
    for family in ("en", "ja", "zh", "ko", "th"):
        copy = _COPY[family]
        if reason == "missing":
            heading, body = copy["missing_title"], copy["missing_body"]
        elif reason == "unavailable":
            heading, body = copy["unavailable_title"], copy["unavailable_body"]
        else:
            heading, body = copy["invalid_title"], copy["invalid_body"]
        if family == "en":
            title = heading
        sections.append(
            f"<section lang=\"{_esc(copy['html_lang'])}\"><h2>{_esc(heading)}</h2><p>{_esc(body)}</p></section>"
        )
    return _document(lang="en", title=title, body=f"<main>{''.join(sections)}</main>", limit=0)


def _alert(copy: dict, *, error: str | None, notice: str | None, limit: int, unsupported: Sequence[str]) -> str:
    if error == "empty":
        return f"<p class=\"error\" role=\"alert\">{_esc(copy['empty'])}</p>"
    if error == "need_pair":
        return f"<p class=\"error\" role=\"alert\">{_esc(copy['need_pair'])}</p>"
    if error == "unsupported":
        codes = ", ".join(code for code in unsupported if code)
        return f"<p class=\"error\" role=\"alert\">{_esc(copy['unsupported'].format(codes=codes))}</p>"
    if error == "too_many":
        return f"<p class=\"error\" role=\"alert\">{_esc(copy['too_many'].format(limit=limit))}</p>"
    if notice == "saved":
        return f"<p class=\"ok\" role=\"status\">{_esc(copy['saved'])}</p>"
    if notice == "already":
        return f"<p class=\"ok\" role=\"status\">{_esc(copy['already'])}</p>"
    return ""


def _option_card(code: str, label: str, search: str, checked: bool) -> str:
    mark = " checked" if checked else ""
    safe_code = _esc(code)
    return (
        f"<label class=\"card\" data-label=\"{_esc(search)}\">"
        f"<input type=\"checkbox\" name=\"languages\" value=\"{safe_code}\"{mark}>"
        f"<span>{_esc(label)}</span>"
        f"</label>"
    )


def _switcher(token: str, current: str) -> str:
    links = []
    for family in UI_FAMILIES:
        query = urlencode((("st", token), ("ui", family), ("openExternalBrowser", "1")))
        current_attr = " aria-current=\"true\"" if family == current else ""
        links.append(f"<a href=\"?{html.escape(query, quote=True)}\"{current_attr}>{_esc(_SWITCH_LABELS[family])}</a>")
    return f"<nav class=\"switch\">{''.join(links)}</nav>"


def _copy(family: str) -> dict:
    return _COPY.get(family) or _COPY["en"]


def _family_prefix(value: str) -> str:
    lowered = (value or "").strip().lower().replace("_", "-")
    for family in UI_FAMILIES:
        if lowered.startswith(family):
            return family
    return ""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _document(*, lang: str, title: str, body: str, limit: int) -> str:
    return (
        "<!DOCTYPE html>"
        f"<html lang=\"{_esc(lang)}\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>{_esc(title)}</title>"
        f"<style>{_STYLE}</style>"
        "</head>"
        f"<body data-limit=\"{int(limit)}\">{body}</body>"
        "</html>"
    )


_STYLE = """
:root { color-scheme: light; }
body { margin: 0; background: #f4f6f3; color: #1c241f; font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 40rem; margin: 0 auto; padding: 16px 16px 8rem; }
h1 { font-size: 1.45rem; margin: 12px 0 8px; }
h2 { font-size: 1.15rem; margin: 0 0 8px; }
p { margin: 0 0 12px; }
.lead, .footer, .current { color: #3d4a43; }
.current span { display: block; font-size: 0.85rem; color: #5c6b63; }
.hint { color: #8a4b08; background: #fff6e8; border-radius: 12px; padding: 10px 12px; }
.error { color: #8a1f1f; background: #fdecec; border-radius: 12px; padding: 10px 12px; }
.ok { color: #0f5132; background: #e8f6ee; border-radius: 12px; padding: 10px 12px; }
.switch { display: flex; flex-wrap: wrap; gap: 8px; }
.switch a { color: #0f6b4c; text-decoration: none; background: #fff; border-radius: 999px; padding: 6px 10px; }
.switch a[aria-current="true"] { background: #0f6b4c; color: #fff; }
#q { width: 100%; box-sizing: border-box; font-size: 16px; border: 1px solid #d5ddd6; border-radius: 12px; padding: 12px; margin: 8px 0 12px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.card { display: flex; align-items: center; gap: 10px; min-height: 48px; background: #fff; border-radius: 12px; padding: 8px 10px; }
.card input { width: 1.25rem; height: 1.25rem; accent-color: #0f6b4c; }
.savebar { position: sticky; bottom: 0; background: #f4f6f3; padding: 12px 0 16px; }
button { width: 100%; min-height: 48px; border: 0; border-radius: 12px; background: #0f6b4c; color: #fff; font-size: 1rem; }
section { background: #fff; border-radius: 12px; padding: 16px; margin-bottom: 12px; }
@media (max-width: 360px) { .grid { grid-template-columns: 1fr; } }
"""

_FILTER_SCRIPT = """
(function () {
  var limit = Number(document.body.dataset.limit || "0");
  var boxes = Array.prototype.slice.call(document.querySelectorAll("input[name=languages]"));
  var count = document.getElementById("count");
  var hint = document.getElementById("limit-hint");
  var query = document.getElementById("q");
  function selected() { return boxes.filter(function (box) { return box.checked; }).length; }
  function refresh() {
    if (!count) return;
    var template = count.getAttribute("data-template") || "";
    count.textContent = template.replace("{n}", String(selected()));
  }
  boxes.forEach(function (box) {
    box.addEventListener("change", function () {
      if (box.checked && limit > 0 && selected() > limit) {
        box.checked = false;
        if (hint) hint.hidden = false;
      }
      refresh();
    });
  });
  if (query) {
    query.addEventListener("input", function () {
      var value = query.value.trim().toLowerCase();
      Array.prototype.forEach.call(document.querySelectorAll("[data-label]"), function (el) {
        var label = el.getAttribute("data-label") || "";
        el.hidden = value !== "" && label.indexOf(value) === -1;
      });
    });
  }
  refresh();
})();
"""
