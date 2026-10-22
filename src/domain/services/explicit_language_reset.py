from __future__ import annotations

import re

# 言語名が無いこと自体はリセットではない。クリアややり直しを明示した文だけを対象にする
_EXPLICIT_RESET = re.compile(
    r"(?:"
    r"リセット|初期化|クリア|最初から|一から|いちから|設定し直|入れ直|やり直"
    r"|全部(?:削除|消)|すべて(?:削除|消)|全て(?:削除|消)"
    r"|\b(?:reset|clear|start over|from scratch)\b"
    r")",
    re.IGNORECASE,
)


def is_explicit_language_reset(text: str) -> bool:
    """英語・日本語の全消去表現があるときだけ True。他言語の根拠には使わない。"""

    return bool(_EXPLICIT_RESET.search(text or ""))


def allows_language_reset(*, explicit_reset: bool, text: str) -> bool:
    """リセットしてよいか。モデルの明示フラグか、英語・日本語の追加の肯定信号だけを見る。"""

    return bool(explicit_reset) or is_explicit_language_reset(text)
