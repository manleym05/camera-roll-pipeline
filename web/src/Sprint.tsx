import { useEffect, useMemo, useState } from 'react'
import { api, dateRange, type EventSummary } from './api'

// A sprint is a run of posts picked one per day in date order, plus spares.
const PICKS = 30
const BACKUPS = 5
// Optional minimum per content pillar, so a run doesn't end up all one thing.
// Keys are pillar names from the API's PILLARS setting, e.g. { fitness: 4, work: 4 }.
// Leave empty for no quotas.
const MIN_PILLAR: Record<string, number> = {}

type Filter = 'all' | 'pick' | 'backup'

export default function Sprint() {
  const [events, setEvents] = useState<EventSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')

  useEffect(() => {
    api.events().then(setEvents, (e) => setError(e.message))
  }, [])

  const live = useMemo(() => (events ?? []).filter((e) => e.status !== 'dropped'), [events])
  const picks = live.filter((e) => e.sprint === 'pick')
  const backups = live.filter((e) => e.sprint === 'backup')
  const day = new Map(picks.map((e, i) => [e.id, i + 1]))
  // A pick counts toward a pillar when any of its top photos carries it.
  const pillarCount = (p: string) => picks.filter((e) => e.pillars[p]).length
  const captioned = picks.filter((e) => e.drafted > 0 && e.approved === e.drafted).length
  const shown = filter === 'all' ? live : live.filter((e) => e.sprint === filter)

  async function mark(e: EventSummary, to: 'pick' | 'backup') {
    const next = e.sprint === to ? 'none' : to
    setError(null)
    try {
      const updated = await api.patchEvent(e.id, { sprint: next })
      setEvents((all) => all!.map((x) => (x.id === e.id ? { ...x, sprint: updated.sprint } : x)))
    } catch (err) {
      setError((err as Error).message)
    }
  }

  if (!events) return <div className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</div>

  const tiles: [string, number, number][] = [
    ['Picks', picks.length, PICKS],
    ['Backups', backups.length, BACKUPS],
    ...Object.entries(MIN_PILLAR).map(
      ([name, min]): [string, number, number] => [
        name.charAt(0).toUpperCase() + name.slice(1),
        pillarCount(name),
        min,
      ],
    ),
    ['Captions', captioned, picks.length || PICKS],
  ]

  return (
    <div className="page">
      <header className="top">
        <a className="back" href="#/">‹ Events</a>
      </header>
      <h1>30 moments</h1>
      <p className="muted small">Pick 30 and 5 backups. Picks post one a day in date order. A pick counts toward fitness or work when its top photos include one.</p>

      <div className="tally">
        {tiles.map(([label, n, goal]) => (
          <div key={label} className={n >= goal ? 'done' : ''}>
            <b>{n}/{goal}</b>
            <span className="muted small">{label}</span>
          </div>
        ))}
      </div>

      <nav className="chips">
        {(['all', 'pick', 'backup'] as Filter[]).map((f) => (
          <button key={f} className={f === filter ? 'chip on' : 'chip'} onClick={() => setFilter(f)}>
            {f === 'all' ? `All (${live.length})` : f === 'pick' ? `Picks (${picks.length})` : `Backups (${backups.length})`}
          </button>
        ))}
      </nav>
      {error && <p className="error">{error}</p>}

      <ul className="events">
        {shown.map((e) => (
          <li key={e.id} className="sprint-row">
            <a href={`#/event/${e.id}`}>
              <div className="cover">{e.cover ? <img src={e.cover} alt="" loading="lazy" /> : null}</div>
              <div className="meta">
                <div className="title">{day.has(e.id) ? `Day ${day.get(e.id)} · ` : ''}{e.label}</div>
                <div className="muted small">{dateRange(e.start_at, e.end_at)}</div>
                <div className="badges">
                  {Object.keys(e.pillars)
                    .sort((a, b) => e.pillars[b] - e.pillars[a])
                    .map((p) => (
                      <span key={p} className={p === e.pillar ? 'badge on' : 'badge'}>{p}</span>
                    ))}
                  {e.drafted > 0 && (
                    <span className={e.approved === e.drafted ? 'badge confirmed' : 'badge'}>
                      {e.approved}/{e.drafted} captions
                    </span>
                  )}
                  {e.were_you_there && <span className="badge warn">were you there?</span>}
                </div>
              </div>
            </a>
            <div className="picks">
              <button className={e.sprint === 'pick' ? 'primary' : 'ghost'} onClick={() => mark(e, 'pick')}>
                {e.sprint === 'pick' ? '✓ Pick' : 'Pick'}
              </button>
              <button className={e.sprint === 'backup' ? 'primary' : 'ghost'} onClick={() => mark(e, 'backup')}>
                {e.sprint === 'backup' ? '✓ Backup' : 'Backup'}
              </button>
            </div>
          </li>
        ))}
      </ul>
      {shown.length === 0 && <p className="muted">Nothing here yet.</p>}
    </div>
  )
}
