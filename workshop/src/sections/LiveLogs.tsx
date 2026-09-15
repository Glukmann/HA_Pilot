import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { PilotClient } from "../api/client";
import type { LogEntry } from "../api/types";
import { formatClock } from "../utils/time";

/** Hard cap on retained log lines; older ones are dropped from the DOM. */
const MAX_DOM_LINES = 500;
const RECENT_LIMIT = 200;
/** Pixels from the bottom that still count as "following" the tail. */
const FOLLOW_THRESHOLD_PX = 40;

type LevelGroup = "info" | "warning" | "error";

function levelGroup(level: string): LevelGroup {
  const up = level.toUpperCase();
  if (up === "WARNING") return "warning";
  if (up === "ERROR" || up === "CRITICAL") return "error";
  return "info";
}

const GROUP_LABELS: Record<LevelGroup, string> = {
  info: "Инфо",
  warning: "Предупреждения",
  error: "Ошибки",
};

export function LiveLogs({ client }: { client: PilotClient }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [levels, setLevels] = useState<Record<LevelGroup, boolean>>({
    info: true,
    warning: true,
    error: true,
  });
  const [follow, setFollow] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  followRef.current = follow;

  const loadRecent = useCallback(() => {
    client
      .request<LogEntry[]>("logs/recent", { limit: RECENT_LIMIT })
      .then((recent) => {
        setEntries(recent.slice(-MAX_DOM_LINES));
        setLoadError(null);
        setFollow(true);
      })
      .catch((err: unknown) => {
        setLoadError(err instanceof Error ? err.message : String(err));
      });
  }, [client]);

  // Boot sequence per protocol: logs/recent, then logs/subscribe. Both are
  // repeated after every reconnect (the server forgets subscriptions when
  // the socket drops).
  useEffect(() => {
    let cancelled = false;
    let subscribed = false;

    const boot = () => {
      loadRecent();
      client
        .request<{ ok: boolean }>("logs/subscribe", {})
        .then(() => {
          if (!cancelled) subscribed = true;
        })
        .catch(() => {
          // Subscription failed; the connection banner tells the user.
        });
    };

    const offLog = client.onLog((entry) => {
      setEntries((prev) => [...prev.slice(-(MAX_DOM_LINES - 1)), entry]);
    });
    const offConn = client.onConnectionChange((state) => {
      if (state === "connected") boot();
    });

    if (client.getConnectionState() === "connected") boot();

    return () => {
      cancelled = true;
      offLog();
      offConn();
      if (subscribed) {
        client.request<{ ok: boolean }>("logs/unsubscribe", {}).catch(() => {});
      }
    };
  }, [client, loadRecent]);

  // Autoscroll to the tail while following.
  useEffect(() => {
    const box = boxRef.current;
    if (box && followRef.current) {
      box.scrollTop = box.scrollHeight;
    }
  }, [entries]);

  const handleScroll = () => {
    const box = boxRef.current;
    if (box === null) return;
    const distance = box.scrollHeight - box.scrollTop - box.clientHeight;
    setFollow(distance < FOLLOW_THRESHOLD_PX);
  };

  const scrollToEnd = () => {
    setFollow(true);
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  };

  const counts = useMemo(() => {
    const result: Record<LevelGroup, number> = { info: 0, warning: 0, error: 0 };
    for (const entry of entries) {
      result[levelGroup(entry.level)] += 1;
    }
    return result;
  }, [entries]);

  const visible = useMemo(
    () => entries.filter((entry) => levels[levelGroup(entry.level)]),
    [entries, levels],
  );

  const toggleLevel = (group: LevelGroup) => {
    setLevels((prev) => ({ ...prev, [group]: !prev[group] }));
  };

  return (
    <div className="card logs-card">
      <div className="logs-toolbar">
        <span className="logs-title">Журнал в реальном времени</span>
        {(Object.keys(GROUP_LABELS) as LevelGroup[]).map((group) => (
          <button
            key={group}
            className={`chip chip-${group} ${levels[group] ? "chip-on" : ""}`}
            onClick={() => toggleLevel(group)}
          >
            {GROUP_LABELS[group]} · {counts[group]}
          </button>
        ))}
        <span className="logs-count">
          показано {visible.length} из {entries.length}
        </span>
        {!follow && (
          <>
            <span className="chip chip-paused">прокрутка на паузе</span>
            <button className="btn-follow" onClick={scrollToEnd}>
              К концу ↓
            </button>
          </>
        )}
      </div>
      <div className="logs-view" ref={boxRef} onScroll={handleScroll}>
        {visible.length === 0 ? (
          <div className="logs-empty">
            {loadError !== null
              ? "Не удалось получить логи от аддона."
              : entries.length === 0
                ? "Пока нет записей — жду события от аддона."
                : "Нет строк, подходящих под выбранные фильтры."}
            {loadError !== null && <div className="hint">{loadError}</div>}
            {loadError !== null && (
              <div className="hint">
                Проверьте, что аддон Пилота запущен, и нажмите «Повторить».
              </div>
            )}
            {loadError !== null && (
              <button className="btn" onClick={loadRecent}>
                Повторить
              </button>
            )}
          </div>
        ) : (
          visible.map((entry, index) => {
            const group = levelGroup(entry.level);
            return (
              <div className="log-line" key={`${entry.ts}-${index}`}>
                <span className="log-time">{formatClock(entry.ts)}</span>
                <span className={`log-level lv-${group}`}>{entry.level}</span>
                <span className="log-logger">{entry.logger}</span>
                <span className="log-message">{entry.message}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
