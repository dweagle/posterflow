import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'

// Bake the release version into the bundle so a running tab knows what it IS —
// the update banner compares this against the server's X-App-Version header.
const appVersion = readFileSync(new URL('../VERSION', import.meta.url), 'utf-8').trim()

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  test: {
    setupFiles: ['./tests/setup.ts'],
    // Reset mock call history between tests so module-level vi.fn() mocks
    // (e.g. onClose) don't accumulate calls across tests.
    clearMocks: true,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) {
            return
          }

          if (id.includes('react-router')) {
            return 'vendor-router'
          }

          if (id.includes('react') || id.includes('scheduler')) {
            return 'vendor-react'
          }

          if (id.includes('axios')) {
            return 'vendor-axios'
          }

          if (id.includes('lucide-react')) {
            return 'vendor-icons'
          }

          return 'vendor'
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true
      }
    },
    watch: {
      usePolling: true
    }
  }
})