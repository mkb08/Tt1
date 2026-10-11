# Faceless YouTube Autopilot

An automated, faceless YouTube channel that runs on GitHub Actions with free tools, with Claude as its writer and strategist.

Every run:

1. **Research.** Pulls recent systematic reviews, meta-analyses and randomised trials from PubMed, plus what people are searching (Google Trends) and watching (YouTube).
2. **Write.** Claude picks the strongest topic not yet covered, writes a hook-first script that cites its sources, then a second Claude pass fact-checks every claim against the abstracts.
3. **Produce.** A free neural voice narrates it, stock clips are cut every few seconds, word-by-word captions are burned in, the audio is loudness-normalised and a thumbnail is generated.
4. **Publish.** Uploads to YouTube as private, scheduled to go public 6 hours later, so you can watch it first.
5. **Learn.** Once a week, Claude reads your channel stats and writes `data/insights.md`. The writer reads that file before every script.

The default niche is **practical health tips backed by the latest medical research**. Change it in `config/channel.yaml`.

```
            ┌──────────── GitHub Actions (cron, 2x/day) ─────────────┐
 PubMed ───►│ research ─► Claude writes ─► Claude fact-checks ─►      │
 Trends ───►│   validate (limits + PMIDs exist on PubMed)             │
 YouTube ──►│ edge-tts voice ─► Pexels/Pixabay b-roll ─► FFmpeg       │──► YouTube
            │   captions + thumbnail ─► upload ─► commit history      │
            └─────────────────────────────────────────────────────────┘
 You ──► "@claude make a video about X" in an issue ─► PR ─► merge ─► published
```

## What it costs

| Part | Tool | Cost |
|---|---|---|
| Scheduling and rendering | GitHub Actions | Free. Private repos get 2,000 min/month; a Short takes about 5 to 8 minutes |
| Research | PubMed E-utilities, Google Trends RSS, YouTube Data API | Free |
| Voice | [edge-tts](https://github.com/rany2/edge-tts) (Microsoft Edge neural voices) | Free, no key |
| Footage | Pexels and Pixabay APIs | Free keys |
| Editing | FFmpeg, Pillow | Free |
| Upload and analytics | YouTube Data API v3 | Free |
| Script and fact-check | Claude | **Option A:** your existing Claude Pro/Max plan through the Claude Code GitHub Action, no extra charge. **Option B:** an Anthropic API key, pay per use, typically a few tens of cents per video including the fact-check |

Everything except Claude is free. Claude is the one part you either already pay for (Pro/Max) or pay per video (API).

## Setup (about 30 minutes)

### 1. Connect Claude (pick one)

- **Option A, Pro/Max subscription:** install [Claude Code](https://code.claude.com), run `claude setup-token`, and save the token as a repository secret named `CLAUDE_CODE_OAUTH_TOKEN`. Install the Claude GitHub App on this repo (run `/install-github-app` inside Claude Code, or go to https://github.com/apps/claude).
- **Option B, API key:** create a key at https://platform.claude.com and save it as `ANTHROPIC_API_KEY`. Scripts are written by `autopilot/writer.py`, using `claude-opus-5-5` with an independent fact-check call. Install the Claude GitHub App as well if you want `@claude` in issues.

If both are set, the scheduled pipeline uses the API key.

### 2. Free stock-footage keys

- Pexels: https://www.pexels.com/api/ → `PEXELS_API_KEY`
- Pixabay: https://pixabay.com/api/docs/ → `PIXABAY_API_KEY`

Without keys, videos use generated title cards. They work, but real footage performs much better.

### 3. YouTube upload access

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable **YouTube Data API v3**.
2. Configure the **OAuth consent screen** (External, add yourself as a test user), then click **Publish app** so it shows "In production". In Testing mode, refresh tokens expire after 7 days. You can keep using an unverified app for your own channel; you will just click past a warning once.
3. Create **Credentials → OAuth client ID → Desktop app** and download the JSON.
4. On your computer, run:
   ```bash
   pip install google-auth-oauthlib
   python scripts/get_youtube_token.py client_secret.json
   ```
   Sign in and choose the channel. Save the three printed values as `YT_CLIENT_ID`, `YT_CLIENT_SECRET` and `YT_REFRESH_TOKEN`.

### 4. Secrets reference

Add these under **Settings → Secrets and variables → Actions**.

| Secret | Required | Purpose |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | yes | Claude writes and fact-checks |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | to upload | Without them, videos are rendered and kept as workflow artifacts |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | recommended | Stock footage |
| `NCBI_API_KEY` | optional | Faster PubMed (10 requests/s instead of 3) |
| `YOUTUBE_API_KEY` | optional | Read-only trending data if you skip OAuth |

### 5. Make it yours

- `config/channel.yaml`: channel name, niche, audience, voice (`edge-tts --list-voices`), Short or long-form, research queries, accuracy rules, publish delay.
- `.github/workflows/autopilot.yml`: the cron schedule (UTC).
- `assets/music/`: drop royalty-free `.mp3` tracks (YouTube Audio Library); music is ducked under the voice automatically.

### 6. Test, then go live

**Actions → Autopilot → Run workflow**, tick **dry_run**, and download the video from the run's artifacts. A dry run leaves its script in `content/queue/`, so the next scheduled run publishes it; delete the file there to discard it. When you are happy with the output, the schedule takes over.

## Talking to Claude

Claude is connected to the repo through the Claude GitHub App:

- **Request a video:** open an issue titled `@claude make a video about creatine and memory`. Claude researches it, writes the script, validates its citations and opens a PR. Merging the PR publishes the video.
- **Change the channel:** `@claude switch to a female voice, make Shorts 40 seconds, and add "hydration" to the research topics` opens a PR with the config change.
- **Review a script:** comment `@claude` on a script PR, for example "make the hook punchier".
- **Weekly strategy:** the analytics workflow has Claude turn your stats into `data/insights.md`, and every new script reads it.

Claude's instructions live in `CLAUDE.md`.

## Getting traffic

No tool can guarantee views. These defaults follow what tends to work for Shorts:

- **Hook in the first sentence.** The writer is told to open with the most surprising accurate finding, with no intro.
- **Retention:** cuts at least every 4 seconds, big word-by-word captions, a key-phrase overlay per scene, 35 to 60 seconds long.
- **Consistency:** 2 Shorts a day in one niche. Change the cron to post more or less.
- **Feedback loop:** weekly insights steer topics, hooks and titles toward what your audience actually watches.
- **Then go long:** when a Short topic takes off, set `format.type: long` and request a deep dive on it.

### Monetization and YouTube's rules

- **Inauthentic content.** YouTube's Partner Program excludes mass-produced, repetitive content. Its July 2025 "inauthentic content" policy targets low-effort stock footage with generic AI voiceover. Faceless channels are allowed, but each video must add real value. This pipeline's value is original research synthesis with citations, accuracy and a consistent voice. To strengthen it further, watch videos during the publish window, record your own narration for some, and vary formats. YouTube reviews the channel as a whole.
- **Partner Program thresholds** (check YouTube's current page): commonly 1,000 subscribers plus 4,000 public watch hours in 12 months, or 1,000 subscribers plus 10 million Shorts views in 90 days.
- **AI disclosure:** set `youtube.contains_synthetic_media: true` if you add realistic AI-generated people, places or events. A TTS voice over real stock footage doesn't normally need the label. Descriptions already say the narration is AI-generated.
- **Music:** only use tracks you have rights to; Content ID claims can block monetization.

## Health content guardrails

Health is a high-trust niche. YouTube enforces a medical misinformation policy, and viewers act on what they hear. The pipeline is built for accuracy first:

- Evidence comes only from PubMed: systematic reviews, meta-analyses and RCTs from the last 180 days. Retracted papers are excluded.
- Every factual scene must cite a PMID from that research. The validator confirms each PMID exists on PubMed and that its title matches, so invented citations are blocked.
- A separate Claude pass fact-checks each claim against the abstracts: association versus causation, study population, certainty and over-promising titles. A script that fails twice is rejected, not published.
- Rules forbid dosing advice, medication changes, diagnosis and cure claims. Every description ends with a not-medical-advice disclaimer and the full source list.
- Videos go public 6 hours after upload by default, which gives you a review window.

## Run it locally

```bash
pip install -r requirements.txt            # also needs ffmpeg on PATH
python -m autopilot research               # -> work/research.json
python -m autopilot write                  # needs ANTHROPIC_API_KEY
python -m autopilot produce                # renders the next queued script
python -m autopilot produce examples/sample-script.json --offline   # no keys, no network
python -m autopilot upload                 # needs the YT_* variables
python -m pytest -q
```

## Project layout

```
autopilot/            research, writer (Claude), tts, visuals, captions, render, thumbnail, youtube, analytics
config/channel.yaml   everything you would want to change
content/queue/        scripts waiting to be produced (Claude writes here)
content/published/    scripts already uploaded; content/rejected/ holds failed scripts with reasons
data/                 history.json (what was published), performance.json, insights.md
examples/             a complete sample script
.github/workflows/    autopilot (2x/day), claude (@claude), analytics (weekly), ci
```

## Troubleshooting

- **Uploads stop after about a week:** the OAuth consent screen is still in Testing. Publish it and create a new refresh token.
- **`quotaExceeded`:** the YouTube API quota resets daily (midnight Pacific). Uploads have their own daily allowance in the current quota table.
- **Videos stay private:** with `publish_delay_hours > 0`, that is expected until the scheduled time. Google's `videos.insert` documentation (updated 2026-10-08) says uploads from unverified API projects are no longer locked to private. If yours still are, request a [YouTube API audit](https://support.google.com/youtube/contact/yt_api_form).
- **Thumbnail not set:** custom thumbnails need a phone-verified channel.
- **edge-tts errors:** these are usually transient and retried automatically. edge-tts is an unofficial client, so if it breaks for good, swap in [Piper](https://github.com/OHF-Voice/piper1-gpl) or Kokoro in `autopilot/tts.py`.
- **A script landed in `content/rejected/`:** the `.issues.txt` file next to it says why.
- **Schedules stopped:** GitHub pauses cron workflows in public repos after 60 days with no activity. Each autopilot run commits its history, which counts as activity.
