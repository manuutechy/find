#!/usr/bin/env python3
"""Find: builds a local search index of the home folder.

Stores every file and folder name (and the readable text of documents)
in SQLite FTS5 at ~/.local/share/find/index.db. Re-runs are incremental:
only new or changed files are re-read, and deleted files are dropped.
"""
import os
import sqlite3
import subprocess
import sys
import time
import zipfile
import re

HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, ".local/share/find")
DB = os.path.join(DATA, "index.db")

# Folders never worth searching (machine stuff, caches, dependencies, SDKs).
SKIP_NAMES = {
    ".git", "node_modules", "vendor", "__pycache__", ".venv", "venv", ".next",
    ".nuxt", "dist-electron", ".dart_tool", ".pub-cache", ".gradle", ".idea",
    ".cache", "target", ".svelte-kit", ".turbo", "Pods", ".expo", "__MACOSX",
    ".terraform", "bower_components", ".parcel-cache", "coverage", ".angular",
}
SKIP_PATHS = {
    os.path.join(HOME, p) for p in (
        "go", "Android", "develop", ".local", ".config", ".npm", ".nvm", ".bun",
        ".cargo", ".rustup", ".m2", ".mozilla", ".var", ".wine", ".claude",
        ".thunderbird", "snap", ".android", ".java", ".pki", ".ssh", ".gnupg",
        "zram-swap",
    )
}
# Readable text is pulled from these (size-capped) so "what's inside" is searchable.
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".rsc", ".log"}
DOC_EXT = {".pdf", ".docx"}
MAX_TEXT_BYTES = 6000
MAX_DOC_SIZE = 25 * 1024 * 1024
CODE_HINT = {"Projects", "Archives", "Software"}  # content of code files is not read


def connect():
    os.makedirs(DATA, exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY, path TEXT UNIQUE, name TEXT, dir TEXT,
            ext TEXT, is_dir INTEGER, size INTEGER, mtime REAL, seen INTEGER);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
            name, dir, content, tokenize="unicode61 remove_diacritics 2 tokenchars '_'");
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS tri USING fts5(name, tokenize="trigram");
        """
    )
    # one-off fill of the typo-tolerant name index for databases built before it existed
    if db.execute("SELECT count(*) FROM tri").fetchone()[0] == 0:
        db.execute("INSERT INTO tri(rowid, name) SELECT id, lower(name) FROM files")
        db.commit()
    return db


def words(s):
    # "Sales-day-report_2026.pdf" -> "Sales day report 2026 pdf" so each part is findable
    return re.sub(r"[-_.()\[\]]+", " ", s)


def doc_text(path, ext, size):
    try:
        if ext in TEXT_EXT:
            with open(path, "rb") as f:
                raw = f.read(MAX_TEXT_BYTES)
            txt = raw.decode("utf-8", "ignore")
            if ext in (".html", ".htm"):
                txt = re.sub(r"<[^>]+>", " ", txt)
            return txt
        if size > MAX_DOC_SIZE:
            return ""
        if ext == ".pdf":
            out = subprocess.run(
                ["pdftotext", "-l", "3", "-q", path, "-"],
                capture_output=True, timeout=8)
            return out.stdout[:MAX_TEXT_BYTES].decode("utf-8", "ignore")
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            return re.sub(r"<[^>]+>", " ", xml)[:MAX_TEXT_BYTES]
    except Exception:
        return ""
    return ""


def walk():
    stack = [HOME]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                name = e.name
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir():
                        if name in SKIP_NAMES or e.path in SKIP_PATHS:
                            continue
                        if name.startswith(".") and d == HOME:
                            continue
                        if name.startswith(".") and name not in (".env",):
                            continue
                        st = e.stat()
                        yield e.path, name, True, 0, st.st_mtime
                        stack.append(e.path)
                    else:
                        if name.startswith("."):
                            continue
                        st = e.stat()
                        yield e.path, name, False, st.st_size, st.st_mtime
                except OSError:
                    continue


def index(verbose=False):
    t0 = time.time()
    db = connect()
    stamp = int(t0)
    old = {p: (i, m) for i, p, m in db.execute("SELECT id, path, mtime FROM files")}
    added = changed = 0
    batch = 0
    for path, name, is_dir, size, mtime in walk():
        rel_dir = os.path.dirname(path).replace(HOME, "~", 1)
        ext = "" if is_dir else os.path.splitext(name)[1].lower()
        prev = old.get(path)
        if prev and abs(prev[1] - mtime) < 1:
            db.execute("UPDATE files SET seen=? WHERE id=?", (stamp, prev[0]))
        else:
            top = path[len(HOME) + 1:].split("/", 1)[0]
            content = ""
            if not is_dir and (ext in DOC_EXT or (ext in TEXT_EXT and top not in CODE_HINT)):
                content = doc_text(path, ext, size)
            if prev:
                db.execute("UPDATE files SET size=?, mtime=?, seen=? WHERE id=?",
                           (size, mtime, stamp, prev[0]))
                db.execute("DELETE FROM fts WHERE rowid=?", (prev[0],))
                db.execute("DELETE FROM tri WHERE rowid=?", (prev[0],))
                rid = prev[0]
                changed += 1
            else:
                cur = db.execute(
                    "INSERT INTO files(path,name,dir,ext,is_dir,size,mtime,seen) VALUES(?,?,?,?,?,?,?,?)",
                    (path, name, rel_dir, ext, int(is_dir), size, mtime, stamp))
                rid = cur.lastrowid
                added += 1
            db.execute("INSERT INTO fts(rowid,name,dir,content) VALUES(?,?,?,?)",
                       (rid, words(name), words(rel_dir), content))
            db.execute("INSERT INTO tri(rowid,name) VALUES(?,?)", (rid, name.lower()))
        batch += 1
        if batch % 5000 == 0:
            db.commit()
            if verbose:
                print(f"  {batch:,} items...", flush=True)
    gone = [r[0] for r in db.execute("SELECT id FROM files WHERE seen<>?", (stamp,))]
    for rid in gone:
        db.execute("DELETE FROM fts WHERE rowid=?", (rid,))
        db.execute("DELETE FROM tri WHERE rowid=?", (rid,))
    db.execute("DELETE FROM files WHERE seen<>?", (stamp,))
    total = db.execute("SELECT count(*) FROM files").fetchone()[0]
    db.execute("INSERT OR REPLACE INTO meta VALUES('indexed_at', ?)", (str(int(time.time())),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('total', ?)", (str(total),))
    db.commit()
    db.close()
    print(f"indexed {total:,} items (+{added:,} new, {changed:,} changed, -{len(gone):,} gone) in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    index(verbose="-v" in sys.argv)
