#!/usr/bin/env python3
"""Claude Code hook: name the agterm session "<emoji> <conversation title>" whenever Claude titles it.

Register for SessionStart, Stop and SessionEnd. The hook returns at once and does the work detached.
The emoji is picked by haiku once per conversation, from what the conversation turns out to be about: until
Claude has said enough to show the subject (a bare "review PR 123" does not), the session carries the title
alone. A session renamed by hand is left alone: the
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
import importlib.util
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

MIN_REPLY = 300  # characters of Claude's replies before the first pick
REGROW = 600     # more reply text before asking again after a WAIT
MAX_WAITS = 2    # then the model has to pick

EMOJI_PROMPT = """Pick ONE emoji for this coding session so it is recognisable at a glance in a list of terminal
tabs. Base it on the subject matter: the feature, system, component or domain the work touches, as the
conversation below shows it. Never base it on the kind of activity, since reviews, pull requests, tickets,
merges and investigations all look alike: avoid 🐙 ➡️ 🔀 👀 🔍 🔎 📋 💻 🔧 📝 ✅ and the like.
{wait}Reply with the single emoji and nothing else.

Title: {title}
{summary}
Conversation:
{conversation}"""
WAIT_RULE = """If the conversation does not show the subject yet (say, only a request to review PR 123 or to look
at a ticket), reply WAIT instead.
"""


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


def search_module():
    # installed next to this hook; its transcript parser is cached and shared with the search index
    path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "session-search.py")
    spec = importlib.util.spec_from_file_location("session_search", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ask(prompt):
    out = subprocess.run([CLAUDE, "-p", "--model", MODEL, "--no-session-persistence", "--tools", ""],
                         input=prompt, capture_output=True, text=True, timeout=60, cwd=CACHE, env=CLEAN_ENV)
    reply = out.stdout.strip().split()
    return reply[0] if out.returncode == 0 and reply else ""


def pick_emoji(title, transcript, claude_sid, state):
    """The conversation's emoji, or None while its subject is still unclear; waiting is recorded in state."""
    cache = {k: v for k, v in load_json(EMOJIS, {}).items() if isinstance(v, dict)}
    if claude_sid in cache:
        return cache[claude_sid]["emoji"]
    parsed = search_module().parse_transcript(transcript)
    said = sum(len(r) for r in parsed["replies"])
    waits = state.get("waits", 0)
    if said < MIN_REPLY or (waits and said < state.get("asked_at", 0) + REGROW):
        return None
    summary = load_json(SUMMARIES, {}).get(transcript, {}).get("summary", "")
    first = parsed["prompts"][0][:1000] if parsed["prompts"] else ""
    replies = "\n".join("CLAUDE: " + r for r in parsed["replies"])[-5000:]
    emoji = ask(EMOJI_PROMPT.format(
        wait=WAIT_RULE if waits < MAX_WAITS else "", title=title,
        summary=("Summary: " + summary) if summary else "", conversation=f"USER: {first}\n{replies}"))
    # anything with letters or digits is WAIT or the model talking, not an emoji
    if not emoji or len(emoji) > 8 or re.search(r"[A-Za-z0-9]", emoji):
        state.update(waits=waits + 1, asked_at=said)
        return None
    cache[claude_sid] = {"emoji": emoji, "title": title}
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
    if state.get("title") == title and name == state.get("name") and state.get("emoji"):
        return
    if not owned(name, state, [title, state.get("title", "")], session.get("cwd") or ""):
        return
    if state.get("claude") != claude_sid:
        state = {"name": state["name"]} if "name" in state else {}
    emoji = pick_emoji(title, transcript, claude_sid, state)
    new_name = f"{emoji} {title}" if emoji else title
    if new_name != name:
        rename(sid, new_name)
    state.update(title=title, name=new_name, claude=claude_sid, emoji=emoji or "")
    save_json(state_path, state)


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
