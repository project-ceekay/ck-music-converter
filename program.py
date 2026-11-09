r"""CK Music Converter - part of the CK Suite.

A music tagger and all-in-one audio converter. Edit tags like Mp3tag (many files at once,
< keep > / < blank >, save and undo, Tag -> Filename, Filename -> Tag, case / replace actions,
auto-numbering, extended tags, MusicBrainz lookup, cover art, CSV export) and convert any audio
(or the sound from a video) to MP3, AAC, ALAC, FLAC, WAV, AIFF, Ogg Vorbis or Opus with presets
or precise settings, loudness normalization and silence trimming. Saves to
Music\CK Music Converter by default.

Requires:  pip install pillow mutagen      and ffmpeg + ffprobe (next to the program, in its
           bin folder, or on PATH)
"""
import base64
import concurrent.futures
import copy
import csv
import difflib
import fnmatch
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags
from PIL import Image, ImageDraw, ImageTk

APP_NAME = "CK Music Converter"
VERSION = "1.0.0"
REPO = "project-ceekay/ck-music-converter"
SETTINGS_NAME = "Music Converter"  # %APPDATA%\CK Suite\<this>

# ======================================================================= CK UI kit
# The shared CK Suite look (native Windows ttk, polished). Each program carries its own
# copy so it compiles on its own.
ACCENT = "#0067c0"      # Windows 11 accent blue
ERROR = "#c42b1c"
SUCCESS = "#0f7b0f"
WARN = "#9d5d00"
MUTED = "#5f6368"
HEADER_BG = "#ffffff"
BORDER = "#d9d9d9"
HOVER = "#e5f1fb"
SCALE = 1.0  # screen scaling (1.0 at 100%); set by init_window()


def px(n):
    """n pixels at 100% scaling, converted to this screen's scaling."""
    return round(n * SCALE)


def app_dir():
    """Folder of the exe when compiled, otherwise of this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
    except Exception:
        pass


def reveal(path):
    """Open the folder with the file selected."""
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            open_path(os.path.dirname(path))
    except Exception:
        pass


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def icon_base(size):
    """(image, draw, unit) with the blue rounded square every CK icon starts from."""
    s = size / 64
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4 * s, 4 * s, 60 * s, 60 * s], radius=12 * s, fill=ACCENT)
    return im, d, s


def save_icon(make_icon, path):
    make_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def init_window(root, title, size, minsize, make_icon):
    """DPI awareness must already be on. Sets scaling, size (capped to the screen), icon, style."""
    global SCALE
    SCALE = root.winfo_fpixels("1i") / 96
    root.title(title)
    h = min(px(size[1]), root.winfo_screenheight() - px(80))
    root.geometry(f"{px(size[0])}x{h}")
    root.minsize(px(minsize[0]), min(px(minsize[1]), h))
    root._icon = ImageTk.PhotoImage(make_icon(64))
    root.iconphoto(True, root._icon)
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    if sys.platform.startswith("win"):
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name).configure(family="Segoe UI", size=10)
            except tk.TclError:
                pass
    line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(px(20), px(6)))
    style.configure("Footer.TButton", padding=(px(14), px(6)))
    style.configure("Toolbutton", padding=(px(10), px(3)))
    style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground=ACCENT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Error.TLabel", foreground=ERROR)
    style.configure("Success.TLabel", foreground=SUCCESS)
    style.configure("Warn.TLabel", foreground=WARN)
    style.configure("Invalid.TEntry", foreground=ERROR)
    style.configure("Treeview", rowheight=line + px(10))
    style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
    style.configure("TNotebook.Tab", padding=(px(10), px(4)))
    # the mouse wheel scrolls whichever ScrollFrame is under the pointer; number boxes and
    # drop-downs shouldn't change value just because the wheel passes over them
    root.bind_all("<MouseWheel>", lambda e: _on_wheel(root, e))
    for cls in ("TSpinbox", "TCombobox"):
        root.unbind_class(cls, "<MouseWheel>")


def enable_dpi_awareness():
    """Tell Windows we draw at real resolution, so text is sharp instead of stretched."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def build_header(root, title, subtitle):
    header = tk.Frame(root, bg=HEADER_BG)
    header.pack(fill="x")
    tk.Label(header, text=title, bg=HEADER_BG, fg="#1a1a1a",
             font=("Segoe UI Semibold", 16)).pack(anchor="w", padx=px(20), pady=(px(14), 0))
    tk.Label(header, text=subtitle, bg=HEADER_BG, fg=MUTED,
             font=("Segoe UI", 10)).pack(anchor="w", padx=px(20), pady=(0, px(14)))
    ttk.Separator(root).pack(fill="x")


class Footer(ttk.Frame):
    """Bottom bar: status (+ progress while busy) on the left, buttons added on the right.
    Create it before the body so it always stays visible."""

    def __init__(self, root):
        super().__init__(root, padding=(px(20), px(10)))
        self.pack(side="bottom", fill="x")
        ttk.Separator(root).pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(self, length=px(160), maximum=100)
        self.status = ttk.Label(self, text="Ready", style="Muted.TLabel")
        self.status.pack(side="left")

    def button(self, text, command, primary=False, **kw):
        b = ttk.Button(self, text=text, command=command, style="Go.TButton" if primary else "Footer.TButton",
                       default="active" if primary else "normal", **kw)
        b.pack(side="right", padx=(px(8), 0))
        return b

    def set_status(self, text, kind="muted"):
        self.status.configure(text=text, style={"muted": "Muted.TLabel", "error": "Error.TLabel",
                                                "ok": "Success.TLabel", "warn": "Warn.TLabel"}[kind])

    def set_progress(self, value):
        """0..100 shows the bar at that point; None hides it."""
        if value is None:
            self.progress.pack_forget()
            return
        if not self.progress.winfo_ismapped():
            self.progress.pack(side="left", padx=(0, px(12)), before=self.status)
        self.progress.configure(value=value)


def section(parent, text, **pack):
    box = ttk.Labelframe(parent, text=text, style="Section.TLabelframe", padding=px(10))
    box.pack(**(pack or {"fill": "x", "pady": (0, px(10))}))
    return box


def hint(parent, text, wrap=380, grid=None, **pack):
    """A muted explanatory line. Packed by default; pass grid={...} for grid layouts."""
    lbl = ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=px(wrap), justify="left")
    if grid is not None:
        lbl.grid(**grid)
    else:
        lbl.pack(anchor="w", **(pack or {"pady": (px(2), 0)}))
    return lbl


def segmented(parent, values, var, command=None):
    """A row of toggle buttons that work like radio buttons. values: list or (value, text) pairs."""
    row = ttk.Frame(parent)
    for v in values:
        value, text = v if isinstance(v, tuple) else (v, v)
        ttk.Radiobutton(row, text=text, value=value, variable=var, style="Toolbutton",
                        command=command).pack(side="left", padx=(0, px(2)))
    return row


def path_row(parent, row, label, var, browse, button="Browse…"):
    """Label / entry / button on one grid row of parent (column 1 stretches)."""
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=px(3))
    ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=px(8), pady=px(3))
    ttk.Button(parent, text=button, command=browse).grid(row=row, column=2, pady=px(3))
    parent.columnconfigure(1, weight=1)


class ScrollFrame(ttk.Frame):
    """A frame whose contents scroll vertically. Put widgets in .body."""

    def __init__(self, master, padding=0):
        super().__init__(master)
        bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, bg=bg, yscrollincrement=px(20))
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)

        def set_bar(lo, hi):  # the scrollbar only shows when the contents don't fit
            if float(lo) <= 0 and float(hi) >= 1:
                bar.pack_forget()
            elif not bar.winfo_ismapped():
                bar.pack(side="right", fill="y", before=self.canvas)
            bar.set(lo, hi)

        self.canvas.configure(yscrollcommand=set_bar)
        self.body = ttk.Frame(self.canvas, padding=padding)
        win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))

    def scroll(self, delta):
        if self.canvas.yview() != (0.0, 1.0):
            steps = -delta / 120 * 3
            self.canvas.yview_scroll(int(steps) or (1 if steps > 0 else -1), "units")


def _on_wheel(root, e):
    w = root.winfo_containing(e.x_root, e.y_root)
    while w is not None:
        if isinstance(w, (tk.Listbox, tk.Text, ttk.Treeview)):
            return  # these scroll themselves
        if isinstance(w, ScrollFrame):
            w.scroll(e.delta)
            return
        w = w.master


class SliderRow(ttk.Frame):
    """Label, slider and a number box you can type into. Double-click the slider to reset.
    Works in whole numbers, or in steps of `step` (e.g. 0.1) when given."""

    def __init__(self, master, text, lo, hi, value, on_change, step=1, label_width=16):
        super().__init__(master)
        self.on_change = on_change
        self.lo, self.hi, self.step, self.default = lo, hi, step, value
        self.value = value
        self._setting = False
        ttk.Label(self, text=text, width=label_width).grid(row=0, column=0, sticky="w")
        self.scale = ttk.Scale(self, from_=lo, to=hi, command=self._moved)
        self.scale.grid(row=0, column=1, sticky="ew", padx=(px(4), px(8)))
        self.spin_var = tk.StringVar()
        spin = ttk.Spinbox(self, from_=lo, to=hi, increment=step, textvariable=self.spin_var, width=6,
                           command=self._typed)
        spin.grid(row=0, column=2)
        spin.bind("<Return>", self._typed)
        spin.bind("<FocusOut>", self._typed)
        self.scale.bind("<Double-Button-1>", lambda e: self.after_idle(self.reset))
        self.columnconfigure(1, weight=1)
        self.set(value)

    def _snap(self, v):
        v = round(round(float(v) / self.step) * self.step, 6)
        v = max(self.lo, min(self.hi, v))
        return int(v) if self.step >= 1 else v

    def _text(self, v):
        return str(v) if self.step >= 1 else f"{v:g}"

    def _moved(self, v):
        if self._setting:
            return
        n = self._snap(v)
        if abs(float(v) - n) > 1e-9:
            self._set_scale(n)
        if n != self.value:
            self.value = n
            self.spin_var.set(self._text(n))
            self.on_change()

    def _typed(self, *_):
        try:
            n = self._snap(self.spin_var.get())
        except ValueError:
            self.spin_var.set(self._text(self.value))
            return
        if n != self.value:
            self.set(n)
            self.on_change()
        else:
            self.spin_var.set(self._text(n))

    def _set_scale(self, v):
        self._setting = True
        try:
            self.scale.set(v)
        finally:
            self._setting = False

    def reset(self):
        if self.value != self.default:
            self.set(self.default)
            self.on_change()

    def get(self):
        return self.value

    def set(self, v):
        self.value = self._snap(v)
        self._set_scale(self.value)
        self.spin_var.set(self._text(self.value))

    def enable(self, on):
        for w in self.winfo_children():
            try:
                w.state(["!disabled"] if on else ["disabled"])
            except (AttributeError, tk.TclError):
                pass


class LogBox(ttk.Frame):
    """A read-only, timestamped activity log."""

    def __init__(self, master, height=6):
        super().__init__(master)
        self.text = tk.Text(self, height=height, wrap="word", state="disabled", font=("Consolas", 9),
                            relief="flat", borderwidth=0, bg="#fbfbfb", padx=px(6), pady=px(4),
                            highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
        sb = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.tag_configure("error", foreground=ERROR)
        self.text.tag_configure("ok", foreground=SUCCESS)
        self.text.tag_configure("warn", foreground=WARN)

    def log(self, msg, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")


def make_tree(parent, columns, height=6, selectmode="extended"):
    """columns: list of (id, heading, width, stretch). Returns a Treeview with a scrollbar."""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height,
                        selectmode=selectmode)
    for cid, text, width, stretch in columns:
        tree.heading(cid, text=text, anchor="w")
        tree.column(cid, width=px(width), minwidth=px(40), stretch=stretch, anchor="w")
    sb = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.tag_configure("pending", foreground=MUTED)
    tree.tag_configure("working", foreground=ACCENT)
    tree.tag_configure("error", foreground=ERROR)
    tree.tag_configure("done", foreground=SUCCESS)
    frame.tree = tree
    return frame


def enable_file_drop(widget, on_drop):
    """Windows only: on_drop(paths) is called when files are dragged onto widget's window.
    Uses the classic WM_DROPFILES message directly, so no extra package is needed."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = LRESULT
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        WM_DROPFILES, GWLP_WNDPROC = 0x0233, -4
        hwnd = widget.winfo_id()
        drops = queue.Queue()

        def proc(h, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                paths = []
                for i in range(shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)):
                    size = shell32.DragQueryFileW(wparam, i, None, 0) + 1
                    buf = ctypes.create_unicode_buffer(size)
                    shell32.DragQueryFileW(wparam, i, buf, size)
                    paths.append(buf.value)
                shell32.DragFinish(wparam)
                drops.put(paths)  # never call Tk from in here (it crashes Python)
                return 0
            return user32.CallWindowProcW(old_proc, h, msg, wparam, lparam)

        widget._drop_proc = WNDPROC(proc)  # must stay referenced while the window exists
        shell32.DragAcceptFiles(hwnd, True)
        old_proc = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(widget._drop_proc, ctypes.c_void_p))

        def check():
            while not drops.empty():
                on_drop(drops.get())
            widget.after(150, check)

        check()
    except Exception:
        pass  # drag and drop is a convenience; the Browse buttons still work


def expand_drop(paths, exts):
    """Dropped files and the matching files inside dropped folders, filtered by extension."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root_, _, files in os.walk(p):
                out += [os.path.join(root_, f) for f in sorted(files) if f.lower().endswith(exts)]
        elif p.lower().endswith(exts):
            out.append(p)
    return [os.path.normpath(p) for p in out]


NO_WINDOW = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}


def find_tool(name):
    """ffmpeg/ffprobe/ffplay: next to the program (or its bin folder) first, then PATH."""
    exe = name + (".exe" if sys.platform.startswith("win") else "")
    for d in (app_dir(), os.path.join(app_dir(), "bin")):
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    return shutil.which(name)
# ---------------------------------------------------------------- settings & updates
# Each program defines APP_NAME, VERSION, REPO ("owner/name") and SETTINGS_NAME before this block.
import re as _re
import tempfile as _tempfile
import urllib.error as _urlerror
import urllib.request as _urlrequest
import webbrowser as _webbrowser


def data_dir():
    """Per-user settings folder: %APPDATA%\\CK Suite\\<SETTINGS_NAME>."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "CK Suite", SETTINGS_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def settings_file(old_paths=(), name="config.json"):
    """data_dir()/name; the first existing old location is copied over once."""
    path = os.path.join(data_dir(), name)
    if not os.path.exists(path):
        for old in old_paths:
            if old and os.path.isfile(old):
                try:
                    shutil.copy2(old, path)
                except OSError:
                    pass
                break
    return path


def version_tuple(text):
    return tuple(int(x) for x in _re.findall(r"\d+", text)[:3]) or (0,)


def latest_release():
    """{'version', 'notes', 'url', 'page'} of the newest GitHub release; {} if none yet."""
    req = _urlrequest.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                              headers={"User-Agent": "CK-Suite-Updater", "Accept": "application/vnd.github+json"})
    try:
        with _urlrequest.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except _urlerror.HTTPError as e:
        if e.code == 404:
            return {}
        raise
    asset = next((a for a in data.get("assets", []) if a["name"].lower().endswith("setup.exe")), None)
    return {"version": data.get("tag_name", "").lstrip("v"), "notes": data.get("body") or "",
            "url": asset["browser_download_url"] if asset else None, "page": data.get("html_url")}


class Updater:
    """About box + daily update check + one-click install, living in the footer `bar`.
    status(text, kind) / progress(0..100 or None) report to the window; quit() saves and closes it."""

    def __init__(self, root, bar, cfg, make_icon, status, progress, quit):
        self.root, self.cfg, self.make_icon = root, cfg, make_icon
        self.status, self.progress, self.quit = status, progress, quit
        self.auto = tk.BooleanVar(value=cfg.get("auto_update", True))
        self.release = None
        self.about_lbl = None
        ttk.Style(root).configure("Link.TLabel", foreground="#0067c0")
        about = ttk.Label(bar, text=f"About · v{VERSION}", style="Link.TLabel", cursor="hand2")
        about.pack(side="right", padx=(0, self._px(16)))
        about.bind("<Button-1>", lambda e: self.show_about())
        self.link = ttk.Label(bar, text="", style="Link.TLabel", cursor="hand2", font=("Segoe UI Semibold", 10))
        self.link.bind("<Button-1>", lambda e: self.offer())
        if self.auto.get() and time.time() - cfg.get("last_update_check", 0) > 20 * 3600:
            root.after(2500, lambda: self.check(quiet=True))

    def state(self):
        return {"auto_update": self.auto.get(), "last_update_check": self.cfg.get("last_update_check", 0)}

    def _px(self, n):
        return round(n * self.root.winfo_fpixels("1i") / 96)

    def _dialog(self, title):
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=self._px(18))
        body.pack(fill="both", expand=True)
        return win, body

    def _show(self, win):
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + self._px(110)}+{self.root.winfo_rooty() + self._px(110)}")
        win.deiconify()
        win.grab_set()

    def show_about(self):
        win, body = self._dialog(f"About {APP_NAME}")
        P = self._px
        self._icon = ImageTk.PhotoImage(self.make_icon(P(56)))
        ttk.Label(body, image=self._icon).grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, P(14)))
        ttk.Label(body, text=APP_NAME, font=("Segoe UI Semibold", 13)).grid(row=0, column=1, sticky="w")
        ttk.Label(body, text=f"Version {VERSION}  ·  CK Suite  ·  © CK Software  ·  MIT License",
                  foreground="#5f6368").grid(row=1, column=1, sticky="w")
        link = ttk.Label(body, text=f"github.com/{REPO}", style="Link.TLabel", cursor="hand2")
        link.grid(row=2, column=1, sticky="w", pady=(P(4), 0))
        link.bind("<Button-1>", lambda e: _webbrowser.open(f"https://github.com/{REPO}"))
        ttk.Separator(body).grid(row=3, column=0, columnspan=2, sticky="ew", pady=P(14))
        ttk.Checkbutton(body, text="Check for updates automatically (once a day)", variable=self.auto).grid(
            row=4, column=0, columnspan=2, sticky="w")
        self.about_lbl = ttk.Label(body, text="", foreground="#5f6368")
        self.about_lbl.grid(row=5, column=0, columnspan=2, sticky="w", pady=(P(8), 0))
        bar = ttk.Frame(body)
        bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(P(14), 0))
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="Check for Updates Now",
                   command=lambda: self.check(quiet=False)).pack(side="right", padx=(0, P(8)))
        self._show(win)

    def _about(self, text):
        if self.about_lbl is not None and self.about_lbl.winfo_exists():
            self.about_lbl.configure(text=text)

    def check(self, quiet):
        self._about("Checking…")
        result = {}

        def work():
            try:
                result["rel"] = latest_release()
            except Exception:
                result["rel"] = None

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def done():
            if t.is_alive():
                self.root.after(200, done)
                return
            self.cfg["last_update_check"] = time.time()
            rel = result.get("rel")
            if rel is None:
                self._about("Couldn't reach GitHub. Check your internet connection.")
            elif not rel:
                self._about(f"No releases published yet. You have version {VERSION}.")
            elif version_tuple(rel["version"]) > version_tuple(VERSION):
                self.release = rel
                self.link.configure(text=f"⬆ Version {rel['version']} is available")
                self.link.pack(side="right", padx=(0, self._px(16)))
                self._about(f"Version {rel['version']} is available (you have {VERSION}).")
                if not quiet:
                    self.offer()
            else:
                self._about(f"You have the latest version ({VERSION}).")

        done()

    def offer(self):
        rel = self.release
        if not rel:
            return
        win, body = self._dialog("Update available")
        P = self._px
        ttk.Label(body, text=f"{APP_NAME} {rel['version']} is available", font=("Segoe UI Semibold", 12)).pack(anchor="w")
        ttk.Label(body, text=f"You have version {VERSION}. What's new:",
                  foreground="#5f6368").pack(anchor="w", pady=(P(2), P(8)))
        notes = tk.Text(body, height=10, width=64, wrap="word", font=("Segoe UI", 10), relief="flat",
                        highlightthickness=1, highlightbackground="#d9d9d9", padx=P(8), pady=P(6))
        notes.insert("1.0", rel["notes"].strip() or "(no release notes)")
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True)
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(P(12), 0))
        if getattr(sys, "frozen", False) and rel["url"]:
            ttk.Button(bar, text="Update Now", default="active",
                       command=lambda: (win.destroy(), self.install(rel))).pack(side="right")
        else:  # running from source: send them to the release page
            ttk.Button(bar, text="Open Download Page", default="active",
                       command=lambda: (win.destroy(), _webbrowser.open(rel["page"]))).pack(side="right")
        ttk.Button(bar, text="Later", command=win.destroy).pack(side="right", padx=(0, P(8)))
        self._show(win)

    def install(self, rel):
        """Download the installer, run it quietly and close; the installer reopens the program."""
        if getattr(self.root, "busy", False):
            messagebox.showinfo(APP_NAME, "Wait for the current job to finish, then update.")
            return
        self.status(f"Downloading version {rel['version']}…", "muted")
        self.progress(0)
        dest = os.path.join(_tempfile.gettempdir(), "CK Suite updates", os.path.basename(rel["url"]))
        st = {"frac": 0.0}

        def work():
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                req = _urlrequest.Request(rel["url"], headers={"User-Agent": "CK-Suite-Updater"})
                with _urlrequest.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
                    total, got = int(r.headers.get("Content-Length") or 0), 0
                    while chunk := r.read(256 * 1024):
                        f.write(chunk)
                        got += len(chunk)
                        st["frac"] = got / total if total else 0
                st["ok"] = True
            except Exception as e:
                st["err"] = e

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def watch():
            self.progress(st["frac"] * 100)
            if t.is_alive():
                self.root.after(150, watch)
                return
            self.progress(None)
            if not st.get("ok"):
                self.status(f"Download failed: {st.get('err')}", "error")
                return
            try:
                subprocess.Popen([dest, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
            except OSError as e:
                self.status(f"Couldn't start the installer: {e}", "error")
                return
            self.quit()

        watch()
# ===================================================================== end CK UI kit

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


def make_icon(size=64):
    """A white music note on the CK blue."""
    im, d, s = icon_base(size)
    d.ellipse([15 * s, 38 * s, 31 * s, 52 * s], fill="white")
    d.rectangle([27 * s, 13 * s, 31 * s, 46 * s], fill="white")
    d.polygon([(31 * s, 13 * s), (47 * s, 19 * s), (47 * s, 27 * s), (31 * s, 21 * s)], fill="white")
    return im


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


def _id3_version(tags, existed):
    """Keep an existing ID3v2.4 tag at 2.4; new tags and 2.3 tags are written as 2.3 (widest support)."""
    return 4 if existed and tuple(getattr(tags, "version", (2, 3, 0))) >= (2, 4, 0) else 3


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


# ------------------------------------------------------------- lyrics: one property, native storage
# The editor only knows one field, "lyrics". Each codec below maps it onto the format's own
# structure. write_lyrics() validates the file and the text before anything touches the disk,
# then reads the result back to make sure it landed.
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
    """iTunes / QuickTime: the \u00a9lyr atom (A9 6C 79 72) in moov.udta.meta.ilst, holding a 'data'
    atom with type flags 0x000001 (UTF-8 text) and locale 0x00000000."""
    containers = ("mp4",)
    label = "iTunes \u00a9lyr atom (moov.udta.meta.ilst)"
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
    """Lyrics as they'll be stored: text, \n line breaks, no NUL bytes (they'd end a USLT frame early)."""
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


# ------------------------------------------------------------- the file list
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


# ------------------------------------------------------------- dialogs
def make_dialog(root, title, resizable=False):
    win = tk.Toplevel(root)
    win.withdraw()
    win.title(title)
    win.transient(root)
    win.resizable(resizable, resizable)
    body = ttk.Frame(win, padding=px(16))
    body.pack(fill="both", expand=True)
    return win, body


def show_dialog(root, win):
    win.bind("<Escape>", lambda e: win.destroy())
    win.update_idletasks()
    win.geometry(f"+{root.winfo_rootx() + px(90)}+{root.winfo_rooty() + px(60)}")
    win.deiconify()
    win.grab_set()


class PreviewDialog:
    """Inputs on top (build(top, refresh)), a live Before/After list, Apply/Cancel.
    preview() -> (rows, note); apply() returns False to keep the dialog open."""

    def __init__(self, app, title, build, preview, apply, ok_text="Apply", heads=("Before", "After")):
        self.preview, self.apply, self._job = preview, apply, None
        self.win, body = make_dialog(app, title, resizable=True)
        top = ttk.Frame(body)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        build(top, self.refresh)
        tf = make_tree(body, [("a", heads[0], 300, True), ("b", heads[1], 300, True)], height=10, selectmode="none")
        tf.pack(fill="both", expand=True, pady=(px(12), 0))
        self.tree = tf.tree
        self.note = ttk.Label(body, text="", style="Muted.TLabel")
        self.note.pack(anchor="w", pady=(px(6), 0))
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(px(12), 0))
        ttk.Button(bar, text="Cancel", command=self.win.destroy).pack(side="right")
        ttk.Button(bar, text=ok_text, style="Go.TButton", command=self.ok).pack(side="right", padx=(0, px(8)))
        self.win.bind("<Return>", lambda e: self.ok())
        show_dialog(app, self.win)
        self.refresh()

    def refresh(self, *_):
        if self._job:
            self.win.after_cancel(self._job)
        self._job = self.win.after(120, self._refresh)

    def _refresh(self):
        self._job = None
        try:
            rows, note = self.preview()
        except re.error as e:
            rows, note = [], f"That pattern isn't valid: {e}"
        except Exception as e:
            rows, note = [], str(e)
        self.tree.delete(*self.tree.get_children())
        for a, b in rows[:1000]:
            self.tree.insert("", "end", values=(a, b))
        self.note.configure(text=note)

    def ok(self):
        try:
            result = self.apply()
        except re.error as e:
            messagebox.showerror(APP_NAME, f"That pattern isn't valid: {e}", parent=self.win)
            return
        if result is not False:
            self.win.destroy()


# ------------------------------------------------------------------ app
class App(tk.Tk):
    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        init_window(self, APP_NAME, (1180, 760), (980, 600), make_icon)
        ttk.Style(self).configure("Treeview", rowheight=tkfont.nametofont("TkDefaultFont").metrics("linespace") + px(5))
        self.ffmpeg, self.ffprobe = find_tool("ffmpeg"), find_tool("ffprobe")
        self.config_file = settings_file([os.path.join(os.environ.get("APPDATA") or "", "CK Suite", "Album Audio Replacer", "config.json"),
                                          os.path.join(app_dir(), "config.json")])
        self.cfg = load_json(self.config_file, {})
        self.user_presets = self.cfg.get("user_presets", {})
        self.p = dict(DEFAULT_PARAMS, **{k: v for k, v in self.cfg.get("params", {}).items() if k in DEFAULT_PARAMS})
        self.save_mode = tk.StringVar(value=self.cfg.get("save_mode", "music"))
        self.out_dir = tk.StringVar(value=self.cfg.get("out_dir", ""))
        self.keep_structure = tk.BooleanVar(value=self.cfg.get("keep_structure", True))
        self.pattern = tk.StringVar(value=self.cfg.get("pattern", "{name}"))
        self.collide = tk.StringVar(value=self.cfg.get("collide", "rename"))
        self.workers = tk.IntVar(value=self.cfg.get("workers", max(1, min(4, (os.cpu_count() or 2) // 2))))
        self.last_dir = self.cfg.get("last_dir", os.path.expanduser("~"))
        saved_recent = self.cfg.get("recent_formats", {})
        self.recent = {k: list(saved_recent.get(k) or v) for k, v in RECENT_DEFAULTS.items()}
        self.jobs = {}          # tree iid -> Job
        self.order = []         # all iids in list order (the filter hides some)
        self.undo_stack = []
        self.sort_col, self.sort_rev = None, False
        self.panel_jobs, self.panel_loaded, self._panel_loading = [], {}, False
        self.busy = False
        self.cancel = threading.Event()
        self.procs = set()
        self.last_output = None
        self._loading = False
        self.probed = queue.Queue()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        self.build_ui()
        self.load_params_into_ui()
        self.load_panel(commit=False)
        enable_file_drop(self, self.on_drop)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        for seq, fn in (("<Control-s>", self.save_tags), ("<Control-z>", self.undo), ("<Control-o>", self.browse_files),
                        ("<Control-d>", self.browse_folder), ("<F5>", self.reload_tags)):
            self.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
        self.after(200, self.check_ffmpeg)
        self.after(200, self.drain_probes)

    # ---------------------------------------------------------- UI
    def build_ui(self):
        build_header(self, "Music Converter",
                     "Tag your music like a pro and convert it to any format, keeping the tags and cover art.")
        self.footer = Footer(self)
        self.go_btn = self.footer.button("Convert", self.start, primary=True)
        self.footer.button("Save Tags", self.save_tags)
        self.footer.button("Open Folder", self.open_out)
        self.updater = Updater(self, self.footer, self.cfg, make_icon, self.footer.set_status,
                               self.footer.set_progress, self.on_close)
        self.cancel_btn = ttk.Button(self.footer, text="Cancel", style="Footer.TButton", command=self.do_cancel)
        self.build_toolbar()

        body = ttk.Frame(self, padding=(px(16), px(8), px(16), px(10)))
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=px(340))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.side = ttk.Notebook(left)
        self.side.pack(fill="both", expand=True)
        self.build_tag_panel()
        self.build_convert_panel()
        act = ttk.Frame(self.side, padding=px(10))
        self.side.add(act, text="  Activity  ")
        self.act_tab = act
        self.logbox = LogBox(act)
        self.logbox.pack(fill="both", expand=True)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(px(14), 0))
        top = ttk.Frame(right)
        top.pack(fill="x", pady=(0, px(6)))
        ttk.Label(top, text="Filter").pack(side="left")
        self.filter_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.filter_var, width=34).pack(side="left", padx=(px(6), 0))
        self.filter_var.trace_add("write", lambda *_: self.apply_filter())
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="right")
        tf = make_tree(right, COLUMNS, height=12)
        tf.pack(fill="both", expand=True)
        self.tree = tf.tree
        for cid, text, *_ in COLUMNS:
            self.tree.heading(cid, text=text, command=lambda c=cid: self.sort_by(c))
        self.tree.tag_configure("modified", foreground=ACCENT)
        self.tree.bind("<<TreeviewSelect>>", lambda e: (self.load_panel(), self.update_count(), self.update_estimate()))
        self.tree.bind("<Double-1>", self.open_row)
        self.tree.bind("<Delete>", lambda e: self.remove())
        self.tree.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        self.empty_hint = hint(right, "Drop music files or whole folders anywhere on this window. Select files to edit "
                                      "their tags on the left (edits show in blue until you press Save), then convert "
                                      "them with the settings on the Convert tab.", wrap=900, pady=(px(8), 0))
        self.update_count()

    def build_toolbar(self):
        bar = ttk.Frame(self, padding=(px(16), px(8), px(16), 0))
        bar.pack(fill="x")

        def btn(text, cmd, gap=4):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, px(gap)))

        def menu(text, items, gap=4):
            mb = ttk.Menubutton(bar, text=text)
            m = tk.Menu(mb, tearoff=False)
            for item in items:
                if item is None:
                    m.add_separator()
                else:
                    m.add_command(label=item[0], command=item[1], accelerator=item[2] if len(item) > 2 else "")
            mb["menu"] = m
            mb.pack(side="left", padx=(0, px(gap)))

        btn("Add Folder…", self.browse_folder)
        btn("Add Files…", self.browse_files, 16)
        btn("Save", self.save_tags)
        btn("Undo", self.undo, 16)
        menu("Convert Names", [("Tag → Filename…", self.tag_to_filename), ("Filename → Tag…", self.filename_to_tag),
                               ("Tag → Tag…", self.tag_to_tag)])
        menu("Actions", [("Change Case…", self.action_case), ("Replace…", self.action_replace),
                         ("Remove Fields…", self.action_remove), ("Trim Spaces", self.action_trim), None,
                         ("Auto-Number Tracks…", self.auto_number)])
        menu("Tag Sources", [("MusicBrainz…", self.musicbrainz), ("Copy Tags From Folder…", self.match_tags)])
        btn("Extended Tags…", self.extended_tags)
        menu("More", [("Export Tags to CSV…", self.export_csv), ("Reload Tags From Files", self.reload_tags, "F5"),
                      None, ("Select All", self.select_all, "Ctrl+A")])
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="right")
        ttk.Button(bar, text="Remove", command=self.remove).pack(side="right", padx=(0, px(4)))

    def build_tag_panel(self):
        tab = ttk.Frame(self.side, padding=(px(8), px(6), px(2), px(4)))
        self.side.add(tab, text="  Tags  ")
        self.tag_tab = tab
        sf = ScrollFrame(tab, padding=(0, 0, px(8), 0))
        sf.pack(fill="both", expand=True)
        p = sf.body
        p.columnconfigure(0, weight=1)
        self.panel_title = ttk.Label(p, text="", font=("Segoe UI Semibold", 10), wraplength=px(300))
        self.panel_title.grid(row=0, column=0, sticky="w", pady=(0, px(4)))
        g = ttk.Frame(p)
        g.grid(row=1, column=0, sticky="ew")
        g.columnconfigure(1, weight=1)
        self.field_vars, self.field_boxes = {}, {}

        def combo(parent, key, width=8):
            v = tk.StringVar()
            cb = ttk.Combobox(parent, textvariable=v, width=width, height=16)
            cb.bind("<Return>", lambda e: self.commit_panel())
            cb.bind("<FocusOut>", lambda e: self.commit_panel(reload=False))
            cb.bind("<<ComboboxSelected>>", lambda e: self.commit_panel())
            self.field_vars[key], self.field_boxes[key] = v, cb
            return cb

        def label(r, text):
            ttk.Label(g, text=text).grid(row=r, column=0, sticky="w", padx=(0, px(8)), pady=px(2))

        r = 0
        for key in ("title", "artist", "album", "albumartist"):
            label(r, FIELD_LABEL[key])
            combo(g, key).grid(row=r, column=1, sticky="ew", pady=px(2))
            r += 1
        label(r, "Year")
        nums = ttk.Frame(g)
        nums.grid(row=r, column=1, sticky="ew", pady=px(2))
        for i, (key, text) in enumerate((("date", None), ("tracknumber", "Track"), ("discnumber", "Disc"))):
            if text:
                ttk.Label(nums, text=text).grid(row=0, column=2 * i - 1, padx=(px(8), px(4)))
            combo(nums, key, width=4).grid(row=0, column=2 * i, sticky="ew")
            nums.columnconfigure(2 * i, weight=1, uniform="n")
        r += 1
        for key in ("genre", "composer", "comment"):
            label(r, FIELD_LABEL[key])
            combo(g, key).grid(row=r, column=1, sticky="ew", pady=px(2))
            r += 1
        label(r, "Filename")
        combo(g, "__filename__").grid(row=r, column=1, sticky="ew", pady=px(2))
        r += 1
        label(r, "Directory")
        self.dir_var = tk.StringVar()
        ttk.Entry(g, textvariable=self.dir_var, state="readonly", width=8).grid(row=r, column=1, sticky="ew", pady=px(2))

        lf = ttk.Frame(p)
        lf.grid(row=2, column=0, sticky="ew", pady=(px(8), 0))
        lf.columnconfigure(0, weight=1)
        head = ttk.Frame(lf)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="Lyrics").pack(side="left")
        ttk.Button(head, text="Clear", style="Toolbutton", command=self.clear_lyrics).pack(side="right")
        ttk.Button(head, text="Edit…", style="Toolbutton", command=self.edit_lyrics).pack(side="right", padx=(0, px(2)))
        self.lyrics_box = tk.Text(lf, height=4, wrap="word", font=("Segoe UI", 9), relief="flat", undo=True,
                                  highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                                  padx=px(5), pady=px(3))
        self.lyrics_box.grid(row=1, column=0, sticky="ew", pady=(px(3), 0))
        self.lyrics_box.bind("<FocusIn>", self.lyrics_focus)
        self.lyrics_box.bind("<FocusOut>", lambda e: self.commit_panel(reload=False))
        self.lyrics_box.bind("<Control-Return>", lambda e: (self.commit_panel(), "break")[1])

        art = ttk.Frame(p)
        art.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        art.columnconfigure(1, weight=1)
        holder = tk.Frame(art, width=px(112), height=px(112), bg="#ececec", highlightthickness=1, highlightbackground=BORDER)
        holder.grid(row=0, column=0, rowspan=4, sticky="nw")
        holder.pack_propagate(False)
        self.cover_lbl = tk.Label(holder, bg="#ececec", fg=MUTED, text="No cover", font=("Segoe UI", 9), wraplength=px(100))
        self.cover_lbl.pack(fill="both", expand=True)
        for i, (text, cmd) in enumerate((("Add Cover…", self.pick_cover), ("Extract Cover…", self.extract_cover_file),
                                         ("Remove Cover", lambda: self.stage_cover("remove")))):
            ttk.Button(art, text=text, command=cmd).grid(row=i, column=1, sticky="ew", padx=(px(10), 0),
                                                         pady=(px(3) if i else 0, 0))
        self.cover_info = ttk.Label(art, text="", style="Muted.TLabel", wraplength=px(170), justify="left")
        self.cover_info.grid(row=3, column=1, sticky="nw", padx=(px(10), 0), pady=(px(4), 0))
        hint(p, "With several files selected, < keep > leaves each file's value and < blank > clears it. Edits "
                "show in blue until you Save (Ctrl+S); Ctrl+Z undoes. Drop an image on the window to make it "
                "the cover.", wrap=300, grid=dict(row=4, column=0, sticky="w", pady=(px(10), 0)))

    def build_convert_panel(self):
        tab = ttk.Frame(self.side, padding=(px(8), px(6), px(2), px(4)))
        self.side.add(tab, text="  Convert  ")
        self.conv_tab = tab
        sf = ScrollFrame(tab, padding=(0, 0, px(8), 0))
        sf.pack(fill="both", expand=True)
        panel = sf.body

        s = section(panel, "Preset")
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(s, textvariable=self.preset_var, state="readonly")
        self.preset_box.pack(fill="x")
        self.preset_box.bind("<<ComboboxSelected>>", lambda e: (self.preset_box.selection_clear(), self.apply_preset()))
        row = ttk.Frame(s)
        row.pack(fill="x", pady=(px(6), 0))
        ttk.Button(row, text="Save as Preset…", command=self.save_preset).pack(side="left")
        ttk.Button(row, text="Delete Preset", command=self.delete_preset).pack(side="left", padx=(px(6), 0))
        self.refresh_presets()

        s = section(panel, "Format")
        fbox = ttk.Combobox(s, state="readonly", values=[v[0] for v in FORMATS.values()])
        fbox.pack(fill="x")
        fbox.bind("<<ComboboxSelected>>", lambda e: (fbox.selection_clear(), self.set_param(
            "fmt", next(k for k, v in FORMATS.items() if v[0] == fbox.get()))))
        self.fmt_box = fbox
        self.fmt_opts = ttk.Frame(s)
        self.fmt_opts.pack(fill="x", pady=(px(8), 0))

        s = section(panel, "Audio")
        g = ttk.Frame(s)
        g.pack(fill="x")
        ttk.Label(g, text="Sample rate", width=13).grid(row=0, column=0, sticky="w")
        self.rate_box = ttk.Combobox(g, state="readonly", width=16,
                                     values=["Keep original", "44100 Hz", "48000 Hz", "88200 Hz", "96000 Hz",
                                             "32000 Hz", "22050 Hz"])
        self.rate_box.grid(row=0, column=1, sticky="w", pady=px(2))
        self.rate_box.bind("<<ComboboxSelected>>", lambda e: (self.rate_box.selection_clear(), self.set_param(
            "rate", "keep" if self.rate_box.get().startswith("Keep") else self.rate_box.get().split()[0])))
        ttk.Label(g, text="Channels", width=13).grid(row=1, column=0, sticky="w")
        self.ch_var = tk.StringVar()
        segmented(g, [("keep", "Keep"), ("2", "Stereo"), ("1", "Mono")], self.ch_var,
                  lambda: self.set_param("channels", self.ch_var.get())).grid(row=1, column=1, sticky="w", pady=px(2))
        self.norm_var = tk.BooleanVar()
        ttk.Checkbutton(s, text="Normalize loudness (even volume)", variable=self.norm_var,
                        command=lambda: self.set_param("normalize", self.norm_var.get())).pack(anchor="w", pady=(px(8), 0))
        self.lufs = SliderRow(s, "Target (LUFS)", -24, -8, -14, lambda: self.set_param("lufs", self.lufs.get()), label_width=13)
        self.lufs.pack(fill="x", padx=(px(22), 0))
        hint(s, "-14 matches Spotify / YouTube, -16 suits Apple Music and podcasts, -23 is broadcast.", wrap=290,
             padx=(px(22), 0))
        self.trim_var = tk.BooleanVar()
        ttk.Checkbutton(s, text="Trim silence at the start and end", variable=self.trim_var,
                        command=lambda: self.set_param("trim", self.trim_var.get())).pack(anchor="w", pady=(px(8), 0))

        s = section(panel, "Save to")
        s.columnconfigure(1, weight=1)
        ttk.Radiobutton(s, text="Music\\CK Music Converter", value="music", variable=self.save_mode,
                        command=self.update_out_controls).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(s, text="Next to the original files", value="beside", variable=self.save_mode,
                        command=self.update_out_controls).grid(row=1, column=0, columnspan=3, sticky="w", pady=(px(2), 0))
        ttk.Radiobutton(s, text="This folder:", value="folder", variable=self.save_mode,
                        command=self.update_out_controls).grid(row=2, column=0, sticky="w", pady=(px(2), 0))
        self.out_entry = ttk.Entry(s, textvariable=self.out_dir, width=12)
        self.out_entry.grid(row=2, column=1, sticky="ew", padx=px(6), pady=(px(2), 0))
        self.out_btn = ttk.Button(s, text="Browse…", command=self.browse_out)
        self.out_btn.grid(row=2, column=2, pady=(px(2), 0))
        self.struct_chk = ttk.Checkbutton(s, text="Keep the folder structure of dropped folders", variable=self.keep_structure)
        self.struct_chk.grid(row=3, column=0, columnspan=3, sticky="w", pady=(px(8), 0))
        ttk.Label(s, text="File names").grid(row=4, column=0, sticky="w", pady=(px(8), 0))
        pat = ttk.Combobox(s, textvariable=self.pattern, width=12,
                           values=["{name}", "%artist% - %title%", "%track% - %title%", "%track% %artist% - %title%"])
        pat.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(px(6), 0), pady=(px(8), 0))
        hint(s, "{name} keeps the original file name; or use placeholders like %artist% %title% %album% %track% %year%.",
             wrap=290, grid=dict(row=5, column=0, columnspan=3, sticky="w"))
        ttk.Label(s, text="If a file exists").grid(row=6, column=0, sticky="w", pady=(px(8), 0))
        segmented(s, [("rename", "Add a number"), ("overwrite", "Replace"), ("skip", "Skip")], self.collide).grid(
            row=6, column=1, columnspan=2, sticky="w", padx=(px(6), 0), pady=(px(8), 0))
        ttk.Label(s, text="At once").grid(row=7, column=0, sticky="w", pady=(px(8), 0))
        ttk.Spinbox(s, from_=1, to=max(2, os.cpu_count() or 2), textvariable=self.workers, width=5).grid(
            row=7, column=1, sticky="w", padx=(px(6), 0), pady=(px(8), 0))
        self.path_lbl = ttk.Label(s, text="", style="Muted.TLabel", wraplength=px(290))
        self.path_lbl.grid(row=8, column=0, columnspan=3, sticky="w", pady=(px(8), 0))
        self.est_lbl = ttk.Label(s, text="", style="Muted.TLabel", wraplength=px(290))
        self.est_lbl.grid(row=9, column=0, columnspan=3, sticky="w", pady=(px(4), 0))
        self.ff_lbl = ttk.Label(s, text="", style="Muted.TLabel", wraplength=px(290))
        self.ff_lbl.grid(row=10, column=0, columnspan=3, sticky="w", pady=(px(4), 0))
        for var in (self.save_mode, self.out_dir, self.pattern, self.keep_structure):
            var.trace_add("write", lambda *_: self.update_path_label())
        self.update_out_controls()

    # ---------------------------------------------------------- settings / presets
    def refresh_presets(self):
        self.preset_box.configure(values=list(PRESETS) + sorted(self.user_presets) + [CUSTOM])

    def matching_preset(self):
        for name, pre in list(PRESETS.items()) + list(self.user_presets.items()):
            if dict(DEFAULT_PARAMS, **pre) == self.p:
                return name
        return CUSTOM

    def apply_preset(self):
        name = self.preset_var.get()
        pre = PRESETS.get(name) or self.user_presets.get(name)
        if pre is None:
            return
        self.p = dict(DEFAULT_PARAMS, **pre)
        self.load_params_into_ui()
        self.footer.set_status(f"Preset: {name}")

    def save_preset(self):
        name = simpledialog.askstring(APP_NAME, "Name for this preset:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in PRESETS or name == CUSTOM:
            messagebox.showinfo(APP_NAME, "That name is already used by a built-in preset.")
            return
        self.user_presets[name] = {k: v for k, v in self.p.items() if v != DEFAULT_PARAMS[k] or k == "fmt"}
        self.refresh_presets()
        self.preset_var.set(name)
        self.footer.set_status(f"Saved preset \"{name}\"", "ok")

    def delete_preset(self):
        name = self.preset_var.get()
        if name in self.user_presets and messagebox.askyesno(APP_NAME, f"Delete the preset \"{name}\"?"):
            del self.user_presets[name]
            self.refresh_presets()
            self.preset_var.set(self.matching_preset())
        elif name in PRESETS:
            messagebox.showinfo(APP_NAME, "Built-in presets can't be deleted.")

    def set_param(self, key, value):
        if self._loading:
            return
        self.p[key] = value
        if key in ("fmt", "mp3_mode"):
            self.build_fmt_opts()
        self.preset_var.set(self.matching_preset())
        self.update_estimate()

    def load_params_into_ui(self):
        self._loading = True
        try:
            p = self.p
            self.fmt_box.set(FORMATS[p["fmt"]][0])
            self.rate_box.set("Keep original" if p["rate"] == "keep" else f"{p['rate']} Hz")
            self.ch_var.set(p["channels"])
            self.norm_var.set(p["normalize"])
            self.lufs.set(p["lufs"])
            self.trim_var.set(p["trim"])
            self.build_fmt_opts()
            self.preset_var.set(self.matching_preset())
        finally:
            self._loading = False
        self.update_estimate()

    def build_fmt_opts(self):
        """The precise settings for the chosen format."""
        fr = self.fmt_opts
        for w in fr.winfo_children():
            w.destroy()
        p, f = self.p, self.p["fmt"]
        was = self._loading
        self._loading = True

        def combo(row, label, values, current, key, conv=str):
            ttk.Label(fr, text=label, width=13).grid(row=row, column=0, sticky="w", pady=px(2))
            cb = ttk.Combobox(fr, state="readonly", width=22, values=[t for _, t in values])
            cb.set(next((t for v, t in values if v == current), values[0][1]))
            cb.grid(row=row, column=1, sticky="w", pady=px(2))
            cb.bind("<<ComboboxSelected>>", lambda e: (cb.selection_clear(), self.set_param(
                key, conv(next(v for v, t in values if t == cb.get())))))

        if f == "mp3":
            self.mp3_mode_var = tk.StringVar(value=p["mp3_mode"])
            ttk.Label(fr, text="Mode", width=13).grid(row=0, column=0, sticky="w")
            segmented(fr, [("cbr", "Constant"), ("vbr", "Variable (VBR)")], self.mp3_mode_var,
                      lambda: self.set_param("mp3_mode", self.mp3_mode_var.get())).grid(row=0, column=1, sticky="w", pady=px(2))
            if p["mp3_mode"] == "cbr":
                combo(1, "Bitrate", [(b, f"{b} kbps") for b in (96, 128, 160, 192, 224, 256, 320)], p["bitrate"], "bitrate", int)
            else:
                combo(1, "Quality", [(q, f"V{q}  (~{VBR_KBPS[q]} kbps)" + ("  best" if q == 0 else "")) for q in range(10)],
                      p["vbr"], "vbr", int)
        elif f == "aac":
            combo(0, "Bitrate", [(b, f"{b} kbps") for b in (96, 128, 160, 192, 256, 320)], p["bitrate"], "bitrate", int)
        elif f == "opus":
            combo(0, "Bitrate", [(b, f"{b} kbps") for b in (32, 48, 64, 96, 128, 160, 192, 256)], p["bitrate"], "bitrate", int)
            combo(1, "Tuned for", [("audio", "Music"), ("voip", "Speech / voice")], p["application"], "application")
        elif f == "vorbis":
            combo(0, "Quality", [(q, f"q{q}  (~{VORBIS_KBPS[q]} kbps)") for q in range(11)], p["vorbis_q"], "vorbis_q", int)
        else:
            depths = [("keep", "Keep original"), ("16", "16-bit"), ("24", "24-bit")]
            if f == "wav":
                depths.append(("32f", "32-bit float"))
            combo(0, "Bit depth", depths, p["depth"] if p["depth"] in dict(depths) else "keep", "depth")
            if f == "flac":
                combo(1, "Compression", [(n, f"{n}" + ("  (fastest)" if n == 0 else "  (smallest)" if n == 12 else
                                                       "  (default)" if n == 5 else "")) for n in range(13)],
                      p["flac_level"], "flac_level", int)
            hint(fr, "Lossless: the output sounds identical to the source.", wrap=290,
                 grid=dict(row=2, column=0, columnspan=2, sticky="w", pady=(px(4), 0)))
        self._loading = was

    # ---------------------------------------------------------- files
    def on_drop(self, paths):
        imgs = [p for p in paths if p.lower().endswith(IMAGE_EXTS)]
        if imgs and self.tree.selection():
            try:
                with open(imgs[0], "rb") as f:
                    self.stage_cover(f.read())
            except OSError as e:
                self.footer.set_status(f"Couldn't read that image: {e}", "error")
        rest = [p for p in paths if not p.lower().endswith(IMAGE_EXTS)]
        if rest:
            self.add_paths(rest)

    def add_paths(self, paths):
        if self.busy:
            return
        exts = AUDIO_EXTS + VIDEO_EXTS
        known = {os.path.normcase(j.path) for j in self.jobs.values()}
        added = 0
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            for p in paths:
                p = os.path.normpath(p)
                if os.path.isdir(p):
                    found = [(f, p) for f in expand_drop([p], exts)]
                elif p.lower().endswith(exts):
                    found = [(p, None)]
                else:
                    found = []
                for f, root in found:
                    if os.path.normcase(f) in known:
                        continue
                    known.add(os.path.normcase(f))
                    job = Job(f, root)
                    job.iid = self.tree.insert("", "end")
                    self.jobs[job.iid] = job
                    self.order.append(job.iid)
                    self.refresh_row(job.iid)
                    self.pool.submit(self.probe_job, job.iid, job)
                    added += 1
        finally:
            self.config(cursor="")
        if added:
            self.last_dir = os.path.dirname(os.path.normpath(paths[0]))
            self.empty_hint.pack_forget()
            self.footer.set_status(f"Added {added} file(s).")
            self.logbox.log(f"Added {added} file(s)")
            if self.sort_col:
                self.resort()
            else:
                self.apply_filter()
        else:
            self.footer.set_status("No new audio or video files there.", "warn")

    def probe_job(self, iid, job):
        if not self.ffprobe:
            return
        try:
            job.info = probe(self.ffprobe, job.path)
        except Exception as e:
            job.info = {"error": str(e)}
        self.probed.put(iid)  # never touch Tk from a worker thread; drain_probes() refreshes

    def drain_probes(self):
        changed, reload_panel = False, False
        while not self.probed.empty():
            iid = self.probed.get()
            job = self.jobs.get(iid)
            if not job:
                continue
            if not job.saved and job.info.get("tags") and fmt_of_path(job.path) in (None, "wav", "aiff"):
                job.saved = dict(job.info["tags"])   # WMA…, or WAV/AIFF with only an INFO list: what ffmpeg reads
                job.info_only = job.taggable()
                reload_panel = reload_panel or job in self.panel_jobs
            self.refresh_row(iid)
            changed = True
        if changed:
            self.update_estimate()
        if reload_panel:
            self.load_panel()
        self.after(200, self.drain_probes)

    def browse_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose music or videos", initialdir=self.last_dir,
            filetypes=[("Audio and video", " ".join("*" + e for e in AUDIO_EXTS + VIDEO_EXTS)), ("All files", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def browse_folder(self):
        d = filedialog.askdirectory(title="Add all music in…", initialdir=self.last_dir)
        if d:
            self.add_paths([d])

    def remove(self):
        if self.busy:
            return
        self.commit_panel(reload=False)
        sel = [i for i in self.tree.selection() if i in self.jobs]
        if not sel:
            return
        dirty = sum(1 for i in sel if self.jobs[i].dirty())
        if dirty and not messagebox.askyesno(APP_NAME, f"{dirty} of these file(s) have unsaved tag changes. "
                                                       "Remove them from the list anyway?"):
            return
        for iid in sel:
            self.tree.delete(iid)
            self.jobs.pop(iid, None)
            self.order.remove(iid)
        self.load_panel(commit=False)
        self.update_count()
        self.update_estimate()

    def clear(self):
        if self.busy:
            return
        self.commit_panel(reload=False)
        dirty = sum(1 for j in self.jobs.values() if j.dirty())
        if dirty and not messagebox.askyesno(APP_NAME, f"{dirty} file(s) have unsaved tag changes. Clear the list anyway?"):
            return
        self.tree.delete(*self.order)
        self.jobs.clear()
        self.order.clear()
        self.undo_stack.clear()
        self.load_panel(commit=False)
        self.update_count()
        self.update_estimate()

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def row_values(self, job):
        i, t = job.info, job.tags()
        if not i:
            src = "reading…" if self.ffprobe else ""
        elif "error" in i:
            src = "can't read"
        else:
            codec = i["codec"]
            lossless = codec in ("flac", "alac", "wavpack", "ape", "tta") or codec.startswith("pcm")
            detail = f" {i['bits']}-bit" if (lossless and i.get("bits")) else (f" {i['kbps']}k" if i.get("kbps") else "")
            src = f"{'PCM' if codec.startswith('pcm') else codec.upper()}{detail}"
        length = f"{int(i['duration'] // 60)}:{int(i['duration'] % 60):02d}" if i.get("duration") else ""
        status = job.status or ("Unsaved" if job.dirty() else "")
        return (os.path.basename(job.path), t.get("title", ""), t.get("artist", ""),
                t.get("album", ""), t.get("date", ""), t.get("tracknumber", ""), t.get("genre", ""), src, length, status)

    def refresh_row(self, iid):
        job = self.jobs.get(iid)
        if not job:
            return
        self.tree.item(iid, values=self.row_values(job))
        if job.status_tag:
            tags = (job.status_tag,)
        else:
            tags = ("modified",) if job.dirty() else ()
        self.tree.item(iid, tags=tags)

    def refresh_jobs(self, jobs):
        for j in jobs:
            if j.iid in self.jobs:
                self.refresh_row(j.iid)
        self.update_count()

    def update_count(self):
        n, vis = len(self.jobs), len(self.tree.get_children())
        sel = len(self.tree.selection())
        dirty = sum(1 for j in self.jobs.values() if j.dirty())
        parts = [f"{vis} of {n} files" if vis != n else f"{n} file{'s' if n != 1 else ''}"]
        if sel:
            parts.append(f"{sel} selected")
        if dirty:
            parts.append(f"{dirty} unsaved")
        self.count_lbl.configure(text="  ·  ".join(parts))

    def apply_filter(self):
        text = self.filter_var.get().strip().lower()
        visible = [iid for iid in self.order
                   if not text or text in (" ".join(str(v) for v in self.tree.item(iid, "values")) + " "
                                           + self.jobs[iid].path).lower()]
        shown = set(visible)
        hidden = [i for i in self.order if i not in shown]
        if hidden:
            self.tree.detach(*hidden)
        for idx, iid in enumerate(visible):
            self.tree.move(iid, "", idx)
        self.update_count()

    def sort_by(self, col):
        self.sort_rev = (not self.sort_rev) if self.sort_col == col else False
        self.sort_col = col
        self.resort()

    def resort(self):
        col = self.sort_col

        def key(iid):
            j = self.jobs[iid]
            if col == "tracknumber":
                return (track_no(j.get("discnumber")), track_no(j.get("tracknumber")), j.path.lower())
            if col == "len":
                return (j.info.get("duration") or 0, j.path.lower())
            return (str(self.tree.set(iid, col)).lower(), j.path.lower())

        self.order.sort(key=key, reverse=self.sort_rev)
        for cid, text, *_ in COLUMNS:
            arrow = (" ▼" if self.sort_rev else " ▲") if cid == col else ""
            self.tree.heading(cid, text=text + arrow)
        self.apply_filter()

    def targets(self):
        """The selected files in list order, or every visible file when none are selected."""
        vis = list(self.tree.get_children())
        sel = set(self.tree.selection())
        return [self.jobs[i] for i in ([i for i in vis if i in sel] or vis)]

    def need_targets(self):
        if self.busy:
            self.footer.set_status("Wait for the conversion to finish first.", "warn")
            return []
        self.commit_panel()
        jobs = self.targets()
        if not jobs:
            messagebox.showinfo(APP_NAME, "Add some music first (drop files or folders on the window).")
        return jobs

    # ---------------------------------------------------------- the tag panel
    def suggestions(self, key, current):
        seen = list(dict.fromkeys(j.get(key) for j in self.jobs.values()))
        out = [v for v in seen if v and v != current][:30]
        if key == "genre":
            out += [g for g in GENRES if g not in out and g != current]
        return out

    def load_panel(self, commit=True):
        if commit:
            self.commit_panel(reload=False)
        jobs = [self.jobs[i] for i in self.tree.selection() if i in self.jobs]
        self.panel_jobs = jobs
        self._panel_loading = True
        try:
            for key, var in self.field_vars.items():
                box = self.field_boxes[key]
                if not jobs:
                    text, values = "", []
                    box.state(["disabled"])
                elif key == "__filename__":
                    text, values = (stem(jobs[0].path), []) if len(jobs) == 1 else (KEEP, [])
                    box.state(["!disabled"] if len(jobs) == 1 else ["disabled"])
                else:
                    box.state(["!disabled"])
                    uniq = list(dict.fromkeys(j.get(key) for j in jobs))
                    text = uniq[0] if len(uniq) == 1 else KEEP
                    if len(jobs) > 1:
                        values = [KEEP, BLANK] + [v for v in uniq if v][:40]
                    else:
                        values = [BLANK] + self.suggestions(key, text)
                var.set(text)
                box.configure(values=values)
                self.panel_loaded[key] = text
            dirs = {os.path.dirname(j.path) for j in jobs}
            self.dir_var.set(dirs.pop() if len(dirs) == 1 else (KEEP if jobs else ""))
            if not jobs:
                self.panel_title.configure(text="Select files in the list to edit their tags.")
            elif len(jobs) == 1:
                self.panel_title.configure(text=os.path.basename(jobs[0].path))
            else:
                self.panel_title.configure(text=f"{len(jobs)} files selected")
            self.load_lyrics_box(jobs)
            self.show_cover(jobs)
        finally:
            self._panel_loading = False

    def panel_changes(self):
        """Edits typed into the panel that haven't been staged yet: ({job: {key: value}}, new file name)."""
        if self._panel_loading or not self.panel_jobs:
            return {}, None
        changes, rename = {}, None
        for key, var in self.field_vars.items():
            text = var.get()
            if text == self.panel_loaded.get(key) or text == KEEP:
                continue
            self.panel_loaded[key] = text
            if key == "__filename__":
                rename = safe_name(text)
                continue
            value = "" if text == BLANK else text.strip()
            for j in self.panel_jobs:
                if j.get(key) != value:
                    changes.setdefault(j, {})[key] = value
        if str(self.lyrics_box.cget("state")) == "normal":
            text = self.lyrics_box.get("1.0", "end-1c")
            if text != self.panel_loaded.get("lyrics") and text != KEEP:
                self.panel_loaded["lyrics"] = text
                value = clean_lyrics(text)
                for j in self.panel_jobs:
                    if j.get("lyrics") != value:
                        changes.setdefault(j, {})["lyrics"] = value
        return changes, rename

    def load_lyrics_box(self, jobs):
        box = self.lyrics_box
        box.configure(state="normal")
        box.delete("1.0", "end")
        text = ""
        if jobs:
            uniq = {j.get("lyrics") for j in jobs}
            text = uniq.pop() if len(uniq) == 1 else KEEP
            box.insert("1.0", text)
        box.configure(fg=MUTED if text == KEEP else "#1a1a1a", state="normal" if jobs else "disabled",
                      bg="white" if jobs else "#f3f3f3")
        box.edit_reset()
        self.panel_loaded["lyrics"] = text

    def lyrics_focus(self, e=None):
        """Clicking into '< keep >' empties the box; leaving it empty still keeps each file's lyrics."""
        if self.lyrics_box.get("1.0", "end-1c") == KEEP:
            self.lyrics_box.delete("1.0", "end")
            self.lyrics_box.configure(fg="#1a1a1a")
            self.panel_loaded["lyrics"] = ""

    def clear_lyrics(self):
        self.commit_panel(reload=False)
        jobs = [j for j in self.panel_jobs if j.get("lyrics")]
        if jobs:
            self.stage({j: {"lyrics": ""} for j in jobs}, "Clear lyrics")
        else:
            self.footer.set_status("No lyrics to clear.")

    def edit_lyrics(self):
        self.commit_panel(reload=False)
        jobs = list(self.panel_jobs)
        if not jobs:
            self.footer.set_status("Select the files first.", "warn")
            return
        uniq = {j.get("lyrics") for j in jobs}
        current = uniq.pop() if len(uniq) == 1 else ""
        name = os.path.basename(jobs[0].path) if len(jobs) == 1 else f"{len(jobs)} files"
        win, body = make_dialog(self, f"Lyrics  ·  {name}", resizable=True)
        frame = ttk.Frame(body)
        frame.pack(fill="both", expand=True)
        txt = tk.Text(frame, width=60, height=22, wrap="word", font=("Segoe UI", 10), undo=True, relief="flat",
                      highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, padx=px(8), pady=px(6))
        sb = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", current)
        txt.edit_reset()
        targets = sorted({lyrics_target(j.path) or "kept for converting (this file type can't hold lyrics)"
                          for j in jobs})
        note = "Several files have different lyrics; what you type replaces all of them.\n" if len(uniq) > 0 else ""
        ttk.Label(body, text=note + "Saved as: " + "; ".join(targets), style="Muted.TLabel", wraplength=px(560),
                  justify="left").pack(anchor="w", pady=(px(8), 0))

        def load_file():
            p = filedialog.askopenfilename(parent=win, title="Load lyrics", initialdir=os.path.dirname(jobs[0].path),
                                           filetypes=[("Lyrics", "*.txt *.lrc"), ("All files", "*.*")])
            if not p:
                return
            with open(p, "rb") as f:
                raw = f.read()
            for enc in ("utf-8-sig", "utf-16", "cp1252"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if p.lower().endswith(".lrc"):   # unsynchronized lyrics: drop the [mm:ss.xx] time stamps and [ar:] headers
                lines = [re.sub(r"\[\d+:\d+(?:[.:]\d+)?\]", "", ln) for ln in text.splitlines()
                         if not re.fullmatch(r"\s*\[[a-z]+:.*\]\s*", ln, re.I)]
                text = "\n".join(lines)
            txt.delete("1.0", "end")
            txt.insert("1.0", text.strip())

        def ok():
            try:
                value = clean_lyrics(txt.get("1.0", "end-1c"))
            except LyricsError as e:
                messagebox.showerror(APP_NAME, str(e), parent=win)
                return
            win.destroy()
            changes = {j: {"lyrics": value} for j in jobs if j.get("lyrics") != value}
            if changes:
                self.stage(changes, "Lyrics")

        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(px(12), 0))
        ttk.Button(bar, text="Load From File…", command=load_file).pack(side="left")
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="OK", style="Go.TButton", command=ok).pack(side="right", padx=(0, px(8)))
        show_dialog(self, win)
        txt.focus_set()

    def commit_panel(self, reload=True):
        changes, rename = self.panel_changes()
        if changes:
            label = ", ".join(sorted({FIELD_LABEL.get(k, k) for c in changes.values() for k in c}))
            self.stage(changes, f"Edit {label}", reload=False)
        if rename and len(self.panel_jobs) == 1:
            j = self.panel_jobs[0]
            new = os.path.join(os.path.dirname(j.path), rename + os.path.splitext(j.path)[1])
            if new != j.path:
                self.rename_jobs([(j, new)], "Rename")
        if (changes or rename) and reload:
            self.load_panel(commit=False)

    def show_cover(self, jobs):
        data, text, info = None, "No cover", ""
        if jobs:
            j = jobs[0]
            if j.cover == "remove":
                text = "Cover removed\n(not saved yet)"
            else:
                data = cover_for(j)
            if data:
                info = cover_text(data) + ("\nnot saved yet" if isinstance(j.cover, bytes) else "")
            if len(jobs) > 1:
                info = (info + "\n" if info else "") + "Showing the first selected file."
        self.cover_info.configure(text=info)
        if data:
            try:
                im = Image.open(io.BytesIO(data)).convert("RGB")
                im.thumbnail((px(108), px(108)), Image.Resampling.LANCZOS)
                self.cover_photo = ImageTk.PhotoImage(im)
                self.cover_lbl.configure(image=self.cover_photo, text="")
                return
            except Exception:
                pass
        self.cover_photo = None
        self.cover_lbl.configure(image="", text=text)

    def stage_cover(self, value):
        """value: image bytes or "remove", for the selected files."""
        self.commit_panel(reload=False)
        jobs = [self.jobs[i] for i in self.tree.selection() if i in self.jobs]
        if not jobs:
            self.footer.set_status("Select the files first.", "warn")
            return
        self.stage({}, "Remove cover" if value == "remove" else "Cover", {j: value for j in jobs})

    def pick_cover(self):
        if not self.tree.selection():
            self.footer.set_status("Select the files first.", "warn")
            return
        p = filedialog.askopenfilename(title="Choose cover art", initialdir=self.last_dir,
                                       filetypes=[("Images", "*.jpg *.jpeg *.png")])
        if p:
            with open(p, "rb") as f:
                self.stage_cover(f.read())

    def extract_cover_file(self):
        jobs = [self.jobs[i] for i in self.tree.selection() if i in self.jobs]
        data = cover_for(jobs[0]) if jobs else None
        if not data:
            self.footer.set_status("There's no cover to extract.", "warn")
            return
        ext = ".png" if image_mime(data) == "image/png" else ".jpg"
        p = filedialog.asksaveasfilename(title="Save the cover as", initialdir=os.path.dirname(jobs[0].path),
                                         initialfile="cover" + ext, defaultextension=ext,
                                         filetypes=[("Image", "*" + ext)])
        if p:
            with open(p, "wb") as f:
                f.write(data)
            self.footer.set_status(f"Saved the cover as {os.path.basename(p)}", "ok")

    # ---------------------------------------------------------- staging, saving, undo
    def stage(self, changes, label, covers=None, reload=True):
        """Record unsaved edits. changes: {job: {key: value}} ("" clears); covers: {job: bytes | "remove"}."""
        covers = covers or {}
        jobs = list(dict.fromkeys(list(changes) + list(covers)))
        if not jobs:
            self.footer.set_status("Nothing to change.", "warn")
            return 0
        self.undo_stack.append(("staged", [(j, dict(j.pending), j.cover) for j in jobs], label))
        del self.undo_stack[:-200]
        for j, ch in changes.items():
            for k, v in ch.items():
                k = k.lower()
                if v == j.saved.get(k, ""):
                    j.pending.pop(k, None)
                else:
                    j.pending[k] = v
        for j, c in covers.items():
            j.cover = c
        self.refresh_jobs(jobs)
        if reload:
            self.load_panel(commit=False)
        self.footer.set_status(f"{label}: {len(jobs)} file(s) changed. Press Save (Ctrl+S) to write the tags.", "ok")
        return len(jobs)

    def save_tags(self):
        if self.busy:
            return
        self.commit_panel(reload=False)
        dirty = [self.jobs[i] for i in self.order if self.jobs[i].dirty()]
        if not dirty:
            self.footer.set_status("No unsaved tag changes.")
            return
        entries, problems, skipped = [], [], 0
        for j in dirty:
            fmt = fmt_of_path(j.path)
            if not fmt:
                skipped += 1
                continue
            try:
                changes = dict(j.pending)
                if j.info_only:   # tags ffmpeg read from a WAV/AIFF INFO list: copy them into the ID3 chunk too
                    changes = dict({k: v for k, v in j.saved.items() if key_ok(j.path, k)}, **changes)
                old = {k: "" if j.info_only else j.saved.get(k, "") for k in changes}
                old_cover = None
                if j.cover is not None:
                    c = extract_cover(j.path)
                    old_cover = c[0] if c else None

                def apply(path, changes=changes, cover=j.cover, fmt=fmt):
                    write_tags(path, changes)
                    if cover is not None:
                        write_cover(path, fmt, None if cover == "remove" else cover)

                safe_edit(j.path, apply)
                entries.append((j, old, j.cover is not None, old_cover))
                j.saved, j.pending, j.cover, j.info_only = read_tags(j.path), {}, None, False
            except Exception as e:
                problems.append(f"{os.path.basename(j.path)}: {e}")
        if entries:
            self.undo_stack.append(("saved", entries, "Save"))
            self.logbox.log(f"Saved tags in {len(entries)} file(s)", "ok")
        for p in problems:
            self.logbox.log(p, "error")
        if skipped:
            self.logbox.log(f"{skipped} file(s) are WMA or another type whose tags can't be edited in place; "
                            "their changes are kept and used when you convert.", "warn")
        self.refresh_jobs(dirty)
        self.load_panel(commit=False)
        msg = f"Saved tags in {len(entries)} file(s)"
        if problems:
            self.footer.set_status(msg + f", {len(problems)} failed (see Activity).", "error")
        elif skipped:
            self.footer.set_status(msg + f"; {skipped} can only take tags when converted (see Activity).", "warn")
        else:
            self.footer.set_status(msg + ".", "ok")

    def rename_jobs(self, pairs, label):
        done, problems = [], []
        for j, new in pairs:
            old = j.path
            if new == old:
                continue
            same = os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(old))
            if not same and os.path.exists(new):
                new = self.unique(new, set(), old)
            try:
                os.makedirs(os.path.dirname(new), exist_ok=True)
                os.rename(old, new)
                j.path = new
                done.append((j, old, new))
            except OSError as e:
                problems.append(f"{os.path.basename(old)}: {e.strerror or e}")
        if done:
            self.undo_stack.append(("rename", done, label))
            self.logbox.log(f"{label}: renamed {len(done)} file(s)", "ok")
        for p in problems:
            self.logbox.log(p, "error")
        self.refresh_jobs([j for j, _ in pairs])
        if problems:
            self.footer.set_status(f"Renamed {len(done)} file(s), {len(problems)} failed (see Activity).", "error")
        else:
            self.footer.set_status(f"Renamed {len(done)} file(s).", "ok")
        return len(done), problems

    def undo(self):
        if self.busy:
            return
        self.commit_panel(reload=False)
        if not self.undo_stack:
            self.footer.set_status("Nothing to undo.")
            return
        kind, entries, label = self.undo_stack.pop()
        jobs, problems = [], []
        if kind == "staged":
            for j, pending, cover in entries:
                j.pending, j.cover = pending, cover
                jobs.append(j)
        elif kind == "saved":
            for j, old, cover_changed, old_cover in entries:
                try:
                    def revert(path, old=old, cover_changed=cover_changed, old_cover=old_cover):
                        write_tags(path, old)
                        if cover_changed:
                            write_cover(path, fmt_of_path(path), old_cover)

                    safe_edit(j.path, revert)
                    j.saved = read_tags(j.path)
                except Exception as e:
                    problems.append(f"{os.path.basename(j.path)}: {e}")
                jobs.append(j)
        elif kind == "rename":
            for j, old, new in reversed(entries):
                try:
                    if os.path.exists(old) and os.path.normcase(old) != os.path.normcase(new):
                        raise OSError(f"{os.path.basename(old)} exists again")
                    os.rename(new, old)
                    j.path = old
                    try:
                        os.removedirs(os.path.dirname(new))   # folders the rename created, if now empty
                    except OSError:
                        pass
                except OSError as e:
                    problems.append(f"{os.path.basename(new)}: {e}")
                jobs.append(j)
        for p in problems:
            self.logbox.log(p, "error")
        self.refresh_jobs(jobs)
        self.load_panel(commit=False)
        self.footer.set_status(f"Undid: {label}" + (" (some files failed, see Activity)" if problems else ""),
                               "error" if problems else "ok")

    def reload_tags(self):
        if self.busy:
            return
        self.commit_panel(reload=False)
        dirty = sum(1 for j in self.jobs.values() if j.dirty())
        if dirty and not messagebox.askyesno(APP_NAME, f"Discard the unsaved changes in {dirty} file(s) and read the "
                                                       "tags again from the files?"):
            return
        for j in self.jobs.values():
            j.saved = read_tags(j.path) if j.taggable() else dict(j.info.get("tags") or {})
            j.pending, j.cover = {}, None
        self.undo_stack.clear()
        self.refresh_jobs(list(self.jobs.values()))
        self.load_panel(commit=False)
        self.footer.set_status("Tags reloaded from the files.")

    # ---------------------------------------------------------- converters (Mp3tag style)
    def remember(self, kind, pattern):
        lst = [pattern] + [p for p in self.recent[kind] if p != pattern]
        self.recent[kind] = lst[:12]

    def format_dialog(self, title, kind, label, preview, apply, extra=None):
        var = tk.StringVar(value=self.recent[kind][0])

        def build(top, refresh):
            ttk.Label(top, text=label).grid(row=0, column=0, sticky="w")
            cb = ttk.Combobox(top, textvariable=var, values=self.recent[kind], width=56)
            cb.grid(row=0, column=1, sticky="ew", padx=(px(8), 0))
            var.trace_add("write", lambda *_: refresh())
            if extra:
                extra(top, refresh)
            hint(top, "Placeholders: " + FORMAT_HELP + ("   ·   %dummy% skips a part" if kind == "f2t" else "")
                 + ("   ·   \\ makes folders" if kind != "t2t" else ""),
                 wrap=680, grid=dict(row=9, column=0, columnspan=2, sticky="w", pady=(px(8), 0)))
            cb.focus_set()
            cb.icursor("end")

        def do_apply():
            pat = var.get().strip()
            if not pat:
                return False
            self.remember(kind, pat)
            return apply(pat)

        PreviewDialog(self, title, build, lambda: preview(var.get()), do_apply)

    def tag_to_filename(self):
        jobs = self.need_targets()
        if not jobs:
            return

        def plan(pat):
            out = []
            for j in jobs:
                vals = token_values(j)
                parts = [safe_name(fill(part, j, vals)) for part in re.split(r"[\\/]", pat.strip())]
                parts = [x for x in parts if x]
                new = os.path.join(os.path.dirname(j.path), *parts) + os.path.splitext(j.path)[1] if parts else j.path
                out.append((j, new))
            return out

        def preview(pat):
            p = plan(pat)
            changed = sum(1 for j, n in p if n != j.path)
            rows = [(os.path.basename(j.path), os.path.relpath(n, os.path.dirname(j.path))) for j, n in p]
            return rows, f"{changed} of {len(p)} file(s) will be renamed."

        def apply(pat):
            pairs = [(j, n) for j, n in plan(pat) if n != j.path]
            if pairs:
                self.rename_jobs(pairs, "Tag → Filename")
                self.load_panel(commit=False)

        self.format_dialog("Tag → Filename", "t2f", "Format string", preview, apply)

    @staticmethod
    def parse_job(pat, job):
        name = stem(job.path)
        depth = len(re.findall(r"[\\/]", pat))
        if depth:
            comps = os.path.normpath(os.path.splitext(job.path)[0]).split(os.sep)
            name = "\\".join(comps[-(depth + 1):])
            pat = re.sub(r"[\\/]", lambda m: "\\", pat)
        return parse_filename(pat, name)

    def filename_to_tag(self):
        jobs = self.need_targets()
        if not jobs:
            return

        def preview(pat):
            rows, hits = [], 0
            for j in jobs:
                res = self.parse_job(pat, j)
                hits += bool(res)
                after = "   ".join(f"{FIELD_LABEL.get(k, k)}: {v}" for k, v in res.items()) if res else "(doesn't match)"
                rows.append((os.path.basename(j.path), after))
            return rows, f"{hits} of {len(jobs)} file name(s) match the pattern."

        def apply(pat):
            changes = {}
            for j in jobs:
                res = self.parse_job(pat, j)
                if res:
                    ch = {k: v for k, v in res.items() if j.get(k) != v}
                    if ch:
                        changes[j] = ch
            self.stage(changes, "Filename → Tag")

        self.format_dialog("Filename → Tag", "f2t", "Format string", preview, apply)

    @staticmethod
    def field_key(text):
        text = text.strip()
        for k, label in FIELDS:
            if text.lower() in (label.lower(), k):
                return k
        return text.lower()

    def tag_to_tag(self):
        jobs = self.need_targets()
        if not jobs:
            return
        field = tk.StringVar(value="Title")

        def extra(top, refresh):
            ttk.Label(top, text="Field").grid(row=1, column=0, sticky="w", pady=(px(8), 0))
            ttk.Combobox(top, textvariable=field, values=[label for _, label in FIELDS], width=20).grid(
                row=1, column=1, sticky="w", padx=(px(8), 0), pady=(px(8), 0))
            field.trace_add("write", lambda *_: refresh())

        def preview(pat):
            k = self.field_key(field.get())
            rows = [(j.get(k), fill(pat, j)) for j in jobs]
            return rows, f"Sets {FIELD_LABEL.get(k, k)} in {sum(1 for a, b in rows if a != b)} file(s)."

        def apply(pat):
            k = self.field_key(field.get())
            if not k:
                return False
            self.stage({j: {k: fill(pat, j)} for j in jobs if j.get(k) != fill(pat, j)}, "Tag → Tag")

        self.format_dialog("Tag → Tag", "t2t", "Format string", preview, apply, extra)

    # ---------------------------------------------------------- actions
    FIELD_CHOICES = ["All text fields"] + [label for _, label in FIELDS]

    def keys_for(self, choice):
        return list(TEXT_FIELDS) if choice == self.FIELD_CHOICES[0] else [self.field_key(choice)]

    def field_row(self, top, var, refresh, row=0):
        ttk.Label(top, text="Field").grid(row=row, column=0, sticky="w", pady=(px(6) if row else 0, 0))
        cb = ttk.Combobox(top, textvariable=var, values=self.FIELD_CHOICES, width=24)
        cb.grid(row=row, column=1, sticky="w", padx=(px(8), 0), pady=(px(6) if row else 0, 0))
        var.trace_add("write", lambda *_: refresh())

    @staticmethod
    def change_rows(changes):
        return [(j.get(k), v) for j, ch in changes.items() for k, v in ch.items()]

    def action_case(self):
        jobs = self.need_targets()
        if not jobs:
            return
        field, mode = tk.StringVar(value=self.FIELD_CHOICES[0]), tk.StringVar(value="title")

        def build(top, refresh):
            self.field_row(top, field, refresh)
            ttk.Label(top, text="Case").grid(row=1, column=0, sticky="w", pady=(px(8), 0))
            segmented(top, [("title", "Title Case"), ("sentence", "Sentence case"), ("upper", "UPPERCASE"),
                            ("lower", "lowercase")], mode, refresh).grid(row=1, column=1, sticky="w",
                                                                       padx=(px(8), 0), pady=(px(8), 0))

        def changes():
            out = {}
            for j in jobs:
                for k in self.keys_for(field.get()):
                    v = j.get(k)
                    nv = change_case(v, mode.get())
                    if v and nv != v:
                        out.setdefault(j, {})[k] = nv
            return out

        def preview():
            ch = changes()
            return self.change_rows(ch), f"{len(ch)} file(s) will change."

        PreviewDialog(self, "Change Case", build, preview, lambda: self.stage(changes(), "Change case"))

    def action_replace(self):
        jobs = self.need_targets()
        if not jobs:
            return
        field, find, repl = tk.StringVar(value=self.FIELD_CHOICES[0]), tk.StringVar(), tk.StringVar()
        regex, case = tk.BooleanVar(value=False), tk.BooleanVar(value=False)

        def build(top, refresh):
            self.field_row(top, field, refresh)
            for r, (label, var) in enumerate((("Find", find), ("Replace with", repl)), 1):
                ttk.Label(top, text=label).grid(row=r, column=0, sticky="w", pady=(px(8), 0))
                e = ttk.Entry(top, textvariable=var, width=40)
                e.grid(row=r, column=1, sticky="ew", padx=(px(8), 0), pady=(px(8), 0))
                var.trace_add("write", lambda *_: refresh())
                if r == 1:
                    e.focus_set()
            opts = ttk.Frame(top)
            opts.grid(row=3, column=1, sticky="w", padx=(px(8), 0), pady=(px(8), 0))
            ttk.Checkbutton(opts, text="Match case", variable=case, command=refresh).pack(side="left")
            ttk.Checkbutton(opts, text="Regular expression ($1 → \\1)", variable=regex, command=refresh).pack(
                side="left", padx=(px(14), 0))

        def changes():
            if not find.get():
                return {}
            flags = 0 if case.get() else re.I
            rx = re.compile(find.get() if regex.get() else re.escape(find.get()), flags)
            sub = re.sub(r"\$(\d)", r"\\\1", repl.get()) if regex.get() else (lambda m: repl.get())
            out = {}
            for j in jobs:
                for k in self.keys_for(field.get()):
                    v = j.get(k)
                    nv = rx.sub(sub, v) if v else v
                    if nv != v:
                        out.setdefault(j, {})[k] = nv
            return out

        def preview():
            ch = changes()
            return self.change_rows(ch), f"{len(ch)} file(s) will change."

        PreviewDialog(self, "Replace", build, preview, lambda: self.stage(changes(), "Replace"))

    def action_remove(self):
        jobs = self.need_targets()
        if not jobs:
            return
        every = "Every field"
        field = tk.StringVar(value=every)
        cover = tk.BooleanVar(value=False)

        def build(top, refresh):
            ttk.Label(top, text="Remove").grid(row=0, column=0, sticky="w")
            cb = ttk.Combobox(top, textvariable=field, values=[every] + [label for _, label in FIELDS], width=24)
            cb.grid(row=0, column=1, sticky="w", padx=(px(8), 0))
            field.trace_add("write", lambda *_: refresh())
            ttk.Checkbutton(top, text="Also remove the cover art", variable=cover, command=refresh).grid(
                row=1, column=1, sticky="w", padx=(px(8), 0), pady=(px(8), 0))

        def changes():
            out = {}
            for j in jobs:
                keys = list(j.tags()) if field.get() == every else [self.field_key(field.get())]
                ch = {k: "" for k in keys if j.get(k)}
                if ch:
                    out[j] = ch
            return out

        def preview():
            ch = changes()
            rows = [(os.path.basename(j.path), "removes " + ", ".join(FIELD_LABEL.get(k, k) for k in c)) for j, c in ch.items()]
            if cover.get():
                rows += [(os.path.basename(j.path), "removes the cover") for j in jobs]
            return rows, f"{len(ch)} file(s) have tags to remove."

        def apply():
            return self.stage(changes(), "Remove fields", {j: "remove" for j in jobs} if cover.get() else None)

        PreviewDialog(self, "Remove Fields", build, preview, apply, ok_text="Remove", heads=("File", "Change"))

    def action_trim(self):
        jobs = self.need_targets()
        if not jobs:
            return
        changes = {}
        for j in jobs:
            for k, v in j.tags().items():
                nv = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in v.strip().splitlines())
                if nv != v:
                    changes.setdefault(j, {})[k] = nv
        if changes:
            self.stage(changes, "Trim spaces")
        else:
            self.footer.set_status("No extra spaces found.")

    def auto_number(self):
        jobs = self.need_targets()
        if not jobs:
            return
        start = tk.StringVar(value="1")
        total, per_folder, disc = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=False)

        def build(top, refresh):
            ttk.Label(top, text="Start at").grid(row=0, column=0, sticky="w")
            sp = ttk.Spinbox(top, from_=0, to=999, textvariable=start, width=6, command=refresh)
            sp.grid(row=0, column=1, sticky="w", padx=(px(8), 0))
            start.trace_add("write", lambda *_: refresh())
            for r, (text, var) in enumerate((("Add the total (1/12)", total),
                                             ("Start again in every folder", per_folder),
                                             ("Set disc numbers by folder (one folder per disc)", disc)), 1):
                ttk.Checkbutton(top, text=text, variable=var, command=refresh).grid(
                    row=r, column=1, sticky="w", padx=(px(8), 0), pady=(px(6), 0))
            hint(top, "Files are numbered in the order they're listed; sort the list first if needed.", wrap=600,
                 grid=dict(row=5, column=0, columnspan=2, sticky="w", pady=(px(8), 0)))

        def changes():
            try:
                first = int(start.get())
            except ValueError:
                first = 1
            folders = list(dict.fromkeys(os.path.dirname(j.path) for j in jobs))
            groups = {}
            for j in jobs:
                groups.setdefault(os.path.dirname(j.path) if per_folder.get() else "", []).append(j)
            out = {}
            for members in groups.values():
                last = first + len(members) - 1
                for i, j in enumerate(members):
                    n = first + i
                    ch = {"tracknumber": f"{n}/{last}" if total.get() else str(n)}
                    if disc.get():
                        ch["discnumber"] = f"{folders.index(os.path.dirname(j.path)) + 1}/{len(folders)}"
                    ch = {k: v for k, v in ch.items() if j.get(k) != v}
                    if ch:
                        out[j] = ch
            return out

        def preview():
            ch = changes()
            rows = [(os.path.basename(j.path), "   ".join(f"{FIELD_LABEL[k]} {v}" for k, v in c.items())) for j, c in ch.items()]
            return rows, f"{len(ch)} file(s) will be numbered."

        PreviewDialog(self, "Auto-Number Tracks", build, preview, lambda: self.stage(changes(), "Auto-number"),
                      ok_text="Number", heads=("File", "New number"))

    # ---------------------------------------------------------- extended tags
    def extended_tags(self):
        if self.busy:
            return
        self.commit_panel()
        sel = [self.jobs[i] for i in self.tree.selection() if i in self.jobs]
        if len(sel) != 1:
            messagebox.showinfo(APP_NAME, "Select one file to see and edit all of its tag fields.")
            return
        job = sel[0]
        work = dict(job.tags())
        win, body = make_dialog(self, f"Extended Tags  ·  {os.path.basename(job.path)}", resizable=True)
        tf = make_tree(body, [("k", "Field", 170, False), ("v", "Value", 420, True)], height=14, selectmode="browse")
        tf.pack(fill="both", expand=True)
        tree = tf.tree
        edit = ttk.Frame(body)
        edit.pack(fill="x", pady=(px(10), 0))
        edit.columnconfigure(3, weight=1)
        key_var, val_var = tk.StringVar(), tk.StringVar()
        ttk.Label(edit, text="Field").grid(row=0, column=0, sticky="w")
        keys = valid_keys(job.path)
        ttk.Combobox(edit, textvariable=key_var, width=20,
                     values=[k for k in (keys or sorted({k for k, _ in FIELDS} | set(work))) if "*" not in k]).grid(
            row=0, column=1, sticky="w", padx=(px(6), px(12)))
        ttk.Label(edit, text="Value").grid(row=0, column=2, sticky="w")
        ttk.Entry(edit, textvariable=val_var).grid(row=0, column=3, sticky="ew", padx=(px(6), 0))
        note = ttk.Label(body, text="" if job.taggable() else
                         "This file type can't hold these tags itself; they're used when you convert it.",
                         style="Muted.TLabel")
        note.pack(anchor="w", pady=(px(6), 0))

        def fill_tree():
            tree.delete(*tree.get_children())
            for k in sorted(work):
                tree.insert("", "end", iid=k, values=(k.upper(), work[k].replace("\n", " ⏎ ")))

        def picked(e=None):
            s = tree.selection()
            if s:
                key_var.set(s[0])
                val_var.set(work.get(s[0], ""))

        def set_field():
            k = key_var.get().strip().lower()
            if not k:
                return
            if job.taggable() and not key_ok(job.path, k):
                messagebox.showinfo(APP_NAME, f"This file type can't store a field named \"{k}\". Pick one from "
                                              "the list.", parent=win)
                return
            if val_var.get():
                work[k] = val_var.get()
            else:
                work.pop(k, None)
            fill_tree()

        def remove_field():
            for k in tree.selection():
                work.pop(k, None)
            fill_tree()

        def ok():
            current = job.tags()
            ch = {k: v for k, v in work.items() if current.get(k, "") != v}
            ch.update({k: "" for k in current if k not in work})
            win.destroy()
            if ch:
                self.stage({job: ch}, "Extended tags")

        b = ttk.Frame(edit)
        b.grid(row=1, column=0, columnspan=4, sticky="w", pady=(px(8), 0))
        ttk.Button(b, text="Add / Set", command=set_field).pack(side="left")
        ttk.Button(b, text="Remove Field", command=remove_field).pack(side="left", padx=(px(6), 0))
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(px(12), 0))
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="OK", style="Go.TButton", command=ok).pack(side="right", padx=(0, px(8)))
        tree.bind("<<TreeviewSelect>>", picked)
        tree.bind("<Delete>", lambda e: remove_field())
        fill_tree()
        show_dialog(self, win)

    # ---------------------------------------------------------- tag sources
    def match_tags(self):
        jobs = self.need_targets()
        if not jobs:
            return
        d = filedialog.askdirectory(title="Folder with tracks whose tags and artwork you want copied", initialdir=self.last_dir)
        if d:
            self.match_tags_from(d, jobs)

    def match_tags_from(self, folder, jobs):
        mine = {os.path.normcase(j.path) for j in jobs}
        tag_files = [f for f in expand_drop([folder], AUDIO_EXTS) if os.path.normcase(f) not in mine]
        pairs = match_pairs({j: (j.path, j.tags()) for j in jobs}, tag_files)
        changes, covers = {}, {}
        for j, src in pairs.items():
            ch = {k: v for k, v in read_tags(src).items() if j.get(k) != v and (not j.taggable() or key_ok(j.path, k))}
            if ch:
                changes[j] = ch
            c = extract_cover(src)
            if c:
                covers[j] = c[0]
        if changes or covers:
            self.stage(changes, "Copy tags from folder", covers)
        self.footer.set_status(f"Matched {len(pairs)} of {len(jobs)} file(s) with tracks in that folder"
                               + (". Press Save to write the tags." if changes or covers else "."),
                               "ok" if pairs else "warn")
        self.logbox.log(f"Tags copied from {folder}: {len(pairs)} file(s) matched")

    def musicbrainz(self):
        jobs = self.need_targets()
        if not jobs:
            return
        first = jobs[0].tags()
        if all(track_no(j.get("tracknumber")) for j in jobs):
            jobs = sorted(jobs, key=lambda j: (track_no(j.get("discnumber")) or 1, track_no(j.get("tracknumber"))))
        win, body = make_dialog(self, "MusicBrainz Album Lookup", resizable=True)
        artist = tk.StringVar(value=first.get("albumartist") or first.get("artist", ""))
        album = tk.StringVar(value=first.get("album", ""))
        top = ttk.Frame(body)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)
        ttk.Label(top, text="Artist").grid(row=0, column=0, sticky="w")
        e1 = ttk.Entry(top, textvariable=artist)
        e1.grid(row=0, column=1, sticky="ew", padx=(px(6), px(12)))
        ttk.Label(top, text="Album").grid(row=0, column=2, sticky="w")
        e2 = ttk.Entry(top, textvariable=album)
        e2.grid(row=0, column=3, sticky="ew", padx=(px(6), px(12)))
        search_btn = ttk.Button(top, text="Search", style="Go.TButton")
        search_btn.grid(row=0, column=4)
        rf = make_tree(body, [("artist", "Artist", 170, True), ("album", "Album", 220, True), ("year", "Date", 80, False),
                              ("tracks", "Tracks", 55, False), ("country", "Country", 60, False),
                              ("fmt", "Format", 110, False)], height=7, selectmode="browse")
        rf.pack(fill="both", expand=True, pady=(px(10), 0))
        results = rf.tree
        mf = make_tree(body, [("file", "File", 230, True), ("before", "Current title", 170, True),
                              ("after", "New title", 200, True), ("track", "Track", 55, False)], height=8, selectmode="none")
        mf.pack(fill="both", expand=True, pady=(px(10), 0))
        mapping = mf.tree
        cover_var = tk.BooleanVar(value=self.cfg.get("mb_cover", True))
        ttk.Checkbutton(body, text="Also use the front cover from the Cover Art Archive", variable=cover_var).pack(
            anchor="w", pady=(px(8), 0))
        note = ttk.Label(body, text=f"{len(jobs)} file(s) will be filled in, in the order shown. Search, then pick "
                                    "the matching release.", style="Muted.TLabel", wraplength=px(760), justify="left")
        note.pack(anchor="w", pady=(px(6), 0))
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(px(12), 0))
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right")
        apply_btn = ttk.Button(bar, text="Apply", style="Go.TButton")
        apply_btn.pack(side="right", padx=(0, px(8)))
        apply_btn.state(["disabled"])
        state = {"found": [], "release": None, "cover": None, "rid": None}
        q = queue.Queue()

        def run(fn, done, token=None):
            def work():
                try:
                    q.put((done, fn(), None, token))
                except Exception as ex:
                    q.put((done, None, ex, token))
            threading.Thread(target=work, daemon=True).start()

        def pump():
            if not win.winfo_exists():
                return
            while not q.empty():
                done, res, err, token = q.get()
                done(res, err, token)
            win.after(100, pump)

        def search(*_):
            note.configure(text="Searching MusicBrainz…")
            results.delete(*results.get_children())
            mapping.delete(*mapping.get_children())
            apply_btn.state(["disabled"])
            a, b = artist.get(), album.get()
            run(lambda: mb_search(a, b), shown)

        def shown(res, err, token):
            if err:
                note.configure(text=f"Couldn't reach MusicBrainz: {err}")
                return
            state["found"] = res
            for i, r in enumerate(res):
                results.insert("", "end", iid=str(i), values=(r["artist"], r["title"], r["date"], r["tracks"],
                                                              r["country"], r["format"]))
            note.configure(text=f"{len(res)} release(s) found. Pick the one that matches your files."
                           if res else "Nothing found. Try fewer words, or check the spelling.")

        def picked(e=None):
            s = results.selection()
            if not s:
                return
            rid = state["found"][int(s[0])]["id"]
            state["rid"] = rid
            apply_btn.state(["disabled"])
            note.configure(text="Loading the track list…")
            want_cover = cover_var.get()
            run(lambda: (mb_release(rid), caa_cover(rid) if want_cover else None), loaded, rid)

        def loaded(res, err, token):
            if token != state["rid"]:
                return   # the user picked another release meanwhile
            if err:
                note.configure(text=f"Couldn't load that release: {err}")
                return
            rel, cover = res
            state["release"], state["cover"] = rel, cover
            mapping.delete(*mapping.get_children())
            for j, t in zip(jobs, rel["tracks"]):
                mapping.insert("", "end", values=(os.path.basename(j.path), j.get("title"), t["title"], t["tracknumber"]))
            for j in jobs[len(rel["tracks"]):]:
                mapping.insert("", "end", values=(os.path.basename(j.path), j.get("title"), "(no track left)", ""))
            n, m = len(rel["tracks"]), len(jobs)
            msg = f"\"{rel['album']}\" has {n} track(s); you selected {m} file(s)."
            if n != m:
                msg += " The counts differ, so check the pairing below."
            msg += " Cover art found." if cover else (" No cover art for this release." if cover_var.get() else "")
            note.configure(text=msg)
            apply_btn.state(["!disabled"])

        def apply():
            rel = state["release"]
            if not rel:
                return
            changes = {}
            for j, t in zip(jobs, rel["tracks"]):
                ch = {k: v for k, v in t.items() if v and j.get(k) != v}
                if ch:
                    changes[j] = ch
            covers = {j: state["cover"] for j in jobs[:len(rel["tracks"])]} if (state["cover"] and cover_var.get()) else None
            self.cfg["mb_cover"] = cover_var.get()
            win.destroy()
            self.stage(changes, "MusicBrainz", covers)
            self.logbox.log(f"MusicBrainz: \"{rel['album']}\" by {rel['artist']} applied to {len(changes or covers or {})} file(s)")

        search_btn.configure(command=search)
        apply_btn.configure(command=apply)
        results.bind("<<TreeviewSelect>>", picked)
        for e in (e1, e2):
            e.bind("<Return>", search)
        show_dialog(self, win)
        win.geometry(f"{px(760)}x{px(580)}")
        pump()
        if artist.get() or album.get():
            search()

    def export_csv(self):
        self.commit_panel(reload=False)
        jobs = self.targets()
        if not jobs:
            self.footer.set_status("There's nothing to export.", "warn")
            return
        path = filedialog.asksaveasfilename(title="Export tags", defaultextension=".csv", initialfile="tags.csv",
                                            initialdir=self.last_dir, filetypes=[("CSV (Excel)", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Path", "Filename"] + [label for _, label in FIELDS] + ["Length (s)", "Format"])
            for j in jobs:
                t = j.tags()
                w.writerow([j.path, os.path.basename(j.path)] + [t.get(k, "") for k, _ in FIELDS]
                           + [round(j.info.get("duration") or 0, 1), self.row_values(j)[7]])
        self.footer.set_status(f"Exported {len(jobs)} file(s) to {os.path.basename(path)}", "ok")
        self.logbox.log(f"Exported tags of {len(jobs)} file(s) to {path}")

    # ---------------------------------------------------------- output paths
    def update_out_controls(self):
        folder = self.save_mode.get() == "folder"
        self.out_entry.state(["!disabled"] if folder else ["disabled"])
        self.out_btn.state(["!disabled"] if folder else ["disabled"])
        self.struct_chk.state(["disabled"] if self.save_mode.get() == "beside" else ["!disabled"])
        self.update_path_label()

    def base_dir(self, job):
        mode = self.save_mode.get()
        if mode == "beside":
            return os.path.dirname(job.path)
        base = DEFAULT_OUT if mode == "music" else (self.out_dir.get().strip() or DEFAULT_OUT)
        if self.keep_structure.get() and job.root:
            rel = os.path.relpath(os.path.dirname(job.path), os.path.dirname(job.root))
            base = os.path.join(base, rel)
        return base

    def out_name(self, job):
        vals = token_values(job)
        for k, default in (("title", vals["name"]), ("artist", "Unknown Artist"), ("album", "Unknown Album")):
            vals[k] = vals.get(k) or default
        vals["albumartist"] = vals.get("albumartist") or vals["artist"]
        name = safe_name(fill(self.pattern.get().strip() or "{name}", job, vals)) or vals["name"]
        return name + FORMATS[self.p["fmt"]][1]

    def update_path_label(self):
        mode = self.save_mode.get()
        if mode == "music":
            where = DEFAULT_OUT
        elif mode == "folder":
            where = self.out_dir.get().strip() or "(choose a folder)"
        else:
            where = "next to each original"
        self.path_lbl.configure(text=f"Saves to  {where}")

    def update_estimate(self):
        if not hasattr(self, "tree"):
            return
        jobs = self.targets()
        total = sum(estimate_bytes(j, self.p) for j in jobs)
        if not jobs:
            self.est_lbl.configure(text="")
        else:
            which = "selected" if self.tree.selection() else "listed"
            self.est_lbl.configure(text=f"Converts the {len(jobs)} {which} file(s)"
                                        + (f", about {human_size(total)} in all." if total else "."))

    def browse_out(self):
        d = filedialog.askdirectory(title="Save converted files in…", initialdir=self.out_dir.get() or DEFAULT_OUT)
        if d:
            self.out_dir.set(os.path.normpath(d))

    def check_ffmpeg(self):
        if self.ffmpeg and self.ffprobe:
            self.ff_lbl.configure(text="FFmpeg is ready.", style="Muted.TLabel")
        else:
            self.ff_lbl.configure(text="FFmpeg not found: put ffmpeg.exe and ffprobe.exe next to this program.",
                                  style="Error.TLabel")

    def open_row(self, event):
        job = self.jobs.get(self.tree.identify_row(event.y))
        if job:
            target = job.output if job.output and os.path.exists(job.output) else job.path
            if os.path.exists(target):
                open_path(target)

    def open_out(self):
        if self.last_output and os.path.exists(self.last_output):
            reveal(self.last_output)
        else:
            target = DEFAULT_OUT if self.save_mode.get() == "music" else self.out_dir.get().strip()
            if target:
                os.makedirs(target, exist_ok=True)
                open_path(target)

    # ---------------------------------------------------------- convert
    def start(self):
        if self.busy:
            return
        self.commit_panel()
        if not (self.ffmpeg and self.ffprobe):
            messagebox.showerror(APP_NAME, "FFmpeg wasn't found. Put ffmpeg.exe and ffprobe.exe next to this program.")
            return
        jobs = [(j.iid, j) for j in self.targets()]
        if not jobs:
            messagebox.showinfo(APP_NAME, "Add some music first (drop files or folders on the window).")
            return
        if any(not j.info for _, j in jobs):
            messagebox.showinfo(APP_NAME, "Still reading some files; try again in a moment.")
            return
        bad = [j for _, j in jobs if "error" in j.info]
        if bad and not messagebox.askyesno(APP_NAME, f"{len(bad)} file(s) couldn't be read and will be skipped. Continue?"):
            return
        if self.save_mode.get() == "folder" and not self.out_dir.get().strip():
            messagebox.showwarning(APP_NAME, "Choose the folder to save in.")
            self.side.select(self.conv_tab)
            return
        plan, used = [], set()
        for iid, job in jobs:
            if "error" in job.info:
                continue
            target = os.path.join(self.base_dir(job), self.out_name(job))
            same = os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(job.path))
            if same or self.collide.get() == "rename" or os.path.normcase(target) in used:
                target = self.unique(target, used, job.path)
            used.add(os.path.normcase(target))
            plan.append((iid, job, target))
        self.busy = True
        self.cancel.clear()
        self.go_btn.state(["disabled"])
        self.cancel_btn.pack(side="right", padx=(px(8), 0))
        self.footer.set_progress(0)
        for j in self.jobs.values():
            j.status, j.status_tag = "", ""
        for iid, job, _ in plan:
            job.status, job.status_tag = "Waiting", "pending"
        self.refresh_jobs(list(self.jobs.values()))
        self.q = queue.Queue()
        self.started = time.perf_counter()
        threading.Thread(target=self.run_all, args=(plan, copy.deepcopy(self.p), self.collide.get(),
                                                    max(1, min(16, int(self.workers.get() or 1)))), daemon=True).start()
        self.after(100, self.poll)

    @staticmethod
    def unique(target, used, source):
        base, ext = os.path.splitext(target)
        n, cand = 2, target
        while (os.path.exists(cand) or os.path.normcase(cand) in used
               or os.path.normcase(os.path.abspath(cand)) == os.path.normcase(os.path.abspath(source))):
            cand = f"{base} ({n}){ext}"
            n += 1
        return cand

    def run_all(self, plan, p, collide, workers):
        fracs = {iid: 0.0 for iid, _, _ in plan}
        lock = threading.Lock()

        def progress(iid, f):
            with lock:
                fracs[iid] = f
                self.q.put(("progress", sum(fracs.values()) / max(1, len(fracs)) * 100))

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self.convert_one, iid, job, target, p, collide, progress) for iid, job, target in plan]
            concurrent.futures.wait(futures)
        self.q.put(("done",))

    def convert_one(self, iid, job, target, p, collide, progress):
        if self.cancel.is_set():
            self.q.put(("row", iid, "Cancelled", "pending"))
            return
        if collide == "skip" and os.path.exists(target):
            self.q.put(("row", iid, "Skipped (exists)", "pending"))
            progress(iid, 1)
            return
        self.q.put(("row", iid, "Converting…", "working"))
        ext = FORMATS[p["fmt"]][1]
        tmp = target[:-len(ext)] + ".part" + ext
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            proc = subprocess.Popen(build_cmd(self.ffmpeg, job, p, tmp), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", **NO_WINDOW)
            self.procs.add(proc)
            dur, tail = job.info.get("duration") or 0, []
            for line in proc.stdout:
                line = line.strip()
                if line.startswith(("out_time_us=", "out_time_ms=")) and dur:
                    v = line.split("=", 1)[1]
                    if v.lstrip("-").isdigit():
                        progress(iid, min(0.99, max(0, int(v)) / 1e6 / dur))
                elif line and "=" not in line:
                    tail = (tail + [line])[-5:]
            proc.wait()
            self.procs.discard(proc)
            if self.cancel.is_set():
                raise InterruptedError
            if proc.returncode != 0:
                raise RuntimeError(" / ".join(tail[-3:]) or f"ffmpeg exited with code {proc.returncode}")
            finish_output(job, tmp, p["fmt"])
            os.replace(tmp, target)
            job.output = target
            progress(iid, 1)
            self.q.put(("row", iid, "✓ " + human_size(os.path.getsize(target)), "done"))
            self.q.put(("saved", target))
        except InterruptedError:
            self.q.put(("row", iid, "Cancelled", "pending"))
        except Exception as e:
            self.q.put(("row", iid, "Failed", "error"))
            self.q.put(("log", f"{os.path.basename(job.path)}: {e}", "error"))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "row":
                    job = self.jobs.get(msg[1])
                    if job:
                        job.status, job.status_tag = msg[2], msg[3]
                        self.refresh_row(msg[1])
                elif msg[0] == "progress":
                    self.footer.set_progress(msg[1])
                    self.footer.set_status(f"Converting… {msg[1]:.0f}%")
                elif msg[0] == "saved":
                    self.last_output = msg[1]
                elif msg[0] == "log":
                    self.logbox.log(msg[1], msg[2])
                else:
                    self.finished()
                    return
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def finished(self):
        self.busy = False
        self.go_btn.state(["!disabled"])
        self.cancel_btn.pack_forget()
        self.footer.set_progress(None)
        jobs = list(self.jobs.values())
        ok = sum(1 for j in jobs if j.status.startswith("✓"))
        failed = sum(1 for j in jobs if j.status == "Failed")
        for j in jobs:
            if j.status == "Waiting":
                j.status, j.status_tag = "", ""
        self.refresh_jobs(jobs)
        secs = time.perf_counter() - self.started
        if self.cancel.is_set():
            self.footer.set_status(f"Cancelled after {ok} file(s).", "warn")
        elif failed:
            self.footer.set_status(f"{ok} converted, {failed} failed. See the Activity tab.", "error")
            self.side.select(self.act_tab)
        else:
            self.footer.set_status(f"Done in {secs:.1f} s  ·  {ok} file(s) converted. Double-click one to play it.", "ok")
        self.logbox.log(f"Finished: {ok} converted, {failed} failed", "error" if failed else "ok")

    def do_cancel(self):
        self.cancel.set()
        for proc in list(self.procs):
            if proc.poll() is None:
                proc.terminate()
        self.footer.set_status("Cancelling…", "warn")

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno(APP_NAME, "Files are still converting. Cancel and quit?"):
                return
            self.do_cancel()
        self.commit_panel(reload=False)
        dirty = sum(1 for j in self.jobs.values() if j.dirty() and j.taggable())
        if dirty and not self.busy:
            answer = messagebox.askyesnocancel(APP_NAME, f"Save the tag changes in {dirty} file(s) before closing?")
            if answer is None:
                return
            if answer:
                self.save_tags()
        self.cfg.update(params=self.p, user_presets=self.user_presets, save_mode=self.save_mode.get(),
                        out_dir=self.out_dir.get().strip(), keep_structure=self.keep_structure.get(),
                        pattern=self.pattern.get(), collide=self.collide.get(), workers=int(self.workers.get() or 1),
                        last_dir=self.last_dir, recent_formats=self.recent, **self.updater.state())
        save_json(self.config_file, self.cfg)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
