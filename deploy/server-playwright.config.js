import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '.', testMatch: 'server-preview.spec.js',
  use: { baseURL: 'http://127.0.0.1:4178', launchOptions: { executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] } },
  webServer: [
    { command: 'python3 deploy/e2e-backend.py', cwd: '..', url: 'http://127.0.0.1:18992/health', reuseExistingServer: false },
    { command: 'npm run preview -- --config deploy/e2e-vite.config.js --host 127.0.0.1 --port 4178 --strictPort', cwd: '..', url: 'http://127.0.0.1:4178/cloud/', reuseExistingServer: false },
  ],
});
