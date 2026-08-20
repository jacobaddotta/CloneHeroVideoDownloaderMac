#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cheap video previews built from YouTube's own storyboard thumbnails.

YouTube generates a sprite sheet of small frames spanning every video (it is
what you see when you scrub the progress bar). One is a few tens of KB and
covers the whole runtime, so it makes a far better "is this the right video?"
check than watching from the start: a still album cover, a live show and a
real music video are obvious at a glance.

No extra dependencies. yt-dlp fetches the sheet, ffmpeg turns it into PNG
tiles that Tk can display directly.
"""

from __future__ import annotations

import email
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

# Preference order: big enough to read, small enough to fetch instantly.
STORYBOARD_FORMATS = ["sb1", "sb2", "sb0"]


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)


def _png(rgb) -> bytes:
    """Encode an (h, w, 3) uint8 array as PNG.

    Doing this in-process instead of shelling out to ffmpeg per tile turns
    ~100 subprocess launches into zero, which is the difference between the
    preview taking eight seconds and appearing instantly.
    """
    import struct
    import zlib

    h, w, _ = rgb.shape
    raw = bytearray()
    for row in rgb:
        raw.append(0)                      # filter type 0 for each scanline
        raw.extend(row.tobytes())

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def _decode_sheet(sheet: Path):
    """Decode one sprite sheet to an (h, w, 3) uint8 array."""
    import numpy as np
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v",
                "-show_entries", "stream=width,height", "-of", "csv=p=0",
                str(sheet)], text=True).stdout.strip()
    try:
        w, h = (int(x) for x in out.split(","))
    except ValueError:
        return None, 0, 0
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(sheet),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    if len(raw) != w * h * 3:
        return None, 0, 0
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3), w, h


def fetch_frames(video_url: str, ytdlp_args: List[str],
                 out_height: int = 180, limit: int = 120) -> List[bytes]:
    """Return PNG frames spanning the whole video, earliest first.

    `ytdlp_args` should carry cookies / js runtime / remote components so the
    call matches how the rest of the tool talks to YouTube.
    """
    tmp = Path(tempfile.mkdtemp(prefix="chvd_preview_"))
    try:
        board = None
        tile_w = tile_h = 0
        for fmt in STORYBOARD_FORMATS:
            cmd = [sys.executable, "-m", "yt_dlp", "-f", fmt,
                   "-o", str(tmp / "board.%(ext)s"),
                   "--no-warnings", "--quiet", "--no-playlist",
                   # --print implies --simulate in yt-dlp, so without this it
                   # reports the tile size and downloads nothing.
                   "--no-simulate",
                   "--print", "%(width)s %(height)s"] + ytdlp_args + [video_url]
            res = _run(cmd, text=True)
            found = list(tmp.glob("board.*"))
            if res.returncode == 0 and found:
                board = found[0]
                try:
                    w, h = res.stdout.strip().split()[:2]
                    tile_w, tile_h = int(w), int(h)
                except (ValueError, IndexError):
                    tile_w = tile_h = 0
                break
        if board is None:
            return []

        # The mhtml wrapper holds one JPEG per sprite sheet.
        msg = email.message_from_bytes(board.read_bytes())
        sheets = [p.get_payload(decode=True) for p in msg.walk()
                  if p.get_content_maintype() == "image"]
        if not sheets:
            return []

        import numpy as np

        frames: List[bytes] = []
        for i, data in enumerate(sheets):
            sheet = tmp / f"s{i}.jpg"
            sheet.write_bytes(data)
            img, w, h = _decode_sheet(sheet)
            if img is None:
                continue
            tw = tile_w if tile_w > 0 else w // 10
            th = tile_h if tile_h > 0 else h // 10
            cols, rows = max(1, w // tw), max(1, h // th)
            zoom = max(1, round(out_height / th))
            for r in range(rows):
                for c in range(cols):
                    if len(frames) >= limit:
                        return frames
                    tile = img[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
                    if tile.shape[0] != th or tile.shape[1] != tw:
                        continue
                    if tile.max() == 0:        # trailing blank tiles
                        continue
                    if zoom > 1:               # nearest-neighbour upscale
                        tile = tile.repeat(zoom, axis=0).repeat(zoom, axis=1)
                    frames.append(_png(np.ascontiguousarray(tile)))
        return frames
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
