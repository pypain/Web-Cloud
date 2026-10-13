import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '.', testMatch: 'preview.spec.js',
  use: { baseURL: 'http://127.0.0.1:4177', launchOptions: { executablePath: '/usr/bin/chromium', args: ['--no-sandbox'] } },
  webServer: { command: 'npm run preview -- --base=/cloud/ --host 127.0.0.1 --port 4177 --strictPort', cwd: '..', url: 'http://127.0.0.1:4177/cloud/', reuseExistingServer: false },
});
