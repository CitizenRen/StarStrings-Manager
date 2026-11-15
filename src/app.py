"""StarStrings Manager - a small installer/updater for MrKraken's StarStrings."""
from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

import core

# --------------------------------------------------------------------------- theme
BG = "#0d1117"
CARD = "#161b24"
BORDER = "#252d3a"
FOOTER = "#090c11"
TEXT = "#e6edf3"
MUTED = "#8b96a8"
CYAN = "#22c3e6"
VIOLET = "#8b5cf6"
GREEN = "#22c55e"
AMBER = "#f59e0b"
AMBER_H = "#d48806"
RED = "#ef4444"
RED_H = "#c53030"
PURPLE = "#8759ed"
PURPLE_H = "#6f45d1"
SLATE = "#2a3342"
SLATE_H = "#354155"
DARK_TEXT = "#0b0f14"

FONT = "Segoe UI"
CHANNELS = {"LIVE", "PTU", "EPTU", "TECH-PREVIEW", "HOTFIX"}


def resource(name: str) -> Path:
    """Locate a generated asset (see make_icons.py), both when frozen and from source."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / "generated" / name


def icon(name: str, size: int) -> ctk.CTkImage | None:
    try:
        return ctk.CTkImage(Image.open(resource(name)), size=(size, size))
    except Exception:
        return None


def spaced(text: str) -> str:
    """Fake letter-spacing for button captions: 'Get it' -> 'G E T   I T'."""
    return "   ".join(" ".join(word) for word in text.upper().split())


# --------------------------------------------------------------------------- borderless window helpers
GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = -20, 0x00040000, 0x00000080
user32 = ctypes.windll.user32
user32.LoadImageW.restype = ctypes.c_void_p
user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]


def _hwnd(win) -> int:
    return user32.GetParent(win.winfo_id())


def _round_corners(win, border: bool = True) -> None:
    """Windows 11 rounded corners + shadow for a borderless window (ignored elsewhere)."""
    try:
        dwm, h = ctypes.windll.dwmapi, _hwnd(win)
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(h, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        if not border:
            none = ctypes.c_uint(0xFFFFFFFE)  # DWMWA_BORDER_COLOR = DWMWA_COLOR_NONE
            dwm.DwmSetWindowAttribute(h, 34, ctypes.byref(none), ctypes.sizeof(none))
    except Exception:
        pass


def _show_in_taskbar(win) -> None:
    try:
        h = _hwnd(win)
        style = user32.GetWindowLongW(h, GWL_EXSTYLE)
        user32.SetWindowLongW(h, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        win.withdraw()
        win.after(10, win.deiconify)
    except Exception:
        pass


def _set_icon(win) -> None:
    ico = resource("window-icon.ico")
    if not ico.exists():
        return
    try:
        win.iconbitmap(str(ico))
    except Exception:
        pass
    # Tk only hands Windows a small icon, which the taskbar stretches (blurry). Load the
    # right-sized frames from the .ico for the current DPI and set both icons directly.
    try:
        h = _hwnd(win)
        dpi = user32.GetDpiForWindow(h) or 96
        # ICON_BIG is what the Windows 11 taskbar draws, at 24px x scale; giving it exactly
        # that size avoids a blurry downscale. ICON_SMALL (title/alt-tab) uses SM_CXSMICON.
        for which, size in ((1, round(24 * dpi / 96)), (0, user32.GetSystemMetricsForDpi(49, dpi))):
            hicon = user32.LoadImageW(None, str(ico), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if hicon:
                user32.SendMessageW(h, 0x0080, which, hicon)  # WM_SETICON
    except Exception:
        pass


class Draggable:
    """Lets a borderless window be moved by dragging the given widgets."""

    def __init__(self, win, *widgets):
        self.win, self._off = win, (0, 0)
        for w in widgets:
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<B1-Motion>", self._drag)

    def _press(self, e):
        self._off = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag(self, e):
        self.win.geometry(f"+{e.x_root - self._off[0]}+{e.y_root - self._off[1]}")


# --------------------------------------------------------------------------- dialog
class Dialog(ctk.CTkToplevel):
    """Borderless dark modal with custom buttons. Returns the chosen key via .result."""

    def __init__(self, master, title: str, message: str, buttons: list[tuple[str, str, str, str]],
                 accent: str = CYAN):
        super().__init__(master, fg_color=BORDER)
        self.result = None
        self.overrideredirect(True)
        self.transient(master)

        body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        ctk.CTkFrame(body, height=3, fg_color=accent, corner_radius=0).pack(fill="x")
        head = ctk.CTkLabel(body, text=title.upper(), font=(FONT, 13, "bold"), text_color=TEXT, anchor="w")
        head.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(body, text=message, font=(FONT, 12), text_color=MUTED, justify="left",
                     anchor="w", wraplength=360).pack(fill="x", padx=20, pady=(0, 18))
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 18))
        for key, label, color, hover in reversed(buttons):
            ctk.CTkButton(row, text=label, fg_color=color, hover_color=hover, height=32,
                          font=(FONT, 12, "bold"), corner_radius=8,
                          text_color=TEXT if color in (SLATE, PURPLE) else DARK_TEXT,
                          command=lambda k=key: self._done(k)).pack(side="right", padx=(8, 0))
        Draggable(self, head)
        self.bind("<Escape>", lambda e: self._done(None))

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(10, lambda: _round_corners(self))
        self.grab_set()
        self.focus_force()

    def _done(self, key):
        self.result = key
        self.grab_release()
        self.destroy()

    @classmethod
    def ask(cls, master, title, message, buttons, accent=CYAN):
        d = cls(master, title, message, buttons, accent)
        master.wait_window(d)
        return d.result


# --------------------------------------------------------------------------- main window
class App(ctk.CTk):
    W = 440

    def __init__(self):
        super().__init__(fg_color=BG)
        self.title(core.APP_NAME)
        self.overrideredirect(True)
        _set_icon(self)

        self.settings = core.load_settings()
        self.installs: dict[str, Path] = {}
        self.latest: core.LatestRelease | None = None
        self.latest_error: str | None = None
        self.busy = False
        self._q: queue.Queue = queue.Queue()

        self._build_ui()
        self.update_idletasks()
        h = self.winfo_reqheight()
        x = (self.winfo_screenwidth() - self.W) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"{self.W}x{h}+{x}+{y}")
        self.after(10, lambda: (_show_in_taskbar(self), _round_corners(self, False), _set_icon(self)))
        self.bind("<Map>", lambda e: self.after(10, lambda: _round_corners(self, False)))

        self.after(50, self._pump)
        self._refresh_installs(select=self.settings.get("selected"))
        self.check_updates()
        self._run(core.check_app_update, self._on_app_update)

    # ---------------------------------------------------------------- threading helpers
    def _pump(self):
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def _ui(self, fn):
        self._q.put(fn)

    def _run(self, work, done, *args):
        def target():
            try:
                res, err = work(*args), None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            self._ui(lambda: done(res, err))
        threading.Thread(target=target, daemon=True).start()

    # ---------------------------------------------------------------- layout
    def _build_ui(self):
        root = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        root.pack(fill="both", expand=True)

        # custom title bar
        bar = ctk.CTkFrame(root, fg_color=BG, corner_radius=0, height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        logo = ctk.CTkLabel(bar, text="", image=icon("titlebar-logo.png", 24))
        logo.pack(side="left", padx=(16, 10))
        title = ctk.CTkLabel(bar, text="STARSTRINGS MANAGER", font=(FONT, 14, "bold"), text_color=TEXT)
        title.pack(side="left")
        ctk.CTkButton(bar, text="", image=icon("ui-icons/close.png", 16), width=34, height=30, corner_radius=6,
                      fg_color="transparent", hover_color=RED, command=self.destroy
                      ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(bar, text="", image=icon("ui-icons/minus.png", 16), width=34, height=30, corner_radius=6,
                      fg_color="transparent", hover_color=SLATE_H, command=self.minimise
                      ).pack(side="right", padx=(0, 2))
        Draggable(self, bar, logo, title)

        # install card
        card = self._card(root, "GAME INSTALL", CYAN)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(2, 4))
        self.install_menu = ctk.CTkOptionMenu(
            row, values=["Searching…"], command=self._on_select, height=34, corner_radius=8,
            fg_color=SLATE, button_color=SLATE_H, button_hover_color=PURPLE_H, text_color=TEXT,
            dropdown_fg_color=CARD, dropdown_hover_color=SLATE_H, dropdown_text_color=TEXT,
            font=(FONT, 12), dropdown_font=(FONT, 12), dynamic_resizing=False)
        self.install_menu.pack(side="left", fill="x", expand=True)
        self.open_btn = self._icon_btn(row, "ui-icons/folder.png", self.open_game_folder, SLATE, VIOLET, 34, 16)
        self.open_btn.pack(side="left", padx=(6, 0))
        self.add_btn = ctk.CTkButton(row, text="+", width=34, height=34, corner_radius=8,
                                     font=(FONT, 17, "bold"), fg_color=PURPLE, hover_color=PURPLE_H,
                                     text_color="#ffffff", command=self.add_custom)
        self.add_btn.pack(side="left", padx=(6, 0))
        self.del_btn = self._icon_btn(row, "ui-icons/minus.png", self.remove_custom, SLATE, RED, 34, 14)
        self.del_btn.pack(side="left", padx=(6, 0))
        self.path_lbl = ctk.CTkLabel(card, text="", font=(FONT, 11), text_color=MUTED, anchor="w")
        self.path_lbl.pack(fill="x", padx=16, pady=(0, 10))

        # status card
        card = self._card(root, "STARSTRINGS", VIOLET)
        self.badge = ctk.CTkLabel(card.header, text="", font=(FONT, 11, "bold"),
                                  corner_radius=11, height=22, fg_color=SLATE, text_color=TEXT)
        self.badge.pack(side="right", padx=14)
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(2, 12))
        grid.columnconfigure(1, weight=1)
        self.rows = {}
        for i, (key, label) in enumerate([("game", "Game version"), ("installed", "Installed"),
                                          ("latest", "Latest release"), ("built", "Built for version")]):
            ctk.CTkLabel(grid, text=label, font=(FONT, 12), text_color=MUTED, anchor="w"
                         ).grid(row=i, column=0, sticky="w", pady=3)
            v = ctk.CTkLabel(grid, text="—", font=(FONT, 12, "bold"), text_color=TEXT, anchor="e")
            v.grid(row=i, column=1, sticky="e", pady=3)
            self.rows[key] = v

        # progress (invisible while idle so the window never changes size) + message
        self.progress = ctk.CTkProgressBar(root, height=4, corner_radius=2, progress_color=BG, fg_color=BG)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=18, pady=(2, 0))
        self.msg = ctk.CTkLabel(root, text="", font=(FONT, 11), text_color=MUTED, anchor="w", height=20)
        self.msg.pack(fill="x", padx=20)

        # actions
        actions = ctk.CTkFrame(root, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(2, 16))
        self.main_btn = ctk.CTkButton(actions, text=spaced("Install"), height=44, corner_radius=10,
                                      font=(FONT, 11, "bold"), fg_color=PURPLE, hover_color=PURPLE_H,
                                      text_color="#ffffff", command=self.primary_action)
        self.main_btn.pack(side="left", fill="x", expand=True)
        self.check_btn = self._icon_btn(actions, "ui-icons/refresh.png", self.check_updates, SLATE, SLATE_H, 44, 20)
        self.check_btn.pack(side="left", padx=(8, 0))
        self.remove_btn = self._icon_btn(actions, "ui-icons/trash.png", self.remove_strings, SLATE, RED, 44, 20)
        self.remove_btn.pack(side="left", padx=(8, 0))

        # footer info bar
        foot = ctk.CTkFrame(root, fg_color=FOOTER, corner_radius=0, height=40)
        foot.pack(fill="x")
        foot.pack_propagate(False)
        self._icon_btn(foot, "github-mark.png", lambda: webbrowser.open(core.SS_REPO_URL),
                       "transparent", VIOLET, 30, 18).pack(side="left", padx=8)
        credit = ctk.CTkFrame(foot, fg_color="transparent")
        credit.pack(side="left", expand=True)
        for text, color, url in (("Manager by ", MUTED, None), ("CitizenRen", CYAN, core.AUTHOR_URL),
                                 ("  ·  StarStrings by ", MUTED, None), ("MrKraken", VIOLET, core.SS_AUTHOR_URL)):
            lbl = ctk.CTkLabel(credit, text=text, font=(FONT, 11, "bold" if url else "normal"), text_color=color)
            lbl.pack(side="left")
            if url:
                self._link(lbl, url, bold=True)
        self.ver_lbl = ctk.CTkLabel(foot, text=f"v{core.APP_VERSION}", font=(FONT, 11, "underline"),
                                    text_color=MUTED, cursor="hand2")
        self.ver_lbl.pack(side="right", padx=12)
        self._ver_url = core.APP_REPO_URL
        self.ver_lbl.bind("<Button-1>", lambda e: webbrowser.open(self._ver_url))

    def _card(self, parent, title: str, accent: str):
        outer = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        outer.pack(fill="x", padx=18, pady=(0, 10))
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", padx=2, pady=(10, 4))  # padx keeps the card border visible
        ctk.CTkFrame(header, width=3, height=14, fg_color=accent, corner_radius=2).pack(side="left", padx=(14, 8))
        ctk.CTkLabel(header, text=title, font=(FONT, 11, "bold"), text_color=accent).pack(side="left")
        outer.header = header
        return outer

    def _icon_btn(self, parent, img, cmd, color, hover, size, img_size):
        return ctk.CTkButton(parent, text="", image=icon(img, img_size), width=size, height=size,
                             corner_radius=8 if size < 40 else 10, fg_color=color, hover_color=hover,
                             command=cmd)

    @staticmethod
    def _link(label, url: str, bold: bool = False):
        """Make a label act as a hyperlink: hand cursor, underline on hover, opens url."""
        weight = "bold" if bold else "normal"
        label.configure(cursor="hand2")
        label.bind("<Button-1>", lambda e: webbrowser.open(url))
        label.bind("<Enter>", lambda e: label.configure(font=(FONT, 11, weight, "underline")))
        label.bind("<Leave>", lambda e: label.configure(font=(FONT, 11, weight)))

    @staticmethod
    def _show(widget, visible: bool, **pack):
        if visible and not widget.winfo_manager():
            widget.pack(side="left", **pack)
        elif not visible and widget.winfo_manager():
            widget.pack_forget()

    def minimise(self):
        try:
            user32.ShowWindow(_hwnd(self), 6)  # SW_MINIMIZE
        except Exception:
            pass

    # ---------------------------------------------------------------- installs
    @property
    def game(self) -> Path | None:
        return self.installs.get(self.install_menu.get())

    def _label_for(self, p: Path, custom: bool) -> str:
        parts = p.parts
        short = str(p) if len(str(p)) <= 34 else f"{parts[0]}…\\{'\\'.join(parts[-2:])}"
        channel = p.name.upper() if p.name.upper() in CHANNELS else ("Custom" if custom else "LIVE")
        return f"{channel}  ·  {short}"

    def _refresh_installs(self, select: str | None = None):
        def done(found, err):
            self.installs.clear()
            for p in found or []:
                self.installs[self._label_for(p, False)] = p
            known = {str(p).lower() for p in self.installs.values()}
            for c in self.settings.get("custom", []):
                p = Path(c)
                if str(p).lower() not in known:
                    self.installs[self._label_for(p, True)] = p
            values = list(self.installs) or ["No install found — add one with +"]
            self.install_menu.configure(values=values)
            pick = next((k for k, v in self.installs.items() if select and str(v).lower() == select.lower()),
                        values[0])
            self.install_menu.set(pick)
            self._on_select(pick)
        self._run(core.detect_installs, done)

    def _on_select(self, _value=None):
        g = self.game
        self.path_lbl.configure(text=str(g) if g else "Star Citizen wasn't found automatically.")
        is_custom = g is not None and str(g) in self.settings.get("custom", [])
        self._show(self.del_btn, is_custom, padx=(6, 0))  # only custom locations can be removed
        self.open_btn.configure(state="normal" if g else "disabled")
        if g:
            self.settings["selected"] = str(g)
            core.save_settings(self.settings)
        self._render()

    def add_custom(self):
        d = filedialog.askdirectory(title="Select your Star Citizen LIVE folder", parent=self)
        if not d:
            return
        p = core.normalise_game_folder(Path(d))
        if not core.is_game_folder(p):
            ok = Dialog.ask(self, "That doesn't look like a game folder",
                            f"{p}\n\nNo Data.p4k or Bin64\\StarCitizen.exe was found here. "
                            "Pick the folder that contains Data.p4k (usually …\\StarCitizen\\LIVE).",
                            [("add", "Use anyway", AMBER, AMBER_H), (None, "Cancel", SLATE, SLATE_H)], AMBER)
            if ok != "add":
                return
        customs = self.settings.setdefault("custom", [])
        if str(p) not in customs:
            customs.append(str(p))
        self.settings["selected"] = str(p)
        core.save_settings(self.settings)
        self._refresh_installs(select=str(p))

    def open_game_folder(self):
        g = self.game
        if g and g.is_dir():
            os.startfile(g)

    def remove_custom(self):
        g = self.game
        if g and str(g) in self.settings.get("custom", []):
            self.settings["custom"].remove(str(g))
            self.settings.pop("selected", None)
            core.save_settings(self.settings)
            self._refresh_installs()

    # ---------------------------------------------------------------- update checks
    def check_updates(self):
        if self.busy:
            return
        self._set_badge("Checking…", CYAN)
        self.msg.configure(text="Checking GitHub for the latest StarStrings…", text_color=MUTED)
        self.check_btn.configure(state="disabled")

        def done(latest, err):
            self.check_btn.configure(state="normal")
            if err:
                self.latest_error = core.describe_error(err)
                cached = self.settings.get("latest_cache")
                if cached and not self.latest:  # fall back to the last successful check
                    try:
                        self.latest = core.latest_from_dict(cached["release"])
                    except (KeyError, TypeError):
                        pass
                if cached and self.latest:
                    self.msg.configure(text=f"{self.latest_error}, using last check ({cached['checked']})",
                                       text_color=AMBER)
                else:
                    self.msg.configure(text=f"{self.latest_error}. Try again later.", text_color=RED)
            else:
                self.latest, self.latest_error = latest, None
                self.settings["latest_cache"] = {"release": core.latest_to_dict(latest),
                                                 "checked": datetime.now().strftime("%d %b %H:%M")}
                core.save_settings(self.settings)
                self.msg.configure(text="Checked just now.", text_color=MUTED)
            self._render()

        def work():
            core.refresh_cache()  # pick up launcher patches that happened while the app was open
            return core.fetch_latest()
        self._run(work, done)

    def _on_app_update(self, res, err):
        if res:
            ver, url = res
            self._ver_url = url
            self.ver_lbl.configure(text=f"v{core.APP_VERSION} → v{ver} available", text_color=AMBER)

    # ---------------------------------------------------------------- render state
    def _set_badge(self, text, color):
        self.badge.configure(text=f"  {text}  ", fg_color=color,
                             text_color=TEXT if color == SLATE else DARK_TEXT)

    def _state(self, g: Path | None, inst: core.InstalledState) -> str:
        if not g:
            return "nogame"
        if not inst.present:
            return "missing"
        if not self.latest:
            return "unknown_latest"
        if inst.release != self.latest.release:
            return "outdated"
        return "current"

    def _render(self):
        if self.busy:
            return
        g = self.game
        gv = core.read_game_version(g) if g else core.GameVersion()
        inst = core.read_installed(g) if g else core.InstalledState()
        bf = self.latest.built_for if self.latest else None
        if g and not gv.patch and bf and bf.change and bf.change == gv.change:
            gv.patch = bf.branch.replace("sc-alpha-", "")  # same build StarStrings names
        self.rows["game"].configure(text=gv.short if g else "—")
        self.rows["installed"].configure(text=inst.label if g else "—",
                                         text_color=TEXT if inst.present else RED)
        if self.latest:
            self.rows["latest"].configure(text=core.pretty_release(self.latest.release, self.latest.published),
                                          text_color=TEXT)
            bf = self.latest.built_for
            match = bool(bf.change and gv.change and bf.change == gv.change)
            self.rows["built"].configure(
                text=(bf.short + ("  ✓" if match else "  ⚠")) if bf.change else "Unknown",
                text_color=GREEN if match else (AMBER if bf.change and gv.change else TEXT))
        else:
            self.rows["latest"].configure(text="Offline" if self.latest_error else "Checking…", text_color=MUTED)
            self.rows["built"].configure(text="—", text_color=MUTED)

        state = self._state(g, inst)
        self._show(self.remove_btn, inst.present, padx=(8, 0))  # nothing to delete -> hidden
        badge, bcol, btn_text, col, hov, enabled = {
            "nogame":   ("No game selected", SLATE, "Select a game", SLATE, SLATE_H, False),
            "missing":  ("Not installed", RED, "Download & Install", PURPLE, PURPLE_H, bool(self.latest)),
            "unknown_latest": ("Installed", SLATE, "Waiting for GitHub", SLATE, SLATE_H, False),
            "outdated": ("Update available", AMBER, "Update StarStrings", AMBER, AMBER_H, True),
            "current":  ("Up to date", GREEN, "Reinstall", SLATE, SLATE_H, True),
        }[state]
        if state == "missing" and not self.latest:
            btn_text = "Waiting for GitHub"
        if state == "outdated" and not inst.release:
            badge = "Unknown version"
        self._set_badge(badge, bcol)
        self.main_btn.configure(text=spaced(btn_text), fg_color=col, hover_color=hov,
                                state="normal" if enabled else "disabled")
        if inst.modified and state == "current":
            self.msg.configure(text="global.ini was changed since it was installed — reinstall to restore.",
                               text_color=AMBER)

    # ---------------------------------------------------------------- actions
    def _set_busy(self, busy: bool):
        self.busy = busy
        st = "disabled" if busy else "normal"
        for w in (self.main_btn, self.check_btn, self.remove_btn, self.install_menu, self.add_btn):
            w.configure(state=st)
        self.progress.set(0)
        self.progress.configure(progress_color=CYAN if busy else BG, fg_color=SLATE if busy else BG)

    def primary_action(self):
        g, latest = self.game, self.latest
        if not g or not latest or self.busy:
            return
        inst = core.read_installed(g)
        do_backup = False
        if inst.present:
            choice = Dialog.ask(
                self, "Back up current StarStrings?",
                "Do you want to keep a copy of your current StarStrings files before updating?\n\n"
                f"Backups are saved to:\n{g / core.BACKUP_DIR_NAME}",
                [("backup", "Back up & update", PURPLE, PURPLE_H),
                 ("skip", "Update only", SLATE, SLATE_H),
                 (None, "Cancel", SLATE, SLATE_H)])
            if choice is None:
                return
            do_backup = choice == "backup"

        self._set_busy(True)

        def progress(frac, text):
            self._ui(lambda: (self.progress.set(frac), self.msg.configure(text=text, text_color=MUTED)))

        def work():
            saved = None
            if do_backup:
                progress(0, "Backing up current files…")
                saved = core.backup(g)
            core.install(g, latest, progress)
            return saved

        def done(saved, err):
            self._set_busy(False)
            self._render()
            if err:
                self._show_error("Install failed", err, g)
            else:
                extra = f"  Backup: {saved.name}" if saved else ""
                self.msg.configure(text=f"StarStrings installed.{extra}", text_color=GREEN)
        self._run(work, done)

    def remove_strings(self):
        g = self.game
        if not g or self.busy:
            return
        if Dialog.ask(self, "Remove StarStrings?",
                      "This deletes the StarStrings global.ini and removes the g_language line "
                      "from user.cfg. Your other user.cfg settings are kept.",
                      [("rm", "Remove", RED, RED_H), (None, "Cancel", SLATE, SLATE_H)], RED) != "rm":
            return
        try:
            core.remove(g)
            self.msg.configure(text="StarStrings removed.", text_color=MUTED)
        except Exception as e:  # noqa: BLE001
            self._show_error("Remove failed", e, g)
        self._render()

    def _show_error(self, title, err, game):
        text = core.describe_error(err)
        if isinstance(err, PermissionError):
            text = (f"Windows blocked writing to:\n{game}\n\n"
                    "Close Star Citizen, or right-click StarStrings Manager and choose "
                    "'Run as administrator'.")
        self.msg.configure(text=title, text_color=RED)
        Dialog.ask(self, title, text, [("ok", "OK", SLATE, SLATE_H)], RED)


def main():
    try:  # own taskbar icon/group instead of python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CitizenRen.StarStringsManager")
    except Exception:
        pass
    ctk.set_appearance_mode("dark")
    App().mainloop()


if __name__ == "__main__":
    main()
