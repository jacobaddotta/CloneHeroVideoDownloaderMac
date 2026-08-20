# Clone Hero Video Downloader for Mac

Finds a music video for each chart in your Songs folder, downloads it as
`video.mp4`, and writes the `video_start_time` that lines the video up with
the chart.

macOS only. Built and tested on Apple Silicon.

## Install

Install the two command-line tools it depends on:

```bash
brew install ffmpeg deno
```

Then clone the repo:

```bash
git clone https://github.com/jacobaddotta/CloneHeroVideoDownloaderMac.git
```

Double-click **`Clone Hero Video Downloader for Mac.app`** (or `start.command`).
The first launch builds its own Python environment, so it takes a few extra
seconds. Open **⚙ Settings…** and point *Songs Folder* at your Clone Hero
`Songs` directory.

`ffmpeg` does the audio and video work. `deno` runs YouTube's JavaScript
challenges -- without it YouTube hides almost every format and downloads fail.

## You will also need a cookies file

YouTube refuses media downloads to signed-out sessions. Install a
"Get cookies.txt" browser extension, sign in to YouTube, export cookies for
`youtube.com`, and save the file as `cookies.txt` in the project folder. It
is picked up automatically.

`cookies.txt` is in `.gitignore`. Treat it like a password: anyone holding it
can act as your YouTube account. Re-export it when downloads start failing,
which is normal every few weeks.

## Running it from the command line

```bash
.venv/bin/python VideoDownload.py ~/"Clone Hero/Songs" --auto-sync
```

Everything Python-side lives in `.venv/` and is installed on first launch.

## About the JavaScript runtime

YouTube signs its media URLs with a JavaScript challenge. Without a runtime
installed, yt-dlp can only see storyboard thumbnails and every download fails,
which is why `deno` is in the install step.

The tool also passes `--remote-components ejs:github` by default. That tells
yt-dlp to fetch its challenge-solver script from the yt-dlp project at runtime
and run it in deno. It is yt-dlp's own official mechanism and nothing
currently works without it, but it does mean code is fetched from the internet
and executed. If you would rather not, run with `--remote-components ""` and
accept that downloads will fail.

## Dark theme

The window uses a dark theme in the style of modern Clone Hero song browsers.
macOS ships ttk's "aqua" theme, which draws native controls and ignores any
colour you set, so the app switches to "clam", which is fully styleable. The
palette lives in `theme.py` -- change the constants at the top of that file
and the whole app follows, dialogs included.

## Using the window

The song list fills the top of the window, the manual override sits directly
under it, and the run buttons are below that. Everything else is behind
**⚙ Settings…**.

Each chart is colour coded:

| Look | Meaning |
|---|---|
| Green fill | Synced and ready |
| Amber fill | Plays fine, just low resolution |
| Green text | Has a video file |
| Red fill | Genuinely broken: will not play, or has no audio to sync against |
| Red text | Problem with song.ini |
| Plain | No video yet |

Red means broken, never just soft. A 360p video plays perfectly well, so it
shows amber and is left out of **Problems**; use the **Low quality** button
if you want to re-download those sharper.

**Checking songs.** Click a box to tick it. Click one box, then hold Shift
and click another further up or down, and everything in between takes the
same state, so you can tick or untick a long run in two clicks. **All /
None / Invert / Missing video / Problems / Not synced** check a whole group.
Run always works on the checked songs and refuses if none are checked.

**Double-click** a song to open its folder in Finder.

**Reviewing the unsure ones.** When a run cannot tell which video is right it
skips that chart and lists the alternatives rather than guessing. Afterwards a
**Review N unsure** button appears next to Run. It steps through each one,
showing the candidates with thumbnails, lengths checked against your chart,
and a Watch on YouTube link. The options are carried over from the run, so
nothing is searched again and the picker opens instantly.

Press **Use this one →** or **Skip this one →** to work through the whole
list. Nothing downloads while you are choosing. At the end it shows what you
picked and asks once whether to download them all together.

**Sorting.** Every column sorts, click a heading to sort and click again to
reverse. The **#** column is fixed at the alphabetical position, so a song
keeps its number whichever way you sort, and sorting by **#** puts the list
back in A-Z order. Ticks follow the songs through a re-sort.

**Previewing a video.** The picker plays a preview of the whole video built
from YouTube's own scrub-bar thumbnails: a few tens of KB, loaded in about a
second, covering the entire runtime. Press play or drag the scrub bar. A
still album cover, a live show and a real music video are obvious instantly,
which is faster than opening YouTube and watching from the start. **Open on
YouTube** is still there when you want the real thing.

**Manual override.** Highlight one row and the override panel below the list
targets that song, showing its current `video_url` and timing. Save a
YouTube URL to force a specific video, save a timing to set
`video_start_time` by hand, or use **Download this** / **Re-sync this** to
act on that one chart. Highlight more than one row and the panel switches
off, so it can never write to the wrong song.

**Settings.** ⚙ Settings… opens a dialog. Nothing takes effect until you
press **Apply**; closing it or pressing Cancel discards every change. Apply
also validates first, so a bad folder path or a non-numeric worker count is
rejected rather than saved. Hover any label for a description.

## Picking the right video

Two things decide what gets downloaded.

**Album art is rejected automatically.** A lot of songs have no real music
video, so YouTube serves an "art track": the album cover as a still image with
the song playing over it. Nothing in the title or channel reliably marks one,
so the tool measures the picture instead. Real music videos score 30 to 50 on
frame-to-frame movement; a still image scores under 0.1. Anything under
`--min-motion` (default 2.0) is thrown away and the next candidate is tried,
up to `--max-attempts` (default 4). Use `--allow-static` to keep them.

**Music videos always come first.** Candidates are grouped into tiers before
scoring is considered, because points alone cannot express a hard preference:
a popular lyric video will always out-point an obscure but genuine music
video. The tiers are:

1. Music video (or an unmarked upload)
2. Lyric video
3. Audio-only / visualizer
4. Live performance

Every candidate in tier 1 is tried before anything in tier 2, so a lyric
video is only ever used when no music video works. A live take sits last for
a studio chart because it is a different recording and will never sync. For a
chart that is itself a live version, that order flips.

Within a tier, the score decides.

**Uncertain matches wait for you.** If the best candidate scores below
`--confident-score` (default 95) the chart is skipped and reported as "needs
your choice" rather than guessing. Pass `--no-review` to always take the best
match instead.

To choose by hand, highlight the song and press **🎬 Choose video…**. It
searches YouTube and shows every candidate with its score, channel, length
(flagged ✓ / ~ / ✗ against the chart's own length), view count and a
thumbnail, plus why it scored what it did. **▶ Watch on YouTube** opens it in
your browser so you can check before committing. Then **Download this one**,
or **Just save the URL** to record your pick for later.

## Reading the output

While a run is going, a single status line above the log keeps being
rewritten with whatever is happening right now:

```
[12/89] Downloading…  ·  Chris Stapleton - Parachute
```

The log itself gets **one line per chart**, so it stays readable across a
long run:

```
[ 12/89] ✓ Chris Stapleton - Parachute  ·  1080p H264  ·  synced -1750 ms
[ 13/89] ? Some Other Song  ·  fallback pick (score 80)
         skipped an album-art upload (not a real video)
[ 14/89] ✗ Third Song  ·  nothing usable found
```

The marker is the outcome: **✓** done, **?** worth a look, **·** skipped,
**✗** could not be done. Extra lines only appear when something actually went
wrong. Pass `--verbose` to log every step instead.

On the command line the same status rewrites itself in place; through the GUI
it feeds the status bar.

The end-of-run summary counts what happened, lists every chart that needs you
with the reason, and suggests what to do next. A chart is flagged **?** when
the tool was unsure which video was right, when the sync was not confident,
or when the top candidate was rejected and it settled for a weaker fallback.

## English versions preferred

Translated, dubbed and subtitled uploads are ranked below the English
original, whatever their view count, using both the script the title is
written in and phrases like "versión en español" or "legendado". If the only
upload for a song is a foreign one it is still used. A chart whose own title
is not in English is not penalised, since matching it is then correct.

## About 4K

**Stick to `best1080`.** YouTube publishes H.264 only up to 1080p; every
1440p and 2160p stream is VP9 or AV1. VP9 does not play in Clone Hero on a
Mac at all, so asking for 4K risks pulling a video that will not play, for a
picture that sits behind the note highway anyway.

`best1440` and `best2160` exist if you want them, but pair them with
`--transcode` to get back to H.264, at the cost of a slow re-encode and a
much larger file.

## Useful flags

| Flag | What it does |
|---|---|
| `--auto-sync` | Work out and write `video_start_time` |
| `--sync-only` | Re-sync existing videos, no downloading, no network |
| `--replace` | Re-download even where `video.mp4` already exists |
| `--dry-run` | Show what it would pick and do, change nothing |
| `--verbose` | Show why each candidate scored what it did |
| `--transcode` | Re-encode to H.264 if the download is not already H.264 |
| `--cookies FILE` | Cookies file. Defaults to `cookies.txt` in this folder |
| `--min-height N` | Reject downloads below this height (default 360) |
| `--min-motion N` | Reject still-image uploads (default 2.0) |
| `--allow-static` | Keep still-image uploads |
| `--confident-score N` | Below this, leave the chart for you to choose (default 95) |
| `--no-review` | Never defer, always take the best match |
| `--max-attempts N` | Candidates to try per chart (default 4) |
| `--workers N` | Charts in parallel (default 4) |
| `--js-runtime` | auto/deno/node/none (default auto, prefers deno) |
| `--min-score N` | Reject the best match below this score (default 40) |
| `--min-conf N` | Minimum sync confidence, 0-1 (default 0.30) |
| `--min-sharpness N` | How many song sections must agree on the offset (default 4) |

Always try `--dry-run --verbose` first on a new batch. It shows the exact
video it would pick for each chart without touching anything.

## How the sync works

`video_start_time` is where chart time zero lands inside the video:

* **positive** – the video has an intro, so skip that far into it
* **negative** – the chart has a lead-in, so delay the video

The tool takes eight short windows from different points in the chart's
audio, locates each one independently inside the video, and only writes an
offset the windows agree on. If they scatter, the video is a different
recording and nothing is written. That is why some charts are skipped —
that is the tool being honest, not failing.

Chart audio is the **sum of all stems** (`song`, `guitar`, `bass`, `drums`,
…), not just one, so it matches what a music video actually contains.

## Things worth knowing

* **Codecs:** H.264 is ideal. AV1 plays fine on Apple Silicon. **VP9 does not
  play** in Clone Hero on macOS -- it is behind every "Video unsupported by
  hardware" warning in the Clone Hero logs. VP9 files are flagged as problems;
  AV1 is not.
* **Videos with no audio track can never be auto-synced.** The sync works by
  matching the video's audio against the chart's, so re-download those with
  Replace turned on.
* **Low resolution is not a fault.** Some older music videos only exist at
  360-480p, so `--min-height` defaults to 360 and you get a video rather than
  nothing. Raise it to 720 to be strict. These show amber, not red.
* **No YouTube API key is needed.** Searching goes through yt-dlp, which has
  no key and no daily quota.

## Files

| File | Purpose |
|---|---|
| `VideoDownload.py` | The engine: search, download, sync, `song.ini` writing |
| `syncengine.py` | Audio alignment |
| `matching.py` | Scores YouTube results against a chart |
| `gui.py` | The window and the song picker |
| `library.py` | Scans your Songs folder for the picker |
| `preview.py` | Builds video previews from YouTube storyboards |
| `theme.py` | Colours and widget styling |
| `_backup_2026-08-19/` | Your original files, plus superseded docs |
| `launch-gui.sh` | Shared launcher used by the app and `start.command` |
| `_backup_2026-08-19/` | Your previous versions, kept as a fallback |

## Credits

Inspired by [stripedew/CloneHeroVideoDownloader](https://github.com/stripedew/CloneHeroVideoDownloader),
which is where the idea of pulling music videos into Clone Hero charts and
syncing them automatically came from.

This is a separate, independent rewrite rather than a fork. None of the
original source is used: the downloader, sync engine, YouTube matcher,
library scanner, previews, theming and the whole interface were written from
scratch. If you want the original, lighter script, go and give theirs a look.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) and
[ffmpeg](https://ffmpeg.org/).

## Licence

MIT, see [LICENSE](LICENSE).
