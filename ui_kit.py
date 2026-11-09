"""Shared CK Suite UI kit, updater, icons, tooltips, and dialogs for CK Music Converter."""
import json
import os
import queue
import re
import re as _re
import shutil
import subprocess
import sys
import tempfile as _tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error as _urlerror
import urllib.request as _urlrequest
import webbrowser as _webbrowser
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

APP_NAME = "CK Music Converter"
VERSION = "1.0.1"
REPO = "project-ceekay/ck-music-converter"
SETTINGS_NAME = "Music Converter"  # %APPDATA%\CK Suite\<this>

# ======================================================================= CK UI kit
# The shared CK Suite look (native Windows ttk, polished).
ACCENT = "#0067c0"      # Windows 11 accent blue
ERROR = "#c42b1c"
SUCCESS = "#0f7b0f"
WARN = "#9d5d00"
MUTED = "#5f6368"
HEADER_BG = "#ffffff"
BORDER = "#d9d9d9"
HOVER = "#e5f1fb"
DARK_MODE = False
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


def smooth_resize(root, settle_ms=120):
    """Keep dragging the window edge smooth. On Windows every Tk widget is its own native window,
    and all of them are moved and repainted on every resize step, so live resizing a full window
    lags. While the size is changing, the window shows a still picture of itself instead (the
    bottom-packed footer pinned to the bottom edge) and the real widgets are hidden; settle_ms
    after the last change they come back, laid out once for the new size."""
    if not sys.platform.startswith("win"):
        return
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    user32.GetDC.restype = H
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, H]
    user32.PrintWindow.argtypes = [wintypes.HWND, H, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = H
    gdi32.CreateCompatibleDC.argtypes = [H]
    gdi32.CreateCompatibleBitmap.restype = H
    gdi32.CreateCompatibleBitmap.argtypes = [H, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = H
    gdi32.SelectObject.argtypes = [H, H]
    gdi32.GetDIBits.argtypes = [H, H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [H]
    gdi32.DeleteDC.argtypes = [H]

    def capture(w, h):
        """The window's client area as Windows composed it, even where other windows cover it."""
        hwnd = int(root.wm_frame(), 16)
        hdc = user32.GetDC(hwnd)
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mem, bmp)
        try:
            if not user32.PrintWindow(hwnd, mem, 3):  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
                return None
            header = (ctypes.c_uint32 * 10)(40, w, ctypes.c_uint32(-h).value, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)  # 32-bit top-down BGRX rows
            if not gdi32.GetDIBits(mem, bmp, 0, h, buf, header, 0):
                return None
            return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(hwnd, hdc)

    bg = ttk.Style(root).lookup("TFrame", "background") or "#f0f0f0"
    state = {"size": None, "frozen": None, "shots": [], "job": None, "zoomed": False}
    big = round(200 * root.winfo_fpixels("1i") / 96)  # a jump this large (200 px at 100%) is no drag

    def thaw():
        state["job"] = None
        frozen, state["frozen"] = state["frozen"], None
        for w, info in frozen or ():
            w.pack(**info)  # in the original order, so the layout is exactly as before
        for shot in state["shots"]:
            shot.destroy()
        state["shots"] = []

    def freeze(old_w, old_h):
        slaves = [(w, w.pack_info()) for w in root.pack_slaves() if w.winfo_ismapped()]
        if not slaves:
            return
        w, h = root.winfo_width(), root.winfo_height()
        try:
            shown = capture(w, h)
        except (OSError, ValueError, tk.TclError):
            shown = None
        if shown is None:
            return  # just resize normally
        # the sections are still laid out for the old size; a strip lost to shrinking shows as background
        image = Image.new("RGB", (old_w, old_h), tuple(c >> 8 for c in root.winfo_rgb(bg)))
        image.paste(shown.crop((0, 0, min(w, old_w), min(h, old_h))))
        # everything from the first bottom-packed section down stays pinned to the bottom edge
        split = min([w.winfo_y() for w, info in slaves if info.get("side") == "bottom"] or [old_h])
        for box, place in (((0, 0, old_w, split), dict(x=0, y=0, anchor="nw")),
                           ((0, split, old_w, old_h), dict(x=0, rely=1, anchor="sw"))):
            if box[3] > box[1]:
                photo = ImageTk.PhotoImage(image.crop(box))
                shot = tk.Label(root, image=photo, bd=0, highlightthickness=0, bg=bg)
                shot.image = photo
                shot.place(**place)
                state["shots"].append(shot)
        for w, info in slaves:
            info.pop("in", None)
            w.pack_forget()
        state["frozen"] = slaves

    def on_configure(e):
        if e.widget is not root:
            return
        size = (e.width, e.height)
        old = state["size"]
        state["size"] = size
        if old in (None, size):
            return
        zoomed, was_zoomed = root.state() == "zoomed", state["zoomed"]
        state["zoomed"] = zoomed
        if state["frozen"] is None:
            # one-off jumps (maximize, restore, snapping) just lay out once, as normal
            if zoomed or was_zoomed or abs(size[0] - old[0]) > big or abs(size[1] - old[1]) > big:
                return
            freeze(*old)  # the window still shows the layout for the previous size
        if state["job"]:
            root.after_cancel(state["job"])
        state["job"] = root.after(settle_ms, thaw)

    root.configure(bg=bg)  # what shows in newly uncovered space while frozen
    root.bind("<Configure>", on_configure, add="+")


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
        bg = "#141414" if DARK_MODE else "#fbfbfb"
        fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
        bd = "#3c4043" if DARK_MODE else BORDER
        self.text = tk.Text(self, height=height, wrap="word", state="disabled", font=("Consolas", 9),
                            relief="flat", borderwidth=0, bg=bg, fg=fg, padx=px(6), pady=px(4),
                            highlightthickness=1, highlightbackground=bd, highlightcolor=bd)
        sb = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._apply_tags()

    def _apply_tags(self):
        self.text.tag_configure("error", foreground="#ff6b6b" if DARK_MODE else ERROR)
        self.text.tag_configure("ok", foreground="#4cc355" if DARK_MODE else SUCCESS)
        self.text.tag_configure("warn", foreground="#f0a020" if DARK_MODE else WARN)

    def log(self, msg, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")


def make_tree(parent, columns, height=6, selectmode="extended"):
    """columns: list of (id, heading, width, stretch). Returns a Treeview with a scrollbar."""
    frame = ttk.Frame(parent)
    col_ids = [c[0] for c in columns]
    tree = ttk.Treeview(frame, columns=col_ids, show="headings", height=height,
                        selectmode=selectmode)
    for cid, text, width, stretch in columns:
        tree.heading(cid, text=text, anchor="w")
        tree.column(cid, width=px(width), minwidth=px(40), stretch=stretch, anchor="w")
    sb = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    gridlines = [tk.Frame(tree, width=1, bd=0, highlightthickness=0) for _ in col_ids[:-1]]

    def update_gridlines(e=None):
        if not tree.winfo_exists():
            return
        line_color = "#2e3134" if DARK_MODE else "#e2e5e8"
        w_total = tree.winfo_width()
        head_h = px(24)
        x = 0
        for cid, line in zip(col_ids[:-1], gridlines):
            x += int(tree.column(cid, "width"))
            line.configure(bg=line_color)
            if 1 < x < w_total - 2:
                line.place(x=x - 1, y=head_h, width=1, relheight=1.0, height=-(head_h + 2))
            else:
                line.place_forget()

    def apply_tags():
        tree.tag_configure("pending", foreground="#9aa0a6" if DARK_MODE else MUTED)
        tree.tag_configure("working", foreground="#4cc2ff" if DARK_MODE else ACCENT)
        tree.tag_configure("modified", foreground="#4cc2ff" if DARK_MODE else ACCENT)
        tree.tag_configure("error", foreground="#ff6b6b" if DARK_MODE else ERROR)
        tree.tag_configure("done", foreground="#4cc355" if DARK_MODE else SUCCESS)

    tree._update_gridlines = update_gridlines
    tree._apply_tags = apply_tags
    apply_tags()
    tree.bind("<Configure>", lambda e: tree.after_idle(update_gridlines), add="+")
    tree.bind("<B1-Motion>", lambda e: tree.after_idle(update_gridlines), add="+")
    tree.bind("<ButtonRelease-1>", lambda e: tree.after_idle(update_gridlines), add="+")
    frame.tree = tree
    return frame


def set_window_dark_titlebar(win, dark):
    """Enable or disable Windows 10/11 native dark title bar on a Tk or Toplevel window."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            uxtheme = ctypes.windll.uxtheme
            uxtheme[135](2 if dark else 3)  # SetPreferredAppMode(ForceDark / ForceLight)
            uxtheme[136]()                  # FlushMenuThemes()
        except Exception:
            pass
        val = ctypes.c_int(1 if dark else 0)
        hwnds = set()
        try:
            frame_id = win.wm_frame()
            if frame_id and frame_id != "0x0":
                hwnds.add(int(frame_id, 16))
        except Exception:
            pass
        hwnd = win.winfo_id()
        hwnds.add(hwnd)
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnds.add(parent)
        for h in hwnds:
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(h, attr, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def apply_theme(root, dark=False):
    """Switch the entire window between Light Mode (Windows vista) and Dark Mode (clam dark)."""
    global DARK_MODE
    DARK_MODE = bool(dark)
    style = ttk.Style(root)
    line = tkfont.nametofont("TkDefaultFont").metrics("linespace")

    if dark:
        BG = "#1e1e1e"
        SURFACE = "#252526"
        FIELD = "#141414"
        HEAD = "#2d2d30"
        FG = "#e8eaed"
        MUTED_FG = "#9aa0a6"
        BD = "#3c4043"
        ACC = "#4cc2ff"
        SEL = "#094771"
        if "clam" in style.theme_names():
            style.theme_use("clam")
        root.configure(bg=BG)
        style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                        troughcolor=SURFACE, bordercolor=BD, darkcolor=BG, lightcolor=SURFACE,
                        selectbackground=SEL, selectforeground="#ffffff", insertcolor=FG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED_FG)
        style.configure("Error.TLabel", background=BG, foreground="#ff6b6b")
        style.configure("Success.TLabel", background=BG, foreground="#4cc355")
        style.configure("Warn.TLabel", background=BG, foreground="#f0a020")
        style.configure("Link.TLabel", background=BG, foreground=ACC)
        style.configure("TSeparator", background=BD)
        style.configure("TLabelframe", background=BG, bordercolor=BD)
        style.configure("TLabelframe.Label", background=BG, foreground=FG)
        style.configure("Section.TLabelframe", background=BG, bordercolor=BD)
        style.configure("Section.TLabelframe.Label", background=BG, foreground=ACC, font=("Segoe UI Semibold", 10))
        style.configure("TButton", background=HEAD, foreground=FG, bordercolor=BD, focuscolor=ACC, padding=(px(10), px(4)))
        style.map("TButton",
                  background=[("disabled", "#222222"), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("disabled", "#666666"), ("pressed", "#ffffff")])
        style.configure("Go.TButton", background=ACCENT, foreground="#ffffff", bordercolor="#005a9e",
                        font=("Segoe UI Semibold", 10), padding=(px(20), px(6)))
        style.map("Go.TButton",
                  background=[("disabled", "#2a2a2a"), ("pressed", "#004c87"), ("active", "#1077d0")],
                  foreground=[("disabled", "#666666"), ("!disabled", "#ffffff")])
        style.configure("Footer.TButton", background=HEAD, foreground=FG, bordercolor=BD, padding=(px(14), px(6)))
        style.map("Footer.TButton",
                  background=[("disabled", "#222222"), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("disabled", "#666666")])
        style.configure("Toolbutton", background=SURFACE, foreground=FG, bordercolor=BD, padding=(px(10), px(3)))
        style.map("Toolbutton",
                  background=[("selected", ACCENT), ("pressed", "#005a9e"), ("active", "#38383c")],
                  foreground=[("selected", "#ffffff"), ("!selected", FG)])
        style.configure("TCheckbutton", background=BG, foreground=FG, indicatorbackground=FIELD,
                        indicatorforeground=ACC, upperbordercolor=BD, lowerbordercolor=BD)
        style.map("TCheckbutton",
                  background=[("active", BG)],
                  foreground=[("disabled", "#666666")],
                  indicatorbackground=[("selected", ACCENT), ("disabled", "#222222")],
                  indicatorforeground=[("selected", "#ffffff")])
        style.configure("TRadiobutton", background=BG, foreground=FG, indicatorbackground=FIELD,
                        indicatorforeground=ACC, upperbordercolor=BD, lowerbordercolor=BD)
        style.map("TRadiobutton",
                  background=[("active", BG)],
                  foreground=[("disabled", "#666666")],
                  indicatorbackground=[("selected", ACCENT), ("disabled", "#222222")])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, bordercolor=BD,
                        lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.map("TEntry",
                  fieldbackground=[("readonly", SURFACE), ("disabled", "#1b1b1b")],
                  foreground=[("readonly", MUTED_FG), ("disabled", "#666666")])
        style.configure("Invalid.TEntry", fieldbackground=FIELD, foreground="#ff6b6b")
        style.configure("TCombobox", fieldbackground=FIELD, background=HEAD, foreground=FG,
                        arrowcolor=FG, bordercolor=BD, lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", FIELD), ("disabled", "#1b1b1b")],
                  foreground=[("readonly", FG), ("disabled", "#666666")],
                  background=[("active", "#38383c"), ("disabled", "#222222")],
                  arrowcolor=[("disabled", "#666666")])
        style.configure("TSpinbox", fieldbackground=FIELD, background=HEAD, foreground=FG,
                        arrowcolor=FG, bordercolor=BD, lightcolor=BD, darkcolor=BD, insertcolor=FG)
        style.map("TSpinbox",
                  fieldbackground=[("disabled", "#1b1b1b")],
                  foreground=[("disabled", "#666666")])
        style.configure("TNotebook", background=BG, bordercolor=BD, tabmargins=(0, px(2), 0, 0))
        style.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED_FG,
                        bordercolor=BD, lightcolor=SURFACE, padding=(px(10), px(4)))
        style.map("TNotebook.Tab",
                  background=[("selected", HEAD), ("active", "#323236")],
                  foreground=[("selected", FG), ("active", FG)])
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG,
                        bordercolor=BD, lightcolor=BD, darkcolor=BD, rowheight=line + px(5))
        style.map("Treeview",
                  background=[("selected", SEL)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=HEAD, foreground=FG, bordercolor=BD,
                        lightcolor=HEAD, darkcolor=BD, font=("Segoe UI Semibold", 10))
        style.map("Treeview.Heading", background=[("active", "#38383c"), ("pressed", "#444448")])
        style.configure("TScrollbar", background=HEAD, troughcolor=FIELD, bordercolor=FIELD,
                        arrowcolor=MUTED_FG, darkcolor=HEAD, lightcolor=HEAD)
        style.map("TScrollbar", background=[("active", "#454549"), ("pressed", "#55555a")])
        style.configure("TScale", background=BG, troughcolor=SURFACE, bordercolor=BD, darkcolor=HEAD, lightcolor=HEAD)
        style.configure("TProgressbar", background=ACCENT, troughcolor=SURFACE, bordercolor=BD)
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", SEL)
        root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    else:
        if "vista" in style.theme_names():
            style.theme_use("vista")
        bg = style.lookup("TFrame", "background") or "#f0f0f0"
        root.configure(bg=bg)
        style.configure("Go.TButton", font=("Segoe UI Semibold", 10), padding=(px(20), px(6)))
        style.configure("Footer.TButton", padding=(px(14), px(6)))
        style.configure("Toolbutton", padding=(px(10), px(3)))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground=ACCENT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Error.TLabel", foreground=ERROR)
        style.configure("Success.TLabel", foreground=SUCCESS)
        style.configure("Warn.TLabel", foreground=WARN)
        style.configure("Link.TLabel", foreground=ACCENT)
        style.configure("Invalid.TEntry", foreground=ERROR)
        style.configure("Treeview", rowheight=line + px(5))
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        style.configure("TNotebook.Tab", padding=(px(10), px(4)))
        root.option_add("*TCombobox*Listbox.background", "#ffffff")
        root.option_add("*TCombobox*Listbox.foreground", "#1a1a1a")
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    set_window_dark_titlebar(root, dark)
    frame_bg = style.lookup("TFrame", "background") or ("#1e1e1e" if dark else "#f0f0f0")

    def walk(w):
        for child in w.winfo_children():
            if isinstance(child, ScrollFrame):
                child.canvas.configure(bg=frame_bg)
            elif isinstance(child, LogBox):
                child.text.configure(
                    bg="#141414" if dark else "#fbfbfb",
                    fg="#e8eaed" if dark else "#1a1a1a",
                    highlightbackground="#3c4043" if dark else BORDER,
                    highlightcolor="#3c4043" if dark else BORDER,
                )
                child._apply_tags()
            elif isinstance(child, ttk.Treeview):
                if hasattr(child, "_apply_tags"):
                    child._apply_tags()
                if hasattr(child, "_update_gridlines"):
                    child._update_gridlines()
            walk(child)

    walk(root)


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
        if DARK_MODE:
            win.configure(bg="#1e1e1e")
        body = ttk.Frame(win, padding=self._px(18))
        body.pack(fill="both", expand=True)
        return win, body

    def _show(self, win):
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        set_window_dark_titlebar(win, DARK_MODE)
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
                  style="Muted.TLabel").grid(row=1, column=1, sticky="w")
        link = ttk.Label(body, text=f"github.com/{REPO}", style="Link.TLabel", cursor="hand2")
        link.grid(row=2, column=1, sticky="w", pady=(P(4), 0))
        link.bind("<Button-1>", lambda e: _webbrowser.open(f"https://github.com/{REPO}"))
        ttk.Separator(body).grid(row=3, column=0, columnspan=2, sticky="ew", pady=P(14))
        ttk.Checkbutton(body, text="Check for updates automatically (once a day)", variable=self.auto).grid(
            row=4, column=0, columnspan=2, sticky="w")
        self.about_lbl = ttk.Label(body, text="", style="Muted.TLabel")
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


def make_icon(size=64):
    """A white music note on the CK blue."""
    im, d, s = icon_base(size)
    d.ellipse([15 * s, 38 * s, 31 * s, 52 * s], fill="white")
    d.rectangle([27 * s, 13 * s, 31 * s, 46 * s], fill="white")
    d.polygon([(31 * s, 13 * s), (47 * s, 19 * s), (47 * s, 27 * s), (31 * s, 21 * s)], fill="white")
    return im


def make_toolbar_icon(kind, size=18, dark=False):
    """Crisp 2x-supersampled Mp3tag-style toolbar icons."""
    W = size * 2
    s = W / 24.0
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ink = "#e8eaed" if dark else "#3c4043"
    acc = "#4cc2ff" if dark else ACCENT
    card = "#2d2d30" if dark else "#ffffff"

    def folder_shape(ox=0, oy=0):
        d.polygon([(2 * s + ox, 6 * s + oy), (9 * s + ox, 6 * s + oy), (11 * s + ox, 8 * s + oy),
                   (21 * s + ox, 8 * s + oy), (21 * s + ox, 19 * s + oy), (2 * s + ox, 19 * s + oy)],
                  fill="#f3b63f", outline="#b87d14", width=max(1, round(1.3 * s)))
        d.rectangle([3 * s + ox, 10 * s + oy, 20 * s + ox, 18 * s + oy], fill="#ffd269")

    def page_shape(x1, y1, x2, y2):
        d.rectangle([x1, y1, x2, y2], fill=card, outline="#9aa0a6" if dark else "#5f6368", width=max(1, round(1.3 * s)))
        for ly in (0.32, 0.55, 0.76):
            y = y1 + (y2 - y1) * ly
            d.line([(x1 + 2.2 * s, y), (x2 - 2.2 * s, y)], fill="#9aa0a6", width=max(1, round(1.2 * s)))

    def plus_badge(cx, cy, r=5.2):
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill="#25a244", outline="#ffffff",
                  width=max(1, round(1.1 * s)))
        w = max(1, round(1.8 * s))
        d.line([(cx * s, (cy - 2.8) * s), (cx * s, (cy + 2.8) * s)], fill="#ffffff", width=w)
        d.line([((cx - 2.8) * s, cy * s), ((cx + 2.8) * s, cy * s)], fill="#ffffff", width=w)

    def tag_dot(cx, cy, r=3.8, color="#e65100"):
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=color, outline="#a83800",
                  width=max(1, round(1.1 * s)))

    def right_arrow(x1, x2, y, color=None):
        color = color or ink
        w = max(1, round(1.6 * s))
        d.line([(x1 * s, y * s), (x2 * s, y * s)], fill=color, width=w)
        d.polygon([(x2 * s + 1 * s, y * s), ((x2 - 3.2) * s, (y - 2.8) * s), ((x2 - 3.2) * s, (y + 2.8) * s)],
                  fill=color)

    if kind == "folder_open":
        folder_shape()
    elif kind == "folder_plus":
        folder_shape(ox=-1 * s, oy=-1 * s)
        plus_badge(17.5, 17.0)
    elif kind == "file_plus":
        page_shape(4 * s, 3 * s, 17 * s, 20 * s)
        plus_badge(17.5, 17.0)
    elif kind == "save":
        d.rounded_rectangle([3 * s, 3 * s, 21 * s, 21 * s], radius=2 * s, fill=ACCENT, outline="#004a8f",
                            width=max(1, round(1.2 * s)))
        d.rectangle([7 * s, 3 * s, 16 * s, 9 * s], fill="#e8eaed")
        d.rectangle([6 * s, 13 * s, 18 * s, 20 * s], fill="#ffffff")
    elif kind == "undo":
        w = max(2, round(2.2 * s))
        d.arc([5 * s, 7 * s, 20 * s, 20 * s], start=210, end=45, fill=acc, width=w)
        d.polygon([(3 * s, 11 * s), (10 * s, 6 * s), (9 * s, 13 * s)], fill=acc)
    elif kind == "refresh":
        w = max(2, round(2.1 * s))
        d.arc([4 * s, 4 * s, 20 * s, 20 * s], start=30, end=310, fill=acc, width=w)
        d.polygon([(20 * s, 4 * s), (21 * s, 11 * s), (14 * s, 9 * s)], fill=acc)
    elif kind == "remove":
        w = max(2, round(2.5 * s))
        c = "#ff6b6b" if dark else ERROR
        d.line([(6 * s, 6 * s), (18 * s, 18 * s)], fill=c, width=w)
        d.line([(18 * s, 6 * s), (6 * s, 18 * s)], fill=c, width=w)
    elif kind == "t2f":
        tag_dot(4.8, 12.0, r=3.6)
        right_arrow(9.2, 13.8, 12.0)
        page_shape(14.5 * s, 4.5 * s, 22 * s, 19.5 * s)
    elif kind == "f2t":
        page_shape(2 * s, 4.5 * s, 9.5 * s, 19.5 * s)
        right_arrow(10.5, 15.2, 12.0)
        tag_dot(19.2, 12.0, r=3.6)
    elif kind == "t2t":
        tag_dot(4.8, 12.0, r=3.6, color="#e65100")
        right_arrow(9.5, 14.5, 12.0)
        tag_dot(19.2, 12.0, r=3.6, color=acc)
    elif kind == "ext_tags":
        page_shape(3 * s, 3 * s, 16 * s, 20 * s)
        tag_dot(18.0, 15.5, r=4.2)
    elif kind == "actions":
        d.rounded_rectangle([2.5 * s, 4.5 * s, 21.5 * s, 19.5 * s], radius=2.5 * s, fill=card,
                            outline=ink, width=max(1, round(1.3 * s)))
        w = max(1, round(1.8 * s))
        d.line([(6 * s, 16.5 * s), (9 * s, 7.5 * s), (12 * s, 16.5 * s)], fill=acc, width=w)
        d.line([(7 * s, 14 * s), (11 * s, 14 * s)], fill=acc, width=max(1, round(1.4 * s)))
        d.ellipse([14 * s, 10.5 * s, 18.5 * s, 16.5 * s], outline=ink, width=max(1, round(1.6 * s)))
        d.line([(18.5 * s, 10.5 * s), (18.5 * s, 16.5 * s)], fill=ink, width=max(1, round(1.6 * s)))
    elif kind == "autonum":
        d.rounded_rectangle([2.5 * s, 4.5 * s, 21.5 * s, 19.5 * s], radius=2.5 * s, fill=card,
                            outline=acc, width=max(1, round(1.3 * s)))
        w = max(1, round(1.8 * s))
        d.line([(7.5 * s, 8 * s), (7.5 * s, 16 * s)], fill=acc, width=w)
        d.line([(10.5 * s, 16 * s), (14 * s, 8 * s)], fill="#80868b", width=max(1, round(1.4 * s)))
        d.line([(15.5 * s, 9 * s), (18.5 * s, 9 * s), (15.5 * s, 15.5 * s), (19 * s, 15.5 * s)],
               fill=ink, width=max(1, round(1.5 * s)))
    elif kind == "sources":
        r = 8.5 * s
        cx = cy = 12 * s
        w = max(1, round(1.4 * s))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#1b3a57" if dark else "#e5f1fb",
                  outline=acc, width=max(1, round(1.5 * s)))
        d.ellipse([cx - 4.2 * s, cy - r, cx + 4.2 * s, cy + r], outline=acc, width=w)
        d.line([(cx - r, cy), (cx + r, cy)], fill=acc, width=w)
    elif kind == "convert":
        d.polygon([(6 * s, 4.5 * s), (19.5 * s, 12 * s), (6 * s, 19.5 * s)], fill="#4cc355" if dark else SUCCESS)
    elif kind == "theme":
        # Crescent moon for theme toggle
        d.ellipse([4.5 * s, 4.5 * s, 19.5 * s, 19.5 * s], fill="#fbbc04" if dark else "#5f6368")
        d.ellipse([9.0 * s, 2.5 * s, 22.0 * s, 15.5 * s], fill=(0, 0, 0, 0))

    return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))


def attach_tooltip(widget, text, status_fn=None):
    """Native-style hover tooltip below a toolbar button."""
    tip = {"win": None, "job": None}

    def hide():
        if tip["job"]:
            widget.after_cancel(tip["job"])
            tip["job"] = None
        if tip["win"] is not None:
            try:
                tip["win"].destroy()
            except tk.TclError:
                pass
            tip["win"] = None

    def show():
        tip["job"] = None
        if not widget.winfo_exists():
            return
        w = tk.Toplevel(widget)
        w.withdraw()
        w.wm_overrideredirect(True)
        bg = "#2d2d30" if DARK_MODE else "#ffffe1"
        fg = "#e8eaed" if DARK_MODE else "#1a1a1a"
        lbl = tk.Label(w, text=text, bg=bg, fg=fg, font=("Segoe UI", 9),
                       relief="solid", bd=1, padx=px(6), pady=px(2))
        lbl.pack()
        w.update_idletasks()
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + px(3)
        w.geometry(f"+{x}+{y}")
        w.deiconify()
        w.attributes("-topmost", True)
        w.lift()
        tip["win"] = w

    def enter(e=None):
        hide()
        if status_fn:
            status_fn(text)
        tip["job"] = widget.after(150, show)

    def leave(e=None):
        hide()
        if status_fn:
            status_fn("")

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<ButtonPress>", lambda e: hide(), add="+")


# ------------------------------------------------------------- dialogs
def make_dialog(root, title, resizable=False):
    win = tk.Toplevel(root)
    win.withdraw()
    win.title(title)
    win.transient(root)
    win.resizable(resizable, resizable)
    if DARK_MODE:
        win.configure(bg="#1e1e1e")
    body = ttk.Frame(win, padding=px(16))
    body.pack(fill="both", expand=True)
    return win, body


def show_dialog(root, win):
    win.bind("<Escape>", lambda e: win.destroy())
    win.update_idletasks()
    set_window_dark_titlebar(win, DARK_MODE)
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
