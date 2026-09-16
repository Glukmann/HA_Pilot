import type { IconName } from "../components/Icon";

export interface SectionMeta {
  path: string;
  title: string;
  icon: IconName;
}

export const SECTIONS: SectionMeta[] = [
  { path: "/channels", title: "Каналы", icon: "channels" },
  { path: "/models", title: "Модели", icon: "models" },
  { path: "/plugins", title: "Плагины", icon: "plugins" },
  { path: "/persona", title: "Персона", icon: "persona" },
  { path: "/queue", title: "Очередь", icon: "queue" },
  { path: "/vitrine", title: "Витрина", icon: "vitrine" },
  { path: "/admin", title: "Админ", icon: "admin" },
];
