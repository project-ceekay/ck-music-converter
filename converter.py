"""Audio/video format presets, Job model, ffprobe inspection, and ffmpeg command building."""
import json
import os
import subprocess
import sys

from lyrics import write_lyrics
from tagger import (FF_SKIP, FF_TAG, FROM_FF, extract_cover, fmt_of_path,
                    key_ok, read_tags, write_cover, write_tags)
from ui_kit import NO_WINDOW

# ------------------------------------------------------------- formats & presets
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".wma",
              ".wv", ".ape", ".alac", ".ac3", ".dts", ".mka", ".caf", ".amr", ".mp2", ".m4b")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".m4v", ".3gp", ".ts")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")

# key: (label, extension, ffmpeg muxer, lossless?)
FORMATS = {
    "mp3": ("MP3", ".mp3", "mp3", False),
    "aac": ("AAC (M4A)", ".m4a", "ipod", False),
    "alac": ("ALAC (lossless M4A)", ".m4a", "ipod", True),
    "flac": ("FLAC", ".flac", "flac", True),
    "wav": ("WAV", ".wav", "wav", True),
    "aiff": ("AIFF", ".aiff", "aiff", True),
    "vorbis": ("Ogg Vorbis", ".ogg", "ogg", False),
    "opus": ("Opus", ".opus", "opus", False),
}
DEFAULT_PARAMS = dict(fmt="aac", bitrate=256, mp3_mode="cbr", vbr=2, vorbis_q=6, flac_level=8, depth="keep",
                      rate="keep", channels="keep", normalize=False, lufs=-14, trim=False, application="audio")
PRESETS = {
    "Apple Music / iPhone (AAC 256 kbps)": dict(fmt="aac", bitrate=256),
    "Universal MP3 (320 kbps)": dict(fmt="mp3", mp3_mode="cbr", bitrate=320),
    "Smaller MP3 (VBR V2, about 190 kbps)": dict(fmt="mp3", mp3_mode="vbr", vbr=2),
    "Lossless archive (FLAC)": dict(fmt="flac", flac_level=8, depth="keep"),
    "Apple Lossless (ALAC)": dict(fmt="alac", depth="keep"),
    "CD quality (WAV 16-bit, 44.1 kHz)": dict(fmt="wav", depth="16", rate="44100", channels="2"),
    "Streaming loudness (AAC 256, -14 LUFS)": dict(fmt="aac", bitrate=256, normalize=True, lufs=-14),
    "Podcast / voice (Opus 64 kbps, mono)": dict(fmt="opus", bitrate=64, channels="1", application="voip",
                                                 normalize=True, lufs=-16),
}
CUSTOM = "Custom settings"
VBR_KBPS = {0: 245, 1: 225, 2: 190, 3: 175, 4: 165, 5: 130, 6: 115, 7: 100, 8: 85, 9: 65}
VORBIS_KBPS = {0: 64, 1: 80, 2: 96, 3: 112, 4: 128, 5: 160, 6: 192, 7: 224, 8: 256, 9: 320, 10: 500}
OPUS_RATES = ("8000", "12000", "16000", "24000", "48000")


def music_folder():
    """The user's real Music folder (even if it was moved), else ~/Music."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

            g = GUID()
            ctypes.memmove(ctypes.byref(g), uuid.UUID("{4BD8D571-6D19-48D3-BE97-422220080E43}").bytes_le, 16)
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                path = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                return path
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Music")


DEFAULT_OUT = os.path.join(music_folder(), "CK Music Converter")


# ------------------------------------------------------------- the file list & conversion
class Job:
    def __init__(self, path, root=None):
        self.path, self.root = path, root   # root: the dropped folder, for "keep folder structure"
        self.iid = None
        self.info = {}                      # probe: duration, codec, rate, channels, bits, kbps, tags
        self.saved = read_tags(path) if self.taggable() else {}
        self.pending = {}                   # unsaved tag edits ("" = remove the field)
        self.cover = None                   # unsaved cover: None (no change), "remove" or image bytes
        self.status, self.status_tag, self.output = "", "", None
        self.info_only = False              # WAV/AIFF whose tags came from an INFO list (no ID3 yet)

    def taggable(self):
        return fmt_of_path(self.path) is not None

    def tags(self):
        t = dict(self.saved)
        for k, v in self.pending.items():
            if v:
                t[k] = v
            else:
                t.pop(k, None)
        return t

    def get(self, key):
        if key in self.pending:
            return self.pending[key]
        return self.saved.get(key, "")

    def dirty(self):
        return bool(self.pending) or self.cover is not None


def probe(ffprobe, path):
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "format=duration,bit_rate:format_tags:stream_tags:stream=codec_name,sample_rate,channels,"
                        "bits_per_raw_sample,bit_rate,bits_per_sample,sample_fmt", "-of", "json", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **NO_WINDOW)
    data = json.loads(r.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    if not st:
        raise ValueError("no audio stream")
    fmtd = data.get("format", {})
    bits = int(st.get("bits_per_raw_sample") or st.get("bits_per_sample") or 0)
    if not bits and str(st.get("sample_fmt", "")).startswith(("s32", "flt")):
        bits = 24
    tags = {}
    for k, v in list((st.get("tags") or {}).items()) + list((fmtd.get("tags") or {}).items()):
        k = k.lower()
        if k not in FF_SKIP and v:
            tags[FROM_FF.get(k, k)] = str(v)
    return {"duration": float(fmtd.get("duration") or 0), "codec": st.get("codec_name", "?"),
            "rate": int(st.get("sample_rate") or 0), "channels": int(st.get("channels") or 0),
            "bits": bits, "kbps": int(st.get("bit_rate") or fmtd.get("bit_rate") or 0) // 1000, "tags": tags}


def estimate_bytes(job, p):
    d = job.info.get("duration")
    if not d:
        return 0
    f = p["fmt"]
    if f == "mp3":
        kbps = p["bitrate"] if p["mp3_mode"] == "cbr" else VBR_KBPS[p["vbr"]]
    elif f in ("aac", "opus"):
        kbps = p["bitrate"]
    elif f == "vorbis":
        kbps = VORBIS_KBPS[p["vorbis_q"]]
    else:
        rate = int(p["rate"]) if p["rate"] != "keep" else (job.info.get("rate") or 44100)
        ch = int(p["channels"]) if p["channels"] != "keep" else (job.info.get("channels") or 2)
        bits = {"16": 16, "24": 24, "32f": 32}.get(p["depth"]) or job.info.get("bits") or 16
        kbps = rate * ch * bits / 1000 * (1 if f in ("wav", "aiff") else 0.58)
    return d * kbps * 1000 / 8


def codec_args(p, job):
    f = p["fmt"]
    depth = p["depth"]
    if depth == "keep" and f in ("wav", "aiff"):
        depth = "24" if (job.info.get("bits") or 16) > 16 else "16"
    if f == "mp3":
        q = ["-b:a", f"{p['bitrate']}k"] if p["mp3_mode"] == "cbr" else ["-q:a", str(p["vbr"])]
        return ["-c:a", "libmp3lame", *q, "-id3v2_version", "3"]
    if f == "aac":
        return ["-c:a", "aac", "-b:a", f"{p['bitrate']}k"]
    if f == "opus":
        return ["-c:a", "libopus", "-b:a", f"{p['bitrate']}k", "-application", p["application"]]
    if f == "vorbis":
        return ["-c:a", "libvorbis", "-q:a", str(p["vorbis_q"])]
    if f == "alac":
        return ["-c:a", "alac", *({"16": ["-sample_fmt", "s16p"], "24": ["-sample_fmt", "s32p", "-bits_per_raw_sample", "24"]}
                                  .get(depth, []))]
    if f == "flac":
        return ["-c:a", "flac", "-compression_level", str(p["flac_level"]),
                *({"16": ["-sample_fmt", "s16"], "24": ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"]}.get(depth, []))]
    if f == "wav":
        return ["-c:a", {"16": "pcm_s16le", "24": "pcm_s24le", "32f": "pcm_f32le"}[depth]]
    if f == "aiff":
        return ["-c:a", {"16": "pcm_s16be", "24": "pcm_s24be", "32f": "pcm_s24be"}[depth], "-write_id3v2", "1"]
    raise ValueError(f"unknown format {f}")


def build_cmd(ffmpeg, job, p, out_tmp):
    """The output gets exactly the tags shown in the list, saved or not."""
    cmd = [ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", "-i", job.path,
           "-map", "0:a:0", "-map_metadata", "-1", "-map_chapters", "-1"]
    af = []
    if p["trim"]:
        quiet = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.1"
        af.append(f"{quiet},areverse,{quiet},areverse")
    if p["normalize"]:
        af.append(f"loudnorm=I={p['lufs']}:TP=-1.0:LRA=11")
    if af:
        cmd += ["-af", ",".join(af)]
    rate = p["rate"]
    if rate == "keep" and (p["normalize"] or p["fmt"] == "opus"):
        rate = str(job.info.get("rate") or 48000)   # loudnorm resamples internally; keep the source rate
    if p["fmt"] == "opus" and rate not in OPUS_RATES:
        rate = "48000"                               # Opus only supports these rates
    if rate != "keep":
        cmd += ["-ar", rate]
    if p["channels"] != "keep":
        cmd += ["-ac", p["channels"]]
    cmd += codec_args(p, job)
    for k, v in job.tags().items():
        if not v or k == "lyrics" or len(v) > 2000 or (k.startswith("replaygain") and (p["normalize"] or p["trim"])):
            continue
        cmd += ["-metadata", f"{FF_TAG.get(k, k)}={v}"]
    cmd += ["-f", FORMATS[p["fmt"]][2], out_tmp]
    return cmd


def cover_for(job):
    """Image bytes for the file's cover as shown (unsaved changes included), or None."""
    if isinstance(job.cover, bytes):
        return job.cover
    if job.cover == "remove":
        return None
    c = extract_cover(job.path)
    return c[0] if c else None


def finish_output(job, path, fmt):
    """After ffmpeg: what it can't write itself. Cover art, lyrics, and the ID3 tag of WAV/AIFF."""
    tags = job.tags()
    if fmt in ("wav", "aiff"):
        write_tags(path, {k: v for k, v in tags.items() if k != "lyrics" and key_ok(path, k)})
    data = cover_for(job)
    if data:
        write_cover(path, fmt, data)
    if tags.get("lyrics"):
        write_lyrics(path, tags["lyrics"])
