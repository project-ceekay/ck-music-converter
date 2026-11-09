"""Tag reading/writing, cover art I/O, Mp3tag format patterns, and MusicBrainz lookup."""
import base64
import difflib
import fnmatch
import io
import json
import os
import re
import urllib.parse
import urllib.request

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags
from PIL import Image

from lyrics import LYRICS_CODECS, _id3_version, read_lyrics, write_lyrics
from ui_kit import REPO, VERSION, human_size

# ------------------------------------------------------------- tag fields
FIELDS = [("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("date", "Year"), ("tracknumber", "Track"),
          ("genre", "Genre"), ("comment", "Comment"), ("albumartist", "Album Artist"), ("composer", "Composer"),
          ("discnumber", "Disc"), ("lyrics", "Lyrics")]
FIELD_LABEL = dict(FIELDS)
TEXT_FIELDS = ("title", "artist", "album", "genre", "comment", "albumartist", "composer")
FF_TAG = {"albumartist": "album_artist", "tracknumber": "track", "discnumber": "disc"}   # ours -> ffmpeg
FROM_FF = {"album_artist": "albumartist", "track": "tracknumber", "disc": "discnumber"}  # ffmpeg -> ours
FF_SKIP = {"encoder", "major_brand", "minor_version", "compatible_brands", "creation_time", "handler_name",
           "vendor_id", "encoded_by", "itunsmpb", "itunnorm"}
KEEP, BLANK = "< keep >", "< blank >"
TAGGABLE = {".mp3": "mp3", ".m4a": "aac", ".m4b": "aac", ".mp4": "aac", ".flac": "flac", ".ogg": "vorbis",
            ".oga": "vorbis", ".opus": "opus", ".wav": "wav", ".aif": "aiff", ".aiff": "aiff"}
TOKEN_ALIAS = {"year": "date", "track": "tracknumber", "disc": "discnumber", "band": "albumartist"}
FORMAT_HELP = "%artist%  %title%  %album%  %year%  %track%  %disc%  %genre%  %albumartist%  %composer%  %filename%"
RECENT_DEFAULTS = {
    "t2f": ["%artist% - %title%", "%track% - %title%", "%track%. %artist% - %title%",
            "%albumartist%\\%album%\\%track% - %title%"],
    "f2t": ["%artist% - %title%", "%track% - %title%", "%track%. %artist% - %title%", "%track% %title%",
            "%artist%\\%album%\\%track% - %title%"],
    "t2t": ["%artist% - %title%", "%track%", "%title% (%year%)"],
}
GENRES = ["Alternative", "Ambient", "Blues", "Classical", "Country", "Dance", "Electronic", "Folk", "Hip-Hop",
          "Indie", "Jazz", "Latin", "Metal", "Pop", "Punk", "R&B", "Reggae", "Rock", "Soul", "Soundtrack", "World"]
COLUMNS = [("file", "Filename", 140, True), ("title", "Title", 114, True), ("artist", "Artist", 92, True),
           ("album", "Album", 92, True), ("date", "Year", 40, False), ("tracknumber", "Track", 44, False),
           ("genre", "Genre", 58, False), ("src", "Format", 78, False), ("len", "Length", 52, False),
           ("status", "Status", 58, False)]


# ------------------------------------------------------------- reading & writing tags
def _register_extra_keys():
    """Mutagen's simple tag names lack a few fields Mp3tag users expect."""
    from mutagen.id3 import COMM

    def get(id3, key):
        frames = id3.getall("COMM")
        if not frames:
            raise KeyError(key)
        return [str(t) for t in frames[0].text]

    def put(id3, key, value):
        id3.delall("COMM")
        id3.add(COMM(encoding=3, lang="eng", desc="", text=list(value)))

    def delete(id3, key):
        if not id3.getall("COMM"):
            raise KeyError(key)
        id3.delall("COMM")

    if "comment" not in EasyID3.Get:
        EasyID3.RegisterKey("comment", get, put, delete)
    for key, atom in (("composer", "\xa9wrt"), ("comment", "\xa9cmt")):
        if key not in EasyMP4Tags.Get:
            EasyMP4Tags.RegisterTextKey(key, atom)


_register_extra_keys()


def fmt_of_path(path):
    """The tag family of a file we can tag in place (None for WMA, APE…)."""
    return TAGGABLE.get(os.path.splitext(path)[1].lower())


def open_easy(path):
    """(dict of simple tag fields, save()) for a file we can tag in place.
    WAV and AIFF get the same simple field names on the ID3v2 tag inside their RIFF / AIFF chunk."""
    fmt = fmt_of_path(path)
    if fmt in ("wav", "aiff"):
        from mutagen.aiff import AIFF
        from mutagen.wave import WAVE
        f = (WAVE if fmt == "wav" else AIFF)(path)
        existed = f.tags is not None
        if not existed:
            f.add_tags()
        version = _id3_version(f.tags, existed)
        easy = EasyID3()
        easy._EasyID3__id3 = f.tags   # EasyID3's field mapping, on the chunk's ID3 tag
        return easy, lambda: f.save(v2_version=version)
    f = MutagenFile(path, easy=True)
    if f is None:
        raise ValueError("this file type can't be tagged")
    existed = f.tags is not None
    if not existed:
        f.add_tags()
    if fmt == "mp3":
        version = _id3_version(f.tags, existed)
        return f.tags, lambda: f.save(v2_version=version)
    return f.tags, f.save


def read_tags(path):
    """{field: text} for every simple tag field in the file (multiple values joined with '; '),
    plus 'lyrics' read through the lyrics layer."""
    try:
        if fmt_of_path(path):
            tags, _ = open_easy(path)
        else:
            f = MutagenFile(path, easy=True)
            tags = f.tags if f is not None else None
        keys = list(tags.keys()) if tags else []
    except Exception:
        return {}
    out = {}
    for k in keys:
        kl = k.lower()
        if kl in ("metadata_block_picture", "coverart", "covr", "lyrics", "unsyncedlyrics"):
            continue
        try:
            v = tags[k]
        except Exception:
            continue
        text = "; ".join(str(x) for x in v) if isinstance(v, list) else str(v)
        if text and len(text) < 20000:
            out[kl] = text
    lyrics = read_lyrics(path)
    if lyrics:
        out["lyrics"] = lyrics
    return out


def valid_keys(path):
    """Field names this file type can hold, or None when any name works (Vorbis comments)."""
    fmt = fmt_of_path(path)
    if fmt in ("mp3", "wav", "aiff"):
        return sorted(EasyID3.Get) + ["lyrics"]
    if fmt == "aac":
        return sorted(EasyMP4Tags.Get) + ["lyrics"]
    return None


def key_ok(path, key):
    if key == "lyrics":
        return os.path.splitext(path)[1].lower() in LYRICS_CODECS
    keys = valid_keys(path)
    if keys is None:
        return bool(re.fullmatch(r"[\x20-\x3c\x3e-\x7d]+", key))
    return any(fnmatch.fnmatchcase(key, k) for k in keys)


def write_tags(path, changes):
    """Save {field: value} into the file; an empty value removes the field. 'lyrics' goes through
    the lyrics layer, everything else through the simple tag fields."""
    changes = dict(changes)
    lyrics = changes.pop("lyrics", None)
    if changes:
        tags, save = open_easy(path)
        for k, v in changes.items():
            if v:
                try:
                    tags[k] = [v]
                except (KeyError, ValueError) as e:
                    raise ValueError(f"this file type can't store a field named \"{k}\"") from e
            else:
                try:
                    del tags[k]
                except KeyError:
                    pass
        save()
    if lyrics is not None:
        write_lyrics(path, lyrics)


def track_no(text):
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else 0


def extract_cover(path):
    """(image bytes, mime) of the embedded front cover, or None."""
    try:
        f = MutagenFile(path)
    except Exception:
        return None
    if f is None:
        return None
    pics = getattr(f, "pictures", None)                       # FLAC
    if pics:
        return pics[0].data, pics[0].mime or "image/jpeg"
    tags = f.tags
    if not tags:
        return None
    try:
        if hasattr(tags, "getall"):                            # MP3 (ID3)
            apic = tags.getall("APIC")
            if apic:
                return apic[0].data, apic[0].mime or "image/jpeg"
        covr = tags.get("covr") if hasattr(tags, "get") else None   # M4A
        if covr:
            return bytes(covr[0]), "image/png" if getattr(covr[0], "imageformat", 13) == 14 else "image/jpeg"
        mbp = tags.get("metadata_block_picture") if hasattr(tags, "get") else None   # Ogg / Opus
        if mbp:
            from mutagen.flac import Picture
            pic = Picture(base64.b64decode(mbp[0]))
            return pic.data, pic.mime or "image/jpeg"
    except Exception:
        pass
    return None


def image_mime(data):
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def write_cover(path, fmt, data):
    """Embed (or with data=None remove) the front cover."""
    mime = image_mime(data) if data else None
    if fmt in ("mp3", "wav", "aiff"):
        from mutagen.id3 import APIC
        if fmt == "mp3":
            from mutagen.mp3 import MP3 as cls
        elif fmt == "wav":
            from mutagen.wave import WAVE as cls
        else:
            from mutagen.aiff import AIFF as cls
        f = cls(path)
        existed = f.tags is not None
        if not existed:
            f.add_tags()
        f.tags.delall("APIC")
        if data:
            f.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        f.save(v2_version=_id3_version(f.tags, existed))
    elif fmt in ("aac", "alac"):
        from mutagen.mp4 import MP4, MP4Cover
        m = MP4(path)
        if data:
            m["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG)]
        elif "covr" in m:
            del m["covr"]
        m.save()
    elif fmt in ("flac", "vorbis", "opus"):
        from mutagen.flac import FLAC, Picture
        pic = None
        if data:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Cover", data
            try:
                with Image.open(io.BytesIO(data)) as im:
                    pic.width, pic.height, pic.depth = im.width, im.height, 24
            except Exception:
                pass
        if fmt == "flac":
            f = FLAC(path)
            f.clear_pictures()
            if pic:
                f.add_picture(pic)
            f.save()
        else:
            f = MutagenFile(path)
            if pic:
                f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            elif "metadata_block_picture" in f:
                del f["metadata_block_picture"]
            f.save()


def cover_text(data):
    try:
        with Image.open(io.BytesIO(data)) as im:
            return f"{im.width} × {im.height} {im.format or ''}, {human_size(len(data))}"
    except Exception:
        return human_size(len(data))


def norm_name(text):
    """'03 - My Song (Remastered)' -> 'mysongremastered' for fuzzy matching."""
    text = re.sub(r"^\s*\d+[\s._-]*", "", text.lower())
    return re.sub(r"[^a-z0-9]+", "", text)


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def match_pairs(items, tag_files):
    """items: {key: (path, tags)}. Returns {key: tag file}, paired by disc + track number, then by
    similar title / file name."""
    t_info = {t: read_tags(t) for t in tag_files}
    pairs, free = {}, set(tag_files)
    for key, (path, tags) in items.items():
        tn = track_no(tags.get("tracknumber"))
        if tn:
            disc = track_no(tags.get("discnumber")) or 1
            hit = next((t for t in sorted(free) if track_no(t_info[t].get("tracknumber")) == tn
                        and (track_no(t_info[t].get("discnumber")) or 1) == disc), None)
            if hit:
                pairs[key] = hit
                free.discard(hit)
    scores = []
    for key, (path, tags) in items.items():
        if key in pairs:
            continue
        for t in free:
            if tags.get("title") and t_info[t].get("title"):
                an, tn = norm_name(tags["title"]), norm_name(t_info[t]["title"])
            else:
                an, tn = norm_name(stem(path)), norm_name(stem(t))
            scores.append((difflib.SequenceMatcher(None, an, tn).ratio(), id(key), key, t))
    for score, _, key, t in sorted(scores, key=lambda s: (s[0], s[1]), reverse=True):
        if score >= 0.6 and key not in pairs and t in free:
            pairs[key] = t
            free.discard(t)
    return pairs


# ------------------------------------------------------------- format strings (Mp3tag style)
def token_values(job):
    t = job.tags()
    vals = dict(t)
    n = track_no(t.get("tracknumber"))
    vals.update(track=f"{n:02d}" if n else "", disc=str(track_no(t.get("discnumber")) or ""),
                year=(t.get("date") or "")[:4], filename=stem(job.path), name=stem(job.path),
                ext=os.path.splitext(job.path)[1].lstrip("."), band=t.get("albumartist", ""),
                folder=os.path.basename(os.path.dirname(job.path)))
    return vals


def fill(pattern, job, vals=None):
    """'%artist% - %title%' (or '{artist} - {title}') filled in from the job's tags."""
    vals = vals if vals is not None else token_values(job)
    return re.sub(r"%(\w+)%|\{(\w+)\}", lambda m: str(vals.get((m.group(1) or m.group(2)).lower(), "")), pattern)


def safe_name(text):
    text = text.replace(":", " -")
    text = re.sub(r'[<>"/\\|?*\x00-\x1f]', "_", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def parse_filename(pattern, name):
    """'%track% - %title%' against '03 - Song' -> {'tracknumber': '03', 'title': 'Song'} (None = no match).
    %dummy% skips a part."""
    parts = re.split(r"%(\w+)%", pattern)
    rx, fields = "", []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            rx += re.escape(part)
        elif part.lower() == "dummy":
            rx += ".*?"
        else:
            rx += f"(?P<g{len(fields)}>.*?)"
            fields.append(TOKEN_ALIAS.get(part.lower(), part.lower()))
    if not fields:
        return None
    m = re.fullmatch(rx, name, re.S)
    if not m:
        return None
    return {k: m.group(f"g{i}").strip() for i, k in enumerate(fields)}


def change_case(text, mode):
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    if mode == "sentence":
        t = text.lower()
        return t[:1].upper() + t[1:]
    return re.sub(r"(^|[\s(\[{/\-\"])([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), text.lower())


# ------------------------------------------------------------- MusicBrainz & Cover Art Archive
MB_UA = f"CKMusicConverter/{VERSION} ( https://github.com/{REPO} )"


def mb_get(path, **params):
    params["fmt"] = "json"
    req = urllib.request.Request(f"https://musicbrainz.org/ws/2/{path}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": MB_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def credit(ac):
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in ac or [])


def mb_search(artist, album):
    clean = lambda s: s.replace('"', " ").strip()   # noqa: E731
    q = f'release:"{clean(album)}"' if album.strip() else ""
    if artist.strip():
        q += (" AND " if q else "") + f'artist:"{clean(artist)}"'
    if not q:
        return []
    out = []
    for r in mb_get("release", query=q, limit=25).get("releases", []):
        out.append({"id": r["id"], "title": r.get("title", ""), "artist": credit(r.get("artist-credit")),
                    "date": r.get("date", ""), "tracks": r.get("track-count", 0), "country": r.get("country", ""),
                    "format": ", ".join(sorted({m.get("format") or "?" for m in r.get("media", [])}))})
    return out


def mb_release(rid):
    r = mb_get(f"release/{rid}", inc="recordings+artist-credits+genres+release-groups")
    album, aa = r.get("title", ""), credit(r.get("artist-credit"))
    group = r.get("release-group") or {}
    year = (r.get("date") or group.get("first-release-date") or "")[:4]
    genres = r.get("genres") or group.get("genres") or []
    genre = max(genres, key=lambda g: g.get("count", 0))["name"].title() if genres else ""
    media = r.get("media", [])
    tracks = []
    for m in media:
        total = m.get("track-count") or len(m.get("tracks", []))
        for t in m.get("tracks", []):
            tracks.append({"title": t.get("title") or (t.get("recording") or {}).get("title", ""),
                           "artist": credit(t.get("artist-credit")) or aa, "album": album, "albumartist": aa,
                           "date": year, "tracknumber": f"{t.get('position') or t.get('number')}/{total}",
                           "discnumber": f"{m.get('position', 1)}/{len(media)}" if len(media) > 1 else "",
                           "genre": genre})
    return {"album": album, "artist": aa, "date": year, "tracks": tracks}


def caa_cover(rid):
    try:
        req = urllib.request.Request(f"https://coverartarchive.org/release/{rid}/front-500", headers={"User-Agent": MB_UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except Exception:
        return None
