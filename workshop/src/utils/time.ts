/** Time formatting helpers (Russian UI). */

/** HH:MM:SS for a unix-seconds timestamp. */
export function formatClock(tsSeconds: number): string {
  const d = new Date(tsSeconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Absolute date-time for tooltips, e.g. "15.09.2026, 14:03:22". */
export function formatAbsolute(tsSeconds: number): string {
  return new Date(tsSeconds * 1000).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Relative past time: "только что", "12 с назад", "5 мин назад", "2 ч назад". */
export function formatRelative(tsSeconds: number, nowMs: number): string {
  const diff = Math.max(0, nowMs / 1000 - tsSeconds);
  if (diff < 5) return "только что";
  if (diff < 60) return `${Math.floor(diff)} с назад`;
  if (diff < 3_600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86_400) return `${Math.floor(diff / 3_600)} ч назад`;
  return `${Math.floor(diff / 86_400)} д назад`;
}

/** Uptime rendering: "45 с", "12 мин", "3 ч 07 мин", "2 д 5 ч". */
export function formatUptime(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  if (s < 120) return `${s} с`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) {
    return restMinutes > 0 ? `${hours} ч ${String(restMinutes).padStart(2, "0")} мин` : `${hours} ч`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours > 0 ? `${days} д ${restHours} ч` : `${days} д`;
}
