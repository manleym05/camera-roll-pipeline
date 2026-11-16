import { useEffect, useMemo, useState } from 'react'
import { api, dateRange, type EventSummary } from './api'

type Filter = 'all' | 'pending' | 'confirmed' | 'dropped' | 'check'

const FILTERS: [Filter, string][] = [
  ['all', 'All'],
  ['pending', 'To review'],
  ['check', 'Were you there?'],
  ['confirmed', 'Confirmed'],
  ['dropped', 'Dropped'],
]

// Kept between visits so going back renders instantly (and at the old scroll position), then refreshes.
let cached: EventSummary[] | null = null

export default function EventList() {
  const [events, setEvents] = useState<EventSummary[] | null>(cached)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [merging, setMerging] = useState<string[] | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => api.events().then((e) => setEvents((cached = e)), (e) => setError(e.message))
  useEffect(() => {
    load()
  }, [])

  const shown = useMemo(() => {
    if (!events) return []
    return events.filter((e) =>
      filter === 'all' ? e.status !== 'dropped' : filter === 'check' ? e.were_you_there && e.status === 'pending' : e.status === filter,
    )
  }, [events, filter])

  const counts = useMemo(() => {
    const c = { confirmed: 0, pending: 0, dropped: 0 }
    events?.forEach((e) => c[e.status]++)
    return c
  }, [events])

  async function doMerge() {
    if (!merging || merging.length < 2) return
    const picked = events!.filter((e) => merging.includes(e.id))
    if (!confirm(`Merge ${picked.length} events into “${picked[0].label}”?`)) return
    setBusy(true)
    try {
      const merged = await api.merge(merging)
      setMerging(null)
      window.location.hash = `#/event/${merged.id}`
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function toggle(id: string) {
    setMerging((m) => (m!.includes(id) ? m!.filter((x) => x !== id) : [...m!, id]))
  }

  if (error) return <div className="page"><p className="error">{error}</p><button onClick={() => { setError(null); load() }}>Retry</button></div>
  if (!events) return <div className="page muted">Loading events…</div>

  return (
    <div className="page">
      <header className="top">
        <div>
          <h1>Moments</h1>
          <p className="muted small">
            {counts.confirmed} confirmed · {counts.pending} to review · {counts.dropped} dropped
          </p>
        </div>
        {merging ? (
          <div className="row">
            <button className="ghost" onClick={() => setMerging(null)}>Cancel</button>
            <button className="primary" disabled={merging.length < 2 || busy} onClick={doMerge}>
              Merge {merging.length || ''}
            </button>
          </div>
        ) : (
          <div className="row">
            <a className="button ghost" href="#/collections">Collections</a>
            <button className="ghost" onClick={() => setMerging([])}>Merge</button>
          </div>
        )}
      </header>

      {merging && <p className="hint">Tap the events to merge, e.g. two halves of one trip. They fold into the earliest one.</p>}

      <nav className="chips">
        {FILTERS.map(([f, label]) => (
          <button key={f} className={f === filter ? 'chip on' : 'chip'} onClick={() => setFilter(f)}>
            {label}
          </button>
        ))}
      </nav>

      <ul className="events">
        {shown.map((e) => {
          const picked = merging?.includes(e.id)
          const body = (
            <>
              <div className="cover">{e.cover ? <img src={e.cover} alt="" loading="lazy" /> : null}</div>
              <div className="meta">
                <div className="title">{e.label}</div>
                <div className="muted small">
                  {dateRange(e.start_at, e.end_at)} · {e.kind} · {e.candidates} photos
                </div>
                <div className="badges">
                  {e.status !== 'pending' && <span className={`badge ${e.status}`}>{e.status}</span>}
                  {e.were_you_there && <span className="badge warn">were you there?</span>}
                  {e.never_post > 0 && <span className="badge">{e.never_post} never-post</span>}
                  {e.sprint && <span className="badge confirmed">{e.sprint}</span>}
                  {e.drafted > 0 && (
                    <span className={e.approved === e.drafted ? 'badge confirmed' : 'badge'}>
                      {e.approved}/{e.drafted} captions approved
                    </span>
                  )}
                </div>
              </div>
            </>
          )
          return (
            <li key={e.id} className={picked ? 'picked' : ''}>
              {merging ? (
                <button className="card" onClick={() => toggle(e.id)}>{body}</button>
              ) : (
                <a className="card" href={`#/event/${e.id}`}>{body}</a>
              )}
            </li>
          )
        })}
      </ul>
      {shown.length === 0 && <p className="muted">Nothing here.</p>}
    </div>
  )
}
