"""Find: search logic. Local index lookups plus the Claude 'ask in plain words' step."""
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from difflib import SequenceMatcher

HOME = os.path.expanduser("~")
DB = os.path.join(HOME, ".local/share/find/index.db")
MODEL = os.environ.get("FIND_MODEL", "claude-opus-5-5")

FRONT = {"Documents": 3.0, "Pictures": 2.0, "Videos": 1.5, "Downloads": 2.0, "Desktop": 2.0, "Projects": 1.0}
BURIED = ("/Archives/", "/backups/", "/Backups/", "/old/", "/Old/")


def _db():
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


_conn = None


def conn():
    global _conn
    if _conn is None:
        _conn = _db()
    return _conn


def stats():
    try:
        rows = dict(conn().execute("SELECT k, v FROM meta").fetchall())
        return int(rows.get("total", 0)), int(rows.get("indexed_at", 0))
    except Exception:
        return 0, 0


def _tokens(q):
    return [t for t in re.findall(r"[\w]+", q.lower()) if t][:8]


def _fts_query(tokens, mode="AND"):
    return f" {mode} ".join(f'"{t}"*' for t in tokens)


def local(q, limit=30, exts=None, newer_than_days=None, kinds=None):
    tokens = _tokens(q)
    if not tokens:
        return []
    rows, seen = [], set()
    and_hits = 0
    sql = """SELECT f.path, f.name, f.dir, f.ext, f.is_dir, f.size, f.mtime,
                    bm25(fts, 10.0, 2.0, 1.0) AS rank,
                    snippet(fts, 2, '', '', '…', 14) AS snip
             FROM fts JOIN files f ON f.id = fts.rowid
             WHERE fts MATCH ? {extra} ORDER BY rank LIMIT 300"""
    extra, args = "", []
    if kinds:  # filter inside the database so a filter never runs out of results
        parts = []
        if "Folders" in kinds:
            parts.append("f.is_dir = 1")
        kexts = sorted(set().union(*(e for n, e in KINDS if n in kinds)))
        if kexts:
            parts.append(f"(f.is_dir = 0 AND f.ext IN ({','.join('?' * len(kexts))}))")
            args += kexts
        extra = "AND (" + " OR ".join(parts) + ")"
    sql = sql.replace("{extra}", extra)
    for mode in ("AND", "OR"):
        # names first, so a file called tax.pdf is not drowned by 500 files inside a "tax-tools" folder
        for q_ in ("{name} : (" + _fts_query(tokens, mode) + ")", _fts_query(tokens, mode)):
            try:
                for r in conn().execute(sql, (q_, *args)).fetchall():
                    if r["path"] not in seen:
                        seen.add(r["path"])
                        rows.append(r)
            except sqlite3.OperationalError:
                pass
        if mode == "AND":
            and_hits = len(rows)
        if len(rows) >= 3 or len(tokens) == 1:
            break
    now = time.time()
    out = []
    best = max((-r["rank"] for r in rows), default=1) or 1
    for r in rows:
        if exts and r["ext"] not in exts and not r["is_dir"]:
            continue
        if newer_than_days and now - r["mtime"] > newer_than_days * 86400:
            continue
        rel = r["path"][len(HOME) + 1:]
        top = rel.split("/", 1)[0]
        score = 6.0 * (-r["rank"]) / best          # text relevance, 0..6
        score += FRONT.get(top, 0)
        depth = rel.count("/")
        score -= 0.3 * depth
        if top == "Projects" and depth > 3:        # deep inside code trees
            score -= 1.5
        if any(b in r["path"] for b in BURIED):
            score -= 2.5
        if now - r["mtime"] < 30 * 86400:
            score += 1.5
        low = r["name"].lower()
        if not any(t in low for t in tokens) and not (r["snip"] or "").strip():
            score -= 5                             # matched only because a parent folder has the word
        if all(t in low for t in tokens):
            score += 6
        elif any(t in low for t in tokens):
            score += 2
        parts = set(re.split(r"[^a-z0-9]+", low))
        if any(t in parts for t in tokens):        # whole word, not just a prefix
            score += 1
        if low.startswith(tokens[0]):
            score += 1.5
        score += _hist_boost(r["path"])
        out.append({**dict(r), "score": score, "why": ""})
    out.sort(key=lambda x: x["score"], reverse=True)
    if and_hits < 5 and not exts and not newer_than_days and not kinds:
        # not every word matched as typed: blend in close spellings, best first
        by_path = {r["path"]: r for r in out}
        for r in fuzzy(q, 20):
            cur = by_path.get(r["path"])
            if cur is None or r["score"] > cur["score"]:
                by_path[r["path"]] = {**r, "snip": cur["snip"] if cur else ""}
        out = sorted(by_path.values(), key=lambda x: x["score"], reverse=True)
    return out[:limit]


# ---------------- Claude ----------------

def _api_key():
    """~/.config/find/api-key first, then ANTHROPIC_API_KEY."""
    path = os.path.join(HOME, ".config/find/api-key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip() or None
    return os.environ.get("ANTHROPIC_API_KEY")


_client = None
_use_cli = False  # flips on when the API key is missing or rejected


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=_api_key(), timeout=30, max_retries=1)
    return _client


def _ask_cli(system, user, schema):
    """Fallback: the signed-in `claude` command (Claude Code), so no API key is needed."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    import shutil
    claude = shutil.which("claude") or os.path.join(HOME, ".local/bin/claude")
    out = subprocess.run(
        [claude, "-p", "--model", MODEL, "--effort", "low", "--tools", "", "--strict-mcp-config",
         "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
         "--system-prompt", system, "--json-schema", json.dumps(schema), "--output-format", "json"],
        input=user, capture_output=True, text=True, timeout=60, env=env, cwd="/tmp")
    try:
        d = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise AIError("Claude didn't answer. Is the claude command signed in?")
    if d.get("is_error"):
        raise AIError(str(d.get("result") or "Claude returned an error")[:120])
    if d.get("structured_output") is not None:
        return d["structured_output"]
    return json.loads(d.get("result") or "{}")


CONTEXT_FILE = os.path.join(HOME, ".config/find/context.md")
DEFAULT_CONTEXT = """This is a personal Linux laptop. Usual folders: ~/Documents, ~/Pictures, ~/Videos, ~/Downloads,
~/Desktop, ~/Projects (code). File names are often short, abbreviated or misspelt."""


def context():
    """What Claude should know about this laptop. Edit ~/.config/find/context.md to describe your own folders."""
    try:
        with open(CONTEXT_FILE) as f:
            text = f.read().strip()
        return text or DEFAULT_CONTEXT
    except OSError:
        return DEFAULT_CONTEXT

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "searches": {"type": "array", "items": {"type": "string"},
                     "description": "2-6 short keyword searches (1-3 words each) likely to match file or folder names or text"},
        "file_types": {"type": "array", "items": {"type": "string"},
                       "description": "extensions like .pdf .png, empty if any"},
        "newer_than_days": {"type": ["integer", "null"]},
    },
    "required": ["searches", "file_types", "newer_than_days"],
    "additionalProperties": False,
}

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {"type": "array", "items": {
            "type": "object",
            "properties": {"n": {"type": "integer"}, "why": {"type": "string"}},
            "required": ["n", "why"], "additionalProperties": False}},
    },
    "required": ["picks"],
    "additionalProperties": False,
}


class AIError(Exception):
    pass


def _ask(system, user, schema, max_tokens=2000):
    global _use_cli
    if not _use_cli and _api_key():
        import anthropic
        try:
            resp = client().beta.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            _use_cli = True
        else:
            if resp.stop_reason == "refusal":
                raise AIError("Claude declined this one")
            text = next((b.text for b in resp.content if b.type == "text"), "")
            return json.loads(text)
    return _ask_cli(system, user, schema)


def plan(question):
    """Step 1: turn plain words into searches, run them locally. Returns (searches, candidates)."""
    today = datetime.now().strftime("%a %d %b %Y")
    p = _ask(
        context() + f"\nToday is {today}. Turn the user's request into keyword searches for a local file index "
        "(matches words at the start of words in file names, folder names and document text). "
        "Include likely synonyms, abbreviations and the correct spelling of misspelt words.",
        question, PLAN_SCHEMA, 1500)
    searches = [s for s in p.get("searches", []) if s.strip()][:6] or [question]
    exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in p.get("file_types") or []} or None
    seen, cands = set(), []
    for rnd in range(15):  # interleave: best of each search first
        for s in searches:
            res = _cache.setdefault((s, tuple(sorted(exts or ())), p.get("newer_than_days")),
                                    local(s, 15, exts, p.get("newer_than_days")))
            if rnd < len(res) and res[rnd]["path"] not in seen:
                seen.add(res[rnd]["path"])
                cands.append(res[rnd])
    _cache.clear()
    return searches, cands[:60]


_cache = {}


def pick(question, cands):
    """Step 2: Claude reads the candidates and picks the real answers, best first, with a reason."""
    if not cands:
        return []
    lines = []
    for i, r in enumerate(cands):
        when = datetime.fromtimestamp(r["mtime"]).strftime("%d %b %Y")
        kind = "folder" if r["is_dir"] else "file"
        snip = (r["snip"] or "").replace("\n", " ")[:120]
        lines.append(f"{i}. [{kind}] {r['dir']}/{r['name']} ({when}){'  text: ' + snip if snip else ''}")
    picked = _ask(
        context() + "\nPick the items that best answer the user's request, best first, at most 8. "
        "Skip anything that does not fit. 'why' is under 12 words, plain English, no quotes.",
        f"Request: {question}\n\nCandidates:\n" + "\n".join(lines), PICK_SCHEMA, 2000)
    out = []
    for p in picked.get("picks", []):
        n = p.get("n")
        if isinstance(n, int) and 0 <= n < len(cands):
            out.append({**cands[n], "why": p.get("why", "")})
    return out


def ask(question, on_step=None):
    searches, cands = plan(question)
    if on_step:
        on_step(searches, cands)
    return pick(question, cands)


# ---------------- typos, kinds, history, recents ----------------

def _grams(s):
    s = s.lower()
    return {s[i:i + 3] for i in range(len(s) - 2)}


def fuzzy(q, limit=15):
    """Names that share most 3-letter pieces with each typed word: 'reciept' finds 'receipt'."""
    tokens = [t for t in _tokens(q) if len(t) >= 4]
    if not tokens:
        return []
    grams = set().union(*(_grams(t) for t in tokens))
    query = " OR ".join(f'"{g}"' for g in sorted(grams) if '"' not in g)
    try:
        rows = conn().execute(
            """SELECT f.path, f.name, f.dir, f.ext, f.is_dir, f.size, f.mtime, 0.0 AS rank, '' AS snip
               FROM tri JOIN files f ON f.id = tri.rowid
               WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT 4000""", (query,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        words_ = re.split(r"[^a-z0-9]+", r["name"].lower())
        sims = [max((SequenceMatcher(None, t, w).ratio() for w in words_ if len(w) >= 3), default=0)
                for t in tokens]
        sim = sum(sims) / len(sims)
        if sim >= 0.72:
            rel = r["path"][len(HOME) + 1:]
            every = min(sims) >= 0.72            # every typed word is (nearly) in the name
            score = sim * (16 if every and len(tokens) > 1 else 10) + FRONT.get(rel.split("/", 1)[0], 0) - 0.3 * rel.count("/") + _hist_boost(r["path"])
            if any(b in r["path"] for b in BURIED):
                score -= 2.5
            out.append({**dict(r), "score": score, "why": "", "fuzzy": True})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


KINDS = [
    ("Documents", {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xlsx", ".xls", ".csv", ".ods",
                   ".pptx", ".ppt", ".odp", ".tsv", ".epub"}),
    ("Images", {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".jfif", ".heic", ".bmp", ".ico"}),
    ("Videos and audio", {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".ogg", ".flac"}),
    ("Code", {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".php", ".dart", ".html", ".htm", ".css", ".json",
              ".vue", ".sh", ".sql", ".yaml", ".yml", ".toml", ".rs", ".java", ".kt", ".lua", ".rsc", ".env"}),
    ("Archives and installers", {".zip", ".tar", ".gz", ".xz", ".zst", ".rar", ".7z", ".iso", ".deb", ".apk",
                                 ".aab", ".run", ".appimage", ".jks"}),
]


def kind(r):
    if r["is_dir"]:
        return "Folders"
    for name, exts in KINDS:
        if r["ext"] in exts:
            return name
    return "Other files"


FILTERS = [("All", None), ("Documents", {"Documents"}), ("Images", {"Images"}),
           ("Folders", {"Folders"}), ("Code", {"Code"})]

HIST = os.path.join(HOME, ".local/share/find/history.json")
_hist = None


def history():
    global _hist
    if _hist is None:
        try:
            with open(HIST) as f:
                _hist = json.load(f)
        except (OSError, ValueError):
            _hist = {}
    return _hist


def remember(path):
    h = history()
    count, _ = h.get(path, [0, 0])
    h[path] = [count + 1, time.time()]
    if len(h) > 400:  # keep the newest 400
        for p in sorted(h, key=lambda k: h[k][1])[: len(h) - 400]:
            del h[p]
    try:
        with open(HIST, "w") as f:
            json.dump(h, f)
    except OSError:
        pass


def _hist_boost(path):
    item = history().get(path)
    if not item:
        return 0.0
    count, last = item
    days = (time.time() - last) / 86400
    return min(count, 5) * 0.8 + (2.0 if days < 7 else 0.5)


def recent_opened(limit=5):
    h = history()
    paths = sorted(h, key=lambda p: h[p][1], reverse=True)
    out = []
    for p in paths:
        if len(out) >= limit:
            break
        r = conn().execute("SELECT path, name, dir, ext, is_dir, size, mtime, 0.0 AS rank, '' AS snip "
                           "FROM files WHERE path=?", (p,)).fetchone()
        if r:
            out.append({**dict(r), "score": 0, "why": ""})
    return out


def recent_changed(limit=8):
    rows = conn().execute(
        """SELECT path, name, dir, ext, is_dir, size, mtime, 0.0 AS rank, '' AS snip FROM files
           WHERE is_dir=0 AND (dir LIKE '~/Documents%' OR dir LIKE '~/Pictures%' OR dir LIKE '~/Downloads%'
                 OR dir LIKE '~/Desktop%' OR dir LIKE '~/Videos%') AND dir NOT LIKE '%/_logs%'
           ORDER BY mtime DESC LIMIT ?""", (limit,)).fetchall()
    return [{**dict(r), "score": 0, "why": ""} for r in rows]
