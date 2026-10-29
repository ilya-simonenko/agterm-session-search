#!/usr/bin/env python3
"""Full-text session search for agterm: name, context, Claude Code summary, prompts and scrollback.

usage:
  session-search.py [AGT_SESSION_ID]   gather sessions and open the search overlay on that session
  session-search.py --summarize        (internal) refresh stale Claude summaries in the background
  session-search.py --dump             print the gathered index as JSON, open nothing

A session's Claude transcript is found exactly through the live claude process: its
~/.claude/sessions/<pid>.json names the transcript and its environment names the agterm session.
A session with no live claude falls back to the newest transcript of its directory.
Summaries cost one haiku call per transcript; they are cached and refreshed in the background, so
a new or changed conversation is summarized for the NEXT open, and is searchable by its prompts now.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".cache", "agterm-session-search")
CLAUDE_DIRS = os.environ.get("CLAUDE_DIRS", os.path.join(HOME, ".claude")).split()
AGTERMCTL = os.environ.get("AGTERMCTL") or shutil.which("agtermctl") or "/usr/local/bin/agtermctl"
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or os.path.join(HOME, ".local/bin/claude")
# custom commands get the invoking instance's socket; agtermctl itself never reads it
SOCKET = ["--socket", os.environ["AGT_SOCKET"]] if os.environ.get("AGT_SOCKET") else []
# a nested `claude -p` without the agterm env fires no agterm hooks (status, session naming)
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith(("AGTERM_", "AGT_"))}
MODEL = os.environ.get("SESSION_SEARCH_MODEL", "haiku")
SUMMARIZE = os.environ.get("SESSION_SEARCH_SUMMARIZE", "1") != "0"
RESUMMARIZE_AFTER = 600  # a changed transcript is re-summarized only after this many seconds of drift
DIGEST_TAIL = 40000      # searchable transcript text kept per session, newest end
CLOSED_DIGEST = 12000    # same for a closed session's or past conversation's transcript, to bound the page
CONVERSATION_DAYS = int(os.environ.get("SESSION_SEARCH_DAYS", "30"))   # past conversations indexed
SUMMARY_DAYS = 14        # past conversations older than this stay searchable by title and prompts only
SUMMARIES_PER_RUN = 30   # newest first; a first run over a month of history takes a few opens
SEEN = os.path.join(CACHE, "session-transcripts.json")  # agterm session -> its conversations, for after it closes
PROMPT_DIGEST = 60000    # transcript text fed to the summarizer

SUMMARY_PROMPT = """Below is a Claude Code conversation (user prompts and trimmed assistant replies).
Write 2-3 plain sentences saying what this session is about: the goal, the components, files or
tickets involved, and where the work stands now. No preamble, no markdown, no bullet points.

"""


def ctl(*args):
    out = subprocess.run([AGTERMCTL, *args, "--json", *SOCKET], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return json.loads(out.stdout)["result"]


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f)
    os.replace(tmp, path)


def find_transcript(session_id):
    for d in CLAUDE_DIRS:
        hits = glob.glob(os.path.join(d, "projects", "*", session_id + ".jsonl"))
        if hits:
            return hits[0]
    return None


def newest_transcript_for(cwd):
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    hits = []
    for d in CLAUDE_DIRS:
        hits += glob.glob(os.path.join(d, "projects", slug, "*.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def live_claude_transcripts():
    """agterm session id -> transcript paths of the claude processes running in it."""
    procs = {}
    for d in CLAUDE_DIRS:
        for path in glob.glob(os.path.join(d, "sessions", "*.json")):
            info = load_json(path, {})
            if info.get("kind", "interactive") == "interactive" and info.get("pid") and info.get("sessionId"):
                procs[int(info["pid"])] = info["sessionId"]
    if not procs:
        return {}
    out = subprocess.run(["ps", "-E", "-ww", "-o", "pid=,command=", "-p", ",".join(map(str, procs))],
                         capture_output=True, text=True).stdout
    mapping = {}
    for line in out.splitlines():
        pid, _, rest = line.strip().partition(" ")
        m = re.search(r"\bAGTERM_SESSION_ID=([0-9A-Fa-f-]{36})", rest)
        if not m or not pid.isdigit():
            continue
        transcript = find_transcript(procs[int(pid)])
        if transcript:
            mapping.setdefault(m.group(1), []).append(transcript)
    return mapping


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def parse_transcript(path):
    """{title, prompts: [...], replies: [...]} from a transcript, cached by mtime and size."""
    st = os.stat(path)
    key = re.sub(r"[^A-Za-z0-9]", "_", path)[-120:]
    cache_path = os.path.join(CACHE, "parsed", key + ".json")
    cached = load_json(cache_path, None)
    if cached and cached.get("stamp") == [st.st_mtime, st.st_size] and "cwd" in cached:
        return cached
    title, prompts, replies, cwd = "", [], [], ""
    with open(path, errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            cwd = cwd or e.get("cwd") or ""
            if kind == "ai-title":
                title = e.get("aiTitle") or title
            elif kind == "custom-title":
                title = e.get("customTitle") or title
            elif kind == "user" and not e.get("isMeta"):
                t = text_of(e.get("message", {}).get("content")).strip()
                # slash-command wrappers, hook output and interrupted-turn markers are not prompts
                if t and not t.startswith("<") and not t.startswith("[Request interrupted"):
                    prompts.append(t[:2000])
            elif kind == "assistant":
                t = text_of(e.get("message", {}).get("content")).strip()
                if t:
                    replies.append(t[:600])
    parsed = {"stamp": [st.st_mtime, st.st_size], "title": title, "prompts": prompts, "replies": replies, "cwd": cwd}
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    save_json(cache_path, parsed)
    return parsed


def digest(parsed, limit):
    parts = []
    for p in parsed["prompts"]:
        parts.append("USER: " + p)
    body = "\n".join(parts) + "\n" + "\n".join("CLAUDE: " + r for r in parsed["replies"])
    return body[-limit:]


def conversation(path, exact, summaries, stale, limit, summarize_window=None):
    parsed = parse_transcript(path)
    mtime = os.path.getmtime(path)
    cached = summaries.get(path, {})
    fresh = summarize_window is None or time.time() - mtime < summarize_window
    if SUMMARIZE and fresh and (not cached or mtime - cached.get("mtime", 0) > RESUMMARIZE_AFTER):
        stale.append((mtime, path))
    return {"id": os.path.basename(path)[:-6], "title": parsed["title"], "summary": cached.get("summary", ""),
            "text": digest(parsed, limit), "mtime": mtime, "exact": exact, "cwd": parsed["cwd"]}


def gather():
    claude = live_claude_transcripts()
    summaries = load_json(os.path.join(CACHE, "summaries.json"), {})
    seen = load_json(SEEN, {})
    sessions, stale, claimed = [], [], set()
    now = time.time()

    for w in ctl("window", "list")["windows"]:
        if not w.get("open"):
            continue
        tree = ctl("tree", "--window", w["id"])["tree"]
        for ws in tree["workspaces"]:
            for s in ws["sessions"]:
                paths, exact = claude.get(s["id"], []), True
                if paths:
                    known = seen.get(s["id"], {}).get("paths", [])
                    seen[s["id"]] = {"paths": list(dict.fromkeys(known + paths))[-5:], "seen": now}
                else:
                    exact = False
                    fallback = newest_transcript_for(s.get("cwd") or "")
                    paths = [fallback] if fallback else []
                claimed.update(paths if exact else [])
                sessions.append({
                    "kind": "open", "id": s["id"], "name": s.get("name") or "", "cwd": s.get("cwd") or "",
                    "context": s.get("context") or "", "status": s.get("status") or "",
                    "active": bool(s.get("active")) and bool(w.get("active")), "realized": s.get("realized", True),
                    "workspace": ws.get("name") or "", "window": w["id"], "windowName": w.get("name") or "",
                    "windowActive": bool(w.get("active")),
                    "claude": [conversation(p, exact, summaries, stale, DIGEST_TAIL) for p in paths],
                })

    cutoff = now - CONVERSATION_DAYS * 86400
    transcripts = [p for d in CLAUDE_DIRS for p in glob.glob(os.path.join(d, "projects", "*", "*.jsonl"))
                   if os.path.getmtime(p) >= cutoff]
    by_title = {}
    for p in sorted(transcripts, key=os.path.getmtime):
        title = parse_transcript(p)["title"]
        if title:
            by_title[title] = p

    open_ids = {s["id"] for s in sessions}
    for r in ctl("recent", "list")["recent"]:
        # a reopened session lingers in the list; it is already shown as open
        if open_ids.intersection(r.get("sessionIDs", [])):
            continue
        paths = [p for sid in r.get("sessionIDs", []) for p in seen.get(sid, {}).get("paths", [])
                 if os.path.exists(p)]
        if not paths:
            # closed before its conversation was recorded: the name is Claude's title behind a glyph or emoji
            title = re.sub(r"^\S{1,3}\s+", "", r.get("title") or "")
            paths = [by_title[title]] if title in by_title else []
        # the same conversation closed twice, or live again in an open session: show it once
        if paths and claimed.intersection(paths):
            continue
        claimed.update(paths)
        convs = [conversation(p, True, summaries, stale, CLOSED_DIGEST) for p in paths]
        sessions.append({
            "kind": "recent", "id": r["id"], "sessionIDs": r.get("sessionIDs", []), "name": r.get("title") or "",
            "cwd": next((c["cwd"] for c in convs if c["cwd"]), ""), "context": "", "status": "",
            "workspace": r.get("subtitle") or "", "closedAt": r.get("closedAt", 0), "recentKind": r.get("kind"),
            "claude": convs,
        })

    for p in transcripts:
        if p not in claimed:
            c = conversation(p, True, summaries, stale, CLOSED_DIGEST, SUMMARY_DAYS * 86400)
            if not c["text"].strip() or not c["cwd"]:
                continue
            sessions.append({
                "kind": "conversation", "id": c["id"], "name": c["title"] or "(untitled conversation)",
                "cwd": c["cwd"], "context": "", "status": "", "workspace": "", "closedAt": c["mtime"],
                "claude": [c],
            })

    # forget sessions not seen for a while, unless the recent list still holds them
    keep = {sid for r in sessions if r["kind"] == "recent" for sid in r["sessionIDs"]}
    seen = {k: v for k, v in seen.items() if k in keep or now - v.get("seen", 0) < CONVERSATION_DAYS * 86400}
    save_json(SEEN, seen)
    stale = [p for _, p in sorted(set(stale), reverse=True)][:SUMMARIES_PER_RUN]
    return sessions, stale


def summarize(paths):
    lock = os.path.join(CACHE, "summarize.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) < 900:
            return
        os.remove(lock)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        for p in paths:
            parsed = parse_transcript(p)
            text = digest(parsed, PROMPT_DIGEST)
            if not text.strip():
                continue
            mtime = os.path.getmtime(p)
            out = subprocess.run([CLAUDE, "-p", "--model", MODEL, "--no-session-persistence", "--tools", ""],
                                 input=SUMMARY_PROMPT + text, capture_output=True, text=True, timeout=120,
                                 cwd=CACHE, env=CLEAN_ENV)
            summary = " ".join(out.stdout.split())
            if out.returncode == 0 and summary:
                summaries = load_json(os.path.join(CACHE, "summaries.json"), {})
                summaries[p] = {"mtime": mtime, "summary": summary}
                save_json(os.path.join(CACHE, "summaries.json"), summaries)
    finally:
        os.remove(lock)


def open_overlay(sessions, target):
    with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "session-search.html")) as f:
        page = f.read()
    data = json.dumps(sessions).replace("<", "\\u003c")
    page = page.replace("/*SESSIONS*/[]", data, 1)
    out = os.path.join(CACHE, "page", "session-search.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(page)
    args = [AGTERMCTL, "session", "overlay", "open", "--html", out, "--js", "--chromeless",
            "--size-percent", "70", *SOCKET]
    if target:
        args += ["--target", target]
    subprocess.run(args, check=True)


def main():
    os.makedirs(CACHE, exist_ok=True)
    if sys.argv[1:2] == ["--summarize"]:
        summarize(sys.argv[2:])
        return
    sessions, stale = gather()
    if sys.argv[1:2] == ["--dump"]:
        json.dump(sessions, sys.stdout, indent=1)
        return
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AGTERM_SESSION_ID", "")
    open_overlay(sessions, target)
    if stale:
        subprocess.Popen([sys.executable, os.path.realpath(__file__), "--summarize", *dict.fromkeys(stale)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


if __name__ == "__main__":
    main()
