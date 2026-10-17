import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { registerSW } from "virtual:pwa-register";

import { App } from "./App";
import "@fontsource-variable/plus-jakarta-sans";
import "./styles.css";

// The organisation dashboard is desktop-first and pulls in Leaflet; load it only when
// someone opens /dashboard so the observer app on phones stays small.
const DashboardApp = lazy(() => import("./dashboard/DashboardApp"));

registerSW({ immediate: true });

const isDashboard = window.location.pathname.startsWith("/dashboard");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isDashboard ? (
      <Suspense fallback={null}>
        <DashboardApp />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
);
