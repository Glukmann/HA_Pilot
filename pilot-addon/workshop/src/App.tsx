import { useEffect, useMemo, useState } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { PilotClientContext } from "./api/context";
import { createPilotClient } from "./api";
import type { PilotClient } from "./api/client";
import { Layout } from "./components/Layout";
import { useStatus } from "./hooks/useStatus";
import { AdminSection } from "./sections/AdminSection";
import { ChannelsSection, ModelsSection } from "./sections/ConfigSection";
import { LibrarySection } from "./sections/LibrarySection";
import { OnboardingWizard } from "./sections/OnboardingWizard";
import { PersonaSection } from "./sections/PersonaSection";
import { QueueSection } from "./sections/QueueSection";
import { VitrineSection } from "./sections/VitrineSection";

/**
 * First-run interception: while the add-on reports onboarded=false the
 * workshop shows the onboarding wizard instead of the normal UI. The route
 * is not changed — the wizard renders by state. It also opens on
 * #/onboarding (sidebar footer link), in edit mode when already onboarded.
 */
function WizardGate({ client }: { client: PilotClient }) {
  const location = useLocation();
  const { status } = useStatus(client);
  const [dismissed, setDismissed] = useState(false);
  const [finished, setFinished] = useState(false);

  const onWizardPath = location.pathname === "/onboarding";
  useEffect(() => {
    if (onWizardPath) setFinished(false);
  }, [onWizardPath]);

  const needsOnboarding = status !== null && status.onboarded === false;
  if (!finished && ((needsOnboarding && !dismissed) || onWizardPath)) {
    return (
      <OnboardingWizard
        onSkip={() => setDismissed(true)}
        onFinish={() => setFinished(true)}
      />
    );
  }
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/admin" replace />} />
        <Route path="/channels" element={<ChannelsSection />} />
        <Route path="/models" element={<ModelsSection />} />
        <Route path="/plugins" element={<LibrarySection />} />
        <Route path="/persona" element={<PersonaSection />} />
        <Route path="/queue" element={<QueueSection />} />
        <Route path="/vitrine" element={<VitrineSection />} />
        <Route path="/admin" element={<AdminSection />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Route>
    </Routes>
  );
}

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
        <WizardGate client={client} />
      </HashRouter>
    </PilotClientContext.Provider>
  );
}
