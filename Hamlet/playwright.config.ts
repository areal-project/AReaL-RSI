import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "*.e2e.ts",
  timeout: 45000,
  workers: 1,
  use: { headless: true },
  reporter: "list",
});
