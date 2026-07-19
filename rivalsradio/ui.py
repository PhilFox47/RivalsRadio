"""RivalsRadio desktop app — CustomTkinter shell (Home / Heroes / Settings).

Design: one dark theme — soft bordered cards on a deep background, a single
green accent, pill status indicators. Snappy: widgets are only reconfigured
when state actually changes; heavy work never runs on the UI thread.
"""

from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk

from . import colors, paths
from .config import Config, Hero
from .conductor import Conductor
from .feed import Feed
from .audio import AudioEngine
from .stage import StageWindow
from .paths import ART_KINDS

# ---- palette ---------------------------------------------------------------
ACCENT = "#1DB954"
ACCENT_HOVER = "#24d862"
ACCENT_INK = "#06210f"
DANGER = "#e5484d"
BG = "#0f1013"
SIDEBAR = "#0a0b0d"
SIDEBAR_SEL = "#1c1e23"
CARD = "#17181c"
CARD_HI = "#22242a"
STROKE = "#26282e"
TEXT = "#eceded"
MUTED = "#8f959c"
FAINT = "#5c6167"
LOG_BG = "#111216"

ACCENT_BTN = dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_INK)
NEUTRAL_BTN = dict(fg_color=CARD_HI, hover_color="#2c2f36", text_color=TEXT)

ART_HELP = {
    "logo": "Centred on the Stage; pulses to the beat, tinted the Main colour. "
            "White-on-transparent works best.",
    "portrait": "Rides the hero-switch animation; source for automatic colours.",
    "signature": "Shown top-right on the Stage.",
    "background": "Full-scene Stage background (blur + dim in Settings).",
}


class App:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        root.title("RivalsRadio")
        root.geometry("1080x740")
        root.minsize(940, 640)
        root.configure(fg_color=BG)
        self._icon()

        self.f_brand = ctk.CTkFont(size=22, weight="bold")
        self.f_hero = ctk.CTkFont(size=34, weight="bold")
        self.f_h1 = ctk.CTkFont(size=24, weight="bold")
        self.f_sec = ctk.CTkFont(size=15, weight="bold")
        self.f_body = ctk.CTkFont(size=13)
        self.f_bold = ctk.CTkFont(size=13, weight="bold")
        self.f_small = ctk.CTkFont(size=12)
        self.f_nav = ctk.CTkFont(size=14, weight="bold")
        self.f_mono = ("Consolas", 11)

        # Core services.
        self.cfg = Config.load()
        self.feed = Feed()
        self.audio = AudioEngine()
        self.feed.attach_audio(self.audio)
        self._logq: "queue.Queue[str]" = queue.Queue()
        self._uiq: "queue.Queue" = queue.Queue()
        self.conductor = Conductor(
            self.cfg, self.feed,
            on_log=self._logq.put,
            on_hero=lambda h: self._uiq.put(("hero", h)),
            on_game=lambda r: self._uiq.put(("game", r)),
            on_roster_change=lambda: self._uiq.put(("roster", None)))
        self.stage: "StageWindow | None" = None

        self.hero_var = tk.StringVar(value="—")
        self._game_running = False
        self._status_sig = None
        self._thumb_cache: dict = {}     # (name, px) -> CTkImage (warm cache)
        self._logo_labels: dict = {}     # name -> logo label of the current render
        self._render_gen = 0             # bumped per render; stale thumbs dropped

        self.nav: dict = {}
        self.pages: dict = {}
        self._build()
        self._tick()
        root.protocol("WM_DELETE_WINDOW", self._close)

        if self.cfg.autostart:
            root.after(300, self._autostart)

    # ----- window helpers -------------------------------------------------
    def _icon(self) -> None:
        ico = paths.bundled("assets", "icon.ico")
        if os.path.exists(ico):
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass

    def _autostart(self) -> None:
        self.conductor.start()
        if self.cfg.spotify.client_id and self.cfg.spotify.client_secret:
            self.conductor.connect_spotify_async(
                lambda ok, _msg: self._uiq.put(("status", None)))

    # ----- UI construction --------------------------------------------------
    def _build(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        bar = ctk.CTkFrame(self.root, width=220, corner_radius=0, fg_color=SIDEBAR)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(6, weight=1)
        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=22, pady=(28, 2))
        ctk.CTkLabel(brand, text="Rivals", font=self.f_brand, text_color=TEXT).pack(side="left")
        ctk.CTkLabel(brand, text="Radio", font=self.f_brand, text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(bar, text="hero-aware Spotify", font=self.f_small,
                     text_color=FAINT).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 24))
        for i, (name, icon) in enumerate((("Home", "▶"), ("Heroes", "♫"),
                                          ("Settings", "⚙"))):
            b = ctk.CTkButton(bar, text=f"{icon}   {name}", font=self.f_nav,
                              anchor="w", height=42, corner_radius=10,
                              fg_color="transparent", text_color=MUTED,
                              hover_color=SIDEBAR_SEL,
                              command=lambda n=name: self._page(n))
            b.grid(row=2 + i, column=0, sticky="ew", padx=14, pady=3)
            self.nav[name] = b
        foot = ctk.CTkFrame(bar, fg_color="transparent")
        foot.grid(row=7, column=0, sticky="ew", padx=22, pady=20)
        self.dot_ow = self._dot(foot, "Overwolf app")
        self.dot_game = self._dot(foot, "Game offline")
        self.dot_sp = self._dot(foot, "Spotify offline")

        content = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        for name, builder in (("Home", self._home), ("Heroes", self._heroes),
                              ("Settings", self._settings)):
            page = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
            page.grid(row=0, column=0, sticky="nsew")
            builder(page)
            self.pages[name] = page
        self._page("Home")

    def _dot(self, parent, text):
        lbl = ctk.CTkLabel(parent, text=f"● {text}", font=self.f_small, text_color=MUTED)
        lbl.pack(anchor="w", pady=2)
        return lbl

    def _page(self, name: str) -> None:
        for n, b in self.nav.items():
            b.configure(fg_color=SIDEBAR_SEL if n == name else "transparent",
                        text_color=ACCENT if n == name else MUTED)
        self.pages[name].tkraise()

    def _card(self, parent, title=None, **pack):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color=STROKE)
        opts = dict(fill="x", padx=24, pady=8)
        opts.update(pack)
        card.pack(**opts)
        if title:
            ctk.CTkLabel(card, text=title, font=self.f_sec, text_color=TEXT).pack(
                anchor="w", padx=18, pady=(14, 2))
        return card

    def _entry_row(self, parent, label, var, show=None, placeholder=""):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label, font=self.f_body, text_color=MUTED,
                     width=180, anchor="w").pack(side="left")
        e = ctk.CTkEntry(row, textvariable=var, show=show, height=34,
                         placeholder_text=placeholder, fg_color=CARD_HI,
                         border_width=0, corner_radius=8)
        e.pack(side="left", fill="x", expand=True)
        return e

    # ----- Home page --------------------------------------------------------
    def _home(self, page) -> None:
        ctk.CTkLabel(page, text="Home", font=self.f_h1, text_color=TEXT).pack(
            anchor="w", padx=26, pady=(24, 6))

        spot = self._card(page)
        inner = ctk.CTkFrame(spot, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=(16, 14))
        ctk.CTkLabel(inner, text="CURRENT HERO", font=self.f_small,
                     text_color=FAINT).pack(anchor="w")
        ctk.CTkLabel(inner, textvariable=self.hero_var, font=self.f_hero,
                     text_color=TEXT).pack(anchor="w", pady=(0, 6))
        pills = ctk.CTkFrame(inner, fg_color="transparent")
        pills.pack(anchor="w")
        self.pill_ow = self._pill(pills, "Overwolf app")
        self.pill_game = self._pill(pills, "Game offline")
        self.pill_sp = self._pill(pills, "Spotify offline")

        man = self._card(page, "Manual hero switch")
        row = ctk.CTkFrame(man, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(4, 2))
        names = sorted(self.cfg.heroes) or ["—"]
        self.manual_var = tk.StringVar(value=names[0])
        self.manual_menu = ctk.CTkOptionMenu(
            row, values=names, variable=self.manual_var, width=240, height=34,
            corner_radius=8, fg_color=CARD_HI, button_color="#2c2f36",
            button_hover_color="#343841")
        self.manual_menu.pack(side="left")
        ctk.CTkButton(row, text="Switch playlist", height=34, font=self.f_bold,
                      corner_radius=8,
                      command=lambda: self.conductor.set_hero(self.manual_var.get()),
                      **ACCENT_BTN).pack(side="left", padx=8)
        ctk.CTkLabel(man, text="Works with or without detection — handy on a second monitor.",
                     font=self.f_small, text_color=MUTED).pack(anchor="w", padx=18, pady=(2, 12))

        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(4, 6))
        ctk.CTkButton(actions, text="Open Stage", height=40, width=160,
                      corner_radius=10, font=self.f_bold,
                      command=self._open_stage, **ACCENT_BTN).pack(side="left")
        ctk.CTkButton(actions, text="Connect Spotify", height=40, corner_radius=10,
                      font=self.f_bold, command=self._connect_spotify,
                      **NEUTRAL_BTN).pack(side="left", padx=8)

        log_card = self._card(page, "Activity", fill="both", expand=True, pady=(8, 24))
        wrap = ctk.CTkFrame(log_card, fg_color=LOG_BG, corner_radius=10)
        wrap.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        self.log = tk.Text(wrap, state="disabled", wrap="word", bg=LOG_BG,
                           fg="#d7dadd", font=self.f_mono, relief="flat", bd=0,
                           highlightthickness=0, padx=14, pady=12)
        self.log.pack(fill="both", expand=True, padx=4, pady=4)
        self.log.tag_config("accent", foreground=ACCENT)
        self.log.tag_config("muted", foreground=MUTED)
        self.log.tag_config("warn", foreground="#e3b341")

    def _pill(self, parent, text):
        p = ctk.CTkLabel(parent, text=f"●  {text}", font=self.f_small,
                         text_color=MUTED, fg_color=CARD_HI, corner_radius=99,
                         padx=10, height=26)
        p.pack(side="left", padx=(0, 8))
        return p

    # ----- Heroes page --------------------------------------------------------
    def _heroes(self, page) -> None:
        ctk.CTkLabel(page, text="Heroes", font=self.f_h1, text_color=TEXT).pack(
            anchor="w", padx=26, pady=(24, 6))
        ctk.CTkLabel(
            page, justify="left", wraplength=800, font=self.f_small, text_color=MUTED,
            text=("New heroes are added automatically when detected in-game. Per hero: "
                  "playlist, art (logo · portrait · signature · background) and two "
                  "colours — Main tints the logo, Accent drives the bars. Unset colours "
                  "are extracted from the portrait."),
        ).pack(anchor="w", padx=26, pady=(0, 10))

        self.rows = ctk.CTkScrollableFrame(page, fg_color=CARD, corner_radius=14,
                                           border_width=1, border_color=STROKE)
        self.rows.pack(fill="both", expand=True, padx=24, pady=4)
        self.playlist_vars: dict = {}
        self.color_vars: dict = {}
        self.art_buttons: dict = {}
        self._render_rows()

        foot = ctk.CTkFrame(page, fg_color="transparent")
        foot.pack(fill="x", padx=24, pady=(10, 24))
        self.new_hero = tk.StringVar()
        e = ctk.CTkEntry(foot, textvariable=self.new_hero, width=240, height=36,
                         placeholder_text="Add hero manually…", fg_color=CARD_HI,
                         border_width=0, corner_radius=8)
        e.pack(side="left")
        e.bind("<Return>", lambda _e: self._add_hero())
        ctk.CTkButton(foot, text="Add", width=80, height=36, font=self.f_bold,
                      corner_radius=8, command=self._add_hero,
                      **NEUTRAL_BTN).pack(side="left", padx=8)
        ctk.CTkButton(foot, text="Save playlists", height=36, width=150,
                      font=self.f_bold, corner_radius=8,
                      command=self._save_playlists, **ACCENT_BTN).pack(side="right")

    def _render_rows(self) -> None:
        self._render_gen += 1
        gen = self._render_gen
        for child in self.rows.winfo_children():
            child.destroy()
        self.playlist_vars.clear()
        self.color_vars.clear()
        self.art_buttons.clear()
        self._logo_labels = {}
        pending: list = []

        if not self.cfg.heroes:
            ctk.CTkLabel(self.rows, font=self.f_body, text_color=MUTED,
                         text="No heroes yet — play a match and they appear here "
                              "automatically.").pack(anchor="w", padx=16, pady=20)

        for name in sorted(self.cfg.heroes):
            hero = self.cfg.heroes[name]
            row = ctk.CTkFrame(self.rows, fg_color=CARD_HI, corner_radius=10)
            row.pack(fill="x", padx=6, pady=4)

            # Logo: use the warm cache for an instant paint, otherwise show a
            # placeholder and let a worker decode + tint it off the UI thread.
            cached = self._thumb_cache.get((name, 26))
            lbl = ctk.CTkLabel(row, image=cached, text="" if cached else "♫",
                               width=30, text_color=FAINT)
            lbl.pack(side="left", padx=(10, 0), pady=8)
            self._logo_labels[name] = lbl
            if cached is None:
                pending.append(name)
            ctk.CTkLabel(row, text=name, width=150, anchor="w", font=self.f_bold,
                         text_color=TEXT).pack(side="left", padx=(4, 6))

            var = tk.StringVar(value=hero.playlist)
            self.playlist_vars[name] = var
            ctk.CTkEntry(row, textvariable=var, height=32, fg_color=CARD,
                         border_width=0, corner_radius=8,
                         placeholder_text="Spotify playlist link…").pack(
                side="left", fill="x", expand=True, padx=4, pady=8)

            done = hero.art_complete()
            art = ctk.CTkButton(row, text="Art ✓" if done else "Art…", width=64,
                                height=32, corner_radius=8, font=self.f_small,
                                command=lambda n=name: self._art_dialog(n),
                                **(ACCENT_BTN if done else NEUTRAL_BTN))
            art.pack(side="left", padx=2)
            self.art_buttons[name] = art

            for kind in ("main", "accent"):
                self._swatch(row, name, kind).pack(side="left", padx=2)

            ctk.CTkButton(row, text="✕", width=32, height=32, font=self.f_bold,
                          corner_radius=8, fg_color="transparent",
                          hover_color=DANGER, text_color=FAINT,
                          command=lambda n=name: self._remove_hero(n)).pack(
                side="left", padx=(2, 10))

        if hasattr(self, "manual_menu"):
            names = sorted(self.cfg.heroes) or ["—"]
            self.manual_menu.configure(values=names)
            if self.manual_var.get() not in names:
                self.manual_var.set(names[0])

        if pending:
            threading.Thread(target=self._produce_thumbs,
                             args=(gen, pending, 26),
                             name="thumbs", daemon=True).start()

    def _produce_thumbs(self, gen: int, names: list, px: int) -> None:
        """Worker thread: decode + tint each logo (and extract its palette),
        posting finished PIL images back to the UI thread. No Tk here."""
        for name in names:
            if gen != self._render_gen:
                return                        # a newer render superseded us
            made = self._thumb_pil(name, px)
            if made is not None:
                self._uiq.put(("thumb", (gen, name, px, made[0], made[1])))

    def _thumb_pil(self, name: str, px: int):
        """Heavy, Tk-free half of the thumbnail: returns (PIL image, size)."""
        hero = self.cfg.heroes.get(name)
        path = hero.art_path("logo") if hero else None
        if not path:
            return None
        try:
            from PIL import Image, ImageChops
            main, _ = self.conductor.effective_colors(name)
            im = Image.open(path).convert("RGBA")
            r = px / max(im.width, im.height)
            im = im.resize((max(1, int(im.width * r)),
                            max(1, int(im.height * r))), Image.LANCZOS)
            solid = Image.new("RGB", im.size, colors.hex_to_rgb(main))
            tint = ImageChops.multiply(im.convert("RGB"), solid).convert("RGBA")
            tint.putalpha(im.getchannel("A"))
            return tint, im.size
        except Exception:
            return None

    def _invalidate_thumb(self, name: str) -> None:
        for key in [k for k in self._thumb_cache if k[0] == name]:
            self._thumb_cache.pop(key, None)

    def _apply_thumb(self, gen: int, name: str, px: int, pil_img, size) -> None:
        """UI thread: wrap a finished PIL image and drop it into its row."""
        if gen != self._render_gen:
            return                            # row belongs to a superseded render
        try:
            img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)
        except Exception:
            return
        self._thumb_cache[(name, px)] = img
        lbl = self._logo_labels.get(name)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(image=img, text="")
    def _swatch(self, parent, name: str, kind: str):
        hero = self.cfg.heroes[name]
        value = hero.color_main if kind == "main" else hero.color_accent
        valid = colors.is_hex(value)
        return ctk.CTkButton(
            parent, text=kind.capitalize(), width=66, height=32,
            font=self.f_small, corner_radius=8,
            text_color=colors.readable_ink(value) if valid else TEXT,
            fg_color=value if valid else CARD,
            hover_color=value if valid else "#2c2f36",
            border_width=1, border_color=STROKE,
            command=lambda: self._pick_color(name, kind))

    def _pick_color(self, name: str, kind: str) -> None:
        hero = self.cfg.heroes[name]
        cur = hero.color_main if kind == "main" else hero.color_accent
        _rgb, hexv = colorchooser.askcolor(
            color=cur if colors.is_hex(cur) else None,
            title=f"{kind.capitalize()} colour — {name}", parent=self.root)
        if not hexv:
            return
        if kind == "main":
            hero.color_main = hexv.lower()
        else:
            hero.color_accent = hexv.lower()
        self.cfg.save()
        self.conductor.invalidate_palette(name)
        self._invalidate_thumb(name)
        self.conductor.refresh_current_visuals()
        self._render_rows()

    def _art_dialog(self, name: str) -> None:
        win = ctk.CTkToplevel(self.root)
        win.title(f"Hero art — {name}")
        win.geometry("480x460")
        win.configure(fg_color=BG)
        win.transient(self.root)
        win.after(200, lambda: win.grab_set() if win.winfo_exists() else None)
        ctk.CTkLabel(win, text=f"Hero art for {name}", font=self.f_sec,
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 8))

        def row(kind: str):
            hero = self.cfg.heroes[name]
            fr = ctk.CTkFrame(win, fg_color=CARD, corner_radius=10,
                              border_width=1, border_color=STROKE)
            fr.pack(fill="x", padx=18, pady=6)
            head = ctk.CTkFrame(fr, fg_color="transparent")
            head.pack(side="left", padx=12, pady=8)
            ctk.CTkLabel(head, text=kind.capitalize(), anchor="w",
                         font=self.f_bold, text_color=TEXT).pack(anchor="w")
            ctk.CTkLabel(head, text=ART_HELP[kind], anchor="w", font=self.f_small,
                         text_color=MUTED, wraplength=220,
                         justify="left").pack(anchor="w")
            status = ctk.CTkLabel(fr, text="✓" if hero.art_file(kind) else "—",
                                  font=self.f_bold,
                                  text_color=ACCENT if hero.art_file(kind) else MUTED)
            status.pack(side="left", padx=6)

            def choose():
                path = filedialog.askopenfilename(
                    title=f"Choose {kind} for {name}",
                    filetypes=[("Images", "*.png *.webp *.jpg *.jpeg"),
                               ("All files", "*.*")])
                if not path:
                    return
                ext = os.path.splitext(path)[1].lower() or ".png"
                fname = f"{name.replace(' ', '_').replace('&', 'and')}_{kind}{ext}"
                try:
                    shutil.copyfile(path,
                                    os.path.join(paths.art_dir(kind), fname))
                except OSError as exc:
                    messagebox.showerror("Art", f"Could not copy image:\n{exc}")
                    return
                setattr(hero, kind, fname)
                self.cfg.save()
                self.conductor.invalidate_palette(name)
                self._invalidate_thumb(name)
                self.conductor.refresh_current_visuals()
                status.configure(text="✓", text_color=ACCENT)

            def clear():
                setattr(hero, kind, "")
                self.cfg.save()
                self.conductor.invalidate_palette(name)
                self._invalidate_thumb(name)
                self.conductor.refresh_current_visuals()
                status.configure(text="—", text_color=MUTED)

            ctk.CTkButton(fr, text="Choose…", width=80, height=30, font=self.f_small,
                          corner_radius=8, command=choose,
                          **NEUTRAL_BTN).pack(side="right", padx=(4, 12))
            ctk.CTkButton(fr, text="Clear", width=60, height=30, font=self.f_small,
                          corner_radius=8, command=clear,
                          **NEUTRAL_BTN).pack(side="right", padx=4)

        for kind in ART_KINDS:
            row(kind)

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._render_rows()

        ctk.CTkButton(win, text="Done", height=34, font=self.f_bold,
                      corner_radius=8, command=close, **ACCENT_BTN).pack(pady=12)
        win.protocol("WM_DELETE_WINDOW", close)

    def _add_hero(self) -> None:
        name = self.new_hero.get().strip()
        if not name:
            return
        if name in self.cfg.heroes:
            messagebox.showinfo("Add hero", f"{name} already exists.")
            return
        self.cfg.heroes[name] = Hero()
        self.cfg.save()
        self.new_hero.set("")
        self._render_rows()

    def _remove_hero(self, name: str) -> None:
        if not messagebox.askyesno("Remove", f"Remove {name}?"):
            return
        hero = self.cfg.heroes.pop(name, None)
        self._invalidate_thumb(name)
        if hero:
            for kind in ART_KINDS:
                p = hero.art_path(kind)
                if p:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        self.cfg.save()
        self._render_rows()

    def _save_playlists(self) -> None:
        for name, var in self.playlist_vars.items():
            if name in self.cfg.heroes:
                self.cfg.heroes[name].playlist = var.get().strip()
        self.cfg.save()
        self._log_line("Playlists saved.")

    # ----- Settings page --------------------------------------------------
    def _settings(self, page) -> None:
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(page, fg_color=BG, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(scroll, text="Settings", font=self.f_h1, text_color=TEXT).pack(
            anchor="w", padx=26, pady=(24, 6))

        sp = self._card(scroll, "Spotify  (Premium required)")
        s = self.cfg.spotify
        self.sp_id = tk.StringVar(value=s.client_id)
        self.sp_secret = tk.StringVar(value=s.client_secret)
        self.sp_redirect = tk.StringVar(value=s.redirect_uri)
        self.sp_device = tk.StringVar(value=s.device_name)
        self._entry_row(sp, "Client ID", self.sp_id)
        self._entry_row(sp, "Client Secret", self.sp_secret, show="•")
        self._entry_row(sp, "Redirect URI", self.sp_redirect)
        self._entry_row(sp, "Device name (optional)", self.sp_device,
                        placeholder="blank = active device")
        ctk.CTkButton(sp, text="Save & connect", height=34, font=self.f_bold,
                      corner_radius=8, command=self._connect_spotify,
                      **ACCENT_BTN).pack(anchor="w", padx=18, pady=(6, 14))

        stg = self._card(scroll, "Stage")
        self._option_row(stg, "Render FPS", [str(v) for v in (60, 90, 120, 144, 160)],
                         str(self.cfg.stage.fps),
                         lambda v: self._set_stage("fps", int(v)))
        self._slider_row(stg, "Background blur", 0, 30, self.cfg.stage.bg_blur,
                         lambda v: self._set_stage("bg_blur", v, refresh=True))
        self._slider_row(stg, "Background dim", 0, 100, self.cfg.stage.bg_dim,
                         lambda v: self._set_stage("bg_dim", v, refresh=True))
        self._slider_row(stg, "Logo glow", 0, 100, self.cfg.stage.glow,
                         lambda v: self._set_stage("glow", v))
        self._slider_row(stg, "Beat pulse depth", 0, 100, self.cfg.stage.pulse,
                         lambda v: self._set_stage("pulse", v))
        self._switch_row(stg, "Floating particles", self.cfg.stage.particles,
                         lambda v: self._set_stage("particles", v))
        self._switch_row(stg, "Show now playing", self.cfg.stage.show_now_playing,
                         lambda v: self._set_stage("show_now_playing", v))
        self._switch_row(stg, "Hero switch animation", self.cfg.stage.switch_anim,
                         lambda v: self._set_stage("switch_anim", v))
        ctk.CTkFrame(stg, fg_color="transparent", height=8).pack()

        gen = self._card(scroll, "General")
        self._switch_row(gen, "Start listening + connect on launch",
                         self.cfg.autostart, self._set_autostart)
        prow = ctk.CTkFrame(gen, fg_color="transparent")
        prow.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(prow, text="Overwolf app port", font=self.f_body,
                     text_color=MUTED, width=180, anchor="w").pack(side="left")
        self.port_var = tk.IntVar(value=self.cfg.detector_port)
        ctk.CTkEntry(prow, textvariable=self.port_var, width=100, height=34,
                     fg_color=CARD_HI, border_width=0, corner_radius=8).pack(side="left")
        ctk.CTkButton(gen, text="Open data folder", height=32, font=self.f_small,
                      corner_radius=8, command=self._open_data_dir,
                      **NEUTRAL_BTN).pack(anchor="w", padx=18, pady=(6, 14))

    def _option_row(self, parent, label, values, current, apply):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label, font=self.f_body, text_color=MUTED,
                     width=180, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(row, values=values,
                          variable=tk.StringVar(value=current),
                          command=apply, width=140, height=34, corner_radius=8,
                          fg_color=CARD_HI, button_color="#2c2f36",
                          button_hover_color="#343841").pack(side="left")

    def _slider_row(self, parent, label, lo, hi, current, apply):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label, font=self.f_body, text_color=MUTED,
                     width=180, anchor="w").pack(side="left")
        val = ctk.CTkLabel(row, text=str(current), font=self.f_small,
                           text_color=FAINT, width=36)
        pend = {"id": None}

        def moved(v):
            n = int(float(v))
            val.configure(text=str(n))
            if pend["id"] is not None:
                try:
                    self.root.after_cancel(pend["id"])
                except Exception:
                    pass
            pend["id"] = self.root.after(350, lambda: apply(n))

        ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=hi - lo,
                      variable=tk.IntVar(value=current), width=220,
                      progress_color=ACCENT, button_color=ACCENT,
                      button_hover_color=ACCENT_HOVER,
                      command=moved).pack(side="left")
        val.pack(side="left", padx=8)

    def _switch_row(self, parent, label, current, apply):
        var = tk.BooleanVar(value=current)
        ctk.CTkSwitch(parent, text=label, variable=var, font=self.f_body,
                      progress_color=ACCENT,
                      command=lambda: apply(bool(var.get()))).pack(
            anchor="w", padx=18, pady=4)

    def _set_stage(self, attr, value, refresh=False) -> None:
        setattr(self.cfg.stage, attr, value)
        self.cfg.save()
        if self.stage and self.stage.alive:
            if attr == "fps":
                self.stage.set_fps(value)
            if refresh:
                self.stage.refresh()

    def _set_autostart(self, value: bool) -> None:
        self.cfg.autostart = value
        self.cfg.save()

    def _open_data_dir(self) -> None:
        d = paths.data_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(d)  # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open", d])
        except Exception:
            self._log_line(f"Data folder: {d}")

    # ----- actions ------------------------------------------------------
    def _connect_spotify(self) -> None:
        s = self.cfg.spotify
        s.client_id = self.sp_id.get().strip() if hasattr(self, "sp_id") else s.client_id
        if hasattr(self, "sp_secret"):
            s.client_secret = self.sp_secret.get().strip()
            s.redirect_uri = self.sp_redirect.get().strip()
            s.device_name = self.sp_device.get().strip()
        try:
            self.cfg.detector_port = int(self.port_var.get())
        except Exception:
            pass
        self.cfg.save()
        self._log_line("Connecting to Spotify — approve in the browser if asked…")
        self.conductor.connect_spotify_async(
            lambda ok, _m: self._uiq.put(("status", None)))

    def _open_stage(self) -> None:
        if self.stage and self.stage.alive:
            self.stage.focus()
            return
        self.audio.start()
        self.stage = StageWindow(self.cfg, self.feed)
        self._log_line("Stage opened — drag to your second screen, F11 = fullscreen.")
        if not self.audio.available:
            self._log_line("Audio capture unavailable — bars will stay idle.")

    # ----- event pump -----------------------------------------------------
    def _tick(self) -> None:
        try:
            while True:
                self._log_line(self._logq.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self._uiq.get_nowait()
                if kind == "hero":
                    self.hero_var.set(payload)
                elif kind == "game":
                    self._game_running = payload
                    self._status_sig = None
                elif kind == "roster":
                    self._render_rows()
                elif kind == "thumb":
                    self._apply_thumb(*payload)
        except queue.Empty:
            pass
        self._refresh_status()
        self.root.after(250, self._tick)

    def _refresh_status(self) -> None:
        ow_linked = (time.time() - self.conductor.detector.last_event_at) < 90
        sp = self.conductor.spotify.connected
        sig = (ow_linked, self._game_running, sp)
        if sig == self._status_sig:
            return
        self._status_sig = sig
        pairs = ((self.pill_ow, self.dot_ow,
                  "Overwolf linked" if ow_linked else "Overwolf app", ow_linked),
                 (self.pill_game, self.dot_game,
                  "Game running" if self._game_running else "Game offline",
                  self._game_running),
                 (self.pill_sp, self.dot_sp,
                  "Spotify connected" if sp else "Spotify offline", sp))
        for pill, dot, text, on in pairs:
            color = ACCENT if on else MUTED
            pill.configure(text=f"●  {text}", text_color=color)
            dot.configure(text=f"● {text}", text_color=color)

    def _log_line(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        tag = ("accent" if msg.startswith("▶") else
               "warn" if any(w in msg.lower() for w in
                             ("fail", "error", "unavailable", "can't")) else None)
        self.log.config(state="normal")
        self.log.insert("end", f"[{ts}] ", ("muted",))
        self.log.insert("end", f"{msg}\n", (tag,) if tag else ())
        self.log.see("end")
        self.log.config(state="disabled")

    def _close(self) -> None:
        self.conductor.stop()
        if self.stage:
            self.stage.close()
        self.audio.stop()
        self.root.destroy()


def _dpi_awareness() -> None:
    """Per-monitor-v2 DPI awareness: windows fill scaled monitors instead of
    being bitmap-stretched. No-op off Windows."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def main() -> None:
    _dpi_awareness()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")
    root = ctk.CTk()
    App(root)
    root.mainloop()
