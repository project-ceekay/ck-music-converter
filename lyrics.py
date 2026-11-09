"""Universal lyrics layer: one 'lyrics' property mapped onto each container's native format."""
import os
import re
import shutil

from mutagen import File as MutagenFile


def _id3_version(tags, existed):
    """Keep an existing ID3v2.4 tag at 2.4; new tags and 2.3 tags are written as 2.3 (widest support)."""
    return 4 if existed and tuple(getattr(tags, "version", (2, 3, 0))) >= (2, 4, 0) else 3


class LyricsError(Exception):
    """The file can't take lyrics, isn't what its extension says, or the write didn't stick."""


CONTAINER_NAMES = {"mp3": "an MP3", "mp4": "an MP4 / M4A", "flac": "a FLAC", "ogg": "an Ogg", "wav": "a WAV",
                   "aiff": "an AIFF", "ape": "a Monkey's Audio", "wavpack": "a WavPack"}


def sniff_container(path):
    """What the file really is, judged from its first bytes (not its name)."""
    with open(path, "rb") as f:
        head, offset, id3_front = f.read(64), 0, False
        while head[:3] == b"ID3" and len(head) >= 10:      # skip ID3v2 tags in front of the audio
            size = (head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 | (head[9] & 0x7F)
            offset += 10 + size + (10 if head[5] & 0x10 else 0)
            f.seek(offset)
            head, id3_front = f.read(64), True
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"MAC ":
        return "ape"
    if head[:4] == b"wvpk":
        return "wavpack"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "mp3"
    if id3_front:
        return "mp3"                                        # padding / junk before the first frame
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    return None


class LyricsCodec:
    containers = ()      # what sniff_container() must report for this codec to write
    label = ""           # where the lyrics end up, shown in the editor

    def read(self, path):
        raise NotImplementedError

    def write(self, path, text):   # text "" removes the lyrics
        raise NotImplementedError


class MP4Lyrics(LyricsCodec):
    """iTunes / QuickTime: the ©lyr atom (A9 6C 79 72) in moov.udta.meta.ilst, holding a 'data'
    atom with type flags 0x000001 (UTF-8 text) and locale 0x00000000."""
    containers = ("mp4",)
    label = "iTunes ©lyr atom (moov.udta.meta.ilst)"
    ATOM = "\xa9lyr"

    def read(self, path):
        from mutagen.mp4 import MP4
        tags = MP4(path).tags
        v = tags.get(self.ATOM) if tags is not None else None
        return str(v[0]) if v else ""

    def write(self, path, text):
        from mutagen.mp4 import MP4
        m = MP4(path)
        if m.tags is None:
            m.add_tags()
        if text:
            m.tags[self.ATOM] = [text]    # mutagen renders: data atom, flags 1 (UTF-8), locale 0
        elif self.ATOM in m.tags:
            del m.tags[self.ATOM]
        m.save()


class ID3Lyrics(LyricsCodec):
    """ID3v2 USLT frame: text encoding byte, 3-byte ISO-639-2 language, null-terminated content
    descriptor (empty), then the lyrics. UTF-8 (0x03) in ID3v2.4 tags; ID3v2.3 has no UTF-8, so
    those tags get UTF-16 with BOM (0x01), which is what v2.3 readers expect."""
    containers = ("mp3",)
    label = "ID3v2 USLT frame"

    def open(self, path):
        from mutagen.mp3 import MP3
        return MP3(path)

    @staticmethod
    def pick(frames):
        return next((fr for fr in frames if not fr.desc), frames[0]) if frames else None

    def read(self, path):
        f = self.open(path)
        fr = self.pick(f.tags.getall("USLT")) if f.tags is not None else None
        return str(fr.text) if fr else ""

    def write(self, path, text):
        from mutagen.id3 import USLT
        f = self.open(path)
        existed = f.tags is not None
        if not existed:
            f.add_tags()
        old = self.pick(f.tags.getall("USLT"))
        lang = old.lang if old and re.fullmatch(r"[a-z]{3}", old.lang or "") else "eng"
        version = _id3_version(f.tags, existed)
        f.tags.delall("USLT")
        if text:
            f.tags.add(USLT(encoding=3 if version == 4 else 1, lang=lang, desc="", text=text))
        f.save(v2_version=version)


class WAVLyrics(ID3Lyrics):
    """WAV: an ID3v2 tag in the RIFF 'id3 ' chunk (the Mp3tag / iTunes way), same USLT frame."""
    containers = ("wav",)
    label = "USLT frame in the WAV's ID3 chunk"

    def open(self, path):
        from mutagen.wave import WAVE
        return WAVE(path)


class AIFFLyrics(ID3Lyrics):
    """AIFF: an ID3v2 tag in the FORM 'ID3 ' chunk, same USLT frame."""
    containers = ("aiff",)
    label = "USLT frame in the AIFF's ID3 chunk"

    def open(self, path):
        from mutagen.aiff import AIFF
        return AIFF(path)


class VorbisLyrics(LyricsCodec):
    """FLAC, Ogg Vorbis, Opus: the plain LYRICS=... Vorbis comment (UTF-8, no binary header).
    UNSYNCEDLYRICS (used by some players) is read as a fallback and replaced on write."""
    containers = ("flac", "ogg")
    label = "LYRICS Vorbis comment"
    FIELD, ALIASES = "LYRICS", ("UNSYNCEDLYRICS",)

    def open(self, path):
        from mutagen.flac import FLAC
        from mutagen.oggflac import OggFLAC
        from mutagen.oggopus import OggOpus
        from mutagen.oggspeex import OggSpeex
        from mutagen.oggvorbis import OggVorbis
        f = MutagenFile(path)
        if not isinstance(f, (FLAC, OggVorbis, OggOpus, OggFLAC, OggSpeex)):
            raise LyricsError("this Ogg file doesn't use Vorbis comments")
        return f

    def read(self, path):
        tags = self.open(path).tags
        for key in (self.FIELD,) + self.ALIASES:
            v = tags.get(key) if tags is not None else None
            if v:
                return str(v[0])
        return ""

    def write(self, path, text):
        f = self.open(path)
        if f.tags is None:
            f.add_tags()
        for key in (self.FIELD,) + self.ALIASES:
            if key in f.tags:
                del f.tags[key]
        if text:
            f.tags[self.FIELD] = [text]
        f.save()


class APELyrics(LyricsCodec):
    """Monkey's Audio and WavPack: the APEv2 'Lyrics' item."""
    containers = ("ape", "wavpack")
    label = "APEv2 Lyrics item"

    def read(self, path):
        from mutagen.apev2 import APEv2, APENoHeaderError
        try:
            v = APEv2(path).get("Lyrics")
        except APENoHeaderError:
            return ""
        return str(v) if v else ""

    def write(self, path, text):
        from mutagen.apev2 import APEv2, APENoHeaderError
        try:
            t = APEv2(path)
        except APENoHeaderError:
            t = APEv2()
        if text:
            t["Lyrics"] = text
        elif "Lyrics" in t:
            del t["Lyrics"]
        t.save(path)


_mp4, _vorbis, _ape = MP4Lyrics(), VorbisLyrics(), APELyrics()
LYRICS_CODECS = {".m4a": _mp4, ".m4b": _mp4, ".mp4": _mp4, ".mp3": ID3Lyrics(), ".flac": _vorbis, ".ogg": _vorbis,
                 ".oga": _vorbis, ".opus": _vorbis, ".wav": WAVLyrics(), ".aif": AIFFLyrics(), ".aiff": AIFFLyrics(),
                 ".ape": _ape, ".wv": _ape}
LYRICS_MAX = 1024 * 1024   # bytes of UTF-8; real lyrics are a few KB


def lyrics_target(path):
    codec = LYRICS_CODECS.get(os.path.splitext(path)[1].lower())
    return codec.label if codec else None


def clean_lyrics(text):
    """Lyrics as they'll be stored: text, \\n line breaks, no NUL bytes (they'd end a USLT frame early)."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise LyricsError("lyrics must be text")
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text.encode("utf-8")) > LYRICS_MAX:
        raise LyricsError("the lyrics are larger than 1 MB")
    return text


def read_lyrics(path):
    """The file's lyrics ('' when there are none or the file can't hold any)."""
    codec = LYRICS_CODECS.get(os.path.splitext(path)[1].lower())
    if not codec:
        return ""
    try:
        return clean_lyrics(codec.read(path))
    except Exception:
        return ""


def write_lyrics(path, text):
    """Store (or with '' remove) the lyrics in the file's native structure. Checks the text, that the
    file exists, is writable and really is the format its extension claims, before writing; then
    reads the lyrics back to confirm."""
    ext = os.path.splitext(path)[1].lower()
    codec = LYRICS_CODECS.get(ext)
    if not codec:
        raise LyricsError(f"{ext or 'these'} files can't hold lyrics")
    text = clean_lyrics(text)
    if not os.path.isfile(path):
        raise LyricsError("the file doesn't exist any more")
    try:
        actual = sniff_container(path)
    except OSError as e:
        raise LyricsError(f"can't read the file ({e.strerror or e})") from e
    if actual not in codec.containers:
        what = CONTAINER_NAMES.get(actual, "not a format we recognize")
        raise LyricsError(f"the file is really {what} file, not {ext}; fix its extension first"
                          if actual else f"the file isn't a valid {ext} file")
    if not os.access(path, os.W_OK):
        raise LyricsError("the file is read-only")
    try:
        codec.write(path, text)
    except LyricsError:
        raise
    except Exception as e:
        raise LyricsError(f"couldn't write the lyrics: {e}") from e
    if read_lyrics(path) != text:
        raise LyricsError("the lyrics didn't read back correctly after saving")


def safe_edit(path, fn):
    """Run fn(copy) on a copy next to the file and swap it in only if everything worked, so a failed
    save never leaves a half-written file."""
    if not os.access(path, os.W_OK):
        raise OSError("the file is read-only")
    base, ext = os.path.splitext(path)
    tmp = f"{base}.ck-saving{ext}"
    shutil.copy2(path, tmp)
    try:
        fn(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
