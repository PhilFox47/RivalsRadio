"""First-run setup wizard: Spotify -> capture region -> calibration pointer."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from . import gamewindow
from .region_selector import select_region


class SetupWizard:
    def __init__(self, parent: tk.Misc, app) -> None:
        self.app = app
        self.cfg = app.cfg
        self.step = 0
        self.steps = [self._step_spotify, self._step_region, self._step_calibrate]

        self.top = tk.Toplevel(parent)
        self.top.title("RivalsRadio — Setup")
        self.top.geometry("560x420")
        self.top.transient(parent)
        self.top.grab_set()

        self.body = ttk.Frame(self.top, padding=16)
        self.body.pack(fill="both", expand=True)

        nav = ttk.Frame(self.top, padding=(16, 0, 16, 12))
        nav.pack(fill="x")
        self.back_btn = ttk.Button(nav, text="Back", command=self._back)
        self.back_btn.pack(side="left")
        ttk.Button(nav, text="Skip setup", command=self._finish).pack(side="left", padx=8)
        self.next_btn = ttk.Button(nav, text="Next", command=self._next)
        self.next_btn.pack(side="right")

        self._render()

    # ------------------------------------------------------------------
    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.steps[self.step]()
        self.back_btn.config(state="normal" if self.step > 0 else "disabled")
        self.next_btn.config(
            text="Finish" if self.step == len(self.steps) - 1 else "Next")

    def _next(self) -> None:
        if self.step == 0:
            self._save_spotify()
        if self.step < len(self.steps) - 1:
            self.step += 1
            self._render()
        else:
            self._finish()

    def _back(self) -> None:
        if self.step > 0:
            self.step -= 1
            self._render()

    def _finish(self) -> None:
        self.cfg.setup_complete = True
        self.cfg.save()
        self.app.refresh_widgets_from_config()
        self.top.destroy()

    # ----- steps ------------------------------------------------------
    def _step_spotify(self) -> None:
        ttk.Label(self.body, text="Step 1 — Connect Spotify",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(self.body, wraplength=510, foreground="#555", text=(
            "RivalsRadio needs Spotify Premium and a free developer app.\n"
            "1. Open developer.spotify.com/dashboard → Create app.\n"
            "2. Set the Redirect URI to exactly http://localhost:8888/callback.\n"
            "3. Paste the Client ID and Secret below, then Connect."
        )).pack(anchor="w", pady=(4, 10))

        self.w_id = tk.StringVar(value=self.cfg.spotify.client_id)
        self.w_secret = tk.StringVar(value=self.cfg.spotify.client_secret)
        for label, var, show in [("Client ID", self.w_id, None),
                                 ("Client Secret", self.w_secret, "•")]:
            row = ttk.Frame(self.body)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=14).pack(side="left")
            ttk.Entry(row, textvariable=var, show=show).pack(side="left", fill="x", expand=True)

        ttk.Button(self.body, text="Connect Spotify",
                   command=self._connect).pack(anchor="w", pady=8)
        self.sp_status = ttk.Label(
            self.body, text="Connected ✓" if self.app.spotify.connected else "Not connected",
            foreground="#1DB954" if self.app.spotify.connected else "#999")
        self.sp_status.pack(anchor="w")

    def _save_spotify(self) -> None:
        self.cfg.spotify.client_id = self.w_id.get().strip()
        self.cfg.spotify.client_secret = self.w_secret.get().strip()
        self.cfg.save()

    def _connect(self) -> None:
        self._save_spotify()
        try:
            self.app.spotify.connect()
            self.sp_status.config(text="Connected ✓", foreground="#1DB954")
        except Exception as exc:
            messagebox.showerror("Spotify", f"Could not connect:\n{exc}")

    def _step_region(self) -> None:
        ttk.Label(self.body, text="Step 2 — Set the HUD capture region",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(self.body, wraplength=510, foreground="#555", text=(
            "RivalsRadio reads a small part of your screen to tell which hero "
            "you're on. With Marvel Rivals running, auto-detect the window for a "
            "suggested region, then fine-tune by dragging a tight box around the "
            "hero portrait in the bottom-left corner (your hero's face). It's a "
            "steadier anchor than the ability icons, which change with cooldowns."
        )).pack(anchor="w", pady=(4, 10))

        ttk.Button(self.body, text="Auto-detect game window",
                   command=self._auto_region).pack(anchor="w", pady=3)
        ttk.Button(self.body, text="Select region manually…",
                   command=self._manual_region).pack(anchor="w", pady=3)
        self.region_lbl = ttk.Label(self.body, text=self._region_text())
        self.region_lbl.pack(anchor="w", pady=8)

    def _region_text(self) -> str:
        r = self.cfg.capture_region
        return (f"Current region: {r.width}×{r.height} at ({r.left}, {r.top})"
                if r.is_valid() else "Current region: not set")

    def _auto_region(self) -> None:
        if not gamewindow.backend_available():
            messagebox.showinfo("Auto-detect",
                                "Window detection needs the 'pygetwindow' package.")
            return
        rect = gamewindow.find_game_rect(self.cfg.game_window_title)
        if not rect:
            messagebox.showinfo(
                "Auto-detect",
                f"Couldn't find a window matching '{self.cfg.game_window_title}'. "
                "Make sure Marvel Rivals is running (windowed/borderless helps).")
            return
        self.cfg.capture_region = gamewindow.suggest_hud_region(rect)
        self.cfg.save()
        self.app.refresh_widgets_from_config()
        self.region_lbl.config(text=self._region_text())

    def _manual_region(self) -> None:
        region = select_region(self.top)
        if region:
            self.cfg.capture_region = region
            self.cfg.save()
            self.app.refresh_widgets_from_config()
            self.region_lbl.config(text=self._region_text())

    def _step_calibrate(self) -> None:
        ttk.Label(self.body, text="Step 3 — Calibrate your heroes",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(self.body, wraplength=510, foreground="#555", text=(
            "Almost done! In the Heroes tab:\n\n"
            "• Paste a Spotify playlist URI for each hero you main.\n"
            "• While in a match on that hero, click 'Capture' to record its HUD.\n"
            "• Optionally set an Avatar image for the Stage view.\n\n"
            "Then press Start monitoring on the Status tab and play. Use "
            "'Test detection' to check a hero is recognised confidently.\n\n"
            "Click Finish to close this wizard."
        )).pack(anchor="w", pady=(4, 10))
