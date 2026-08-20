#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dark theme for the app, styled after modern Clone Hero song browsers.

macOS ships ttk's "aqua" theme, which draws native controls and silently
ignores any background colour you set. Switching to "clam" gives up the
native look but is fully styleable, which is the only way to get a real dark
UI in Tk.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------
BG          = "#0d1117"   # window
PANEL       = "#161b22"   # cards, headers, entries
ROW         = "#1c2128"   # table row
ROW_ALT     = "#161b22"   # alternating table row
HOVER       = "#232a33"
BORDER      = "#30363d"
FG          = "#c9d1d9"   # main text
FG_DIM      = "#8b949e"   # secondary text
FG_BRIGHT   = "#f0f6fc"   # headings
ACCENT      = "#ff4d5a"   # the coral used for emphasis
ACCENT_DIM  = "#7d2730"
SEL_BG      = "#243044"
SEL_FG      = "#f0f6fc"

# Row states, tuned for a dark background rather than the light pastels a
# light theme would use.
FILL_OK     = "#12301f"   # synced and ready
FILL_SOFT   = "#33290d"   # low resolution
FILL_BAD    = "#3a1b1f"   # broken
TEXT_VID    = "#7ee787"   # has a video
TEXT_BAD    = "#ff7b72"   # something wrong
TEXT_PLAIN  = FG

FONT        = ("Helvetica Neue", 12)
FONT_BOLD   = ("Helvetica Neue", 12, "bold")
FONT_H1     = ("Helvetica Neue", 15, "bold")
FONT_SMALL  = ("Helvetica Neue", 11)

# Consistent spacing so sections do not crowd each other.
PAD_WINDOW  = 18
PAD_SECTION = 14
PAD_ITEM    = 8


def apply_theme(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")          # aqua cannot be recoloured
    except tk.TclError:
        pass

    # Classic tk widgets (Label, Frame, Message, Toplevel, Text) pick these up
    # automatically, including ones created later in dialogs.
    for pattern, value in [
        ("*Background", BG), ("*background", BG),
        ("*Foreground", FG), ("*foreground", FG),
        ("*Label.background", BG), ("*Label.foreground", FG),
        ("*Frame.background", BG),
        ("*Message.background", BG), ("*Message.foreground", FG),
        ("*Toplevel.background", BG),
        ("*Text.background", PANEL), ("*Text.foreground", FG),
        ("*Text.insertBackground", FG),
        ("*Text.selectBackground", SEL_BG), ("*Text.selectForeground", SEL_FG),
        ("*Entry.background", PANEL), ("*Entry.foreground", FG),
        ("*highlightBackground", BG), ("*highlightColor", BORDER),
        ("*Font", FONT),
    ]:
        root.option_add(pattern, value)

    # focuscolor must match the background: clam draws it as a dashed ring
    # around focused widgets, which reads as an error box on a dark theme.
    style.configure(".", background=BG, foreground=FG, font=FONT,
                    bordercolor=BORDER, darkcolor=PANEL, lightcolor=PANEL,
                    troughcolor=PANEL, focuscolor=BG)

    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT)
    style.configure("TLabelframe", background=BG, foreground=FG_DIM,
                    bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=FG_DIM,
                    font=FONT_BOLD)
    style.configure("TSeparator", background=BORDER)

    # --- buttons ---
    style.configure("TButton", background=PANEL, foreground=FG,
                    bordercolor=BORDER, focusthickness=0, relief="flat",
                    padding=(10, 6), font=FONT)
    style.map("TButton",
              background=[("pressed", ACCENT_DIM), ("active", HOVER),
                          ("disabled", BG)],
              foreground=[("disabled", FG_DIM)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    font=FONT_BOLD)
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_DIM), ("active", "#ff6b76"),
                          ("disabled", PANEL)],
              foreground=[("disabled", FG_DIM)])

    # --- entries, combos ---
    style.configure("TEntry", fieldbackground=PANEL, foreground=FG,
                    bordercolor=BORDER, insertcolor=FG, padding=6)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                    foreground=FG, arrowcolor=FG, bordercolor=BORDER, padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
              foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", SEL_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", SEL_FG)

    # clam draws the tick box from indicatorbackground/indicatorforeground.
    # The "indicatorcolor" option other themes use is ignored here, which is
    # why an unstyled box stays white with a black tick.
    style.configure("TCheckbutton", background=BG, foreground=FG,
                    focuscolor=BG, padding=(2, 4),
                    indicatorbackground=PANEL, indicatorforeground=FG_BRIGHT,
                    upperbordercolor=BORDER, lowerbordercolor=BORDER)
    style.map("TCheckbutton",
              background=[("active", BG)],
              foreground=[("disabled", FG_DIM)],
              indicatorbackground=[("selected", ACCENT), ("active", HOVER),
                                   ("disabled", BG)],
              indicatorforeground=[("selected", "#ffffff")],
              upperbordercolor=[("selected", ACCENT), ("active", "#4a5462")],
              lowerbordercolor=[("selected", ACCENT), ("active", "#4a5462")])

    # Header used by the collapsible sections.
    style.configure("Section.TButton", background=PANEL, foreground=FG_BRIGHT,
                    font=FONT_BOLD, anchor="w", relief="flat",
                    padding=(12, 9), bordercolor=BORDER, focuscolor=PANEL)

    # Decision buttons in the review picker. These must be ttk, not tk: a
    # classic tk.Button keeps the native macOS look whatever colours you set,
    # so white text ends up on a white body.
    style.configure("Success.TButton", background="#238636", foreground="#ffffff",
                    font=FONT_BOLD, relief="flat", padding=(10, 7),
                    bordercolor="#238636", focuscolor="#238636",
                    darkcolor="#238636", lightcolor="#238636")
    style.map("Success.TButton",
              background=[("active", "#2ea043"), ("pressed", "#1a612a"),
                          ("disabled", PANEL)],
              foreground=[("disabled", FG_DIM)])

    style.configure("Warn.TButton", background="#9e6a03", foreground="#ffffff",
                    font=FONT, relief="flat", padding=(10, 7),
                    bordercolor="#9e6a03", focuscolor="#9e6a03",
                    darkcolor="#9e6a03", lightcolor="#9e6a03")
    style.map("Warn.TButton",
              background=[("active", "#bb8009"), ("pressed", "#7d5402"),
                          ("disabled", PANEL)],
              foreground=[("disabled", FG_DIM)])

    # Inline toggle used on the Run row: no fill, just the label.
    style.configure("Inline.TButton", background=BG, foreground=FG_DIM,
                    font=FONT, relief="flat", padding=(6, 6), focuscolor=BG,
                    bordercolor=BG)
    style.map("Inline.TButton",
              background=[("active", BG), ("pressed", BG)],
              foreground=[("active", FG_BRIGHT)])
    style.map("Section.TButton",
              background=[("active", HOVER), ("pressed", HOVER)],
              foreground=[("active", FG_BRIGHT)])

    # --- table ---
    style.configure("Treeview", background=ROW, fieldbackground=ROW,
                    foreground=FG, bordercolor=BORDER, borderwidth=0,
                    rowheight=30, font=FONT)
    style.map("Treeview",
              background=[("selected", SEL_BG)],
              foreground=[("selected", SEL_FG)])
    style.configure("Treeview.Heading", background=PANEL, foreground=FG_DIM,
                    relief="flat", borderwidth=0, padding=(8, 8),
                    font=("Helvetica Neue", 12, "bold"))
    style.map("Treeview.Heading",
              background=[("active", HOVER)], foreground=[("active", FG_BRIGHT)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    # --- scrollbars, sliders ---
    # clam draws the trough from bordercolor/darkcolor/lightcolor as well as
    # troughcolor, so all four have to be set or the bar stays pale.
    for orient in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(orient, gripcount=0, borderwidth=0, relief="flat",
                        background=BORDER, troughcolor=BG, bordercolor=BG,
                        darkcolor=BG, lightcolor=BG, arrowcolor=FG_DIM,
                        arrowsize=12)
        style.map(orient,
                  background=[("pressed", ACCENT), ("active", "#4a5462")],
                  arrowcolor=[("active", FG)])

    # Scrub bar: thin dark trough, pale grip. Tinting darkcolor/lightcolor
    # with the accent made the grip render as a small red block.
    for sc in ("TScale", "Horizontal.TScale"):
        style.configure(sc, background=BG, troughcolor=PANEL,
                        bordercolor=BORDER, darkcolor=BORDER,
                        lightcolor=BORDER, gripcount=0, sliderthickness=14)
        style.map(sc, background=[("active", BG)],
                  darkcolor=[("active", "#4a5462")],
                  lightcolor=[("active", "#4a5462")])

    return style


def style_tk_scrollbar(bar) -> None:
    """Recolour a classic tk.Scrollbar, which ttk styling does not reach.

    ScrolledText builds its own tk.Scrollbar, so without this the log keeps a
    bright white scrollbar against the dark panel.
    """
    try:
        bar.configure(background=BORDER, troughcolor=BG,
                      activebackground="#4a5462", highlightbackground=BG,
                      borderwidth=0, elementborderwidth=0, width=12)
    except Exception:
        pass
