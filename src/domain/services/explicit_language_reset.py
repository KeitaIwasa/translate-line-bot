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
    """言語設定の全消去を頼んでいるときだけ True。"""

    return bool(_EXPLICIT_RESET.search(text or ""))
