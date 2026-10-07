import { useCallback, useEffect, useState } from "react";

import { usePilotClient } from "../api/context";
import type { ModelAckPayload, ModelProfile, ModelsPayload } from "../api/types";
import { useConnectionState } from "../hooks/useConnectionState";
import { PROVIDER_PRESETS } from "./providerPresets";

interface Draft {
  /** null = creating a new profile. */
  id: string | null;
  label: string;
  baseUrl: string;
  model: string;
  apiKey: string;
  priceIn: string;
  priceOut: string;
  /** Key already stored server-side (edit mode). */
  hasKey: boolean;
}

function draftFrom(profile: ModelProfile | null): Draft {
  if (profile === null) {
    return {
      id: null,
      label: "",
      baseUrl: "",
      model: "",
      apiKey: "",
      priceIn: "",
      priceOut: "",
      hasKey: false,
    };
  }
  return {
    id: profile.id,
    label: profile.label,
    baseUrl: profile.base_url,
    model: profile.model,
    apiKey: "",
    priceIn: profile.price_input_per_1m?.toString() ?? "",
    priceOut: profile.price_output_per_1m?.toString() ?? "",
    hasKey: profile.has_key,
  };
}

/**
 * Model profiles: several OpenAI-compatible configs, one active (галочка).
 * The daily supervisor run uses the active profile; keys never leave the
 * add-on (an empty key field on edit keeps the stored one).
 */
export function ModelsSection() {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const online = connection === "connected";

  const [items, setItems] = useState<ModelProfile[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    client
      .request<ModelsPayload>("models/list", {})
      .then((payload) => {
        setItems(payload.items);
        setActiveId(payload.active_id);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      });
  }, [client]);

  useEffect(refresh, [refresh]);

  const run = (call: Promise<ModelAckPayload>) => {
    setBusy(true);
    setError(null);
    call
      .then(() => {
        setDraft(null);
        refresh();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setBusy(false));
  };

  const activate = (id: string) => {
    if (id === activeId || busy) return;
    run(client.request<ModelAckPayload>("models/activate", { id }));
  };

  const remove = (profile: ModelProfile) => {
    if (busy) return;
    if (profile.id === activeId) return; // сервер тоже откажет, но не дёргаем зря
    if (!window.confirm(`Удалить модель «${profile.label}»?`)) return;
    run(client.request<ModelAckPayload>("models/remove", { id: profile.id }));
  };

  const save = () => {
    if (draft === null) return;
    const values: Record<string, unknown> = {
      label: draft.label.trim(),
      base_url: draft.baseUrl.trim(),
      model: draft.model.trim(),
      api_key: draft.apiKey.trim() === "" ? "***" : draft.apiKey.trim(),
      price_input_per_1m: draft.priceIn.trim(),
      price_output_per_1m: draft.priceOut.trim(),
    };
    if (draft.id !== null) values.id = draft.id;
    run(client.request<ModelAckPayload>("models/upsert", values));
  };

  const onProviderPick = (id: string) => {
    const preset = PROVIDER_PRESETS.find((p) => p.id === id);
    if (preset === undefined || draft === null) return;
    setDraft({ ...draft, baseUrl: preset.baseUrl, model: preset.model });
  };

  if (draft !== null) {
    return (
      <div className="card">
        <div className="card-title-row">
          <span className="card-title">
            {draft.id === null ? "Новая модель" : `Модель: ${draft.label}`}
          </span>
          <span className="badge badge-dim">supervisor-конфиг</span>
        </div>
        {error !== null && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}
        <div className="wizard-form">
          <div className="wizard-field">
            <label htmlFor="m-provider">Платформа</label>
            <select
              id="m-provider"
              className="input"
              value="custom"
              disabled={busy}
              onChange={(e) => onProviderPick(e.target.value)}
            >
              <option value="custom">Подставить адрес и модель…</option>
              {PROVIDER_PRESETS.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
          </div>
          <div className="wizard-field">
            <label htmlFor="m-label">Название</label>
            <input
              id="m-label"
              className="input"
              type="text"
              placeholder="DeepSeek"
              value={draft.label}
              disabled={busy}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            />
          </div>
          <div className="wizard-field">
            <label htmlFor="m-base-url">Base URL</label>
            <input
              id="m-base-url"
              className="input"
              type="text"
              placeholder="https://api.deepseek.com/v1"
              value={draft.baseUrl}
              disabled={busy}
              onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
            />
          </div>
          <div className="wizard-field">
            <label htmlFor="m-model">Модель</label>
            <input
              id="m-model"
              className="input"
              type="text"
              placeholder="deepseek-chat"
              value={draft.model}
              disabled={busy}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
            />
          </div>
          <div className="wizard-field">
            <label htmlFor="m-api-key">API-ключ</label>
            <input
              id="m-api-key"
              className="input"
              type="password"
              placeholder={draft.hasKey ? "сохранён — оставьте пустым" : "sk-…"}
              value={draft.apiKey}
              disabled={busy}
              onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
            />
            <div className="wizard-field-hint">
              {draft.hasKey
                ? "Ключ на месте — пустое поле не меняет его."
                : "Хранится в аддоне, наружу не отдаётся."}
            </div>
          </div>
          <div className="wizard-form-row">
            <div className="wizard-field">
              <label htmlFor="m-price-in">Цена ввода / 1M</label>
              <input
                id="m-price-in"
                className="input input-narrow"
                type="number"
                min={0}
                step={0.5}
                placeholder="15"
                value={draft.priceIn}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, priceIn: e.target.value })}
              />
            </div>
            <div className="wizard-field">
              <label htmlFor="m-price-out">Цена вывода / 1M</label>
              <input
                id="m-price-out"
                className="input input-narrow"
                type="number"
                min={0}
                step={0.5}
                placeholder="60"
                value={draft.priceOut}
                disabled={busy}
                onChange={(e) => setDraft({ ...draft, priceOut: e.target.value })}
              />
            </div>
          </div>
        </div>
        <div className="queue-actions">
          <button className="btn" disabled={busy} onClick={() => setDraft(null)}>
            Отмена
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || !online}
            onClick={save}
          >
            {busy ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-title-row">
        <span className="card-title">Модели</span>
        <button
          className="btn btn-primary"
          disabled={busy || !online}
          onClick={() => setDraft(draftFrom(null))}
        >
          + Добавить модель
        </button>
      </div>
      {error !== null && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}
      {items === null ? (
        <p className="hint">Загружаю список моделей…</p>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <p className="hint">
            Моделей пока нет. Добавьте первую — супервизор будет ходить в неё
            на ежедневный запуск.
          </p>
        </div>
      ) : (
        items.map((profile) => (
          <div key={profile.id} className="lib-item">
            <div className="lib-item-head">
              <label className="lib-item-name">
                <input
                  type="radio"
                  name="active-model"
                  checked={profile.id === activeId}
                  disabled={busy || !online}
                  onChange={() => activate(profile.id)}
                />{" "}
                {profile.label}
              </label>
              <div className="lib-item-meta">
                {profile.id === activeId && (
                  <span className="badge badge-ok">активна</span>
                )}
                {profile.has_key ? (
                  <span className="badge badge-dim">ключ сохранён</span>
                ) : (
                  <span className="badge badge-warn">нет ключа</span>
                )}
              </div>
            </div>
            <div className="lib-item-desc">
              {profile.model} · {profile.base_url}
              {(profile.price_input_per_1m !== null ||
                profile.price_output_per_1m !== null) && (
                <>
                  {" "}
                  · {profile.price_input_per_1m ?? "?"}/{profile.price_output_per_1m ?? "?"}{" "}
                  за 1M токенов
                </>
              )}
            </div>
            <div className="queue-actions">
              <button
                className="btn"
                disabled={busy}
                onClick={() => setDraft(draftFrom(profile))}
              >
                Изменить
              </button>
              <button
                className="btn"
                disabled={busy || profile.id === activeId}
                onClick={() => remove(profile)}
              >
                Удалить
              </button>
            </div>
          </div>
        ))
      )}
      <p className="hint">
        Галочка — какая модель работает сейчас: ежедневный запуск супервизора
        идёт в активный профиль. Остальные хранятся для переключения.
      </p>
    </div>
  );
}
