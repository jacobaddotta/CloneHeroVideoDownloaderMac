#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube candidate scoring for Clone Hero charts.

The old scorer used four signals (artist in title, song in title, "official"
in title, duration < 90s) on a 0-7 point scale, and hard-banned any title
containing "live" -- which broke every live chart in the library.

This version scores on a ~150 point scale and, critically, uses the chart's
own `song_length` from song.ini. Duration is the single strongest signal
available for telling the real track apart from a remix, an edit, a live
cut, or an hour-long "extended" upload, and it was previously unused.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Titles that mean "this is not the studio track"
DISQUALIFYING = [
    "cover", "karaoke", "instrumental", "backing track", "reaction",
    "tutorial", "how to play", "lesson", "nightcore", "bass boosted",
    "sped up", "slowed", "reverb", "8 bit", "8-bit", "chiptune",
    "nightstep", "hardstyle remix", "daycore",
    "clone hero", "chart preview", "guitar hero", "rock band",
    "fan made", "fanmade", "ai cover", "parody", "in the style of",
]
# Titles that are probably fine but are second choice
# These describe uploads that are a STILL IMAGE with sound. They play fine,
# but the whole point here is a music video, so they rank as last resort.
SOFT_PENALTY = {
    "official audio": -8, "audio only": -8,
    "lyric video": -4, "lyrics": -4, "visualizer": -4,
    "full album": -60, "megamix": -50, "compilation": -50, "playlist": -40,
    "mix": -6,
}
OFFICIAL_TITLE = [
    "official music video", "official video", "official mv",
]

# Unicode lookalikes that show up in Clone Hero folder names, e.g. "AC／DC"
# because "/" is illegal in a path. The old matcher never undid these.
CHAR_FIXES = {
    "／": "/", "＼": "\\", "：": ":", "？": "?", "＊": "*",
    "＂": '"', "＜": "<", "＞": ">", "｜": "|",
    "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
}


# Clone Hero renders <color=#RRGGBB>…</color>, <b> and <i> markup in song.ini
# fields. It must be stripped before searching, or the query goes to YouTube
# with the raw tags in it.
MARKUP_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def strip_markup(s: str) -> str:
    return MARKUP_RE.sub("", s or "").strip()


# Scripts that mean the upload is almost certainly not the English original.
_NON_LATIN = (
    (0x0400, 0x04FF),   # Cyrillic
    (0x0590, 0x06FF),   # Hebrew, Arabic
    (0x0900, 0x097F),   # Devanagari
    (0x0E00, 0x0E7F),   # Thai
    (0x3040, 0x30FF),   # Hiragana, Katakana
    (0x3400, 0x4DBF),   # CJK extension
    (0x4E00, 0x9FFF),   # CJK
    (0xAC00, 0xD7AF),   # Hangul
)
# Phrases that explicitly mark a translated, dubbed or subtitled upload.
_FOREIGN_MARKERS = (
    "sub espanol", "subtitulado", "en espanol", "version en espanol",
    "legendado", "traducida", "traducao", "letra y musica",
    "traduction", "traduit", "vostfr", "version francaise",
    "deutsche version", "auf deutsch", "versione italiana",
    "japanese version", "japanese ver", "korean version", "korean ver",
    "chinese version", "chinese ver", "spanish version", "french version",
    "russian version", "turkce", "napisy pl", "po polsku",
)


def _non_latin_ratio(s: str) -> float:
    letters = [c for c in (s or "") if c.isalpha()]
    if not letters:
        return 0.0
    foreign = sum(1 for c in letters
                  if any(lo <= ord(c) <= hi for lo, hi in _NON_LATIN))
    return foreign / len(letters)


# Rhythm-game gameplay footage. These flood the results for exactly the songs
# that are in Clone Hero, and none of them are the music video.
_GAMEPLAY_PHRASES = (
    "full combo", "playthrough", "play through", "sightread", "sight read",
    "guitar hero", "rock band", "clone hero", "rocksmith", "beat saber",
    "gh sh", "gh wor", "ghwt", "gh3", "gh2", "rb3", "rb4",
    "expert guitar", "expert bass", "expert drums", "expert vocals",
    "gold stars", "no fail", "fret smasher", "chart preview", "custom chart",
    "full band", "pro drums", "drum chart", "bass chart", "guitar chart",
)
_GAMEPLAY_TOKENS = {"fc", "sightread", "playthrough", "ghsh", "ghwor"}
_PLATFORM_TOKENS = {"ps2", "ps3", "ps4", "xbox", "x360", "wii", "pc"}


def looks_like_gameplay(title: str) -> bool:
    """Is this someone playing the song in a rhythm game rather than the song?"""
    t = normalize(title)
    if any(ph in t for ph in _GAMEPLAY_PHRASES):
        return True
    tk = set(t.split())
    # "FC" alone is ambiguous, but paired with a difficulty or a console it is
    # unmistakably a gameplay capture.
    if "fc" in tk and (tk & _PLATFORM_TOKENS or "expert" in tk or "hard" in tk):
        return True
    # "FC #4919" run numbers only appear on gameplay captures. Match the raw
    # title, because normalising strips the "#" that makes it unambiguous --
    # without it, "FC Barcelona Anthem 2024" would be caught too.
    if re.search(r"\bFC\s*#\s*\d+", title or "", re.I):
        return True
    if _GAMEPLAY_TOKENS & tk and "expert" in tk:
        return True
    return False


def looks_non_english(title: str, reference: str = "") -> bool:
    """Is this upload a translated/foreign-language version?

    The chart itself is the reference: if the chart's own title is in another
    script then matching that script is correct, not a problem.
    """
    if _non_latin_ratio(reference) > 0.15:
        return False                       # the chart is not English either
    if _non_latin_ratio(title) > 0.15:
        return True
    t = normalize(title)
    return any(m in t for m in _FOREIGN_MARKERS)


def _fix_chars(s: str) -> str:
    for bad, good in CHAR_FIXES.items():
        s = s.replace(bad, good)
    return s


def normalize(s: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    s = _fix_chars(strip_markup(s or ""))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_parentheticals(s: str) -> str:
    """Remove (feat. X), [Official Video], etc. for core-title comparison."""
    return re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", s or "")


def core_title(s: str) -> str:
    s = _fix_chars(s or "")
    s = re.sub(r"\b(feat|ft|featuring|with)\b.*", " ", s, flags=re.I)
    return normalize(strip_parentheticals(s))


def tokens(s: str) -> List[str]:
    return [t for t in normalize(s).split() if t]


def token_coverage(needle: str, haystack: str) -> float:
    """Fraction of `needle`'s tokens that appear in `haystack`. 0..1."""
    nt = tokens(needle)
    if not nt:
        return 0.0
    ht = set(tokens(haystack))
    return sum(1 for t in nt if t in ht) / len(nt)


def is_live_chart(song_title: str, extra: str = "") -> bool:
    blob = f" {normalize(song_title)} {normalize(extra)} "
    return " live " in blob


@dataclass
class Candidate:
    id: str
    title: str
    channel: str
    duration: int = 0          # seconds
    views: int = 0
    score: float = 0.0
    tier: int = 0          # 0 music video, 1 lyric, 2 audio-only, 3 live
    foreign: bool = False  # a translated / non-English version
    reasons: List[str] = field(default_factory=list)

    @property
    def tier_name(self) -> str:
        return {0: "music video", 1: "lyric video",
                2: "audio only", 3: "live"}.get(self.tier, "?")

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


def score_candidate(cand: Candidate, artist: str, song: str,
                    chart_len_s: Optional[float] = None,
                    want_live: bool = False) -> Candidate:
    """Score one YouTube result against the chart. Higher is better.
    Anything below ~25 should be treated as 'no good match'."""
    t_norm = normalize(cand.title)
    c_norm = normalize(cand.channel)
    reasons: List[str] = []
    score = 0.0

    # --- hard disqualifiers -------------------------------------------------
    if looks_like_gameplay(cand.title):
        cand.score = -1000.0
        cand.reasons = ["disqualified: rhythm-game gameplay footage"]
        return cand

    for bad in DISQUALIFYING:
        if bad in t_norm:
            # "instrumental"/"cover" in the actual song name is not a
            # disqualifier (e.g. a track genuinely called "Cover Me").
            if bad in normalize(song):
                continue
            cand.score = -1000.0
            cand.reasons = [f"disqualified: '{bad}' in title"]
            return cand

    # --- artist match -------------------------------------------------------
    artist_cov = max(token_coverage(artist, cand.title),
                     token_coverage(artist, cand.channel))
    score += 30.0 * artist_cov
    reasons.append(f"artist {artist_cov:.0%} (+{30.0*artist_cov:.0f})")

    # --- song title match ---------------------------------------------------
    song_cov = token_coverage(core_title(song), cand.title)
    score += 45.0 * song_cov
    reasons.append(f"title {song_cov:.0%} (+{45.0*song_cov:.0f})")
    if song_cov < 0.5:
        score -= 25.0
        reasons.append("title barely matches (-25)")

    # --- duration match (the strongest single signal) -----------------------
    if chart_len_s and cand.duration:
        # Hard guard: "1 Hour Loop" uploads, full-album rips and 30s teasers
        # can otherwise survive on artist/title points alone.
        ratio = cand.duration / chart_len_s
        if ratio > 2.5 or ratio < 0.4:
            cand.score = -1000.0
            cand.reasons = [f"duration {cand.duration}s vs chart {chart_len_s:.0f}s "
                            f"(ratio {ratio:.1f}x) -- not the same recording"]
            return cand
        diff = abs(cand.duration - chart_len_s)
        if diff <= 2:
            pts = 40.0
        elif diff <= 5:
            pts = 32.0
        elif diff <= 10:
            pts = 20.0
        elif diff <= 20:
            pts = 5.0
        elif diff <= 45:
            pts = -20.0
        else:
            pts = -55.0
        score += pts
        reasons.append(f"duration off by {diff:.0f}s ({pts:+.0f})")
    elif cand.duration and cand.duration < 90:
        score -= 30.0
        reasons.append("suspiciously short (-30)")

    # --- official-ness ------------------------------------------------------
    # A trustworthy SOURCE matters more than the word "official" in a title,
    # because reuploaders put "Official Music Video" on everything.
    channel_is_artist = bool(c_norm) and token_coverage(artist, cand.channel) >= 0.9
    trusted_channel = "vevo" in c_norm or channel_is_artist or "official" in c_norm

    if "vevo" in c_norm:
        score += 25.0
        reasons.append("VEVO channel (+25)")
    if channel_is_artist:
        score += 18.0
        reasons.append("channel is the artist (+18)")
    elif "official" in c_norm:
        score += 8.0
        reasons.append("official-looking channel (+8)")

    if any(h in t_norm for h in OFFICIAL_TITLE):
        pts = 15.0 if trusted_channel else 3.0
        score += pts
        reasons.append(f"'official video' in title (+{pts:.0f})"
                       + ("" if trusted_channel else " -- untrusted channel, discounted"))

    # "- Topic" channels are YouTube's auto-generated static album art.
    if c_norm.endswith(" topic"):
        score -= 12.0
        reasons.append("auto-generated Topic channel, static image (-12)")

    # --- soft penalties -----------------------------------------------------
    for phrase, pts in SOFT_PENALTY.items():
        if phrase in t_norm:
            score += pts
            reasons.append(f"'{phrase}' ({pts:+.0f})")

    # A bare "Audio" upload is also a still image. Checked as a whole word
    # because the normalized title has no punctuation for "(Audio)" to match.
    if "audio" in set(tokens(cand.title)) and "official audio" not in t_norm \
            and "audio only" not in t_norm:
        score -= 32.0
        reasons.append("'audio' upload, static image (-32)")

    # --- live handling: match the chart, don't blanket-ban ------------------
    # Match "live" as a WHOLE WORD. Substring matching flagged "Stayin' Alive",
    # "Delivery Man" and "You're Not Alone" as live performances, which cost
    # the correct studio video 55 points.
    # A live take is a different recording and will never sync, but plenty of
    # them never use the word "live" -- e.g. "Performs X at the 2016 CMT Music
    # Awards". Catch the whole family.
    t_tokens = set(tokens(cand.title))
    PERFORMANCE_WORDS = {"live", "performs", "performing", "performance",
                         "unplugged", "acoustic", "concert", "festival",
                         "tour", "session", "sessions", "recital"}
    PERFORMANCE_PHRASES = ("tiny desk", "music awards", "awards", "on stage",
                           "in concert", "jools holland", "later with",
                           "saturday night live", "the tonight show",
                           "jimmy fallon", "jimmy kimmel", "colbert")
    cand_live = bool(t_tokens & PERFORMANCE_WORDS) or \
        any(ph in t_norm for ph in PERFORMANCE_PHRASES)
    if want_live and cand_live:
        score += 20.0
        reasons.append("live chart wants live video (+20)")
    elif want_live and not cand_live:
        score -= 15.0
        reasons.append("chart is live, video is not (-15)")
    elif not want_live and cand_live:
        score -= 20.0
        reasons.append("live video for a studio chart, wrong recording (-20)")

    # --- remix guard --------------------------------------------------------
    if "remix" in t_norm and "remix" not in normalize(song):
        score -= 35.0
        reasons.append("unwanted remix (-35)")
    if "extended" in t_norm and "extended" not in normalize(song):
        score -= 20.0
        reasons.append("extended version (-20)")

    # --- obscure reupload guard --------------------------------------------
    if not trusted_channel and cand.views < 50_000:
        score -= 15.0
        reasons.append("untrusted channel with few views (-15)")

    # --- popularity ---------------------------------------------------------
    if cand.views > 0:
        # log10: 10k views -> +2, 1M -> +8, 100M -> +14. Enough to separate a
        # canonical upload from a small reupload without ever outweighing
        # artist/title/duration agreement.
        pts = min(14.0, max(0.0, (math.log10(cand.views + 1) - 3.0) * 3.0))
        score += pts
        reasons.append(f"{cand.views:,} views (+{pts:.0f})")

    # --- what KIND of upload is this? ------------------------------------
    # Points cannot express "always prefer a real music video": a well-liked
    # lyric video will always out-point an obscure but genuine one. So the
    # kind is a hard tier, and points only order candidates within a tier.
    if want_live:
        cand.tier = 0 if cand_live else 1
    elif cand_live:
        cand.tier = 3                      # different recording, never syncs
    elif "lyric" in t_tokens or "lyrics" in t_tokens:
        cand.tier = 1
    elif ("official audio" in t_norm or "audio only" in t_norm
          or "audio" in t_tokens or "visualizer" in t_norm
          or c_norm.endswith(" topic")):
        cand.tier = 2
    else:
        cand.tier = 0                      # unmarked, treat as a music video

    # --- language ---------------------------------------------------------
    # Prefer the English original, but never rule out a foreign upload
    # entirely: for some songs it is the only thing on YouTube.
    cand.foreign = looks_non_english(cand.title, f"{artist} {song}")
    if cand.foreign:
        score -= 10.0
        reasons.append("not the English version (-10)")

    cand.score = score
    cand.reasons = [f"[{cand.tier_name}]"] + reasons
    return cand


def rank(cands: List[Candidate], artist: str, song: str,
         chart_len_s: Optional[float] = None,
         want_live: bool = False) -> List[Candidate]:
    scored = [score_candidate(c, artist, song, chart_len_s, want_live)
              for c in cands]
    scored = [c for c in scored if c.score > -900]
    # Tier first (music video before lyric before audio before live), then
    # English before translated, then score. Both are hard preferences: a
    # popular foreign upload must not displace the English original.
    scored.sort(key=lambda c: (c.tier, c.foreign, -c.score))
    return scored


def build_queries(artist: str, song: str, want_live: bool = False) -> List[str]:
    """Search queries, best first.

    Kept deliberately short: the YouTube Data API charges 100 quota units per
    search and the default daily allowance is 10,000, so every extra query
    costs real throughput (4 queries = only ~24 songs/day).
    """
    a = _fix_chars(strip_markup(artist)).strip()
    s = _fix_chars(strip_markup(song)).strip()
    if want_live:
        return [f"{a} {s} live", f"{a} {s}"]
    return [f"{a} {s} official music video", f"{a} {s}"]
