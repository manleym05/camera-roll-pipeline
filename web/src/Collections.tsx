import { useEffect, useMemo, useState } from 'react'
import {
  DndContext,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { api, type Collection, type Photo } from './api'

// the owner posts a handful of collections rather than one post per event: everything before
// 2026 as a single look back, then a few for this year, then whatever happens next.
const PERIODS = [
  {
    key: 'through-2025',
    label: 'Through 2025',
    start: '2024-01-01',
    end: '2025-12-31',
    note: 'Everything before 2026, with the 2024 autumn and Christmas photos folded in.',
  },
  { key: '2026', label: '2026', start: '2026-01-01', end: '2026-12-31', note: 'This year so far.' },
  { key: 'recent', label: 'Last 90 days', start: '', end: '', note: 'Whatever is upcoming in life.' },
]

// Meta allows 20 per carousel; X takes 4.
const LIMIT: Record<string, number> = { instagram: 20, x: 4 }
const PILLAR_EMOJI: Record<string, string> = { fitness: 'fit', work: 'work', travel: 'trip', tech: 'tech' }

function Picked({
  id,
  n,
  photo,
  locked,
  onRemove,
}: {
  id: string
  n: number
  photo: Photo | undefined
  locked: boolean
  onRemove: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id, disabled: locked })
  const style = { transform: CSS.Transform.toString(transform), transition, zIndex: isDragging ? 5 : undefined }
  return (
    <li ref={setNodeRef} style={style} className={`tile picked${isDragging ? ' dragging' : ''}`}>
      <div className="frame" {...attributes} {...listeners}>
        {photo?.thumb && <img src={photo.thumb} alt="" loading="lazy" draggable={false} />}
        <span className="n">{n}</span>
      </div>
      <div className="caption">
        <span className="small muted">{photo?.event_label ?? 'another period'}</span>
        <button className="toggle" disabled={locked} onClick={onRemove} title="Remove from the post">
          &times;
        </button>
      </div>
    </li>
  )
}

function ninetyDays(): { start: string; end: string } {
  const end = new Date()
  const start = new Date(end.getTime() - 90 * 24 * 3600 * 1000)
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) }
}

export default function Collections() {
  const [periodKey, setPeriodKey] = useState(PERIODS[0].key)
  const [platform, setPlatform] = useState<'instagram' | 'x'>('instagram')
  const [photos, setPhotos] = useState<Photo[] | null>(null)
  const [saved, setSaved] = useState<Collection[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [caption, setCaption] = useState('')
  const [current, setCurrent] = useState<Collection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const period = PERIODS.find((p) => p.key === periodKey)!
  const range = useMemo(
    () => (period.start ? { start: period.start, end: period.end } : ninetyDays()),
    [period],
  )
  const limit = LIMIT[platform] ?? 10
  const approved = current?.status === 'approved'

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    // Press-and-hold to drag, so a normal swipe still scrolls the page.
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  function onDragEnd({ active, over }: DragEndEvent) {
    if (approved || !over || active.id === over.id) return
    const from = selected.indexOf(String(active.id))
    const to = selected.indexOf(String(over.id))
    if (from < 0 || to < 0) return
    setSelected(arrayMove(selected, from, to))
  }

  useEffect(() => {
    setPhotos(null)
    api.photos(range.start, range.end).then(setPhotos, (e) => setError(e.message))
  }, [range.start, range.end])

  useEffect(() => {
    api.collections().then(setSaved, (e) => setError(e.message))
  }, [])

  const byId = useMemo(() => new Map((photos ?? []).map((p) => [p.media_id, p])), [photos])

  function toggle(id: string) {
    if (approved) return
    setError(null)
    setSelected((cur) => {
      if (cur.includes(id)) return cur.filter((x) => x !== id)
      if (cur.length >= limit) {
        setError(`${platform} takes at most ${limit} photos. Drop one first.`)
        return cur
      }
      return [...cur, id]
    })
  }

  function open(c: Collection) {
    setCurrent(c)
    setPlatform(c.platform)
    setSelected(c.media_ids)
    setCaption(c.caption ?? '')
    setError(null)
    window.scrollTo(0, 0)
  }

  function startNew() {
    setCurrent(null)
    setSelected([])
    setCaption('')
    setError(null)
  }

  async function save(status?: string) {
    setBusy(true)
    setError(null)
    try {
      let c: Collection
      if (current) {
        c = await api.patchCollection(current.id, {
          media_ids: selected,
          caption,
          ...(status ? { status } : {}),
        })
      } else {
        c = await api.createCollection({ platform, media_ids: selected, caption })
        if (status) c = await api.patchCollection(c.id, { status })
      }
      setCurrent(c)
      setSaved(await api.collections())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="top">
        <a className="back" href="#/">
          &lsaquo; Events
        </a>
        {current && (
          <button className="ghost" onClick={startNew}>
            New post
          </button>
        )}
      </header>

      <h1>Collections</h1>
      <p className="muted small">
        One post built from the best photos across many events. Photo 1 sets the crop for the whole
        carousel on Instagram.
      </p>

      <div className="chips">
        {PERIODS.map((p) => (
          <button
            key={p.key}
            className={p.key === periodKey ? 'chip on' : 'chip'}
            onClick={() => setPeriodKey(p.key)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <p className="muted small">{period.note}</p>

      <div className="chips">
        {(['instagram', 'x'] as const).map((p) => (
          <button
            key={p}
            className={p === platform ? 'chip on' : 'chip'}
            disabled={!!current}
            onClick={() => {
              setPlatform(p)
              setSelected((cur) => cur.slice(0, LIMIT[p] ?? 10))
            }}
          >
            {p === 'x' ? 'X' : 'Instagram'}
          </button>
        ))}
        <span className="muted small">
          {selected.length}/{limit} picked{current ? ` - ${current.status}` : ' - unsaved'}
        </span>
      </div>

      {error && <p className="error">{error}</p>}

      {selected.length > 0 && (
        <>
          <h2 className="small">In the post, in order</h2>
          <p className="muted small">
            Drag to reorder. Photo 1 sets the crop for all of them. The x removes one.
          </p>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={selected} strategy={rectSortingStrategy}>
              <ul className="grid">
                {selected.map((id, i) => (
                  <Picked
                    key={id}
                    id={id}
                    n={i + 1}
                    photo={byId.get(id)}
                    locked={approved}
                    onRemove={() => toggle(id)}
                  />
                ))}
              </ul>
            </SortableContext>
          </DndContext>

          <label className="small" htmlFor="cap">
            Caption
          </label>
          <textarea
            id="cap"
            rows={5}
            value={caption}
            disabled={approved}
            onChange={(e) => setCaption(e.target.value)}
            placeholder="Write it in your own words."
          />
          <div className="row status">
            <button className="ghost" disabled={busy || approved} onClick={() => save()}>
              Save draft
            </button>
            {approved ? (
              <button className="ghost" disabled={busy} onClick={() => save('draft')}>
                Undo approval
              </button>
            ) : (
              <button
                className="primary"
                disabled={busy || !caption.trim() || selected.length === 0}
                onClick={() => save('approved')}
              >
                Approve
              </button>
            )}
          </div>
        </>
      )}

      {saved.length > 0 && (
        <>
          <h2 className="small">Saved collections</h2>
          <ul className="events">
            {saved.map((c) => (
              <li key={c.id}>
                <button className="card" onClick={() => open(c)}>
                  <span className="title">
                    {c.platform === 'x' ? 'X' : 'Instagram'} - {c.media_ids.length} photos
                  </span>
                  <span className="meta small muted">
                    {c.status}
                    {c.caption ? ` - ${c.caption.slice(0, 60)}` : ' - no caption yet'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <h2 className="small">
        {period.label} - {photos ? photos.length : '...'} photos, best first
      </h2>
      {!photos ? (
        <p className="muted">Loading...</p>
      ) : (
        <ul className="grid">
          {photos.map((p) => {
            const at = selected.indexOf(p.media_id)
            return (
              <li key={p.media_id} className={at >= 0 ? 'tile picked' : 'tile'}>
                <div className="frame" onClick={() => toggle(p.media_id)}>
                  {p.thumb && <img src={p.thumb} alt="" loading="lazy" />}
                  {at >= 0 && <span className="n">{at + 1}</span>}
                  {p.score !== null && <span className="score">{p.score.toFixed(1)}</span>}
                </div>
                <div className="caption">
                  <span className="small muted">
                    {p.pillar ? `${PILLAR_EMOJI[p.pillar] ?? p.pillar} - ` : ''}
                    {p.event_label}
                  </span>
                </div>
              </li>
            )
          })}
        </ul>
      )}

    </div>
  )
}
