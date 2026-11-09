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
import concurrent.futures
import copy
import csv
import io
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

import ui_kit
from ui_kit import *
from lyrics import *
from tagger import *
from converter import *

APP_NAME = "CK Music Converter"
VERSION = "1.0.1"
REPO = "project-ceekay/ck-music-converter"
SETTINGS_NAME = "Music Converter"  # %APPDATA%\CK Suite\<this>
ui_kit.VERSION = VERSION


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
        self.dark_mode = tk.BooleanVar(value=bool(self.cfg.get("dark_mode", False)))
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
        self.apply_dark_mode()
        self.load_params_into_ui()
        self.load_panel(commit=False)
        self.update_title()
        self.footer.set_status("Ready — drop music files or folders anywhere on this window.")
        enable_file_drop(self, self.on_drop)
        smooth_resize(self)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        for seq, fn in (("<Control-s>", self.save_tags), ("<Control-z>", self.undo), ("<Control-o>", self.browse_files),
                        ("<Control-d>", self.browse_folder), ("<Control-k>", self.auto_number),
                        ("<Control-m>", self.musicbrainz), ("<Control-Return>", self.start),
                        ("<Alt-Key-1>", self.tag_to_filename), ("<Alt-Key-2>", self.filename_to_tag),
                        ("<Alt-Key-3>", self.tag_to_tag), ("<Alt-t>", self.extended_tags),
                        ("<Alt-T>", self.extended_tags), ("<F3>", self.toggle_filter), ("<F5>", self.reload_tags)):
            self.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
        self.after(200, self.check_ffmpeg)
        self.after(200, self.drain_probes)

    # ---------------------------------------------------------- UI
    def build_ui(self):
        self.footer = Footer(self)
        self.go_btn = self.footer.button("Convert", self.start, primary=True)
        self.footer.button("Save Tags", self.save_tags)
        self.footer.button("Open Folder", self.open_out)
        self.updater = Updater(self, self.footer, self.cfg, make_icon, self.footer.set_status,
                               self.footer.set_progress, self.on_close)
        self.cancel_btn = ttk.Button(self.footer, text="Cancel", style="Footer.TButton", command=self.do_cancel)
        self.show_filter_var = tk.BooleanVar(value=True)
        self.build_menubar()
        self.build_toolbar()

        body = ttk.Frame(self, padding=(px(12), px(8), px(12), px(8)))
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
        right.pack(side="left", fill="both", expand=True, padx=(px(12), 0))
        self.filter_bar = ttk.Frame(right)
        self.filter_bar.pack(fill="x", pady=(0, px(6)))
        ttk.Label(self.filter_bar, text="Filter").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(self.filter_bar, textvariable=self.filter_var, width=34)
        self.filter_entry.pack(side="left", padx=(px(6), 0))
        self.filter_var.trace_add("write", lambda *_: self.apply_filter())
        self.count_lbl = ttk.Label(self.filter_bar, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="right")
        self.tree_frame = make_tree(right, COLUMNS, height=12)
        self.tree_frame.pack(fill="both", expand=True)
        self.tree = self.tree_frame.tree
        for cid, text, *_ in COLUMNS:
            self.tree.heading(cid, text=text, command=lambda c=cid: self.sort_by(c))
        self.tree.tag_configure("modified", foreground=ACCENT)
        self.tree.bind("<<TreeviewSelect>>", lambda e: (self.load_panel(), self.update_count(), self.update_estimate()))
        self.tree.bind("<Double-1>", self.open_row)
        self.tree.bind("<Delete>", lambda e: self.remove())
        self.tree.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        self.update_count()

    def build_menubar(self):
        mb_bg = "#1e1e1e" if ui_kit.DARK_MODE else "#ffffff"
        mb_fg = "#e8eaed" if ui_kit.DARK_MODE else "#1a1a1a"
        bar = tk.Frame(self, bg=mb_bg, padx=px(4), pady=px(1))
        bar.pack(fill="x")
        self.menubar_frame = bar
        self._menu_btns = []
        self._menus = []

        def add_menu(label, underline, items):
            btn = tk.Label(bar, text=label, underline=underline, bg=mb_bg, fg=mb_fg,
                           font=("Segoe UI", 9), padx=px(7), pady=px(2))
            btn.pack(side="left")
            m = tk.Menu(btn, tearoff=False, font=("Segoe UI", 9))
            self._menus.append(m)
            for item in items:
                if item is None:
                    m.add_separator()
                elif item[0] == "__check__":
                    m.add_checkbutton(label=item[1], variable=item[2], command=item[3],
                                      accelerator=item[4] if len(item) > 4 else "")
                else:
                    m.add_command(label=item[0], command=item[1],
                                  accelerator=item[2] if len(item) > 2 else "",
                                  underline=item[3] if len(item) > 3 else -1)

            def on_enter(e=None):
                hov = "#323236" if ui_kit.DARK_MODE else "#e5f1fb"
                btn.configure(bg=hov)

            def on_leave(e=None):
                cur = "#1e1e1e" if ui_kit.DARK_MODE else "#ffffff"
                btn.configure(bg=cur)

            def open_menu(e=None):
                on_enter()
                try:
                    m.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
                finally:
                    m.grab_release()
                    on_leave()
                return "break"

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            btn.bind("<Button-1>", open_menu)
            if 0 <= underline < len(label):
                ch = label[underline].lower()
                self.bind(f"<Alt-Key-{ch}>", open_menu)
                self.bind(f"<Alt-Key-{ch.upper()}>", open_menu)
            self._menu_btns.append(btn)
            return m

        add_menu("File", 0, [
            ("Change directory…", self.change_folder, "", 0),
            ("Add directory…", self.browse_folder, "Ctrl+D", 0),
            ("Add files…", self.browse_files, "Ctrl+O", 4),
            None,
            ("Save tag", self.save_tags, "Ctrl+S", 0),
            ("Remove tag…", self.action_remove, "", 0),
            None,
            ("Add cover…", self.pick_cover, ""),
            ("Extract cover…", self.extract_cover_file, ""),
            ("Remove cover", lambda: self.stage_cover("remove"), ""),
            None,
            ("Export to CSV…", self.export_csv, "", 0),
            None,
            ("Exit", self.on_close, "Alt+F4", 1),
        ])
        add_menu("Edit", 0, [
            ("Undo", self.undo, "Ctrl+Z", 0),
            None,
            ("Select all files", self.select_all, "Ctrl+A", 0),
            ("Invert selection", self.invert_selection, "", 0),
            None,
            ("Remove from list", self.remove, "Del", 0),
            ("Clear list", self.clear, "", 0),
        ])
        add_menu("View", 0, [
            ("Extended Tags…", self.extended_tags, "Alt+T", 9),
            None,
            ("Tags panel", lambda: self.side.select(self.tag_tab), ""),
            ("Convert panel", lambda: self.side.select(self.conv_tab), ""),
            ("Activity log", lambda: self.side.select(self.act_tab), ""),
            None,
            ("__check__", "Filter", self.show_filter_var, self.toggle_filter, "F3"),
            ("__check__", "Dark Mode", self.dark_mode, self.apply_dark_mode, ""),
            None,
            ("Refresh", self.reload_tags, "F5", 0),
        ])
        add_menu("Convert", 0, [
            ("Tag - Filename…", self.tag_to_filename, "Alt+1", 0),
            ("Filename - Tag…", self.filename_to_tag, "Alt+2", 0),
            ("Tag - Tag…", self.tag_to_tag, "Alt+3", 6),
            None,
            ("Convert Audio Now", self.start, "Ctrl+Enter", 0),
        ])
        self.actions_menu_items = [
            ("Change Case…", self.action_case),
            ("Replace…", self.action_replace),
            ("Remove Fields…", self.action_remove),
            ("Trim Spaces", self.action_trim),
            None,
            ("Auto-Number Tracks…", self.auto_number, "Ctrl+K"),
        ]
        add_menu("Actions", 0, self.actions_menu_items)
        self.sources_menu_items = [
            ("MusicBrainz…", self.musicbrainz, "Ctrl+M"),
            ("Copy Tags From Folder…", self.match_tags),
        ]
        add_menu("Tag Sources", 4, self.sources_menu_items)
        add_menu("Help", 0, [
            ("Check for Updates…", lambda: self.updater.check(quiet=False), ""),
            None,
            (f"About {APP_NAME}", self.updater.show_about, ""),
        ])
        self.menubar_sep = tk.Frame(self, height=1, bg=BORDER)
        self.menubar_sep.pack(fill="x")

    def build_toolbar(self):
        bg = ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0"
        bar = tk.Frame(self, bg=bg, padx=px(8), pady=px(3))
        bar.pack(fill="x")
        self.toolbar_bar = bar
        self._tb_icons = {}
        self._tb_btns = []
        self._tb_seps = []
        icon_px = px(18)
        cell_px = px(24)
        self.tb_hint = tk.Label(bar, text="", bg=bg, fg=MUTED, font=("Segoe UI", 9))
        self.tb_hint.pack(side="right", padx=(0, px(6)))

        def set_tb_hint(text):
            self.tb_hint.configure(text=text)
            if not self.busy:
                self.footer.set_status(text or "Ready")

        def make_btn(kind, tip, cmd, menu_items=None):
            icon = make_toolbar_icon(kind, icon_px, dark=ui_kit.DARK_MODE)
            self._tb_icons[kind] = icon
            btn = tk.Label(bar, image=icon, width=cell_px, height=cell_px, bg=bg, bd=0,
                           highlightthickness=1, highlightbackground=bg)
            btn.pack(side="left", padx=px(1))
            self._tb_btns.append((kind, btn))

            popup = None
            if menu_items is not None:
                popup = tk.Menu(btn, tearoff=False, font=("Segoe UI", 9))
                self._menus.append(popup)
                for item in menu_items:
                    if item is None:
                        popup.add_separator()
                    else:
                        popup.add_command(label=item[0], command=item[1],
                                          accelerator=item[2] if len(item) > 2 else "")

            def on_enter(e=None):
                hov = "#323236" if ui_kit.DARK_MODE else HOVER
                bd_hov = "#4cc2ff" if ui_kit.DARK_MODE else "#99d1ff"
                btn.configure(bg=hov, highlightbackground=bd_hov)

            def on_leave(e=None):
                cur_bg = "#252526" if ui_kit.DARK_MODE else (ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0")
                btn.configure(bg=cur_bg, highlightbackground=cur_bg)

            def on_press(e=None):
                pr_bg = "#094771" if ui_kit.DARK_MODE else "#cce4f7"
                btn.configure(bg=pr_bg, highlightbackground=ACCENT)

            def on_release(e=None):
                on_enter()
                if popup is not None:
                    popup.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
                elif cmd:
                    cmd()

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            btn.bind("<ButtonPress-1>", on_press)
            btn.bind("<ButtonRelease-1>", on_release)
            attach_tooltip(btn, tip, status_fn=set_tb_hint)

        def sep():
            s = tk.Frame(bar, width=1, height=px(18), bg=BORDER)
            s.pack(side="left", padx=px(5), pady=px(4))
            self._tb_seps.append(s)

        make_btn("folder_open", "Change directory…", self.change_folder)
        make_btn("folder_plus", "Add directory… (Ctrl+D)", self.browse_folder)
        make_btn("file_plus", "Add files… (Ctrl+O)", self.browse_files)
        sep()
        make_btn("save", "Save tag (Ctrl+S)", self.save_tags)
        make_btn("undo", "Undo (Ctrl+Z)", self.undo)
        make_btn("refresh", "Refresh (F5)", self.reload_tags)
        make_btn("remove", "Remove from list (Del)", self.remove)
        sep()
        make_btn("t2f", "Tag - Filename (Alt+1)", self.tag_to_filename)
        make_btn("f2t", "Filename - Tag (Alt+2)", self.filename_to_tag)
        make_btn("t2t", "Tag - Tag (Alt+3)", self.tag_to_tag)
        sep()
        make_btn("actions", "Actions", None, menu_items=self.actions_menu_items)
        make_btn("autonum", "Auto-numbering Wizard (Ctrl+K)", self.auto_number)
        make_btn("ext_tags", "Extended Tags… (Alt+T)", self.extended_tags)
        sep()
        make_btn("sources", "Tag Sources (MusicBrainz / Copy Tags)", None, menu_items=self.sources_menu_items)
        make_btn("convert", "Convert Audio Now (Ctrl+Enter)", self.start)
        sep()
        make_btn("theme", "Toggle Dark Mode", self.toggle_dark_mode)
        ttk.Separator(self).pack(fill="x")

    def toggle_dark_mode(self):
        self.dark_mode.set(not self.dark_mode.get())
        self.apply_dark_mode()

    def apply_dark_mode(self):
        dark = bool(self.dark_mode.get())
        apply_theme(self, dark)
        mb_bg = "#1e1e1e" if dark else "#ffffff"
        mb_fg = "#e8eaed" if dark else "#1a1a1a"
        tb_bg = "#252526" if dark else (ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0")
        sep_bg = "#3c4043" if dark else BORDER
        if hasattr(self, "menubar_frame"):
            self.menubar_frame.configure(bg=mb_bg)
            self.menubar_sep.configure(bg="#2d2d30" if dark else "#e5e5e5")
            for b in self._menu_btns:
                b.configure(bg=mb_bg, fg=mb_fg)
        for m in getattr(self, "_menus", ()):
            m.configure(
                bg="#252526" if dark else "#ffffff",
                fg="#e8eaed" if dark else "#1a1a1a",
                activebackground="#094771" if dark else ACCENT,
                activeforeground="#ffffff",
                selectcolor="#4cc2ff" if dark else "#1a1a1a",
            )
        self.toolbar_bar.configure(bg=tb_bg)
        self.tb_hint.configure(bg=tb_bg, fg="#9aa0a6" if dark else MUTED)
        icon_px = px(18)
        for kind, btn in self._tb_btns:
            icon = make_toolbar_icon(kind, icon_px, dark=dark)
            self._tb_icons[kind] = icon
            btn.configure(image=icon, bg=tb_bg, highlightbackground=tb_bg)
        for s in self._tb_seps:
            s.configure(bg=sep_bg)
        if hasattr(self, "cover_holder"):
            c_bg = "#252526" if dark else "#ececec"
            self.cover_holder.configure(bg=c_bg, highlightbackground=sep_bg)
            self.cover_lbl.configure(bg=c_bg, fg="#9aa0a6" if dark else MUTED)
        if hasattr(self, "lyrics_box"):
            self.lyrics_box.configure(highlightbackground=sep_bg, insertbackground="#e8eaed" if dark else "#1a1a1a")
            self.load_lyrics_box(self.panel_jobs)

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
        self.cover_holder = holder
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
            first_p = os.path.normpath(paths[0])
            self.last_dir = first_p if os.path.isdir(first_p) else os.path.dirname(first_p)
            self.footer.set_status(f"Added {added} file(s).")
            self.logbox.log(f"Added {added} file(s)")
            if self.sort_col:
                self.resort()
            else:
                self.apply_filter()
            self.update_title()
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

    def change_folder(self):
        if self.busy:
            return
        d = filedialog.askdirectory(title="Change directory…", initialdir=self.last_dir)
        if not d:
            return
        self.clear()
        if not self.jobs:
            self.add_paths([d])

    def invert_selection(self):
        vis = list(self.tree.get_children())
        sel = set(self.tree.selection())
        self.tree.selection_set([i for i in vis if i not in sel])

    def toggle_filter(self):
        if self.filter_bar.winfo_ismapped():
            self.filter_bar.pack_forget()
            self.show_filter_var.set(False)
        else:
            self.filter_bar.pack(fill="x", pady=(0, px(6)), before=self.tree_frame)
            self.show_filter_var.set(True)
            self.filter_entry.focus_set()

    def update_title(self):
        base = f"{APP_NAME} v{VERSION}"
        jobs = self.panel_jobs or [self.jobs[i] for i in self.order if i in self.jobs]
        if not jobs:
            self.title(base)
            return
        dirs = list(dict.fromkeys(os.path.dirname(os.path.normpath(j.path)) for j in jobs))
        try:
            common = os.path.commonpath(dirs) if len(dirs) > 1 else dirs[0]
        except ValueError:
            common = dirs[0]
        if os.path.splitdrive(common)[1] in ("", "\\"):
            folder = dirs[0]
        else:
            folder = common
        folder = folder.rstrip("\\/") + "\\"
        self.title(f"{base} - {folder}")

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
            self.update_title()
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
        dark = ui_kit.DARK_MODE
        fg_muted = "#9aa0a6" if dark else MUTED
        fg_norm = "#e8eaed" if dark else "#1a1a1a"
        bg_act = "#141414" if dark else "white"
        bg_dis = "#1b1b1b" if dark else "#f3f3f3"
        box.configure(fg=fg_muted if text == KEEP else fg_norm, state="normal" if jobs else "disabled",
                      bg=bg_act if jobs else bg_dis, insertbackground=fg_norm)
        box.edit_reset()
        self.panel_loaded["lyrics"] = text

    def lyrics_focus(self, e=None):
        """Clicking into '< keep >' empties the box; leaving it empty still keeps each file's lyrics."""
        if self.lyrics_box.get("1.0", "end-1c") == KEEP:
            self.lyrics_box.delete("1.0", "end")
            self.lyrics_box.configure(fg="#e8eaed" if ui_kit.DARK_MODE else "#1a1a1a")
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
        dark = ui_kit.DARK_MODE
        txt = tk.Text(frame, width=60, height=22, wrap="word", font=("Segoe UI", 10), undo=True, relief="flat",
                      bg="#141414" if dark else "white", fg="#e8eaed" if dark else "#1a1a1a",
                      insertbackground="#e8eaed" if dark else "#1a1a1a",
                      highlightthickness=1, highlightbackground="#3c4043" if dark else BORDER,
                      highlightcolor=ACCENT, padx=px(8), pady=px(6))
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
                        last_dir=self.last_dir, dark_mode=self.dark_mode.get(), recent_formats=self.recent,
                        **self.updater.state())
        save_json(self.config_file, self.cfg)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
