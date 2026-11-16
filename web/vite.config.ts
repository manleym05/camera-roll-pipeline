import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// Dev: the local API serves both /review and /thumbs. In production the Worker (worker/index.ts)
// proxies /api through the tunnel and serves /thumbs from R2.
// BRAND_API (web/.env.local, untracked) points the proxy somewhere else, so the laptop can run
// the page against a second API on another port with REVIEW_AUTH=none, away from Cloudflare Access.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const api = env.BRAND_API || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      host: true,
      proxy: {
        '/api': { target: api, rewrite: (p: string) => p.replace(/^\/api/, '') },
        '/thumbs': api,
      },
    },
  }
})
