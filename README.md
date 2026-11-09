# CK Music Converter

A music tagger and audio converter in one. Fix the tags and cover art of a whole library the way
you would in Mp3tag, then convert it to MP3, AAC, ALAC, FLAC, WAV, AIFF, Ogg Vorbis or Opus,
keeping every tag.

Part of the **CK Suite**, a set of small, free Windows tools by CK Software.

![CK Music Converter](docs/screenshot.png)

## Features

### Tagging
- **Sortable file list** with Title, Artist, Album, Year, Track, Genre, format and length columns,
  plus a **filter** box
- **Tag panel** for one file or hundreds: fields that differ show `< keep >`, and `< blank >` clears
  a field. Title, Artist, Album, Year, Track, Genre, Comment, Album Artist, Composer, Disc and the
  file name.
- **Save and undo:** edits show in blue until you save (Ctrl+S), and Ctrl+Z undoes them, even after
  saving or renaming
- **Tag → Filename** (with subfolders, e.g. `%albumartist%\%album%\%track% - %title%`),
  **Filename → Tag** and **Tag → Tag**, each with a live preview
- **Actions:** change case, find and replace (with regular expressions), remove fields, trim spaces
- **Auto-numbering:** track and total, restarting per folder, with disc numbers by folder
- **Extended tags:** see and edit every field a file holds
- **Lyrics** for M4A, MP3, FLAC, Ogg, Opus, WAV, AIFF, WavPack and APE: one Lyrics field, saved the
  way each format expects (iTunes `©lyr` atom, ID3 `USLT` frame, `LYRICS` Vorbis comment, ID3 chunk in
  WAV/AIFF, APEv2). Load them from a `.txt` or `.lrc` file.
- **WAV and AIFF tags** are stored in an ID3 chunk, as Mp3tag and iTunes do
- **MusicBrainz lookup:** find the album and fill in titles, track numbers, year, album artist and
  genre, plus the front cover from the Cover Art Archive
- **Copy tags from tracks you already have:** pair new audio (e.g. FLAC masters) with a tagged
  album by track number and title
- **Cover art:** add, extract or remove, or drop an image on the window
- **Export tags to CSV**

### Converting
- **Any audio in:** MP3, M4A/AAC, FLAC, WAV, AIFF, Ogg, Opus, WMA, WavPack, APE and more, or the
  sound from a video (MP4, MOV, MKV, WebM…)
- **Eight formats out:** MP3 (CBR or VBR), AAC, ALAC, FLAC, WAV, AIFF, Ogg Vorbis and Opus
- **Presets** for Apple Music / iPhone, universal MP3, lossless archives, CD-quality WAV,
  streaming loudness and podcasts, plus **precise settings**: bitrate or quality, bit depth,
  FLAC compression, sample rate, stereo or mono. Save your own setups as presets.
- **Loudness normalization** (e.g. -14 LUFS for Spotify / YouTube) and **silence trimming**
- **Tags, lyrics and cover art carry over** to every output format (WAV and AIFF included), even edits
  you haven't saved yet
- **Fast:** converts several files at once and can keep the folder structure of dropped folders
- Saves to **Music\CK Music Converter** by default, next to the originals, or anywhere you choose

FFmpeg is included in the installer, so there's nothing else to install.

![Editing the tags of several files at once](docs/tags.png)

## Download

**[Download the latest version](https://github.com/project-ceekay/ck-music-converter/releases/latest)**:
get the `…-Setup.exe` file under *Assets* and run it.

- Windows 10 or 11 (64-bit). Installs for your user account only, so no administrator rights are needed.
- Windows may show **"Windows protected your PC"** because the installer isn't code-signed.
  Click **More info → Run anyway**.
- Uninstall any time from **Settings → Apps**.

## How to use it

1. Drop music files or folders onto the window (or click **Add Folder…** / **Add Files…**).
2. Select files and edit their tags on the **Tags** tab, or use the **Convert Names**, **Actions**
   and **Tag Sources** menus. Press **Save** (Ctrl+S) to write the tags into the files.
3. To convert, pick a preset or settings on the **Convert** tab and click **Convert**. It converts
   the selected files, or all of them when none are selected. The results appear in
   `Music\CK Music Converter` (or wherever you chose); double-click one in the list to play it.

**Format strings** use placeholders such as `%artist%`, `%title%`, `%album%`, `%year%`, `%track%`
(two digits), `%disc%`, `%genre%`, `%albumartist%` and `%filename%`. In Filename → Tag, `%dummy%`
skips a part, and `\` matches folder names.

**Copying tags from an existing album:** add the new audio, choose **Tag Sources → Copy Tags From
Folder…** and pick the folder with the tagged tracks. Each file is paired by track number, then by
title, and gets that track's tags and artwork.

## Updates

The program checks GitHub for a new version once a day. When one is available, a link appears at
the bottom of the window: click it to read what's new and update in one click. Your settings are
kept. You can turn the automatic check off under **About**.

Settings are stored in `%APPDATA%\CK Suite\Music Converter`.

## Run or build from source

Requires Python 3.10 or newer and FFmpeg (ffmpeg.exe and ffprobe.exe in a `bin` folder next to the
script, or on your PATH).

```
pip install -r requirements.txt
python program.py
```

Releases are built automatically by GitHub Actions when a version tag (`v1.2.3`) is pushed; see
[`.github/workflows/release.yml`](.github/workflows/release.yml) and [`installer/setup.iss`](installer/setup.iss).

## License

[MIT](LICENSE) © CK Software. Bundled third-party components are listed in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
