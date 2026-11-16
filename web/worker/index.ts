import { createRemoteJWKSet, jwtVerify } from 'jose'

interface Env {
  ASSETS: Fetcher
  MEDIA: R2Bucket
  API: Fetcher
  ACCESS_TEAM_DOMAIN: string
  ACCESS_AUD: string
}

// The API listens on 127.0.0.1:8000 on the owner's PC; the VPC service reaches it through the tunnel.
const ORIGIN = 'http://127.0.0.1:8000'
const THUMB = /^\/thumbs\/[0-9a-f]{64}\.jpg$/

let jwks: ReturnType<typeof createRemoteJWKSet> | undefined

async function accessOk(req: Request, env: Env): Promise<boolean> {
  const token = req.headers.get('Cf-Access-Jwt-Assertion')
  if (!token || !env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD) return false
  const issuer = `https://${env.ACCESS_TEAM_DOMAIN}`
  jwks ??= createRemoteJWKSet(new URL(`${issuer}/cdn-cgi/access/certs`))
  try {
    await jwtVerify(token, jwks, { issuer, audience: env.ACCESS_AUD })
    return true
  } catch {
    return false
  }
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url)
    if (!(await accessOk(req, env))) return new Response('Forbidden', { status: 403 })

    if (url.pathname.startsWith('/api/')) {
      const target = new URL(url.pathname.slice(4) + url.search, ORIGIN)
      const headers = new Headers(req.headers)
      headers.delete('Cookie')
      try {
        return await env.API.fetch(new Request(target, { method: req.method, headers, body: req.body }))
      } catch {
        return Response.json({ detail: 'The API on the PC is unreachable. Is the PC on and the tunnel running?' }, { status: 502 })
      }
    }

    if (THUMB.test(url.pathname)) {
      const obj = await env.MEDIA.get(url.pathname.slice(1))
      if (!obj) return new Response('Not found', { status: 404 })
      return new Response(obj.body, {
        headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'private, max-age=86400', ETag: obj.httpEtag },
      })
    }
    return new Response('Not found', { status: 404 })
  },
} satisfies ExportedHandler<Env>
