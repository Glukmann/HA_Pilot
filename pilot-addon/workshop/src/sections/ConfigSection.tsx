import { usePilotClient } from "../api/context";
import { useConfig } from "../hooks/useConfig";
import { useConnectionState } from "../hooks/useConnectionState";

/** Renders one config node recursively: nested objects become indented
 * groups, scalars become key-value rows, "***" renders as a masked secret. */
function ConfigNode({ name, value }: { name: string; value: unknown }) {
  if (typeof value === "object" && value !== null) {
    const entries: Array<[string, unknown]> = Array.isArray(value)
      ? value.map((item, index): [string, unknown] => [String(index + 1), item])
      : Object.entries(value);
    return (
      <div className="cfg-group">
        <div className="cfg-group-key">{name}</div>
        <div className="cfg-children">
          {entries.map(([key, child]) => (
            <ConfigNode key={key} name={key} value={child} />
          ))}
        </div>
      </div>
    );
  }
  if (value === null) {
    return (
      <div className="cfg-row">
        <span className="cfg-key">{name}</span>
        <span className="cfg-value">—</span>
      </div>
    );
  }
  const isSecret = value === "***";
  const display = typeof value === "boolean" ? (value ? "да" : "нет") : String(value);
  return (
    <div className="cfg-row">
      <span className="cfg-key">{name}</span>
      <span className={`cfg-value ${isSecret ? "cfg-secret" : ""}`}>{display}</span>
    </div>
  );
}

interface ConfigSectionProps {
  /** Section key inside config/get sections (channels/models/plugins). */
  sectionKey: string;
  title: string;
  emptyHeadline: string;
  emptyBody: string;
}

/**
 * Read-only view of one runtime config section (task 9 lite): a key-value
 * tree of the masked config/get payload. Editing configs is a later phase.
 */
export function ConfigSection({
  sectionKey,
  title,
  emptyHeadline,
  emptyBody,
}: ConfigSectionProps) {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const { config, loaded, error, refresh } = useConfig(client);

  if (!loaded) {
    if (error !== null && connection === "connected") {
      return (
        <div className="card empty-state">
          <h2>{title}: конфигурация недоступна</h2>
          <p className="hint">{error}</p>
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
        <h2>Загружаю конфигурацию…</h2>
        <p className="hint">Жду ответа аддона на config/get.</p>
      </div>
    );
  }

  const section = config?.sections[sectionKey];
  const sectionEmpty =
    section === undefined ||
    section === null ||
    (typeof section === "object" &&
      !Array.isArray(section) &&
      Object.keys(section).length === 0);

  if (!config?.configured || sectionEmpty) {
    return (
      <div className="card empty-state">
        <h2>{emptyHeadline}</h2>
        <p className="hint">{emptyBody}</p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-title-row">
        <span className="card-title">{title}</span>
        <span className="badge badge-dim">только чтение</span>
      </div>
      <div className="cfg-tree">
        <ConfigNode name={sectionKey} value={section} />
      </div>
    </div>
  );
}

export function ChannelsSection() {
  return (
    <ConfigSection
      sectionKey="channels"
      title="Каналы связи"
      emptyHeadline="Каналы ещё не настроены."
      emptyBody="Мессенджеры (MAX, Telegram), чат и голос Home Assistant появятся здесь после онбординга рантайма."
    />
  );
}

export function ModelsSection() {
  return (
    <ConfigSection
      sectionKey="models"
      title="Модели"
      emptyHeadline="Модели ещё не настроены."
      emptyBody="Провайдеры, ключи API и выбор моделей по направлениям появятся здесь после онбординга рантайма."
    />
  );
}

export function PluginsSection() {
  return (
    <ConfigSection
      sectionKey="plugins"
      title="Плагины и навыки"
      emptyHeadline="Плагины ещё не настроены."
      emptyBody="Каталог навыков и workspace агента (skills, память, журналы) появятся здесь после онбординга рантайма."
    />
  );
}
