"""Pilot language: string tables loaded from locales/chat.csv.

The CSV ships in the repository next to this module — one row per string
key, one column per language. The community adds a column to translate;
ru and en are the maintained examples. A language column counts as
supported once `prompt.system` is filled in; the completeness test
guards against half-translated columns.

Cell conventions:
- lists are separated with "|" (synonyms, verb variants);
- "\\n" in a cell is a real newline (keeps the CSV one line per key).

Language resolution: the per-request Assist code wins, then the
configured default (pilot.json section "chat": {"language": ...}), then
"ru".
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

_CSV_PATH = Path(__file__).parent / "locales" / "chat.csv"


@lru_cache(maxsize=1)
def _rows() -> dict[str, dict[str, str]]:
    """key -> {language: text} straight from the CSV (cached)."""
    with _CSV_PATH.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        result: dict[str, dict[str, str]] = {}
        for row in reader:
            key = (row.get("key") or "").strip()
            if not key:
                continue
            result[key] = {
                lang: (text or "")
                for lang, text in row.items()
                if lang and lang != "key"
            }
        return result


@lru_cache(maxsize=1)
def supported_languages() -> tuple[str, ...]:
    """Languages with a fully present system prompt (others are hidden)."""
    rows = _rows()
    base = [lang for lang in rows.get("prompt.system", {}) if lang]
    return tuple(lang for lang in base if rows["prompt.system"].get(lang))


def _text(key: str, language: str) -> str:
    return _rows().get(key, {}).get(language, "")


def _unescape(text: str) -> str:
    return text.replace("\\n", "\n")


def _list(key: str, language: str) -> list[str]:
    return [part for part in _text(key, language).split("|") if part]


def table(language: str) -> dict[str, Any]:
    """The deterministic-fallback table for a language."""
    lang = language if language in supported_languages() else "ru"
    light_verbs = [
        (verb, service)
        for service in ("turn_on", "turn_off", "toggle")
        for verb in _list(f"fallback.light.verbs.{service}", lang)
    ]
    return {
        "light_verbs": light_verbs,
        "light_noun": _text("fallback.light.noun", lang),
        "set_verbs": tuple(_list("fallback.set.verbs", lang)),
        "temp_starts": tuple(_list("fallback.temp.starts", lang)),
        "temp_word": _text("fallback.temp.word", lang),
        "setpoint_re": _text("fallback.setpoint.re", lang),
        "say": {
            name: _unescape(_text(f"fallback.say.{name}", lang))
            for name in (
                "turn_on",
                "turn_off",
                "toggle",
                "lights_none",
                "temp_listing",
                "temp_none",
                "setpoint",
            )
        },
    }


def _lang_dict(prefix: str, language: str, names: tuple[str, ...]) -> dict[str, str]:
    return {name: _text(f"{prefix}.{name}", language) for name in names}


PRESET_KEYS = ("butler", "observer", "economy")
MODE_KEYS = ("normal", "vacation", "guests", "sick")

CHAT_SYSTEM_PROMPTS = {
    lang: _unescape(_text("prompt.system", lang)) for lang in supported_languages()
}
PRESET_NAMES = {
    lang: _lang_dict("persona.preset", lang, PRESET_KEYS)
    for lang in supported_languages()
}
MODE_NAMES = {
    lang: _lang_dict("persona.mode", lang, MODE_KEYS) for lang in supported_languages()
}
# Verbosity thresholds live in code (30/70); only the style strings translate.
VERBOSITY_STYLES = {
    lang: (
        (30, _text("persona.verb.short", lang)),
        (70, _text("persona.verb.medium", lang)),
    )
    for lang in supported_languages()
}
VERBOSITY_STYLES_LONG = {
    lang: _text("persona.verb.long", lang) for lang in supported_languages()
}
PERSONA_BLOCK_TITLES = {
    lang: (
        _text("persona.block.title", lang),
        _text("persona.block.preset", lang),
        _text("persona.block.mode", lang),
    )
    for lang in supported_languages()
}
PERSONA_BLOCK_SLIDERS = {
    lang: (
        _text("persona.slider.butler_observer", lang),
        _text("persona.slider.politeness", lang),
        _text("persona.slider.conservative", lang),
    )
    for lang in supported_languages()
}
PERSONA_BLOCK_FOCUS = {
    lang: _text("persona.block.focus", lang) for lang in supported_languages()
}
PROMPT_HEADER_VITRINE = {
    lang: _text("prompt.header.vitrine", lang) for lang in supported_languages()
}
PROMPT_HEADER_TRUNC = {
    lang: _unescape(_text("prompt.header.trunc", lang))
    for lang in supported_languages()
}
PROMPT_HISTORY_TITLE = {
    lang: _text("prompt.history.title", lang) for lang in supported_languages()
}
PROMPT_ROLES = {
    lang: {
        "user": _text("prompt.role.user", lang),
        "assistant": _text("prompt.role.assistant", lang),
    }
    for lang in supported_languages()
}
PROMPT_OWNER_MESSAGE = {
    lang: _text("prompt.owner_message", lang) for lang in supported_languages()
}
_OWNER_MEMORY_KEYS = ("title", "total", "kinds_prefix", "kinds_pair", "rejects_title")
OWNER_MEMORY_TITLES = {
    lang: {
        key: _unescape(_text(f"owner_memory.{key}", lang)) for key in _OWNER_MEMORY_KEYS
    }
    for lang in supported_languages()
}


def normalize_language(code: str | None) -> str:
    """'ru-RU' -> 'ru'; unsupported or empty -> ''."""
    if not code:
        return ""
    short = code.strip().lower().replace("_", "-").split("-", 1)[0]
    return short if short in supported_languages() else ""


def resolve_language(state: Any, requested: str | None) -> str:
    """Per-request code wins, then the configured default, then 'ru'."""
    from_request = normalize_language(requested)
    if from_request:
        return from_request
    config_default = ""
    raw = (state.read_config() or {}).get("chat")
    if isinstance(raw, dict):
        config_default = normalize_language(str(raw.get("language") or ""))
    return config_default or "ru"
