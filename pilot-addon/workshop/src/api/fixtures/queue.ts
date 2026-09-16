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
  {
    id: "mock-proposal-3",
    title: "Сценарий «Кино» в кабинете?",
    summary:
      "Samsung TV включён, свет в кабинете горит. Пилот предлагает приглушить свет в зоне TV.",
    created_ts: NOW_S - 95,
  },
];

// Rotating pool the mock feeds into the queue over time, so the live
// section stays interesting without the add-on.
export const MOCK_PROPOSAL_POOL: Array<Omit<QueueItem, "created_ts">> = [
  {
    id: "mock-proposal-4",
    title: "Перевести дом в режим «Отпуск»?",
    summary:
      "Завтра в календаре начало отпуска за городом. Пилот предложит минимальный обогрев и имитацию присутствия.",
  },
  {
    id: "mock-proposal-5",
    title: "Догреть спальню перед сном?",
    summary:
      "Спальня 19.2°C, тепловой насос в эконом-режиме. Пилот предлагает включить Tower Heater на 30 минут.",
  },
  {
    id: "mock-proposal-6",
    title: "Проверить калитку?",
    summary:
      "Калитка открывалась три раза за час, последний импульс дольше обычного. Пилот предлагает посмотреть, не застряла ли.",
  },
];
