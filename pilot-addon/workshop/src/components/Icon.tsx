export type IconName =
  | "channels"
  | "models"
  | "plugins"
  | "persona"
  | "vitrine"
  | "admin";

const PATHS: Record<IconName, string[]> = {
  channels: [
    "M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z",
    "M8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01",
  ],
  models: [
    "M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z",
    "M9 9h6v6H9z",
    "M9 1.5V4M15 1.5V4M9 20v2.5M15 20v2.5M20 9h2.5M20 15h2.5M1.5 9H4M1.5 15H4",
  ],
  plugins: [
    "M4 4h7v7H4z",
    "M13 4h7v7h-7z",
    "M4 13h7v7H4z",
    "M13 13h7v7h-7z",
  ],
  persona: [
    "M19.5 21v-1.5a4 4 0 0 0-4-4h-7a4 4 0 0 0-4 4V21",
    "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  ],
  vitrine: ["M3 3h18v18H3z", "M3 9h18", "M9 3v18"],
  admin: ["M22 12h-4l-3 9L9 3l-3 9H2"],
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
