# Pilot — proactive AI agent for Home Assistant

Pilot is not an "assistant on demand". It is a **proactive home manager**: it
watches the household, derives human-level states ("awake", "away", "guests"),
keeps the owner's long-term goals, and proposes — and, as trust grows, applies —
adjustments to the home's policy. The home learns from the owner, not the other
way around.

## Установка

Pilot ставится двумя частями из одного репозитория: **интеграция**
`custom_components/pilot` (сущности и панель в HA) и **аддон** (рантайм
агента). Для пользователей это custom repository — проект пока не в
каталоге HACS.

### Требования

- Home Assistant **с Supervisor** (HA OS или Supervised) — нужен для аддона
  и автоматического обнаружения.
- Права **администратора** в HA.
- HACS — **не обязателен**: интеграцию можно поставить вручную (вариант Б).
- Аддон собирается под архитектуры `amd64` и `aarch64`.
- Интернет нужен только на этапе установки — вся работа дальше локальная.

### Шаг 1. Интеграция

**Вариант А. Через HACS (рекомендуется)**

1. HACS → **Интеграции** → меню **⋮** → **Пользовательские репозитории**.
2. URL: `https://github.com/Glukmann/HA_Pilot`, категория: **Интеграция** →
   **Добавить**.
3. Найти **Pilot Eyes** в списке → **Скачать** → перезапустить Home Assistant
   (HACS предложит сам).

**Вариант Б. Вручную**

1. В репозитории открыть папку
   [`custom_components/pilot`](https://github.com/Glukmann/HA_Pilot/tree/main/custom_components/pilot).
2. Скопировать её целиком в `/config/custom_components/pilot` (через File
   editor, Samba или SSH-дополнение).
3. Перезапустить Home Assistant.

### Шаг 2. Аддон (рантайм агента)

1. **Настройки** → **Дополнения** → **Магазин дополнений**.
2. Меню **⋮** (правый верхний угол) → **Репозитории** → добавить
   `https://github.com/Glukmann/HA_Pilot` → **Закрыть**.
3. В магазине появится карточка **Pilot** → **Установить**.
4. После установки нажать **Запустить** и проверить автозапуск:
   Настройки → Автозапуск → «Включено».

Доступ к API Home Assistant Supervisor выдаёт аддону сам, настраивать
ничего не нужно. Единственная настройка на старте — **дневной бюджет**
(₽/день, по умолчанию 10): Дополнения → Pilot → Конфигурация.

### Шаг 3. Подключение

Когда аддон запущен, он **объявляет себя** Home Assistant автоматически:

1. **Настройки** → **Устройства и службы** → в разделе «Обнаружено»
   появится **Pilot** (аддон) → **Подтвердить** → **Готово**. В списке
   интеграций появится **Pilot Eyes**.
2. Устройство «Pilot Eyes» появится в списке устройств — со статусом агента,
   очередью подтверждений, шкалами персоны и ссылкой на панель.
3. Откройте пункт **«Пилот»** в боковом меню — мастерская. При первом входе
   запустится **онбординг-визард** (4 шага): знакомство, настройка
   супервизора (провайдер, модель, ключ — с пресетами популярных
   платформ), пресет персоны, итог. Дальше моделями можно управлять в
   разделе **«Модели»**: несколько профилей, активная — галочкой, список
   моделей подгружается у провайдера кнопкой.

Если обнаружение не сработало (например, аддон запущен вне Supervisor):
**Добавить интеграцию** → **Pilot Eyes** → ввести вручную:

- **Хост** — имя аддона в сети Supervisor, например `94eaa3d6-pilot`;
- **Порт** — `8899`.

### Обновление

- **Аддон:** Магазин дополнений → карточка Pilot → кнопка **Обновить** при
  выходе новой версии. Что нового — на вкладке **Changelog** в интерфейсе.
- **Интеграция (HACS):** HACS → Интеграции → Pilot Eyes → **Обновить** →
  перезапуск HA.
- **Интеграция (вручную):** повторить вариант Б шага 1 поверх существующей
  папки → перезапуск HA.

Обновления не теряют настройки: очередь подтверждений и конфигурация
переживают перезапуск, схема конфига мигрирует сама, а данные ядра
снапшотятся перед миграциями (см. `docs/2026-10-07-openclaw-update-policy.md`).

### Если что-то пошло не так

1. **Журнал аддона:** Дополнения → Pilot → вкладка **Журнал**.
2. **Журнал HA:** Настройки → Система → **Журнал ошибок** — там же
   появляются замечания интеграции (repairs): рантайм недоступен и т.п.
3. Подробный лог интеграции — в `configuration.yaml`:

   ```yaml
   logger:
     logs:
       custom_components.pilot: debug
   ```

### Удаление

1. Настройки → Устройства и службы → Pilot Eyes → **Удалить**.
2. Дополнения → Pilot → **Остановить** → **Удалить** (опционально убрать
   репозиторий из списка репозиториев магазина).

## Why

- Trigger-based automations catch events, not intentions ("TV at 23:00" is a
  movie night, background noise, or insomnia).
- A hundred-device home can never be hand-tuned to completion; policy goes stale
  with seasons, habits, and device degradation.
- The value of a smart home is under-used because owners don't know what it
  "can do".
- Notification streams from the home turn into noise.

## Core principles

1. **The LLM is never in the real-time loop.** Frequent work is deterministic
   code (zero tokens); the AI runs rarely, over prepared data.
2. **Home Assistant is the single source of truth.** Real-time execution belongs
   to HA automations; the agent changes policy, it does not flip switches.
3. **Soft degradation.** If the agent dies, the home keeps working as a plain
   HA installation.
4. **Trust is earned, then grown.** A narrow auto-apply whitelist, a "yes/no"
   approval queue, an append-only audit log, a hard daily budget, and one-click
   rollback. Destructive/irreversible actions always require confirmation.

## What it does

Реализовано (подробности — CHANGELOG аддона):

- **Supervisor of goals**: deterministic checkers notice deviations (dead
  sensors, climate without effect, energy spikes, stuck lights or gates);
  the agent runs once a day over the flagged facts and adjusts setpoints
  within an explicit whitelist; everything else waits in the trust queue.
- **Trust loop**: yes/no approval queue (persistent across restarts),
  append-only audit log, hard daily budget in ₽, one-click rollback of
  applied setpoints. Confirmed actions are really executed in HA.
- **Chat remote control**: Pilot is a full Assist agent — ask about the
  home or tell it what to do. Reversible actions (lights, media, setpoints)
  execute immediately; irreversible ones land in the trust queue; cameras
  and alarms are never touched from chat.
- **Model profiles**: several LLM providers with an active one-switch
  selection; onboarding wizard with presets for popular platforms; the
  provider's model list is fetched in-place.
- **Persona**: presets («butler» / «quiet observer» / «economy») and
  sliders, editable in the workshop; the queue learns from the owner's
  accept/reject statistics.

Концепция (roadmap, ещё не в продукте): pattern log — repeated manual
actions become candidates for automations; derived states («awake»,
«sleeps», «guests»); butler-style contextual suggestions; local memory
уровней 1–5; детерминированный разбор простых интентов в чате без
LLM-запроса.

## Скриншоты мастерской (mock-данные)

Единый интерфейс «Пилот» — пункт бокового меню HA, открывается за ingress
(авторизация — сессия HA). Снято в mock-режиме фронтенда (`VITE_PILOT_API=mock`).

| Админ: live-логи и слои рантайма | Очередь доверия: подтверждения да/нет |
|---|---|
| ![Админ](images/workshop-admin.jpg) | ![Очередь](images/workshop-queue.jpg) |

| Витрина: карта дома по комнатам | Персона: шкалы, пресеты, бюджет |
|---|---|
| ![Витрина](images/workshop-vitrine.jpg) | ![Персона](images/workshop-persona.jpg) |

## Architecture

Pilot ships as two artifacts of one repository, following the ESPHome /
Music Assistant pattern:

- **`custom_components/pilot`** — a native Home Assistant custom integration:
  one "Pilot Eyes" device with entities (status, daily cost, suggestion queue,
  persona sliders, presets, reset buttons), a **conversation agent in the
  Assist pipeline** (the chat remote control: safe actions execute via HA
  services, risky ones go to the trust queue), mobile actionable
  notifications, repairs/diagnostics/system health. The single user-facing
  admin UI is the add-on's "Пилот" sidebar panel (the workshop SPA behind
  ingress).
- **A Home Assistant add-on** — the agent runtime behind the same UI:
  deterministic checker layer, the daily LLM supervisor run, the trust loop
  (queue, audit, budget guard, executor), the chat endpoint (LLM with the
  home vitrine as context and deterministic action classification), and the
  workshop itself. The OpenClaw core is pinned into the hermetic image as a
  build argument and updates together with the add-on, under a snapshot
  migration gate (`docs/2026-10-07-openclaw-update-policy.md`).

MVP scope: local add-on mode only. A "remote instance" connection mode is
architecturally reserved for power users who run the runtime on their own
machine.

## Status

MVP (integration + add-on) is implemented and runs in the owner's home:
install via the add-on store, auto-discovery of the integration, device
entities, the «Пилот» workshop panel with onboarding, the trust queue with
real execution, model profiles, and the Assist chat remote control. Data
survives updates: persistent queue, config schema migrations, and a snapshot
gate for core migrations. See [GOALS.md](GOALS.md) for product goals and
[LICENSES.md](LICENSES.md) for licensing (PolyForm Noncommercial 1.0.0 —
personal use free, commercial by agreement, see [COMMERCIAL.md](COMMERCIAL.md)).

## Author

Pilot is developed and maintained by
[Andrey Lipanov](https://www.linkedin.com/in/andrey-lipanov-5a3481122/).
Ideas, questions, and collaboration offers — via
[Issues](https://github.com/Glukmann/HA_Pilot/issues) or LinkedIn.

---

*Powered by [OpenClaw](https://github.com/openclaw/openclaw) (MIT).*
