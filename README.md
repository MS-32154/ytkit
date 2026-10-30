# ytkit

[![CI](https://github.com/MS-32154/ytkit/actions/workflows/ci.yml/badge.svg)](https://github.com/MS-32154/ytkit/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/MS-32154/ytkit?include_prereleases&sort=semver)](https://github.com/MS-32154/ytkit/releases)
[![Python](https://img.shields.io/badge/python-3.14%20%7C%203.15-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A terminal toolkit for pulling **video, audio, captions, transcripts and metadata**
off YouTube — with a full-screen TUI, a guided wizard, and a scriptable CLI.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [Textual](https://textual.textualize.io/).

---

## Why it exists

yt-dlp is superb but has hundreds of flags. ytkit puts a real interface on the
parts people actually use, adds post-processing yt-dlp doesn't do (readable
transcripts, Markdown metadata summaries), and never hides what it's doing —
every screen can show you the exact `yt-dlp` command it will run.

## Install

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/). `ffmpeg` is needed
for merging, audio extraction and embedding.

```bash
git clone https://github.com/MS-32154/ytkit && cd ytkit
uv sync
uv run ytkit          # launches the TUI
```

Or install straight from GitHub, no clone needed; add `@vX.Y.Z` to pin a
[release](https://github.com/MS-32154/ytkit/releases):

```bash
uv tool install git+https://github.com/MS-32154/ytkit
```

Install it as a standalone tool:

```bash
uv tool install .
ytkit
```

## The three ways in

### 1. TUI — `ytkit`

```
 1 Queue   2 Settings   3 Command
┌──────────────────────────────────────────────────────────────────────────┐
│ Paste a video, playlist or channel URL and press Enter…   [Preset ▾] Add │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ #  Status       Title / URL              Progress            Speed   │ │
│ │ 1  downloading  Big Buck Bunny 60fps 4K  ███████░░░░░  63.0% 4.2MiB/s│ │
│ │ 2  done         Me at the zoo            ████████████ 100.0%         │ │
│ │ 3  failed       https://youtu.be/…       Private video               │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ ┌ Log ─────────────────────────────────────────────────────────────────┐ │
│ │ job 2 → …/Me at the zoo [jNQXAC9IVRw].en.txt                         │ │
│ │ ✓ job 2 finished in 4s                                               │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
 ^q Quit  a Add  i Inspect  x Cancel  o Folder  c Copy  p Profiles  ^s Save
```

Queue as many URLs as you like; they download in parallel. The **Settings** tab
exposes every option as a live form, and the **Command** tab shows the
equivalent `yt-dlp` invocation plus every setting you've changed from default.

| Key | Action |
| --- | --- |
| `1` `2` `3` | Queue, Settings, Command tabs |
| `esc` | Leave a text box, so the one-letter keys work again |
| `a` | Focus the URL box |
| `i` | Inspect the pasted URL, else the selected job (metadata, no download); `a` there queues a pasted URL |
| `x` | Cancel the selected job, or remove it from the list once finished |
| `X` | Remove every finished job from the list |
| `r` | Retry a failed or cancelled job |
| `o` | Open the selected job's folder from the job table, else the output folder |
| `c` | Copy the equivalent yt-dlp command — from the job table, that job's own |
| `p` | Save / load / delete a named profile |
| `/` | Filter the settings |
| `ctrl+s` | Save the current settings as your config |
| `ctrl+l` | Clear the log |
| `ctrl+p` | Command palette: every action above, plus the theme picker |
| `ctrl+q` | Quit (confirms if downloads are running) |

The theme you pick in the palette is remembered between runs (in `tui.toml`,
next to the config, so it never becomes a download setting). On a narrow
terminal the queue drops the progress bar for a percentage and puts a failed
job's reason before its title, and each setting's help moves under it.

The footer only offers what applies to the selected job. A failed job's row
says why it failed, and a URL that is already queued or downloading is not
queued twice.

Text settings apply when you press enter or leave the field, never
half-typed; a value that can't be used is outlined in red and left out. A
`●` marks every setting changed from its default, and `Settings*` means
there are changes `ctrl+s` hasn't saved.

Path settings complete as you type, like a shell: `tab` or `→` accepts the
suggestion, and the output and temporary directory fields only offer folders.
Copying uses the terminal's clipboard escape (OSC 52), which most terminals
support and macOS Terminal does not.

### 2. Wizard — `ytkit wizard`

Answers instead of flags: what to download, what quality, which extras, where
to put it. It prints the resulting command before running anything.

### 3. CLI — `ytkit get …`

```bash
# 1080p mp4 with subs, transcript, thumbnail and metadata
ytkit get 'https://youtu.be/VIDEO' --preset balanced

# best-quality archival copy of a whole playlist, all subtitle languages
ytkit get 'https://youtube.com/playlist?list=PL...' --preset archival

# audio only, sponsor segments cut, timestamped transcript
ytkit get 'https://youtu.be/VIDEO' --preset podcast

# transcripts for several videos, no media at all
ytkit transcript 'https://youtu.be/A' 'https://youtu.be/B' --langs en,es

# what would this actually run?
ytkit get 'https://youtu.be/VIDEO' --preset archival --dry-run
```

## Commands

| Command | Purpose |
| --- | --- |
| `ytkit` / `ytkit tui` | Full-screen interface |
| `ytkit get URL...` | Download non-interactively |
| `ytkit wizard` | Guided prompts |
| `ytkit info URL` | Metadata, `--formats`, `--subtitles`, or `--json` |
| `ytkit transcript URL...` | Captions → plain-text transcripts, no media |
| `ytkit convert FILE...` | Turn `.vtt`/`.srt`/`.ass` you already have into transcripts |
| `ytkit presets` | List the built-in presets |
| `ytkit config` | `--path`, `--show`, `--init`, `--edit` |
| `ytkit profile` | `list`, `save`, `show`, `delete` |

## Presets

| Key | What you get |
| --- | --- |
| `balanced` | 1080p MP4, metadata, thumbnail, English transcript |
| `archival` | Best quality MKV, every subtitle language, everything embedded |
| `audio` | Best audio → MP3 with cover art and tags |
| `podcast` | Opus audio, sponsor segments removed, timestamped transcript |
| `transcript` | Captions, transcript and metadata sidecars — no media |
| `metadata` | `.info.json`, description, thumbnail, summary only |
| `mobile` | 720p H.264 MP4, no sidecars |

Presets are a starting point — every option remains adjustable on top.

## What lands on disk

For `Some Video [dQw4w9WgXcQ]`, depending on your settings:

```
Some Video [dQw4w9WgXcQ].mp4          # media
Some Video [dQw4w9WgXcQ].en.srt       # captions, converted to your format
Some Video [dQw4w9WgXcQ].en.txt       # readable transcript        <- ytkit
Some Video [dQw4w9WgXcQ].summary.md   # Markdown metadata record   <- ytkit
Some Video [dQw4w9WgXcQ].info.json    # complete yt-dlp metadata
Some Video [dQw4w9WgXcQ].description
Some Video [dQw4w9WgXcQ].webp         # thumbnail
```

### Transcripts

YouTube's auto-captions are *rolling* — each cue repeats the tail of the last
one so the text scrolls on screen. Dumped naively that triples the word count.
ytkit de-duplicates cues (handling both verbatim carry-over and cues that grow
word by word), strips timing and styling, and reflows the result into
paragraphs broken on natural pauses. Use `--timestamps` for `[hh:mm:ss]`
prefixed lines instead.

## Non-Latin titles

Arabic, Cyrillic, CJK and other non-Latin titles make terminal navigation
painful — right-to-left text is reordered for display by the Unicode bidi
algorithm, so what `ls` shows doesn't match the byte order you have to type,
and tab-completion stops being useful. The channel name is usually non-Latin
too, so the *directory* is affected as well.

```bash
ytkit get URL --romanise -s restrict_filenames=true
```

```
lfylm_lwthiqy_lrby/
  kyf_njd_lkwkb_lslhh_llhyh_-_wthiqy_lfd [Q88Q9ZDjvYg].mp4
  kyf_njd_lkwkb_lslhh_llhyh_-_wthiqy_lfd [Q88Q9ZDjvYg].ar.srt
  kyf_njd_lkwkb_lslhh_llhyh_-_wthiqy_lfd [Q88Q9ZDjvYg].summary.md
```

The original stays in the sidecars, so nothing is lost and everything is still
searchable in its own script:

```bash
grep -rl "الفضاء" ~/Downloads/ytkit --include="*.summary.md"
```

Titles that are already ASCII are left completely alone. Characters yt-dlp
would rewrite to fullwidth look-alikes (`? : | " * < > / \`) are mapped to plain
equivalents first, and shell-hostile output (Arabic *ain* romanises to a
backtick) is stripped, so paths are safe unquoted.

Note that YouTube does **not** expose translated titles to downloaders —
`--extractor-args youtube:lang=en` returns the original title unchanged, even
on channels that show localised titles in the web UI. Romanisation is the
workable route, not translation.

## Configuration

Settings live in a TOML file — `ytkit config --path` tells you where.
Only values you've changed are written, so upgrades keep sensible defaults.

```bash
ytkit config --init     # write a fully populated file
ytkit config --show     # effective settings, changes highlighted
ytkit config --edit     # open in $EDITOR
```

Override anything per-run without touching the file:

```bash
ytkit get URL --set video_quality=1440 --set sponsorblock_remove=sponsor,intro
```

Save a combination you like as a profile:

```bash
ytkit profile save hifi --preset audio --set audio_format=flac
ytkit get URL --profile hifi
```

### What's configurable

Output paths and templates · download archive · filename restrictions ·
quality, codec, container, FPS caps and raw format selectors · audio format and
bitrate · subtitles (manual + auto, any language, any format, embedded or
sidecar) · transcripts · thumbnails · info JSON, descriptions, comments ·
metadata and chapter embedding · chapter splitting and time-range downloads ·
SponsorBlock marking and removal · playlist ranges, ordering, date and match
filters · concurrency, rate limits, retries, timeouts, proxies, IP binding,
geo-bypass, client impersonation · cookies from file or browser · and an
`extra_args` escape hatch for anything not exposed.

## Authentication

Private or age-restricted content needs your session:

```bash
ytkit get URL --cookies-from-browser firefox
ytkit get URL --cookies /path/to/cookies.txt
```

## How it works

Configuration is translated into **real yt-dlp command-line arguments** and run
through yt-dlp's own parser. That means postprocessor ordering (SponsorBlock
before chapter edits, metadata before thumbnail embedding) is handled by yt-dlp
rather than reimplemented — and the command shown in the UI is exactly the one
being executed.

```
Config --> argv --> yt_dlp.parse_options --> YoutubeDL --> post-processing
```

Downloads run on a thread pool. Progress events are pushed onto a queue and
drained on the UI thread in batches, so a fast download can't flood the
interface.

## Development

```bash
uv sync --all-groups
uv run pytest          # no network required
uv run ruff check src/ tests/
```

The suite stubs the yt-dlp call itself but exercises the real queue, worker
pool, event sink and TUI — Textual's headless pilot drives the actual widgets.

### Releasing

The version lives in `pyproject.toml` alone. To release, bump it and push:

```bash
uv version --bump minor          # or patch / major; 1.2.0rc1-style versions work too
git commit -am "Release $(uv version --short)" && git push
```

The **Release** workflow notices the new version, runs the full suite, tags
`vX.Y.Z`, builds the wheel and sdist, and publishes a GitHub release with
generated notes (versions with a letter in them go out as pre-releases).
**CI** runs lint and tests on Python 3.14 and 3.15 for every push and pull
request.

## Licence

MIT
