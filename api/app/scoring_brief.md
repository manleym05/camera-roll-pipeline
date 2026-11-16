# Scoring brief for Brand Engine subagents

Folder: `<repo>\data\scoring`

For each event packet you're given (`<event_id>.json`):

1. Read the scoring guide at `<repo>\api\app\rubric.md` and follow it exactly.
   That file is not in the repo — you write it. See 'Configuring it for you' in the README.
2. Read the packet. Each entry in `candidates` has a `ref` (c1, c2, ...), time, phone owner, kind, face count, CLIP tags and a `thumb` path.
3. View **every** thumbnail with the Read tool (they're JPEGs, ~576x768). Don't skip any, and don't guess from tags.
4. Write `<event_id>.result.json` in the same folder: one JSON object matching `schema.json`:
   `{"event_name", "summary", "carousel": [refs, max 10], "candidates": [{"ref", "technical", "owner", "story", "pillar", "pillar_fit", "variety", "never_post", "never_post_reason", "rationale"}]}`
   - Include every ref from the packet exactly once. Scores are numbers 0-10. `pillar` is one of work, tech, fitness, travel.
   - `never_post_reason` is null unless `never_post` is true.
5. Check the file parses: `python -c "import json,sys;d=json.load(open(sys.argv[1]));print(len(d['candidates']))" <file>`, and the count must match the packet.

Only write the result files. Don't touch any other file, run no other commands, and don't describe private images in more detail than the reason needs.

Reply with one line per event: event_id, proposed name, number scored, number flagged never_post.
