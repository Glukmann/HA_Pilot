import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { usePilotClient } from "../api/context";
import type { ConfigSetPayload } from "../api/types";
import { useConfig } from "../hooks/useConfig";
import { useConnectionState } from "../hooks/useConnectionState";
import { useStatus } from "../hooks/useStatus";

const STEPS = [
  "Знакомство",
  "Супервизор",
  "Персона",
  "Готово",
] as const;

const PRESET_CARDS: Array<{ key: string; name: string; desc: string }> = [
  {
    key: "butler",
    name: "Дворецкий",
    desc: "Заметный и вежливый: предлагает улучшения и ждёт вашего подтверждения.",
  },
  {
    key: "observer",
    name: "Тихий наблюдатель",
    desc: "Следит молча, пишет редко, вмешивается только в важном.",
  },
  {
    key: "economy",
    name: "Эконом",
    desc: "Сначала деньги: меньше LLM-вызовов, быстрые простые решения.",
  },
];

interface OnboardingWizardProps {
  /** "Пропустить настройку" — enter the workshop without finishing. */
  onSkip: () => void;
  /** "В мастерскую" — close the wizard (navigation happens here). */
  onFinish: () => void;
}

export function OnboardingWizard({ onSkip, onFinish }: OnboardingWizardProps) {
  const client = usePilotClient();
  const navigate = useNavigate();
  const connection = useConnectionState(client);
  const { status } = useStatus(client);
  const { config } = useConfig(client);
  const online = connection === "connected";

  const [step, setStep] = useState(1);
  const [visitedMax, setVisitedMax] = useState(1);

  // Supervisor form. An empty apiKey means "keep the stored one" — the
  // mask from config/get ("***") is never sent back.
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [budget, setBudget] = useState("10");
  const [schedule, setSchedule] = useState("07:00");
  const [touched, setTouched] = useState(false);
  const [keyOnPlace, setKeyOnPlace] = useState(false);
  const [supervisorSaved, setSupervisorSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [presetBusy, setPresetBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Prefill from the stored supervisor section (edit mode, or a retry
  // after a partial setup). Only until the user edits the form.
  useEffect(() => {
    if (touched || config === null) return;
    const supervisor = config.sections.supervisor;
    if (
      supervisor === null ||
      typeof supervisor !== "object" ||
      Array.isArray(supervisor)
    ) {
      return;
    }
    const section = supervisor as Record<string, unknown>;
    setBaseUrl(typeof section.base_url === "string" ? section.base_url : "");
    setModel(typeof section.model === "string" ? section.model : "");
    setSchedule(typeof section.schedule === "string" ? section.schedule : "07:00");
    setKeyOnPlace(Boolean(section.api_key));
  }, [config, touched]);

  const goTo = (next: number) => {
    setStep(next);
    setVisitedMax((prev) => Math.max(prev, next));
  };

  const validateSupervisor = (): string | null => {
    if (!/^https?:\/\/.+/.test(baseUrl.trim())) {
      return "Base URL должен начинаться с http:// или https://";
    }
    if (model.trim() === "") return "Укажите модель";
    if (!keyOnPlace && apiKey.trim() === "") return "Укажите API-ключ";
    const limit = Number(budget.replace(",", "."));
    if (!Number.isFinite(limit) || limit < 0) {
      return "Лимит должен быть неотрицательным числом";
    }
    return null;
  };

  const saveSupervisor = () => {
    const validationError = validateSupervisor();
    if (validationError !== null) {
      setError(validationError);
      return;
    }
    setSaving(true);
    setError(null);
    const values: Record<string, unknown> = {
      base_url: baseUrl.trim(),
      model: model.trim(),
      schedule,
    };
    if (apiKey.trim() !== "") {
      values.api_key = apiKey.trim();
    }
    const calls = [
      client.request<ConfigSetPayload>("config/set", {
        section: "supervisor",
        values,
      }),
      client.request<{ ok: boolean }>("budget/set", {
        value: Number(budget.replace(",", ".")),
      }),
    ];
    Promise.all(calls)
      .then(() => {
        setSupervisorSaved(true);
        setKeyOnPlace(true);
        setApiKey("");
        goTo(3);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setSaving(false));
  };

  const choosePreset = (preset: string) => {
    setPresetBusy(true);
    setActionError(null);
    client
      .request<{ ok: boolean }>("preset/apply", { preset })
      .catch((err: unknown) => {
        setActionError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setPresetBusy(false));
  };

  const finish = () => {
    navigate("/admin");
    onFinish();
  };

  const activePreset = status?.persona_preset ?? "butler";
  const presetLabel =
    PRESET_CARDS.find((p) => p.key === activePreset)?.name ?? activePreset;
  const supervisorConfigured =
    supervisorSaved || status?.onboarded === true;

  const renderStepBody = () => {
    if (step === 1) {
      return (
        <>
          <h2>Что такое Пилот</h2>
          <p>
            Пилот — проактивный жилец вашего умного дома. Он не крутит
            рубильник в реальном времени, а наблюдает и предлагает:
          </p>
          <ul className="wizard-list">
            <li>
              Чекер следит за домом каждые 5 минут — детерминированно, без
              нейросети и без токенов.
            </li>
            <li>
              Раз в день супервизор думает над флагами чекера — один короткий
              запуск к готовым данным.
            </li>
            <li>
              Ничего не меняет без ваших правил: всё необратимое ждёт в
              очереди доверия на ваше подтверждение.
            </li>
          </ul>
        </>
      );
    }
    if (step === 2) {
      return (
        <>
          <h2>Супервизор</h2>
          <p>
            Ежедневный «мозг» Пилота: OpenAI-совместимый провайдер, модель и
            лимит расходов.
          </p>
          <div className="wizard-form">
            <div className="wizard-field">
              <label htmlFor="ob-base-url">Base URL провайдера</label>
              <input
                id="ob-base-url"
                className="input"
                type="text"
                placeholder="https://api.deepseek.com/v1"
                value={baseUrl}
                disabled={saving}
                onChange={(e) => {
                  setTouched(true);
                  setBaseUrl(e.target.value);
                }}
              />
            </div>
            <div className="wizard-field">
              <label htmlFor="ob-model">Модель</label>
              <input
                id="ob-model"
                className="input"
                type="text"
                placeholder="deepseek-chat"
                value={model}
                disabled={saving}
                onChange={(e) => {
                  setTouched(true);
                  setModel(e.target.value);
                }}
              />
            </div>
            <div className="wizard-field">
              <label htmlFor="ob-api-key">API-ключ</label>
              <input
                id="ob-api-key"
                className="input"
                type="password"
                placeholder={keyOnPlace ? "сохранён" : "sk-…"}
                value={apiKey}
                disabled={saving}
                onChange={(e) => {
                  setTouched(true);
                  setApiKey(e.target.value);
                }}
              />
              <div className="wizard-field-hint">
                {keyOnPlace
                  ? "Ключ на месте — оставьте поле пустым, чтобы не менять."
                  : "Хранится в аддоне, наружу не отдаётся."}
              </div>
            </div>
            <div className="wizard-form-row">
              <div className="wizard-field">
                <label htmlFor="ob-budget">Лимит, ₽/день</label>
                <input
                  id="ob-budget"
                  className="input input-narrow"
                  type="number"
                  min={0}
                  step={0.5}
                  value={budget}
                  disabled={saving}
                  onChange={(e) => {
                    setTouched(true);
                    setBudget(e.target.value);
                  }}
                />
              </div>
              <div className="wizard-field">
                <label htmlFor="ob-schedule">Время ежедневного запуска</label>
                <input
                  id="ob-schedule"
                  className="input input-time"
                  type="time"
                  value={schedule}
                  disabled={saving}
                  onChange={(e) => {
                    setTouched(true);
                    setSchedule(e.target.value);
                  }}
                />
              </div>
            </div>
          </div>
        </>
      );
    }
    if (step === 3) {
      return (
        <>
          <h2>Персона</h2>
          <p>Как Пилот будет общаться и насколько активно предлагать:</p>
          <div className="wizard-presets">
            {PRESET_CARDS.map((preset) => (
              <button
                key={preset.key}
                className={`wizard-preset ${
                  preset.key === activePreset ? "wizard-preset-active" : ""
                }`}
                disabled={presetBusy || !online}
                onClick={() => choosePreset(preset.key)}
              >
                <div className="wizard-preset-name">{preset.name}</div>
                <div className="wizard-preset-desc">{preset.desc}</div>
              </button>
            ))}
          </div>
        </>
      );
    }
    return (
      <>
        <h2>Готово</h2>
        <div className="wizard-summary">
          <div className="wizard-summary-row">
            <span>Супервизор</span>
            {supervisorConfigured ? (
              <span className="badge badge-ok">настроен</span>
            ) : (
              <span className="badge badge-dim">не настроен</span>
            )}
          </div>
          <div className="wizard-summary-row">
            <span>Пресона</span>
            <span className="wizard-summary-value">{presetLabel}</span>
          </div>
        </div>
        <p>
          Завтра в {schedule || "07:00"} я посмотрю на дом первый раз. Молча
          поменяю только мелкие уставки (вроде ±3°C), всё остальное поставлю
          в очередь на ваше подтверждение.
        </p>
      </>
    );
  };

  const renderNav = () => {
    if (step === 1) {
      return (
        <button className="btn btn-primary" onClick={() => goTo(2)}>
          Начать настройку
        </button>
      );
    }
    if (step === 2) {
      return (
        <>
          <button className="btn" disabled={saving} onClick={() => goTo(1)}>
            Назад
          </button>
          <button
            className="btn btn-primary"
            disabled={saving || !online}
            onClick={saveSupervisor}
          >
            {saving ? "Сохраняю…" : "Сохранить супервизора"}
          </button>
        </>
      );
    }
    if (step === 3) {
      return (
        <>
          <button className="btn" onClick={() => goTo(2)}>
            Назад
          </button>
          <button className="btn btn-primary" onClick={() => goTo(4)}>
            Далее
          </button>
        </>
      );
    }
    return (
      <>
        <button className="btn" onClick={() => goTo(3)}>
          Назад
        </button>
        <button className="btn btn-primary" onClick={finish}>
          В мастерскую
        </button>
      </>
    );
  };

  return (
    <div className="wizard">
      <div className="wizard-card">
        <div className="wizard-brand">
          <span className="brand-name">Пилот</span>
          <span className="brand-sub">первичная настройка</span>
        </div>
        <div className="wizard-progress">
          {STEPS.map((label, index) => {
            const num = index + 1;
            const state =
              num === step ? "wizard-progress-active" : num < step || num <= visitedMax ? "wizard-progress-done" : "";
            return (
              <button
                key={label}
                className={`wizard-step ${state}`}
                disabled={num > visitedMax}
                onClick={() => goTo(num)}
              >
                {label}
              </button>
            );
          })}
        </div>
        {!online && (
          <div className="banner" role="status">
            Подключение к аддону… настройка появится, как только соединение
            установится.
          </div>
        )}
        {error !== null && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}
        {actionError !== null && (
          <div className="banner banner-error" role="alert">
            Не удалось применить пресет: {actionError}
          </div>
        )}
        {renderStepBody()}
        <div className="wizard-nav">
          {renderNav()}
          <button className="wizard-skip" onClick={onSkip}>
            пропустить настройку
          </button>
        </div>
      </div>
    </div>
  );
}
