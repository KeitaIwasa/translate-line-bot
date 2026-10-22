from __future__ import annotations

import re

# 「すべての言語」と消去語が離れていてもリセット。「すべて削除」の隣接だけでなく、
# 「すべての言語を削除」「remove all languages」も対象にする。
_ALL_LANGUAGES = (
    r"(?:全|すべて|全て|全部).{0,8}言語"
    r"|言語.{0,6}(?:全|すべて|全て|全部)"
    r"|every language"
    r"|all (?:the )?languages"
)
_RESET_CUE = r"削除|消|リセット|\b(?:remove|removed|delete|deleted|clear|cleared|reset)\b"

# 言語名が無いこと自体はリセットではない。クリアややり直しを明示した文だけを対象にする
_EXPLICIT_RESET = re.compile(
    r"(?:"
    r"リセット|初期化|クリア|最初から|一から|いちから|設定し直|入れ直|やり直"
    r"|全部(?:削除|消)|すべて(?:削除|消)|全て(?:削除|消)"
    r"|\b(?:reset|clear|start over|from scratch)\b"
    rf"|(?:{_ALL_LANGUAGES}).{{0,24}}(?:{_RESET_CUE})"
    rf"|(?:{_RESET_CUE}).{{0,24}}(?:{_ALL_LANGUAGES})"
    r")",
    re.IGNORECASE,
)


def is_explicit_language_reset(text: str) -> bool:
    """英語・日本語の全消去表現があるときだけ True。他言語の根拠には使わない。"""

    return bool(_EXPLICIT_RESET.search(text or ""))


def allows_language_reset(*, explicit_reset: bool, text: str) -> bool:
    """リセットしてよいか。モデルの明示フラグか、英語・日本語の追加の肯定信号だけを見る。"""

    return bool(explicit_reset) or is_explicit_language_reset(text)
