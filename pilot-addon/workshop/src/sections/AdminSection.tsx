import { useEffect, useState } from "react";

import { usePilotClient } from "../api/context";
import type { LayerStatus, LayersStatus } from "../api/types";
import { useConnectionState } from "../hooks/useConnectionState";
import { useStatus } from "../hooks/useStatus";
import { formatAbsolute, formatRelative, formatUptime } from "../utils/time";
import { LiveLogs } from "./LiveLogs";

const LAYER_META: Array<{
  key: keyof LayersStatus;
  name: string;
  desc: string;
}> = [
  {
    key: "vitrine",
    name: "Витрина данных",
    desc: "Карта дома: поток состояний сущностей из Home Assistant.",
  },
  {
    key: "checker",
    name: "Чекер",
    desc: "Детерминированные проверки: флаги отклонений без ИИ, по расписанию.",
  },
  {
    key: "trust",
    name: "Контур доверия",
    desc: "Очередь подтверждений хозяина и append-only журнал действий.",
  },
];

function LayerCard({
  name,
  desc,
  layer,
  nowMs,
}: {
  name: string;
  desc: string;
  layer: LayerStatus | undefined;
  nowMs: number;
}) {
  const alive = layer?.alive ?? false;
  const lastRun = layer?.last_run_ts ?? null;
  return (
    <div className="card layer-card">
      <div className="layer-head">
        <span className="layer-name">{name}</span>
        <span className={`badge ${alive ? "badge-ok" : "badge-dim"}`}>
          {alive ? "работает" : "не активен"}
        </span>
      </div>
      <p className="layer-desc">{desc}</p>
      <div className="layer-meta">
        последний запуск:{" "}
        {lastRun !== null ? (
          <time title={formatAbsolute(lastRun)}>{formatRelative(lastRun, nowMs)}</time>
        ) : (
          <span>ещё не запускался</span>
        )}
      </div>
    </div>
  );
}

export function AdminSection() {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const { status, error, refresh } = useStatus(client);

  // Relative times ("5 мин назад") tick once a second.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);

  const connected = connection === "connected";

  return (
    <>
      {status === null ? (
        <div className="card empty-state">
          <h2>Статус недоступен</h2>
          <p className="hint">
            {connected
              ? (error ?? "Аддон пока не ответил на запрос статуса.")
              : "Нет соединения с аддоном — статус появится после подключения."}
          </p>
          <p className="hint">
            Проверьте, что аддон Пилота запущен (Supervisor → Аддоны → Pilot),
            и что этой панели открыт доступ по ingress.
          </p>
          {connected && (
            <button className="btn" onClick={refresh}>
              Повторить
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="grid-stats">
            <div className="card stat">
              <div className="stat-label">Время работы</div>
              <div className="stat-value">{formatUptime(status.uptime_s)}</div>
              <div className="stat-hint">рантайм {status.runtime_version}</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Очередь подтверждений</div>
              <div className="stat-value">{status.queue_size}</div>
              <div className="stat-hint">
                {status.awaiting_confirmation ? "ожидает решения хозяина" : "пуста"}
              </div>
            </div>
            <div className="card stat">
              <div className="stat-label">Бюджет дня</div>
              <div className="stat-value">
                {status.cost_today.toFixed(2)} ₽
              </div>
              <div className="stat-hint">из {status.daily_budget.toFixed(2)} ₽</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Режим</div>
              <div className="stat-value">{status.mode}</div>
              <div className="stat-hint">пресета {status.persona_preset}</div>
            </div>
          </div>

          <div className="grid-layers">
            {LAYER_META.map((meta) => (
              <LayerCard
                key={meta.key}
                name={meta.name}
                desc={meta.desc}
                layer={status.layers[meta.key]}
                nowMs={nowMs}
              />
            ))}
          </div>
        </>
      )}

      <LiveLogs client={client} />
    </>
  );
}
