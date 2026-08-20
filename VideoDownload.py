#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clone Hero video fetch + autosync.

Rewritten 2026-08-19. Same command-line surface as before (the GUI keeps
working unchanged) with the following fixed:

  * Search no longer needs a YouTube API key. yt-dlp searches YouTube
    directly, so the 10,000-unit/day API quota (~24 songs/day) is gone.
  * Format selection prefers H.264, which is what Clone Hero plays reliably.
    The old selector fell through to `best[ext=mp4]`, a progressive format
    YouTube caps at 360p -- that is why 30 of 83 existing videos are 360p
    and 36 are AV1/VP9.
  * Videos are always downloaded with their audio track. Without it a video
    cannot be auto-synced at all (12 existing videos have no audio).
  * song.ini updates are surgical. The old writer rebuilt the file and
    destroyed every other key when the file had no [song] header.
  * Sync uses FFT cross-correlation over onset envelopes (syncengine.py)
    instead of a brute-force Python loop, and no longer contains any
    song-specific hardcoded offsets.
  * has_min_resolution() referenced an undefined `video_url` and crashed the
    entire run with a NameError whenever it reached its fallback branch.

CLI examples
------------
  # Normal run: fetch anything missing a video, and sync it
  python3 VideoDownload.py "~/Clone Hero/Songs" --quality best1080 --auto-sync

  # Re-sync what is already downloaded, no network
  python3 VideoDownload.py "~/Clone Hero/Songs" --sync-only

  # Re-download everything at better quality
  python3 VideoDownload.py "~/Clone Hero/Songs" --replace --transcode
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

import matching
import syncengine
from matching import Candidate

PRINT_LOCK = threading.Lock()
PROGRESS = {"done": 0, "total": 0}


@dataclass
class ChartResult:
    """What happened to one chart, in enough detail to summarise it later."""
    label: str
    status: str                      # ok | skip | review | fail
    detail: str = ""
    downloaded: bool = False
    synced: bool = False
    notes: List[str] = field(default_factory=list)


_LOCAL = threading.local()
IS_TTY = sys.stdout.isatty()


def set_current(label: str) -> None:
    _LOCAL.label = label


def status(text: str) -> None:
    """Update the single live status line.

    In a terminal this rewrites the current line in place. Through a pipe
    (the GUI) it is sent as a tagged line the GUI shows in its status bar
    instead of appending to the log, so the log stays short.
    """
    label = getattr(_LOCAL, "label", "")
    line = f"[{PROGRESS['done'] + 1}/{PROGRESS['total']}] {text}"
    if label:
        line += f"  ·  {label}"
    with PRINT_LOCK:
        if IS_TTY:
            sys.stdout.write("\r\033[K" + line[:150])
            sys.stdout.flush()
        else:
            print("@STATUS " + line, flush=True)


def step(log: List[str], text: str) -> None:
    """A progress step: shown live, and kept for --verbose."""
    log.append(f"         {text}")
    status(text)


def next_index() -> str:
    with PRINT_LOCK:
        PROGRESS["done"] += 1
        return f"[{PROGRESS['done']:>3}/{PROGRESS['total']}]"


# --------------------------- CLI ---------------------------

def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Clone Hero video fetch + autosync",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("songs_root", help="Path to Clone Hero 'Songs' folder")

    # --- preserved from the original CLI (the GUI passes all of these) ---
    p.add_argument("--quality", default="best1080",
                   help="best720 / best1080 / best1440 / best2160 / best. "
                        "Above 1080p there is no H.264 on YouTube, so pair the "
                        "higher settings with --transcode")
    p.add_argument("--replace", action="store_true",
                   help="Re-download even if video.mp4 already exists")
    p.add_argument("--auto-sync", action="store_true",
                   help="Compute and write video_start_time")
    p.add_argument("--min-conf", type=float, default=0.30,
                   help="Minimum correlation confidence to accept a sync")
    p.add_argument("--official-360", action="store_true",
                   help="Legacy alias for --min-height 360")
    p.add_argument("--min-height", type=int, default=360,
                   help="Reject a download below this height. Many older music "
                        "videos only exist at 360-480p, so rejecting them means "
                        "no video at all. Raise to 720 to be strict")
    p.add_argument("--sleep-interval", type=float, default=0.0,
                   help="Min seconds to sleep between downloads")
    p.add_argument("--max-sleep-interval", type=float, default=0.0,
                   help="Max seconds to sleep between downloads")
    p.add_argument("--limit-rate", default=None, help="Cap download speed, e.g. 5M")
    p.add_argument("--workers", type=int, default=3, help="Charts processed in parallel")
    p.add_argument("--manual-map", default=None,
                   help="File of lines: <artist> - <song>|<youtube_url>")
    p.add_argument("--only-list", default=None,
                   help="Only process songs listed in this file")
    p.add_argument("--sync-only", action="store_true",
                   help="Recompute offsets for existing videos, no downloading")

    # --- new ---
    p.add_argument("--min-sharpness", type=float, default=4.0,
                   help="Minimum peak sharpness to accept a sync (guards against "
                        "a confident-looking but flat correlation)")
    p.add_argument("--search-results", type=int, default=8,
                   help="Candidates to fetch per query")
    p.add_argument("--min-score", type=float, default=40.0,
                   help="Reject the best candidate if it scores below this")
    p.add_argument("--print-candidates", action="store_true",
                   help="Print the candidate list for each queued chart as JSON "
                        "and exit. Used by the GUI's video picker")
    p.add_argument("--confident-score", type=float, default=95.0,
                   help="Below this the best match is treated as uncertain and "
                        "the chart is left for you to choose (see --no-review)")
    p.add_argument("--no-review", action="store_true",
                   help="Never defer an uncertain chart, just take the best match")
    p.add_argument("--max-attempts", type=int, default=4,
                   help="How many candidates to try before giving up on a chart")
    p.add_argument("--min-motion", type=float, default=2.0,
                   help="Reject a download whose picture barely moves. Filters out "
                        "album-art 'art tracks'. Real videos score 30+, still "
                        "images score under 0.1")
    p.add_argument("--allow-static", action="store_true",
                   help="Keep still-image uploads instead of rejecting them")
    p.add_argument("--transcode", action="store_true",
                   help="Re-encode to H.264 if the download is not already H.264")
    p.add_argument("--cookies-from-browser", default=None,
                   help="e.g. chrome/safari/firefox. Needs Full Disk Access for your terminal")
    p.add_argument("--cookies", default=None,
                   help="Path to a cookies.txt exported from a signed-in browser. "
                        "YouTube now refuses media downloads to unauthenticated "
                        "sessions, so this is usually what makes downloads work")
    p.add_argument("--remote-components", default="ejs:github",
                   help="yt-dlp challenge-solver components. YouTube signs its "
                        "media URLs; without these yt-dlp can only see "
                        "storyboard thumbnails. Empty string disables")
    p.add_argument("--js-runtime", default="auto",
                   help="JS runtime for yt-dlp (auto/deno/node/none). deno is "
                        "preferred; YouTube challenges cannot be solved without one")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be picked and downloaded, change nothing")
    p.add_argument("--verbose", action="store_true", help="Show scoring detail")
    return p


# --------------------------- small utils ---------------------------

def emit(lines: List[str]) -> None:
    """Print a chart's result atomically so parallel workers don't interleave."""
    with PRINT_LOCK:
        if IS_TTY:
            sys.stdout.write("\r\033[K")
        print("\n".join(lines), flush=True)


def offer_options(chart_dir: Path, label: str, cands: List[Candidate],
                  chart_len: Optional[float], problems: List[str]) -> None:
    """Show the alternatives for a chart we are not confident about, and hand
    the GUI the same list so it can offer them without searching again."""
    top = cands[:4]
    if not top:
        return
    problems.append("options:")
    for i, c in enumerate(top, 1):
        mins, secs = divmod(int(c.duration or 0), 60)
        fit = ""
        if chart_len and c.duration:
            d = abs(c.duration - chart_len)
            fit = "  length ✓" if d <= 5 else ("  length ~" if d <= 20 else "  length ✗")
        problems.append(f'   {i}. "{c.title[:52]}"')
        problems.append(f"      {c.channel[:26]} · {mins}:{secs:02d} · "
                        f"{c.tier_name} · score {c.score:.0f}{fit}")
    problems.append("   Listed music videos first, then lyric videos, so the "
                    "scores are not in order.")

    payload = {
        "rel": str(chart_dir),
        "label": label,
        "chart_len": chart_len,
        "candidates": [
            {"id": c.id, "title": c.title, "channel": c.channel,
             "duration": c.duration, "views": c.views, "score": round(c.score, 1),
             "url": c.url, "reasons": c.reasons}
            for c in top],
    }
    with PRINT_LOCK:
        print("@REVIEW " + json.dumps(payload), flush=True)


def finish(idx: str, mark: str, label: str, facts: List[str],
           problems: List[str], log: List[str], args) -> None:
    """One line per chart, plus problem lines. Full detail only with --verbose."""
    head = f"{idx} {mark} {label}"
    if facts:
        head += "  ·  " + "  ·  ".join(facts)
    emit([head] + (log if args.verbose else [f"         {p}" for p in problems]))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def run(cmd: List[str], timeout: Optional[float] = None) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except FileNotFoundError as e:
        return 127, "", str(e)


def youtube_args(args) -> List[str]:
    """Flags every YouTube call needs.

    YouTube signs its media URLs with a JavaScript challenge. Without both a
    JS runtime AND yt-dlp's solver components, extraction silently degrades
    to "only images are available", or a 403 at download time.
    """
    out: List[str] = []
    if getattr(args, "js_runtime", None):
        out += ["--js-runtimes", args.js_runtime]
    if getattr(args, "remote_components", ""):
        out += ["--remote-components", args.remote_components]
    if getattr(args, "cookies", None):
        out += ["--cookies", str(args.cookies)]
    elif getattr(args, "cookies_from_browser", None):
        out += ["--cookies-from-browser", args.cookies_from_browser]
    return out


def ytdlp_cmd() -> List[str]:
    """Always use the yt-dlp installed in THIS interpreter's environment.

    The old code called a bare `yt-dlp` from PATH, which on this Mac was a
    Homebrew build ten months older than the one in the project venv.
    """
    return [sys.executable, "-m", "yt_dlp"]


# --------------------------- song.ini ---------------------------

KV_RE = re.compile(r"^(\s*)([^=\[\]#;\r\n]+?)(\s*)=(\s*)(.*?)(\s*)$")


def parse_song_ini(path: Path) -> Dict[str, str]:
    """Read song.ini into a dict. Tolerates a missing [song] header, duplicate
    keys and stray junk, which real charts all contain."""
    meta: Dict[str, str] = {}
    for line in read_text(path).splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ";")) or (s.startswith("[") and s.endswith("]")):
            continue
        m = KV_RE.match(line)
        if m:
            meta[m.group(2).strip().lower()] = m.group(5).strip()
    return meta


def update_song_ini(path: Path, updates: Dict[str, str]) -> bool:
    """Change only the given keys, leaving every other line byte-identical.

    The previous implementation rebuilt the file from scratch, re-sorted the
    keys, and -- when the file had no [song] header -- emitted only the key it
    was setting, silently deleting name/artist/song_length/charter/everything.
    """
    try:
        raw = path.read_bytes()
    except Exception:
        return False
    if not raw:
        return False
    # Detect the file's own line ending from the BYTES. read_text() applies
    # universal-newline translation, so checking the decoded string always
    # says LF and would silently convert a CRLF chart on every write.
    newline = "\r\n" if b"\r\n" in raw else "\n"
    txt = raw.decode("utf-8", errors="ignore")
    had_trailing = txt.endswith(("\n", "\r"))
    lines = txt.splitlines()

    remaining = {k.lower(): str(v) for k, v in updates.items()}
    out: List[str] = []
    last_kv_idx = -1

    for line in lines:
        m = KV_RE.match(line)
        if m:
            key = m.group(2).strip().lower()
            if key in remaining:
                # Rewrite in place, preserving the file's own spacing style.
                out.append(f"{m.group(1)}{m.group(2)}{m.group(3)}="
                           f"{m.group(4)}{remaining.pop(key)}")
            else:
                out.append(line)
            last_kv_idx = len(out) - 1
        else:
            out.append(line)

    # Anything not already present gets inserted right after the last existing
    # key/value pair, which keeps it inside the [song] section.
    if remaining:
        insert_at = last_kv_idx + 1 if last_kv_idx >= 0 else len(out)
        for k, v in remaining.items():
            out.insert(insert_at, f"{k} = {v}")
            insert_at += 1

    body = newline.join(out) + (newline if had_trailing else "")
    tmp = path.with_suffix(".ini.tmp")
    # newline="" stops Python translating our explicit line endings again.
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    tmp.replace(path)   # atomic; a crash mid-write cannot truncate song.ini
    return True


def chart_length_seconds(meta: Dict[str, str]) -> Optional[float]:
    """song_length is in milliseconds and present in every real chart."""
    raw = meta.get("song_length", "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    if v <= 0:
        return None
    return v / 1000.0


# --------------------------- search ---------------------------

def ytdlp_search(query: str, n: int, args) -> List[Candidate]:
    """Search YouTube through yt-dlp. No API key, no quota."""
    cmd = ytdlp_cmd() + [
        f"ytsearch{n}:{query}", "--flat-playlist", "--no-warnings", "--quiet",
        "--ignore-config",
        "--print", "%(id)s\t%(title)s\t%(channel)s\t%(duration)s\t%(view_count)s",
    ]
    cmd += youtube_args(args)

    code, out, err = run(cmd, timeout=120)
    if code != 0:
        return []

    cands: List[Candidate] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        vid, title, channel, dur, views = parts[:5]
        if not vid or vid == "NA":
            continue

        def num(s: str) -> int:
            try:
                return int(float(s))
            except (ValueError, TypeError):
                return 0

        cands.append(Candidate(id=vid, title=title,
                               channel=channel if channel != "NA" else "",
                               duration=num(dur), views=num(views)))
    return cands


def find_best_video(artist: str, song: str, chart_len: Optional[float],
                    args) -> Tuple[List[Candidate], List[str]]:
    """Search and score; return every candidate good enough to try, best first."""
    log: List[str] = []
    want_live = matching.is_live_chart(song)
    queries = matching.build_queries(artist, song, want_live)

    seen: Dict[str, Candidate] = {}
    for q in queries:
        found = ytdlp_search(q, args.search_results, args)
        for c in found:
            seen.setdefault(c.id, c)
        # A confident hit on the first, most specific query is enough.
        if seen:
            ranked_now = matching.rank(list(seen.values()), artist, song,
                                       chart_len, want_live)
            if ranked_now and ranked_now[0].score >= 110:
                break

    if not seen:
        log.append("   ! no search results")
        return [], log

    ranked = matching.rank(list(seen.values()), artist, song, chart_len, want_live)
    if not ranked:
        log.append(f"   ! all {len(seen)} results disqualified (cover/karaoke/wrong length)")
        return [], log

    if args.verbose:
        for c in ranked[:4]:
            log.append(f"   · {c.score:6.1f}  {c.title[:60]}")
            log.append(f"            {'; '.join(c.reasons)}")

    # Keep everything above the bar, not just the winner. A candidate can still
    # turn out to be a still image once downloaded, and we want a fallback.
    shortlist = [c for c in ranked if c.score >= args.min_score]
    if not shortlist:
        log.append(f"   ! best match scored {ranked[0].score:.0f}, below "
                   f"--min-score {args.min_score:.0f}: {ranked[0].title[:56]}")
        return [], log
    return shortlist, log


# --------------------------- download ---------------------------

def format_selector(quality: str) -> str:
    """Prefer H.264 (avc1), which Clone Hero decodes reliably, then fall back.

    Note there is deliberately no bare `best[ext=mp4]` early in this chain:
    on YouTube that resolves to a progressive stream capped at 360p, which is
    exactly how the old version ended up with 30 x 360p videos.
    """
    # YouTube only publishes H.264 up to 1080p. Above that it is VP9 or AV1
    # only, which Clone Hero often refuses or stutters on, so 1440/2160 will
    # fall through to the non-avc1 branches. Pair them with --transcode.
    cap = {"best720": 720, "best1080": 1080,
           "best1440": 1440, "best2160": 2160}.get(quality)
    h = f"[height<={cap}]" if cap else ""
    return (
        f"bestvideo{h}[vcodec^=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo{h}[vcodec^=avc1]+bestaudio/"
        f"bestvideo{h}+bestaudio[ext=m4a]/"
        f"bestvideo{h}+bestaudio/"
        f"best{h}/best"
    )


def probe_video(path: Path) -> Dict:
    code, out, _ = run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_streams", "-show_format", str(path)], timeout=60)
    if code != 0:
        return {}
    try:
        return json.loads(out)
    except Exception:
        return {}


def video_info(path: Path) -> Tuple[int, str, bool, float]:
    """(height, video codec, has_audio, duration)"""
    d = probe_video(path)
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = float(d.get("format", {}).get("duration", 0) or 0)
    return int(v.get("height") or 0), str(v.get("codec_name") or "?"), has_audio, dur


def video_motion_score(path: Path, samples: int = 12, size: int = 64) -> float:
    """How much the picture actually moves, as mean frame-to-frame difference.

    An "art track" -- a still album cover with the song playing over it -- is
    what YouTube serves for a lot of songs that have no real music video, and
    nothing in the title or channel reliably marks it as one. Measuring the
    picture is the only dependable test.

    Measured on this library: static album art scores ~0.01, real music videos
    score 37-50. Anything above a couple of points is genuinely moving.
    """
    code, out, _ = run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)], timeout=60)
    try:
        duration = float(out.strip())
    except (ValueError, AttributeError):
        return -1.0
    if duration <= 0:
        return -1.0

    frames = []
    for i in range(samples):
        t = duration * (i + 0.5) / samples
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={size}:{size},format=gray",
             "-f", "rawvideo", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if len(proc.stdout) == size * size:
            frames.append(np.frombuffer(proc.stdout, dtype=np.uint8).astype(np.float32))

    if len(frames) < 3:
        return -1.0            # could not measure; caller should not reject
    diffs = [float(np.mean(np.abs(frames[i + 1] - frames[i])))
             for i in range(len(frames) - 1)]
    return float(np.mean(diffs))


def download_video(url: str, out_path: Path, args, log: List[str],
                   facts: List[str], problems: List[str]) -> bool:
    tmp = out_path.with_name(out_path.stem + ".part.mp4")
    for stale in out_path.parent.glob(f"{out_path.stem}.part*"):
        stale.unlink(missing_ok=True)

    cmd = ytdlp_cmd() + [
        "-f", format_selector(args.quality),
        "--merge-output-format", "mp4",
        # Clone Hero wants a plain H.264/AAC mp4; strip anything else that
        # would otherwise ride along in the container.
        "--postprocessor-args", "Merger:-c:v copy -c:a aac -movflags +faststart",
        "-o", str(tmp),
        "--no-playlist", "--no-warnings", "--quiet", "--no-progress",
        "--ignore-config",
        "--retries", "5", "--fragment-retries", "10",
        "--socket-timeout", "30", "--concurrent-fragments", "4",
    ]
    cmd += youtube_args(args)
    if args.limit_rate:
        cmd += ["--limit-rate", str(args.limit_rate)]
    if args.sleep_interval > 0:
        cmd += ["--sleep-interval", str(args.sleep_interval)]
    if args.max_sleep_interval > 0:
        cmd += ["--max-sleep-interval", str(args.max_sleep_interval)]

    code, out, err = run(cmd + [url], timeout=1800)
    if code != 0 or not tmp.exists():
        blob = (err or out).strip()
        msg = blob.splitlines()
        last = msg[-1] if msg else "unknown error"
        log.append(f"   ! download failed: {last}")
        # This specific pair of errors means YouTube refused an unauthenticated
        # session, not that anything is wrong with the video or the settings.
        if ("403" in blob or "page needs to be reloaded" in blob
                or "Requested format is not available" in blob
                or "Only images are available" in blob):
            if not (args.cookies or args.cookies_from_browser):
                log.append("     No cookies configured. Put a cookies.txt "
                           "exported from a signed-in browser next to this "
                           "script, see the README.")
            elif not args.js_runtime:
                log.append("     No JavaScript runtime. Run: brew install deno")
            elif not args.remote_components:
                log.append("     Challenge solver disabled. Drop "
                           "--remote-components '' to re-enable it.")
        tmp.unlink(missing_ok=True)
        return False

    h, codec, has_audio, dur = video_info(tmp)
    mins, secs = divmod(int(dur), 60)
    step(log, f"Downloaded {h}p {codec.upper()}, {mins}:{secs:02d} long"
              + ("" if has_audio else ", but it has NO AUDIO"))
    facts[:] = [f"{h}p {codec.upper()}"]
    if not has_audio:
        problems.append("this video has no audio track, so it cannot be synced")

    # Resolution policy
    if h and h < args.min_height:
        step(log, f"Rejected: only {h}p, below your {args.min_height}p minimum.")
        problems.append(f"skipped a {h}p upload (below your minimum)")
        tmp.unlink(missing_ok=True)
        return False
    if h and h < 720:
        step(log, f"Note: {h}p is the best quality this video offers.")

    # Reject still-image uploads unless the user asked to keep them.
    if not args.allow_static:
        motion = video_motion_score(tmp)
        if motion < 0:
            step(log, "Could not check whether the picture moves. Keeping it.")
        elif motion < args.min_motion:
            step(log, "Rejected: the picture never moves. This is album art, "
                      "not a music video.")
            problems.append("skipped an album-art upload (not a real video)")
            tmp.unlink(missing_ok=True)
            return False
        else:
            step(log, "Checked: the picture moves, so it is a real video.")

    if args.transcode and codec != "h264":
        if not transcode_to_h264(tmp, log):
            tmp.unlink(missing_ok=True)
            return False

    out_path.unlink(missing_ok=True)
    tmp.replace(out_path)
    return True


def transcode_to_h264(path: Path, log: List[str]) -> bool:
    """Re-encode to H.264. Uses Apple's hardware encoder when available."""
    dst = path.with_name(path.stem + ".h264.mp4")
    encoders = ["h264_videotoolbox", "libx264"]
    for enc in encoders:
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
               "-c:v", enc, "-b:v", "6M", "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", str(dst)]
        code, _, err = run(cmd, timeout=3600)
        if code == 0 and dst.exists() and dst.stat().st_size > 0:
            dst.replace(path)
            log.append(f"         Converted to H.264 so Clone Hero can play it.")
            return True
        dst.unlink(missing_ok=True)
    log.append("         Could not convert this video to H.264.")
    return False


# --------------------------- sync ---------------------------

def autosync(chart_dir: Path, video_path: Path, args, log: List[str],
             facts: List[str], problems: List[str]) -> Tuple[int, float]:
    h, codec, has_audio, _ = video_info(video_path)
    if not has_audio:
        step(log, "Cannot sync: this video has no audio track. "
                  "Re-download it to fix.")
        problems.append("no audio track, so it could not be synced")
        return 0, 0.0

    res = syncengine.sync_chart_to_video(chart_dir, video_path)
    if res.note:
        step(log, f"Could not sync: {res.note}")
        problems.append(f"could not sync: {res.note}")
        return 0, 0.0

    accept = res.confidence >= args.min_conf and res.sharpness >= args.min_sharpness
    verdict = "APPLY" if accept else "SKIP"
    step(log, f"Matched the video against the chart audio "
              f"({res.sharpness:.0f} of 8 sections agree, "
              f"confidence {res.confidence:.2f}).")
    step(log, ("Sync: " + res.interpretation) if accept else
              f"Not confident enough to set the sync, leaving it alone.")

    if accept and not args.dry_run:
        update_song_ini(chart_dir / "song.ini", {"video_start_time": str(res.offset_ms)})
        step(log, f"Saved video_start_time = {res.offset_ms} to song.ini")
        facts.append(f"synced {res.offset_ms:+d} ms")
    elif not accept:
        step(log, "Left song.ini alone. You can set the timing by hand.")
        problems.append(f"sync was not confident ({res.sharpness:.0f}/8 sections "
                        f"agreed), song.ini left alone")
    return res.offset_ms, res.confidence


# --------------------------- per chart ---------------------------

def load_manual_map(path: Optional[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not path:
        return mapping
    p = Path(path).expanduser()
    if not p.exists():
        return mapping
    for line in read_text(p).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        key, url = line.split("|", 1)
        mapping[matching.normalize(key)] = url.strip()
    return mapping


def process_chart(chart_dir: Path, args, manual_map: Dict[str, str]) -> ChartResult:
    ini = chart_dir / "song.ini"
    if not ini.exists():
        return ChartResult(chart_dir.name, "fail", "no song.ini in this folder")

    meta = parse_song_ini(ini)
    # song.ini may carry Clone Hero colour markup; it must not reach YouTube.
    artist = matching.strip_markup(meta.get("artist", ""))
    song = matching.strip_markup(meta.get("name", ""))
    if not artist or not song:
        return ChartResult(chart_dir.name, "fail", "song.ini has no artist or name")

    label = f"{artist} - {song}"
    set_current(label)
    chart_len = chart_length_seconds(meta)
    video = chart_dir / "video.mp4"
    log: List[str] = []
    facts: List[str] = []
    problems: List[str] = []

    # ---------- sync-only ----------
    if args.sync_only:
        if not video.exists():
            return ChartResult(label, "skip", "no video to sync yet")
        step(log, "Checking the sync…")
        off, conf = autosync(chart_dir, video, args, log, facts, problems)
        ok = conf >= args.min_conf
        finish(next_index(), "✓" if ok else "?", label, facts, problems, log, args)
        return ChartResult(label, "ok" if ok else "review",
                           f"synced {off:+d} ms" if ok else "sync not confident",
                           synced=ok,
                           notes=[] if ok else ["sync was not confident"])

    # ---------- already have it ----------
    if video.exists() and not args.replace:
        if args.auto_sync:
            step(log, "Already has a video, checking the sync…")
            off, conf = autosync(chart_dir, video, args, log, facts, problems)
            ok = conf >= args.min_conf
            finish(next_index(), "✓" if ok else "?", label, facts, problems, log, args)
            return ChartResult(label, "ok" if ok else "review",
                               f"synced {off:+d} ms" if ok else "sync not confident",
                               synced=ok, notes=[] if ok else ["sync was not confident"])
        return ChartResult(label, "skip", "already has a video")

    # ---------- pick a video ----------
    url = (chart_dir / "video_url.txt").read_text(encoding="utf-8",
                                                  errors="ignore").strip() \
        if (chart_dir / "video_url.txt").exists() else ""
    if not url:
        url = meta.get("video_url", "").strip()
    if not url and manual_map:
        url = manual_map.get(matching.normalize(label), "")

    if args.print_candidates:
        cands, _slog = find_best_video(artist, song, chart_len, args) \
            if not url else ([], [])
        payload = {
            "chart": chart_dir.name,
            "rel": str(chart_dir),
            "label": label,
            "chart_len": chart_len,
            "manual_url": url,
            "candidates": [
                {"id": c.id, "title": c.title, "channel": c.channel,
                 "duration": c.duration, "views": c.views,
                 "score": round(c.score, 1), "url": c.url,
                 "reasons": c.reasons}
                for c in cands],
        }
        with PRINT_LOCK:
            print("CANDIDATES " + json.dumps(payload), flush=True)
        return ChartResult(label, "skip", "listed candidates")

    if url:
        step(log, f"Using the URL you saved: {url}")
        attempts = [Candidate(id="", title="(your URL)", channel="", duration=0)]
        attempts[0].url_override = url
    else:
        attempts, slog = find_best_video(artist, song, chart_len, args)
        log += slog
        if not attempts:
            finish(next_index(), "✗", label, ["no acceptable video found"],
                   problems, log, args)
            return ChartResult(label, "fail", "no acceptable video found on YouTube")

        # When the best match is not clearly right, leave it for a human
        # rather than downloading something plausible but wrong.
        best = attempts[0]
        if not args.no_review and best.score < args.confident_score:
            runner = f" (next best {attempts[1].score:.0f})" if len(attempts) > 1 else ""
            step(log, f"Unsure: best match only scored {best.score:.0f}, "
                      f"under {args.confident_score:.0f}{runner}.")
            offer_options(chart_dir, label, attempts, chart_len, problems)
            finish(next_index(), "?", label,
                   [f"unsure, best only scored {best.score:.0f}"], problems, log, args)
            return ChartResult(label, "review",
                               f"unsure, best match only scored {best.score:.0f}",
                               notes=["unsure which video is right"])

    if args.dry_run:
        best = attempts[0]
        log.append(f"   → {best.title[:66]}")
        if best.channel:
            log.append(f"     {best.channel[:34]} · {best.duration}s · "
                       f"score {best.score:.0f}")
        finish(next_index(), "·", label,
               [f"would use \"{best.title[:44]}\" (score {best.score:.0f})"],
               problems, log, args)
        return ChartResult(label, "skip", "dry run")

    # ---------- download, falling through the shortlist ----------
    downloaded = False
    used: Optional[Candidate] = None
    for attempt_no, cand in enumerate(attempts[:args.max_attempts], 1):
        target = getattr(cand, "url_override", None) or cand.url
        if attempt_no > 1:
            step(log, f"Trying the next candidate "
                      f"({attempt_no} of {min(len(attempts), args.max_attempts)})…")
        if cand.channel:
            mins, secs = divmod(int(cand.duration or 0), 60)
            step(log, f'Chose: "{cand.title[:58]}"')
            step(log, f"   by {cand.channel[:32]} · {mins}:{secs:02d} · "
                      f"{cand.tier_name} · match score {cand.score:.0f}")
        step(log, "Downloading…")
        if download_video(target, video, args, log, facts, problems):
            downloaded = True
            used = cand
            break

    if not downloaded:
        finish(next_index(), "✗", label, ["nothing usable found"], problems, log, args)
        notes = ["album art rejected"] if any("album art" in l for l in log) else []
        return ChartResult(label, "fail",
                           "every candidate failed or was album art", notes=notes)

    off, conf = (0, 0.0)
    if args.auto_sync:
        off, conf = autosync(chart_dir, video, args, log, facts, problems)

    notes = []
    if any("album art" in l for l in log):
        notes.append("album art rejected")
    if any("NO AUDIO" in l for l in log):
        notes.append("video has no audio track")

    # The top candidate can be confident while the one that actually survived
    # is not. Say so rather than quietly accepting a weak fallback.
    weak = (used is not None and used.channel
            and not args.no_review and used.score < args.confident_score)
    if weak:
        problems.append(f"fallback pick (score {used.score:.0f}) — worth checking "
                        f"it is the right video")
        notes.append("settled for a lower-confidence match")
        others = [c for c in attempts if c is not used and c.channel]
        if others:
            offer_options(chart_dir, label, others, chart_len, problems)

    synced = bool(args.auto_sync and conf >= args.min_conf)
    finish(next_index(), "?" if weak else "✓", label, facts, problems, log, args)
    return ChartResult(label, "review" if weak else "ok",
                       (f"downloaded a fallback match (score {used.score:.0f})"
                        if weak else
                        "downloaded" + (f", synced {off:+d} ms" if synced else "")),
                       downloaded=True, synced=synced, notes=notes)


# --------------------------- main ---------------------------

def preflight(args) -> List[str]:
    """Check the tools we depend on before touching 175 folders."""
    problems = []
    here = Path(__file__).resolve().parent
    if args.official_360:
        args.min_height = min(args.min_height, 360)

    # Pick a JS runtime. YouTube's signature/n challenges cannot be solved
    # without one, and the failure mode is silent: yt-dlp reports that only
    # storyboard images are available, or 403s at download time.
    if args.js_runtime == "auto":
        args.js_runtime = next((r for r in ("deno", "node") if shutil.which(r)), None)
        if args.js_runtime:
            print(f"js runtime: {args.js_runtime}")
        else:
            print("note: no JS runtime found. Install one with 'brew install deno' "
                  "or YouTube downloads will fail.")
    elif args.js_runtime in ("none", ""):
        args.js_runtime = None

    # Use cookies.txt sitting next to the script without being asked.
    if not args.cookies and not args.cookies_from_browser:
        default_cookies = here / "cookies.txt"
        if default_cookies.is_file():
            args.cookies = str(default_cookies)
            print(f"cookies: using {default_cookies.name}")
    if not shutil.which("ffmpeg"):
        problems.append("ffmpeg not found on PATH (brew install ffmpeg)")
    if not shutil.which("ffprobe"):
        problems.append("ffprobe not found on PATH (comes with ffmpeg)")
    code, out, _ = run(ytdlp_cmd() + ["--version"], timeout=60)
    if code != 0:
        problems.append(f"yt-dlp is not installed in {sys.executable}")
    else:
        print(f"yt-dlp {out.strip()} · python {sys.version.split()[0]}")
    if not (args.cookies or args.cookies_from_browser) and not args.sync_only:
        print("note: no cookies found. YouTube refuses media downloads to "
              "signed-out sessions. Put a cookies.txt next to this script, "
              "see the README.")
    if args.cookies and not Path(args.cookies).expanduser().is_file():
        problems.append(f"--cookies file not found: {args.cookies}")
    if args.js_runtime and not shutil.which(args.js_runtime):
        print(f"note: --js-runtime '{args.js_runtime}' not found on PATH; "
              f"YouTube will hide most formats")
        args.js_runtime = None
    return problems


def main() -> None:
    args = build_cli().parse_args()
    root = Path(args.songs_root).expanduser().resolve()
    if not root.is_dir():
        print(f"Songs folder not found: {root}")
        sys.exit(2)

    problems = preflight(args)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        sys.exit(2)

    manual_map = load_manual_map(args.manual_map)

    only_keys = set()
    if args.only_list:
        f = Path(args.only_list).expanduser()
        if not f.is_file():
            print(f"✗ --only-list file not found: {f}")
            sys.exit(2)
        only_keys = {matching.normalize(l) for l in read_text(f).splitlines()
                     if l.strip()}
        # Falling through with an empty set used to mean "process everything",
        # so one typo'd filename could re-download the entire library.
        if not only_keys:
            print(f"✗ --only-list file is empty: {f}")
            sys.exit(2)

    charts = sorted(p.parent for p in root.rglob("song.ini"))
    todo = []
    for c in charts:
        if not only_keys:
            todo.append(c)
            continue
        meta = parse_song_ini(c / "song.ini")
        label = matching.normalize(f"{meta.get('artist','')} - {meta.get('name','')}")
        rel = matching.normalize(str(c.relative_to(root)))
        if label in only_keys or rel in only_keys or matching.normalize(c.name) in only_keys:
            todo.append(c)

    if args.sync_only:
        job = "Re-syncing existing videos (nothing will be downloaded)"
    elif args.dry_run:
        job = "Dry run: showing what would happen, changing nothing"
    else:
        job = "Finding and downloading videos" + (
            ", then syncing them" if args.auto_sync else "")

    print()
    print("─" * 74)
    print(f"  {job}")
    print("─" * 74)
    print(f"  Songs folder   {root}")
    print(f"  Quality        {args.quality}  (H.264 preferred, minimum "
          f"{args.min_height}p)")
    print(f"  Working on     {len(todo)} of {len(charts)} charts, "
          f"{args.workers} at a time")
    if args.replace and not args.dry_run:
        existing = sum(1 for c in todo if (c / "video.mp4").exists())
        if existing:
            print(f"  Warning        Replace is on: {existing} existing "
                  f"video(s) will be overwritten")
    print("─" * 74)
    print()
    PROGRESS["total"] = len(todo)

    results: List[ChartResult] = []
    started = time.time()

    try:
        if args.workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = [ex.submit(process_chart, c, args, manual_map) for c in todo]
                for f in concurrent.futures.as_completed(futures):
                    results.append(f.result())
        else:
            for c in todo:
                results.append(process_chart(c, args, manual_map))
    except KeyboardInterrupt:
        print("\n  Stopped by you. Anything already finished has been saved.")
        sys.exit(130)

    print_summary(results, time.time() - started, args)


def print_summary(results: List[ChartResult], elapsed: float, args) -> None:
    """A plain-English account of what the run actually did."""
    mins, secs = divmod(int(elapsed), 60)
    took = f"{mins}m {secs:02d}s" if mins else f"{secs}s"

    done = [r for r in results if r.status == "ok"]
    review = [r for r in results if r.status == "review"]
    failed = [r for r in results if r.status == "fail"]
    skipped = [r for r in results if r.status == "skip"]
    downloaded = [r for r in results if r.downloaded]   # counts weak picks too
    synced = [r for r in results if r.synced]

    print()
    print("═" * 74)
    n = len(results)
    print(f"  FINISHED IN {took}".ljust(58)
          + f"{n} chart{'s' if n != 1 else ''}".rjust(15))
    print("═" * 74)

    def line(symbol, count, label, note=""):
        if count:
            print(f"   {symbol}  {count:>4}  {label:<26}{note}")

    def plural(n, one, many):
        return one if n == 1 else many
    line("✓", len(downloaded), plural(len(downloaded), "video downloaded", "videos downloaded"))
    line("✓", len(synced), plural(len(synced), "video synced", "videos synced"),
         "video_start_time written")
    line("·", len(skipped), "skipped", "nothing to do")
    line("?", len(review), plural(len(review), "needs your attention", "need your attention"),
         "unsure or a weak match")
    line("✗", len(failed), "could not be done")

    # What the filters actually caught
    # Notes that already have their own headline count would just repeat.
    REDUNDANT = {"unsure which video is right", "sync was not confident"}
    counts: Dict[str, int] = {}
    for r in results:
        for n in r.notes:
            if n in REDUNDANT:
                continue
            counts[n] = counts.get(n, 0) + 1
    if counts:
        print()
        print("   Along the way")
        for note, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"      · {n} × {note}")

    if review or failed:
        print()
        print("   Needs you")
        shown = 0
        for r in review + failed:
            if shown >= 25:
                print(f"      … and {len(review) + len(failed) - shown} more")
                break
            mark = "?" if r.status == "review" else "✗"
            print(f"      {mark} {r.label[:44]:<44} {r.detail}")
            shown += 1

    # Concrete next steps rather than raw numbers
    tips = []
    if review:
        tips.append(f"Press “Review {len(review)} unsure” to step through them. "
                    f"The options are already loaded, so there is no waiting.")
    if any("album art" in n for n in counts):
        tips.append("Album-art uploads were skipped automatically; those songs "
                    "may simply have no real music video.")
    if any("no audio" in n for n in counts):
        tips.append("Videos with no audio track cannot be synced. Re-download "
                    "them with Replace to fix.")
    if failed and not review:
        tips.append("For anything marked ✗, try “Choose video…” or paste a "
                    "YouTube URL by hand.")
    if tips:
        print()
        print("   What to do next")
        for t in tips:
            print(f"      · {t}")
    print("═" * 74)


if __name__ == "__main__":
    main()
