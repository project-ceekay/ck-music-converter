# Changelog

## 1.0.1

- Classic top menu bar (`File`, `Edit`, `View`, `Convert`, `Actions`, `Tag Sources`, `Help`) and compact icon toolbar with hover tooltips
- Added Dark Mode (`View -> Dark Mode` or toolbar moon button), saved in settings
- Full-height vertical column lines extending all the way down the right-hand file viewer box
- Window title bar now shows the active folder path
- Added Mp3tag keyboard shortcuts (`Alt+1` Tag → Filename, `Alt+2` Filename → Tag, `Alt+3` Tag → Tag, `Alt+T` Extended Tags, `Ctrl+K` Auto-Number, `Ctrl+M` MusicBrainz, `F3` Filter toggle, `Ctrl+Enter` Convert Audio)
- Split codebase into modular files (`program.py`, `ui_kit.py`, `tagger.py`, `lyrics.py`, `converter.py`)

## 1.0.0

First public release.

- A full music tagger: sortable and filterable file list, a tag panel for many files at once
  (`< keep >` / `< blank >`), save and undo, Tag → Filename, Filename → Tag and Tag → Tag with live
  previews, case / replace / remove / trim actions, auto-numbering, extended tags and CSV export
- MusicBrainz album lookup with cover art from the Cover Art Archive
- Lyrics for M4A, MP3, FLAC, Ogg, Opus, WAV, AIFF, WavPack and APE, each stored in the format's own
  structure, checked before writing and verified after
- WAV and AIFF tags, cover art and lyrics stored in an ID3 chunk
- Tags are saved safely: a failed save never leaves a half-written file
- Copy tags and artwork from tracks you already have, matched by track number and title
- Cover art: add, extract, remove, or drop an image on the window
- Convert any audio (or the sound from a video) to MP3, AAC, ALAC, FLAC, WAV, AIFF, Ogg Vorbis or Opus
- One-click presets (Apple Music, universal MP3, lossless archive, CD quality, streaming loudness,
  podcast) plus precise settings, and your own saved presets
- Loudness normalization and silence trimming
- Tags, lyrics and cover art carried over to every output format
- Several files converted at once; drag and drop files or folders, keeping their folder structure
- Saves to Music\CK Music Converter by default
- Smooth window resizing: the window keeps up with the mouse while you drag its edge, and lays
  itself out once when you let go
- Built-in updates: checks GitHub once a day and installs new versions in one click
