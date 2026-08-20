#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audio sync engine for Clone Hero video alignment.

Replaces the old brute-force sample correlation with FFT-based normalized
cross-correlation over onset-strength envelopes.

Why envelopes instead of raw samples:
  A chart's audio and a music video's audio are different masters. Raw
  waveforms decorrelate almost immediately (different EQ, compression,
  stereo fold-down, encoder). What DOES survive is the rhythmic pattern of
  note onsets. Correlating onset envelopes is both far more accurate and
  ~1000x faster than the old per-offset Python loop.

Clone Hero convention for song.ini `video_start_time` (milliseconds):
  positive -> skip INTO the video (video has an intro before the music)
  negative -> delay the video (chart has silence before the music)

That is exactly "the timestamp in the video where chart time zero lands",
which is what estimate_offset_ms() returns.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

SR = 16000          # analysis sample rate
HOP = 160           # 10 ms per envelope frame
FPS = SR / HOP      # 100 envelope frames per second

# Stems that make up a Clone Hero chart's audio. `crowd` is deliberately
# excluded (audience noise is not in the music video and only adds noise);
# `preview` is a clip, not the song.
STEM_NAMES = [
    "song", "guitar", "bass", "rhythm", "keys", "vocals",
    "drums", "drums_1", "drums_2", "drums_3", "drums_4",
]
AUDIO_EXTS = [".ogg", ".opus", ".mp3", ".wav", ".m4a", ".flac"]


@dataclass
class SyncResult:
    offset_ms: int          # value to write to video_start_time
    confidence: float       # 0..1 peak normalized correlation
    sharpness: float        # peak height vs. background; >2 is a distinct peak
    chart_dur: float
    video_dur: float
    note: str = ""

    @property
    def interpretation(self) -> str:
        if self.offset_ms > 0:
            return f"Video has a ~{self.offset_ms/1000:.2f}s intro -> skip INTO the video"
        if self.offset_ms < 0:
            return f"Chart has ~{abs(self.offset_ms)/1000:.2f}s of lead-in -> delay the video"
        return "Video and chart already aligned"


# --------------------------- decoding ---------------------------

def decode_mono(path: Path, sr: int = SR, max_seconds: Optional[float] = None) -> np.ndarray:
    """Decode any media file to a mono float32 array via ffmpeg, straight off
    a pipe. No temp files, no _tmp_sync directory left behind."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
            "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found on PATH")
    if proc.returncode != 0 or not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def find_stems(chart_dir: Path) -> List[Path]:
    """All audio stems in a chart folder, in a stable order."""
    found = []
    for name in STEM_NAMES:
        for ext in AUDIO_EXTS:
            p = chart_dir / f"{name}{ext}"
            if p.exists():
                found.append(p)
                break
    return found


def decode_chart_audio(chart_dir: Path, sr: int = SR,
                       max_seconds: Optional[float] = None) -> np.ndarray:
    """Decode a chart's audio as the SUM of all its stems.

    This matters a lot. The old code preferred `guitar.ogg` first, which is a
    single isolated instrument stem -- it correlates badly against a full
    music video mix. Summing the stems reconstructs the full song, which is
    what the video actually contains.
    """
    stems = find_stems(chart_dir)
    if not stems:
        # last resort: any audio file that isn't a preview clip
        stems = [p for p in sorted(chart_dir.iterdir())
                 if p.suffix.lower() in AUDIO_EXTS and "preview" not in p.stem.lower()]
    if not stems:
        return np.zeros(0, dtype=np.float32)

    # A lone song.ogg is already the full mix; skip the summing work.
    if len(stems) == 1:
        return decode_mono(stems[0], sr, max_seconds)

    mixed = None
    for stem in stems:
        x = decode_mono(stem, sr, max_seconds)
        if x.size == 0:
            continue
        if mixed is None:
            mixed = x
        else:
            n = max(mixed.size, x.size)
            if mixed.size < n:
                mixed = np.pad(mixed, (0, n - mixed.size))
            if x.size < n:
                x = np.pad(x, (0, n - x.size))
            mixed = mixed + x
    if mixed is None:
        return np.zeros(0, dtype=np.float32)
    peak = np.max(np.abs(mixed))
    if peak > 0:
        mixed = mixed / peak
    return mixed.astype(np.float32)


# --------------------------- onset envelope ---------------------------

def onset_envelope(x: np.ndarray, sr: int = SR, hop: int = HOP) -> np.ndarray:
    """Spectral-flux onset strength: the rhythmic 'fingerprint' of the audio.

    Robust to EQ, loudness and codec differences between a chart's audio and
    a YouTube video's audio, because it keys on WHEN energy rises, not on the
    absolute spectrum.
    """
    if x.size < sr:
        return np.zeros(0, dtype=np.float32)

    n_fft = 1024
    n_frames = 1 + (x.size - n_fft) // hop
    if n_frames < 8:
        return np.zeros(0, dtype=np.float32)

    # Frame the signal without copying (stride tricks), then window + rFFT.
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n_frames, n_fft),
        strides=(x.strides[0] * hop, x.strides[0]), writeable=False)
    window = np.hanning(n_fft).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * window, axis=1))

    # Log compression keeps quiet onsets visible next to loud ones.
    spec = np.log1p(spec * 20.0)

    # Half-wave rectified spectral difference = energy going UP only.
    flux = np.diff(spec, axis=0)
    np.maximum(flux, 0.0, out=flux)
    env = flux.sum(axis=1)

    # Subtract a local median to kill slow loudness drift between masters.
    if env.size > 101:
        k = 101
        pad = np.pad(env, (k // 2, k // 2), mode="edge")
        strides = np.lib.stride_tricks.as_strided(
            pad, shape=(env.size, k), strides=(pad.strides[0], pad.strides[0]),
            writeable=False)
        env = env - np.median(strides, axis=1)
        np.maximum(env, 0.0, out=env)

    return env.astype(np.float32)


# --------------------------- correlation ---------------------------

def _normalized_xcorr(a: np.ndarray, v: np.ndarray, min_overlap: int):
    """Normalized cross-correlation of `v` slid across `a`, via FFT.

    Returns (lags, r) where r[i] is the Pearson-style correlation over the
    OVERLAPPING region only at lag lags[i]. Normalizing per-lag by the actual
    overlap energy is what stops tiny 3-frame overlaps at the edges from
    producing bogus 1.0 correlations.
    """
    na, nv = a.size, v.size
    n = 1 << int(np.ceil(np.log2(na + nv)))

    A = np.fft.rfft(a, n)
    V = np.fft.rfft(v, n)
    # Negative lags live at the END of the length-n circular result. They must
    # be taken from the FULL array -- slicing to na+nv-1 first grabs positive
    # lags na..na+nv-2 instead, which silently makes every negative offset
    # unfindable.
    full = np.fft.irfft(A * np.conj(V), n)
    raw = np.concatenate([full[n - (nv - 1):], full[:na]]) if nv > 1 else full[:na]

    # Overlap-dependent energy of `a` under the sliding window of `v`,
    # computed once with cumulative sums.
    a2 = np.concatenate([[0.0], np.cumsum(a.astype(np.float64) ** 2)])
    a1 = np.concatenate([[0.0], np.cumsum(a.astype(np.float64))])
    v2_total = float(np.sum(v.astype(np.float64) ** 2))
    v1_total = float(np.sum(v.astype(np.float64)))

    lags = np.arange(-(nv - 1), na)
    r = np.zeros(lags.size, dtype=np.float64)

    for i, lag in enumerate(lags):
        lo = max(0, lag)
        hi = min(na, lag + nv)
        cnt = hi - lo
        if cnt < min_overlap:
            continue
        vlo = lo - lag
        vhi = hi - lag
        sa = a1[hi] - a1[lo]
        saa = a2[hi] - a2[lo]
        if vlo == 0 and vhi == nv:
            sv, svv = v1_total, v2_total
        else:
            sv = float(np.sum(v[vlo:vhi]))
            svv = float(np.sum(v[vlo:vhi].astype(np.float64) ** 2))
        num = raw[i] - sa * sv / cnt
        den = np.sqrt(max(saa - sa * sa / cnt, 1e-12) * max(svv - sv * sv / cnt, 1e-12))
        r[i] = num / den if den > 0 else 0.0

    return lags, r


def _locate_window(seg_env: np.ndarray, video_env: np.ndarray,
                   start_frame: int, max_lag_frames: int):
    """Find where one chart window sits inside the video.

    Returns (offset_frames, peak_r) or (None, 0.0)."""
    if seg_env.size < 50:
        return None, 0.0
    lags, r = _normalized_xcorr(video_env, seg_env,
                                min_overlap=int(seg_env.size * 0.9))
    # The window sits at `start_frame` in the chart, so a video position of
    # `lag` implies an overall offset of lag - start_frame. Restrict the
    # search to offsets that are physically plausible.
    lo = start_frame - max_lag_frames
    hi = start_frame + max_lag_frames
    mask = (lags >= lo) & (lags <= hi)
    if not np.any(mask):
        return None, 0.0
    lags_m, r_m = lags[mask], r[mask]
    i = int(np.argmax(r_m))
    return int(lags_m[i]) - start_frame, float(r_m[i])


def estimate_offset_ms(chart_audio: np.ndarray, video_audio: np.ndarray,
                       sr: int = SR, max_lag_s: float = 60.0,
                       n_windows: int = 8) -> SyncResult:
    """Find where chart time zero lands inside the video.

    Rather than correlating the whole chart at once -- which can lock onto a
    repeated chorus and confidently return a wrong answer -- this takes
    several short windows from DIFFERENT points in the song, locates each one
    independently, and only trusts an answer that the windows agree on.

    That agreement IS the confidence measure: if a video is the wrong
    recording entirely, the windows scatter and nothing gets written.
    """
    chart_dur = chart_audio.size / sr
    video_dur = video_audio.size / sr

    if chart_audio.size < sr * 20 or video_audio.size < sr * 20:
        return SyncResult(0, 0.0, 0.0, chart_dur, video_dur,
                          "audio too short to analyze")

    ce = onset_envelope(chart_audio, sr)
    ve = onset_envelope(video_audio, sr)
    if ce.size < 200 or ve.size < 200:
        return SyncResult(0, 0.0, 0.0, chart_dur, video_dur,
                          "not enough onset detail to analyze")

    max_lag_frames = int(max_lag_s * FPS)
    win = int(24 * FPS)                      # 24-second windows
    usable = ce.size - win
    if usable <= 0:
        win = max(int(10 * FPS), ce.size // 2)
        usable = max(1, ce.size - win)

    # Spread the windows across the middle of the chart, avoiding the very
    # start (often silence or a count-in) and the very end (fade-outs).
    starts = np.linspace(usable * 0.05, usable * 0.95,
                         max(3, n_windows)).astype(int)

    votes: List[tuple] = []
    for s in starts:
        seg = ce[s:s + win]
        if seg.size < win * 0.8 or float(np.sum(seg)) <= 0:
            continue
        off, r = _locate_window(seg, ve, int(s), max_lag_frames)
        if off is not None and r > 0.15:
            votes.append((off, r))

    if len(votes) < 3:
        return SyncResult(0, 0.0, 0.0, chart_dur, video_dur,
                          "could not locate enough of the song in the video")

    offsets = np.array([v[0] for v in votes], dtype=float)
    median = float(np.median(offsets))

    # Windows agreeing to within 150 ms of the median are treated as one vote
    # for the same answer.
    tol = 0.150 * FPS
    keep = np.abs(offsets - median) <= tol
    n_agree = int(np.sum(keep))
    if n_agree < 2:
        return SyncResult(0, 0.0, 0.0, chart_dur, video_dur,
                          "windows disagreed -- video is probably a different "
                          "recording")

    agreed = offsets[keep]
    mean_r = float(np.mean([votes[i][1] for i in range(len(votes)) if keep[i]]))
    offset_frames = float(np.mean(agreed))
    offset_ms = int(round(offset_frames * 1000.0 / FPS))

    # confidence blends "how well did it match" with "how many sections agreed"
    agreement = n_agree / len(votes)
    confidence = mean_r * agreement
    # sharpness is now simply the number of independent sections that agreed,
    # so --min-sharpness 4 reads as "at least 4 parts of the song must agree".
    sharpness = float(n_agree)

    return SyncResult(offset_ms, min(1.0, max(0.0, confidence)), sharpness,
                      chart_dur, video_dur)


def sync_chart_to_video(chart_dir: Path, video_path: Path,
                        max_lag_s: float = 60.0) -> SyncResult:
    """Convenience wrapper: decode both sides and estimate the offset."""
    # Analyze a generous window: enough song to be distinctive, plus headroom
    # on the video side to absorb a long intro.
    analyze_s = 180.0
    chart = decode_chart_audio(chart_dir, SR, analyze_s)
    video = decode_mono(video_path, SR, analyze_s + max_lag_s)
    if chart.size == 0:
        return SyncResult(0, 0.0, 0.0, 0.0, video.size / SR, "no chart audio found")
    if video.size == 0:
        return SyncResult(0, 0.0, 0.0, chart.size / SR, 0.0, "could not decode video audio")
    return estimate_offset_ms(chart, video, SR, max_lag_s)
