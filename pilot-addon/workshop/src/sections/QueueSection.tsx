import { useEffect, useState } from "react";

import { usePilotClient } from "../api/context";
import type { QueueItem } from "../api/types";
import { useConnectionState } from "../hooks/useConnectionState";
import { useQueue } from "../hooks/useQueue";
import { formatAbsolute, formatRelative } from "../utils/time";

export function QueueSection() {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const { items, loaded, error, refresh } = useQueue(client);
  const [pending, setPending] = useState<Record<string, "yes" | "no">>({});
  const [actionError, setActionError] = useState<string | null>(null);

  // Relative timestamps ("12 мин назад") tick once a second.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);

  const confirm = (item: QueueItem, decision: "yes" | "no") => {
    setPending((prev) => ({ ...prev, [item.id]: decision }));
    setActionError(null);
    client
      .request<{ ok: boolean }>("queue/confirm", { id: item.id, decision })
      // The server also broadcasts a fresh queue after a confirm; refetch
      // as a safety sync in case the event was lost to a reconnect.
      .then(() => refresh())
      .catch((err: unknown) => {
        setActionError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        setPending((prev) => {
          const next = { ...prev };
          delete next[item.id];
          return next;
        });
      });
  };

  const renderBody = () => {
    if (!loaded) {
      if (error !== null && connection === "connected") {
        return (
          <div className="card empty-state">
            <h2>Очередь недоступна</h2>
            <p className="hint">{error}</p>
            <p className="hint">
              Проверьте, что аддон Пилота запущен (Supervisor → Аддоны →
              Pilot), и обновите раздел.
            </p>
            <button className="btn" onClick={refresh}>
              Повторить
            </button>
          </div>
        );
      }
      return (
        <div className="card empty-state">
          <h2>Загружаю очередь…</h2>
          <p className="hint">Жду ответа аддона на queue/get.</p>
        </div>
      );
    }
    if (items.length === 0) {
      return (
        <div className="card empty-state">
          <h2>Ничего не ждёт вашего решения</h2>
          <p className="hint">
            Когда Пилот предложит изменение, оно появится здесь — с кнопками
            «Принять» и «Отклонить». Без вашего ответа Пилот ничего не меняет.
          </p>
        </div>
      );
    }
    return items.map((item) => (
      <div className="card queue-item" key={item.id}>
        <div className="queue-head">
          <span className="queue-title">{item.title}</span>
          <time
            className="queue-time"
            title={formatAbsolute(item.created_ts)}
          >
            {formatRelative(item.created_ts, nowMs)}
          </time>
        </div>
        {item.summary !== "" && <p className="queue-summary">{item.summary}</p>}
        <div className="queue-actions">
          <button
            className="btn btn-yes"
            disabled={item.id in pending}
            onClick={() => confirm(item, "yes")}
          >
            {pending[item.id] === "yes" ? "Отправляю…" : "Принять"}
          </button>
          <button
            className="btn btn-no"
            disabled={item.id in pending}
            onClick={() => confirm(item, "no")}
          >
            {pending[item.id] === "no" ? "Отправляю…" : "Отклонить"}
          </button>
        </div>
      </div>
    ));
  };

  return (
    <>
      {actionError !== null && (
        <div className="banner banner-error" role="alert">
          Не удалось отправить решение: {actionError}. Попробуйте ещё раз.
        </div>
      )}
      {renderBody()}
    </>
  );
}
