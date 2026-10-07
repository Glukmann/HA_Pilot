"""Pilot language: string tables and resolution for the chat layer.

The effective language is, in order of priority: the per-request code
(the Assist input carries the HA language), the configured default
(pilot.json section "chat": {"language": "..."}, settable via config/set),
then "ru". The deterministic fallback (director.py) and the LLM prompts
both speak the resolved language — nothing Russian is hardcoded outside
this module's tables.
"""

from __future__ import annotations

from typing import Any

SUPPORTED = ("ru", "en")

TABLES: dict[str, dict[str, Any]] = {
    "ru": {
        "light_verbs": [
            ("выключи", "turn_off"),
            ("погаси", "turn_off"),
            ("выруби", "turn_off"),
            ("включи", "turn_on"),
            ("зажги", "turn_on"),
            ("вруби", "turn_on"),
            ("переключи", "toggle"),
        ],
        "light_noun": "свет",
        "set_verbs": ("поставь", "установи", "выставь", "сделай"),
        "temp_starts": ("какая", "какова", "сколько", "что за"),
        "temp_word": "температур",
        "setpoint_re": (
            r"^(?P<verb>поставь|установи|выставь|сделай)\s+"
            r"(?P<value>\d{1,2}(?:[.,]\d+)?)\s*"
            r"(?:градус\w*)?\s*(?:в\s+(?P<area>.+))?$"
        ),
        "say": {
            "turn_on": "Включаю свет в «{area}» ({count}).",
            "turn_off": "Выключаю свет в «{area}» ({count}).",
            "toggle": "Переключаю свет в «{area}» ({count}).",
            "lights_none": "В «{area}» нет световых устройств.",
            "temp_listing": "Температурные показания в «{area}»: {listing}.",
            "temp_none": (
                "Датчика температуры воздуха в «{area}» нет — "
                "подходящих показаний не вижу."
            ),
            "setpoint": "Ставлю {value} °C в «{area}».",
        },
    },
    "en": {
        "light_verbs": [
            ("turn off", "turn_off"),
            ("switch off", "turn_off"),
            ("turn on", "turn_on"),
            ("switch on", "turn_on"),
            ("toggle", "toggle"),
        ],
        "light_noun": "light",
        "set_verbs": ("set",),
        "temp_starts": ("what", "how"),
        "temp_word": "temperature",
        "setpoint_re": (
            r"^set\s+(?:the\s+)?(?:temperature\s+(?:to\s+)?)?"
            r"(?P<value>\d{1,2}(?:\.\d+)?)\s*(?:degrees?)?"
            r"(?:\s+in\s+(?:the\s+)?(?P<area>.+))?$"
        ),
        "say": {
            "turn_on": "Turning on the lights in {area} ({count}).",
            "turn_off": "Turning off the lights in {area} ({count}).",
            "toggle": "Toggling the lights in {area} ({count}).",
            "lights_none": "There are no lights in {area}.",
            "temp_listing": "Temperature readings in {area}: {listing}.",
            "temp_none": (
                "There is no air temperature sensor in {area} — no suitable readings."
            ),
            "setpoint": "Setting {value} °C in {area}.",
        },
    },
}

# Chat system prompts per language. Same contract: strict JSON {say, actions}.
CHAT_SYSTEM_PROMPTS = {
    "ru": """Ты — Пилот, голосовой жилец умного дома. Хозяин пишет \
тебе из чата Home Assistant. У тебя есть актуальная карта дома (витрина).

Жёсткие правила:
- Отвечай СТРОГО одним JSON-объектом, без текста вне JSON.
- Поле "say" — твоя реплика хозяину (на русском, длина — по стилю ниже).
- Поле "actions" — список сервис-вызовов HA, которые нужны для просьбы \
хозяина. Пустой список, если хозяин просто спрашивает.
- Каждый вызов: {"domain", "service", "entity_id", "data" (можно пустой)}.
  Только сущности из карты дома выше. Не выдумывай entity_id.
- Значения с % — влажность или заряд батарейки датчика; названия вида \
«…Батарея», «…Влажность», «…Sampling interval» — НЕ температура и НЕ \
состояние устройства. Отвечай про устройство по его основной сущности.
- Показатели самих устройств (роутер: частоты, нагрев радио, CPU, \
память, сигнал, аптайм; розетки: мощность; реле: countdown) — это \
диагностика железа, а НЕ климат комнаты. Никогда не выдавай их за \
температуру/влажность воздуха.
- Если в комнате нет подходящей сущности — честно скажи, что датчика \
нет. НЕ подставляй ближайшее совпадение по названию.
- Ты НЕ исполняешь действия сам — их исполнит рантайм по своим правилам \
безопасности. Камеры и охрану не трогай вообще.
- Используй недавний диалог ниже: «тот же», «его», «а ещё» отсылают к нему.
- Если просьба неоднозначна — не действуй, уточни в "say".

Формат ответа:
{"say": "…", "actions": []}""",
    "en": """You are Pilot, the voice of a smart home. The owner writes to you \
from the Home Assistant chat. You have the current home map (the vitrine).

Hard rules:
- Answer with EXACTLY one JSON object, no text outside the JSON.
- "say" is your reply to the owner (in English, length per the style below).
- "actions" is the list of HA service calls the request needs. Empty list \
when the owner is only asking.
- Each call: {"domain", "service", "entity_id", "data" (may be empty)}.
  Only entities from the home map above. Never invent entity_ids.
- Values with % are humidity or a sensor's battery; names like \
"…Battery", "…Humidity", "…Sampling interval" are NOT the temperature \
or the device state. Answer about a device via its primary entity.
- Device self-metrics (router: frequencies, radio heat, CPU, memory, \
signal, uptime; plugs: power; relays: countdown) are hardware \
diagnostics, NOT room climate. Never present them as air temperature.
- When a room has no suitable entity, say honestly that there is no \
sensor. Do NOT substitute the nearest name match.
- You never execute actions yourself — the runtime executes by its \
safety rules. Never touch cameras or alarms.
- Use the recent dialog below: "the same one", "it", "also" refer to it.
- When a request is ambiguous, do not act; ask in "say".

Answer format:
{"say": "…", "actions": []}""",
}

PRESET_NAMES = {
    "ru": {"butler": "Дворецкий", "observer": "Тихий наблюдатель", "economy": "Эконом"},
    "en": {"butler": "Butler", "observer": "Quiet observer", "economy": "Economy"},
}
MODE_NAMES = {
    "ru": {
        "normal": "обычный",
        "vacation": "отпуск (дом пустует)",
        "guests": "гости",
        "sick": "кто-то болеет",
    },
    "en": {
        "normal": "normal",
        "vacation": "vacation (home empty)",
        "guests": "guests",
        "sick": "someone is sick",
    },
}
VERBOSITY_STYLES = {
    "ru": (
        (30, "Отвечай предельно коротко: одно предложение, без вступлений."),
        (70, "Отвечай кратко: 1–2 предложения по существу."),
    ),
    "en": (
        (30, "Answer extremely briefly: one sentence, no preamble."),
        (70, "Answer briefly: 1–2 sentences to the point."),
    ),
}
VERBOSITY_STYLES_LONG = {
    "ru": "Отвечай развёрнуто: контекст, детали, что предпринято.",
    "en": "Answer in detail: context, details, what was done.",
}
PERSONA_BLOCK_TITLES = {
    "ru": ("Стиль и обстановка:", "- Пресет персоны:", "- Режим дома:"),
    "en": ("Style and context:", "- Persona preset:", "- Home mode:"),
}
PERSONA_BLOCK_SLIDERS = {
    "ru": ("дворецкий", "вежливость", "осторожность"),
    "en": ("butler", "politeness", "caution"),
}
PERSONA_BLOCK_FOCUS = {
    "ru": "- Текущий фокус хозяина:",
    "en": "- Owner's current focus:",
}
PROMPT_HEADER_VITRINE = {"ru": "Карта дома (витрина):", "en": "Home map (vitrine):"}
PROMPT_HEADER_TRUNC = {
    "ru": (
        " (показаны последние {shown} из {total} строк —"
        + " дом больше окна контекста)"
    ),
    "en": (
        " (showing the last {shown} of {total} lines —"
        + " home exceeds the context window)"
    ),
}
PROMPT_HISTORY_TITLE = {"ru": "Недавний диалог:", "en": "Recent dialog:"}
PROMPT_ROLES = {
    "ru": {"user": "Хозяин", "assistant": "Пилот"},
    "en": {"user": "Owner", "assistant": "Pilot"},
}
PROMPT_OWNER_MESSAGE = {"ru": "Сообщение хозяина:", "en": "Owner's message:"}
OWNER_MEMORY_TITLES = {
    "ru": {
        "title": "История решений хозяина:",
        "total": (
            "- Всего: предложено {proposed}, принято {accepted}, "
            "отклонено {rejected}, применено молча {silent}."
        ),
        "kinds_prefix": "- По видам действий: ",
        "kinds_pair": "{kind}: принято {accepted}/отклонено {rejected}",
        "rejects_title": "- Недавние отказы:",
    },
    "en": {
        "title": "Owner's decision history:",
        "total": (
            "- Total: proposed {proposed}, accepted {accepted}, "
            "declined {rejected}, silently applied {silent}."
        ),
        "kinds_prefix": "- By action kind: ",
        "kinds_pair": "{kind}: accepted {accepted}/declined {rejected}",
        "rejects_title": "- Recent declines:",
    },
}


def normalize_language(code: str | None) -> str:
    """'ru-RU' -> 'ru'; unsupported or empty -> ''."""
    if not code:
        return ""
    short = code.strip().lower().replace("_", "-").split("-", 1)[0]
    return short if short in SUPPORTED else ""


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


def table(language: str) -> dict[str, Any]:
    return TABLES[language if language in TABLES else "ru"]
