"""RivalsRadio desktop app (Home / Heroes / Settings).

Visual language: a flat, quiet dark tool. One background, surfaces raised only
where the eye needs an edit target (inputs, the activity log), hairline
dividers instead of boxed cards, a single corner radius, one accent colour
used only for the primary action and live state. Hierarchy comes from type
weight and text colour, not from decoration.

Engineering: heavy work never runs on the UI thread; widgets are only
reconfigured when their state actually changes.
"""

from __future__ import annotations

import os
import queue
import shutil
import sys
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

# ---- palette (one cool near-black family, one accent) ----------------------
BG = "#111214"
SURFACE = "#191b1e"       # inputs, log, chips
HOVER = "#202327"
BORDER = "#26292d"        # hairlines
TEXT = "#e8eaec"
MUTED = "#9aa0a6"
FAINT = "#5f656c"
ACCENT = "#1db954"
ACCENT_HOVER = "#1aa64c"
ACCENT_INK = "#08260f"
DANGER = "#d5484f"
WARN = "#d9a441"

RADIUS = 6                # the only corner radius in the app

ART_HELP = {
    "logo": "Shown centred on the Stage, tinted the main colour. "
            "White on transparent works best.",
    "portrait": "Shown during hero switches. Also the source for "
                "automatic colours.",
    "signature": "Shown top right on the Stage.",
    "background": "Fills the Stage behind everything. Blur and dim "
                  "are in Settings.",
}


class App:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        root.title("RivalsRadio")
        root.geometry("1060x720")
        root.minsize(920, 620)
        root.configure(fg_color=BG)
        self._icon()

        f = "Segoe UI"
        self.f_display = ctk.CTkFont(family=f, size=30, weight="bold")
        self.f_title = ctk.CTkFont(family=f, size=19, weight="bold")
        self.f_section = ctk.CTkFont(family=f, size=13, weight="bold")
        self.f_body = ctk.CTkFont(family=f, size=13)
        self.f_small = ctk.CTkFont(family=f, size=12)
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

        self.hero_var = tk.StringVar(value="No hero yet")
        self._game_running = False
        self._status_sig = None
        self._thumbs: dict = {}

        self.nav: dict = {}
        self.pages: dict = {}
        self._build()
        self._tick()
        root.protocol("WM_DELETE_WINDOW", self._close)

        if self.cfg.autostart:
            root.after(300, self._autostart)

    # ----- window helpers ---------------------------------------------------
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

    # ----- shared pieces ------------------------------------------------------
    def _hairline(self, parent, **pack) -> None:
        line = ctk.CTkFrame(parent, height=1, fg_color=BORDER, corner_radius=0)
        opts = dict(fill="x")
        opts.update(pack)
        line.pack(**opts)

    def _primary(self, parent, text, command, width=None):
        kw = dict(width=width) if width else {}
        return ctk.CTkButton(parent, text=text, command=command, height=32,
                             corner_radius=RADIUS, font=self.f_body,
                             fg_color=ACCENT, hover_color=ACCENT_HOVER,
                             text_color=ACCENT_INK, **kw)

    def _ghost(self, parent, text, command, width=None):
        kw = dict(width=width) if width else {}
        return ctk.CTkButton(parent, text=text, command=command, height=32,
                             corner_radius=RADIUS, font=self.f_body,
                             fg_color="transparent", hover_color=HOVER,
                             border_width=1, border_color=BORDER,
                             text_color=TEXT, **kw)

    def _field(self, parent, var, placeholder="", show=None, width=None):
        kw = dict(width=width) if width else {}
        return ctk.CTkEntry(parent, textvariable=var, show=show, height=32,
                            placeholder_text=placeholder, fg_color=SURFACE,
                            border_width=1, border_color=BORDER,
                            corner_radius=RADIUS, text_color=TEXT, **kw)

    def _menu(self, parent, values, variable=None, command=None, width=180):
        return ctk.CTkOptionMenu(
            parent, values=values, variable=variable, command=command,
            width=width, height=32, corner_radius=RADIUS, font=self.f_body,
            fg_color=SURFACE, button_color=SURFACE, button_hover_color=HOVER,
            text_color=TEXT, dropdown_fg_color=SURFACE,
            dropdown_hover_color=HOVER, dropdown_text_color=TEXT)

    def _section(self, parent, title: str):
        """Flat section: hairline, heading, content frame."""
        self._hairline(parent, padx=32, pady=(26, 0))
        ctk.CTkLabel(parent, text=title, font=self.f_section,
                     text_color=TEXT).pack(anchor="w", padx=32, pady=(14, 8))
        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.pack(fill="x", padx=32)
        return body

    def _setting_row(self, parent, label: str):
        """Label on the left, control container returned on the right."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text=label, font=self.f_body, text_color=MUTED,
                     width=170, anchor="w").pack(side="left")
        holder = ctk.CTkFrame(row, fg_color="transparent")
        holder.pack(side="left", fill="x", expand=True)
        return holder

    # ----- shell --------------------------------------------------------------
    def _build(self) -> None:
        self.root.grid_columnconfigure(2, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        rail = ctk.CTkFrame(self.root, width=190, corner_radius=0, fg_color=BG)
        rail.grid(row=0, column=0, sticky="nsew")
        rail.grid_propagate(False)
        ctk.CTkFrame(self.root, width=1, corner_radius=0,
                     fg_color=BORDER).grid(row=0, column=1, sticky="ns")

        ctk.CTkLabel(rail, text="RivalsRadio", font=self.f_section,
                     text_color=TEXT).pack(anchor="w", padx=24, pady=(26, 22))

        for name in ("Home", "Heroes", "Settings"):
            item = ctk.CTkFrame(rail, fg_color="transparent")
            item.pack(fill="x", padx=12, pady=1)
            mark = ctk.CTkFrame(item, width=2, fg_color=BG, corner_radius=0)
            mark.pack(side="left", fill="y", padx=(0, 0))
            btn = ctk.CTkButton(item, text=name, font=self.f_body, anchor="w",
                                height=32, corner_radius=RADIUS,
                                fg_color="transparent", hover_color=HOVER,
                                text_color=MUTED,
                                command=lambda n=name: self._page(n))
            btn.pack(side="left", fill="x", expand=True, padx=(8, 0))
            self.nav[name] = (btn, mark)

        # Live status, stated once, at the bottom of the rail.
        status = ctk.CTkFrame(rail, fg_color="transparent")
        status.pack(side="bottom", fill="x", padx=24, pady=20)
        self.status_rows = {}
        for key, label in (("overwolf", "Overwolf"), ("game", "Game"),
                           ("spotify", "Spotify")):
            row = ctk.CTkFrame(status, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=label, font=self.f_small,
                         text_color=FAINT, anchor="w").pack(side="left")
            val = ctk.CTkLabel(row, text="off", font=self.f_small,
                               text_color=FAINT, anchor="e")
            val.pack(side="right")
            self.status_rows[key] = val

        content = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        content.grid(row=0, column=2, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        for name, builder in (("Home", self._home), ("Heroes", self._heroes),
                              ("Settings", self._settings)):
            page = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
            page.grid(row=0, column=0, sticky="nsew")
            builder(page)
            self.pages[name] = page
        self._page("Home")

    def _page(self, name: str) -> None:
        for n, (btn, mark) in self.nav.items():
            active = n == name
            btn.configure(text_color=TEXT if active else MUTED)
            mark.configure(fg_color=ACCENT if active else BG)
        self.pages[name].tkraise()

    # ----- Home ---------------------------------------------------------------
    def _home(self, page) -> None:
        ctk.CTkLabel(page, text="Current hero", font=self.f_small,
                     text_color=FAINT).pack(anchor="w", padx=32, pady=(30, 0))
        ctk.CTkLabel(page, textvariable=self.hero_var, font=self.f_display,
                     text_color=TEXT).pack(anchor="w", padx=32, pady=(0, 14))

        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.pack(fill="x", padx=32)
        self._primary(actions, "Open Stage", self._open_stage,
                      width=120).pack(side="left")
        names = sorted(self.cfg.heroes) or ["No heroes"]
        self.manual_var = tk.StringVar(value=names[0])
        self.manual_menu = self._menu(actions, names, self.manual_var, width=220)
        self.manual_menu.pack(side="left", padx=(16, 8))
        self._ghost(actions, "Switch",
                    lambda: self.conductor.set_hero(self.manual_var.get()),
                    width=76).pack(side="left")

        body = self._section(page, "Activity")
        body.pack_configure(fill="both", expand=True, pady=(0, 28))
        self.log = tk.Text(body, state="disabled", wrap="word", bg=SURFACE,
                           fg=MUTED, font=self.f_mono, relief="flat", bd=0,
                           highlightthickness=1, highlightbackground=BORDER,
                           highlightcolor=BORDER, padx=12, pady=10)
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("time", foreground=FAINT)
        self.log.tag_config("play", foreground=TEXT)
        self.log.tag_config("warn", foreground=WARN)

    # ----- Heroes ---------------------------------------------------------------
    def _heroes(self, page) -> None:
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=32, pady=(28, 2))
        ctk.CTkLabel(head, text="Heroes", font=self.f_title,
                     text_color=TEXT).pack(side="left")
        self._primary(head, "Save playlists", self._save_playlists,
                      width=118).pack(side="right")
        self.new_hero = tk.StringVar()
        self._ghost(head, "Add", self._add_hero, width=56).pack(
            side="right", padx=(8, 8))
        e = self._field(head, self.new_hero, placeholder="Hero name", width=180)
        e.pack(side="right")
        e.bind("<Return>", lambda _e: self._add_hero())

        ctk.CTkLabel(page, text="Heroes you play are added automatically.",
                     font=self.f_small, text_color=FAINT).pack(
            anchor="w", padx=32, pady=(0, 12))

        # Column labels, aligned with the rows below.
        cols = ctk.CTkFrame(page, fg_color="transparent")
        cols.pack(fill="x", padx=32)
        for text, width in (("", 34), ("Hero", 150), ("Playlist", 0)):
            ctk.CTkLabel(cols, text=text, font=self.f_small, text_color=FAINT,
                         width=width or 200, anchor="w").pack(
                side="left", fill="x" if not width else None,
                expand=not width, padx=(0, 8))
        for text, width in (("Del", 34), ("Accent", 52), ("Main", 52),
                            ("Art", 64)):
            ctk.CTkLabel(cols, text=text if text != "Del" else "",
                         font=self.f_small, text_color=FAINT, width=width,
                         anchor="w").pack(side="right", padx=(8, 0))
        self._hairline(page, padx=32, pady=(6, 0))

        self.rows = ctk.CTkScrollableFrame(page, fg_color=BG, corner_radius=0)
        self.rows.pack(fill="both", expand=True, padx=32, pady=(0, 28))
        self.playlist_vars: dict = {}
        self._row_imgs: list = []
        self._render_rows()

    def _render_rows(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        self.playlist_vars.clear()
        self._row_imgs = []

        if not self.cfg.heroes:
            ctk.CTkLabel(self.rows, font=self.f_body, text_color=MUTED,
                         text="No heroes yet. Play a match and they show up "
                              "here.").pack(anchor="w", pady=18)

        for name in sorted(self.cfg.heroes):
            hero = self.cfg.heroes[name]
            row = ctk.CTkFrame(self.rows, fg_color="transparent", height=44)
            row.pack(fill="x")
            row.pack_propagate(False)

            thumb = self._logo_thumb(name, 22)
            ctk.CTkLabel(row, image=thumb, text="", width=34).pack(
                side="left", padx=(0, 8))
            ctk.CTkLabel(row, text=name, width=150, anchor="w",
                         font=self.f_body, text_color=TEXT).pack(
                side="left", padx=(0, 8))

            var = tk.StringVar(value=hero.playlist)
            self.playlist_vars[name] = var
            self._field(row, var, placeholder="Spotify playlist link").pack(
                side="left", fill="x", expand=True, padx=(0, 8), pady=6)

            ctk.CTkButton(row, text="Remove", width=64, height=28,
                          corner_radius=RADIUS, font=self.f_small,
                          fg_color="transparent", hover_color=HOVER,
                          text_color=FAINT,
                          command=lambda n=name: self._remove_hero(n)).pack(
                side="right", padx=(8, 0))
            self._swatch(row, name, "accent").pack(side="right", padx=(8, 0))
            self._swatch(row, name, "main").pack(side="right", padx=(8, 0))
            done = hero.art_complete()
            ctk.CTkButton(row, text="Art", width=64, height=28,
                          corner_radius=RADIUS, font=self.f_small,
                          fg_color="transparent", hover_color=HOVER,
                          border_width=1, border_color=BORDER,
                          text_color=TEXT if done else MUTED,
                          command=lambda n=name: self._art_dialog(n)).pack(
                side="right", padx=(8, 0))

            self._hairline(self.rows)

        if hasattr(self, "manual_menu"):
            names = sorted(self.cfg.heroes) or ["No heroes"]
            self.manual_menu.configure(values=names)
            if self.manual_var.get() not in names:
                self.manual_var.set(names[0])

    def _logo_thumb(self, name: str, px: int):
        hero = self.cfg.heroes.get(name)
        path = hero.art_path("logo") if hero else None
        if not path:
            return None
        main, _ = self.conductor.effective_colors(name)
        key = (path, main, px)
        img = self._thumbs.get(key)
        if img is None:
            try:
                from PIL import Image, ImageChops
                im = Image.open(path).convert("RGBA")
                r = px / max(im.width, im.height)
                im = im.resize((max(1, int(im.width * r)),
                                max(1, int(im.height * r))), Image.LANCZOS)
                solid = Image.new("RGB", im.size, colors.hex_to_rgb(main))
                tint = ImageChops.multiply(im.convert("RGB"), solid).convert("RGBA")
                tint.putalpha(im.getchannel("A"))
                img = ctk.CTkImage(light_image=tint, dark_image=tint, size=im.size)
                self._thumbs[key] = img
            except Exception:
                return None
        self._row_imgs.append(img)
        return img

    def _swatch(self, parent, name: str, kind: str):
        """A small colour chip. Filled when set, hollow when automatic."""
        hero = self.cfg.heroes[name]
        value = hero.color_main if kind == "main" else hero.color_accent
        valid = colors.is_hex(value)
        return ctk.CTkButton(
            parent, text="", width=52, height=28, corner_radius=RADIUS,
            fg_color=value if valid else SURFACE,
            hover_color=value if valid else HOVER,
            border_width=1, border_color=BORDER,
            command=lambda: self._pick_color(name, kind))

    def _pick_color(self, name: str, kind: str) -> None:
        hero = self.cfg.heroes[name]
        cur = hero.color_main if kind == "main" else hero.color_accent
        _rgb, hexv = colorchooser.askcolor(
            color=cur if colors.is_hex(cur) else None,
            title=f"{name}: {kind} colour", parent=self.root)
        if not hexv:
            return
        if kind == "main":
            hero.color_main = hexv.lower()
        else:
            hero.color_accent = hexv.lower()
        self.cfg.save()
        self.conductor.invalidate_palette(name)
        self.conductor.refresh_current_visuals()
        self._render_rows()

    def _art_dialog(self, name: str) -> None:
        win = ctk.CTkToplevel(self.root)
        win.title(f"Art: {name}")
        win.geometry("470x400")
        win.configure(fg_color=BG)
        win.transient(self.root)
        win.after(200, lambda: win.grab_set() if win.winfo_exists() else None)
        ctk.CTkLabel(win, text=name, font=self.f_title,
                     text_color=TEXT).pack(anchor="w", padx=24, pady=(20, 10))

        def row(kind: str):
            hero = self.cfg.heroes[name]
            self._hairline(win, padx=24)
            fr = ctk.CTkFrame(win, fg_color="transparent")
            fr.pack(fill="x", padx=24, pady=8)
            head = ctk.CTkFrame(fr, fg_color="transparent")
            head.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(head, text=kind.capitalize(), anchor="w",
                         font=self.f_body, text_color=TEXT).pack(anchor="w")
            ctk.CTkLabel(head, text=ART_HELP[kind], anchor="w",
                         font=self.f_small, text_color=FAINT, wraplength=250,
                         justify="left").pack(anchor="w")
            status = ctk.CTkLabel(fr, width=52, font=self.f_small,
                                  text="set" if hero.art_file(kind) else "empty",
                                  text_color=TEXT if hero.art_file(kind) else FAINT)
            status.pack(side="left", padx=8)

            def choose():
                path = filedialog.askopenfilename(
                    title=f"{name}: choose {kind}",
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
                    messagebox.showerror("Art", f"Could not copy the image:\n{exc}")
                    return
                setattr(hero, kind, fname)
                self.cfg.save()
                self.conductor.invalidate_palette(name)
                self.conductor.refresh_current_visuals()
                status.configure(text="set", text_color=TEXT)

            def clear():
                setattr(hero, kind, "")
                self.cfg.save()
                self.conductor.invalidate_palette(name)
                self.conductor.refresh_current_visuals()
                status.configure(text="empty", text_color=FAINT)

            self._ghost(fr, "Choose", choose, width=72).pack(side="right",
                                                             padx=(6, 0))
            self._ghost(fr, "Clear", clear, width=60).pack(side="right")

        for kind in ART_KINDS:
            row(kind)

        def close():
            try:
                win.destroy()
            except Exception:
                pass
            self._render_rows()

        self._primary(win, "Done", close, width=90).pack(
            anchor="e", padx=24, pady=14)
        win.protocol("WM_DELETE_WINDOW", close)

    def _add_hero(self) -> None:
        name = self.new_hero.get().strip()
        if not name:
            return
        if name in self.cfg.heroes:
            messagebox.showinfo("Add hero", f"{name} is already in the list.")
            return
        self.cfg.heroes[name] = Hero()
        self.cfg.save()
        self.new_hero.set("")
        self._render_rows()

    def _remove_hero(self, name: str) -> None:
        if not messagebox.askyesno("Remove hero", f"Remove {name}?"):
            return
        hero = self.cfg.heroes.pop(name, None)
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

    # ----- Settings -------------------------------------------------------------
    def _settings(self, page) -> None:
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(page, fg_color=BG, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(scroll, text="Settings", font=self.f_title,
                     text_color=TEXT).pack(anchor="w", padx=32, pady=(28, 4))

        sp = self._section(scroll, "Spotify")
        s = self.cfg.spotify
        self.sp_id = tk.StringVar(value=s.client_id)
        self.sp_secret = tk.StringVar(value=s.client_secret)
        self.sp_redirect = tk.StringVar(value=s.redirect_uri)
        self.sp_device = tk.StringVar(value=s.device_name)
        self._field(self._setting_row(sp, "Client ID"),
                    self.sp_id).pack(fill="x")
        self._field(self._setting_row(sp, "Client secret"),
                    self.sp_secret, show="•").pack(fill="x")
        self._field(self._setting_row(sp, "Redirect URI"),
                    self.sp_redirect).pack(fill="x")
        self._field(self._setting_row(sp, "Device"),
                    self.sp_device, placeholder="Active device").pack(fill="x")
        ctk.CTkLabel(sp, text="Premium account required.",
                     font=self.f_small, text_color=FAINT).pack(
            anchor="w", pady=(6, 0))
        self._primary(sp, "Save and connect", self._connect_spotify,
                      width=140).pack(anchor="w", pady=(10, 4))

        stg = self._section(scroll, "Stage")
        self._menu(self._setting_row(stg, "Render FPS"),
                   [str(v) for v in (60, 90, 120, 144, 160)],
                   tk.StringVar(value=str(self.cfg.stage.fps)),
                   lambda v: self._set_stage("fps", int(v)),
                   width=110).pack(anchor="w")
        self._slider(stg, "Background blur", 0, 30, self.cfg.stage.bg_blur,
                     lambda v: self._set_stage("bg_blur", v, refresh=True))
        self._slider(stg, "Background dim", 0, 100, self.cfg.stage.bg_dim,
                     lambda v: self._set_stage("bg_dim", v, refresh=True))
        self._slider(stg, "Logo glow", 0, 100, self.cfg.stage.glow,
                     lambda v: self._set_stage("glow", v))
        self._slider(stg, "Beat pulse", 0, 100, self.cfg.stage.pulse,
                     lambda v: self._set_stage("pulse", v))
        self._switch(stg, "Particles", self.cfg.stage.particles,
                     lambda v: self._set_stage("particles", v))
        self._switch(stg, "Now playing", self.cfg.stage.show_now_playing,
                     lambda v: self._set_stage("show_now_playing", v))
        self._switch(stg, "Switch animation", self.cfg.stage.switch_anim,
                     lambda v: self._set_stage("switch_anim", v))

        gen = self._section(scroll, "General")
        self._switch(gen, "Connect and listen on launch",
                     self.cfg.autostart, self._set_autostart)
        self.port_var = tk.IntVar(value=self.cfg.detector_port)
        self._field(self._setting_row(gen, "Overwolf app port"),
                    self.port_var, width=90).pack(anchor="w")
        self._ghost(gen, "Open data folder", self._open_data_dir,
                    width=140).pack(anchor="w", pady=(10, 30))

    def _slider(self, parent, label, lo, hi, current, apply) -> None:
        holder = self._setting_row(parent, label)
        val = ctk.CTkLabel(holder, text=str(current), font=self.f_small,
                           text_color=FAINT, width=34, anchor="w")
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

        ctk.CTkSlider(holder, from_=lo, to=hi, number_of_steps=hi - lo,
                      variable=tk.IntVar(value=current), width=210,
                      progress_color=ACCENT, button_color=TEXT,
                      button_hover_color=TEXT, fg_color=SURFACE,
                      command=moved).pack(side="left")
        val.pack(side="left", padx=(10, 0))

    def _switch(self, parent, label, current, apply) -> None:
        holder = self._setting_row(parent, label)
        var = tk.BooleanVar(value=current)
        ctk.CTkSwitch(holder, text="", variable=var, width=40,
                      progress_color=ACCENT, fg_color=SURFACE,
                      button_color=TEXT, button_hover_color=TEXT,
                      command=lambda: apply(bool(var.get()))).pack(anchor="w")

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

    # ----- actions ------------------------------------------------------------
    def _connect_spotify(self) -> None:
        s = self.cfg.spotify
        if hasattr(self, "sp_id"):
            s.client_id = self.sp_id.get().strip()
            s.client_secret = self.sp_secret.get().strip()
            s.redirect_uri = self.sp_redirect.get().strip()
            s.device_name = self.sp_device.get().strip()
        try:
            self.cfg.detector_port = int(self.port_var.get())
        except Exception:
            pass
        self.cfg.save()
        self._log_line("Connecting to Spotify. Approve in the browser if asked.")
        self.conductor.connect_spotify_async(
            lambda ok, _m: self._uiq.put(("status", None)))

    def _open_stage(self) -> None:
        if self.stage and self.stage.alive:
            self.stage.focus()
            return
        self.audio.start()
        self.stage = StageWindow(self.cfg, self.feed)
        self._log_line("Stage opened. F11 for fullscreen on its monitor.")
        if not self.audio.available:
            self._log_line("No audio capture available, bars will stay still.")

    # ----- event pump -----------------------------------------------------------
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
        except queue.Empty:
            pass
        self._refresh_status()
        self.root.after(250, self._tick)

    def _refresh_status(self) -> None:
        ow = (time.time() - self.conductor.detector.last_event_at) < 90
        sp = self.conductor.spotify.connected
        sig = (ow, self._game_running, sp)
        if sig == self._status_sig:
            return
        self._status_sig = sig
        states = (("overwolf", ow, "linked", "waiting"),
                  ("game", self._game_running, "running", "off"),
                  ("spotify", sp, "connected", "off"))
        for key, on, on_text, off_text in states:
            self.status_rows[key].configure(
                text=on_text if on else off_text,
                text_color=TEXT if on else FAINT)

    def _log_line(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        tag = ("play" if msg.startswith("Now playing") else
               "warn" if any(w in msg.lower() for w in
                             ("fail", "error", "unavailable", "can't")) else None)
        self.log.config(state="normal")
        self.log.insert("end", f"{ts}  ", ("time",))
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
    """Per-monitor-v2 DPI awareness so windows fill scaled monitors."""
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
