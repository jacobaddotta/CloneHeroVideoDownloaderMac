#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI wrapper for VideoDownload.py - Clone Hero Video Downloader
Provides a simple desktop interface to configure and run the video downloader.
"""

import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import signal
import configparser

# Reuse the surgical song.ini writer from VideoDownload.py. The previous
# hand-rolled configparser writes here rebuilt the file, which stripped the
# [song] header, dropped comments, and lost any second section.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from VideoDownload import update_song_ini, parse_song_ini
import library
import preview
import tempfile
import theme
import json as _json
import urllib.request
import webbrowser

# Config file location
CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "songs_folder": "",
    "quality": "best1080",
    "replace": False,
    "auto_sync": True,
    "min_conf": 0.20,
    "official_360": False,
    "sleep_interval": 0.0,
    "max_sleep_interval": 0.0,
    "limit_rate": "",
    "workers": 1,
    "sync_only": False,
    "manual_map": "",
    "only_list": ""
}


# --------------------------------------------------------------------------
# Hover descriptions
# --------------------------------------------------------------------------

class ToolTip:
    """Small yellow popup shown when the pointer rests on a widget."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)          # no title bar
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify=tk.LEFT,
                 background=theme.PANEL, foreground=theme.FG,
                 relief=tk.SOLID, borderwidth=1, wraplength=380,
                 font=theme.FONT_SMALL, padx=10, pady=8).pack()

    def _hide(self, _=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


# What each control does, keyed by the exact label text shown in the window.
TOOLTIPS = {
    "Songs Folder:":
        "Your Clone Hero Songs folder. Every chart inside it (including "
        "subfolders) gets scanned.",
    "Quality:":
        "Highest resolution to download.\n\n"
        "best1080 = up to 1080p (recommended)\n"
        "best720 = up to 720p, smaller files\n"
        "best = no cap, can be very large",
    "Replace existing videos":
        "Re-download videos for charts that already have one. Without this, "
        "charts with a video.mp4 are skipped.\n\n"
        "Use this to upgrade the 360p and AV1 videos in your library.",
    "Auto-sync":
        "Work out where the video should start relative to the chart and "
        "write video_start_time into song.ini.\n\n"
        "Only writes when several sections of the song independently agree, "
        "so a wrong video is left alone rather than guessed at.",
    "Allow official 360p fallback":
        "Accept a 360p video instead of rejecting it. Off means anything "
        "below 720p is refused.",
    "Sync-only (no download)":
        "Do not download anything. Just recalculate video_start_time for "
        "charts that already have a video. Needs no internet.",
    "Min autosync conf:":
        "How confident the sync must be before it is written, from 0 to 1. "
        "0.30 is a sensible default. Raise it to be stricter.",
    "Workers:":
        "How many charts to work on at once. 4 is a good balance. Set to 1 "
        "if you want the output to read in order.",
    "Sleep interval (s):":
        "Seconds to wait between downloads, to be polite to YouTube. 0 means "
        "no wait.",
    "Max sleep (s):":
        "Upper bound for the random wait between downloads. Used together "
        "with Sleep interval.",
    "Limit rate:":
        "Cap the download speed, for example 5M for 5 MB/s. Leave blank for "
        "no limit.",
    "Manual map file:":
        "A plain text file that forces specific YouTube videos for specific "
        "songs, one per line:\n\n"
        "Artist - Song|https://youtube.com/watch?v=...\n\n"
        "Use it when the automatic search keeps picking the wrong video. "
        "Leave blank if you are not using one.",
    "Only-list file:":
        "Restrict the run to only the songs listed in this file, one per "
        "line (either 'Artist - Song' or the chart's folder name).\n\n"
        "You normally do not need this: tick songs in the list above and "
        "turn on 'Only run checked songs' instead.",
    "YouTube URL:":
        "Paste a YouTube link to force it for the one song you loaded with "
        "'Load Song Folder'. Saved into song.ini as video_url.",
    "Video timing (ms):":
        "Set video_start_time by hand, in milliseconds.\n\n"
        "Positive skips INTO the video (the video has an intro).\n"
        "Negative delays the video (the chart has a lead-in).",
}


class Collapsible(ttk.Frame):
    """A titled strip that folds its contents away when clicked."""

    def __init__(self, parent, title, expanded=False, subtitle=""):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self._title = title
        self._subtitle = subtitle
        self._open = bool(expanded)

        self._header = ttk.Button(self, style="Section.TButton",
                                  command=self.toggle)
        self._header.grid(row=0, column=0, sticky=(tk.W, tk.E))

        self.body = ttk.Frame(self, padding=(16, 12, 16, 14))
        self.body.columnconfigure(1, weight=1)
        self._refresh()

    def toggle(self):
        self._open = not self._open
        self._refresh()

    def expand(self):
        if not self._open:
            self.toggle()

    def set_subtitle(self, text):
        self._subtitle = text
        self._refresh()

    def _refresh(self):
        arrow = "▾" if self._open else "▸"
        tail = f"     {self._subtitle}" if self._subtitle else ""
        self._header.config(text=f"{arrow}   {self._title}{tail}")
        if self._open:
            self.body.grid(row=1, column=0, sticky=(tk.W, tk.E))
        else:
            self.body.grid_remove()


class VideoDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Clone Hero Video Downloader for Mac")
        self.root.geometry("1120x1000")
        self.style = theme.apply_theme(self.root)
        self.root.configure(background=theme.BG)
        
        # Process management
        self.process = None
        self.is_running = False

        # Song picker state
        self.charts = []          # list[library.ChartInfo], scan order
        self.item_to_chart = {}   # treeview item id -> ChartInfo
        self.checked_rels = set() # chart.rel values that are ticked
        self.scanning = False
        self.sort_col = "num"
        self.sort_desc = False
        
        self.active_chart = None   # the highlighted row, drives the manual panel
        self.review_queue = []     # charts the last run was unsure about
        self.review_picks = []     # (chart, title) chosen during a review pass
        self._anchor_item = None   # last checkbox clicked, for shift-click ranges

        # Load config
        self.config = self.load_config()

        # Settings vars must exist before the UI reads them
        self.init_vars()

        # Build UI
        self.build_ui()
        
        # Populate from config
        self.load_settings_to_ui()
        
        # Hover descriptions, matched to labels by their text
        self.attach_tooltips()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Scan whatever folder was restored from the config
        if self.songs_folder_var.get().strip():
            self.root.after(300, self.scan_songs)
    
    # ------------------------------------------------------------------
    # Settings live in these vars. The dialog edits copies and only writes
    # back when Apply is pressed, so closing the dialog discards everything.
    # ------------------------------------------------------------------

    SETTING_SPECS = [
        # (attribute,             config key,           kind,    default)
        ("songs_folder_var",      "songs_folder",       "str",   ""),
        ("quality_var",           "quality",            "str",   "best1080"),
        ("replace_var",           "replace",            "bool",  False),
        ("auto_sync_var",         "auto_sync",          "bool",  True),
        ("official_360_var",      "official_360",       "bool",  True),
        ("sync_only_var",         "sync_only",          "bool",  False),
        ("min_conf_var",          "min_conf",           "str",   "0.30"),
        ("workers_var",           "workers",            "str",   "4"),
        ("sleep_interval_var",    "sleep_interval",     "str",   "0.0"),
        ("max_sleep_var",         "max_sleep_interval", "str",   "0.0"),
        ("limit_rate_var",        "limit_rate",         "str",   ""),
        ("manual_map_var",        "manual_map",         "str",   ""),
        ("only_list_var",         "only_list",          "str",   ""),
    ]

    def init_vars(self):
        for attr, key, kind, default in self.SETTING_SPECS:
            raw = self.config.get(key, default)
            if kind == "bool":
                setattr(self, attr, tk.BooleanVar(value=bool(raw)))
            else:
                setattr(self, attr, tk.StringVar(value="" if raw is None else str(raw)))

    def build_ui(self):
        # The table styling already comes from theme.apply_theme(); re-setting
        # it here would undo the taller rows.

        main = ttk.Frame(self.root, padding=theme.PAD_WINDOW)
        main.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(0, weight=1)

        row = 0
        row = self.build_song_panel(main, row)

        self.override_section = Collapsible(main, "Override this song",
                                            subtitle="click a song first")
        self.override_section.grid(row=row, column=0, sticky=(tk.W, tk.E),
                                   pady=(theme.PAD_SECTION, 0))
        row += 1
        self.build_manual_panel(self.override_section.body, 0)



        # ---------------- Run controls ----------------
        actions = ttk.Frame(main)
        actions.grid(row=row, column=0,
                     pady=(theme.PAD_SECTION + 4, theme.PAD_SECTION))
        row += 1
        self.options_open = False
        self.options_toggle = ttk.Button(actions, style="Inline.TButton",
                                         text="▸  Run options",
                                         command=self.toggle_run_options)
        self.options_toggle.pack(side=tk.LEFT, padx=(0, 16))
        ToolTip(self.options_toggle,
                "Replace, Auto-sync, 360p fallback and Sync-only. "
                "Changes apply straight away.")

        self.run_hint = ttk.Label(actions, text="", foreground=theme.FG_DIM)
        self.run_hint.pack(side=tk.LEFT, padx=(0, 12))
        self.run_button = ttk.Button(actions, text="▶  Run", style="Accent.TButton",
                                     command=self.run_script, width=14)
        self.run_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(actions, text="⏹  Stop", command=self.stop_script,
                                      state=tk.DISABLED, width=12)
        self.stop_button.pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Clear output", command=self.clear_output,
                   width=14).pack(side=tk.LEFT)
        self.review_button = ttk.Button(actions, text="Review unsure",
                                        command=self.start_review,
                                        state=tk.DISABLED, width=18)
        self.review_button.pack(side=tk.LEFT, padx=6)
        ToolTip(self.review_button,
                "After a run, step through the songs it was unsure about and "
                "pick the right video for each. The options are already "
                "loaded, so there is no waiting.")

        ToolTip(self.run_button, "Runs on the songs you have checked in the list.")

        # The switches drop in underneath the Run row when opened.
        self.options_body = ttk.Frame(main, padding=(0, 2, 0, 10))
        self.options_row = row
        row += 1
        self.build_run_options(self.options_body)

        # ---------------- Output ----------------
        outhead = ttk.Frame(main)
        outhead.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 6))
        outhead.columnconfigure(1, weight=1)
        ttk.Label(outhead, text="Output", font=theme.FONT_BOLD).grid(
            row=0, column=0, sticky=tk.W)
        # One line that keeps being rewritten while a run is going, so the log
        # below stays short.
        self.status_line = ttk.Label(outhead, text="", foreground=theme.FG_DIM,
                                     anchor=tk.W)
        self.status_line.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=14)
        row += 1
        self.output_text = scrolledtext.ScrolledText(
            main, height=11, wrap=tk.WORD, state=tk.DISABLED)
        self.output_text.grid(row=row, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        # ScrolledText builds a classic tk.Scrollbar that ttk styling misses.
        theme.style_tk_scrollbar(self.output_text.vbar)
        self.output_text.configure(background=theme.PANEL, foreground=theme.FG,
                                   insertbackground=theme.FG,
                                   relief=tk.FLAT, borderwidth=0,
                                   font=("Menlo", 11))
        self.output_text.tag_config("timestamp", foreground=theme.FG_DIM)
        self.output_text.tag_config("error", foreground=theme.TEXT_BAD)
        self.output_text.tag_config("success", foreground=theme.TEXT_VID)
        main.rowconfigure(row, weight=1)

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    def open_settings(self):
        if getattr(self, "_settings_win", None) and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return

        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("Settings")
        win.transient(self.root)
        win.resizable(False, False)

        # Shadow copies. The real vars are untouched until Apply.
        draft = {}
        for attr, key, kind, default in self.SETTING_SPECS:
            live = getattr(self, attr)
            draft[attr] = (tk.BooleanVar(value=live.get()) if kind == "bool"
                           else tk.StringVar(value=live.get()))

        box = ttk.Frame(win, padding=16)
        box.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        box.columnconfigure(1, weight=1)
        r = 0

        def browse_dir(var):
            d = filedialog.askdirectory(title="Select your Clone Hero Songs folder",
                                        initialdir=var.get() or str(Path.home()),
                                        parent=win)
            if d:
                var.set(d)

        def browse_file(var, title):
            f = filedialog.askopenfilename(title=title, parent=win,
                                           filetypes=[("Text files", "*.txt"),
                                                      ("All files", "*.*")])
            if f:
                var.set(f)

        ttk.Label(box, text="Songs Folder:").grid(row=r, column=0, sticky=tk.W, pady=4)
        ttk.Entry(box, textvariable=draft["songs_folder_var"], width=52).grid(
            row=r, column=1, sticky=(tk.W, tk.E), padx=6, pady=4)
        ttk.Button(box, text="Browse…", width=11,
                   command=lambda: browse_dir(draft["songs_folder_var"])).grid(row=r, column=2)
        r += 1

        ttk.Label(box, text="Quality:").grid(row=r, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(box, textvariable=draft["quality_var"], state="readonly", width=18,
                     values=["best1080", "best720", "best1440", "best2160", "best"]).grid(
            row=r, column=1, sticky=tk.W, padx=6, pady=4)
        r += 1

        # The four run switches live next to Run on the main window now.

        ttk.Separator(box, orient=tk.HORIZONTAL).grid(
            row=r, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10); r += 1
        ttk.Label(box, text="Advanced", font=theme.FONT_BOLD).grid(
            row=r, column=0, sticky=tk.W); r += 1
        adv = ttk.Frame(box)
        adv.grid(row=r, column=0, columnspan=3, sticky=tk.W, pady=2); r += 1
        for i, (label, attr) in enumerate([
                ("Min autosync conf:", "min_conf_var"),
                ("Workers:", "workers_var"),
                ("Sleep interval (s):", "sleep_interval_var"),
                ("Max sleep (s):", "max_sleep_var"),
                ("Limit rate:", "limit_rate_var")]):
            rr, cc = divmod(i, 2)
            ttk.Label(adv, text=label).grid(row=rr, column=cc*2, sticky=tk.W,
                                            padx=(0, 6), pady=3)
            ttk.Entry(adv, textvariable=draft[attr], width=12).grid(
                row=rr, column=cc*2+1, sticky=tk.W, padx=(0, 26), pady=3)

        ttk.Separator(box, orient=tk.HORIZONTAL).grid(
            row=r, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10); r += 1
        ttk.Label(box, text="File Options", font=theme.FONT_BOLD).grid(
            row=r, column=0, sticky=tk.W); r += 1
        for label, attr, title in [
                ("Manual map file:", "manual_map_var", "Select a manual map file"),
                ("Only-list file:", "only_list_var", "Select an only-list file")]:
            ttk.Label(box, text=label).grid(row=r, column=0, sticky=tk.W, pady=4)
            ttk.Entry(box, textvariable=draft[attr]).grid(
                row=r, column=1, sticky=(tk.W, tk.E), padx=6, pady=4)
            ttk.Button(box, text="Browse…", width=11,
                       command=lambda v=draft[attr], t=title: browse_file(v, t)).grid(
                row=r, column=2)
            r += 1

        # --- buttons ---
        ttk.Separator(box, orient=tk.HORIZONTAL).grid(
            row=r, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(12, 10)); r += 1
        btns = ttk.Frame(box)
        btns.grid(row=r, column=0, columnspan=3, sticky=(tk.W, tk.E))
        ttk.Label(btns, text="Changes are discarded unless you press Apply.",
                  foreground=theme.FG_DIM).pack(side=tk.LEFT)

        def cancel():
            win.grab_release()
            win.destroy()
            self._settings_win = None

        def apply():
            errors = []
            for label, attr, caster in [("Min autosync conf", "min_conf_var", float),
                                        ("Workers", "workers_var", int),
                                        ("Sleep interval", "sleep_interval_var", float),
                                        ("Max sleep", "max_sleep_var", float)]:
                val = draft[attr].get().strip()
                if not val:
                    continue
                try:
                    caster(val)
                except ValueError:
                    errors.append(f"{label}: {val!r} is not a number")
            folder = draft["songs_folder_var"].get().strip()
            if folder and not Path(folder).expanduser().is_dir():
                errors.append(f"Songs Folder does not exist:\n{folder}")
            for label, attr in [("Manual map file", "manual_map_var"),
                                ("Only-list file", "only_list_var")]:
                v = draft[attr].get().strip()
                if v and not Path(v).expanduser().is_file():
                    errors.append(f"{label} not found:\n{v}")
            if errors:
                messagebox.showerror("Settings not applied",
                                     "\n\n".join(errors), parent=win)
                return

            old_folder = self.songs_folder_var.get().strip()
            for attr, *_ in self.SETTING_SPECS:
                getattr(self, attr).set(draft[attr].get())
            self.save_config()
            self.log_output("✓ Settings applied", "success")
            cancel()
            if self.songs_folder_var.get().strip() != old_folder:
                self.scan_songs()

        ttk.Button(btns, text="Apply", command=apply, width=12).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=cancel, width=12).pack(side=tk.RIGHT, padx=6)

        # Tooltips for the dialog's own widgets
        def walk(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                except Exception:
                    t = None
                if t and t in TOOLTIPS:
                    ToolTip(c, TOOLTIPS[t])
                walk(c)
        walk(win)

        win.protocol("WM_DELETE_WINDOW", cancel)
        win.bind("<Escape>", lambda _e: cancel())
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + 60
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.grab_set()

    def ui_call(self, fn):
        """Run fn on the Tk thread, ignoring it if the window has gone away."""
        try:
            if self.root.winfo_exists():
                self.root.after(0, fn)
        except Exception:
            pass          # interpreter or window already tearing down

    def toggle_run_options(self):
        self.options_open = not self.options_open
        if self.options_open:
            self.options_body.grid(row=self.options_row, column=0,
                                   sticky=(tk.W, tk.E))
            self.options_toggle.config(text="▾  Run options")
        else:
            self.options_body.grid_remove()
            self.options_toggle.config(text="▸  Run options")

    def build_run_options(self, box):
        """The four run switches, next to Run instead of buried in Settings."""
        opts = [
            ("Replace existing videos", "replace_var",
             "Re-download videos for charts that already have one. Without "
             "this, charts with a video.mp4 are skipped."),
            ("Auto-sync", "auto_sync_var",
             "Work out where the video should start and write "
             "video_start_time into song.ini."),
            ("Allow official 360p fallback", "official_360_var",
             "Accept a 360p video rather than rejecting it."),
            ("Sync-only (no download)", "sync_only_var",
             "Do not download anything. Just recalculate the timing for "
             "charts that already have a video. Needs no internet."),
        ]
        inner = ttk.Frame(box)
        inner.pack()                      # centres the switches under the row
        for i, (text, attr, tip) in enumerate(opts):
            cb = ttk.Checkbutton(inner, text=text, variable=getattr(self, attr),
                                 command=self.save_config)
            cb.pack(side=tk.LEFT, padx=(0, 22))
            ToolTip(cb, tip)

    def build_manual_panel(self, parent, row):
        box = parent
        box.columnconfigure(1, weight=1)
        row += 1

        self.manual_target = ttk.Label(
            box, text="Click a song in the list above to override it.",
            foreground=theme.FG_DIM)
        self.manual_target.grid(row=0, column=0, columnspan=3, sticky=tk.W,
                                pady=(0, 12))

        # Each row is: label | stretchy entry | tight group of buttons.
        # Putting the buttons in their own frame keeps them together instead of
        # drifting apart across separate stretchy grid columns.

        # --- YouTube URL ---
        ttk.Label(box, text="YouTube URL:").grid(row=1, column=0, sticky=tk.W,
                                                 pady=5, padx=(0, 10))
        self.manual_url_var = tk.StringVar()
        self.manual_url_entry = ttk.Entry(box, textvariable=self.manual_url_var,
                                          state=tk.DISABLED)
        self.manual_url_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5)

        url_btns = ttk.Frame(box)
        url_btns.grid(row=1, column=2, sticky=tk.E, padx=(12, 0), pady=5)
        self.apply_url_button = ttk.Button(url_btns, text="Save URL",
                                           command=self.apply_manual_url,
                                           state=tk.DISABLED, width=13)
        self.apply_url_button.pack(side=tk.LEFT)
        self.download_button = ttk.Button(url_btns, text="Download this",
                                          command=self.download_video,
                                          state=tk.DISABLED, width=15)
        self.download_button.pack(side=tk.LEFT, padx=(6, 0))

        # --- Video timing ---
        ttk.Label(box, text="Video timing (ms):").grid(row=2, column=0, sticky=tk.W,
                                                       pady=5, padx=(0, 10))
        self.video_timing_var = tk.StringVar()
        self.video_timing_entry = ttk.Entry(box, textvariable=self.video_timing_var,
                                            state=tk.DISABLED)
        self.video_timing_entry.grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5)

        time_btns = ttk.Frame(box)
        time_btns.grid(row=2, column=2, sticky=tk.E, padx=(12, 0), pady=5)
        self.set_timing_button = ttk.Button(time_btns, text="Save timing",
                                            command=self.apply_video_timing,
                                            state=tk.DISABLED, width=13)
        self.set_timing_button.pack(side=tk.LEFT)
        self.sync_button = ttk.Button(time_btns, text="Re-sync this",
                                      command=self.sync_audio,
                                      state=tk.DISABLED, width=15)
        self.sync_button.pack(side=tk.LEFT, padx=(6, 0))

        self.choose_button = ttk.Button(box, text="🎬  Choose video…",
                                        command=self.choose_video,
                                        state=tk.DISABLED, width=30)
        self.choose_button.grid(row=3, column=2, sticky=tk.E,
                                padx=(12, 0), pady=(8, 0))
        ToolTip(self.choose_button,
                "Search YouTube for this song and show every candidate with a "
                "preview, so you can watch and pick the right one yourself.")

        hint = ttk.Label(box, foreground=theme.FG_DIM, font=theme.FONT_SMALL,
                         text="Positive timing skips into the video (the video has an "
                              "intro). Negative delays it (the chart has a lead-in).")
        hint.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(12, 0))
        return row

    def on_tree_select(self, _event=None):
        """The manual panel always reflects whichever single row is highlighted."""
        sel = self.tree.selection()
        chart = self.item_to_chart.get(sel[0]) if len(sel) == 1 else None
        self.active_chart = chart

        widgets = (self.manual_url_entry, self.video_timing_entry,
                   self.apply_url_button, self.set_timing_button,
                   self.download_button, self.sync_button, self.choose_button)

        if chart is None:
            for w in widgets:
                w.config(state=tk.DISABLED)
            self.manual_url_var.set("")
            self.video_timing_var.set("")
            self.manual_target.config(
                text=("Select exactly one song to override it."
                      if len(sel) > 1 else
                      "Click a song in the list above to override it."),
                foreground=theme.FG_DIM)
            if hasattr(self, "override_section"):
                self.override_section.set_subtitle(
                    "select one song" if len(sel) > 1 else "click a song first")
            return

        for w in widgets:
            w.config(state=tk.NORMAL)
        # Re-syncing needs a video that actually has an audio track.
        if not chart.has_video:
            self.sync_button.config(state=tk.DISABLED)
        elif not chart.has_audio:
            self.sync_button.config(state=tk.DISABLED)

        meta = parse_song_ini(chart.path / "song.ini")
        self.manual_url_var.set(meta.get("video_url", ""))
        self.video_timing_var.set("" if chart.offset_ms is None else str(chart.offset_ms))

        note = chart.status
        if chart.has_video and not chart.has_audio:
            note += " · cannot re-sync, video has no audio"
        self.manual_target.config(text=f"#{chart.num}   {chart.label}   —   {note}",
                                  foreground=theme.FG)
        if hasattr(self, "override_section"):
            self.override_section.set_subtitle(f"#{chart.num}  {chart.label[:40]}")

    def _require_active(self):
        if not getattr(self, "active_chart", None):
            messagebox.showinfo("No song selected",
                                "Click a single song in the list above first.")
            return None
        return self.active_chart

    def apply_manual_url(self):
        chart = self._require_active()
        if not chart:
            return
        url = self.manual_url_var.get().strip()
        try:
            if url:
                update_song_ini(chart.path / "song.ini", {"video_url": url})
                self.log_output(f"✓ #{chart.num} {chart.label}: video_url saved", "success")
            else:
                update_song_ini(chart.path / "song.ini", {"video_url": ""})
                self.log_output(f"✓ #{chart.num} {chart.label}: video_url cleared", "success")
        except Exception as e:
            self.log_output(f"✗ Could not write song.ini: {e}", "error")
            messagebox.showerror("Error", f"Could not write song.ini:\n{e}")

    def apply_video_timing(self):
        chart = self._require_active()
        if not chart:
            return
        raw = self.video_timing_var.get().strip()
        if not raw:
            messagebox.showwarning("No timing", "Enter a value in milliseconds, e.g. -2500.")
            return
        try:
            ms = int(float(raw))
        except ValueError:
            messagebox.showerror("Invalid timing",
                                 f"{raw!r} is not a number of milliseconds.")
            return
        try:
            update_song_ini(chart.path / "song.ini", {"video_start_time": str(ms)})
        except Exception as e:
            self.log_output(f"✗ Could not write song.ini: {e}", "error")
            messagebox.showerror("Error", f"Could not write song.ini:\n{e}")
            return
        chart.offset_ms = ms
        chart.synced = bool(chart.has_video and chart.video_ok)
        self.render_rows()
        self.log_output(
            f"✓ #{chart.num} {chart.label}: video_start_time = {ms} "
            f"({'skip into video' if ms > 0 else 'delay video' if ms < 0 else 'aligned'})",
            "success")

    # --- single-song runs ---

    def single_song_cmd(self, chart, extra):
        """Command for one chart, reusing the main settings."""
        script = Path(__file__).resolve().parent / "VideoDownload.py"
        sel = Path(__file__).resolve().parent / "_single_song.txt"
        sel.write_text(chart.rel + "\n", encoding="utf-8")
        cmd = [sys.executable, str(script), self.songs_folder_var.get(),
               "--only-list", str(sel), "--quality", self.quality_var.get(),
               "--workers", "1"]
        if self.official_360_var.get():
            cmd.append("--official-360")
        try:
            cmd += ["--min-conf", str(float(self.min_conf_var.get()))]
        except ValueError:
            pass
        if self.limit_rate_var.get().strip():
            cmd += ["--limit-rate", self.limit_rate_var.get().strip()]
        return cmd + extra

    def run_single(self, chart, extra, banner):
        if self.is_running:
            messagebox.showinfo("Busy", "Something is already running. Stop it first.")
            return
        try:
            cmd = self.single_song_cmd(chart, extra)
        except Exception as e:
            messagebox.showerror("Error", f"Could not build the command:\n{e}")
            return
        self.clear_output()
        self.log_output("═" * 80)
        self.log_output(banner)
        self.log_output("═" * 80)
        env = os.environ.copy()
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, bufsize=1, universal_newlines=True)
        except Exception as e:
            self.log_output(f"✗ Failed to start: {e}", "error")
            messagebox.showerror("Error", f"Failed to start:\n{e}")
            return
        self.is_running = True
        self.run_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        threading.Thread(target=self.read_output, daemon=True).start()

    # ------------------------------------------------------------------
    # Video picker
    # ------------------------------------------------------------------

    def start_review(self):
        """Step through the charts the last run was unsure about."""
        if self.is_running:
            messagebox.showinfo("Busy", "Something is already running.")
            return
        if not self.review_queue:
            messagebox.showinfo("Nothing to review",
                                "The last run was confident about everything.")
            return
        self._review_index = 0
        self.review_picks = []
        self.log_output("")
        self.log_output(f"Going through {len(self.review_queue)} unsure "
                        f"song{'s' if len(self.review_queue) != 1 else ''}. "
                        f"Nothing downloads until you have been through them all.",
                        "success")
        self._show_next_review()

    def _show_next_review(self):
        while self._review_index < len(self.review_queue):
            payload = self.review_queue[self._review_index]
            self._review_index += 1
            chart = self._chart_for(payload)
            if chart is None or not payload.get("candidates"):
                continue
            remaining = len(self.review_queue) - self._review_index
            self.open_picker(chart, payload,
                             queue_note=f"{self._review_index} of "
                                        f"{len(self.review_queue)}",
                             on_skip=self._show_next_review)
            return
        self._finish_review()

    def _finish_review(self):
        picks = self.review_picks
        self.review_queue = []
        self.review_button.config(state=tk.DISABLED, text="Review unsure")
        if not picks:
            self.log_output("Finished reviewing. You did not choose anything.")
            self.scan_songs()
            return

        names = "\n".join(f"   • {c.label[:46]} → {t[:44]}" for c, t in picks[:12])
        if len(picks) > 12:
            names += f"\n   … and {len(picks) - 12} more"
        go = messagebox.askyesno(
            "Download your picks?",
            f"You chose {len(picks)} video{'s' if len(picks) != 1 else ''}:\n\n"
            f"{names}\n\nDownload them all now?")
        if not go:
            self.log_output(f"Saved {len(picks)} choice(s) to song.ini. "
                            f"Check those songs and press Run when ready.")
            self.scan_songs()
            return

        extra = ["--replace"]
        if self.auto_sync_var.get():
            extra.append("--auto-sync")
        self.run_batch([c for c, _ in picks], extra,
                       f"Downloading your {len(picks)} chosen video"
                       f"{'s' if len(picks) != 1 else ''}")

    def run_batch(self, charts, extra, banner):
        """Run the downloader over a specific set of charts."""
        if self.is_running:
            messagebox.showinfo("Busy", "Something is already running.")
            return
        sel = Path(__file__).resolve().parent / "_review_picks.txt"
        sel.write_text("\n".join(c.rel for c in charts) + "\n", encoding="utf-8")
        script = Path(__file__).resolve().parent / "VideoDownload.py"
        cmd = [sys.executable, str(script), self.songs_folder_var.get(),
               "--only-list", str(sel), "--quality", self.quality_var.get(),
               "--workers", self.workers_var.get() or "2"]
        if self.official_360_var.get():
            cmd.append("--official-360")
        try:
            cmd += ["--min-conf", str(float(self.min_conf_var.get()))]
        except ValueError:
            pass
        if self.limit_rate_var.get().strip():
            cmd += ["--limit-rate", self.limit_rate_var.get().strip()]
        cmd += extra

        self.review_queue = []
        self.log_output("")
        self.log_output("═" * 80)
        self.log_output(banner)
        self.log_output("═" * 80)
        env = os.environ.copy()
        try:
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, bufsize=1, universal_newlines=True)
        except Exception as e:
            self.log_output(f"✗ Failed to start: {e}", "error")
            messagebox.showerror("Error", f"Failed to start:\n{e}")
            return
        self.is_running = True
        self.run_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        threading.Thread(target=self.read_output, daemon=True).start()

    def _chart_for(self, payload):
        """Match a @REVIEW payload back to a scanned chart."""
        want = Path(payload.get("rel", ""))
        for c in self.charts:
            if c.path == want or c.path.name == want.name:
                return c
        return None

    def ytdlp_extra_args(self):
        """The YouTube flags the rest of the tool uses, for direct yt-dlp calls."""
        here = Path(__file__).resolve().parent
        args = []
        cookies = here / "cookies.txt"
        if cookies.is_file():
            args += ["--cookies", str(cookies)]
        for runtime in ("deno", "node"):
            if shutil.which(runtime):
                args += ["--js-runtimes", runtime]
                break
        args += ["--remote-components", "ejs:github"]
        return args

    def choose_video(self):
        chart = self._require_active()
        if not chart or self.is_running:
            if self.is_running:
                messagebox.showinfo("Busy", "Something is already running. Stop it first.")
            return
        self.choose_button.config(state=tk.DISABLED, text="Searching…")

        def work():
            sel = Path(__file__).resolve().parent / "_picker.txt"
            sel.write_text(chart.rel + "\n", encoding="utf-8")
            cmd = [sys.executable, str(Path(__file__).resolve().parent / "VideoDownload.py"),
                   self.songs_folder_var.get(), "--only-list", str(sel),
                   "--print-candidates", "--workers", "1", "--search-results", "12"]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True,
                                     timeout=180).stdout
            except Exception as e:
                self.ui_call(lambda: self._picker_failed(str(e)))
                return
            payload = None
            for line in out.splitlines():
                if line.startswith("CANDIDATES "):
                    try:
                        payload = _json.loads(line[len("CANDIDATES "):])
                    except ValueError:
                        pass
            self.ui_call(lambda: self._picker_ready(chart, payload))

        threading.Thread(target=work, daemon=True).start()

    def _picker_failed(self, msg):
        self.choose_button.config(state=tk.NORMAL, text="🎬  Choose video…")
        self.log_output(f"✗ Search failed: {msg}", "error")
        messagebox.showerror("Search failed", msg)

    def _picker_ready(self, chart, payload):
        self.choose_button.config(state=tk.NORMAL, text="🎬  Choose video…")
        if not payload or not payload.get("candidates"):
            messagebox.showinfo(
                "Nothing found",
                "No candidates passed the basic filters for this song.\n\n"
                "You can still paste a YouTube URL by hand.")
            return
        self.open_picker(chart, payload)

    def open_picker(self, chart, payload, queue_note=None, on_skip=None):
        cands = payload["candidates"]
        win = tk.Toplevel(self.root)
        win.title(f"Choose a video — {chart.label}")
        win.transient(self.root)
        self._thumb_cache = {}
        self._thumb_current = None

        outer = ttk.Frame(win, padding=14)
        outer.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        clen = payload.get("chart_len")
        heading = f"#{chart.num}  {chart.label}"
        if queue_note:
            heading = f"({queue_note})   " + heading
        ttk.Label(outer, font=theme.FONT_BOLD, text=heading).grid(
            row=0, column=0, sticky=tk.W)
        ttk.Label(outer, foreground=theme.FG_DIM,
                  text=(f"Chart is {clen:.0f}s long. A good match is close to that "
                        f"length. Pick one and press Download."
                        if clen else "Pick one and press Download.")
                  ).grid(row=0, column=1, sticky=tk.W, padx=12)

        body = ttk.Frame(outer)
        body.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(10, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        cols = ("score", "title", "channel", "len", "views")
        tree = ttk.Treeview(body, columns=cols, show="headings", height=12)
        for key, title, w, anchor in [("score","Score",60,tk.CENTER),
                                      ("title","Title",380,tk.W),
                                      ("channel","Channel",170,tk.W),
                                      ("len","Length",70,tk.CENTER),
                                      ("views","Views",90,tk.E)]:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=w, anchor=anchor, stretch=(key == "title"))
        vs = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        vs.grid(row=0, column=1, sticky=(tk.N, tk.S))

        side = ttk.Frame(body, padding=(14, 0, 0, 0))
        side.grid(row=0, column=2, sticky=(tk.N, tk.S))
        # Fixed-size holder so the panel does not jump about as frames load.
        PREV_W, PREV_H = 320, 180
        holder = tk.Frame(side, width=PREV_W, height=PREV_H,
                          relief=tk.SOLID, borderwidth=1, background=theme.PANEL)
        holder.pack()
        holder.pack_propagate(False)
        thumb = tk.Label(holder, text="\n\nselect a video\n\n",
                         background=theme.PANEL)
        thumb.pack(expand=True)

        # Scrub bar across the whole runtime of the selected video
        scrub = ttk.Scale(side, from_=0, to=1, orient=tk.HORIZONTAL)
        scrub.pack(fill=tk.X, pady=(6, 0))
        transport = ttk.Frame(side)
        transport.pack(fill=tk.X, pady=(4, 0))
        play_btn = ttk.Button(transport, text="▶ Play", width=9, state=tk.DISABLED)
        play_btn.pack(side=tk.LEFT)
        pos_lbl = ttk.Label(transport, text="", foreground=theme.FG_DIM)
        pos_lbl.pack(side=tk.LEFT, padx=8)
        watch = ttk.Button(transport, text="Open on YouTube", state=tk.DISABLED,
                           width=18)
        watch.pack(side=tk.RIGHT)

        detail = tk.Message(side, width=300, justify=tk.LEFT, text="", font=theme.FONT_SMALL)
        detail.pack(anchor=tk.W, pady=(8, 0))

        for c in cands:
            mins, secs = divmod(int(c["duration"] or 0), 60)
            delta = ""
            if clen and c["duration"]:
                d = abs(c["duration"] - clen)
                delta = "  ✓" if d <= 5 else ("  ~" if d <= 20 else "  ✗")
            tree.insert("", tk.END, values=(
                f"{c['score']:.0f}", c["title"], c["channel"],
                f"{mins}:{secs:02d}{delta}", f"{c['views']:,}" if c["views"] else "—"))

        def selected():
            sel = tree.selection()
            return cands[tree.index(sel[0])] if sel else None

        state = {"frames": [], "imgs": [], "i": 0, "playing": False,
                 "vid": None, "after": None, "duration": 0}

        def stop_play():
            state["playing"] = False
            if state["after"]:
                try:
                    win.after_cancel(state["after"])
                except Exception:
                    pass
                state["after"] = None
            play_btn.config(text="▶ Play")

        def show_frame(i):
            if not state["imgs"]:
                return
            i = max(0, min(i, len(state["imgs"]) - 1))
            state["i"] = i
            # width/height must go back to 0 or Tk clips the image to the
            # character-based size used by the placeholder text.
            thumb.config(image=state["imgs"][i], text="", width=0, height=0)
            if state["duration"]:
                at = state["duration"] * i / max(1, len(state["imgs"]) - 1)
                pos_lbl.config(text=f"{int(at)//60}:{int(at)%60:02d} "
                                    f"/ {state['duration']//60}:{state['duration']%60:02d}")
            else:
                pos_lbl.config(text=f"{i+1}/{len(state['imgs'])}")

        def on_scrub(v):
            if state["imgs"]:
                stop_play()
                show_frame(int(float(v)))

        def tick():
            if not state["playing"] or not win.winfo_exists():
                return
            nxt = (state["i"] + 1) % len(state["imgs"])
            show_frame(nxt)
            scrub.set(nxt)
            state["after"] = win.after(120, tick)      # about 8 frames a second

        def toggle_play():
            if not state["imgs"]:
                return
            if state["playing"]:
                stop_play()
            else:
                state["playing"] = True
                play_btn.config(text="⏸ Pause")
                tick()

        play_btn.config(command=toggle_play)
        scrub.config(command=on_scrub)

        def load_preview(vid, url, duration):
            """Fetch YouTube's own storyboard frames for the whole video."""
            try:
                frames = preview.fetch_frames(url, self.ytdlp_extra_args(),
                                              out_height=176, limit=80)
            except Exception:
                frames = []
            self.ui_call(lambda: show_preview(vid, frames, duration))

        def show_preview(vid, frames, duration):
            if not win.winfo_exists() or state["vid"] != vid:
                return
            if not frames:
                thumb.config(image="", text="\n\nno preview available\n"
                                            "use Open on YouTube\n\n",
                             width=0, height=0)
                return
            state["frames"] = frames
            state["imgs"] = [tk.PhotoImage(data=f) for f in frames]  # keep refs
            state["duration"] = int(duration or 0)
            scrub.config(to=max(1, len(state["imgs"]) - 1))
            scrub.set(0)
            show_frame(0)
            play_btn.config(state=tk.NORMAL)
            toggle_play()                       # start playing straight away

        def on_select(_=None):
            c = selected()
            if not c:
                return
            stop_play()
            state.update(frames=[], imgs=[], i=0, vid=c["id"], duration=0)
            play_btn.config(state=tk.DISABLED)
            pos_lbl.config(text="")
            thumb.config(image="", text="\n\nloading preview…\n\n",
                         width=0, height=0)
            detail.config(text=f"{c['title']}\n\n{c['channel']}\n\n"
                               + "\n".join(f"• {r}" for r in c.get("reasons", [])[:7]))
            watch.config(state=tk.NORMAL,
                         command=lambda u=c["url"]: webbrowser.open(u))
            threading.Thread(target=load_preview,
                             args=(c["id"], c["url"], c.get("duration", 0)),
                             daemon=True).start()

        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-1>", lambda _e: use_it(download=not on_skip))

        bar = ttk.Frame(outer)
        bar.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(12, 0))
        ttk.Label(bar, foreground=theme.FG_DIM,
                  text=("Double-click a row to choose it and move on."
                        if on_skip else
                        "Double-click a row to download it.")).pack(side=tk.LEFT)

        def close(advance=False):
            stop_play()
            win.grab_release(); win.destroy()
            if advance and on_skip:
                self.root.after(60, on_skip)

        def use_it(download):
            c = selected()
            if not c:
                messagebox.showinfo("Nothing selected",
                                    "Pick a video from the list first.", parent=win)
                return
            update_song_ini(chart.path / "song.ini", {"video_url": c["url"]})
            self.manual_url_var.set(c["url"])

            if on_skip:
                # Reviewing: remember the choice and move to the next song.
                # Nothing downloads until you have been through them all.
                self.review_picks.append((chart, c["title"]))
                self.log_output(f"   chose for #{chart.num} {chart.label}: "
                                f"{c['title'][:54]}")
                close(advance=True)
                return

            self.log_output(f"✓ #{chart.num} {chart.label}: using {c['title'][:52]}",
                            "success")
            close(advance=False)
            if download:
                extra = ["--replace"]
                if self.auto_sync_var.get():
                    extra.append("--auto-sync")
                self.run_single(chart, extra,
                                f"Downloading your pick for #{chart.num} {chart.label}\n"
                                f"{c['title']}")

        if on_skip:
            # ttk cannot paint button backgrounds under the macOS aqua theme,
            # so the two decision buttons are classic tk widgets tinted with
            # highlightbackground, which aqua does honour.
            ttk.Button(bar, text="Use this one  →", width=17,
                       style="Success.TButton",
                       command=lambda: use_it(False)).pack(side=tk.RIGHT)
            ttk.Button(bar, text="Skip this one  →", width=17,
                       style="Warn.TButton",
                       command=lambda: close(advance=True)).pack(side=tk.RIGHT, padx=8)
            ttk.Button(bar, text="Stop reviewing", width=15,
                       command=lambda: close(advance=False)).pack(side=tk.RIGHT)
        else:
            ttk.Button(bar, text="Download this one", width=20,
                       command=lambda: use_it(True)).pack(side=tk.RIGHT)
            ttk.Button(bar, text="Just save the URL", width=18,
                       command=lambda: use_it(False)).pack(side=tk.RIGHT, padx=6)
            ttk.Button(bar, text="Cancel", width=11,
                       command=lambda: close(advance=False)).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", lambda: close(advance=False))
        win.bind("<Escape>", lambda _e: close(advance=False))
        if tree.get_children():
            tree.selection_set(tree.get_children()[0])
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx()+40}+{self.root.winfo_rooty()+50}")
        win.grab_set()
        self._picker_win = win

    def download_video(self):
        chart = self._require_active()
        if not chart:
            return
        url = self.manual_url_var.get().strip()
        if url:
            update_song_ini(chart.path / "song.ini", {"video_url": url})
        extra = ["--replace"]
        if self.auto_sync_var.get():
            extra.append("--auto-sync")
        self.run_single(chart, extra,
                        f"Downloading #{chart.num} {chart.label}"
                        + (f"\nUsing your URL: {url}" if url else
                           "\nSearching YouTube for the best match"))

    def sync_audio(self):
        chart = self._require_active()
        if not chart:
            return
        if not (chart.path / "video.mp4").exists():
            messagebox.showerror("No video",
                                 "This song has no video.mp4 yet. Download it first.")
            return
        self.run_single(chart, ["--sync-only"],
                        f"Re-syncing #{chart.num} {chart.label}")

    # ------------------------------------------------------------------
    # Song picker
    # ------------------------------------------------------------------

    # Row colours. "Outline" is the text colour, "highlight" is the row fill,
    # so a chart can show both at once (e.g. a synced video whose song.ini is
    # broken shows a green fill with red text).
    C_FILL_OK   = theme.FILL_OK     # synced and ready
    C_FILL_SOFT = theme.FILL_SOFT   # plays fine, just low resolution
    C_FILL_BAD  = theme.FILL_BAD    # broken: won't play or can't be synced
    C_TEXT_VID  = theme.TEXT_VID    # has a video file
    C_TEXT_BAD  = theme.TEXT_BAD    # something wrong with song.ini
    C_TEXT_PLAIN = theme.TEXT_PLAIN

    def build_song_panel(self, parent, row):
        head = ttk.Frame(parent)
        head.grid(row=row, column=0, sticky=(tk.W, tk.E),
                  pady=(0, theme.PAD_SECTION)); row += 1
        ttk.Label(head, text="Songs", font=theme.FONT_H1).pack(side=tk.LEFT)
        self.scan_status = ttk.Label(head, text="", foreground=theme.FG_DIM)
        self.scan_status.pack(side=tk.LEFT, padx=(14, 0))
        settings_btn = ttk.Button(head, text="⚙  Settings…",
                                  command=self.open_settings, width=14)
        settings_btn.pack(side=tk.RIGHT)
        ToolTip(settings_btn, "Songs folder, quality, download options and file "
                              "options. Changes only take effect when you press "
                              "Apply.")
        ttk.Button(head, text="⟳ Rescan", command=self.scan_songs,
                   width=11).pack(side=tk.RIGHT, padx=(0, 6))

        sel = ttk.Frame(parent)
        sel.grid(row=row, column=0, sticky=tk.W,
                 pady=(0, theme.PAD_ITEM)); row += 1
        ttk.Label(sel, text="Check:",
                  foreground=theme.FG_DIM).pack(side=tk.LEFT, padx=(0, 10))
        for text, cmd, tip in [
            ("All", lambda: self.set_all_checks(True), "Check every song"),
            ("None", lambda: self.set_all_checks(False), "Uncheck every song"),
            ("Invert", self.invert_checks, "Flip every checkbox"),
            ("Missing video", lambda: self.check_where(lambda c: not c.has_video),
             "Check only charts with no video yet"),
            ("Problems", lambda: self.check_where(
                lambda c: (c.has_video and not c.video_ok) or not c.song_ok),
             "Check only charts that are genuinely broken: the video will not "
             "play, has no audio track, or song.ini is bad"),
            ("Low quality", lambda: self.check_where(
                lambda c: c.has_video and c.video_ok and c.low_quality),
             "Check charts whose video plays fine but is below 700p, if you "
             "want to re-download them sharper"),
            ("Not synced", lambda: self.check_where(
                lambda c: c.has_video and not c.synced),
             "Check charts that have a video but no video_start_time"),
        ]:
            b = ttk.Button(sel, text=text, command=cmd)
            b.pack(side=tk.LEFT, padx=(0, 5))
            ToolTip(b, tip)
        self.sel_count = ttk.Label(sel, text="0 checked", foreground=theme.FG_DIM)
        self.sel_count.pack(side=tk.LEFT, padx=(10, 0))

        wrap = ttk.Frame(parent)
        wrap.grid(row=row, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        wrap.columnconfigure(0, weight=1); wrap.rowconfigure(0, weight=1)

        # "num" is assigned once from the alphabetical scan order, so a song
        # keeps the same number no matter which column you sort by.
        self.columns = [
            ("sel",    "✓",       40,  tk.CENTER, False),
            ("num",    "#",       50,  tk.CENTER, False),
            ("song",   "Song",    400, tk.W,      True),
            ("video",  "Video",   170, tk.CENTER, False),
            ("sync",   "Sync",    110, tk.CENTER, False),
            ("status", "Status",  260, tk.CENTER, False),
        ]
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in self.columns],
                                 show="headings", height=15, selectmode="extended")
        for key, title, width, anchor, stretch in self.columns:
            self.tree.heading(key, text=title, anchor=anchor,
                              command=(lambda k=key: self.sort_by(k)))
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        vs = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        vs.grid(row=0, column=1, sticky=(tk.N, tk.S), padx=(2, 0))

        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<space>", self.on_tree_space)
        self.tree.bind("<Double-1>", self.on_tree_double)
        parent.rowconfigure(row, weight=1)
        row += 1

        # Key sits under the table. No sticky, so grid centres it.
        legend = ttk.Frame(parent)
        legend.grid(row=row, column=0, pady=(theme.PAD_SECTION, 4)); row += 1
        ttk.Label(legend, text="Key:",
                  foreground=theme.FG_DIM).pack(side=tk.LEFT, padx=(0, 8))
        for text, fill, fg in [
            ("synced & ready", self.C_FILL_OK, self.C_TEXT_VID),
            ("low quality", self.C_FILL_SOFT, self.C_TEXT_VID),
            ("has video", theme.ROW, self.C_TEXT_VID),
            ("won't play / can't sync", self.C_FILL_BAD, self.C_TEXT_BAD),
            ("song.ini problem", theme.ROW, self.C_TEXT_BAD),
            ("no video yet", theme.ROW, self.C_TEXT_PLAIN),
        ]:
            tk.Label(legend, text=f" {text} ", background=fill, foreground=fg,
                     relief=tk.SOLID, borderwidth=1,
                     font=theme.FONT_SMALL).pack(side=tk.LEFT, padx=3)
        return row

    # --- sorting ---

    SORT_KEYS = {
        "sel":    lambda c, checked: (c.rel not in checked, c.artist.lower(), c.name.lower()),
        "num":    lambda c, checked: c.num,
        "song":   lambda c, checked: (c.artist.lower(), c.name.lower()),
        "video":  lambda c, checked: (-(c.height or 0), c.codec or "~", not c.has_video),
        "sync":   lambda c, checked: (c.offset_ms is None, c.offset_ms or 0),
        "status": lambda c, checked: (c.status.lower(), c.artist.lower()),
    }

    def sort_by(self, col):
        if not self.charts:
            return
        self.sort_desc = not self.sort_desc if col == self.sort_col else False
        self.sort_col = col
        key = self.SORT_KEYS.get(col, self.SORT_KEYS["song"])
        self.charts.sort(key=lambda c: key(c, self.checked_rels),
                         reverse=self.sort_desc)
        self.render_rows()

    def _heading_labels(self):
        for key, title, _w, anchor, _s in self.columns:
            arrow = ""
            if key == self.sort_col:
                arrow = "  ▼" if self.sort_desc else "  ▲"
            self.tree.heading(key, text=title + arrow, anchor=anchor)

    # --- populating ---

    def _row_tag(self, chart):
        """One tag per (fill, text colour) pair, created on demand."""
        # Red is reserved for a video that will not play or cannot be synced.
        # Low resolution plays perfectly well, so it gets amber, not an error.
        fill = theme.ROW
        if chart.has_video and not chart.video_ok:
            fill = self.C_FILL_BAD
        elif chart.synced and not chart.low_quality:
            fill = self.C_FILL_OK
        elif chart.has_video and chart.low_quality:
            fill = self.C_FILL_SOFT
        fg = self.C_TEXT_PLAIN
        if not chart.song_ok:
            fg = self.C_TEXT_BAD
        elif chart.has_video:
            fg = self.C_TEXT_VID
        tag = f"row{fill}{fg}".replace("#", "")
        self.tree.tag_configure(tag, background=fill, foreground=fg)
        return tag

    def render_rows(self):
        """Redraw the table from self.charts in its current order."""
        self._anchor_item = None
        self.tree.delete(*self.tree.get_children())
        self.item_to_chart.clear()
        for c in self.charts:
            item = self.tree.insert(
                "", tk.END,
                values=("☑" if c.rel in self.checked_rels else "☐",
                        c.num, c.label, c.video_desc, c.sync_desc, c.status),
                tags=(self._row_tag(c),))
            self.item_to_chart[item] = c
        self._heading_labels()
        self.update_sel_count()

    def scan_songs(self):
        folder = self.songs_folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            self.scan_status.config(text="pick a Songs folder in Settings")
            return
        if self.scanning:
            return
        self.scanning = True
        self.scan_status.config(text="scanning…")

        def work():
            try:
                rows = library.scan_library(
                    Path(folder),
                    progress=lambda i, n, lbl: (
                        self.ui_call(lambda: self.scan_status.config(
                            text=f"scanning {i}/{n}…")) or True))
            except Exception as e:
                self.ui_call(lambda: self.scan_status.config(text=f"scan failed: {e}"))
                self.scanning = False
                return
            self.ui_call(lambda: self.populate_tree(rows))

        threading.Thread(target=work, daemon=True).start()

    def populate_tree(self, rows):
        self.scanning = False
        # scan_library returns alphabetical order, so numbering here means a
        # song's number and its alphabetical position always agree.
        for i, c in enumerate(rows, 1):
            c.num = i
        self.charts = rows
        self.checked_rels &= {c.rel for c in rows}      # drop stale ticks
        self.sort_col, self.sort_desc = "num", False
        self.render_rows()

        n_vid = sum(1 for c in rows if c.has_video)
        n_bad = sum(1 for c in rows if (c.has_video and not c.video_ok) or not c.song_ok)
        n_sync = sum(1 for c in rows if c.synced)
        self.scan_status.config(
            text=f"{len(rows)} charts · {n_vid} with video · {n_sync} synced · "
                 f"{n_bad} need attention")

    # --- checkbox handling (tracked by chart path, so sorting cannot lose it) ---

    def set_check(self, item, on):
        chart = self.item_to_chart.get(item)
        if chart is None:
            return
        if on:
            self.checked_rels.add(chart.rel)
        else:
            self.checked_rels.discard(chart.rel)
        vals = list(self.tree.item(item, "values"))
        vals[0] = "☑" if on else "☐"
        self.tree.item(item, values=vals)

    def toggle(self, item):
        chart = self.item_to_chart.get(item)
        if chart:
            self.set_check(item, chart.rel not in self.checked_rels)

    def on_tree_click(self, event):
        """Clicks in the ✓ column tick boxes. Shift-click ticks a whole range."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return          # let normal row highlighting happen elsewhere
        item = self.tree.identify_row(event.y)
        if not item:
            return

        shift_held = bool(event.state & 0x0001)
        rows = list(self.tree.get_children())

        if shift_held and self._anchor_item in rows and self._anchor_item != item:
            # Everything between the last box clicked and this one takes the
            # state this click produces, so shift-click can tick or untick.
            chart = self.item_to_chart.get(item)
            target = chart is not None and chart.rel not in self.checked_rels
            lo, hi = sorted((rows.index(self._anchor_item), rows.index(item)))
            for row in rows[lo:hi + 1]:
                self.set_check(row, target)
            self.tree.selection_set(rows[lo:hi + 1])
        else:
            self.toggle(item)

        self._anchor_item = item
        self.update_sel_count()
        return "break"

    def on_tree_space(self, _event):
        for item in self.tree.selection():
            self.toggle(item)
        self.update_sel_count()
        return "break"

    def on_tree_double(self, event):
        """Double-click opens the chart's folder in Finder."""
        chart = self.item_to_chart.get(self.tree.identify_row(event.y))
        if not chart:
            return
        try:
            # `open` reuses an existing Finder window for the folder if there
            # is one, otherwise it opens a new one.
            subprocess.run(["open", str(chart.path)], check=True)
            self.log_output(f"Opened folder for #{chart.num} {chart.label}")
        except Exception as e:
            self.log_output(f"✗ Could not open the folder: {e}", "error")
            messagebox.showerror("Could not open folder",
                                 f"{chart.path}\n\n{e}")

    def set_all_checks(self, on):
        for item in self.tree.get_children():
            self.set_check(item, on)
        self.update_sel_count()

    def invert_checks(self):
        for item in self.tree.get_children():
            self.toggle(item)
        self.update_sel_count()

    def check_where(self, predicate):
        for item in self.tree.get_children():
            chart = self.item_to_chart.get(item)
            self.set_check(item, bool(chart and predicate(chart)))
        self.update_sel_count()

    def update_sel_count(self):
        n = len(self.checked_rels)
        self.sel_count.config(text=f"{n} checked")
        if hasattr(self, "run_hint"):
            self.run_hint.config(
                text="check some songs first" if n == 0 else f"will run on {n} song(s)")

    def selected_charts(self):
        return [c for c in self.charts if c.rel in self.checked_rels]

    def attach_tooltips(self):
        """Attach hover text by matching each widget's visible label."""
        def walk(widget):
            for child in widget.winfo_children():
                try:
                    text = child.cget("text")
                except Exception:
                    text = None
                if text and text in TOOLTIPS:
                    ToolTip(child, TOOLTIPS[text])
                walk(child)
        walk(self.root)

    def build_command(self):
        """Build the command line from current settings"""
        # Get the Python executable and script path
        script_path = Path(__file__).parent / "VideoDownload.py"
        
        if not script_path.exists():
            raise FileNotFoundError(f"VideoDownload.py not found at {script_path}")
        
        # Start with base command
        cmd = [sys.executable, str(script_path), self.songs_folder_var.get()]
        
        # Quality
        cmd.extend(["--quality", self.quality_var.get()])
        
        # Flags
        if self.replace_var.get():
            cmd.append("--replace")
        if self.auto_sync_var.get():
            cmd.append("--auto-sync")
        if self.official_360_var.get():
            cmd.append("--official-360")
        if self.sync_only_var.get():
            cmd.append("--sync-only")
        
        # Advanced options
        try:
            min_conf = float(self.min_conf_var.get())
            cmd.extend(["--min-conf", str(min_conf)])
        except ValueError:
            raise ValueError(f"Invalid min-conf value: {self.min_conf_var.get()}")
        
        try:
            workers = int(self.workers_var.get())
            cmd.extend(["--workers", str(workers)])
        except ValueError:
            raise ValueError(f"Invalid workers value: {self.workers_var.get()}")
        
        try:
            sleep_interval = float(self.sleep_interval_var.get())
            if sleep_interval > 0:
                cmd.extend(["--sleep-interval", str(sleep_interval)])
        except ValueError:
            raise ValueError(f"Invalid sleep-interval value: {self.sleep_interval_var.get()}")
        
        try:
            max_sleep = float(self.max_sleep_var.get())
            if max_sleep > 0:
                cmd.extend(["--max-sleep-interval", str(max_sleep)])
        except ValueError:
            raise ValueError(f"Invalid max-sleep-interval value: {self.max_sleep_var.get()}")
        
        # Limit rate
        if self.limit_rate_var.get().strip():
            cmd.extend(["--limit-rate", self.limit_rate_var.get().strip()])
        
        # File options
        if self.manual_map_var.get().strip():
            cmd.extend(["--manual-map", self.manual_map_var.get().strip()])

        # Checked songs win over a manually chosen only-list file. The chart's
        # path relative to the Songs folder is written, because two different
        # charts can easily share an "Artist - Song" name.
        picked = self.selected_charts()
        if not picked:
            raise ValueError(
                "No songs are checked.\n\nTick the songs you want in the list "
                "above, or use one of the Check buttons (All, Missing video, "
                "Problems, Not synced).")
        if True:
            sel_file = Path(__file__).resolve().parent / "_selected_songs.txt"
            sel_file.write_text("\n".join(c.rel for c in picked) + "\n",
                                encoding="utf-8")
            cmd.extend(["--only-list", str(sel_file)])
        elif self.only_list_var.get().strip():
            cmd.extend(["--only-list", self.only_list_var.get().strip()])

        return cmd
    
    def run_script(self):
        """Launch the VideoDownload.py script in a subprocess"""
        # Validate songs folder
        songs_folder = self.songs_folder_var.get().strip()
        if not songs_folder:
            messagebox.showerror("Error", "Please select a Songs folder.")
            return
        
        if not Path(songs_folder).exists():
            messagebox.showerror("Error", f"Songs folder does not exist:\n{songs_folder}")
            return
        
        # Save current settings
        self.save_config()
        
        try:
            # Build command
            cmd = self.build_command()
            
            # Each run builds its own review list
            self.review_queue = []
            self.review_button.config(state=tk.DISABLED, text="Review unsure")

            # Log the command
            self.clear_output()
            self.log_output("═" * 80)
            self.log_output(f"Executing: {' '.join(cmd)}")
            self.log_output("═" * 80)
            
            env = os.environ.copy()
            
            # Launch process
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                bufsize=1,
                universal_newlines=True
            )
            
            # Update UI state
            self.is_running = True
            self.run_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
            
            # Start reading output in a thread
            threading.Thread(target=self.read_output, daemon=True).start()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start script:\n{e}")
            self.log_output(f"✗ Failed to start: {e}", "error")
    
    def set_status_line(self, text):
        if hasattr(self, "status_line"):
            self.status_line.config(text=text[:150])

    def read_output(self):
        """Read subprocess output in a thread"""
        try:
            for line in self.process.stdout:
                line = line.rstrip()
                # Live progress replaces the status line rather than filling
                # the log. Everything else is a real result worth keeping.
                if line.startswith("@STATUS "):
                    self.ui_call(lambda t=line[8:]: self.set_status_line(t))
                    continue
                if line.startswith("@REVIEW "):
                    try:
                        self.review_queue.append(_json.loads(line[8:]))
                    except ValueError:
                        pass
                    continue
                self.log_output(line)
            
            # Wait for process to finish
            exit_code = self.process.wait()
            
            # Log completion
            if exit_code == 0:
                self.log_output("═" * 80)
                self.log_output(f"✓ Process completed successfully (exit code: {exit_code})", "success")
            else:
                self.log_output("═" * 80)
                self.log_output(f"✗ Process exited with code: {exit_code}", "error")
            
        except Exception as e:
            self.log_output(f"✗ Error reading output: {e}", "error")
        
        finally:
            # Reset UI state
            self.is_running = False
            self.ui_call(lambda: self.set_status_line(""))
            self.ui_call(self.reset_buttons)
    
    def stop_script(self):
        """Stop the running subprocess"""
        if self.process and self.is_running:
            try:
                self.log_output("⚠ Stopping process...", "error")
                
                # Send SIGTERM on Unix-like, terminate() on Windows
                if sys.platform == "win32":
                    self.process.terminate()
                else:
                    self.process.send_signal(signal.SIGTERM)
                
                # Wait a bit, then kill if needed
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.log_output("⚠ Process didn't stop gracefully, killing...", "error")
                    self.process.kill()
                
                self.log_output("✓ Process stopped by user", "error")
                
            except Exception as e:
                self.log_output(f"✗ Error stopping process: {e}", "error")
    
    def reset_buttons(self):
        """Reset button states after process ends"""
        self.run_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        n = len(self.review_queue)
        self.review_button.config(state=tk.NORMAL if n else tk.DISABLED,
                                  text=f"Review {n} unsure" if n else "Review unsure")
        if n:
            self.log_output("")
            self.log_output(f"▶  {n} song{'s' if n != 1 else ''} need a choice. "
                            f"Press “Review {n} unsure” to step through them.",
                            "success")
        # Refresh the song list so the colours reflect what just changed
        self.scan_songs()

    def clear_output(self):
        """Clear the output text area"""
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete(1.0, tk.END)
        self.output_text.config(state=tk.DISABLED)
    
    def log_output(self, message, tag=None):
        """Append a message to the output text area"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.output_text.config(state=tk.NORMAL)
        self.output_text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.output_text.insert(tk.END, f"{message}\n", tag)
        self.output_text.see(tk.END)
        self.output_text.config(state=tk.DISABLED)
    
    def load_config(self):
        """Load configuration from JSON file"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    # Merge with defaults to handle new keys
                    return {**DEFAULT_CONFIG, **config}
            except Exception as e:
                print(f"Warning: Failed to load config: {e}")
        return DEFAULT_CONFIG.copy()
    
    def save_config(self):
        """Save current settings to JSON file"""
        config = {
            "songs_folder": self.songs_folder_var.get(),
            "quality": self.quality_var.get(),
            "replace": self.replace_var.get(),
            "auto_sync": self.auto_sync_var.get(),
            "min_conf": float(self.min_conf_var.get()) if self.min_conf_var.get() else 0.20,
            "official_360": self.official_360_var.get(),
            "sleep_interval": float(self.sleep_interval_var.get()) if self.sleep_interval_var.get() else 0.0,
            "max_sleep_interval": float(self.max_sleep_var.get()) if self.max_sleep_var.get() else 0.0,
            "limit_rate": self.limit_rate_var.get(),
            "workers": int(self.workers_var.get()) if self.workers_var.get() else 1,
            "sync_only": self.sync_only_var.get(),
            "manual_map": self.manual_map_var.get(),
            "only_list": self.only_list_var.get()
        }
        
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save config: {e}")
    
    def load_settings_to_ui(self):
        """Populate UI fields from loaded config"""
        # This is already done via variable initialization in build_ui,
        # but keeping this method for explicit clarity
        pass
    
    def on_close(self):
        """Handle window close event"""
        # Stop any running process
        if self.is_running:
            response = messagebox.askyesno(
                "Process Running",
                "A process is currently running. Stop it and exit?")
            if response:
                self.stop_script()
            else:
                return
        
        # Save settings
        self.save_config()
        
        # Close window
        self.root.destroy()


def main():
    """Entry point"""
    root = tk.Tk()
    app = VideoDownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
