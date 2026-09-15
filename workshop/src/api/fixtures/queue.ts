import type { QueueItem } from "../types";

// Timestamps are "seconds ago" baked at module load; the mock client keeps
// the relative spread when serving them.

const NOW_S = Date.now() / 1000;

export const MOCK_QUEUE: QueueItem[] = [
  {
    id: "mock-proposal-1",
    title: "Сдвинуть уставку теплового насоса на −1°C?",
    summary:
      "Гостиная пуста уже больше часа, за окном +14°C. Пилот предлагает снизить уставку с 22°C до 21°C до вашего возвращения.",
    created_ts: NOW_S - 1260,
  },
  {
    id: "mock-proposal-2",
    title: "Включить вытяжку в ванной после душа?",
    summary:
      "Душ завершён в 08:12. Сценарий: вытяжка N1 на 15 минут, затем контроль по таймеру.",
    created_ts: NOW_S - 320,
  },
];
