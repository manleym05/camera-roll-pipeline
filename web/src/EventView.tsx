import { useEffect, useRef, useState } from 'react'
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
import { api, dateRange, type Candidate, type EventDetail } from './api'
import Captions from './Captions'

// Cosmetic only, keyed by pillar name. Unlisted pillars just render without a glyph.
const PILLAR_EMOJI: Record<string, string> = { work: '💼', tech: '💻', fitness: '🏋️', travel: '✈️' }

export default function EventView({ id }: { id: string }) {
  const [ev, setEv] = useState<EventDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const saveSeq = useRef(0)

  useEffect(() => {
    api.event(id).then((e) => { setEv(e); setNote(e.note ?? '') }, (e) => setError(e.message))
  }, [id])

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    // Press-and-hold to drag, so a normal swipe still scrolls the page.
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  async function save(fn: () => Promise<EventDetail>) {
    // Saves can overlap (a caption blur, then Approve); only the newest response may replace the page.
    const n = ++saveSeq.current
    setSaving(true)
    setError(null)
    try {
      const next = await fn()
      if (n === saveSeq.current) setEv(next)
      return next
    } catch (e) {
      setError((e as Error).message)
    } finally {
      if (n === saveSeq.current) setSaving(false)
    }
  }

  function onDragEnd({ active, over }: DragEndEvent) {
    if (!ev || !over || active.id === over.id) return
    const from = ev.items.findIndex((c) => c.id === active.id)
    const to = ev.items.findIndex((c) => c.id === over.id)
    const items = arrayMove(ev.items, from, to)
    setEv({ ...ev, items })
    save(() => api.reorder(ev.id, items.map((c) => c.id)))
  }

  async function toggleNeverPost(c: Candidate) {
    if (!ev) return
    setError(null)
    try {
      const updated = await api.neverPost(c.id, !c.never_post)
      setEv({ ...ev, items: ev.items.map((x) => (x.id === c.id ? updated : x)) })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!ev) return <div className="page">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</div>

  const setStatus = (status: string) => save(() => api.patchEvent(ev.id, { status }))
  const postable = ev.items.filter((c) => !c.never_post).length

  return (
    <div className="page">
      <header className="top">
        <a className="back" href="#/">‹ Events</a>
        <span className="muted small">{saving ? 'Saving…' : ''}</span>
      </header>

      {editing ? (
        <form
          className="rename"
          onSubmit={(e) => {
            e.preventDefault()
            save(() => api.patchEvent(ev.id, { label: name })).then(() => setEditing(false))
          }}
        >
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          <button className="primary" disabled={!name.trim()}>Save</button>
          <button type="button" className="ghost" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <h1 className="event-title" onClick={() => { setName(ev.label); setEditing(true) }}>
          {ev.label} <span className="edit">✎</span>
        </h1>
      )}
      <p className="muted small">
        {dateRange(ev.start_at, ev.end_at)} · {ev.kind} · {ev.media_count} media ·{' '}
        {Object.entries(ev.owners).map(([o, n]) => `${o} ${n}`).join(', ')}
        {ev.label_source === 'manual' && ev.ai_label && ev.ai_label !== ev.label ? ` · Claude suggested “${ev.ai_label}”` : ''}
      </p>
      {ev.summary && <p className="summary">{ev.summary}</p>}

      {ev.were_you_there && ev.status === 'pending' && (
        <div className="banner">
          <span>None of these photos are from your phone. Were you there?</span>
          <div className="row">
            <button className="primary" onClick={() => setStatus('confirmed')}>Yes</button>
            <button className="ghost" onClick={() => setStatus('dropped')}>No, drop</button>
          </div>
        </div>
      )}

      <div className="row status">
        <button className={ev.status === 'confirmed' ? 'primary' : 'ghost'} onClick={() => setStatus(ev.status === 'confirmed' ? 'pending' : 'confirmed')}>
          {ev.status === 'confirmed' ? '✓ Confirmed' : 'Confirm'}
        </button>
        <button className={ev.status === 'dropped' ? 'danger' : 'ghost'} onClick={() => setStatus(ev.status === 'dropped' ? 'pending' : 'dropped')}>
          {ev.status === 'dropped' ? 'Dropped (undo)' : 'Drop'}
        </button>
      </div>

      <label className="note">
        <span className="muted small">Note</span>
        <input
          value={note}
          placeholder="What was this? (one line)"
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => note !== (ev.note ?? '') && save(() => api.patchEvent(ev.id, { note }))}
        />
      </label>

      {error && <p className="error">{error}</p>}

      <Captions posts={ev.posts} save={(postId, body) => save(() => api.patchPost(postId, body))} />

      <h2>
        Photos <span className="muted small">{postable} postable · hold and drag to reorder</span>
      </h2>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={ev.items.map((c) => c.id)} strategy={rectSortingStrategy}>
          <ol className="grid">
            {ev.items.map((c, i) => (
              <Tile
                key={c.id}
                c={c}
                n={i + 1}
                open={open === c.id}
                onOpen={() => setOpen(open === c.id ? null : c.id)}
                onToggle={() => toggleNeverPost(c)}
              />
            ))}
          </ol>
        </SortableContext>
      </DndContext>
    </div>
  )
}

function Tile({ c, n, open, onOpen, onToggle }: { c: Candidate; n: number; open: boolean; onOpen: () => void; onToggle: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: c.id })
  const style = { transform: CSS.Transform.toString(transform), transition, zIndex: isDragging ? 5 : undefined }
  const pillar = typeof c.subscores.pillar === 'string' ? c.subscores.pillar : ''
  return (
    <li ref={setNodeRef} style={style} className={`tile${c.never_post ? ' blocked' : ''}${isDragging ? ' dragging' : ''}${open ? ' open' : ''}`}>
      <div className="frame" {...attributes} {...listeners} onClick={onOpen}>
        {c.thumb && <img src={c.thumb} alt="" loading="lazy" draggable={false} />}
        <span className="n">{n}</span>
        {c.score !== null && <span className="score">{c.score.toFixed(1)}</span>}
        {c.kind !== 'photo' && <span className="kind">{c.kind === 'video' ? '▶' : 'LIVE'}</span>}
        {c.never_post && <span className="stamp">never post</span>}
      </div>
      <div className="caption">
        <span className="small">
          {PILLAR_EMOJI[pillar] ?? ''} {c.credit ? `📷 ${c.credit}` : ''}
        </span>
        <button className={c.never_post ? 'toggle on' : 'toggle'} onClick={onToggle} title="Never post">
          {c.never_post ? '🚫' : '⊘'}
        </button>
      </div>
      {open && (
        <div className="why small">
          {c.never_post && c.never_post_reason && <p className="error">Never post: {c.never_post_reason}</p>}
          <p>{c.rationale}</p>
          <p className="muted">
            {(['technical', 'owner', 'story', 'pillar_fit', 'variety'] as const)
              .map((k) => `${k.replace('_', ' ')} ${c.subscores[k] ?? '–'}`)
              .join(' · ')}
          </p>
        </div>
      )}
    </li>
  )
}
