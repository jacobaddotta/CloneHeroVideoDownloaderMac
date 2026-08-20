#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scans a Clone Hero Songs folder and reports the state of every chart:
whether it has a video, whether that video is usable, and whether it has
been synced. Used by the GUI's song picker.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# Clone Hero decodes H.264 reliably; AV1/VP9 often stutter or refuse to play.
MARKUP_RE = re.compile(r"</?[a-zA-Z][^>]*>")

# H.264 is ideal. AV1 also plays on Apple Silicon (macOS decodes it, and this
# library's AV1 videos have never produced a Clone Hero decode warning), so it
# is not flagged. VP9 is not supported by Apple's video stack and is the codec
# behind every "Video unsupported by hardware" warning in the Clone Hero logs.
GOOD_CODECS = {"h264", "av1"}
BAD_CODECS = {"vp9", "vp8", "theora"}
# 720p looks fine behind the note highway, so only flag genuinely soft video.
# The bound is 700 rather than 720 because some uploads are 704p, which is
# 720p in all but name.
MIN_HEIGHT = 700


@dataclass
class ChartInfo:
    path: Path
    rel: str
    artist: str = ""
    name: str = ""
    num: int = 0          # 1-based position in alphabetical order, assigned by the GUI

    song_ok: bool = True
    song_problem: str = ""

    has_video: bool = False
    video_ok: bool = True          # False only when the video is genuinely broken
    video_problem: str = ""
    low_quality: bool = False      # plays fine, just soft. Not a fault.
    quality_note: str = ""
    height: int = 0
    codec: str = ""
    has_audio: bool = False

    synced: bool = False
    offset_ms: Optional[int] = None

    @property
    def label(self) -> str:
        if self.artist and self.name:
            return f"{self.artist} - {self.name}"
        return self.path.name

    @property
    def video_desc(self) -> str:
        if not self.has_video:
            return "—"
        bits = []
        if self.height:
            bits.append(f"{self.height}p")
        if self.codec:
            bits.append(self.codec)
        if not self.has_audio:
            bits.append("no audio")
        return " ".join(bits) or "present"

    @property
    def sync_desc(self) -> str:
        if not self.has_video:
            return "—"
        if self.offset_ms is None:
            return "not synced"
        return f"{self.offset_ms:+d} ms"

    @property
    def status(self) -> str:
        """One short human sentence describing what needs doing."""
        if not self.song_ok:
            return self.song_problem
        if not self.has_video:
            return "no video yet"
        if not self.video_ok:
            return self.video_problem
        if not self.synced:
            return ("not synced, " + self.quality_note) if self.low_quality \
                else "video ok, not synced"
        if self.low_quality:
            return self.quality_note
        return "ready"


def _parse_ini(path: Path) -> dict:
    meta = {}
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return meta
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ";")) or (s.startswith("[") and s.endswith("]")):
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            meta[k.strip().lower()] = v.strip()
    return meta


def _probe(video: Path) -> tuple:
    """(height, codec, has_audio, readable)"""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", str(video)],
            capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            return 0, "", False, False
        streams = json.loads(p.stdout).get("streams", [])
    except Exception:
        return 0, "", False, False
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        return 0, "", any(s.get("codec_type") == "audio" for s in streams), False
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return int(v.get("height") or 0), str(v.get("codec_name") or ""), has_audio, True


def scan_chart(chart_dir: Path, root: Path) -> ChartInfo:
    ini = chart_dir / "song.ini"
    info = ChartInfo(path=chart_dir, rel=str(chart_dir.relative_to(root)))

    if not ini.is_file():
        info.song_ok = False
        info.song_problem = "no song.ini"
        return info

    meta = _parse_ini(ini)
    info.artist = MARKUP_RE.sub("", meta.get("artist", "")).strip()
    info.name = MARKUP_RE.sub("", meta.get("name", "")).strip()
    if not info.artist or not info.name:
        info.song_ok = False
        info.song_problem = "song.ini missing artist or name"
    elif not meta.get("song_length", "").strip():
        info.song_ok = False
        info.song_problem = "song.ini missing song_length"

    raw = meta.get("video_start_time", "").strip()
    if raw:
        try:
            info.offset_ms = int(float(raw))
        except ValueError:
            info.offset_ms = None

    video = chart_dir / "video.mp4"
    if video.is_file():
        info.has_video = True
        if video.stat().st_size == 0:
            info.video_ok = False
            info.video_problem = "video.mp4 is empty"
        else:
            h, codec, has_audio, readable = _probe(video)
            info.height, info.codec, info.has_audio = h, codec, has_audio
            # A fault is something that stops the video working: it will not
            # play, or it cannot be synced. Low resolution is neither -- it
            # plays fine, it is just soft -- so it is tracked separately and
            # never shown as an error.
            problems = []
            if not readable:
                problems.append("unreadable or has no video stream")
            else:
                if not has_audio:
                    problems.append("no audio track, cannot be synced")
                if codec in BAD_CODECS:
                    problems.append(f"{codec}, Clone Hero cannot play it")
                elif codec and codec not in GOOD_CODECS:
                    problems.append(f"{codec}, may not play")
            if h and h < MIN_HEIGHT:
                info.low_quality = True
                info.quality_note = f"only {h}p, could be sharper"
            if problems:
                info.video_ok = False
                info.video_problem = "; ".join(problems)

    # Low resolution does not stop a chart being finished and synced.
    info.synced = bool(info.has_video and info.video_ok and info.offset_ms is not None)
    return info


def scan_library(root: Path,
                 progress: Optional[Callable[[int, int, str], bool]] = None
                 ) -> List[ChartInfo]:
    """Scan every chart. `progress(done, total, label)` may return False to
    cancel. Results are sorted by artist then song."""
    root = Path(root).expanduser().resolve()
    charts = sorted(p.parent for p in root.rglob("song.ini"))
    total = len(charts)
    out: List[ChartInfo] = []
    for i, c in enumerate(charts, 1):
        info = scan_chart(c, root)
        out.append(info)
        if progress and not progress(i, total, info.label):
            break
    out.sort(key=lambda c: (c.artist.lower(), c.name.lower()))
    return out
