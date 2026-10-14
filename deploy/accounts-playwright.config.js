import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '.', testMatch: 'accounts-preview.spec.js',
  use: { baseURL: 'http://127.0.0.1:4179', launchOptions: { executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] } },
  webServer: [
    { command: 'python3 deploy/e2e-accounts-backend.py', cwd: '..', url: 'http://127.0.0.1:18993/health', reuseExistingServer: false },
    { command: 'npm run preview -- --config deploy/e2e-accounts-vite.config.js --host 127.0.0.1 --port 4179 --strictPort', cwd: '..', url: 'http://127.0.0.1:4179/cloud/', reuseExistingServer: false },
  ],
});
