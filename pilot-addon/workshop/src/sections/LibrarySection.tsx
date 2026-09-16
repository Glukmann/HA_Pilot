import { useCallback, useEffect, useState } from "react";

import { usePilotClient } from "../api/context";
import type {
  AssetAckPayload,
  PromptGetPayload,
  PromptMeta,
  SkillGetPayload,
  SkillMeta,
} from "../api/types";
import { useConnectionState } from "../hooks/useConnectionState";

type AssetKind = "prompt" | "skill";

interface AssetRef {
  kind: AssetKind;
  name: string;
}

function formatSize(sizeBytes: number): string {
  return `${(sizeBytes / 1024).toFixed(1)} КБ`;
}

function DefaultBadge({ isDefault }: { isDefault: boolean }) {
  return isDefault ? (
    <span className="badge badge-dim">по умолчанию</span>
  ) : (
    <span className="badge badge-warn">правилось</span>
  );
}

/** Fullscreen editor for one prompt or skill (set/reset over the WS API). */
function AssetEditor({ asset, onClose }: { asset: AssetRef; onClose: () => void }) {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const online = connection === "connected";

  const [content, setContent] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<string | null>(null);
  const [isDefault, setIsDefault] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedMark, setSavedMark] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);

  const isSkill = asset.kind === "skill";

  useEffect(() => {
    let cancelled = false;
    const load = isSkill
      ? client.getSkill(asset.name)
      : client.getPrompt(asset.name);
    load
      .then((entry: PromptGetPayload | SkillGetPayload) => {
        if (cancelled) return;
        setContent(entry.content);
        setBaseline(entry.content);
        setIsDefault(false); // refreshed from the list meta right after save
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
    // The editor is keyed by asset in the parent; load once per open.
  }, [client, asset.name, isSkill]);

  const dirty = content !== null && content !== baseline;

  const save = () => {
    if (content === null) return;
    setSaving(true);
    setError(null);
    setSavedMark(false);
    const call: Promise<AssetAckPayload> = isSkill
      ? client.setSkill(asset.name, content)
      : client.setPrompt(asset.name, content);
    call
      .then(() => {
        setBaseline(content);
        setIsDefault(false);
        setSavedMark(true);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setSaving(false));
  };

  const reset = () => {
    setError(null);
    const call: Promise<AssetAckPayload> = isSkill
      ? client.resetSkill(asset.name)
      : client.resetPrompt(asset.name);
    call
      .then(() => {
        setConfirmReset(false);
        // Re-read the restored default so the editor shows fresh content.
        const reload = isSkill
          ? client.getSkill(asset.name)
          : client.getPrompt(asset.name);
        return reload.then((entry: PromptGetPayload | SkillGetPayload) => {
          setContent(entry.content);
          setBaseline(entry.content);
          setIsDefault(true);
        });
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      });
  };

  const close = () => {
    if (dirty && !confirmClose) {
      setConfirmClose(true);
      return;
    }
    onClose();
  };

  const sizeKb =
    content !== null ? formatSize(new TextEncoder().encode(content).length) : null;

  return (
    <div className="asset-editor">
      <div className="asset-editor-card">
        <div className="asset-editor-head">
          <span className="asset-editor-name">{asset.name}</span>
          <DefaultBadge isDefault={isDefault && !dirty} />
          {sizeKb !== null && <span className="asset-editor-size">{sizeKb}</span>}
          {dirty && <span className="badge badge-warn">изменено</span>}
        </div>
        {loadError !== null ? (
          <div className="empty-state">
            <h2>Не удалось открыть</h2>
            <p className="hint">{loadError}</p>
            <p className="hint">Обновите страницу и попробуйте снова.</p>
          </div>
        ) : (
          <>
            {isSkill && (
              <p className="hint">
                Первые строки — фронтматтер (name/description), не удаляйте его.
              </p>
            )}
            <textarea
              className="asset-textarea"
              spellCheck={false}
              value={content ?? ""}
              placeholder="Загружаю…"
              disabled={content === null || saving}
              onChange={(e) => {
                setContent(e.target.value);
                setSavedMark(false);
                setConfirmClose(false);
              }}
            />
            <p className="hint">
              Применяется со следующего запуска супервизора.
            </p>
          </>
        )}
        {error !== null && (
          <div className="banner banner-error" role="alert">
            {error}
          </div>
        )}
        {confirmClose && (
          <div className="banner" role="status">
            Изменения не сохранены — закрыть без сохранения?{" "}
            <button className="btn" onClick={onClose}>
              Закрыть без сохранения
            </button>{" "}
            <button className="btn" onClick={() => setConfirmClose(false)}>
              Остаться
            </button>
          </div>
        )}
        <div className="asset-editor-nav">
          <button
            className="btn btn-primary"
            disabled={saving || !online || content === null || !dirty}
            onClick={save}
          >
            {saving ? "Сохраняю…" : "Сохранить"}
          </button>
          {savedMark && <span className="save-ok">сохранено ✓</span>}
          {(!isDefault || dirty) && !confirmReset && (
            <button
              className="btn"
              disabled={saving || !online}
              onClick={() => setConfirmReset(true)}
            >
              Сбросить к умолчанию
            </button>
          )}
          {confirmReset && (
            <span className="asset-editor-confirm">
              Точно сбросить правки?{" "}
              <button className="btn" disabled={saving} onClick={reset}>
                Да, сбросить
              </button>{" "}
              <button className="btn" onClick={() => setConfirmReset(false)}>
                Отмена
              </button>
            </span>
          )}
          <button className="btn asset-editor-close" onClick={close}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}

/** "Библиотека знаний": editable system prompts and runtime skills. */
export function LibrarySection() {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const [prompts, setPrompts] = useState<PromptMeta[] | null>(null);
  const [skills, setSkills] = useState<SkillMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openAsset, setOpenAsset] = useState<AssetRef | null>(null);

  const load = useCallback(() => {
    if (client.getConnectionState() !== "connected") return;
    client.listPrompts().then(
      (payload) => {
        setPrompts(payload.prompts);
        setError(null);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      },
    );
    client.listSkills().then(
      (payload) => {
        setSkills(payload.skills);
        setError(null);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      },
    );
  }, [client]);

  useEffect(() => {
    load();
    return client.onConnectionChange((state) => {
      if (state === "connected") load();
    });
  }, [client, load]);

  const renderLoading = () => (
    <div className="empty-state">
      <h2>Загружаю библиотеку…</h2>
      <p className="hint">Жду ответа аддона на prompts/list и skills/list.</p>
    </div>
  );

  if (error !== null && prompts === null && skills === null) {
    if (connection === "connected") {
      return (
        <div className="card empty-state">
          <h2>Библиотека недоступна</h2>
          <p className="hint">{error}</p>
          <p className="hint">Обновите страницу и попробуйте снова.</p>
          <button className="btn" onClick={load}>
            Повторить
          </button>
        </div>
      );
    }
    return <div className="card empty-state">{renderLoading()}</div>;
  }

  return (
    <>
      {error !== null && (
        <div className="banner banner-error" role="alert">
          {error} — обновите страницу, если не уходит.
        </div>
      )}
      <div className="card">
        <div className="card-title-row">
          <span className="card-title">Системные промпты</span>
        </div>
        {prompts === null ? (
          renderLoading()
        ) : (
          prompts.map((prompt) => (
            <div className="lib-item" key={prompt.name}>
              <div className="lib-item-head">
                <span className="lib-item-name">{prompt.name}</span>
                <DefaultBadge isDefault={prompt.is_default} />
              </div>
              <div className="lib-item-meta">{formatSize(prompt.size)}</div>
              <button
                className="btn"
                onClick={() => setOpenAsset({ kind: "prompt", name: prompt.name })}
              >
                Открыть
              </button>
            </div>
          ))
        )}
      </div>
      <div className="card">
        <div className="card-title-row">
          <span className="card-title">Навыки</span>
        </div>
        {skills === null ? (
          renderLoading()
        ) : (
          skills.map((skill) => (
            <div className="lib-item" key={skill.name}>
              <div className="lib-item-head">
                <span className="lib-item-name">{skill.name}</span>
                <DefaultBadge isDefault={skill.is_default} />
              </div>
              {skill.description !== "" && (
                <div className="lib-item-desc">{skill.description}</div>
              )}
              <div className="lib-item-meta">{formatSize(skill.size)}</div>
              <button
                className="btn"
                onClick={() => setOpenAsset({ kind: "skill", name: skill.name })}
              >
                Открыть
              </button>
            </div>
          ))
        )}
      </div>
      {openAsset !== null && (
        <AssetEditor
          key={`${openAsset.kind}:${openAsset.name}`}
          asset={openAsset}
          onClose={() => setOpenAsset(null)}
        />
      )}
    </>
  );
}
