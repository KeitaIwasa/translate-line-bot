from __future__ import annotations

import base64
import json
import zlib
from typing import Dict, Optional


def decode_postback_payload(data: str) -> Optional[Dict]:
    """LINE postback data を decode する共通ユーティリティ。

    - 言語設定: "langpref=" / "langpref2=" で zlib 圧縮 + base64
    - サブスク操作: "subctrl=" で base64
    """
    if not data:
        return None

    if data.startswith("langpref"):
        prefix, token = data.split("=", 1)
        padding = "=" * (-len(token) % 4)
        try:
            blob = base64.urlsafe_b64decode(token + padding)
            if prefix == "langpref2":
                blob = zlib.decompress(blob)
            decoded = blob.decode("utf-8")
            return json.loads(decoded)
        except Exception:  # pylint: disable=broad-except
            return None

    if data.startswith("subctrl="):
        token = data.split("=", 1)[1]
        padding = "=" * (-len(token) % 4)
        try:
            blob = base64.urlsafe_b64decode(token + padding)
            decoded = blob.decode("utf-8")
            return json.loads(decoded)
        except Exception:  # pylint: disable=broad-except
            return None

    return None


def encode_subscription_payload(payload: Dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"subctrl={token}"
