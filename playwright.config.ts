import { defineConfig } from '@playwright/test'

const port = 5174

export default defineConfig({
  testDir: 'e2e',
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${port}/tv_program_match/`,
    // Moscow times must not depend on the phone's zone.
    timezoneId: 'America/New_York',
    locale: 'ru-RU',
    serviceWorkers: 'block',
  },
  projects: [360, 390, 430].map((width) => ({
    name: `${width}px`,
    use: { viewport: { width, height: 800 }, isMobile: true, hasTouch: true },
  })),
  webServer: {
    command: `node node_modules/vite/bin/vite.js --port ${port} --strictPort`,
    url: `http://localhost:${port}/tv_program_match/`,
    reuseExistingServer: !process.env.CI,
  },
})
