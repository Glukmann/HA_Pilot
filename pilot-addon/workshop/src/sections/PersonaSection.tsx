import { useEffect, useMemo, useRef, useState } from "react";

import { usePilotClient } from "../api/context";
import { useConnectionState } from "../hooks/useConnectionState";
import { useStatus } from "../hooks/useStatus";

type SliderKey = "butler_observer" | "politeness" | "verbosity" | "conservative";

const SLIDERS: Array<{ key: SliderKey; label: string; hint: string }> = [
  {
    key: "butler_observer",
    label: "Дворецкий ↔ наблюдатель",
    hint: "0 — тихий наблюдатель, 100 — активный дворецкий",
  },
  {
    key: "politeness",
    label: "Вежливость",
    hint: "тон обращения к хозяину",
  },
  {
    key: "verbosity",
    label: "Разговорчивость",
    hint: "сколько слов тратить на сообщение",
  },
  {
    key: "conservative",
    label: "Консерватизм",
    hint: "осторожность перед необратимым",
  },
];

const PRESETS: Array<{ key: string; label: string }> = [
  { key: "butler", label: "Дворецкий" },
  { key: "observer", label: "Тихий наблюдатель" },
  { key: "economy", label: "Эконом" },
];

// Mirrors PERSONA_PRESETS in the add-on (state.py) — the reference the
// "differs from preset" chip compares against.
const PRESET_VALUES: Record<string, Record<SliderKey, number>> = {
  butler: { butler_observer: 80, politeness: 30, verbosity: 70, conservative: 40 },
  observer: { butler_observer: 10, politeness: 20, verbosity: 20, conservative: 70 },
  economy: { butler_observer: 40, politeness: 60, verbosity: 30, conservative: 80 },
};

const MODES: Array<{ key: string; label: string }> = [
  { key: "normal", label: "Обычный" },
  { key: "vacation", label: "Отпуск" },
  { key: "guests", label: "Гости" },
  { key: "sick", label: "Болезнь" },
];

const DEBOUNCE_MS = 300;

export function PersonaSection() {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const { status, error: statusError, refresh } = useStatus(client);
  const online = connection === "connected";

  // Slider values dragged by the user but not yet echoed by the server.
  const [draft, setDraft] = useState<Partial<Record<SliderKey, number>>>({});
  const [savingSliders, setSavingSliders] = useState<
    Partial<Record<SliderKey, boolean>>
  >({});
  const [presetBusy, setPresetBusy] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [budgetDraft, setBudgetDraft] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const debounceTimers = useRef<Partial<Record<SliderKey, ReturnType<typeof setTimeout>>>>({});

  // The server caught up with a drafted value (its broadcast arrived) —
  // drop the draft so the slider rests on the server value.
  useEffect(() => {
    if (status === null) return;
    setDraft((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const key of Object.keys(next) as SliderKey[]) {
        if (status.persona[key] === next[key]) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [status]);

  useEffect(() => {
    const timers = debounceTimers.current;
    return () => {
      for (const timer of Object.values(timers)) {
        if (timer !== undefined) clearTimeout(timer);
      }
    };
  }, []);

  const fail = (err: unknown) => {
    setActionError(err instanceof Error ? err.message : String(err));
  };

  const sendSlider = (key: SliderKey, value: number) => {
    setSavingSliders((prev) => ({ ...prev, [key]: true }));
    setActionError(null);
    client
      .request<{ ok: boolean }>("persona/set", { slider: key, value })
      .catch((err: unknown) => {
        // Revert to the server value and surface the banner.
        setDraft((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
        fail(err);
      })
      .finally(() => {
        setSavingSliders((prev) => ({ ...prev, [key]: false }));
      });
  };

  const onSliderChange = (key: SliderKey, value: number) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setActionError(null);
    const timers = debounceTimers.current;
    if (timers[key] !== undefined) clearTimeout(timers[key]);
    timers[key] = setTimeout(() => sendSlider(key, value), DEBOUNCE_MS);
  };

  const applyPreset = (preset: string) => {
    setPresetBusy(true);
    setActionError(null);
    client
      .request<{ ok: boolean }>("preset/apply", { preset })
      .catch(fail)
      .finally(() => setPresetBusy(false));
  };

  const setMode = (mode: string) => {
    setModeBusy(true);
    setActionError(null);
    client
      .request<{ ok: boolean }>("mode/set", { mode })
      .catch(fail)
      .finally(() => setModeBusy(false));
  };

  const commitBudget = () => {
    if (budgetDraft === null) return;
    const value = Number(budgetDraft.replace(",", "."));
    if (!Number.isFinite(value) || value < 0) {
      setBudgetDraft(null);
      setActionError("Лимит бюджета должен быть неотрицательным числом.");
      return;
    }
    setBudgetBusy(true);
    setActionError(null);
    client
      .request<{ ok: boolean }>("budget/set", { value })
      .then(() => setBudgetDraft(null))
      .catch((err: unknown) => {
        setBudgetDraft(null);
        fail(err);
      })
      .finally(() => setBudgetBusy(false));
  };

  const presetKey = status?.persona_preset ?? "butler";
  const presetLabel =
    PRESETS.find((p) => p.key === presetKey)?.label ?? presetKey;

  const differs = useMemo(() => {
    if (status === null) return false;
    const reference = PRESET_VALUES[presetKey];
    if (reference === undefined) return false;
    return SLIDERS.some((s) => status.persona[s.key] !== reference[s.key]);
  }, [status, presetKey]);

  const anySaving =
    Object.values(savingSliders).some(Boolean) || presetBusy || modeBusy || budgetBusy;

  if (status === null) {
    if (statusError !== null && online) {
      return (
        <div className="card empty-state">
          <h2>Персона недоступна</h2>
          <p className="hint">{statusError}</p>
          <p className="hint">
            Проверьте, что аддон Пилота запущен (Supervisor → Аддоны → Pilot),
            и обновите раздел.
          </p>
          <button className="btn" onClick={refresh}>
            Повторить
          </button>
        </div>
      );
    }
    return (
      <div className="card empty-state">
        <h2>Загружаю персону…</h2>
        <p className="hint">Жду ответа аддона на status.</p>
      </div>
    );
  }

  return (
    <>
      {actionError !== null && (
        <div className="banner banner-error" role="alert">
          Не удалось сохранить: {actionError}. Значение возвращено к
          серверному.
        </div>
      )}

      <div className="card">
        <div className="card-title-row">
          <span className="card-title">Шкалы персоны</span>
          {anySaving && <span className="save-hint">сохранение…</span>}
          {differs && (
            <span className="badge badge-dim">
              отличается от пресета «{presetLabel}»
            </span>
          )}
        </div>
        {SLIDERS.map((slider) => {
          const value = draft[slider.key] ?? status.persona[slider.key] ?? 50;
          return (
            <div className="slider-row" key={slider.key}>
              <label className="slider-label">
                {slider.label}
                <span className="slider-hint">{slider.hint}</span>
              </label>
              <input
                type="range"
                className="slider"
                min={0}
                max={100}
                step={1}
                value={value}
                disabled={!online}
                onChange={(e) => onSliderChange(slider.key, Number(e.target.value))}
              />
              <span className="slider-value">
                {savingSliders[slider.key] ? "сохранение…" : value}
              </span>
            </div>
          );
        })}
        <div className="preset-row">
          {PRESETS.map((preset) => (
            <button
              key={preset.key}
              className={`btn preset-btn ${preset.key === presetKey ? "preset-active" : ""}`}
              disabled={presetBusy || !online}
              onClick={() => applyPreset(preset.key)}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title">Лимит бюджета</div>
          <div className="form-row">
            <input
              className="input input-narrow"
              type="number"
              min={0}
              step={0.5}
              value={budgetDraft ?? status.daily_budget.toFixed(2)}
              disabled={budgetBusy || !online}
              onChange={(e) => setBudgetDraft(e.target.value)}
              onBlur={commitBudget}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitBudget();
              }}
            />
            <span className="form-unit">₽/день</span>
            {budgetBusy && <span className="save-hint">сохранение…</span>}
          </div>
          <p className="hint">
            Потрачено сегодня: {status.cost_today.toFixed(2)} ₽ из{" "}
            {status.daily_budget.toFixed(2)} ₽.
          </p>
        </div>
        <div className="card">
          <div className="card-title">Режим</div>
          <div className="form-row">
            <select
              className="input"
              value={status.mode}
              disabled={modeBusy || !online}
              onChange={(e) => setMode(e.target.value)}
            >
              {MODES.map((mode) => (
                <option key={mode.key} value={mode.key}>
                  {mode.label}
                </option>
              ))}
            </select>
            {modeBusy && <span className="save-hint">сохранение…</span>}
          </div>
          <p className="hint">
            Режим меняет поведение Пилота: в отпуске — имитация присутствия,
            при болезни — тишина и забота.
          </p>
        </div>
      </div>
    </>
  );
}
