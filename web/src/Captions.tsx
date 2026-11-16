import { useEffect, useState } from 'react'
import type { CaptionStyle, EventDetail, Post } from './api'

const PLATFORM_LABEL: Record<Post['platform'], string> = { instagram: 'Instagram', x: 'X' }
const STYLE_LABEL: Record<CaptionStyle, string> = { plain: 'Plain', nerd: 'Nerd detail', self_own: 'Self-own' }
const LIMIT: Record<Post['platform'], number> = { instagram: 2200, x: 280 }

type Save = (postId: string, body: { caption?: string; pick?: CaptionStyle; status?: string }) => Promise<EventDetail | undefined>

export default function Captions({ posts, save }: { posts: Post[]; save: Save }) {
  const [platform, setPlatform] = useState<Post['platform']>('instagram')
  const post = posts.find((p) => p.platform === platform)

  if (posts.length === 0) {
    return (
      <>
        <h2>Captions</h2>
        <p className="muted small">No drafts yet. Run captions export, write them, then captions import.</p>
      </>
    )
  }

  return (
    <>
      <h2>
        Captions{' '}
        <span className="muted small">
          {posts.filter((p) => p.status === 'approved').length}/{posts.length} approved
        </span>
      </h2>
      <nav className="chips">
        {posts.map((p) => (
          <button key={p.platform} className={p.platform === platform ? 'chip on' : 'chip'} onClick={() => setPlatform(p.platform)}>
            {PLATFORM_LABEL[p.platform]} {p.status === 'approved' ? '✓' : ''}
          </button>
        ))}
      </nav>
      {post && <PostEditor key={post.id} post={post} save={save} />}
    </>
  )
}

function PostEditor({ post, save }: { post: Post; save: Save }) {
  const [text, setText] = useState(post.caption ?? '')
  useEffect(() => setText(post.caption ?? ''), [post.caption])

  const approved = post.status === 'approved'
  const limit = LIMIT[post.platform]
  const commit = () => {
    if (text.trim() !== (post.caption ?? '')) save(post.id, { caption: text })
  }

  return (
    <div className="post">
      <ul className="options">
        {post.options.map((o) => {
          const on = post.picked === o.style
          return (
            <li key={o.style}>
              <button className={on ? 'option on' : 'option'} disabled={approved} onClick={() => save(post.id, { pick: o.style })}>
                <span className="muted small">{STYLE_LABEL[o.style]}</span>
                <span>{o.text}</span>
              </button>
            </li>
          )
        })}
      </ul>

      <label className="note">
        <span className="muted small">
          Caption {post.picked && text !== post.options.find((o) => o.style === post.picked)?.text ? '(edited)' : ''}
        </span>
        <textarea
          value={text}
          rows={post.platform === 'x' ? 3 : 4}
          disabled={approved}
          placeholder="Tap an option above, or write your own"
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
        />
        <span className={`small ${text.length > limit ? 'error' : 'muted'}`}>
          {text.length}/{limit}
        </span>
      </label>

      <div className="row status">
        {approved ? (
          <button className="ghost" onClick={() => save(post.id, { status: 'draft' })}>
            ✓ Approved with {post.media_ids.length} photos (undo)
          </button>
        ) : (
          <button
            className="primary"
            disabled={!text.trim() || text.length > limit}
            onClick={() => save(post.id, { caption: text, status: 'approved' })}
          >
            Approve
          </button>
        )}
      </div>
      {approved && <p className="muted small">The photos are locked in at approval. Undo to reorder them.</p>}
    </div>
  )
}
