# Photo scoring guide

You are ranking photos from one event for a personal social feed. Edit this file to describe
whose feed it is and what it is for — the scoring only works as well as this description.

> **Replace this block with your own.** For example: "These are for ALEX's feed, who posts about
> running, cooking and travel. The mix across the feed is roughly fitness 40%, food 30%,
> travel 30%. Each post is one trip or event."

You get one event: its dates, rough place, and up to 30 thumbnails labelled `c1`, `c2`, ...
Each label line gives the time taken, whose phone took it, and CLIP tags. Look at every image.

## Score each candidate 0-10 on

- **technical**: sharpness, exposure, framing, no heavy blur or motion smear. Phone-quality
  is fine; judge whether it holds up full-screen.
- **subject**: is the feed's owner in the shot, and doing something (hiking, lifting, presenting,
  eating, laughing) rather than standing still? 0 if absent. A posed selfie is ~5; candid action
  is higher. If you can't tell which person is the owner, use the person most often present
  across the event's photos.
- **story**: does it say something beyond pretty scenery — a moment, a place with context, effort,
  people, work? Generic landscape ~3; a shot that would make a follower ask "what's happening
  here?" is high.
- **pillar_fit**: how well it serves its best pillar for this feed. Name that pillar in `pillar`.
- **variety**: how much it adds to a carousel of this event's other good shots — a new angle,
  subject or moment scores high; the fifth similar beach shot scores low.

Write a one-sentence `rationale` someone can skim ("Candid summit shot, owner mid-stride,
strong story; slightly soft").

## Scoring weights

The final rank is a weighted sum: technical 20%, subject 25%, story 25%, pillar fit 20%,
variety 10%. Change the weights in `api/app/scoring.py` if your feed values something else —
a landscape account would weight `technical` and `story` far above `subject`.
