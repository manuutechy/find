#!/usr/bin/env python3
"""Find: Super+K search for the whole laptop.

Type for instant results (typos are fine). Tab, or a sentence, asks Claude.
Enter opens · Ctrl+Enter shows in folder · Ctrl+C copies the path · Ctrl+Shift+C copies the file
Ctrl+1..5 filters · drag a result into any app · Esc clears, then closes.
"""
import hashlib
import html
import os
import re
import subprocess
import sys
import threading
import time
T0 = time.time()
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import search  # noqa: E402

HOME = os.path.expanduser("~")
APP_ID = "io.github.manuutechy.Find"
THUMBS = os.path.join(HOME, ".cache/find/thumbs")
SHOT = os.environ.get("FIND_SHOT")
DEBUG = os.environ.get("FIND_DEBUG")
KEEP = os.environ.get("FIND_KEEP")


LOG = os.path.join(HOME, ".cache/find/find.log")


def elog(msg):
    """Small event log: Tab presses, Claude start/finish/errors. Never the typed text."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 200_000:
            os.replace(LOG, LOG + ".old")
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def dlog(msg):
    with open(os.path.join(HOME, ".cache/find/debug.log"), "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

CSS = """
window.find { background: transparent; }
.panel {
  background: @window_bg_color;
  border-radius: 16px;
  border: 1px solid alpha(@window_fg_color, 0.10);
  margin: 14px;
  box-shadow: 0 14px 40px alpha(black, 0.22);
}
.panel, .panel * { font-family: "Geist", "Inter", sans-serif; }
.searchrow { padding: 14px 18px 10px 18px; }
.query { font-size: 19px; background: none; border: none; box-shadow: none; caret-color: @accent_color; }
.searchicon { color: alpha(@window_fg_color, 0.45); }
.chips { padding: 0 14px 10px 14px; }
.chip { font-size: 12px; font-weight: 500; padding: 3px 10px; min-height: 0; border-radius: 8px;
        background: none; color: alpha(@window_fg_color, 0.62); box-shadow: none; }
.chip:hover { background: alpha(@window_fg_color, 0.06); }
.chip:checked { background: alpha(@window_fg_color, 0.09); color: @window_fg_color; }
.chipkey { font-family: "Geist Mono", monospace; font-size: 10px; color: alpha(@window_fg_color, 0.38); margin-left: 6px; }
.hairline { background: alpha(@window_fg_color, 0.08); min-height: 1px; min-width: 1px; }
.status { font-size: 12px; color: alpha(@window_fg_color, 0.58); padding: 10px 18px 4px 18px; }
.status.error { color: @error_color; }
list.results { background: none; padding-bottom: 6px; }
list.results > row { padding: 6px 10px; margin: 0 8px; border-radius: 8px; transition: background 100ms; }
list.results > row:hover { background: alpha(@window_fg_color, 0.04); }
list.results > row:selected { background: alpha(@accent_bg_color, 0.14); }
.group { font-size: 12px; font-weight: 500; color: alpha(@window_fg_color, 0.50); padding: 12px 18px 4px 18px; }
.name { font-size: 14px; }
.path { font-size: 12px; color: alpha(@window_fg_color, 0.55); }
.snip { font-size: 12px; color: alpha(@window_fg_color, 0.62); }
.why { font-size: 12px; color: alpha(@window_fg_color, 0.66); }
.when { font-size: 12px; color: alpha(@window_fg_color, 0.48); font-feature-settings: "tnum"; }
.rowicon { color: alpha(@window_fg_color, 0.62); -gtk-icon-style: symbolic; }
.askrow .name { color: @accent_color; font-weight: 500; }
.askrow .rowicon { color: @accent_color; }
.preview { padding: 18px 18px 14px 18px; }
.thumbframe { background: alpha(@window_fg_color, 0.04); border-radius: 12px; border: 1px solid alpha(@window_fg_color, 0.06); }
.bigicon { color: alpha(@window_fg_color, 0.35); -gtk-icon-style: symbolic; }
.ptitle { font-size: 15px; font-weight: 600; }
.pwhy { font-size: 12px; color: @accent_color; }
.mkey { font-size: 12px; color: alpha(@window_fg_color, 0.50); }
.mval { font-size: 12px; font-feature-settings: "tnum"; }
.textpeek { font-family: "Geist Mono", monospace; font-size: 11px; color: alpha(@window_fg_color, 0.75); padding: 10px 12px; }
.child { font-size: 12px; color: alpha(@window_fg_color, 0.75); }
.more { font-size: 12px; color: alpha(@window_fg_color, 0.48); }
.pbtn { font-size: 12px; font-weight: 500; padding: 5px 12px; min-height: 0; border-radius: 8px; }
.emptyline { font-size: 13px; color: alpha(@window_fg_color, 0.58); padding: 32px 24px; }
.footer { padding: 8px 18px; font-size: 12px; color: alpha(@window_fg_color, 0.50); }
.key { font-family: "Geist Mono", monospace; font-size: 11px; padding: 1px 5px; border-radius: 4px;
       background: alpha(@window_fg_color, 0.07); color: alpha(@window_fg_color, 0.72); }
"""

ICONS = [
    ({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".jfif", ".heic", ".bmp", ".ico"}, "image-x-generic-symbolic"),
    ({".mp4", ".mov", ".mkv", ".webm", ".avi"}, "video-x-generic-symbolic"),
    ({".mp3", ".wav", ".m4a", ".ogg", ".flac"}, "audio-x-generic-symbolic"),
    ({".zip", ".tar", ".gz", ".xz", ".zst", ".rar", ".7z", ".iso", ".deb", ".apk", ".aab", ".jks"}, "package-x-generic-symbolic"),
    ({".pdf", ".doc", ".docx", ".odt", ".rtf", ".epub"}, "x-office-document-symbolic"),
    ({".xlsx", ".xls", ".csv", ".ods", ".tsv"}, "x-office-spreadsheet-symbolic"),
    ({".pptx", ".ppt", ".odp"}, "x-office-presentation-symbolic"),
    ({".sh", ".run", ".appimage"}, "utilities-terminal-symbolic"),
]
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".jfif", ".bmp", ".ico"}
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".py", ".js", ".ts", ".go", ".php", ".sh", ".html", ".css",
            ".rsc", ".log", ".yaml", ".yml", ".toml", ".sql", ".dart", ".vue", ".env", ".lua", ".tsx", ".jsx"}
GROUP_ORDER = ["Folders", "Documents", "Images", "Videos and audio", "Code", "Archives and installers", "Other files"]


def icon_name(r):
    if r.get("ask"):
        return "chat-message-new-symbolic"
    if r["is_dir"]:
        return "folder-symbolic"
    for exts, name in ICONS:
        if r["ext"] in exts:
            return name
    return "text-x-generic-symbolic"


def when_label(ts, long=False):
    d = datetime.fromtimestamp(ts)
    now = datetime.now()
    secs = (now - d).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))} min ago"
    if long:
        return d.strftime("%a %-d %b %Y, %H:%M")
    if d.date() == now.date():
        return d.strftime("%H:%M")
    if d.year == now.year:
        return d.strftime("%a %-d %b")
    return d.strftime("%-d %b %Y")


def size_label(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


def pretty_dir(d):
    return d.replace("~/", "", 1).replace("/", "  ›  ") if d.startswith("~/") else d


def highlight(name, tokens):
    """Bold the parts of the name that match what was typed."""
    if not tokens:
        return html.escape(name)
    pat = re.compile("|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)), re.I)
    out, last = [], 0
    for m in pat.finditer(name):
        out.append(html.escape(name[last:m.start()]))
        out.append(f"<b>{html.escape(m.group(0))}</b>")
        last = m.end()
    out.append(html.escape(name[last:]))
    return "".join(out)


class Row(Gtk.ListBoxRow):
    def __init__(self, r, group, tokens):
        super().__init__()
        self.r = r
        self.group = group
        box = Gtk.Box(spacing=12)
        img = Gtk.Image.new_from_icon_name(icon_name(r))
        img.set_pixel_size(18)
        img.add_css_class("rowicon")
        img.set_valign(Gtk.Align.CENTER)
        box.append(img)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        name.set_markup(highlight(r["name"], tokens) if not r.get("ask") else html.escape(r["name"]))
        name.add_css_class("name")
        col.append(name)
        sub = r.get("why") or ""
        if r.get("ask"):
            sub = "Claude reads what you mean, even with typos · Tab"
        lab = Gtk.Label(label=sub or pretty_dir(r["dir"]), xalign=0,
                        ellipsize=Pango.EllipsizeMode.START if not sub else Pango.EllipsizeMode.END)
        lab.add_css_class("why" if r.get("why") else "path")
        col.append(lab)
        if not r.get("ask") and r.get("snip") and not any(t in r["name"].lower() for t in tokens):
            sn = Gtk.Label(label="“" + " ".join(r["snip"].split())[:90] + "”", xalign=0,
                           ellipsize=Pango.EllipsizeMode.END)
            sn.add_css_class("snip")
            col.append(sn)
        box.append(col)
        if not r.get("ask"):
            when = Gtk.Label(label=when_label(r["mtime"]))
            when.add_css_class("when")
            when.set_valign(Gtk.Align.CENTER)
            box.append(when)
            ds = Gtk.DragSource(actions=Gdk.DragAction.COPY)
            ds.connect("prepare", self._drag)
            self.add_controller(ds)
        else:
            self.add_css_class("askrow")
        self.set_child(box)

    def _drag(self, *_):
        f = Gio.File.new_for_path(self.r["path"])
        return Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([f])),
            Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new((f.get_uri() + "\r\n").encode())),
        ])


class Preview(Gtk.Box):
    """Right-hand pane: see the file before you open it."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.win = win
        self.add_css_class("preview")
        self.set_vexpand(True)
        self.token = 0

    def clear(self):
        while (c := self.get_first_child()):
            self.remove(c)

    def show(self, r):
        self.token += 1
        tok = self.token
        self.clear()
        if r is None:
            return
        if r.get("ask"):
            self._ask_card()
            return
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("thumbframe")
        frame.set_size_request(-1, 220)
        frame.set_vexpand(False)
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        big = Gtk.Image.new_from_icon_name(icon_name(r))
        big.set_pixel_size(56)
        big.add_css_class("bigicon")
        big.set_vexpand(True)
        frame.append(big)
        self.append(frame)
        self._fill_thumb(r, frame, big, tok)

        title = Gtk.Label(label=r["name"], xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=3,
                          ellipsize=Pango.EllipsizeMode.END, max_width_chars=40, width_chars=30)
        title.add_css_class("ptitle")
        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tb.append(title)
        if r.get("why"):
            w = Gtk.Label(label=r["why"], xalign=0, wrap=True, max_width_chars=40, width_chars=30)
            w.add_css_class("pwhy")
            tb.append(w)
        self.append(tb)

        grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        kind = "Folder" if r["is_dir"] else search.kind(r)
        kind = {"Documents": "Document", "Images": "Image", "Videos and audio": "Video or audio",
                "Archives and installers": "Archive or installer", "Other files": "File"}.get(kind, kind)
        rows = [("Kind", kind + (f" · {r['ext'][1:].upper()}" if r["ext"] else ""))]
        if not r["is_dir"]:
            rows.append(("Size", size_label(r["size"])))
        rows += [("Changed", when_label(r["mtime"], long=True)), ("Where", pretty_dir(r["dir"]))]
        for i, (k, v) in enumerate(rows):
            kl = Gtk.Label(label=k, xalign=0, yalign=0)
            kl.add_css_class("mkey")
            vl = Gtk.Label(label=v, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, hexpand=True,
                           max_width_chars=36, width_chars=24)
            vl.add_css_class("mval")
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(vl, 1, i, 1, 1)
        self.append(grid)

        self.append(Gtk.Box(vexpand=True))
        btns = Gtk.Box(spacing=8)
        b1 = Gtk.Button(label="Open")
        b1.add_css_class("pbtn")
        b1.add_css_class("suggested-action")
        b1.connect("clicked", lambda *_: self.win.open_path(r, False))
        b2 = Gtk.Button(label="Show in folder")
        b2.add_css_class("pbtn")
        b2.connect("clicked", lambda *_: self.win.open_path(r, True))
        b3 = Gtk.Button(label="Copy file" if not r["is_dir"] else "Copy path")
        b3.add_css_class("pbtn")
        b3.connect("clicked", lambda *_: self.win.copy_file(r) if not r["is_dir"] else self.win.copy_path(r))
        for b in (b1, b2, b3):
            btns.append(b)
        self.append(btns)

    def _ask_card(self):
        lab = Gtk.Label(label="Ask Claude", xalign=0)
        lab.add_css_class("ptitle")
        self.append(lab)
        t = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, width_chars=30)
        t.set_label("Claude turns what you typed into searches, reads the matches and picks the files you mean. "
                    "Works with plain words and typos, like “the poster for the graduation party” or “my tax certificate”.")
        t.add_css_class("mval")
        self.append(t)
        t2 = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, width_chars=30,
                       label="Only file names, folders and a short line of text from matching files are sent.")
        t2.add_css_class("mkey")
        self.append(t2)
        self.append(Gtk.Box(vexpand=True))
        b = Gtk.Button(label="Ask Claude")
        b.add_css_class("pbtn")
        b.add_css_class("suggested-action")
        b.set_halign(Gtk.Align.START)
        b.connect("clicked", lambda *_: self.win.ask_ai())
        self.append(b)

    def _fill_thumb(self, r, frame, big, tok):
        path, ext = r["path"], r["ext"]

        def put_picture(p):
            if tok != self.token:
                return False
            pic = Gtk.Picture.new_for_filename(p)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            pic.set_can_shrink(True)
            pic.set_size_request(-1, 204)
            for side in ("top", "bottom", "start", "end"):
                getattr(pic, f"set_margin_{side}")(8)
            if big.get_parent() is frame:
                frame.remove(big)
            frame.append(pic)
            return False

        if r["is_dir"]:
            self._folder_peek(r, frame, big)
        elif ext in IMAGE_EXT and r["size"] < 40 * 1024 * 1024:
            put_picture(path)
        elif ext == ".pdf":
            os.makedirs(THUMBS, exist_ok=True)
            key = hashlib.sha1(f"{path}{r['mtime']}".encode()).hexdigest()
            png = os.path.join(THUMBS, key + ".png")
            if os.path.exists(png):
                put_picture(png)
            else:
                def work():
                    subprocess.run(["pdftoppm", "-png", "-singlefile", "-r", "40", "-f", "1", "-l", "1",
                                    path, png[:-4]], capture_output=True, timeout=10)
                    if os.path.exists(png):
                        GLib.idle_add(put_picture, png)
                threading.Thread(target=work, daemon=True).start()
        elif ext in TEXT_EXT and r["size"] < 5 * 1024 * 1024:
            try:
                with open(path, "rb") as f:
                    text = f.read(1600).decode("utf-8", "ignore")
            except OSError:
                return
            lines = "\n".join(l[:70] for l in text.splitlines()[:12])
            if lines.strip():
                lab = Gtk.Label(label=lines, xalign=0, yalign=0, vexpand=True)
                lab.add_css_class("textpeek")
                frame.remove(big)
                frame.append(lab)

    def _folder_peek(self, r, frame, big):
        try:
            kids = sorted((e for e in os.scandir(r["path"]) if not e.name.startswith(".")),
                          key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        if not kids:
            return
        frame.remove(big)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, vexpand=True)
        box.set_margin_top(12)
        box.set_margin_start(14)
        box.set_margin_end(14)
        for e in kids[:7]:
            line = Gtk.Box(spacing=8)
            ic = Gtk.Image.new_from_icon_name("folder-symbolic" if e.is_dir() else icon_name(
                {"is_dir": False, "ext": os.path.splitext(e.name)[1].lower()}))
            ic.set_pixel_size(14)
            ic.add_css_class("rowicon")
            line.append(ic)
            lab = Gtk.Label(label=e.name, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            lab.add_css_class("child")
            line.append(lab)
            box.append(line)
        if len(kids) > 7:
            more = Gtk.Label(label=f"and {len(kids) - 7:,} more", xalign=0)
            more.add_css_class("more")
            box.append(more)
        frame.append(box)


class FindWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Find")
        self.app = app
        self.connect("close-request", lambda *_: (self.app.close_window(), True)[1])
        self.set_decorated(False)
        self.set_default_size(1000, 620)
        self.add_css_class("find")
        self.gen = 0
        self.ai_query = None
        self.timers = {}
        self.filter = None
        self.results = None
        self.mode = "home"
        self.lock = threading.Lock()
        self.ai_started = 0
        self.ai_phase = ""

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("panel")
        panel.set_overflow(Gtk.Overflow.HIDDEN)

        sr = Gtk.Box(spacing=12)
        sr.add_css_class("searchrow")
        ic = Gtk.Image.new_from_icon_name("system-search-symbolic")
        ic.set_pixel_size(20)
        ic.add_css_class("searchicon")
        sr.append(ic)
        self.entry = Gtk.Text(hexpand=True)
        self.entry.add_css_class("query")
        self.entry.set_placeholder_text("Search your laptop, or ask in plain words")
        self.entry.connect("changed", self.on_changed)
        sr.append(self.entry)
        self.spinner = Adw.Spinner()
        self.spinner.set_visible(False)
        sr.append(self.spinner)
        panel.append(sr)

        chips = Gtk.Box(spacing=4)
        chips.add_css_class("chips")
        self.chips = []
        group = None
        for i, (label, kinds) in enumerate(search.FILTERS):
            b = Gtk.ToggleButton()
            inner = Gtk.Box()
            inner.append(Gtk.Label(label=label))
            k = Gtk.Label(label=f"Ctrl {i + 1}")
            k.add_css_class("chipkey")
            inner.append(k)
            b.set_child(inner)
            b.add_css_class("chip")
            b.set_focusable(False)
            if group:
                b.set_group(group)
            else:
                group = b
                b.set_active(True)
            b.connect("toggled", self.on_chip, kinds)
            chips.append(b)
            self.chips.append(b)
        panel.append(chips)
        panel.append(self._line())

        body = Gtk.Box(vexpand=True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("status")
        left.append(self.status)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("results")
        self.list.set_activate_on_single_click(False)
        self.list.set_header_func(self._header)
        self.list.connect("row-activated", lambda _l, row: self.activate_row(row, False))
        self.list.connect("row-selected", lambda _l, row: self.preview.show(row.r if row else None))
        self.emptyline = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=50, width_chars=30)
        self.emptyline.add_css_class("emptyline")
        self.list.set_placeholder(self.emptyline)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=False)
        self.scroll.set_child(self.list)
        left.append(self.scroll)
        body.append(left)
        v = Gtk.Box()
        v.add_css_class("hairline")
        body.append(v)
        self.preview = Preview(self)
        psc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=False)
        psc.set_size_request(400, -1)
        psc.set_hexpand(False)
        psc.set_child(self.preview)
        body.append(psc)
        panel.append(body)

        panel.append(self._line())
        foot = Gtk.Box(spacing=6)
        foot.add_css_class("footer")
        total, at = search.stats()
        self.count = Gtk.Label(xalign=0, hexpand=True,
                               label=f"{total:,} files and folders · updated {when_label(at) if at else 'never'}")
        foot.append(self.count)
        for k, label in (("↵", "Open"), ("Ctrl ↵", "Show in folder"), ("Ctrl ⇧ C", "Copy file"), ("Tab", "Ask Claude")):
            kl = Gtk.Label(label=k)
            kl.add_css_class("key")
            foot.append(kl)
            foot.append(Gtk.Label(label=label, margin_end=10))
        panel.append(foot)
        self.set_child(panel)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("notify::is-active", self.on_active)
        self.show_home()

    def _line(self):
        b = Gtk.Box()
        b.add_css_class("hairline")
        return b

    # ---------- list ----------
    def _header(self, row, before):
        if row.group and (before is None or before.group != row.group):
            lab = Gtk.Label(label=row.group, xalign=0)
            lab.add_css_class("group")
            row.set_header(lab)
        else:
            row.set_header(None)

    def render(self, items, status, ask_row=True):
        """items: list of (group, result). Applies the filter, shows group headers, selects the first."""
        self.results = (items, status, ask_row)
        q = self.entry.get_text().strip()
        tokens = [t for t in re.findall(r"\w+", q.lower()) if len(t) >= 2]
        shown = [(g, r) for g, r in items if not self.filter or search.kind(r) in self.filter][:40]
        self.list.remove_all()
        for g, r in shown:
            self.list.append(Row(r, g, tokens))
        if ask_row and q:
            self.list.append(Row({"ask": True, "name": f"Ask Claude about “{q}”", "path": "", "dir": "",
                                  "is_dir": False, "ext": "", "mtime": 0, "size": 0}, "", tokens))
        self.status.remove_css_class("error")
        self.status.set_label(status)
        if DEBUG:
            dlog(f"render status={status!r} rows={len(shown)}")
        first = self.list.get_row_at_index(0)
        if first:
            self.list.select_row(first)
        else:
            self.preview.show(None)
        self.scroll.get_vadjustment().set_value(0)

    def grouped(self, rows):
        if not rows:
            return []
        out = [("Top hit", rows[0])]
        rest = rows[1:]
        order = []
        for r in rest:  # rows are best-first, so first sighting = the group's best score
            g = search.kind(r)
            if g not in order:
                order.append(g)
        for g in order:
            out += [(g, r) for r in rest if search.kind(r) == g]
        return out

    # ---------- states ----------
    def show_home(self):
        self.mode = "home"
        items = [("Opened from Find", r) for r in search.recent_opened(5)]
        have = {r["path"] for _, r in items}
        items += [("Changed recently", r) for r in search.recent_changed(8) if r["path"] not in have]
        self.emptyline.set_label("Type a file name, a word inside a document, or a question.")
        self.render(items, "Type to search · typos are fine · press Tab to ask Claude", ask_row=False)

    # ---------- typing ----------
    def on_changed(self, *_):
        self.gen += 1
        self.ai_query = None
        self._spin(False)
        for key in list(self.timers):
            GLib.source_remove(self.timers.pop(key))
        q = self.entry.get_text().strip()
        if not q:
            self.show_home()
            return
        self.timers["local"] = GLib.timeout_add(35, self._start_local, q, self.gen)
        if len(q.split()) >= 4:
            self.timers["ai"] = GLib.timeout_add(1200, self._auto_ai)

    def _start_local(self, q, gen):
        self.timers.pop("local", None)

        def work():
            with self.lock:
                if gen != self.gen:
                    return
                rows = search.local(q, 40, kinds=self.filter)
            GLib.idle_add(self._local_done, q, gen, rows)
        threading.Thread(target=work, daemon=True).start()
        return False

    def _local_done(self, q, gen, rows):
        if gen != self.gen or self.mode == "ai":
            return False
        self.mode = "local"
        if self.filter:
            rows = [r for r in rows if search.kind(r) in self.filter][:40]
        self.emptyline.set_label(f"Nothing called “{q}”. Press Tab and Claude will look for it.")
        n = len(rows)
        status = ("No " + [l for l, k in search.FILTERS if k == self.filter][0].lower() + " match") if not n and self.filter else "No matches" if not n else ("Best 40 matches" if n >= 40 else f"{n} match{'es' if n != 1 else ''}")
        if any(r.get("fuzzy") for r in rows[:5]):
            status += " · including close spellings"
        self.render(self.grouped(rows), status)
        return False

    def _auto_ai(self):
        self.timers.pop("ai", None)
        self.ask_ai("auto")
        return False

    # ---------- Claude ----------
    def _spin(self, on):
        self.spinner.set_visible(on)
        if not on and "tick" in self.timers:
            GLib.source_remove(self.timers.pop("tick"))

    def ask_ai(self, source="tab"):
        q = self.entry.get_text().strip()
        if DEBUG:
            dlog(f"ask_ai q={q!r} ai_query={self.ai_query!r}")
        if not q:
            elog(f"{source}: empty box")
            self.toast("Type what you're looking for, then press Tab")
            return
        if self.ai_query == q and self.spinner.get_visible():
            elog(f"{source}: already asking")
            self._tick(self.gen)
            return
        elog(f"{source}: asking Claude ({len(q.split())} words)")
        self.ai_query = q
        gen = self.gen
        self.mode = "ai"
        self.ai_started = time.time()
        self.ai_phase = "Claude is reading your question"
        self._spin(True)
        self._tick(gen)
        self.timers["tick"] = GLib.timeout_add(500, self._tick, gen)

        def work():
            try:
                searches, cands = search.plan(q)
                GLib.idle_add(self._ai_step, gen, searches, cands)
                picks = search.pick(q, cands)
                GLib.idle_add(self._ai_done, gen, picks, cands, None)
            except Exception as e:  # network, sign-in, refusal
                elog(f"claude error: {type(e).__name__}: {str(e)[:200]}")
                if DEBUG:
                    import traceback
                    dlog("ai error " + traceback.format_exc())
                GLib.idle_add(self._ai_done, gen, None, None, e)
        threading.Thread(target=work, daemon=True).start()

    def _tick(self, gen):
        if gen != self.gen or self.mode != "ai" or not self.spinner.get_visible():
            return False
        self.status.set_label(f"{self.ai_phase} · {int(time.time() - self.ai_started)}s")
        return True

    def _ai_step(self, gen, searches, cands):
        if gen != self.gen:
            return False
        self.ai_phase = "Searched for " + ", ".join(searches[:4]) + " · choosing the best"
        if cands:
            self.render([("Possible matches", r) for r in cands[:15]], self.ai_phase, ask_row=False)
            self._tick(gen)
        return False

    def _ai_done(self, gen, picks, cands, err):
        if gen != self.gen:
            return False
        self._spin(False)
        if err is not None:
            msg = str(err)
            if any(w in msg.lower() for w in ("connect", "resolve", "timed out", "network")):
                msg = "no internet connection"
            if DEBUG:
                dlog(f"ai failed: {msg}")
            self.status.add_css_class("error")
            self.status.set_label(f"Claude couldn't answer ({msg[:70]}). Press Tab to try again.")
            self.ai_query = None
            self.mode = "local"
            return False
        secs = time.time() - self.ai_started
        elog(f"claude answered in {secs:.1f}s with {len(picks or [])} picks")
        picked = {r["path"] for r in picks or []}
        items = [("Claude's picks", r) for r in picks or []]
        items += [("Other matches", r) for r in (cands or [])[:12] if r["path"] not in picked]
        self.emptyline.set_label("Claude couldn't find it. Try other words, or part of the name.")
        self.render(items, f"Claude answered in {secs:.0f}s" if picks else "Claude didn't find a clear match",
                    ask_row=False)
        return False

    # ---------- actions ----------
    def current(self):
        return self.list.get_selected_row() or self.list.get_row_at_index(0)

    def activate_row(self, row, folder):
        if row is None:
            return
        if row.r.get("ask"):
            self.ask_ai()
            return
        self.open_path(row.r, folder)

    def open_path(self, r, folder):
        path = r["path"]
        search.remember(path)
        if folder:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION)
                bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                              "org.freedesktop.FileManager1", "ShowItems",
                              GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")),
                              None, Gio.DBusCallFlags.NONE, 3000)
            except GLib.Error:
                subprocess.Popen(["xdg-open", os.path.dirname(path)], start_new_session=True)
        else:
            subprocess.Popen(["xdg-open", path], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.app.close_window()

    def toast(self, text):
        self.status.remove_css_class("error")
        self.status.set_label(text)

    def copy_path(self, r):
        Gdk.Display.get_default().get_clipboard().set(r["path"])
        search.remember(r["path"])
        self.toast(f"Copied the path of {r['name']}")

    def copy_file(self, r):
        """Copies the file itself: paste into Gmail, WhatsApp, Files…"""
        f = Gio.File.new_for_path(r["path"])
        uri = f.get_uri()
        prov = Gdk.ContentProvider.new_union([
            Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list([f])),
            Gdk.ContentProvider.new_for_bytes("x-special/gnome-copied-files", GLib.Bytes.new(f"copy\n{uri}".encode())),
            Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new((uri + "\r\n").encode())),
        ])
        Gdk.Display.get_default().get_clipboard().set_content(prov)
        search.remember(r["path"])
        self.toast(f"Copied {r['name']} · paste it into an email or chat")

    def move(self, step):
        row = self.list.get_selected_row()
        i = (row.get_index() if row else -1) + step
        n = 0
        while self.list.get_row_at_index(n):
            n += 1
        if n == 0:
            return
        nxt = self.list.get_row_at_index(max(0, min(n - 1, i)))
        self.list.select_row(nxt)
        ok, rect = nxt.compute_bounds(self.list)
        if ok:  # keep the selected row on screen
            adj = self.scroll.get_vadjustment()
            top, bottom = rect.get_y(), rect.get_y() + rect.get_height()
            if top < adj.get_value():
                adj.set_value(max(0, top - 30))
            elif bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(bottom - adj.get_page_size() + 8)

    def on_chip(self, btn, kinds):
        if btn.get_active():
            self.filter = kinds
            if self.mode == "local" and self.entry.get_text().strip():
                self.gen += 1  # search again, deeper, for this kind
                self._start_local(self.entry.get_text().strip(), self.gen)
            elif self.results:
                self.render(*self.results)

    def on_key(self, _c, keyval, _code, state):
        if DEBUG:
            dlog(f"key {Gdk.keyval_name(keyval)} state={int(state)} focus={type(self.get_focus()).__name__}")
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        row = self.current()
        if keyval == Gdk.KEY_Escape:
            if self.entry.get_text():
                self.entry.set_text("")
            else:
                self.app.close_window()
            return True
        if keyval == Gdk.KEY_Down or (ctrl and keyval == Gdk.KEY_n):
            self.move(1)
            return True
        if keyval == Gdk.KEY_Up or (ctrl and keyval == Gdk.KEY_p):
            self.move(-1)
            return True
        if keyval == Gdk.KEY_Page_Down:
            self.move(8)
            return True
        if keyval == Gdk.KEY_Page_Up:
            self.move(-8)
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.ask_ai()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.activate_row(row, ctrl)
            return True
        if ctrl and Gdk.KEY_1 <= keyval <= Gdk.KEY_5:
            self.chips[keyval - Gdk.KEY_1].set_active(True)
            return True
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C) and row and not row.r.get("ask"):
            if self.entry.get_selection_bounds()[0] and not shift:
                return False
            if shift and not row.r["is_dir"]:
                self.copy_file(row.r)
            else:
                self.copy_path(row.r)
            return True
        return False

    def on_active(self, *_):
        if DEBUG:
            dlog(f"active={self.is_active()}")
        if SHOT or KEEP:
            return
        if not self.is_active():  # click away closes it, like a launcher
            GLib.timeout_add(200, lambda: (self.app.win is self and not self.is_active()) and self.app.close_window())


class FindApp(Adw.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if SHOT:  # test runs never talk to (or open) the copy running in the background
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.win = None
        self.spare = None
        self.resident = False

    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        if "--background" in args:  # started at login: stay ready, show nothing
            if not self.resident:
                self.resident = True
                self.hold()
                warm_up()
                elog("ready in the background")
                GLib.idle_add(self._build_spare)
            return 0
        self.activate()
        return 0

    def close_window(self):
        """Hide by destroying the window; the app stays running when it was started at login."""
        if self.win:
            w, self.win = self.win, None
            w.destroy()
        if not self.resident:
            self.quit()
        else:
            GLib.timeout_add(300, self._build_spare)

    def _build_spare(self):
        if self.spare is None:
            self.spare = FindWindow(self)  # built but not shown
        return False

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Settings.get_default().set_property("gtk-icon-theme-name", "Adwaita")
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        if self.win:  # Super+K again closes it
            self.close_window()
            return
        if DEBUG:
            dlog(f"activate at {int((time.time()-T0)*1000)}ms")
        reused = self.spare is not None
        self.win = self.spare or FindWindow(self)
        self.spare = None
        if reused:  # built a while ago: refresh what's on the start screen
            self.win.show_home()
            total, at = search.stats()
            self.win.count.set_label(f"{total:,} files and folders · updated {when_label(at) if at else 'never'}")
        self.win.present()
        self.win.entry.grab_focus()
        if DEBUG:
            dlog(f"presented at {int((time.time()-T0)*1000)}ms")
            self.win.connect("map", lambda *_: dlog(f"mapped at {int((time.time()-T0)*1000)}ms"))
        if SHOT:
            self._test_mode()
            return
        refresh_index_if_stale()

    # test mode: FIND_SHOT=out.png [FIND_QUERY=..] [FIND_AI=1] [FIND_FILTER=2] [FIND_DOWN=n] [FIND_WAIT=ms]
    def _test_mode(self):
        q = os.environ.get("FIND_QUERY", "")
        if q:
            self.win.entry.set_text(q)
        if os.environ.get("FIND_FILTER"):
            GLib.timeout_add(500, lambda: self.win.chips[int(os.environ["FIND_FILTER"]) - 1].set_active(True) or False)
        if os.environ.get("FIND_DOWN"):
            GLib.timeout_add(700, lambda: [self.win.move(1) for _ in range(int(os.environ["FIND_DOWN"]))] and False)
        if os.environ.get("FIND_AI"):
            GLib.timeout_add(300, lambda: self.win.ask_ai() or False)
        GLib.timeout_add(int(os.environ.get("FIND_WAIT", "1300")), self._shot)

    def _shot_unused(self):
        pass

    def _shot(self):
        w = self.win.get_child()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(w).snapshot(snap, w.get_width(), w.get_height())
        tex = self.win.get_renderer().render_texture(snap.to_node(), None)
        tex.save_to_png(SHOT)
        self.quit()
        return False


def warm_up():
    """Load the slow bits once so the first Super+K is instant."""
    search.stats()
    search.local("warm", 5)
    try:
        search.client()
    except Exception:
        pass


def refresh_index_if_stale():
    _, at = search.stats()
    if time.time() - at > 20 * 60:
        subprocess.Popen(["nice", "-n", "15", "/usr/bin/python3",
                          os.path.join(os.path.dirname(os.path.abspath(__file__)), "indexer.py")],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(FindApp().run(sys.argv))
