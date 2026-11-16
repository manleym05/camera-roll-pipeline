export type EventSummary = {
  id: string
  kind: 'trip' | 'local'
  start_at: string
  end_at: string
  label: string
  label_source: string
  ai_label: string | null
  summary: string | null
  status: 'pending' | 'confirmed' | 'dropped'
  note: string | null
  media_count: number
  owners: Record<string, number>
  were_you_there: boolean
  candidates: number
  never_post: number
  cover: string | null
  drafted: number
  approved: number
  sprint: 'pick' | 'backup' | null
  pillar: string | null
  pillars: Record<string, number>
}

export type Candidate = {
  id: string
  media_id: string
  thumb: string | null
  taken_at: string | null
  kind: string
  owner: string
  credit: string | null
  score: number | null
  rank: number | null
  manual_rank: number | null
  subscores: Record<string, number | string | null>
  rationale: string | null
  never_post: boolean
  never_post_reason: string | null
}

// A photo offered for a collection: a candidate, plus the event it came from.
export type Photo = {
  candidate_id: string | null
  media_id: string
  thumb: string | null
  taken_at: string | null
  score: number | null
  pillar: string | null
  rationale: string | null
  event_id: string | null
  event_label: string | null
}

// A post built from photos across several events, rather than one event's carousel.
export type Collection = {
  id: string
  platform: 'instagram' | 'x'
  status: 'draft' | 'approved'
  caption: string | null
  media_ids: string[]
  photos: Photo[]
}

export type CaptionStyle = 'plain' | 'nerd' | 'self_own'

export type Post = {
  id: string
  platform: 'instagram' | 'x'
  status: 'draft' | 'approved'
  caption: string | null
  options: { style: CaptionStyle; text: string }[]
  picked: CaptionStyle | null
  media_ids: string[]
}

export type EventDetail = EventSummary & { items: Candidate[]; posts: Post[] }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/review${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ? String(body.detail) : `${res.status} ${res.statusText}`)
  }
  return res.json()
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const api = {
  events: () => call<EventSummary[]>('/events'),
  event: (id: string) => call<EventDetail>(`/events/${id}`),
  patchEvent: (id: string, body: { label?: string; status?: string; note?: string; sprint?: 'pick' | 'backup' | 'none' }) =>
    call<EventDetail>(`/events/${id}`, json('PATCH', body)),
  merge: (ids: string[]) => call<EventDetail>('/events/merge', json('POST', { ids })),
  reorder: (id: string, candidateIds: string[]) =>
    call<EventDetail>(`/events/${id}/order`, json('PUT', { candidate_ids: candidateIds })),
  neverPost: (candidateId: string, neverPost: boolean) =>
    call<Candidate>(`/candidates/${candidateId}`, json('PATCH', { never_post: neverPost })),
  patchPost: (postId: string, body: { caption?: string; pick?: CaptionStyle; status?: string }) =>
    call<EventDetail>(`/posts/${postId}`, json('PATCH', body)),
  photos: (start: string, end: string) =>
    call<Photo[]>(`/photos?start=${start}&end=${end}`),
  collections: () => call<Collection[]>('/collections'),
  createCollection: (body: { platform: string; media_ids: string[]; caption?: string }) =>
    call<Collection>('/collections', json('POST', body)),
  patchCollection: (id: string, body: { media_ids?: string[]; caption?: string; status?: string }) =>
    call<Collection>(`/collections/${id}`, json('PATCH', body)),
}

export function dateRange(start: string, end: string): string {
  const s = new Date(start)
  const e = new Date(end)
  const day = { month: 'short', day: 'numeric' } as const
  const year = s.getFullYear() === new Date().getFullYear() ? {} : { year: 'numeric' as const }
  if (s.toDateString() === e.toDateString()) return s.toLocaleDateString(undefined, { ...day, ...year })
  return `${s.toLocaleDateString(undefined, day)} – ${e.toLocaleDateString(undefined, { ...day, ...year })}`
}
