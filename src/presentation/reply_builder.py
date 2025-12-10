from __future__ import annotations

from typing import Optional


class ReplyBuilder:
    """LINE 送信用メッセージ辞書を組み立てるユーティリティ。"""

    @staticmethod
    def build_text(text: str) -> dict:
        return {"type": "text", "text": text}

    @staticmethod
    def build_template(template: dict, alt_text: Optional[str] = None) -> dict:
        if not template:
            return {}
        base = {
            "type": "template",
            "altText": alt_text or template.get("altText") or "",
            "template": template,
        }
        return base
