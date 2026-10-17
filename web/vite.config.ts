/// <reference types="vitest/config" />
import basicSsl from "@vitejs/plugin-basic-ssl";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// The API runs separately (uvicorn or docker compose). Proxying keeps the app same-origin,
// so it works unchanged on a phone over the LAN and behind a single domain in production.
const apiTarget = process.env.API_URL ?? "http://localhost:8000";
const proxy = { "/api": apiTarget, "/media": apiTarget };

export default defineConfig({
  plugins: [
    react(),
    // Phones only allow GPS on HTTPS: `HTTPS=1 npm run dev -- --host` uses a self-signed cert.
    ...(process.env.HTTPS ? [basicSsl()] : []),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg", "apple-touch-icon.png"],
      manifest: {
        name: "Vanya: wildlife, spotted together",
        short_name: "Vanya",
        description: "Spot birds, animals and plants, get an instant AI identification, and help protect wildlife.",
        theme_color: "#155c3c",
        background_color: "#f5f4ee",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-512-maskable.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // The app shell works offline; API and photo requests always go to the network
        // (captures are queued in IndexedDB instead).
        navigateFallback: "/index.html",
        // The desktop dashboard (with Leaflet) and admin console load on demand; don't push them to phones.
        globIgnores: ["**/DashboardApp-*", "**/AdminApp-*"],
        // App code plus the Latin font files, so the app looks the same offline.
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        navigateFallbackDenylist: [/^\/api\//, /^\/media\//],
      },
    }),
  ],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  test: { environment: "node" },
});
