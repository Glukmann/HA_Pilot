"""Keep the core's config aligned with the add-on (idempotent, hot-reloaded).

Everything converges through ``openclaw config set``: the gateway watches
openclaw.json and hot-reloads models/tools/hooks/plugins/heartbeat — no
restart. Sync runs at startup and after the matching owner actions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .corebridge import config_set
from .corecfg import state_dir
from .modelstore import resolve_section

PLUGIN_DIR = "/opt/openclaw-plugins/pilot-home"


async def ensure_runtime_config(state: Any) -> list[str]:
    """Converge the core config once; returns labels of applied settings."""
    applied: list[str] = []
    sets: list[tuple[list[str], str]] = [
        (["plugins.enabled", "true"], "plugins on"),
        (
            [
                "plugins.load.paths",
                json.dumps([PLUGIN_DIR]),
                "--strict-json",
            ],
            "plugin path",
        ),
        (
            ["plugins.allow", json.dumps(["pilot-home"]), "--strict-json"],
            "plugin allow",
        ),
        (['plugins.entries."pilot-home".enabled', "true"], "plugin entry"),
        (
            [
                "tools.allow",
                json.dumps(
                    [
                        "vitrine_get",
                        "home_state",
                        "home_history",
                        "home_statistics",
                        "home_action",
                        "memory_search",
                        "memory_get",
                    ]
                ),
                "--strict-json",
            ],
            "tool allowlist",
        ),
        (
            [
                "tools.deny",
                json.dumps(["exec", "process", "code_execution"]),
                "--strict-json",
            ],
            "exec denied",
        ),
        (["hooks.allowRequestSessionKey", "true"], "session keys"),
        (
            [
                "hooks.allowedSessionKeyPrefixes",
                # The core requires its own "hook:" prefix in the list.
                json.dumps(["hook:", "pilot:"]),
                "--strict-json",
            ],
            "session prefixes",
        ),
        (
            [
                "cron.webhookSsrfPolicy.allowedHostnames",
                json.dumps(["127.0.0.1", "localhost"]),
                "--strict-json",
            ],
            "webhook ssrf",
        ),
        (
            ["gateway.http.endpoints.chatCompletions.enabled", "true"],
            "chat endpoint",
        ),
        (["agents.defaults.heartbeat.every", '"2h"'], "heartbeat 2h"),
        (["agents.defaults.heartbeat.lightContext", "true"], "heartbeat light"),
        (["agents.defaults.heartbeat.target", '"none"'], "heartbeat silent"),
        (
            [
                "agents.defaults.heartbeat.activeHours",
                json.dumps(
                    {"start": "08:00", "end": "23:00", "timezone": "Asia/Yekaterinburg"}
                ),
                "--strict-json",
            ],
            "heartbeat hours",
        ),
        (
            [
                "agents.defaults.heartbeat.prompt",
                '"Ты Пилот в доме. Прочитай vitrine_get. Если всё штатно — ответь '
                "NO_REPLY. Если заметил отклонение или можешь улучшить комфорт — "
                "запомни вывод в память и, при необходимости, предложи действие "
                "через home_action (он сам решит direct/queue). Камеры и замки не "
                'трогаем никогда."',
            ],
            "heartbeat prompt",
        ),
    ]
    for args, label in sets:
        ok, _out = await config_set(args)
        if ok:
            applied.append(label)
    return applied


async def sync_model(state: Any) -> bool:
    """Active model profile (pilot.json) -> core provider + default model."""
    import logging

    logger = logging.getLogger("pilot.coresync")
    raw = state.read_config() or {}
    section = raw.get("supervisor")
    profile = resolve_section(section if isinstance(section, dict) else {})
    base_url = str(profile.get("base_url") or "")
    model = str(profile.get("model") or "")
    if not base_url or not model:
        # Diagnostics (no secrets): which fields the resolved profile lacked.
        keys = sorted(section.keys()) if isinstance(section, dict) else []
        first = {}
        models = section.get("models") if isinstance(section, dict) else None
        if isinstance(models, list) and models and isinstance(models[0], dict):
            first = {
                k: ("<set>" if "key" in k.lower() else v)
                for k, v in models[0].items()
                if k in ("id", "base_url", "model", "label")
            }
        logger.warning(
            "sync_model skipped: base_url=%s model=%s | section keys=%s | first profile=%s",
            bool(base_url),
            model or "<empty>",
            keys,
            first,
        )
        return False
    provider = {
        "baseUrl": base_url,
        "apiKey": str(profile.get("api_key") or ""),
        "api": "openai-completions",
        # The core's provider schema requires a string `name` per model.
        "models": [{"id": model, "name": model}],
    }
    # No --merge: the add-on owns providers.pilot wholesale, and the core
    # refuses list/object merges on existing keys ("use --replace").
    ok_provider, out_provider = await config_set(
        ["models.providers.pilot", json.dumps(provider), "--strict-json"]
    )
    ok_model, out_model = await config_set(
        ["agents.defaults.model", f'"pilot/{model}"']
    )
    if not (ok_provider and ok_model):
        logging.getLogger("pilot.coresync").warning(
            "sync_model write failed: provider=%s (%s) default=%s (%s)",
            ok_provider,
            out_provider.strip()[-200:],
            ok_model,
            out_model.strip()[-200:],
        )
    return ok_provider and ok_model


_PRESET_NAMES = {
    "butler": "Дворецкий",
    "observer": "Тихий наблюдатель",
    "economy": "Эконом",
}

EVENING_ROUND_NAME = "pilot-round"
EVENING_ROUND_CRON = "30 21 * * *"
EVENING_WEBHOOK = "http://127.0.0.1:8899/api/core-run"
EVENING_PROMPT = (
    "Ты Пилот. Вечерний обход дома: прочитай vitrine_get, оцени день "
    "(отклонения, комфорт, безопасность, расход). Если всё штатно — короткий "
    "итог одним абзацем; если есть на что обратить внимание хозяина — назови "
    "это первым. Без действий в доме, только наблюдение."
)


async def ensure_evening_round() -> bool:
    """Create the evening automation job once (webhook into the add-on)."""
    from .corebridge import core_cli

    ok, out = await core_cli(["automations", "list"])
    if not ok:
        return False
    if EVENING_ROUND_NAME in out:
        return True
    ok_add, _ = await core_cli(
        [
            "automations",
            "add",
            EVENING_ROUND_CRON,
            EVENING_PROMPT,
            "--name",
            EVENING_ROUND_NAME,
            "--session",
            "isolated",
            "--webhook",
            EVENING_WEBHOOK,
        ]
    )
    return ok_add


def sync_persona(state: Any, language: str = "ru") -> bool:
    """Render persona/policy into the core workspace bootstrap files.

    True when any file changed. Bootstrap is re-read every agent turn, so
    edits apply without restarts.
    """
    workspace = state_dir(Path(state.data_dir)) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    persona = state.persona
    preset = _PRESET_NAMES.get(state.persona_preset, state.persona_preset)
    soul = (
        "# SOUL.md — Пилот\n\n"
        "Ты — Пилот, proactive-агент умного дома на Home Assistant. "
        f"Пресета: {preset}.\n"
        f"Шкалы (0–100): дворецкий↔наблюдатель {persona['butler_observer']}, "
        f"вежливость {persona['politeness']}, разговорчивость {persona['verbosity']}, "
        f"консерватизм {persona['conservative']}.\n"
        f"Режим дома: {state.mode}. Текущий фокус хозяина: {state.current_focus or '—'}.\n\n"
        "Говори с хозяином по-русски, коротко и по делу. Ты — не чат-бот, а житель "
        "дома: наблюдай, запоминай привычки, предлагай улучшения. Действия в дом — "
        "только через инструмент home_action; он сам решает, что исполнить, а что "
        "поставить на подтверждение хозяину. Перед вопросами о доме читай vitrine_get.\n"
    )
    identity = (
        "# IDENTITY.md\n\n"
        "- Имя: Пилот\n"
        "- Роль: проактивный агент умного дома (Home Assistant)\n"
        "- Хозяин: Андрей. Дом — его территория; необратимые действия — только с "
        "его подтверждением.\n"
    )
    agents_md = (
        "# AGENTS.md — правила работы в доме\n\n"
        "1. Состояние дома читай только через vitrine_get (живой снимок; "
        "не выдумывай показания).\n"
        "2. Любое действие — только через home_action. Прямых вызовов HA нет.\n"
        "3. Камеры, охрана, замки — никогда (refuse встроен в home_action).\n"
        "4. Мягкая деградация: инструмент недоступен — скажи об этом, не импровизируй.\n"
        "5. Уставки отопления/кондиционеров ±3° — без подтверждения; всё прочее — "
        "очередь хозяина.\n"
        "6. Устройства трактуй по смыслу: вытяжка ванной = PTC-нагрев после душа; "
        "кондиционер гостиной = тепловой насос.\n"
    )
    changed = False
    for name, text in (
        ("SOUL.md", soul),
        ("IDENTITY.md", identity),
        ("AGENTS.md", agents_md),
    ):
        path = workspace / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed = True
    return changed
