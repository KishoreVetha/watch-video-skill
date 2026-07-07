# watch-video — a no-API video analysis skill for Codex & Claude Code

Let your coding agent **watch a video** — YouTube link, direct video URL, or any
local video file — by extracting timestamped frames and looking at them,
instead of relying on a transcript API.

**No API key. No credits. No transcript service.** Everything runs locally with
`yt-dlp` + `ffmpeg`.

## Why frames instead of transcripts?

Transcripts only tell you what was *said*. Frames show what was *shown*:
demos, dashboards, charts, UI walkthroughs, on-screen code and numbers, and
videos that have no captions at all. When captions do exist (e.g. YouTube),
this skill grabs them too — for free, via `yt-dlp` — so you get both.

## What it does

1. Downloads the video with `yt-dlp` (or takes a local file path — no network needed).
2. Extracts frames with `ffmpeg` at an auto-scaled rate: short videos are
   sampled densely, long ones are capped (default budget: 100 frames).
3. **Burns the video timestamp into the corner of every frame**, so each image
   is self-describing.
4. Optionally tiles frames into 3x4 **contact sheets** (12 frames per image)
   for agents/chats where attaching many images is impractical.
5. Pulls the platform's own captions when available (free, no API).
6. Prints a JSON manifest (frames + timestamps + captions path) for the agent.

## Install

### Codex (CLI or desktop app)

Copy the `watch-video` folder into your user skills directory:

```powershell
# Windows (PowerShell)
git clone https://github.com/KishoreVetha/watch-video-skill
New-Item -ItemType Directory -Force "$env:USERPROFILE\.agents\skills" | Out-Null
Copy-Item -Recurse watch-video-skill\watch-video "$env:USERPROFILE\.agents\skills\"
```

```bash
# macOS / Linux
git clone https://github.com/KishoreVetha/watch-video-skill
mkdir -p ~/.agents/skills
cp -r watch-video-skill/watch-video ~/.agents/skills/
```

Restart Codex so the skill is discovered. Then paste a video URL or file path
and ask for a summary — or invoke it explicitly with `$watch-video`.

Or simply give Codex the repo URL and this prompt:
> take a look at this repo and install the watch-video skill here: `https://github.com/KishoreVetha/watch-video-skill`

### Claude Code

Copy the same folder to `~/.claude/skills/` instead — the script is
agent-agnostic.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) (includes ffprobe)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — `python -m pip install -U yt-dlp` (only needed for URLs)
- Node.js or Deno (yt-dlp needs a JS runtime for YouTube extraction)

## Usage examples

```bash
# whole video
python watch-video/scripts/watch_video.py "https://www.youtube.com/watch?v=..."

# local file — works fully offline
python watch-video/scripts/watch_video.py "C:\videos\demo.mp4"

# zoom into 1:20–2:05 with hi-res frames (legible on-screen text)
python watch-video/scripts/watch_video.py "demo.mp4" --start 1:20 --end 2:05 --resolution 1024

# contact sheets: 12 timestamped frames per image
python watch-video/scripts/watch_video.py "demo.mp4" --sheets
```

## Corporate network notes (the honest section)

- **YouTube bot-check** ("Sign in to confirm you're not a bot"): YouTube flags
  many corporate/VPN egress IPs. Disconnect the VPN, or export your YouTube
  cookies once (browser extension "Get cookies.txt LOCALLY") and pass
  `--cookies cookies.txt` or set `WATCH_COOKIES`.
- **TLS-inspecting proxies**: the script auto-retries with certificate
  verification off (with a warning) when it detects the corporate-proxy
  certificate error.
- **Local files and direct video URLs need none of the above.**

## Credits

Frame-extraction approach inspired by
[bradautomates/claude-video](https://github.com/bradautomates/claude-video).

MIT licensed.
