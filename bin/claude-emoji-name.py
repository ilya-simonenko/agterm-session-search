#!/usr/bin/env python3
"""Claude Code hook: name the agterm session "<emoji> <conversation title>" whenever Claude titles it.

Register for SessionStart, Stop and SessionEnd. The hook returns at once and does the work detached.
The emoji is picked by haiku once per title and cached. A session renamed by hand is left alone: the
hook only renames a session whose name is still Claude's own title, the folder name, or the name it set
itself. SessionEnd clears the name it set, so the sidebar goes back to following the terminal title.

env: SESSION_EMOJI_MODEL (default haiku), SESSION_EMOJI_DRY_RUN=1 prints the rename instead of doing it.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".cache", "agterm-session-search")
STATE_DIR = os.path.join(CACHE, "names")
EMOJIS = os.path.join(CACHE, "emojis.json")
SUMMARIES = os.path.join(CACHE, "summaries.json")
SEEN = os.path.join(CACHE, "session-transcripts.json")  # shared with session-search.py
AGTERMCTL = os.environ.get("AGTERMCTL") or shutil.which("agtermctl") or "/usr/local/bin/agtermctl"
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or os.path.join(HOME, ".local/bin/claude")
MODEL = os.environ.get("SESSION_EMOJI_MODEL", "haiku")
DRY_RUN = os.environ.get("SESSION_EMOJI_DRY_RUN") == "1"
# a nested `claude -p` without the agterm env fires no agterm hooks, ours or the status ones
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith(("AGTERM_", "AGT_"))}
SOCKET = ["--socket", os.environ["AGTERM_SOCKET"]] if os.environ.get("AGTERM_SOCKET") else []

EMOJI_PROMPT = """Pick ONE emoji that best captures the theme of this coding session so it is recognisable
at a glance in a list of terminal tabs. Prefer a concrete, specific emoji over generic ones like 💻 🔧 📝 ✅.
Reply with the single emoji and nothing else.

Title: {title}
{summary}"""


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, ensure_ascii=False)
    os.replace(tmp, path)


def ctl(*args):
    out = subprocess.run([AGTERMCTL, *args, *SOCKET], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return out.stdout


def current_session(sid):
    windows = json.loads(ctl("window", "list", "--json"))["result"]["windows"]
    for w in windows:
        if not w.get("open"):
            continue
        tree = json.loads(ctl("tree", "--window", w["id"], "--json"))["result"]["tree"]
        for ws in tree["workspaces"]:
            for s in ws["sessions"]:
                if s["id"].lower() == sid.lower():
                    return s
    return None


def rename(sid, name):
    if DRY_RUN:
        print(f"rename {sid} -> {name!r}")
        return
    ctl("session", "rename", name, "--target", sid)


def latest_title(transcript):
    title = ""
    with open(transcript, errors="replace") as f:
        for line in f:
            if '"ai-title"' not in line and '"custom-title"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            title = e.get("aiTitle") or e.get("customTitle") or title
    return title.strip()


def pick_emoji(title, transcript):
    cache = load_json(EMOJIS, {})
    if title in cache:
        return cache[title]
    summary = load_json(SUMMARIES, {}).get(transcript, {}).get("summary", "")
    prompt = EMOJI_PROMPT.format(title=title, summary=("Summary: " + summary) if summary else "")
    out = subprocess.run([CLAUDE, "-p", "--model", MODEL, "--no-session-persistence", "--tools", ""],
                         input=prompt, capture_output=True, text=True, timeout=60, cwd=CACHE, env=CLEAN_ENV)
    reply = out.stdout.strip().split()
    emoji = reply[0] if out.returncode == 0 and reply else ""
    # anything with letters or digits is the model talking, not an emoji
    if not emoji or len(emoji) > 8 or re.search(r"[A-Za-z0-9]", emoji):
        return None
    cache[title] = emoji
    save_json(EMOJIS, cache)
    return emoji


def owned(name, state, titles, cwd):
    """Whether the session's name is ours to replace rather than a manual rename."""
    if not name or name == state.get("name"):
        return True
    if cwd and name == os.path.basename(cwd.rstrip("/")):
        return True
    # Claude's terminal title is "<status glyph> <title>"
    return any(t and name.endswith(t) for t in titles)


def run(event, sid, transcript, claude_sid):
    state_path = os.path.join(STATE_DIR, sid + ".json")
    state = load_json(state_path, {})
    session = current_session(sid)
    if session is None:
        return
    name = session.get("name") or ""

    if event == "SessionEnd":
        if state.get("claude") != claude_sid:
            return
        if name == state.get("name"):
            rename(sid, "")
        if os.path.exists(state_path):
            os.remove(state_path)
        return

    if transcript and os.path.exists(transcript):
        seen = load_json(SEEN, {})
        entry = seen.setdefault(sid, {"paths": []})
        entry["paths"] = list(dict.fromkeys(entry.get("paths", []) + [transcript]))[-5:]
        entry["seen"] = time.time()
        save_json(SEEN, seen)

    title = latest_title(transcript) if transcript and os.path.exists(transcript) else ""
    if not title:
        return
    if state.get("title") == title and name == state.get("name"):
        return
    if not owned(name, state, [title, state.get("title", "")], session.get("cwd") or ""):
        return
    emoji = pick_emoji(title, transcript)
    new_name = f"{emoji} {title}" if emoji else title
    if new_name != name:
        rename(sid, new_name)
    save_json(state_path, {"title": title, "name": new_name, "claude": claude_sid})


def main():
    sid = os.environ.get("AGTERM_SESSION_ID", "")
    if os.environ.get("AGTERM_ENABLED") != "1" or not re.fullmatch(r"[0-9A-Fa-f-]{36}", sid):
        return
    if sys.argv[1:2] == ["--worker"]:
        run(sys.argv[2], sid, sys.argv[3], sys.argv[4])
        return
    try:
        hook = json.load(sys.stdin)
    except ValueError:
        return
    args = [sys.executable, os.path.realpath(__file__), "--worker",
            hook.get("hook_event_name", ""), hook.get("transcript_path") or "", hook.get("session_id") or ""]
    if DRY_RUN:
        subprocess.run(args)
        return
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


if __name__ == "__main__":
    main()
