"""Tkinter desktop UI for RivalsRadio."""

from __future__ import annotations

import os
import queue
import shutil
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

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

ACCENT = "#1DB954"  # Spotify green


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("RivalsRadio")
        self.root.geometry("760x620")
        self.root.minsize(680, 560)

        self.cfg = Config.load()
        self.spotify = SpotifyController(self.cfg.spotify)
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._hero_queue: "queue.Queue[str]" = queue.Queue()
        self.monitor = Monitor(
            self.cfg, self.spotify,
            on_log=self._enqueue_log,
            on_hero=self._on_hero_detected,
        )

        # Stage (second-screen) view + its audio source + shared state.
        self.visualizer = AudioVisualizer()
        self.state = StageState()
        self.state.attach_visualizer(self.visualizer)
        self.stage: "StageWindow | None" = None
        self._accent_cache: dict = {}  # hero -> auto-extracted accent hex

        # Now-playing poller (updates track info) and OBS web overlay.
        self.nowplaying = NowPlaying(self.spotify, self.state, on_log=self._enqueue_log)
        self.nowplaying.start()
        self.web_overlay = WebOverlay(self.state, self.cfg.web_overlay_port)

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
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_status_tab(nb)
        self._build_heroes_tab(nb)
        self._build_settings_tab(nb)

    # ----- Status tab -------------------------------------------------
    def _build_status_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Status")

        top = ttk.Frame(tab)
        top.pack(fill="x", padx=12, pady=12)

        self.status_var = tk.StringVar(value="Idle")
        self.hero_var = tk.StringVar(value="—")
        self.spotify_var = tk.StringVar(value="Not connected")

        ttk.Label(top, text="Monitoring:", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Current hero:", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w")
        ttk.Label(top, textvariable=self.hero_var).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Spotify:", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w")
        ttk.Label(top, textvariable=self.spotify_var).grid(row=2, column=1, sticky="w", padx=8)

        btns = ttk.Frame(tab)
        btns.pack(fill="x", padx=12, pady=4)
        self.start_btn = ttk.Button(btns, text="Start monitoring", command=self._toggle_monitor)
        self.start_btn.pack(side="left")
        ttk.Button(btns, text="Connect Spotify", command=self._connect_spotify).pack(side="left", padx=8)
        ttk.Button(btns, text="Test detection", command=self._test_detection).pack(side="left")
        ttk.Button(btns, text="Open Stage view", command=self._open_stage).pack(side="left", padx=8)

        ttk.Label(tab, text="Activity log:").pack(anchor="w", padx=12, pady=(12, 2))
        self.log_text = tk.Text(tab, height=14, state="disabled", wrap="word",
                                bg="#1e1e1e", fg="#dddddd", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # ----- Heroes tab -------------------------------------------------
    def _build_heroes_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Heroes")

        ttk.Label(
            tab,
            text=("For each hero: paste the Spotify playlist URI, and (while in a "
                  "match on that hero) click 'Capture' to record its HUD. Set an "
                  "Avatar image for the Stage view; the accent colour is read from "
                  "the avatar automatically, or type a #hex override."),
            wraplength=720, foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        # Scrollable list of heroes.
        container = ttk.Frame(tab)
        container.pack(fill="both", expand=True, padx=12, pady=4)
        canvas = tk.Canvas(container, highlightthickness=0)
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.hero_rows = ttk.Frame(canvas)
        self.hero_rows.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.hero_rows, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.playlist_vars = {}
        self.accent_vars = {}
        self.ref_labels = {}
        self.avatar_labels = {}
        self._render_hero_rows()

        add = ttk.Frame(tab)
        add.pack(fill="x", padx=12, pady=8)
        self.new_hero_var = tk.StringVar()
        ttk.Entry(add, textvariable=self.new_hero_var, width=24).pack(side="left")
        ttk.Button(add, text="Add hero", command=self._add_hero).pack(side="left", padx=6)
        ttk.Button(add, text="Save mappings", command=self._save_heroes).pack(side="right")

    def _render_hero_rows(self) -> None:
        for child in self.hero_rows.winfo_children():
            child.destroy()
        self.playlist_vars.clear()
        self.accent_vars.clear()
        self.ref_labels.clear()
        self.avatar_labels.clear()

        header = ttk.Frame(self.hero_rows)
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text="Hero", width=16, font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(header, text="Spotify playlist URI", font=("Segoe UI", 9, "bold")).pack(side="left")

        for hero in sorted(self.cfg.heroes):
            hc = self.cfg.heroes[hero]
            row = ttk.Frame(self.hero_rows)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=hero, width=16).pack(side="left")
            var = tk.StringVar(value=hc.playlist_uri)
            self.playlist_vars[hero] = var
            ttk.Entry(row, textvariable=var, width=34).pack(side="left", padx=4)
            ttk.Button(row, text="Capture", width=8,
                       command=lambda h=hero: self._capture_reference(h)).pack(side="left", padx=2)
            ref_lbl = ttk.Label(row, text="✓" if hc.reference else "—", width=2,
                                foreground=ACCENT if hc.reference else "#999")
            ref_lbl.pack(side="left")
            self.ref_labels[hero] = ref_lbl
            ttk.Button(row, text="Avatar", width=7,
                       command=lambda h=hero: self._choose_avatar(h)).pack(side="left", padx=2)
            av_lbl = ttk.Label(row, text="✓" if hc.avatar else "—", width=2,
                               foreground=ACCENT if hc.avatar else "#999")
            av_lbl.pack(side="left")
            self.avatar_labels[hero] = av_lbl
            acc = tk.StringVar(value=hc.accent)
            self.accent_vars[hero] = acc
            ttk.Entry(row, textvariable=acc, width=8).pack(side="left", padx=2)
            ttk.Button(row, text="✕", width=2,
                       command=lambda h=hero: self._remove_hero(h)).pack(side="left", padx=2)

    # ----- Settings tab ----------------------------------------------
    def _build_settings_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Settings")

        # Spotify credentials.
        sf = ttk.LabelFrame(tab, text="Spotify (Premium required)")
        sf.pack(fill="x", padx=12, pady=12)
        self.client_id_var = tk.StringVar(value=self.cfg.spotify.client_id)
        self.client_secret_var = tk.StringVar(value=self.cfg.spotify.client_secret)
        self.redirect_var = tk.StringVar(value=self.cfg.spotify.redirect_uri)
        self.device_var = tk.StringVar(value=self.cfg.spotify.device_name)
        self._labeled_entry(sf, "Client ID", self.client_id_var, 0)
        self._labeled_entry(sf, "Client Secret", self.client_secret_var, 1, show="•")
        self._labeled_entry(sf, "Redirect URI", self.redirect_var, 2)
        self._labeled_entry(sf, "Device name (optional)", self.device_var, 3)

        # Capture region.
        cf = ttk.LabelFrame(tab, text="HUD capture region")
        cf.pack(fill="x", padx=12, pady=8)
        self.region_var = tk.StringVar()
        ttk.Label(cf, textvariable=self.region_var).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=6)
        ttk.Button(cf, text="Auto-detect game", command=self._auto_find_region).grid(row=1, column=0, padx=8, pady=(0, 6))
        ttk.Button(cf, text="Select region…", command=self._select_region).grid(row=1, column=1, padx=8, pady=(0, 6))
        ttk.Button(cf, text="Preview", command=self._preview_region).grid(row=1, column=2, padx=8, pady=(0, 6))

        # Stage / presentation.
        pf = ttk.LabelFrame(tab, text="Stage & overlay")
        pf.pack(fill="x", padx=12, pady=8)
        ttk.Label(pf, text="Visualizer style").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.style_var = tk.StringVar(value=self.cfg.stage_style)
        ttk.Combobox(pf, textvariable=self.style_var, width=12, state="readonly",
                     values=["bars", "mirror", "radial"]).grid(row=0, column=1, sticky="w", padx=8)
        self.nowplaying_var = tk.BooleanVar(value=self.cfg.show_now_playing)
        ttk.Checkbutton(pf, text="Show now-playing (track + album art)",
                        variable=self.nowplaying_var).grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        ttk.Label(pf, text="OBS overlay port").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.web_port_var = tk.IntVar(value=self.cfg.web_overlay_port)
        ttk.Entry(pf, textvariable=self.web_port_var, width=10).grid(row=2, column=1, sticky="w", padx=8)
        self.web_btn = ttk.Button(
            pf, text="Stop OBS overlay" if self.web_overlay.running else "Start OBS overlay",
            command=self._toggle_web_overlay)
        self.web_btn.grid(row=3, column=0, padx=8, pady=4, sticky="w")

        # Tuning.
        tf = ttk.LabelFrame(tab, text="Detection tuning")
        tf.pack(fill="x", padx=12, pady=8)
        self.threshold_var = tk.DoubleVar(value=self.cfg.match_threshold)
        self.interval_var = tk.DoubleVar(value=self.cfg.poll_interval)
        self.confirm_var = tk.IntVar(value=self.cfg.confirm_count)
        self._labeled_entry(tf, "Match threshold (0–1)", self.threshold_var, 0)
        self._labeled_entry(tf, "Poll interval (s)", self.interval_var, 1)
        self._labeled_entry(tf, "Confirm count", self.confirm_var, 2)

        ttk.Button(tab, text="Save settings", command=self._save_settings).pack(pady=12)

        self._update_region_label()

    def _labeled_entry(self, parent, label, var, row, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        entry = ttk.Entry(parent, textvariable=var, width=48, show=show)
        entry.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        return entry

    # --------------------------------------------------------------- actions
    def _enqueue_log(self, message: str) -> None:
        self._log_queue.put(message)

    def _on_hero_detected(self, hero: str) -> None:
        """Monitor callback (background thread): log + signal the Stage."""
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
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _refresh_status(self) -> None:
        self.status_var.set("Running" if self.monitor.running else "Idle")
        self.start_btn.config(
            text="Stop monitoring" if self.monitor.running else "Start monitoring"
        )
        self.spotify_var.set("Connected" if self.spotify.connected else "Not connected")

    def _toggle_monitor(self) -> None:
        if self.monitor.running:
            self.monitor.stop()
        else:
            self._save_heroes(silent=True)
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
        """Capture once and report the top hero matches with their scores.

        Use this during calibration: a confident detection has a high top score
        and a clear gap to the runner-up. If two heroes score close together,
        re-capture one with a more distinctive HUD region.
        """
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
        """Resolve a hero's accent: manual override > auto from avatar > default."""
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
        """Push the current hero/accent into shared state (Stage + web overlay)."""
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
            self.web_btn.config(
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
        self._accent_cache.pop(hero, None)  # re-extract next time
        self.avatar_labels[hero].config(text="✓", foreground=ACCENT)
        self._append_log(f"Avatar set for {hero} (accent: {self._effective_accent(hero)}).")
        # Live-update the Stage if it's showing this hero.
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
        self.ref_labels[hero].config(text="✓", foreground=ACCENT)
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
            self._accent_cache.pop(hero, None)  # let override take effect
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
            # frame is BGR; convert to RGB for PIL.
            rgb = frame[:, :, ::-1]
            img = Image.fromarray(rgb)
            win = tk.Toplevel(self.root)
            win.title("Region preview")
            photo = ImageTk.PhotoImage(img)
            lbl = ttk.Label(win, image=photo)
            lbl.image = photo  # keep a reference
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
        """Re-sync settings widgets after the wizard (or auto-detect) changes cfg."""
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
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
