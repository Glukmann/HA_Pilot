import { useEffect, useMemo } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";

import { PilotClientContext } from "./api/context";
import { createPilotClient } from "./api";
import { Layout } from "./components/Layout";
import { AdminSection } from "./sections/AdminSection";
import { ChannelsSection, ModelsSection, PluginsSection } from "./sections/ConfigSection";
import { PersonaSection } from "./sections/PersonaSection";
import { QueueSection } from "./sections/QueueSection";
import { VitrineSection } from "./sections/VitrineSection";

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
            <Route path="/channels" element={<ChannelsSection />} />
            <Route path="/models" element={<ModelsSection />} />
            <Route path="/plugins" element={<PluginsSection />} />
            <Route path="/persona" element={<PersonaSection />} />
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
