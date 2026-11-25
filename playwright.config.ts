import { defineConfig } from '@playwright/test'

const devPort = 5174
const previewPort = 4174

export default defineConfig({
  testDir: 'e2e',
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${devPort}/tv_program_match/`,
    // Moscow times must not depend on the phone's zone.
    timezoneId: 'America/New_York',
    locale: 'ru-RU',
    serviceWorkers: 'block',
  },
  projects: [
    ...[360, 390, 430].map((width) => ({
      name: `${width}px`,
      testIgnore: /offline/,
      use: { viewport: { width, height: 800 }, isMobile: true, hasTouch: true },
    })),
    {
      // Runs against the production build (npm run build first): the service worker only exists there.
      name: 'offline',
      testMatch: /offline/,
      use: {
        baseURL: `http://localhost:${previewPort}/tv_program_match/`,
        serviceWorkers: 'allow',
        viewport: { width: 390, height: 800 },
      },
    },
  ],
  webServer: [
    {
      command: `node node_modules/vite/bin/vite.js --port ${devPort} --strictPort`,
      url: `http://localhost:${devPort}/tv_program_match/`,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: `node node_modules/vite/bin/vite.js preview --port ${previewPort} --strictPort`,
      url: `http://localhost:${previewPort}/tv_program_match/`,
      reuseExistingServer: !process.env.CI,
    },
  ],
})
