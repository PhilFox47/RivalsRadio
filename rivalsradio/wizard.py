"""First-run setup wizard: Spotify -> capture region -> calibration pointer."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from . import gamewindow
from .region_selector import select_region

ACCENT = "#1DB954"
ACCENT_HOVER = "#1ed760"
ACCENT_INK = "#06210f"
NEUTRAL = "#34373e"
NEUTRAL_HOVER = "#40444d"
TEXT = "#e9ebed"
MUTED = "#8b9096"
CARD_HI = "#2b2d33"
BG = "#1b1c20"

ACCENT_BTN = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_INK)
NEUTRAL_BTN = dict(fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=TEXT)


class SetupWizard:
    def __init__(self, parent: tk.Misc, app) -> None:
        self.app = app
        self.cfg = app.cfg
        self.step = 0
        self.steps = [self._step_spotify, self._step_region, self._step_calibrate]

        self.f_h = ctk.CTkFont(size=18, weight="bold")
        self.f_body = ctk.CTkFont(size=13)
        self.f_bold = ctk.CTkFont(size=13, weight="bold")

        self.top = ctk.CTkToplevel(parent)
        self.top.title("RivalsRadio — Setup")
        self.top.geometry("600x460")
        self.top.configure(fg_color=BG)
        self.top.transient(parent)
        # grab_set can fire before the Toplevel is viewable; defer it.
        self.top.after(200, self._grab)

        self.body = ctk.CTkFrame(self.top, fg_color=BG)
        self.body.pack(fill="both", expand=True, padx=20, pady=16)

        nav = ctk.CTkFrame(self.top, fg_color=BG)
        nav.pack(fill="x", padx=20, pady=(0, 16))
        self.back_btn = ctk.CTkButton(nav, text="Back", width=90, height=36,
                                      font=self.f_bold, command=self._back, **NEUTRAL_BTN)
        self.back_btn.pack(side="left")
        ctk.CTkButton(nav, text="Skip setup", width=110, height=36, font=self.f_bold,
                      command=self._finish, **NEUTRAL_BTN).pack(side="left", padx=8)
        self.next_btn = ctk.CTkButton(nav, text="Next", width=110, height=36,
                                      font=self.f_bold, command=self._next, **ACCENT_BTN)
        self.next_btn.pack(side="right")

        self._render()

    def _grab(self) -> None:
        try:
            self.top.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.steps[self.step]()
        self.back_btn.configure(state="normal" if self.step > 0 else "disabled")
        self.next_btn.configure(
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

    def _title(self, text: str) -> None:
        ctk.CTkLabel(self.body, text=text, font=self.f_h, text_color=TEXT).pack(anchor="w")

    def _hint(self, text: str) -> None:
        ctk.CTkLabel(self.body, text=text, font=self.f_body, text_color=MUTED,
                     justify="left", wraplength=540).pack(anchor="w", pady=(6, 12))

    # ----- steps ------------------------------------------------------
    def _step_spotify(self) -> None:
        self._title("Step 1 — Connect Spotify")
        self._hint(
            "RivalsRadio needs Spotify Premium and a free developer app.\n"
            "1. Open developer.spotify.com/dashboard → Create app.\n"
            "2. Set the Redirect URI to exactly http://127.0.0.1:8888/callback\n"
            "   (Spotify no longer accepts 'localhost' — use the IP 127.0.0.1).\n"
            "3. Paste the Client ID and Secret below, then Connect.")

        self.w_id = tk.StringVar(value=self.cfg.spotify.client_id)
        self.w_secret = tk.StringVar(value=self.cfg.spotify.client_secret)
        for label, var, show in [("Client ID", self.w_id, None),
                                 ("Client Secret", self.w_secret, "•")]:
            row = ctk.CTkFrame(self.body, fg_color="transparent")
            row.pack(fill="x", pady=4)
            ctk.CTkLabel(row, text=label, width=120, anchor="w", font=self.f_body,
                         text_color=MUTED).pack(side="left")
            ctk.CTkEntry(row, textvariable=var, show=show, height=34, fg_color=CARD_HI,
                         border_width=0).pack(side="left", fill="x", expand=True)

        ctk.CTkButton(self.body, text="Connect Spotify", height=36, font=self.f_bold,
                      command=self._connect, **ACCENT_BTN).pack(anchor="w", pady=10)
        self.sp_status = ctk.CTkLabel(
            self.body, font=self.f_bold,
            text="Connected ✓" if self.app.spotify.connected else "Not connected",
            text_color=ACCENT if self.app.spotify.connected else MUTED)
        self.sp_status.pack(anchor="w")

    def _save_spotify(self) -> None:
        self.cfg.spotify.client_id = self.w_id.get().strip()
        self.cfg.spotify.client_secret = self.w_secret.get().strip()
        self.cfg.save()

    def _connect(self) -> None:
        self._save_spotify()
        self.sp_status.configure(text="Connecting… approve in your browser",
                                 text_color=MUTED)

        def worker() -> None:
            # Off the UI thread so the wizard can't freeze while spotipy waits
            # for the browser redirect.
            try:
                self.app.spotify.connect()
                self.top.after(0, lambda: self.sp_status.configure(
                    text="Connected ✓", text_color=ACCENT))
            except Exception as exc:
                msg = str(exc)
                self.top.after(0, lambda: (
                    self.sp_status.configure(text="Not connected", text_color=MUTED),
                    messagebox.showerror("Spotify", f"Could not connect:\n{msg}")))

        import threading
        threading.Thread(target=worker, name="spotify-connect", daemon=True).start()

    def _step_region(self) -> None:
        self._title("Step 2 — Set the HUD capture region")
        self._hint(
            "RivalsRadio reads a small part of your screen to tell which hero "
            "you're on. With Marvel Rivals running, auto-detect the window for a "
            "suggested region, then fine-tune by dragging a tight box around the "
            "hero portrait in the bottom-left corner (your hero's face). It's a "
            "steadier anchor than the ability icons, which change with cooldowns.")

        ctk.CTkButton(self.body, text="Auto-detect game window", height=36, font=self.f_bold,
                      command=self._auto_region, **NEUTRAL_BTN).pack(anchor="w", pady=4)
        ctk.CTkButton(self.body, text="Select region manually…", height=36, font=self.f_bold,
                      command=self._manual_region, **NEUTRAL_BTN).pack(anchor="w", pady=4)
        self.region_lbl = ctk.CTkLabel(self.body, text=self._region_text(),
                                       font=self.f_body, text_color=TEXT)
        self.region_lbl.pack(anchor="w", pady=10)

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
        self.region_lbl.configure(text=self._region_text())

    def _manual_region(self) -> None:
        region = select_region(self.top)
        if region:
            self.cfg.capture_region = region
            self.cfg.save()
            self.app.refresh_widgets_from_config()
            self.region_lbl.configure(text=self._region_text())

    def _step_calibrate(self) -> None:
        self._title("Step 3 — Calibrate your heroes")
        self._hint(
            "Almost done! In the Heroes tab:\n\n"
            "• Paste a Spotify playlist URI for each hero you main.\n"
            "• While in a match on that hero, click 'Capture' to record its HUD.\n"
            "• Optionally set an Avatar image for the Stage view.\n\n"
            "Then press Start monitoring on the Status tab and play. Use "
            "'Test detection' to check a hero is recognised confidently.\n\n"
            "Click Finish to close this wizard.")
