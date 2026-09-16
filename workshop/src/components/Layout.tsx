import { NavLink, Outlet, useLocation } from "react-router-dom";

import { usePilotClient } from "../api/context";
import { ConnectionBadge } from "./ConnectionBadge";
import { Icon } from "./Icon";
import { useConnectionState } from "../hooks/useConnectionState";
import { SECTIONS } from "../sections/sections";

export function Layout() {
  const client = usePilotClient();
  const connection = useConnectionState(client);
  const location = useLocation();
  const title =
    SECTIONS.find((s) => location.pathname.startsWith(s.path))?.title ??
    "Мастерская";

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-name">Пилот</div>
          <div className="brand-sub">мастерская</div>
        </div>
        <nav className="nav">
          {SECTIONS.map((section) => (
            <NavLink
              key={section.path}
              to={section.path}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              <Icon name={section.icon} />
              {section.title}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className={client.mode === "mock" ? "mode-chip" : "mode-dim"}>
            {client.mode === "mock" ? "режим: mock-данные" : "источник: аддон"}
          </span>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <h1>{title}</h1>
          <ConnectionBadge state={connection} />
        </header>
        {connection !== "connected" && (
          <div className="banner" role="status">
            {connection === "reconnecting"
              ? "Соединение потеряно, переподключение…"
              : connection === "connecting"
                ? "Подключение к аддону…"
                : "Соединение с аддоном отсутствует."}
          </div>
        )}
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
