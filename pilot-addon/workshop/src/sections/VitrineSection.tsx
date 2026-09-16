import { useEffect, useMemo, useState } from "react";

import { usePilotClient } from "../api/context";
import { useConnectionState } from "../hooks/useConnectionState";
import { useStatus } from "../hooks/useStatus";
import { useVitrine } from "../hooks/useVitrine";

/** Mirrors VITRINE_MAX_AGE_S in the add-on (state.py). */
const STALE_AFTER_S = 60;
/** The backend labels room-less entities "No room" (state.py). */
const NO_ROOM_LABEL = "Без комнаты";

interface VitrineRow {
  name: string;
  value: string;
}

interface VitrineGroup {
  room: string;
  rows: VitrineRow[];
}

/**
 * Parses the exact text format VitrineState.lines renders (state.py):
 * room headers "<Комната>:" at column 0, entity rows indented with two
 * spaces as "<имя>: <значение>" (split on the FIRST colon — values may
 * contain colons). Anything unexpected lands in the "Без комнаты" group
 * instead of being dropped.
 */
export function parseVitrineLines(lines: string[]): VitrineGroup[] {
  const groups: VitrineGroup[] = [];
  let current: VitrineGroup | null = null;
  const fallback = (): VitrineGroup => {
    if (current === null) {
      current = { room: NO_ROOM_LABEL, rows: [] };
      groups.push(current);
    }
    return current;
  };
  for (const raw of lines) {
    const trimmed = raw.trim();
    if (trimmed === "") continue;
    if (!raw.startsWith(" ") && trimmed.endsWith(":")) {
      current = { room: trimmed.slice(0, -1).trim(), rows: [] };
      groups.push(current);
      continue;
    }
    const idx = trimmed.indexOf(":");
    if (idx === -1) {
      fallback().rows.push({ name: trimmed, value: "" });
    } else {
      fallback().rows.push({
        name: trimmed.slice(0, idx).trim(),
        value: trimmed.slice(idx + 1).trim(),
      });
    }
  }
  return groups;
}

function roomLabel(room: string): string {
  return room === "No room" ? NO_ROOM_LABEL : room;
}

export function VitrineSection() {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const { status } = useStatus(client);
  const { vitrine, error, refresh } = useVitrine(client);
  const [query, setQuery] = useState("");
  const [roomFilter, setRoomFilter] = useState<string>("all");

  // Relative "обновлено N с назад" ticks once a second.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, []);

  // Snapshot age comes from the status command; remember when it arrived
  // so the number keeps counting between the 10s polls.
  const [statusAtMs, setStatusAtMs] = useState(() => Date.now());
  useEffect(() => setStatusAtMs(Date.now()), [status]);

  const groups = useMemo(
    () => (vitrine ? parseVitrineLines(vitrine.lines) : []),
    [vitrine],
  );
  const roomNames = useMemo(() => groups.map((g) => g.room), [groups]);

  const baseAge = status?.vitrine_age_s ?? null;
  const ageS =
    baseAge === null ? null : baseAge + (nowMs - statusAtMs) / 1000;
  const stale =
    vitrine !== null &&
    (!vitrine.fresh || (ageS !== null && ageS > STALE_AFTER_S));

  const totalRows = groups.reduce((n, g) => n + g.rows.length, 0);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups
      .filter((g) => roomFilter === "all" || g.room === roomFilter)
      .map((g) => ({
        ...g,
        rows: g.rows.filter(
          (r) =>
            q === "" ||
            r.name.toLowerCase().includes(q) ||
            r.value.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.rows.length > 0);
  }, [groups, query, roomFilter]);
  const shownRows = filtered.reduce((n, g) => n + g.rows.length, 0);

  const renderBody = () => {
    if (vitrine === null) {
      if (error !== null && connection === "connected") {
        return (
          <div className="empty-state">
            <h2>Карта дома недоступна</h2>
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
        <div className="empty-state">
          <h2>Загружаю карту дома…</h2>
          <p className="hint">
            Жду ответа аддона на vitrine/get.
          </p>
        </div>
      );
    }
    if (totalRows === 0) {
      return (
        <div className="empty-state">
          <h2>Карта дома пуста</h2>
          <p className="hint">
            Интеграция Пилота ещё не прислала состояния сущностей. Проверьте,
            что интеграция настроена и аддон запущен.
          </p>
        </div>
      );
    }
    if (shownRows === 0) {
      return (
        <div className="empty-state">
          <h2>Ничего не нашлось</h2>
          <p className="hint">По текущему поиску и фильтру комнаты строк нет.</p>
        </div>
      );
    }
    return (
      <div className="vitrine-grid">
        {filtered.map((group) => (
          <div className="vitrine-room" key={group.room}>
            <div className="vitrine-room-name">{roomLabel(group.room)}</div>
            {group.rows.map((row, index) => (
              <div className="vitrine-row" key={`${row.name}-${index}`}>
                <span className="vitrine-entity" title={row.name}>
                  {row.name}
                </span>
                <span className="vitrine-value">{row.value}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    );
  };

  return (
    <>
      <div className={`card ${stale ? "card-stale" : ""}`}>
        <div className="vitrine-head">
          <span className={`badge ${stale ? "badge-stale" : "badge-fresh"}`}>
            {stale ? "🔴 протухло" : "🟢 свежо"}
          </span>
          <span className="vitrine-age">
            {ageS === null
              ? "возраст снапшота пока неизвестен"
              : `обновлено ${Math.floor(ageS)} с назад`}
          </span>
          <div className="vitrine-filters">
            <input
              className="input"
              type="search"
              placeholder="Поиск по имени или значению…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              className="input"
              value={roomFilter}
              onChange={(e) => setRoomFilter(e.target.value)}
            >
              <option value="all">Все комнаты</option>
              {roomNames.map((room) => (
                <option key={room} value={room}>
                  {roomLabel(room)}
                </option>
              ))}
            </select>
          </div>
          <span className="vitrine-count">
            показано {shownRows} из {totalRows}
          </span>
        </div>
      </div>
      {renderBody()}
    </>
  );
}
