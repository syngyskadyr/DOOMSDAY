import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5174", channel: "chrome", headless: true, viewport: { width: 1440, height: 1000 } },
  webServer: [
    { command: `"${process.env.PYTHON_EXECUTABLE || "python"}" tests/serve_fixture.py`, url: "http://127.0.0.1:8012/api/health", reuseExistingServer: false },
    { command: "node scripts/serve.mjs --dist", url: "http://127.0.0.1:5174", env: { API_BASE_URL: "http://127.0.0.1:8012", PORT: "5174" }, reuseExistingServer: false },
  ],
});
