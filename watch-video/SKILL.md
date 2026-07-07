---
name: watch-video
description: Watch and analyze any video — YouTube URL, direct video URL, or local file — by extracting timestamped frames with ffmpeg. No transcript API or paid key needed. Use when the user shares a video link or file and asks to summarize it, explain what happens, find something shown on screen, read on-screen text/numbers, or analyze a specific time range.
---

# watch-video

Analyze videos by **looking at them frame by frame** instead of relying on a
transcript service. One script does everything and prints a JSON manifest.

## Run

```
python <folder-containing-this-SKILL.md>/scripts/watch_video.py "<url-or-local-path>"
```

Useful options:

| Option | When to use |
|---|---|
| `--start 1:20 --end 2:05` | Analyze only a time range (samples it more densely) |
| `--resolution 1024` | On-screen text, code, or numbers must be legible (default 512) |
| `--sheets` | Also build 3x4 contact sheets (12 frames per image) |
| `--max-frames N` | Raise/lower the frame budget (default 100) |
| `--no-captions` | Skip the caption fetch |
| `--cookies file.txt` | YouTube bot-checks this network (see Troubleshooting) |

The script prints a JSON manifest with: video metadata, the extracted frame
files (each with its `timestamp_seconds`), an optional `captions_file`, and
optional contact sheets. It is also saved as `manifest.json` in the workdir.

## Analyze

1. Check the manifest's `timestamps_burned_into_frames` field — when `true`,
   every frame has the video timestamp **burned into its top-left corner**
   (e.g. `00:01:23.000`), so a frame is self-describing even out of context.
   When `false` (no usable font/drawtext on this machine), fall back to each
   frame's `timestamp_seconds` in the manifest instead.
2. View the extracted frames with your image-viewing capability, in batches,
   starting evenly spread across the video; zoom into interesting moments by
   re-running with `--start/--end`.
3. If you can only view a small number of images, re-run with `--sheets` and
   view the contact sheets instead — each sheet covers 12 consecutive frames
   in reading order (left-to-right, top-to-bottom) and the manifest lists the
   time range each sheet covers.
4. If viewing local image files is not permitted in this environment, tell the
   user which frame/sheet files to attach (paths are in the manifest), or fall
   back to `captions_file` for audio content.
5. When answering, cite timestamps (e.g. "at 01:23 ...") so the user can jump
   to the moment in the video.

Captions, when the platform provides them, are written to `captions.txt` as
`[MM:SS] text` lines (`[H:MM:SS]` past one hour) — free, no API. Use them for
what is *said*; use frames for what is *shown*.

## Requirements

- `python` 3.10+, `ffmpeg`/`ffprobe`, `yt-dlp` (only for URLs), and `node` or
  `deno` on PATH (yt-dlp needs a JS runtime for YouTube).
- If a tool is not on PATH, set `WATCH_FFMPEG`, `WATCH_FFPROBE`, or
  `WATCH_YT_DLP` to its full path.
- Keep `yt-dlp` fresh when YouTube breaks: `python -m pip install -U yt-dlp`.

## Troubleshooting

- **"Sign in to confirm you're not a bot"** — YouTube has flagged the network
  IP (very common on corporate VPNs). Disconnect the VPN, or export YouTube
  cookies once with a browser extension ("Get cookies.txt LOCALLY") and pass
  `--cookies <file>` or set `WATCH_COOKIES=<file>`.
- **TLS certificate errors** — corporate HTTPS inspection; the script already
  retries with certificate checks off and warns.
- **Caption download failed** — non-fatal; frames still work. Captions simply
  may not exist for that video.
- **Local files and direct video URLs** (.mp4 links, portals) need none of the
  YouTube workarounds.
