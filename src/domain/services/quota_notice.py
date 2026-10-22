"""無料枠・有料枠の上限案内文。

料金の金額はコードと設定に無いので書かない。件数と言語数はプラン定義と
実行時の上限（環境変数で上書きされうる）から組み立てる。
"""

from __future__ import annotations

from .plan_policy import FREE_PLAN, language_limit_for, monthly_quota_for

SEE_PLANS_LABEL = "See plans"
SEE_PLANS_BUTTON_TEXT = "Plans and prices"
PLAN_LINK_ALT_PREFIX = "See plans: "
# LINE の URI アクションとテンプレート altText の上限
_LINE_URI_LIMIT = 1000
_LINE_ALT_TEXT_LIMIT = 400
_LINE_BUTTON_TEXT_LIMIT = 160
_LINE_BUTTON_LABEL_LIMIT = 20


def approaching_usage_threshold(limit: int) -> int | None:
    """事前案内を出す件数。50件なら40件。小さすぎる枠では出さない。"""

    if limit < 5:
        return None
    threshold = (limit * 4) // 5
    if threshold < 1 or threshold >= limit:
        return None
    return threshold


def plan_catalog_text(
    *,
    free_quota: int,
    standard_quota: int,
    pro_quota: int,
) -> str:
    """プラン名と、コード上の月間件数・言語数。金額は含めない。"""

    free_languages = language_limit_for("free")
    standard_languages = language_limit_for("standard")
    pro_languages = language_limit_for("pro")
    return (
        "Plans: "
        f"Free {free_quota:,}/month ({free_languages} languages). "
        f"Standard {standard_quota:,}/month ({standard_languages} languages). "
        f"Pro {pro_quota:,}/month ({pro_languages} languages)."
    )


def _resume_sentence(reset_date: str | None, *, free: bool) -> str:
    """再開の起点。日付の前置詞は from だけにする。on は訳すと「その日に止まる」へ反転する。"""

    subject = "Free translation" if free else "Translation"
    if reset_date:
        return f"{subject} resumes from {reset_date} (UTC)."
    return f"{subject} resumes from the monthly quota reset."


def build_free_limit_notice(
    *,
    limit: int,
    reset_date: str | None,
    free_quota: int,
    standard_quota: int,
    pro_quota: int,
    reminder: bool,
) -> str:
    """無料枠を使い切ったときの案内。reminder は停止中の再案内。"""

    opening = (
        f"Translation is still paused. This group used all {limit:,} free messages this month."
        if reminder
        else f"Translation stopped. This group used all {limit:,} free messages this month."
    )
    catalog = plan_catalog_text(
        free_quota=free_quota,
        standard_quota=standard_quota,
        pro_quota=pro_quota,
    )
    return f"{opening}\n{_resume_sentence(reset_date, free=True)}\n{catalog}"


def build_standard_limit_notice(
    *,
    limit: int,
    reset_date: str | None,
    pro_quota: int,
    reminder: bool,
) -> str:
    opening = (
        f"Translation is still paused. This group used all {limit:,} Standard plan messages this period."
        if reminder
        else f"Translation stopped. This group used all {limit:,} Standard plan messages this period."
    )
    pro_languages = language_limit_for("pro")
    return (
        f"{opening}\n"
        f"{_resume_sentence(reset_date, free=False)}\n"
        f"The Pro plan includes {pro_quota:,} messages/month and up to {pro_languages} languages."
    )


def build_pro_limit_notice(*, limit: int, reset_date: str | None, reminder: bool) -> str:
    opening = (
        f"Translation is still paused. This group used all {limit:,} Pro plan messages this period."
        if reminder
        else f"Translation stopped. This group used all {limit:,} Pro plan messages this period."
    )
    return f"{opening}\n{_resume_sentence(reset_date, free=False)}"


def build_approaching_notice(
    *,
    usage: int,
    limit: int,
    reset_date: str | None,
    free_quota: int,
    standard_quota: int,
    pro_quota: int,
) -> str:
    """無料枠の80%（50件なら40件）に達したときの事前案内。"""

    catalog = plan_catalog_text(
        free_quota=free_quota,
        standard_quota=standard_quota,
        pro_quota=pro_quota,
    )
    reset = (
        f"The free quota resets from {reset_date} (UTC)."
        if reset_date
        else "The free quota resets from the start of next month (UTC)."
    )
    return (
        f"This group has used {usage:,} of {limit:,} free messages this month. "
        f"Translation stops once this group reaches {limit:,} messages.\n"
        f"{reset}\n"
        f"{catalog}"
    )


def build_quota_reset_notice(*, reset_date: str | None) -> str:
    """無料枠の月次リセットで翻訳が戻ったときの一文。

    件数は残数ではない。最初の翻訳と同時に送るので、実行時の残り件数は書かない。
    月間上限はプラン定義から取り、文面に 50 を直書きしない。
    """

    cap = monthly_quota_for(FREE_PLAN)
    again = f" The next reset is from {reset_date} (UTC)." if reset_date else ""
    return f"The free monthly quota has reset. Up to {cap:,} messages per month.{again}"


def build_resume_blocked_short(*, limit: int, reset_date: str | None) -> str:
    """同じ日に再開を頼まれたときの短い再案内。リンクは再送しない。"""

    when = f" until {reset_date} (UTC)" if reset_date else " until the monthly quota resets"
    return (
        f"Translation stays paused{when}. "
        f"This group already used all {limit:,} free messages. "
        "The plan link was sent earlier today."
    )


def missing_plan_link_sentence() -> str:
    return "A plan link could not be created right now. Please try again later or contact the administrator."


def price_page_sentence() -> str:
    return "Prices are on the plan page in the next message."


def build_plan_link_message(url: str) -> dict:
    """課金ページへのボタン。URI が長すぎるときは URL テキストに戻す。"""

    link = (url or "").strip()
    if not link:
        return {"type": "text", "text": ""}
    if len(link) > _LINE_URI_LIMIT:
        return {"type": "text", "text": link[:5000]}
    alt = f"{PLAN_LINK_ALT_PREFIX}{link}"
    return {
        "type": "template",
        "altText": alt[:_LINE_ALT_TEXT_LIMIT],
        "template": {
            "type": "buttons",
            "text": SEE_PLANS_BUTTON_TEXT[:_LINE_BUTTON_TEXT_LIMIT],
            "actions": [
                {
                    "type": "uri",
                    "label": SEE_PLANS_LABEL[:_LINE_BUTTON_LABEL_LIMIT],
                    "uri": link,
                }
            ],
        },
    }
