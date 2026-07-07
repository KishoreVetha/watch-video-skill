#!/usr/bin/env python3
"""Watch a video WITHOUT any transcript API.

Pipeline:
  1. Download the video with yt-dlp (or accept a local file path).
  2. Grab the platform's own captions if they exist (free, no API key).
  3. Extract frames with ffmpeg at an auto-scaled fps, with the video
     timestamp burned into the corner of every frame.
  4. Optionally tile frames into contact sheets (12 frames per image) so
     an agent that can only view a few images still sees the whole video.
  5. Print a JSON manifest (video metadata, frame list with timestamps,
     caption file, sheet list) to stdout.

No Python packages required — only yt-dlp and ffmpeg/ffprobe binaries.

Usage:
  python watch_video.py <url-or-local-path> [options]

Options:
  --start T          only analyze from T (SS, MM:SS or HH:MM:SS)
  --end T            only analyze up to T
  --resolution N     frame width in px (default 512; use 1024 for on-screen text)
  --max-frames N     frame budget (default 100)
  --fps F            force an exact fps instead of the auto budget
  --sheets           also build 3x4 contact sheets from the frames
  --no-captions      skip caption download
  --cookies FILE     Netscape cookies.txt for sites that bot-check the network
  --workdir DIR      where to put everything (default: <temp>/codex-watch/<id>)
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_FPS = 2.0

# yt-dlp intermediate/sidecar files that are never the final video, even
# though they share the "video.*" prefix: partial merges, per-stream
# downloads, and non-video sidecars (subtitles, thumbnails, metadata).
_NON_VIDEO_SUFFIXES = {".vtt", ".part", ".ytdl", ".json", ".description", ".jpg", ".png", ".webp", ".txt"}
_PARTIAL_VIDEO_RE = re.compile(r"^video\.(f\d+|temp)\.", re.IGNORECASE)

CAPTION_LANGS = "en-orig,en,en-US,en-GB"
CAPTION_LANG_PRIORITY = ["en", "en-US", "en-GB", "en-orig"]


def _extra_tool_dirs() -> list[str]:
    """Common install locations to try when a tool is not on PATH."""
    home = os.path.expanduser("~")
    dirs = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    dirs += glob.glob(os.path.join(home, "ffmpeg", "*", "bin"))  # unzipped ffmpeg builds
    dirs += glob.glob(os.path.join(home, "AppData", "Local", "Programs", "Python", "Python3*", "Scripts"))
    return dirs


EXTRA_TOOL_DIRS = _extra_tool_dirs()

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def find_tool(name: str) -> str:
    env_override = os.environ.get(f"WATCH_{name.upper().replace('-', '_')}")
    if env_override and Path(env_override).exists():
        return env_override
    hit = shutil.which(name)
    if hit:
        return hit
    for d in EXTRA_TOOL_DIRS:
        for ext in ("", ".exe", ".cmd", ".bat"):
            cand = Path(d) / f"{name}{ext}"
            if cand.exists():
                return str(cand)
    raise SystemExit(
        f"ERROR: '{name}' not found. Install it and/or set the "
        f"WATCH_{name.upper().replace('-', '_')} environment variable to its full path."
    )


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    if cmd and str(cmd[0]).lower().endswith((".cmd", ".bat")):
        # On Windows, batch files run through cmd.exe, whose parser treats
        # &|<>^ as metacharacters even inside an argv list — subprocess's
        # list2cmdline only quotes args containing whitespace, so an
        # unquoted URL like "...&t=30s" gets split into extra commands
        # (the "BatBADBut" argv-injection class). Quoting every argument
        # keeps it literal.
        quoted = " ".join('"{}"'.format(str(a).replace('"', '\\"')) for a in cmd)
        return subprocess.run(quoted, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               shell=True, **kw)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def parse_time(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    parts = str(value).strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SystemExit(f"Cannot parse time value: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def is_url(target: str) -> bool:
    return bool(re.match(r"^https?://", target, re.IGNORECASE))


def default_workdir(target: str) -> Path:
    slug = hashlib.sha1(target.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "codex-watch" / slug


def find_video_file(workdir: Path) -> Path | None:
    """The largest complete video.* file, excluding sidecars and partial merges."""
    candidates = [
        p for p in workdir.glob("video.*")
        if p.suffix.lower() not in _NON_VIDEO_SUFFIXES and not _PARTIAL_VIDEO_RE.match(p.name)
    ]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


def ytdlp_base(cookies: str | None) -> list[str]:
    cmd = [find_tool("yt-dlp"), "--no-playlist"]
    # YouTube extraction needs a JS runtime; deno is the default but node works too.
    if not shutil.which("deno") and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    # Tell yt-dlp about the same ffmpeg this script found — it needs ffmpeg
    # itself to merge separate video+audio streams, and won't discover an
    # ffmpeg that's only reachable via WATCH_FFMPEG/EXTRA_TOOL_DIRS.
    cmd += ["--ffmpeg-location", str(Path(find_tool("ffmpeg")).parent)]
    if cookies:
        cmd += ["--cookies", cookies]
    elif os.environ.get("WATCH_COOKIES") and Path(os.environ["WATCH_COOKIES"]).exists():
        cmd += ["--cookies", os.environ["WATCH_COOKIES"]]
    return cmd


def run_ytdlp(cmd: list[str]) -> subprocess.CompletedProcess:
    result = run(cmd)
    if result.returncode != 0 and "CERTIFICATE_VERIFY_FAILED" in result.stderr:
        # Corporate TLS-inspection proxies re-sign certificates with a root CA
        # Python does not trust. Public-video downloads are low stakes, so
        # retry without verification rather than fail.
        print("WARNING: TLS certificate verification failed (corporate proxy?); "
              "retrying with --no-check-certificates", file=sys.stderr)
        result = run(cmd[:1] + ["--no-check-certificates"] + cmd[1:])
    return result


def pick_caption_vtt(workdir: Path) -> Path | None:
    """Prefer manual subtitles over autogenerated ones (yt-dlp downloads all matching langs)."""
    for lang in CAPTION_LANG_PRIORITY:
        p = workdir / f"video.{lang}.vtt"
        if p.exists():
            return p
    vtts = sorted(workdir.glob("video.*.vtt"))
    return vtts[0] if vtts else None


def fetch_captions(target: str, workdir: Path, cookies: str | None) -> Path | None:
    """Best-effort, non-fatal caption fetch. Returns the flattened captions.txt path, if any."""
    out_tpl = str(workdir / "video.%(ext)s")
    # Exact language codes only — a wildcard like en.* matches dozens of
    # auto-translated tracks and gets rate-limited (HTTP 429).
    sub_result = run_ytdlp(ytdlp_base(cookies) + [
        "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", CAPTION_LANGS,
        "--sub-format", "vtt",
        "-o", out_tpl,
        target,
    ])
    if sub_result.returncode != 0:
        tail = (sub_result.stderr.strip().splitlines() or ["<no output>"])[-1][:200]
        print(f"WARNING: caption download failed (video is fine, continuing without captions): {tail}",
              file=sys.stderr)
    vtt = pick_caption_vtt(workdir)
    if not vtt:
        return None
    caption_txt = workdir / "captions.txt"
    caption_txt.write_text(vtt_to_text(vtt.read_text(encoding="utf-8-sig", errors="replace")), encoding="utf-8")
    return caption_txt


def download(target: str, workdir: Path, want_captions: bool, cookies: str | None) -> tuple[Path, Path | None]:
    """Download the video, then captions as a separate best-effort step."""
    out_tpl = str(workdir / "video.%(ext)s")
    result = run_ytdlp(ytdlp_base(cookies) + [
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--merge-output-format", "mp4",
        "-o", out_tpl,
        target,
    ])
    if result.returncode != 0:
        err = result.stderr.strip()
        if "Sign in to confirm" in err:
            raise SystemExit(
                "yt-dlp failed: YouTube bot-check on this network (common on corporate/VPN IPs —\n"
                "try disconnecting the VPN first).\n"
                "Alternative fix: export your YouTube cookies once with a browser extension such as\n"
                "'Get cookies.txt LOCALLY', save the file, and re-run with --cookies <file>\n"
                "(or set the WATCH_COOKIES environment variable to the file path).\n"
                "Local video files and direct video URLs work without cookies."
            )
        raise SystemExit(f"yt-dlp failed:\n{err[-2000:]}")

    video = find_video_file(workdir)
    if not video:
        raise SystemExit(f"yt-dlp reported success but no video file found in {workdir}")

    caption_txt = fetch_captions(target, workdir, cookies) if want_captions else None
    return video, caption_txt


def vtt_to_text(vtt: str) -> str:
    """Flatten a VTT file to '[MM:SS] text' (or '[H:MM:SS]' past 1h) lines, deduping auto-sub repeats."""
    lines = vtt.splitlines()
    lines_out: list[str] = []
    last_text = None
    stamp = None
    in_note = False
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if in_note:
            if not line:
                in_note = False
            continue
        if line.startswith("NOTE"):
            in_note = True
            continue
        if not line or line.startswith("WEBVTT") or line.startswith(("Kind:", "Language:")):
            continue
        # Hours are optional per the WebVTT spec (only YouTube always writes them).
        m = re.match(r"^(?:(\d{1,3}):)?(\d{2}):(\d{2})[.,]\d{3}\s*-->", line)
        if m:
            h = int(m.group(1)) if m.group(1) else 0
            mnt, s = int(m.group(2)), int(m.group(3))
            stamp = f"[{h}:{mnt:02d}:{s:02d}]" if h else f"[{mnt:02d}:{s:02d}]"
            continue
        if "-->" in line:
            continue  # unrecognized timing-line variant — never leak it as caption text
        # An SRT-style cue-number line is only a number if the very next
        # line is a timing line — otherwise it may be real numeric caption text.
        if line.isdigit() and i < len(lines) and "-->" in lines[i]:
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if text and text != last_text:
            lines_out.append(f"{stamp} {text}" if stamp else text)
            last_text = text
    return "\n".join(lines_out)


def probe(video: Path) -> dict:
    ffprobe = find_tool("ffprobe")
    result = run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(video)])
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fmt = data.get("format", {})
    return {
        "duration_seconds": float(fmt.get("duration") or vstream.get("duration") or 0),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def auto_fps(duration: float, max_frames: int, focused: bool) -> float:
    """Frame budget by duration: short videos dense, long videos capped."""
    if duration <= 0:
        return 1.0
    if focused:
        if duration <= 5:
            target = max(10, round(duration * 6))
        elif duration <= 15:
            target = max(30, round(duration * 4))
        elif duration <= 30:
            target = 60
        elif duration <= 60:
            target = 80
        else:
            target = max_frames
    else:
        if duration <= 30:
            target = max(12, round(duration))
        elif duration <= 60:
            target = 40
        elif duration <= 180:
            target = 60
        elif duration <= 600:
            target = 80
        else:
            target = max_frames
    return min(MAX_FPS, min(max_frames, target) / duration)


def find_font() -> str | None:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


def timestamp_filter(offset_seconds: float, fontsize: int = 22) -> str | None:
    font = find_font()
    if not font:
        return None
    # ffmpeg filter args split on ':', so colons in the font path and inside
    # the %{pts} expansion must be backslash-escaped.
    font_esc = font.replace("\\", "/").replace(":", r"\:")
    return (
        f"drawtext=fontfile='{font_esc}':text='%{{pts\\:hms\\:{offset_seconds:.3f}}}'"
        f":x=8:y=8:fontsize={fontsize}:fontcolor=white:borderw=2:bordercolor=black"
    )


def _numeric_glob(dir_: Path, pattern: str) -> list[Path]:
    """Sort frame_%04d.jpg / sheet_%02d.jpg by their numeric suffix, not lexicographically
    (ffmpeg widens the field past 4/2 digits once counts exceed the pattern width)."""
    return sorted(dir_.glob(pattern), key=lambda p: int(re.search(r"\d+", p.stem).group()))


def extract_frames(
    video: Path, out_dir: Path, fps: float, resolution: int, max_frames: int,
    start: float | None, end: float | None,
) -> tuple[list[dict], bool]:
    """Returns (frames, timestamps_actually_burned_in)."""
    ffmpeg = find_tool("ffmpeg")
    # Extract into a temp dir and swap on success, so a failed re-run leaves
    # the previous successful frames (and manifest.json, which describes
    # them) intact instead of deleting frames a stale manifest still points to.
    tmp_dir = out_dir.with_name(out_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    offset = start or 0.0
    vf_parts = [f"fps={fps}", f"scale={resolution}:-2"]
    stamp = timestamp_filter(offset)
    if stamp:
        vf_parts.append(stamp)

    def build_cmd(vf: str) -> list[str]:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if start is not None:
            cmd += ["-ss", f"{start:.3f}"]
        if end is not None:
            cmd += ["-to", f"{end:.3f}"]
        cmd += ["-i", str(video), "-vf", vf, "-frames:v", str(max_frames), "-q:v", "4",
                str(tmp_dir / "frame_%04d.jpg")]
        return cmd

    result = run(build_cmd(",".join(vf_parts)))
    stamped_ok = stamp is not None and result.returncode == 0
    if result.returncode != 0 and stamp:
        # Retry without the timestamp overlay (missing/odd font or drawtext setups).
        result = run(build_cmd(",".join(vf_parts[:2])))
        stamped_ok = False
    if result.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise SystemExit(f"ffmpeg frame extraction failed: {result.stderr.strip()[-2000:]}")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp_dir.rename(out_dir)

    frames = _numeric_glob(out_dir, "frame_*.jpg")
    return [
        {"index": i, "timestamp_seconds": round(offset + i / fps, 2), "path": str(p)}
        for i, p in enumerate(frames)
    ], stamped_ok


def build_sheets(frames_dir: Path, out_dir: Path, frames: list[dict], cols: int = 3, rows: int = 4) -> list[dict]:
    ffmpeg = find_tool("ffmpeg")
    tmp_dir = out_dir.with_name(out_dir.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    per_sheet = cols * rows
    result = run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", "1", "-start_number", "1",
        "-i", str(frames_dir / "frame_%04d.jpg"),
        "-vf", f"tile={cols}x{rows}",
        "-q:v", "4",
        str(tmp_dir / "sheet_%02d.jpg"),
    ])
    if result.returncode != 0:
        print(f"WARNING: contact-sheet build failed: {result.stderr.strip()[-500:]}", file=sys.stderr)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return []

    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp_dir.rename(out_dir)

    sheets = []
    for i, p in enumerate(_numeric_glob(out_dir, "sheet_*.jpg")):
        chunk = frames[i * per_sheet:(i + 1) * per_sheet]
        if not chunk:
            break
        sheets.append({
            "path": str(p),
            "covers_seconds": [chunk[0]["timestamp_seconds"], chunk[-1]["timestamp_seconds"]],
            "frame_indexes": [chunk[0]["index"], chunk[-1]["index"]],
            "layout": f"{cols} columns x {rows} rows, reading order left-to-right then top-to-bottom",
        })
    return sheets


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="video URL or local file path")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--resolution", type=int, default=512)
    ap.add_argument("--max-frames", type=int, default=100)
    ap.add_argument("--fps", type=float, default=None)
    ap.add_argument("--sheets", action="store_true")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--cookies", default=None, help="Netscape cookies.txt for sites that bot-check the network")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    workdir = Path(args.workdir) if args.workdir else default_workdir(args.target)
    workdir.mkdir(parents=True, exist_ok=True)

    caption_txt = None
    reused = False
    if is_url(args.target):
        video = find_video_file(workdir)
        if video:
            reused = True
            print(f"Reusing already-downloaded {video}", file=sys.stderr)
            if args.no_captions:
                caption_txt = None
            else:
                cap = workdir / "captions.txt"
                caption_txt = cap if cap.exists() else fetch_captions(args.target, workdir, args.cookies)
        else:
            video, caption_txt = download(args.target, workdir, want_captions=not args.no_captions, cookies=args.cookies)
    else:
        video = Path(args.target)
        if not video.exists():
            raise SystemExit(f"Local file not found: {video}")

    try:
        meta = probe(video)
    except SystemExit:
        if not reused:
            raise
        # Cached file from an interrupted prior run (truncated merge, killed
        # download) — drop it and fetch a fresh copy instead of failing forever.
        print("WARNING: cached video looks corrupt/incomplete; re-downloading", file=sys.stderr)
        video.unlink(missing_ok=True)
        (workdir / "captions.txt").unlink(missing_ok=True)
        video, caption_txt = download(args.target, workdir, want_captions=not args.no_captions, cookies=args.cookies)
        meta = probe(video)

    start = parse_time(args.start)
    end = parse_time(args.end)
    if start is not None and meta["duration_seconds"] > 0 and start >= meta["duration_seconds"]:
        raise SystemExit(
            f"--start {args.start} ({start:.1f}s) is at or past the video's "
            f"duration ({meta['duration_seconds']:.1f}s)."
        )
    focused = start is not None or end is not None
    eff_start = start or 0.0
    eff_end = end if end is not None else meta["duration_seconds"]
    duration = max(0.0, eff_end - eff_start)

    fps = args.fps if args.fps else auto_fps(duration, args.max_frames, focused)
    frames, stamped_ok = extract_frames(video, workdir / "frames", fps, args.resolution, args.max_frames, start, end)
    sheets = build_sheets(workdir / "frames", workdir / "sheets", frames) if args.sheets else []

    # An explicit --fps (or a container with no duration metadata) can hit
    # the frame budget before covering the requested range; reflect what was
    # actually captured rather than silently overclaiming full coverage.
    if fps > 0 and len(frames) == args.max_frames:
        covered_end = round(eff_start + len(frames) / fps, 2)
        if covered_end < eff_end - 0.5:
            print(f"WARNING: hit --max-frames {args.max_frames} at {covered_end}s; "
                  f"the range up to {eff_end}s was not fully sampled. Re-run with --start {covered_end} "
                  f"to continue, or raise --max-frames.", file=sys.stderr)
            eff_end = covered_end

    manifest = {
        "video": str(video),
        "meta": meta,
        "analyzed_range_seconds": [round(eff_start, 2), round(eff_end, 2)],
        "fps": round(fps, 4),
        "frame_count": len(frames),
        "timestamps_burned_into_frames": stamped_ok,
        "captions_file": str(caption_txt) if caption_txt else None,
        "frames_dir": str(workdir / "frames"),
        "frames": frames,
        "sheets": sheets,
    }
    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
