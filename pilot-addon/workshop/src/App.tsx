import { useEffect, useMemo } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";

import { PilotClientContext } from "./api/context";
import { createPilotClient } from "./api";
import { Layout } from "./components/Layout";
import { AdminSection } from "./sections/AdminSection";
import { QueueSection } from "./sections/QueueSection";
import { SectionStub } from "./sections/SectionStub";
import { SECTIONS } from "./sections/sections";
import { VitrineSection } from "./sections/VitrineSection";

// Live sections render real data; everything else falls back to a stub.
const LIVE_SECTIONS = new Set(["/admin", "/queue", "/vitrine"]);

export default function App() {
  // One client for the app lifetime; started/stopped with the mount.
  const client = useMemo(() => createPilotClient(), []);
  useEffect(() => {
    client.start();
    return () => client.stop();
  }, [client]);

  return (
    <PilotClientContext.Provider value={client}>
      {/* Hash routing: robust when the bundle is served from any ingress
          sub-path without server-side SPA fallback. */}
      <HashRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Navigate to="/admin" replace />} />
            {SECTIONS.filter((s) => !LIVE_SECTIONS.has(s.path)).map((section) => (
              <Route
                key={section.path}
                path={section.path}
                element={<SectionStub meta={section} />}
              />
            ))}
            <Route path="/queue" element={<QueueSection />} />
            <Route path="/vitrine" element={<VitrineSection />} />
            <Route path="/admin" element={<AdminSection />} />
            <Route path="*" element={<Navigate to="/admin" replace />} />
          </Route>
        </Routes>
      </HashRouter>
    </PilotClientContext.Provider>
  );
}
