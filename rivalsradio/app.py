"""Modern CustomTkinter desktop UI for RivalsRadio."""

from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, filedialog, colorchooser

import customtkinter as ctk

from . import theming
from .config import Config, HeroConfig, app_data_dir
from .spotify_controller import SpotifyController
from .monitor import Monitor
from .audio_visualizer import AudioVisualizer
from .stage import StageWindow
from .stage_state import StageState
from .nowplaying import NowPlaying
from .web_overlay import WebOverlay
from .wizard import SetupWizard
from .stats import SessionStats

# ---- Palette -------------------------------------------------------------
ACCENT = "#1DB954"
ACCENT_HOVER = "#1ed760"
ACCENT_INK = "#06210f"
DANGER = "#e5484d"
DANGER_HOVER = "#ec5d62"
DANGER_INK = "#2a0a08"
NEUTRAL = "#34373e"
NEUTRAL_HOVER = "#40444d"
SIDEBAR = "#16171b"
SIDEBAR_SEL = "#26282e"
CONTENT_BG = "#1b1c20"
CARD = "#232429"
CARD_HI = "#2b2d33"
TEXT = "#e9ebed"
MUTED = "#8b9096"
LOG_BG = "#17181b"
LOG_FG = "#d7dadd"
WARN = "#e3b341"

ACCENT_BTN = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_INK)
NEUTRAL_BTN = dict(fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TEXT)
DANGER_BTN = dict(fg_color=DANGER, hover_color=DANGER_HOVER, text_color=DANGER_INK)


class App:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title("RivalsRadio")
        self.root.geometry("1000x720")
        self.root.minsize(880, 620)
        self.root.configure(fg_color=CONTENT_BG)
        self._set_window_icon()

        # Fonts (must be created after the root exists).
        self.f_brand = ctk.CTkFont(size=22, weight="bold")
        self.f_h1 = ctk.CTkFont(size=24, weight="bold")
        self.f_section = ctk.CTkFont(size=15, weight="bold")
        self.f_body = ctk.CTkFont(size=13)
        self.f_bold = ctk.CTkFont(size=13, weight="bold")
        self.f_small = ctk.CTkFont(size=12)
        self.f_nav = ctk.CTkFont(size=14, weight="bold")
        self.f_mono = ("Consolas", 11)

        self.cfg = Config.load()
        self.spotify = SpotifyController(self.cfg.spotify)
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._hero_queue: "queue.Queue[str]" = queue.Queue()
        self._event_queue: "queue.Queue[dict]" = queue.Queue()
        self.session_stats = SessionStats()
        self.monitor = Monitor(
            self.cfg, self.spotify,
            on_log=self._enqueue_log,
            on_hero=self._on_hero_detected,
            on_event=self._enqueue_event,
        )

        self.visualizer = AudioVisualizer()
        self.state = StageState()
        self.state.attach_visualizer(self.visualizer)
        self.stage: "StageWindow | None" = None
        self._accent_cache: dict = {}

        self.nowplaying = NowPlaying(self.spotify, self.state, on_log=self._enqueue_log)
        self.nowplaying.start()
        self.web_overlay = WebOverlay(self.state, self.cfg.web_overlay_port)

        # Shared UI variables.
        self.status_var = tk.StringVar(value="Idle")
        self.hero_var = tk.StringVar(value="—")
        self.spotify_var = tk.StringVar(value="Not connected")
        self.game_var = tk.StringVar(value="Not detected")
        self.source_var = tk.StringVar(value=self.cfg.hero_source)

        self.nav_buttons: dict = {}
        self.pages: dict = {}

        self._build_ui()
        self._refresh_status()
        self.root.after(150, self._drain_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.cfg.web_overlay_enabled:
            self._toggle_web_overlay(initial=True)
        if not self.cfg.setup_complete:
            self.root.after(300, lambda: SetupWizard(self.root, self))

    def _set_window_icon(self) -> None:
        """Set the taskbar/title-bar icon from the bundled .ico (Windows)."""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ico = os.path.join(base, "assets", "icon.ico")
        if os.path.exists(ico):
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass  # .ico is Windows-only; ignore elsewhere

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        content = ctk.CTkFrame(self.root, fg_color=CONTENT_BG, corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        for name, builder in (
            ("Status", self._build_status_page),
            ("Heroes", self._build_heroes_page),
            ("Stats", self._build_stats_page),
            ("Settings", self._build_settings_page),
        ):
            page = ctk.CTkFrame(content, fg_color=CONTENT_BG, corner_radius=0)
            page.grid(row=0, column=0, sticky="nsew")
            builder(page)
            self.pages[name] = page

        self._select_page("Status")

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self.root, width=212, corner_radius=0, fg_color=SIDEBAR)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(6, weight=1)

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=22, pady=(26, 6))
        ctk.CTkLabel(brand, text="Rivals", font=self.f_brand, text_color=TEXT).pack(side="left")
        ctk.CTkLabel(brand, text="Radio", font=self.f_brand, text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(bar, text="hero-aware Spotify", font=self.f_small,
                     text_color=MUTED).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 22))

        for i, name in enumerate(("Status", "Heroes", "Stats", "Settings")):
            btn = ctk.CTkButton(
                bar, text=name, font=self.f_nav, anchor="w", height=42,
                corner_radius=8, fg_color="transparent", text_color=MUTED,
                hover_color=SIDEBAR_SEL, command=lambda n=name: self._select_page(n),
            )
            btn.grid(row=2 + i, column=0, sticky="ew", padx=14, pady=3)
            self.nav_buttons[name] = btn

        # Footer: live status dots.
        footer = ctk.CTkFrame(bar, fg_color="transparent")
        footer.grid(row=7, column=0, sticky="ew", padx=22, pady=18)
        self.dot_monitor = ctk.CTkLabel(footer, text="● Idle", font=self.f_small, text_color=MUTED)
        self.dot_monitor.pack(anchor="w")
        self.dot_spotify = ctk.CTkLabel(footer, text="● Spotify offline", font=self.f_small, text_color=MUTED)
        self.dot_spotify.pack(anchor="w", pady=(4, 0))

    def _select_page(self, name: str) -> None:
        for n, btn in self.nav_buttons.items():
            if n == name:
                btn.configure(fg_color=SIDEBAR_SEL, text_color=ACCENT)
            else:
                btn.configure(fg_color="transparent", text_color=MUTED)
        self.pages[name].tkraise()

    # ----- helpers ----------------------------------------------------
    def _card(self, parent, title: str | None = None, **pack):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14)
        defaults = dict(fill="x", padx=24, pady=10)
        defaults.update(pack)
        card.pack(**defaults)
        if title:
            ctk.CTkLabel(card, text=title, font=self.f_section, text_color=TEXT).pack(
                anchor="w", padx=18, pady=(14, 2))
        return card

    def _page_title(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=self.f_h1, text_color=TEXT).pack(
            anchor="w", padx=24, pady=(24, 4))

    def _labeled_entry(self, parent, label, var, show=None, placeholder=""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label, font=self.f_body, text_color=MUTED,
                     width=170, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, textvariable=var, show=show, height=34,
                             placeholder_text=placeholder, fg_color=CARD_HI, border_width=0)
        entry.pack(side="left", fill="x", expand=True)
        return entry

    # ----- Status page ------------------------------------------------
    def _build_status_page(self, page) -> None:
        self._page_title(page, "Status")

        info = self._card(page)
        grid = ctk.CTkFrame(info, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=16)
        rows = [("Monitoring", self.status_var), ("Game", self.game_var),
                ("Current hero", self.hero_var), ("Spotify", self.spotify_var)]
        for r, (label, var) in enumerate(rows):
            ctk.CTkLabel(grid, text=label, font=self.f_bold, text_color=MUTED,
                         width=130, anchor="w").grid(row=r, column=0, sticky="w", pady=3)
            ctk.CTkLabel(grid, textvariable=var, font=self.f_body, text_color=TEXT,
                         anchor="w").grid(row=r, column=1, sticky="w", pady=3)

        # Detection source as a modern segmented control.
        src = self._card(page, "Detection source")
        seg = ctk.CTkSegmentedButton(
            src, values=["native", "gep"], variable=self.source_var,
            command=self._apply_source_change, font=self.f_bold,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=CARD_HI, unselected_hover_color=NEUTRAL_HOVER,
            text_color=TEXT, height=34,
        )
        seg.pack(anchor="w", padx=18, pady=(4, 4))
        ctk.CTkLabel(src, text="native = companion Overwolf app over localhost · "
                     "gep = ow-electron bridge",
                     font=self.f_small, text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 14))

        # Manual hero switch — works today, no detection needed.
        man = self._card(page, "Manual hero switch")
        mrow = ctk.CTkFrame(man, fg_color="transparent")
        mrow.pack(fill="x", padx=18, pady=(2, 4))
        heroes = sorted(self.cfg.heroes) or ["—"]
        self.manual_hero_var = tk.StringVar(value=heroes[0])
        self.manual_menu = ctk.CTkOptionMenu(
            mrow, values=heroes, variable=self.manual_hero_var, width=200, height=34,
            fg_color=CARD_HI, button_color=NEUTRAL, button_hover_color=NEUTRAL_HOVER)
        self.manual_menu.pack(side="left")
        ctk.CTkButton(mrow, text="Switch playlist", height=34, font=self.f_bold,
                      command=lambda: self._manual_set_hero(self.manual_hero_var.get()),
                      **ACCENT_BTN).pack(side="left", padx=8)
        ctk.CTkLabel(man, text="Pick your hero to switch the music now — handy on a second "
                     "monitor while automatic detection is unavailable.",
                     font=self.f_small, text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 14))

        # Action buttons.
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(2, 8))
        self.start_btn = ctk.CTkButton(actions, text="Start monitoring", height=40, width=160,
                                       font=self.f_bold, command=self._toggle_monitor, **ACCENT_BTN)
        self.start_btn.pack(side="left")
        ctk.CTkButton(actions, text="Connect Spotify", height=40, font=self.f_bold,
                      command=self._connect_spotify, **NEUTRAL_BTN).pack(side="left", padx=8)
        ctk.CTkButton(actions, text="Open Stage view", height=40, font=self.f_bold,
                      command=self._open_stage, **NEUTRAL_BTN).pack(side="left")

        # Activity log.
        log_card = self._card(page, "Activity log", fill="both", expand=True, pady=(10, 24))
        wrap = ctk.CTkFrame(log_card, fg_color=LOG_BG, corner_radius=10)
        wrap.pack(fill="both", expand=True, padx=18, pady=(2, 16))
        self.log_text = tk.Text(
            wrap, state="disabled", wrap="word", bg=LOG_BG, fg=LOG_FG, font=self.f_mono,
            relief="flat", bd=0, highlightthickness=0, padx=14, pady=12,
            insertbackground=LOG_FG, selectbackground=ACCENT, selectforeground=ACCENT_INK,
            spacing1=1, spacing3=3,
        )
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_text.tag_config("accent", foreground=ACCENT)
        self.log_text.tag_config("muted", foreground=MUTED)
        self.log_text.tag_config("warn", foreground=WARN)

    # ----- Heroes page ------------------------------------------------
    def _build_heroes_page(self, page) -> None:
        self._page_title(page, "Heroes")
        ctk.CTkLabel(
            page, justify="left", wraplength=760, font=self.f_small, text_color=MUTED,
            text=("For each hero: paste the Spotify playlist URI, set the Stage art "
                  "(logo + signature), and pick the two Stage colours — a main colour "
                  "(background glow) and an accent colour (visualizer bars). Leave the "
                  "colours unset to auto-extract them from the logo."),
        ).pack(anchor="w", padx=24, pady=(0, 8))

        self.hero_rows = ctk.CTkScrollableFrame(page, fg_color=CARD, corner_radius=14)
        self.hero_rows.pack(fill="both", expand=True, padx=24, pady=4)

        self.playlist_vars = {}
        self.color_main_vars = {}
        self.color_accent_vars = {}
        self.color_swatches = {}  # hero -> (main_btn, accent_btn)
        self.avatar_labels = {}
        self._render_hero_rows()

        add = ctk.CTkFrame(page, fg_color="transparent")
        add.pack(fill="x", padx=24, pady=(8, 24))
        self.new_hero_var = tk.StringVar()
        new_entry = ctk.CTkEntry(add, textvariable=self.new_hero_var, width=220, height=36,
                                 placeholder_text="New hero name", fg_color=CARD_HI, border_width=0)
        new_entry.pack(side="left")
        new_entry.bind("<Return>", lambda _e: self._add_hero())
        ctk.CTkButton(add, text="Add hero", height=36, font=self.f_bold,
                      command=self._add_hero, **NEUTRAL_BTN).pack(side="left", padx=8)
        ctk.CTkButton(add, text="Save mappings", height=36, width=150, font=self.f_bold,
                      command=self._save_heroes, **ACCENT_BTN).pack(side="right")

    def _render_hero_rows(self) -> None:
        for child in self.hero_rows.winfo_children():
            child.destroy()
        self.playlist_vars.clear()
        self.color_main_vars.clear()
        self.color_accent_vars.clear()
        self.color_swatches.clear()
        self.avatar_labels.clear()
        self._rendered_hero_count = len(self.cfg.heroes)

        for hero in sorted(self.cfg.heroes):
            hc = self.cfg.heroes[hero]
            row = ctk.CTkFrame(self.hero_rows, fg_color=CARD_HI, corner_radius=10)
            row.pack(fill="x", padx=6, pady=4)
            ctk.CTkLabel(row, text=hero, width=140, anchor="w", font=self.f_bold,
                         text_color=TEXT).pack(side="left", padx=(12, 6), pady=8)
            var = tk.StringVar(value=hc.playlist_uri)
            self.playlist_vars[hero] = var
            ctk.CTkEntry(row, textvariable=var, height=32, fg_color=CARD, border_width=0,
                         placeholder_text="spotify:playlist:…").pack(
                side="left", fill="x", expand=True, padx=4, pady=8)
            art_set = bool(hc.logo or hc.signature)
            art_btn = ctk.CTkButton(row, text="Art ✓" if art_set else "Art…", width=64, height=32,
                                    font=self.f_small, command=lambda h=hero: self._open_hero_art(h),
                                    **(ACCENT_BTN if art_set else NEUTRAL_BTN))
            art_btn.pack(side="left", padx=2)
            self.avatar_labels[hero] = art_btn  # reused dict: hero -> art button

            # Two per-hero colour swatches: main (glow) and accent (bars).
            self.color_main_vars[hero] = tk.StringVar(value=hc.color_main)
            self.color_accent_vars[hero] = tk.StringVar(value=hc.color_accent)
            main_btn = self._color_swatch(row, hero, "main")
            main_btn.pack(side="left", padx=(6, 2))
            accent_btn = self._color_swatch(row, hero, "accent")
            accent_btn.pack(side="left", padx=2)
            self.color_swatches[hero] = (main_btn, accent_btn)

            ctk.CTkButton(row, text="✕", width=32, height=32, font=self.f_bold,
                          fg_color="transparent", hover_color=DANGER, text_color=MUTED,
                          command=lambda h=hero: self._remove_hero(h)).pack(side="left", padx=(2, 10))

        # Keep the Status-tab manual hero dropdown in sync with the roster.
        if hasattr(self, "manual_menu"):
            names = sorted(self.cfg.heroes) or ["—"]
            self.manual_menu.configure(values=names)
            if self.manual_hero_var.get() not in names:
                self.manual_hero_var.set(names[0])

    def _color_swatch(self, parent, hero: str, kind: str):
        """A small colour button (main/accent) that opens a colour picker."""
        var = (self.color_main_vars if kind == "main" else self.color_accent_vars)[hero]
        value = var.get().strip()
        label = "Main" if kind == "main" else "Accent"
        btn = ctk.CTkButton(
            parent, text=label, width=66, height=32, font=self.f_small,
            text_color=self._swatch_ink(value),
            fg_color=value if theming.is_valid_hex(value) else CARD,
            hover_color=value if theming.is_valid_hex(value) else NEUTRAL_HOVER,
            border_width=1, border_color=NEUTRAL,
            command=lambda h=hero, k=kind: self._pick_color(h, k))
        return btn

    @staticmethod
    def _swatch_ink(hex_value: str) -> str:
        """Pick black/white text for legibility on a coloured swatch."""
        if not theming.is_valid_hex(hex_value):
            return TEXT
        h = hex_value.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"

    def _pick_color(self, hero: str, kind: str) -> None:
        var = (self.color_main_vars if kind == "main" else self.color_accent_vars)[hero]
        current = var.get().strip() or None
        rgb, hex_value = colorchooser.askcolor(
            color=current if theming.is_valid_hex(current or "") else None,
            title=f"{kind.capitalize()} colour — {hero}", parent=self.root)
        if not hex_value:
            return
        hex_value = hex_value.lower()
        var.set(hex_value)
        btn = self.color_swatches.get(hero, (None, None))[0 if kind == "main" else 1]
        if btn:
            btn.configure(fg_color=hex_value, hover_color=hex_value,
                          text_color=self._swatch_ink(hex_value))
        # Persist immediately and refresh the Stage if this hero is showing.
        field = "color_main" if kind == "main" else "color_accent"
        if hero in self.cfg.heroes:
            setattr(self.cfg.heroes[hero], field, hex_value)
            self.cfg.save()
        self._accent_cache.pop(hero, None)
        if self.hero_var.get() == hero:
            self._update_stage(hero)

    # ----- Stats page -------------------------------------------------
    def _stat_tile(self, parent, caption: str):
        tile = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14)
        tile.pack(side="left", expand=True, fill="x", padx=6)
        var = tk.StringVar(value="—")
        ctk.CTkLabel(tile, textvariable=var, font=self.f_h1, text_color=TEXT).pack(
            anchor="w", padx=16, pady=(14, 0))
        ctk.CTkLabel(tile, text=caption, font=self.f_small, text_color=MUTED).pack(
            anchor="w", padx=16, pady=(0, 14))
        return var

    def _build_stats_page(self, page) -> None:
        header = ctk.CTkFrame(page, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(24, 4))
        ctk.CTkLabel(header, text="Stats", font=self.f_h1, text_color=TEXT).pack(side="left")
        ctk.CTkButton(header, text="Reset session", height=34, font=self.f_small,
                      command=self._reset_stats, **NEUTRAL_BTN).pack(side="right")
        ctk.CTkLabel(page, text="This session — playtime is tracked live; match results "
                     "and KDA come from the Overwolf GEP bridge when available.",
                     font=self.f_small, text_color=MUTED).pack(anchor="w", padx=24, pady=(0, 8))

        tiles = ctk.CTkFrame(page, fg_color="transparent")
        tiles.pack(fill="x", padx=18, pady=4)
        self.stat_playtime = self._stat_tile(tiles, "Session playtime")
        self.stat_matches = self._stat_tile(tiles, "Matches (W–L)")
        self.stat_winrate = self._stat_tile(tiles, "Win rate")
        self.stat_kda = self._stat_tile(tiles, "KDA")

        card = self._card(page, "Hero breakdown", fill="both", expand=True, pady=(10, 24))
        wrap = ctk.CTkFrame(card, fg_color=LOG_BG, corner_radius=10)
        wrap.pack(fill="both", expand=True, padx=18, pady=(2, 16))
        self.stats_text = tk.Text(
            wrap, state="disabled", wrap="none", bg=LOG_BG, fg=LOG_FG, font=self.f_mono,
            relief="flat", bd=0, highlightthickness=0, padx=14, pady=12)
        self.stats_text.pack(fill="both", expand=True, padx=6, pady=6)
        self.stats_text.tag_config("head", foreground=MUTED)
        self.stats_text.tag_config("accent", foreground=ACCENT)
        self._refresh_stats()

    def _fmt_dur(self, seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"

    def _refresh_stats(self) -> None:
        if not hasattr(self, "stat_kda"):
            return
        s = self.session_stats.snapshot()
        self.stat_playtime.set(self._fmt_dur(s["duration"]))
        self.stat_matches.set(f"{s['matches']}  ({s['wins']}–{s['losses']})")
        self.stat_winrate.set("—" if s["winrate"] is None else f"{s['winrate']:.0f}%")
        self.stat_kda.set(f"{s['kda']:.2f}  ({s['kills']}/{s['deaths']}/{s['assists']})")

        self.stats_text.config(state="normal")
        self.stats_text.delete("1.0", "end")
        self.stats_text.insert("end", f"{'Hero':<20}{'Playtime':>12}{'Plays':>8}\n", ("head",))
        for h in s["heroes"]:
            mark = "▸ " if h["hero"] == s["current"] else "  "
            line = f"{mark}{h['hero']:<18}{self._fmt_dur(h['seconds']):>12}{h['plays']:>8}\n"
            self.stats_text.insert("end", line, ("accent",) if h["hero"] == s["current"] else ())
        if not s["heroes"]:
            self.stats_text.insert("end", "\n  No heroes played yet this session.", ("head",))
        self.stats_text.config(state="disabled")

    def _reset_stats(self) -> None:
        self.session_stats.reset()
        self._refresh_stats()
        self._append_log("Session stats reset.")

    # ----- Settings page ----------------------------------------------
    def _build_settings_page(self, page) -> None:
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(page, fg_color=CONTENT_BG, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")

        self._page_title(scroll, "Settings")

        # Spotify.
        sf = self._card(scroll, "Spotify (Premium required)")
        self.client_id_var = tk.StringVar(value=self.cfg.spotify.client_id)
        self.client_secret_var = tk.StringVar(value=self.cfg.spotify.client_secret)
        self.redirect_var = tk.StringVar(value=self.cfg.spotify.redirect_uri)
        self.device_var = tk.StringVar(value=self.cfg.spotify.device_name)
        self._labeled_entry(sf, "Client ID", self.client_id_var)
        self._labeled_entry(sf, "Client Secret", self.client_secret_var, show="•")
        self._labeled_entry(sf, "Redirect URI", self.redirect_var)
        self._labeled_entry(sf, "Device name (optional)", self.device_var,
                            placeholder="leave blank for active device")
        ctk.CTkFrame(sf, fg_color="transparent", height=8).pack()

        # Detection source.
        df = self._card(scroll, "Hero detection source")
        ctk.CTkSegmentedButton(
            df, values=["native", "gep"], variable=self.source_var,
            command=self._apply_source_change, font=self.f_bold,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=CARD_HI, unselected_hover_color=NEUTRAL_HOVER,
            text_color=TEXT, height=34,
        ).pack(anchor="w", padx=18, pady=4)
        self.bridge_cmd_var = tk.StringVar(value=self.cfg.gep_bridge_cmd)
        self._labeled_entry(df, "GEP bridge command", self.bridge_cmd_var,
                            placeholder="blank = use the bundled bridge")
        self.pkg_url_var = tk.StringVar(value=self.cfg.gep_packages_url)
        self._labeled_entry(df, "GEP packages URL (advanced)", self.pkg_url_var,
                            placeholder="blank = Overwolf PROD; QA URL for DEV games")
        self.gep_debug_var = tk.BooleanVar(value=self.cfg.gep_debug)
        ctk.CTkSwitch(df, text="Log raw GEP events for diagnostics",
                      variable=self.gep_debug_var, command=self._toggle_gep_debug,
                      font=self.f_body, progress_color=ACCENT).pack(anchor="w", padx=18, pady=(8, 2))
        ctk.CTkLabel(df, text=f"Writes to {os.path.join(app_data_dir(), 'gep-debug.log')}",
                     font=self.f_small, text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 14))

        # Stage & overlay.
        pf = self._card(scroll, "Stage & overlay")
        frow = ctk.CTkFrame(pf, fg_color="transparent")
        frow.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(frow, text="Visualizer FPS", font=self.f_body, text_color=MUTED,
                     width=170, anchor="w").pack(side="left")
        self.fps_var = tk.IntVar(value=self.cfg.stage_fps)
        ctk.CTkOptionMenu(frow, values=["60", "90", "120", "144", "160"],
                          variable=tk.StringVar(value=str(self.cfg.stage_fps)),
                          command=lambda v: self.fps_var.set(int(v)),
                          width=140, height=34, fg_color=CARD_HI, button_color=NEUTRAL,
                          button_hover_color=NEUTRAL_HOVER).pack(side="left")
        self.nowplaying_var = tk.BooleanVar(value=self.cfg.show_now_playing)
        ctk.CTkSwitch(pf, text="Show now-playing (track + album art)", variable=self.nowplaying_var,
                      font=self.f_body, progress_color=ACCENT).pack(anchor="w", padx=18, pady=8)
        prow = ctk.CTkFrame(pf, fg_color="transparent")
        prow.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(prow, text="OBS overlay port", font=self.f_body, text_color=MUTED,
                     width=170, anchor="w").pack(side="left")
        self.web_port_var = tk.IntVar(value=self.cfg.web_overlay_port)
        ctk.CTkEntry(prow, textvariable=self.web_port_var, width=100, height=34,
                     fg_color=CARD_HI, border_width=0).pack(side="left")
        self.web_btn = ctk.CTkButton(
            pf, text="Stop OBS overlay" if self.web_overlay.running else "Start OBS overlay",
            height=34, font=self.f_small, command=self._toggle_web_overlay, **NEUTRAL_BTN)
        self.web_btn.pack(anchor="w", padx=18, pady=(8, 14))

        ctk.CTkButton(scroll, text="Save settings", height=40, width=170, font=self.f_bold,
                      command=self._save_settings, **ACCENT_BTN).pack(pady=18)

    # --------------------------------------------------------------- actions
    def _enqueue_log(self, message: str) -> None:
        self._log_queue.put(message)

    def _enqueue_event(self, event: dict) -> None:
        self._event_queue.put(event)

    def _on_hero_detected(self, hero: str) -> None:
        self._enqueue_log(f"▶ Now playing as: {hero}")
        self._hero_queue.put(hero)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._append_log(msg)
                if msg.startswith("▶ Now playing as: "):
                    self.hero_var.set(msg.replace("▶ Now playing as: ", ""))
        except queue.Empty:
            pass
        try:
            while True:
                hero = self._hero_queue.get_nowait()
                self._update_stage(hero)
        except queue.Empty:
            pass
        try:
            while True:
                self._handle_game_event(self._event_queue.get_nowait())
        except queue.Empty:
            pass
        self._refresh_stats()
        # If the monitor auto-added a newly-detected hero, surface it in the
        # Heroes list without discarding any playlist text typed but not saved.
        if hasattr(self, "_rendered_hero_count") and \
                len(self.cfg.heroes) != self._rendered_hero_count:
            self._persist_hero_edits()
            self._render_hero_rows()
        self._refresh_status()
        self.root.after(250, self._drain_log_queue)

    def _persist_hero_edits(self) -> None:
        """Save current hero-row edits without validation dialogs (for refreshes)."""
        for hero, var in self.playlist_vars.items():
            if hero in self.cfg.heroes:
                self.cfg.heroes[hero].playlist_uri = var.get().strip()
        for hero, var in self.color_main_vars.items():
            if hero in self.cfg.heroes:
                value = var.get().strip()
                if not value or theming.is_valid_hex(value):
                    self.cfg.heroes[hero].color_main = value
        for hero, var in self.color_accent_vars.items():
            if hero in self.cfg.heroes:
                value = var.get().strip()
                if not value or theming.is_valid_hex(value):
                    self.cfg.heroes[hero].color_accent = value
        self.cfg.save()

    def _append_log(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        if message.startswith("▶"):
            tag = "accent"
        elif any(w in message.lower() for w in
                 ("fail", "could not", "not found", "unavailable", "error")):
            tag = "warn"
        else:
            tag = None
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] ", ("muted",))
        self.log_text.insert("end", f"{message}\n", (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _refresh_status(self) -> None:
        running = self.monitor.running
        self.status_var.set("Running" if running else "Idle")
        if not running:
            self.game_var.set("Not detected")
        self.start_btn.configure(
            text="Stop monitoring" if running else "Start monitoring",
            **(DANGER_BTN if running else ACCENT_BTN),
        )
        connected = self.spotify.connected
        self.spotify_var.set("Connected" if connected else "Not connected")
        if hasattr(self, "dot_monitor"):
            self.dot_monitor.configure(
                text="● Running" if running else "● Idle",
                text_color=ACCENT if running else MUTED)
            self.dot_spotify.configure(
                text="● Spotify connected" if connected else "● Spotify offline",
                text_color=ACCENT if connected else MUTED)

    def _toggle_monitor(self) -> None:
        if self.monitor.running:
            self.monitor.stop()
        else:
            self._save_heroes(silent=True)
            self.monitor.start()
        self._refresh_status()

    def _manual_set_hero(self, hero: str) -> None:
        """Manually set the current hero and switch its playlist (no detection)."""
        if not hero or hero == "—":
            return
        self.hero_var.set(hero)
        self._update_stage(hero)  # Stage + session-stats playtime
        hc = self.cfg.heroes.get(hero)
        playlist = hc.playlist_uri if hc else ""
        if not playlist:
            self._append_log(f"{hero}: no playlist mapped — add one in the Heroes tab.")
            return
        if not self.spotify.connected:
            self._append_log(f"Set {hero}, but Spotify isn't connected.")
            return
        try:
            self.spotify.play_playlist(playlist)
            self._append_log(f"▶ Switched to {hero}'s playlist (manual).")
        except Exception as exc:
            self._append_log(f"Set {hero}, but playback failed: {exc}")

    def _apply_source_change(self, _value=None) -> None:
        """Persist the detection source immediately and apply it live."""
        src = self.source_var.get()
        if src == self.cfg.hero_source and not self.monitor.running:
            return
        self.cfg.hero_source = src
        self.cfg.save()
        self._append_log(f"Detection source set to '{src}'.")
        if self.monitor.running:
            self.monitor.stop()
            self.monitor.start()
            self._refresh_status()

    def _toggle_gep_debug(self) -> None:
        self.cfg.gep_debug = bool(self.gep_debug_var.get())
        self.cfg.save()
        self._append_log(f"GEP debug logging {'on' if self.cfg.gep_debug else 'off'}.")
        if self.monitor.running:  # re-launch the bridge so the change applies now
            self.monitor.stop()
            self.monitor.start()
            self._refresh_status()

    def _connect_spotify(self) -> None:
        self._save_settings(silent=True)
        self._append_log(
            "Connecting to Spotify — a browser will open. Approve access there. "
            f"(Redirect URI: {self.cfg.spotify.redirect_uri})")

        def worker() -> None:
            # Runs off the UI thread: spotipy blocks waiting for the browser
            # redirect, so doing this inline would freeze the window.
            try:
                self.spotify.connect()
                self._enqueue_log("Spotify connected.")
            except Exception as exc:
                self._enqueue_log(f"Spotify connection failed: {exc}")

        threading.Thread(target=worker, name="spotify-connect", daemon=True).start()

    # ----- Stage (second screen) -------------------------------------
    def _effective_accent(self, hero: str) -> str:
        hc = self.cfg.heroes.get(hero)
        if hc and hc.color_accent and theming.is_valid_hex(hc.color_accent):
            return hc.color_accent
        if hero in self._accent_cache:
            return self._accent_cache[hero]
        path = self.cfg.logo_path(hero) or self.cfg.avatar_path(hero)
        accent = theming.extract_accent(path) if path else theming.DEFAULT_ACCENT
        self._accent_cache[hero] = accent
        return accent

    def _effective_main(self, hero: str) -> str:
        """Main (background-glow) colour: explicit override, else a deep tone
        derived from the accent."""
        hc = self.cfg.heroes.get(hero)
        if hc and hc.color_main and theming.is_valid_hex(hc.color_main):
            return hc.color_main
        return theming.darken(self._effective_accent(hero), 0.45)

    def _open_stage(self) -> None:
        if self.stage and self.stage.alive:
            self.stage.top.deiconify()
            self.stage.top.lift()
            return
        self.stage = StageWindow(self.root, self.state, self.cfg, self.visualizer)
        if not self.visualizer.available:
            self._append_log(
                "Stage opened. Audio visualizer backend unavailable "
                "(install 'soundcard' on Windows for live audio bars).")
        else:
            self._append_log("Stage opened. Drag it to your second screen, F11 = fullscreen.")

    def _handle_game_event(self, event: dict) -> None:
        """Feed best-effort GEP match/KDA events into the session stats."""
        etype = event.get("type")
        if etype == "match" and event.get("result"):
            self.session_stats.note_match_result(event["result"])
            self._append_log(f"Match {event['result']} recorded.")
        elif etype == "stats":
            self.session_stats.note_kda(
                event.get("kills", 0), event.get("deaths", 0), event.get("assists", 0))
        elif etype == "game":
            running = bool(event.get("running"))
            self.game_var.set("Running ✓" if running else "Not running")
            self._append_log("Marvel Rivals " + ("detected and running." if running else "exited."))
        elif etype == "needs_admin" and not getattr(self, "_warned_admin", False):
            self._warned_admin = True
            messagebox.showwarning(
                "Run as administrator",
                "Marvel Rivals runs with anti-cheat (administrator), so RivalsRadio "
                "must also run as administrator to read game events.\n\n"
                "Close RivalsRadio, right-click it, and choose 'Run as administrator'.")

    def _update_stage(self, hero: str) -> None:
        accent = self._effective_accent(hero)
        main = self._effective_main(hero)
        self.state.set_hero(
            hero, self.cfg.avatar_path(hero), accent,
            logo_path=self.cfg.logo_path(hero),
            signature_path=self.cfg.signature_path(hero),
            main_hex=main)
        playlist = self.cfg.heroes[hero].playlist_uri if hero in self.cfg.heroes else ""
        self.session_stats.note_hero(hero, playlist)

    def _toggle_web_overlay(self, initial: bool = False) -> None:
        if self.web_overlay.running and not initial:
            self.web_overlay.stop()
            self.cfg.web_overlay_enabled = False
            self.cfg.save()
            self._append_log("OBS web overlay stopped.")
        else:
            self.web_overlay.port = self.cfg.web_overlay_port
            try:
                self.web_overlay.start()
            except Exception as exc:
                messagebox.showerror("Web overlay", f"Could not start server:\n{exc}")
                return
            self.cfg.web_overlay_enabled = True
            self.cfg.save()
            self._append_log(
                f"OBS web overlay running — add a Browser Source at {self.web_overlay.url}")
        if hasattr(self, "web_btn"):
            self.web_btn.configure(
                text="Stop OBS overlay" if self.web_overlay.running else "Start OBS overlay")

    def _open_hero_art(self, hero: str) -> None:
        """Per-hero Stage art: a centred logo (pulses) and a top-right signature."""
        win = ctk.CTkToplevel(self.root)
        win.title(f"Stage art — {hero}")
        win.geometry("420x260")
        win.configure(fg_color=CONTENT_BG)
        win.transient(self.root)
        win.after(200, lambda: win.grab_set() if win.winfo_exists() else None)

        ctk.CTkLabel(win, text=f"Stage art for {hero}", font=self.f_section,
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(win, text="PNG with transparent background works best.",
                     font=self.f_small, text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 10))

        def row(kind: str):
            hc = self.cfg.heroes.get(hero)
            cur = getattr(hc, kind, "") if hc else ""
            fr = ctk.CTkFrame(win, fg_color=CARD, corner_radius=10)
            fr.pack(fill="x", padx=18, pady=6)
            ctk.CTkLabel(fr, text=kind.capitalize(), width=90, anchor="w", font=self.f_bold,
                         text_color=TEXT).pack(side="left", padx=12, pady=10)
            status = ctk.CTkLabel(fr, text="✓ set" if cur else "not set", font=self.f_small,
                                  text_color=ACCENT if cur else MUTED)
            status.pack(side="left", padx=6)

            def choose():
                if self._choose_hero_image(hero, kind):
                    status.configure(text="✓ set", text_color=ACCENT)
                    self._mark_art_button(hero)

            def clear():
                setattr(self.cfg.heroes[hero], kind, "")
                self.cfg.save()
                self._accent_cache.pop(hero, None)
                status.configure(text="not set", text_color=MUTED)
                self._mark_art_button(hero)
                if self.hero_var.get() == hero:
                    self._update_stage(hero)

            ctk.CTkButton(fr, text="Choose…", width=80, height=30, font=self.f_small,
                          command=choose, **NEUTRAL_BTN).pack(side="right", padx=(4, 12))
            ctk.CTkButton(fr, text="Clear", width=60, height=30, font=self.f_small,
                          command=clear, **NEUTRAL_BTN).pack(side="right", padx=4)

        row("logo")
        row("signature")
        ctk.CTkButton(win, text="Done", height=34, font=self.f_bold,
                      command=win.destroy, **ACCENT_BTN).pack(pady=12)

    def _mark_art_button(self, hero: str) -> None:
        hc = self.cfg.heroes.get(hero)
        art_set = bool(hc and (hc.logo or hc.signature))
        btn = self.avatar_labels.get(hero)
        if btn:
            btn.configure(text="Art ✓" if art_set else "Art…",
                          **(ACCENT_BTN if art_set else NEUTRAL_BTN))

    def _choose_hero_image(self, hero: str, kind: str) -> bool:
        """Copy a chosen image into the logos/ or signatures/ dir. Returns success."""
        path = filedialog.askopenfilename(
            title=f"Choose {kind} for {hero}",
            filetypes=[("Images", "*.png *.webp *.jpg *.jpeg"), ("All files", "*.*")],
        )
        if not path:
            return False
        ext = os.path.splitext(path)[1].lower() or ".png"
        filename = f"{hero.replace(' ', '_').replace('&', 'and')}_{kind}{ext}"
        dest_dir = self.cfg.logos_dir if kind == "logo" else self.cfg.signatures_dir
        dest = os.path.join(dest_dir, filename)
        try:
            shutil.copyfile(path, dest)
        except OSError as exc:
            messagebox.showerror(kind.capitalize(), f"Could not copy image:\n{exc}")
            return False
        setattr(self.cfg.heroes[hero], kind, filename)
        self.cfg.save()
        self._accent_cache.pop(hero, None)
        self._append_log(f"{kind.capitalize()} set for {hero}.")
        if self.hero_var.get() == hero:
            self._update_stage(hero)
        return True

    def _add_hero(self) -> None:
        name = self.new_hero_var.get().strip()
        if not name:
            return
        if name in self.cfg.heroes:
            messagebox.showinfo("Add hero", f"{name} already exists.")
            return
        self.cfg.heroes[name] = HeroConfig()
        self.cfg.save()
        self.new_hero_var.set("")
        self._render_hero_rows()

    def _remove_hero(self, hero: str) -> None:
        if not messagebox.askyesno("Remove", f"Remove {hero}?"):
            return
        for path in (self.cfg.logo_path(hero), self.cfg.signature_path(hero),
                     self.cfg.avatar_path(hero)):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.cfg.heroes.pop(hero, None)
        self._accent_cache.pop(hero, None)
        self.cfg.save()
        self._render_hero_rows()

    def _save_heroes(self, silent: bool = False) -> None:
        for hero, var in self.playlist_vars.items():
            self.cfg.heroes[hero].playlist_uri = var.get().strip()
        for field, vars_map in (("color_main", self.color_main_vars),
                                ("color_accent", self.color_accent_vars)):
            for hero, var in vars_map.items():
                value = var.get().strip()
                if value and not theming.is_valid_hex(value):
                    messagebox.showwarning(
                        "Colour", f"'{value}' for {hero} is not a valid #RRGGBB colour.")
                    return
                setattr(self.cfg.heroes[hero], field, value)
                self._accent_cache.pop(hero, None)
        self.cfg.save()
        if not silent:
            self._append_log("Saved hero mappings (playlists + colours).")

    def _save_settings(self, silent: bool = False) -> None:
        self.cfg.spotify.client_id = self.client_id_var.get().strip()
        self.cfg.spotify.client_secret = self.client_secret_var.get().strip()
        self.cfg.spotify.redirect_uri = self.redirect_var.get().strip()
        self.cfg.spotify.device_name = self.device_var.get().strip()
        try:
            self.cfg.stage_fps = int(self.fps_var.get())
            self.cfg.web_overlay_port = int(self.web_port_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Settings", "Numeric fields must be numbers.")
            return
        self.cfg.show_now_playing = bool(self.nowplaying_var.get())
        self.cfg.hero_source = self.source_var.get()
        self.cfg.gep_bridge_cmd = self.bridge_cmd_var.get().strip()
        self.cfg.gep_packages_url = self.pkg_url_var.get().strip()
        self.cfg.gep_debug = bool(self.gep_debug_var.get())
        self.cfg.save()
        # Apply the new FPS to a live Stage immediately.
        if self.stage and self.stage.alive:
            self.stage.set_fps(self.cfg.stage_fps)
        if not silent:
            self._append_log("Settings saved.")

    def refresh_widgets_from_config(self) -> None:
        self.client_id_var.set(self.cfg.spotify.client_id)
        self.client_secret_var.set(self.cfg.spotify.client_secret)
        self.redirect_var.set(self.cfg.spotify.redirect_uri)
        self._refresh_status()

    def _on_close(self) -> None:
        self.monitor.stop()
        if self.stage:
            self.stage.close()
        self.visualizer.stop()
        self.nowplaying.stop()
        self.web_overlay.stop()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware so windows fill scaled monitors.

    On a 150%/200%-scaled display a DPI-unaware window is bitmap-stretched and
    its content ends up occupying only a fraction of the screen when maximised
    or fullscreened. Declaring per-monitor-v2 awareness makes Tk geometry map to
    physical pixels. Harmless / no-op off Windows."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        # PER_MONITOR_AWARE_V2 = -4
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        pass


def main() -> None:
    _enable_dpi_awareness()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
