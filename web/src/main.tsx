import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { registerSW } from "virtual:pwa-register";

import { App } from "./App";
import "@fontsource-variable/plus-jakarta-sans";
import "./styles.css";

// The organisation dashboard is desktop-first and pulls in Leaflet; load it only when
// someone opens /dashboard so the observer app on phones stays small.
const DashboardApp = lazy(() => import("./dashboard/DashboardApp"));
const AdminApp = lazy(() => import("./admin/AdminApp"));

registerSW({ immediate: true });

const path = window.location.pathname;
const Console = path.startsWith("/admin") ? AdminApp : path.startsWith("/dashboard") ? DashboardApp : null;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {Console ? (
      <Suspense fallback={null}>
        <Console />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
);
