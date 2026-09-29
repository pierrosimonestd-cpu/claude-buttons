"""Button bar docked to the right edge of the Claude desktop window.

Collapsed, it is a small tab on the edge; one click (or Ctrl+Alt+B) opens it, one click
on ✕ closes it. The windows are "owned" by Claude: they stay above Claude, hide when
Claude is minimized and go behind other apps together with Claude.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from . import registry

if sys.platform == "win32":
    from . import dock

HOTKEY = "B"  # Ctrl+Alt+B
TICK_MS = 120
# sizes in logical pixels (96 dpi), multiplied by the scale of the target's monitor
TAB_W, TAB_H, TAB_Y = 16, 58, 128
PANEL_W, PANEL_TOP, PANEL_RIGHT, PANEL_BOTTOM = 280, 52, 10, 14

DARK = dict(
    bg="#262624",
    surface="#30302e",
    hover="#3a3a37",
    border="#44433f",
    text="#f5f4ef",
    muted="#a6a39a",
    accent="#d97757",
    ok="#6cc08b",
    err="#e5766e",
    run="#e0a84a",
)
LIGHT = dict(
    bg="#faf9f5",
    surface="#ffffff",
    hover="#f0eee6",
    border="#dedbd1",
    text="#1f1e1d",
    muted="#6b6a66",
    accent="#c96442",
    ok="#2f8a4c",
    err="#c2413a",
    run="#b7791f",
)
ICON = {"running": "↻", "ok": "✓", "err": "✕"}


class Bar:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.c = LIGHT if dock.claude_theme_light() else DARK
        self.theme_sig = dock.theme_signature()
        self.target: int | None = None
        self.running: set[str] = set()
        self.events: queue.Queue = queue.Queue()
        self.signature = None
        self.content_h: int | None = None
        self.last_geom: dict[str, str] = {}
        self.settings_path = registry.home() / "bar.json"
        settings = self._load_settings()
        self.expanded = settings.get("expanded", False)
        self.targets = settings.get("dock_to") or list(dock.DEFAULT_TARGETS)
        self.px = root.winfo_fpixels("1i") / 96  # Tk scale, for inner padding

        self.tab = self._make_window(root)
        self.panel = self._make_window(tk.Toplevel(root))
        self._build_tab()
        self._build_panel()

        dock.start_hotkey(HOTKEY, lambda: self.events.put(("toggle", None)))
        self._tick()
        self._watch()
        self._watch_theme()

    # --- settings ------------------------------------------------------------------
    def _load_settings(self) -> dict:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def set_expanded(self, value: bool):
        self.expanded = value
        settings = self._load_settings()  # keep keys edited by hand, e.g. dock_to
        settings["expanded"] = value
        with contextlib.suppress(OSError):
            self.settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        self.last_geom.clear()
        self._place()

    # --- theme ---------------------------------------------------------------------
    def _watch_theme(self):
        sig = dock.theme_signature()
        if sig != self.theme_sig:
            self.theme_sig = sig
            palette = LIGHT if dock.claude_theme_light() else DARK
            if palette is not self.c:
                self.c = palette
                self._rebuild()
        self.root.after(2000, self._watch_theme)

    def _rebuild(self):
        for win in (self.tab, self.panel):
            for child in win.winfo_children():
                child.destroy()
            win.configure(bg=self.c["border"])
        self._build_tab()
        self._build_panel()
        self.signature = None
        self.last_geom.clear()

    # --- windows -------------------------------------------------------------------
    def _make_window(self, win):
        win.overrideredirect(True)
        win.configure(bg=self.c["border"])
        win.withdraw()
        return win

    def _p(self, n: float) -> int:
        return max(1, round(n * self.px))

    def _build_tab(self):
        self.tab_canvas = tk.Canvas(self.tab, highlightthickness=0, bd=0, bg=self.c["bg"], cursor="hand2")
        self.tab_canvas.pack(fill="both", expand=True)
        self.tab_canvas.bind("<Button-1>", lambda e: self.set_expanded(True))
        self.tab_canvas.bind("<Configure>", lambda e: self._draw_tab())
        self.tab_canvas.bind("<Enter>", lambda e: self._draw_tab(hover=True))
        self.tab_canvas.bind("<Leave>", lambda e: self._draw_tab())

    def _draw_tab(self, hover: bool = False):
        cv = self.tab_canvas
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        color = self.c["run"] if self.running else self.c["accent"]
        if hover:
            color = _mix(color, "#ffffff", 0.18)
        r = w  # rounded left corners
        cv.create_rectangle(r // 2, 0, w, h, fill=color, outline=color)
        cv.create_oval(0, 0, r, r, fill=color, outline=color)
        cv.create_oval(0, h - r, r, h, fill=color, outline=color)
        cv.create_rectangle(0, r // 2, r, h - r // 2, fill=color, outline=color)
        cx, dot = w // 2 + 1, max(2, w // 7)  # three dots = "menu"
        for dy in (-1, 0, 1):
            cy = h // 2 + dy * dot * 3
            cv.create_oval(cx - dot, cy - dot, cx + dot, cy + dot, fill="#ffffff", outline="")

    def _build_panel(self):
        c = self.c
        outer = tk.Frame(self.panel, bg=c["bg"])
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(outer, bg=c["bg"])
        head.pack(fill="x", padx=self._p(12), pady=(self._p(10), self._p(6)))
        tk.Label(head, text="Claude Buttons", bg=c["bg"], fg=c["text"], font=("Segoe UI Semibold", 10)).pack(
            side="left"
        )
        self.count = tk.Label(head, text="", bg=c["bg"], fg=c["muted"], font=("Segoe UI", 9))
        self.count.pack(side="left", padx=(self._p(4), 0))
        close = self._head_button(head, "✕", lambda: self.set_expanded(False), "Close (Ctrl+Alt+B)")
        close.pack(side="right")
        folder = self._head_button(
            head, "Folder", lambda: os.startfile(registry.home() / "scripts"), "Open the scripts folder"
        )
        folder.pack(side="right", padx=(0, self._p(4)))

        body = tk.Frame(outer, bg=c["bg"])
        body.pack(fill="both", expand=True, padx=self._p(8), pady=(0, self._p(8)))
        self.canvas = tk.Canvas(body, bg=c["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list = tk.Frame(self.canvas, bg=c["bg"])
        self.list_id = self.canvas.create_window(0, 0, window=self.list, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.list_id, width=e.width))
        self.list.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.panel.bind("<MouseWheel>", self._wheel)
        self.head = head
        self.content_h = None

    def _head_button(self, parent, text, command, tip):
        c = self.c
        lbl = tk.Label(
            parent,
            text=text,
            bg=c["bg"],
            fg=c["muted"],
            font=("Segoe UI", 9),
            padx=self._p(6),
            pady=self._p(2),
            cursor="hand2",
        )
        lbl.bind("<Button-1>", lambda e: command())
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=c["hover"], fg=c["text"]))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=c["bg"], fg=c["muted"]))
        _Tip(lbl, tip, c)
        return lbl

    def _wheel(self, event):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # --- docking -------------------------------------------------------------------
    def _tick(self):
        while not self.events.empty():
            kind, data = self.events.get()
            if kind == "toggle":
                self.set_expanded(not self.expanded)
            elif kind == "done":
                self.running.discard(data)
                self._draw_tab()
                self.signature = None
        self._place()
        self.root.after(TICK_MS, self._tick)

    def _place(self):
        hwnd = dock.find_target_window(self.targets, self.target)
        if not hwnd or dock.is_minimized(hwnd):
            self.tab.withdraw()
            self.panel.withdraw()
            return
        self.target = hwnd
        show, hide = (self.panel, self.tab) if self.expanded else (self.tab, self.panel)
        hide.withdraw()

        s = dock.dpi_scale(hwnd)
        left, top, right, bottom = dock.window_rect(hwnd)
        if show is self.tab:
            w, h = round(TAB_W * s), round(TAB_H * s)
            x, y = right - w, top + round(TAB_Y * s)
        else:
            w = round(PANEL_W * s)
            x, y = right - w - round(PANEL_RIGHT * s), top + round(PANEL_TOP * s)
            max_h = max(round(120 * s), bottom - y - round(PANEL_BOTTOM * s))
            h = min(max_h, self._panel_content_height())
        geom = f"{w}x{h}+{x}+{y}"

        name = "panel" if show is self.panel else "tab"
        if show.state() == "withdrawn":
            show.deiconify()
            self.last_geom.pop(name, None)
        own = dock.toplevel_hwnd(show)
        dock.set_owner(own, hwnd)
        if self.last_geom.get(name) != geom:
            show.geometry(geom)
            self.last_geom[name] = geom
            dock.round_corners(own)
            dock.bring_above(own)

    def _panel_content_height(self) -> int:
        if self.content_h is None:
            self.list.update_idletasks()
            content = self.list.winfo_reqheight()
            self.canvas.configure(height=content)
            self.content_h = self.head.winfo_reqheight() + self._p(16) + content + self._p(10)
        return self.content_h

    # --- content -------------------------------------------------------------------
    def _watch(self):
        try:
            logs = registry.home() / "logs"
            sig = (
                registry.buttons_file().stat().st_mtime if registry.buttons_file().exists() else 0,
                tuple(sorted((e.name, e.stat().st_mtime) for e in os.scandir(logs))),
                tuple(sorted(self.running)),
            )
        except OSError:
            sig = None
        if sig != self.signature:
            self.signature = sig
            self.render()
        self.root.after(1000, self._watch)

    def render(self):
        c = self.c
        for child in self.list.winfo_children():
            child.destroy()
        buttons = registry.load()
        self.count.configure(text=f"· {len(buttons)}" if buttons else "")
        if not buttons:
            tk.Label(
                self.list,
                text="No buttons yet.\nAsk Claude, for example:\n“make me a button that zips my Downloads”",
                bg=c["bg"],
                fg=c["muted"],
                font=("Segoe UI", 9),
                justify="center",
                pady=self._p(16),
            ).pack(fill="x")
        for b in buttons:
            self._row(b)
        self.content_h = None
        self.last_geom.pop("panel", None)

    def _row(self, b: dict):
        c = self.c
        status = "running" if b["id"] in self.running else registry.last_status(b["id"])
        row = tk.Frame(
            self.list, bg=c["surface"], cursor="hand2", highlightthickness=1, highlightbackground=c["border"]
        )
        row.pack(fill="x", pady=(0, self._p(6)))
        inner = tk.Frame(row, bg=c["surface"])
        inner.pack(fill="x", padx=(self._p(10), 0), pady=self._p(7))

        color_hex = b.get("color") or registry.DEFAULT_COLOR
        swatch = tk.Frame(inner, bg=color_hex, width=self._p(8), height=self._p(8))
        swatch.grid(row=0, column=0, sticky="n", pady=(self._p(5), 0), padx=(0, self._p(8)))
        name = tk.Label(
            inner, text=b["name"], bg=c["surface"], fg=c["text"], anchor="w", font=("Segoe UI Semibold", 10)
        )
        name.grid(row=0, column=1, sticky="ew")
        widgets = [row, inner, name]
        if b.get("description"):
            desc = tk.Label(
                inner,
                text=_clip(b["description"], 90),
                bg=c["surface"],
                fg=c["muted"],
                anchor="w",
                justify="left",
                font=("Segoe UI", 9),
                wraplength=self._p(PANEL_W - 90),
            )
            desc.grid(row=1, column=1, sticky="ew")
            widgets.append(desc)
        icon_color = {"ok": c["ok"], "err": c["err"], "running": c["run"]}.get(status, c["muted"])
        icon = tk.Label(
            inner, text=ICON.get(status, ""), bg=c["surface"], fg=icon_color, font=("Segoe UI", 9), width=2
        )
        icon.grid(row=0, column=2, sticky="n")
        more = tk.Label(
            inner, text="⋯", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 11), padx=self._p(6)
        )
        more.grid(row=0, column=3, rowspan=2, sticky="ns")
        inner.columnconfigure(1, weight=1)
        widgets += [icon, swatch]

        def paint(bg):
            for w in widgets + [more]:
                w.configure(bg=bg)
            swatch.configure(bg=color_hex)

        for w in widgets:
            w.bind("<Button-1>", lambda e, bid=b["id"]: self.run(bid))
            w.bind("<Button-3>", lambda e, bid=b["id"]: self._menu(e, bid))
            w.bind("<MouseWheel>", self._wheel)
        more.bind("<Button-1>", lambda e, bid=b["id"]: self._menu(e, bid))
        for w in widgets + [more]:
            w.bind("<Enter>", lambda e: paint(c["hover"]), add="+")
            w.bind("<Leave>", lambda e: paint(c["surface"]), add="+")
        if b.get("description"):
            _Tip(name, b["description"], c)

    # --- actions -------------------------------------------------------------------
    def run(self, button_id: str):
        if button_id in self.running:
            return
        self.running.add(button_id)
        self._draw_tab()

        def worker():
            with contextlib.suppress(Exception):
                registry.run(button_id)
            self.events.put(("done", button_id))

        threading.Thread(target=worker, daemon=True).start()

    def _menu(self, event, button_id: str):
        c = self.c
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=c["surface"],
            fg=c["text"],
            activebackground=c["hover"],
            activeforeground=c["text"],
            bd=0,
        )
        menu.add_command(label="Run", command=lambda: self.run(button_id))
        menu.add_command(label="Show output", command=lambda: self.show_text(button_id, "log"))
        menu.add_command(label="View script", command=lambda: self.show_text(button_id, "script"))
        menu.add_command(label="Edit in Notepad", command=lambda: self.edit(button_id))
        menu.add_separator()
        menu.add_command(label="Delete", foreground=c["err"], command=lambda: self.delete(button_id))
        menu.tk_popup(event.x_root, event.y_root)

    def show_text(self, button_id: str, kind: str):
        try:
            b = registry.get(button_id)
            text = registry.last_log(button_id) if kind == "log" else registry.read_script(button_id)
        except (registry.RegistryError, OSError) as e:
            text, b = str(e), {"name": button_id}
        c = self.c
        win = tk.Toplevel(self.root)
        win.title(f"{'Output' if kind == 'log' else 'Script'} — {b['name']}")
        win.configure(bg=c["bg"])
        s = dock.dpi_scale(self.target) if self.target else 1
        win.geometry(f"{round(680 * s)}x{round(420 * s)}")
        box = tk.Text(
            win,
            bg=c["bg"],
            fg=c["text"],
            font=("Consolas", 10),
            wrap="word",
            bd=0,
            insertbackground=c["text"],
            padx=self._p(12),
            pady=self._p(10),
        )
        box.insert("1.0", text or "Not run yet.")
        box.configure(state="disabled")
        box.pack(fill="both", expand=True)
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        if self.target:
            dock.set_owner(dock.toplevel_hwnd(win), self.target)
        win.focus_force()

    def edit(self, button_id: str):
        with contextlib.suppress(registry.RegistryError):
            subprocess.Popen(["notepad.exe", str(registry.script_path(registry.get(button_id)))])

    def delete(self, button_id: str):
        if messagebox.askyesno(
            "Delete button", f"Delete the button '{button_id}' and its script?", parent=self.panel
        ):
            with contextlib.suppress(registry.RegistryError):
                registry.remove(button_id)
            self.signature = None


class _Tip:
    def __init__(self, widget, text, c):
        self.widget, self.text, self.c, self.tip, self.job = widget, text, c, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _schedule(self, _=None):
        self.job = self.widget.after(500, self._show)

    def _show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip.geometry(f"+{x}+{y}")
        tk.Label(
            self.tip,
            text=self.text,
            bg=self.c["text"],
            fg=self.c["bg"],
            font=("Segoe UI", 9),
            justify="left",
            wraplength=320,
            padx=8,
            pady=5,
        ).pack()

    def _hide(self, _=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True))


def main():
    if sys.platform != "win32":
        sys.exit("Claude Buttons: the bar only runs on Windows.")
    if not dock.acquire_single_instance():
        return
    dock.set_dpi_aware()
    root = tk.Tk()
    root.title("Claude Buttons")
    Bar(root)
    root.mainloop()
