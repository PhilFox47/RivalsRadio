"""Tkinter desktop UI for RivalsRadio."""

from __future__ import annotations

import os
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox

from .config import Config, HeroConfig
from .capture import ScreenGrabber
from .recognizer import save_reference
from .region_selector import select_region
from .spotify_controller import SpotifyController
from .monitor import Monitor

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
        self.monitor = Monitor(
            self.cfg, self.spotify,
            on_log=self._enqueue_log,
            on_hero=lambda hero: self._enqueue_log(f"▶ Now playing as: {hero}"),
        )

        self._build_ui()
        self._refresh_status()
        self.root.after(150, self._drain_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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
            text=("For each hero: paste the Spotify playlist URI, then while you're "
                  "in a match on that hero click 'Capture' to record its HUD."),
            wraplength=700, foreground="#555",
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
        self.ref_labels = {}
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
        self.ref_labels.clear()

        header = ttk.Frame(self.hero_rows)
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text="Hero", width=18, font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(header, text="Spotify playlist URI", font=("Segoe UI", 9, "bold")).pack(side="left")

        for hero in sorted(self.cfg.heroes):
            row = ttk.Frame(self.hero_rows)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=hero, width=18).pack(side="left")
            var = tk.StringVar(value=self.cfg.heroes[hero].playlist_uri)
            self.playlist_vars[hero] = var
            ttk.Entry(row, textvariable=var, width=42).pack(side="left", padx=4)
            ttk.Button(row, text="Capture", width=8,
                       command=lambda h=hero: self._capture_reference(h)).pack(side="left", padx=2)
            has_ref = "✓" if self.cfg.heroes[hero].reference else "—"
            lbl = ttk.Label(row, text=has_ref, width=3,
                            foreground=ACCENT if self.cfg.heroes[hero].reference else "#999")
            lbl.pack(side="left")
            self.ref_labels[hero] = lbl
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
        ttk.Label(cf, textvariable=self.region_var).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        ttk.Button(cf, text="Select region…", command=self._select_region).grid(row=0, column=2, padx=8)
        ttk.Button(cf, text="Preview", command=self._preview_region).grid(row=0, column=3, padx=8)

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

    def _drain_log_queue(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._append_log(msg)
                if msg.startswith("▶ Now playing as: "):
                    self.hero_var.set(msg.replace("▶ Now playing as: ", ""))
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
        ref = self.cfg.reference_path(hero)
        if ref and os.path.exists(ref):
            try:
                os.remove(ref)
            except OSError:
                pass
        self.cfg.heroes.pop(hero, None)
        self.cfg.save()
        self._render_hero_rows()

    def _save_heroes(self, silent: bool = False) -> None:
        for hero, var in self.playlist_vars.items():
            self.cfg.heroes[hero].playlist_uri = var.get().strip()
        self.cfg.save()
        if not silent:
            self._append_log("Saved hero → playlist mappings.")

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
        except (tk.TclError, ValueError):
            messagebox.showwarning("Settings", "Tuning values must be numbers.")
            return
        self.cfg.save()
        if not silent:
            self._append_log("Settings saved.")

    def _on_close(self) -> None:
        self.monitor.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
