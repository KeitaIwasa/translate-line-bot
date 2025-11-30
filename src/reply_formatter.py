"""Compatibility shim for the legacy `reply_formatter` import path.

The implementation now lives in `presentation.reply_formatter`, but tests and
deployed code still import `reply_formatter` directly. This module re-exports
the public API to keep those imports working.
"""

from src.presentation.reply_formatter import (
    MAX_REPLY_LENGTH,
    build_translation_reply,
    format_translations,
    strip_source_echo,
)

__all__ = [
    "MAX_REPLY_LENGTH",
    "build_translation_reply",
    "format_translations",
    "strip_source_echo",
]
