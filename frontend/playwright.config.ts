import {defineConfig} from '@playwright/test';
const remote = process.env.ATLAS_E2E_URL;
const port = Number(process.env.ATLAS_E2E_PORT||5174);
export default defineConfig({testDir:'e2e',use:{baseURL:remote || `http://127.0.0.1:${port}`},webServer:remote?undefined:{command:`npm run dev -- --port ${port} --strictPort`,url:`http://127.0.0.1:${port}`,reuseExistingServer:!process.env.ATLAS_E2E_PORT}});
