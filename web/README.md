# web

Phone review page: React (Vite) served by a Cloudflare Worker behind Access. See the main [README](../README.md#review-page).

```bash
npm install
npm run dev                      # http://localhost:5173, proxies /api and /thumbs to 127.0.0.1:8000 (run the API with REVIEW_AUTH=none)
npm run build && npx wrangler deploy
```

`worker/index.ts` checks the Cloudflare Access JWT, proxies `/api/*` through the `API` VPC service (Cloudflare Tunnel to the PC) and serves `/thumbs/*` from the `MEDIA` R2 bucket.
