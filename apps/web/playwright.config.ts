import { defineConfig } from "@playwright/test";

// Runs against an already-running stack (docker compose, or `make api-dev` + `make web-dev` + worker).
export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  use: {
    baseURL: process.env.LK_WEB_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
});
