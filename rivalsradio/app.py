"""Modern CustomTkinter desktop UI for RivalsRadio."""

from __future__ import annotations

import os
import queue
import shutil
import time
import tkinter as tk
from tkinter import messagebox, filedialog

import customtkinter as ctk

from . import theming, gamewindow
from .config import Config, HeroConfig
from .capture import ScreenGrabber
from .recognizer import save_reference
from .region_selector import select_region
from .spotify_controller import SpotifyController
from .monitor import Monitor
from .audio_visualizer import AudioVisualizer
from .stage import StageWindow
from .stage_state import StageState
from .nowplaying import NowPlaying
from .web_overlay import WebOverlay
from .wizard import SetupWizard

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
        self.monitor = Monitor(
            self.cfg, self.spotify,
            on_log=self._enqueue_log,
            on_hero=self._on_hero_detected,
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

        for i, name in enumerate(("Status", "Heroes", "Settings")):
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
        rows = [("Monitoring", self.status_var), ("Current hero", self.hero_var),
                ("Spotify", self.spotify_var)]
        for r, (label, var) in enumerate(rows):
            ctk.CTkLabel(grid, text=label, font=self.f_bold, text_color=MUTED,
                         width=130, anchor="w").grid(row=r, column=0, sticky="w", pady=3)
            ctk.CTkLabel(grid, textvariable=var, font=self.f_body, text_color=TEXT,
                         anchor="w").grid(row=r, column=1, sticky="w", pady=3)

        # Detection source as a modern segmented control.
        src = self._card(page, "Detection source")
        seg = ctk.CTkSegmentedButton(
            src, values=["auto", "gep", "screen"], variable=self.source_var,
            command=self._apply_source_change, font=self.f_bold,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=CARD_HI, unselected_hover_color=NEUTRAL_HOVER,
            text_color=TEXT, height=34,
        )
        seg.pack(anchor="w", padx=18, pady=(4, 4))
        ctk.CTkLabel(src, text="auto = Overwolf GEP, automatically falls back to screen capture",
                     font=self.f_small, text_color=MUTED).pack(anchor="w", padx=18, pady=(0, 14))

        # Action buttons.
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(2, 8))
        self.start_btn = ctk.CTkButton(actions, text="Start monitoring", height=40, width=160,
                                       font=self.f_bold, command=self._toggle_monitor, **ACCENT_BTN)
        self.start_btn.pack(side="left")
        ctk.CTkButton(actions, text="Connect Spotify", height=40, font=self.f_bold,
                      command=self._connect_spotify, **NEUTRAL_BTN).pack(side="left", padx=8)
        ctk.CTkButton(actions, text="Test detection", height=40, font=self.f_bold,
                      command=self._test_detection, **NEUTRAL_BTN).pack(side="left")
        ctk.CTkButton(actions, text="Open Stage view", height=40, font=self.f_bold,
                      command=self._open_stage, **NEUTRAL_BTN).pack(side="left", padx=8)

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
            text=("For each hero: paste the Spotify playlist URI, and (in a match on that "
                  "hero) click Capture to record its HUD. Set an Avatar for the Stage view; "
                  "the accent colour is read from it automatically, or type a #hex override."),
        ).pack(anchor="w", padx=24, pady=(0, 8))

        self.hero_rows = ctk.CTkScrollableFrame(page, fg_color=CARD, corner_radius=14)
        self.hero_rows.pack(fill="both", expand=True, padx=24, pady=4)

        self.playlist_vars = {}
        self.accent_vars = {}
        self.ref_labels = {}
        self.avatar_labels = {}
        self._render_hero_rows()

        add = ctk.CTkFrame(page, fg_color="transparent")
        add.pack(fill="x", padx=24, pady=(8, 24))
        self.new_hero_var = tk.StringVar()
        ctk.CTkEntry(add, textvariable=self.new_hero_var, width=220, height=36,
                     placeholder_text="New hero name", fg_color=CARD_HI, border_width=0).pack(side="left")
        ctk.CTkButton(add, text="Add hero", height=36, font=self.f_bold,
                      command=self._add_hero, **NEUTRAL_BTN).pack(side="left", padx=8)
        ctk.CTkButton(add, text="Save mappings", height=36, width=150, font=self.f_bold,
                      command=self._save_heroes, **ACCENT_BTN).pack(side="right")

    def _render_hero_rows(self) -> None:
        for child in self.hero_rows.winfo_children():
            child.destroy()
        self.playlist_vars.clear()
        self.accent_vars.clear()
        self.ref_labels.clear()
        self.avatar_labels.clear()

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
            ctk.CTkButton(row, text="Capture", width=72, height=32, font=self.f_small,
                          command=lambda h=hero: self._capture_reference(h),
                          **NEUTRAL_BTN).pack(side="left", padx=2)
            ref_lbl = ctk.CTkLabel(row, text="✓" if hc.reference else "—", width=16,
                                   text_color=ACCENT if hc.reference else MUTED, font=self.f_bold)
            ref_lbl.pack(side="left", padx=(2, 4))
            self.ref_labels[hero] = ref_lbl
            ctk.CTkButton(row, text="Avatar", width=64, height=32, font=self.f_small,
                          command=lambda h=hero: self._choose_avatar(h),
                          **NEUTRAL_BTN).pack(side="left", padx=2)
            av_lbl = ctk.CTkLabel(row, text="✓" if hc.avatar else "—", width=16,
                                  text_color=ACCENT if hc.avatar else MUTED, font=self.f_bold)
            av_lbl.pack(side="left", padx=(2, 4))
            self.avatar_labels[hero] = av_lbl
            acc = tk.StringVar(value=hc.accent)
            self.accent_vars[hero] = acc
            ctk.CTkEntry(row, textvariable=acc, width=80, height=32, fg_color=CARD,
                         border_width=0, placeholder_text="#hex").pack(side="left", padx=2)
            ctk.CTkButton(row, text="✕", width=32, height=32, font=self.f_bold,
                          fg_color="transparent", hover_color=DANGER, text_color=MUTED,
                          command=lambda h=hero: self._remove_hero(h)).pack(side="left", padx=(2, 10))

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
            df, values=["auto", "gep", "screen"], variable=self.source_var,
            command=self._apply_source_change, font=self.f_bold,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=CARD_HI, unselected_hover_color=NEUTRAL_HOVER,
            text_color=TEXT, height=34,
        ).pack(anchor="w", padx=18, pady=4)
        self.bridge_cmd_var = tk.StringVar(value=self.cfg.gep_bridge_cmd)
        self._labeled_entry(df, "GEP bridge command", self.bridge_cmd_var,
                            placeholder="blank = use the bundled bridge")
        ctk.CTkFrame(df, fg_color="transparent", height=8).pack()

        # Capture region.
        cf = self._card(scroll, "HUD capture region")
        self.region_var = tk.StringVar()
        ctk.CTkLabel(cf, textvariable=self.region_var, font=self.f_body,
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(2, 6))
        rbtns = ctk.CTkFrame(cf, fg_color="transparent")
        rbtns.pack(anchor="w", padx=18, pady=(0, 14))
        ctk.CTkButton(rbtns, text="Auto-detect game", height=34, font=self.f_small,
                      command=self._auto_find_region, **NEUTRAL_BTN).pack(side="left")
        ctk.CTkButton(rbtns, text="Select region…", height=34, font=self.f_small,
                      command=self._select_region, **NEUTRAL_BTN).pack(side="left", padx=8)
        ctk.CTkButton(rbtns, text="Preview", height=34, font=self.f_small,
                      command=self._preview_region, **NEUTRAL_BTN).pack(side="left")

        # Stage & overlay.
        pf = self._card(scroll, "Stage & overlay")
        srow = ctk.CTkFrame(pf, fg_color="transparent")
        srow.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(srow, text="Visualizer style", font=self.f_body, text_color=MUTED,
                     width=170, anchor="w").pack(side="left")
        self.style_var = tk.StringVar(value=self.cfg.stage_style)
        ctk.CTkOptionMenu(srow, values=["bars", "mirror", "radial"], variable=self.style_var,
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

        # Tuning.
        tf = self._card(scroll, "Detection tuning")
        self.threshold_var = tk.DoubleVar(value=self.cfg.match_threshold)
        self.interval_var = tk.DoubleVar(value=self.cfg.poll_interval)
        self.confirm_var = tk.IntVar(value=self.cfg.confirm_count)
        self._labeled_entry(tf, "Match threshold (0–1)", self.threshold_var)
        self._labeled_entry(tf, "Poll interval (s)", self.interval_var)
        self._labeled_entry(tf, "Confirm count", self.confirm_var)
        ctk.CTkFrame(tf, fg_color="transparent", height=8).pack()

        ctk.CTkButton(scroll, text="Save settings", height=40, width=170, font=self.f_bold,
                      command=self._save_settings, **ACCENT_BTN).pack(pady=18)

        self._update_region_label()

    # --------------------------------------------------------------- actions
    def _enqueue_log(self, message: str) -> None:
        self._log_queue.put(message)

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
        self._refresh_status()
        self.root.after(250, self._drain_log_queue)

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

    def _connect_spotify(self) -> None:
        self._save_settings(silent=True)
        try:
            self.spotify.connect()
            self._append_log("Spotify connected.")
        except Exception as exc:
            messagebox.showerror("Spotify", f"Could not connect:\n{exc}")
            self._append_log(f"Spotify connection failed: {exc}")
        self._refresh_status()

    def _test_detection(self) -> None:
        if not self.cfg.capture_region.is_valid():
            messagebox.showwarning("Test", "Set the HUD capture region first.")
            return
        refs = self.cfg.heroes_with_references()
        if not refs:
            messagebox.showinfo("Test", "No calibrated heroes yet. Capture some first.")
            return
        try:
            grabber = ScreenGrabber()
            frame = grabber.grab(self.cfg.capture_region)
            grabber.close()
        except Exception as exc:
            messagebox.showerror("Test", f"Screen capture failed:\n{exc}")
            return
        from .recognizer import HeroRecognizer
        recognizer = HeroRecognizer()
        recognizer.load_references(refs)
        ranked = recognizer.rank_matches(frame)[:3]
        thr = self.cfg.match_threshold
        lines = []
        for i, (hero, score) in enumerate(ranked):
            mark = "✓" if (i == 0 and score >= thr) else " "
            lines.append(f"  {mark} {hero}: {score:.3f}")
        verdict = (
            f"Would switch to: {ranked[0][0]}"
            if ranked and ranked[0][1] >= thr
            else f"No confident match (threshold {thr:.2f})"
        )
        self._append_log("Test detection — " + verdict)
        for line in lines:
            self._append_log(line)

    # ----- Stage (second screen) -------------------------------------
    def _effective_accent(self, hero: str) -> str:
        hc = self.cfg.heroes.get(hero)
        if hc and hc.accent and theming.is_valid_hex(hc.accent):
            return hc.accent
        if hero in self._accent_cache:
            return self._accent_cache[hero]
        path = self.cfg.avatar_path(hero)
        accent = theming.extract_accent(path) if path else theming.DEFAULT_ACCENT
        self._accent_cache[hero] = accent
        return accent

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

    def _update_stage(self, hero: str) -> None:
        path = self.cfg.avatar_path(hero)
        accent = self._effective_accent(hero)
        self.state.set_hero(hero, path, accent)

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

    def _choose_avatar(self, hero: str) -> None:
        path = filedialog.askopenfilename(
            title=f"Choose avatar for {hero}",
            filetypes=[("Images", "*.png *.webp *.jpg *.jpeg"), ("All files", "*.*")],
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower() or ".png"
        filename = f"{hero.replace(' ', '_').replace('&', 'and')}{ext}"
        dest = os.path.join(self.cfg.avatars_dir, filename)
        try:
            shutil.copyfile(path, dest)
        except OSError as exc:
            messagebox.showerror("Avatar", f"Could not copy image:\n{exc}")
            return
        self.cfg.heroes[hero].avatar = filename
        self.cfg.save()
        self._accent_cache.pop(hero, None)
        self.avatar_labels[hero].configure(text="✓", text_color=ACCENT)
        self._append_log(f"Avatar set for {hero} (accent: {self._effective_accent(hero)}).")
        if self.hero_var.get() == hero:
            self._update_stage(hero)

    def _capture_reference(self, hero: str) -> None:
        if not self.cfg.capture_region.is_valid():
            messagebox.showwarning(
                "No region", "Set the HUD capture region in Settings first.")
            return
        try:
            grabber = ScreenGrabber()
            frame = grabber.grab(self.cfg.capture_region)
            grabber.close()
        except Exception as exc:
            messagebox.showerror("Capture", f"Screen capture failed:\n{exc}")
            return
        if frame is None:
            messagebox.showerror("Capture", "Captured an empty frame.")
            return
        filename = f"{hero.replace(' ', '_').replace('&', 'and')}.png"
        path = os.path.join(self.cfg.references_dir, filename)
        save_reference(frame, path)
        self.cfg.heroes[hero].reference = filename
        self.cfg.save()
        self.ref_labels[hero].configure(text="✓", text_color=ACCENT)
        self._append_log(f"Captured HUD reference for {hero}.")

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
        for path in (self.cfg.reference_path(hero), self.cfg.avatar_path(hero)):
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
        for hero, var in self.accent_vars.items():
            value = var.get().strip()
            if value and not theming.is_valid_hex(value):
                messagebox.showwarning(
                    "Accent", f"'{value}' for {hero} is not a valid #RRGGBB colour.")
                return
            self.cfg.heroes[hero].accent = value
            self._accent_cache.pop(hero, None)
        self.cfg.save()
        if not silent:
            self._append_log("Saved hero mappings (playlists + accents).")

    def _select_region(self) -> None:
        region = select_region(self.root)
        if region:
            self.cfg.capture_region = region
            self.cfg.save()
            self._update_region_label()
            self._append_log(
                f"Capture region set: {region.width}×{region.height} "
                f"at ({region.left}, {region.top})."
            )

    def _update_region_label(self) -> None:
        r = self.cfg.capture_region
        if r.is_valid():
            self.region_var.set(f"{r.width}×{r.height} at ({r.left}, {r.top})")
        else:
            self.region_var.set("Not set")

    def _preview_region(self) -> None:
        if not self.cfg.capture_region.is_valid():
            messagebox.showwarning("Preview", "Set a region first.")
            return
        try:
            from PIL import Image, ImageTk
            grabber = ScreenGrabber()
            frame = grabber.grab(self.cfg.capture_region)
            grabber.close()
            rgb = frame[:, :, ::-1]
            img = Image.fromarray(rgb)
            win = ctk.CTkToplevel(self.root)
            win.title("Region preview")
            photo = ImageTk.PhotoImage(img)
            lbl = tk.Label(win, image=photo, bd=0)
            lbl.image = photo
            lbl.pack()
        except Exception as exc:
            messagebox.showerror("Preview", f"Preview failed:\n{exc}")

    def _save_settings(self, silent: bool = False) -> None:
        self.cfg.spotify.client_id = self.client_id_var.get().strip()
        self.cfg.spotify.client_secret = self.client_secret_var.get().strip()
        self.cfg.spotify.redirect_uri = self.redirect_var.get().strip()
        self.cfg.spotify.device_name = self.device_var.get().strip()
        try:
            self.cfg.match_threshold = float(self.threshold_var.get())
            self.cfg.poll_interval = float(self.interval_var.get())
            self.cfg.confirm_count = int(self.confirm_var.get())
            self.cfg.web_overlay_port = int(self.web_port_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Settings", "Numeric fields must be numbers.")
            return
        self.cfg.stage_style = self.style_var.get()
        self.cfg.show_now_playing = bool(self.nowplaying_var.get())
        self.cfg.hero_source = self.source_var.get()
        self.cfg.gep_bridge_cmd = self.bridge_cmd_var.get().strip()
        self.cfg.save()
        if not silent:
            self._append_log("Settings saved.")

    def _auto_find_region(self) -> None:
        if not gamewindow.backend_available():
            messagebox.showinfo(
                "Auto-detect", "Window detection needs the 'pygetwindow' package "
                "(included in the Windows build).")
            return
        rect = gamewindow.find_game_rect(self.cfg.game_window_title)
        if not rect:
            messagebox.showinfo(
                "Auto-detect",
                f"Couldn't find a window matching '{self.cfg.game_window_title}'. "
                "Make sure Marvel Rivals is running.")
            return
        self.cfg.capture_region = gamewindow.suggest_hud_region(rect)
        self.cfg.save()
        self._update_region_label()
        self._append_log(
            "Auto-detected game window; suggested a HUD region. "
            "Use Preview / Select region to fine-tune, then re-capture references.")

    def refresh_widgets_from_config(self) -> None:
        self.client_id_var.set(self.cfg.spotify.client_id)
        self.client_secret_var.set(self.cfg.spotify.client_secret)
        self.redirect_var.set(self.cfg.spotify.redirect_uri)
        self._update_region_label()
        self._refresh_status()

    def _on_close(self) -> None:
        self.monitor.stop()
        if self.stage:
            self.stage.close()
        self.visualizer.stop()
        self.nowplaying.stop()
        self.web_overlay.stop()
        self.root.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
