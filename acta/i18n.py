"""Trilingual support for ACTA: Russian, Hebrew and English.

Provides script-based language detection and a small catalogue of system
phrases. The language code (``ru`` | ``he`` | ``en``) is threaded through the
pipeline so every agent (and any real LLM) responds in the user's language.
"""

from __future__ import annotations

import re

SUPPORTED = ("ru", "he", "en")
RTL = {"he"}

_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_HEBREW = re.compile(r"[\u0590-\u05FF]")
_LATIN = re.compile(r"[A-Za-z]")

_NAMES = {"ru": "Russian", "he": "Hebrew", "en": "English"}


def detect_language(text: str, default: str = "ru") -> str:
    """Detect language by dominant script. Robust for mixed input."""
    if not text:
        return default if default in SUPPORTED else "ru"
    counts = {
        "he": len(_HEBREW.findall(text)),
        "ru": len(_CYRILLIC.findall(text)),
        "en": len(_LATIN.findall(text)),
    }
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        return default if default in SUPPORTED else "ru"
    return best


def language_name(lang: str) -> str:
    return _NAMES.get(lang, "Russian")


def is_rtl(lang: str) -> bool:
    return lang in RTL


# System phrases used by agents when composing answers offline (no LLM).
_STRINGS: dict[str, dict[str, str]] = {
    "greeting": {
        "ru": "Здравствуйте! Я ACTA GHOST — ваша когнитивная платформа. Опишите задачу.",
        "he": "שלום! אני ACTA GHOST — פלטפורמת ה-AI הקוגניטיבית שלך. תאר/י את המשימה.",
        "en": "Hello! I'm ACTA GHOST — your cognitive platform. Describe your task.",
    },
    "how_i_got_here": {
        "ru": "Как я к этому пришёл:",
        "he": "איך הגעתי לזה:",
        "en": "How I got here:",
    },
    "empty_answer": {
        "ru": "(пустой ответ)",
        "he": "(תשובה ריקה)",
        "en": "(empty answer)",
    },
    "done": {
        "ru": "Готово.",
        "he": "בוצע.",
        "en": "Done.",
    },
    "system_denied": {
        "ru": "Контроль системы отключён в конфигурации (ACTA_ALLOW_SYSTEM_CONTROL).",
        "he": "שליטת המערכת מושבתת בהגדרות (ACTA_ALLOW_SYSTEM_CONTROL).",
        "en": "System control is disabled in configuration (ACTA_ALLOW_SYSTEM_CONTROL).",
    },
}


def t(key: str, lang: str) -> str:
    entry = _STRINGS.get(key, {})
    return entry.get(lang) or entry.get("en") or key


def respond_in_directive(lang: str) -> str:
    """System-prompt fragment instructing a model to answer in the user's language."""
    return f"Respond strictly in {language_name(lang)}."
