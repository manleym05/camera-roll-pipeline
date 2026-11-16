# Caption brief

Folder: `<repo>\data\captions` (the path you're given). The voice guide is `<repo>\docs\voice.md`.

Write your own `docs/voice.md` before using this — `docs/voice.example.md` shows the shape.
Captions are only as good as that file; without it you get generic social-media filler.

For each event packet you're given (`<event_id>.json`):

1. Read `docs/voice.md` in full and follow it exactly.
2. Read the packet: the event's label, summary, the owner's note (if any), dates, who took the
   photos, and the top `photos` with the model's rationale for each.
3. View the thumbnails with the Read tool (JPEGs, ~576x768) so the captions match what's
   actually in the photos.
4. Write `<event_id>.result.json` in the same folder, one JSON object:
   `{"instagram": [{"style": "plain", "text": ...}, {"style": "nerd", "text": ...}, {"style": "self_own", "text": ...}], "x": [same three styles]}`
   - **plain:** what happened, one line, earnest.
   - **nerd:** the same moment with one specific, factual detail. Only use details that are in
     the packet or the photos. If there's no real detail to reach for, keep it light and true
     rather than invent one.
   - **self_own:** a short honest or self-deprecating angle. At most one per batch of events.

Validation on import rejects em and en dashes, captions over the platform limit, and any term
listed in `INSTAGRAM_BLOCKED_TERMS` / `X_BLOCKED_TERMS`. Import never overwrites a caption the
owner picked or wrote.
