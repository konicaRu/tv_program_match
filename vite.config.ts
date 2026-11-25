/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

const base = '/tv_program_match/'

export default defineConfig({
  base,
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'icons/apple-touch-icon.png'],
      manifest: {
        name: 'Биатлон — расписание',
        short_name: 'Биатлон',
        description: 'Трансляции и повторы биатлона на каналах «Матча» по московскому времени',
        lang: 'ru',
        start_url: base,
        scope: base,
        display: 'standalone',
        background_color: '#071b31',
        theme_color: '#071b31',
        icons: [
          { src: 'icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        navigateFallback: `${base}index.html`,
        runtimeCaching: [
          {
            // Fresh schedule when online, the last downloaded one when offline.
            urlPattern: ({ url }) => url.pathname.startsWith(`${base}data/`),
            handler: 'NetworkFirst',
            options: { cacheName: 'schedule-data', networkTimeoutSeconds: 5 },
          },
        ],
      },
    }),
  ],
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
