# CLAUDE.md

This repository runs a faceless YouTube channel. Claude is its writer and strategist; Python and FFmpeg do the production. The channel's niche, audience, voice and rules are in `config/channel.yaml`. Read it before writing anything.

## How the pipeline fits together

```
research (PubMed, Google Trends, YouTube) -> work/research.json
Claude writes a script                     -> content/queue/<date>-<slug>.json
python -m autopilot produce                -> work/<slug>/final.mp4 (+ thumbnail, metadata)
python -m autopilot upload                 -> YouTube; script moves to content/published/
weekly analytics                           -> data/performance.json -> data/insights.md
```

The script format is the `Script` model in `autopilot/schema.py`. `examples/sample-script.json` is a complete, valid example.

## Procedure: write the next video

1. Read `config/channel.yaml` (especially `channel`, `format` and `writer.rules`) and `work/research.json`. If `work/research.json` is missing, run `python -m autopilot research` (add `--topic "..."` for a requested topic).
2. Choose the topic. If `requested_topic` is set, use it. Otherwise pick the evidence item with the strongest design (systematic reviews, meta-analyses and large RCTs first) and the most surprising, useful and accurate hook. Skip anything in `recent_topics`. Use `insights` (what has worked on this channel) and the trend `signals` to choose the angle and title.
3. Write the script to `content/queue/<YYYY-MM-DD>-<topic-slug>.json`:
   - Narration is spoken by a TTS voice: plain words, short sentences, numbers written as they are spoken. Open with the hook; no greetings.
   - Length: about `format.target_seconds` x 2.6 words in total. Shorts use 6-10 scenes of 1-2 sentences.
   - `visual_query`: 2-4 concrete, filmable stock-footage words (people, objects, places). No charts, text, logos or named people.
   - `source_ids` on every scene that makes a factual claim, using evidence ids exactly as in the research file (e.g. `PMID:42716561`). In `sources`, copy the evidence title exactly and describe the study type, population and size in `note`.
   - Title under 70 characters, curiosity-driven, and fully delivered by the video.
4. Fact-check yourself before finishing. Re-read every claim against the cited abstract. Fix anything the abstract does not support, any association stated as causation, missing population or certainty context, and any title that over-promises.
5. Run `python -m autopilot validate content/queue/<file>.json` and fix every error until it prints `OK`. The validator checks the format and YouTube limits, and confirms that every PMID exists on PubMed with a matching title.

## Procedure: a video requested in an issue

Same as above, with the issue's topic: run `python -m autopilot research --topic "<topic>"` first. Commit only the new file in `content/queue/` and open a pull request. In the PR description, give the title, the hook and the sources. Merging the PR publishes the video.

## Procedure: weekly insights

Read `data/performance.json`. Write `data/insights.md` in under 300 words: which topics, hooks and title patterns earn the most views per day, what is not working, and five concrete recommendations for the next videos. Base every point on the numbers. If there is too little data, say so and suggest what to test next.

## Accuracy rules (health content)

These come from `config/channel.yaml` and override style:

- Only state what the cited sources support. Name the study type and who was studied.
- Observational evidence is "linked to", never "causes".
- No dosing, no medication changes, no diagnosis. Point viewers to their clinician for anything personal.
- No cure claims, fear-mongering or miracle language.
- State uncertainty when the evidence is low-certainty, small, short, or in a narrow population.
- Never invent a study, number, quote or id.

## Working on the code

- Python 3.12, standard library first. Dependencies are in `requirements.txt`.
- Run `python -m pytest -q` after changes. `python -m autopilot produce examples/sample-script.json --offline` renders a test video with no network or keys.
- Keep secrets out of the repository; the code reads them from environment variables (GitHub Actions secrets).
