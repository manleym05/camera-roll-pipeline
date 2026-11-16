# camera-roll-pipeline

Turns a few thousand unsorted phone photos into a ranked shortlist worth posting.

Point it at your camera roll. It reads EXIF, pairs Live Photos with their stills, throws out
screenshots and documents, tags every image with CLIP, measures sharpness, finds faces, collapses
near-duplicate bursts, groups what's left into events by time and place, and gives you a phone
page to pick the keepers.

Nothing uploads anywhere you didn't configure. The heavy work runs on your own machine.

```
camera roll ──► agent (local)  ──►  API + Postgres/pgvector  ──►  review page
                EXIF, CLIP,          events, shortlists,           pick on your phone
                faces, dedupe        scoring, captions
```

## Why this exists

The photos worth posting are already on your phone; the problem is that there are 40,000 of them
and the good ones are buried between eleven near-identical shots of the same thing. Apple's
"Memories" picks for sentiment. This picks for a feed: is it sharp, is the subject actually doing
something, does it tell a story, and does it add anything the other nine shots from that day don't.

## How it works

**The agent** (`agent/`) runs where your photos are. For each file it pulls EXIF (including GPS),
pairs Live Photo stills with their video, drops screenshots and receipts, runs
[OpenCLIP](https://github.com/mlfoundations/open_clip) for tags and an embedding, scores sharpness
with a Laplacian variance, detects faces, generates a thumbnail, and decides which way up the
photo goes. It sends metadata and thumbnails to the API — never the originals.

**The API** (`api/`) is FastAPI over Postgres with [pgvector](https://github.com/pgvector/pgvector).
It collapses near-duplicates using cosine distance over CLIP embeddings (keeping the sharpest of
each cluster), groups media into events by time gap and distance, geofences your home so private
moments never enter the shortlist, and ranks each event's candidates.

**The review page** (`web/`) is a React app sized for a phone. Swipe an event, reorder a carousel,
pick a caption, mark it done.

**Scoring and captions** are deliberately decoupled from any particular model. The API exports a
packet per event, something writes a `<event_id>.result.json`, and an import step validates and
stores it. Today that something is Claude; it could be an API batch job or you. See
`api/app/scoring_brief.md` and `api/app/caption_brief.md`.

## Quick start

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Node, and Docker.

```bash
git clone https://github.com/manleym05/camera-roll-pipeline
cd camera-roll-pipeline
cp .env.example .env
```

Edit `.env`: set `PHOTOS_DIR` to your camera roll, and for a first run set `REVIEW_AUTH=none`
(the review page is then open, so keep the API on `127.0.0.1`).

```bash
docker compose up -d                        # Postgres + pgvector on :5433
cd api && uv sync && uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000
```

In another terminal:

```bash
cd agent && uv sync                         # CPU works; set CLIP_DEVICE=cuda if you have an NVIDIA GPU
uv run brand-agent scan --limit 200         # start small
uv run brand-agent scan --dry-run           # analyse only, send nothing
```

Then `cd web && npm install && npm run dev` and open the page.

A first full run on tens of thousands of photos takes hours — CLIP is the bottleneck. Results are
cached, so later runs only process new files.

## Configuring it for you

| What | Where |
|---|---|
| Whose feed this is, and what it's for | `api/app/rubric.md` — **you write this**, see below |
| How you sound in captions | `docs/voice.md` — **you write this**, start from `docs/voice.example.md` |
| Your content pillars | `PILLARS` in `.env`, plus `PILLAR_PROMPTS` in `agent/brand_agent/config.py` |
| Shortlisting bias between pillars | `PILLAR_WEIGHTS` in `.env` (empty = all equal) |
| Scoring weights | `WEIGHTS` in `api/app/scoring.py` |
| Home geofence (never postable) | `HOMES` in `.env` |
| A second person's camera roll | `OWNER_DIRS` in `.env`; photos keep their owner and carry a credit |
| Words banned from a given platform | `INSTAGRAM_BLOCKED_TERMS` / `X_BLOCKED_TERMS` |

### Writing your scoring rubric

`api/app/rubric.md` is not in the repo — it describes a specific person's feed, so shipping one
would only teach you to copy someone else's taste. Both it and `docs/voice.md` are gitignored by
default so a rubric about you doesn't end up in a public fork; drop those lines if you'd rather
track your own.

Write it as a prompt addressed to whatever model does the scoring. It needs to say whose feed
this is and what the feed is for, then define each score the importer expects. `scoring.py`
validates a `CandidateScore` per photo, so the rubric must explain all of:

| Field | What you're asking the model to judge |
|---|---|
| `technical` | 0–10 sharpness, exposure, framing |
| `owner` | 0–10 is the feed's owner in it, and doing something |
| `story` | 0–10 does it say anything beyond pretty scenery |
| `pillar` | which of your configured `PILLARS` it serves |
| `pillar_fit` | 0–10 how well it serves that pillar |
| `variety` | 0–10 what it adds that the event's other good shots don't |
| `never_post` | a hard veto, with `never_post_reason` |
| `rationale` | one sentence you can skim |

The final rank is a weighted sum — technical 20%, owner 25%, story 25%, pillar fit 20%,
variety 10% — set in `WEIGHTS` in `scoring.py`. A landscape account would weight `technical` and
`story` far above `owner`.

### Pillars

A pillar is one of the few things your feed is *about*. They default to `work`, `tech`, `fitness`
and `travel`, but they are only a default — set `PILLARS` to whatever yours are.

Changing them touches two places. `PILLARS` in `.env` is the list the scorer is given and
validated against. `PILLAR_PROMPTS` in `agent/brand_agent/config.py` is how CLIP recognises each
one in a photo, so each entry has to describe what the pillar *looks* like — "a photo of someone
cooking in a home kitchen", not "cooking". Keys in the two must match.

`PILLAR_WEIGHTS` optionally biases shortlisting toward some pillars; leave it empty and they
all count equally.

`api/app/scoring_brief.md` is the instruction sheet handed to the scorer alongside each packet;
it points at your rubric.

## Deploying the review page

Optional, and the fiddliest part. The page runs as a Cloudflare Worker that proxies `/api` back to
wherever the API lives and serves thumbnails from R2, with Cloudflare Access in front so only your
email can open it. `web/README.md` has the details. For local use you can skip all of it.

## Tests

```bash
cd api && uv run pytest -q     # needs docker compose up
cd agent && uv run pytest -q
```

## Notes

- **Windows:** keep the `tzdata` dependency (no system tz database) and OpenCV below 5
  (Haar cascades were removed in 5).
- **Originals never leave your machine.** The agent sends metadata and thumbnails. When you export
  for upload, `exif.strip_location()` removes GPS first.
- This is a personal project published because the pipeline is reusable. No support promise.

## License

MIT — see [LICENSE](LICENSE).
