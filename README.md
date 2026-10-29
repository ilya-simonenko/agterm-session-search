# agterm-session-search

Full-text search across your [agterm](https://agterm.com) sessions: names, context lines, Claude Code
conversation summaries, prompts and scrollback, including recently closed sessions and past Claude
conversations. One chord opens a search overlay; Return jumps to the session, reopens it, or resumes the
conversation.

An optional Claude Code hook also names each session after its conversation, with an emoji picked to match
the topic: `🧪 Flaky login test investigation`.

## What it does

Press ⌃⌥P and a search panel opens over the current session. Type a few words; every word has to match
somewhere in a session, and each row shows where: the summary, the conversation or the scrollback.

Results come in three groups, always in this order:

1. **Open sessions** in every open window, ranked by where the words matched: the session name and its
   `session context` line count most, then the Claude conversation title and summary, then the workspace
   and directory, then the conversation text and the last 400 lines of scrollback.
2. **Recently closed** sessions, from File ▸ Open Recent. Return reopens one under its original id, in its
   original directory and workspace.
3. **Past Claude conversations** from the last 30 days. Return opens a new session in the directory the
   conversation was started in and runs `claude --resume <id>` there.

Closed results are dimmed, carry a dashed status ring, and say when they were closed and what Return does.
Arrows or ⌃J/⌃K/⌃N/⌃P move, Return acts, Esc closes.

### Where the Claude summary comes from

A session's conversation is found exactly when Claude Code is running in it: Claude Code's
`~/.claude/sessions/<pid>.json` names the transcript, and the process environment names the agterm session.
A session without Claude Code falls back to the newest conversation of its directory, labelled
"claude in dir" and ranked lower, since that is a guess.

Summaries are two or three sentences written by Claude Haiku, one call per conversation, cached until the
conversation grows. They are written in the background after the overlay opens, so a new conversation is
summarized for the next search. Until then it is still found by its title and its text.

## Requirements

- macOS and agterm 0.38.1 or later, for the page bridge (`agterm.request`) the overlay drives the app with
- Python 3.9 or later; macOS ships one as `/usr/bin/python3`
- Claude Code, for summaries and emoji names; the search works without it, over names, context and
  scrollback
- `jq`, only for `--emoji-names` and `--uninstall`, which edit Claude Code's `settings.json`

## Install

```sh
git clone https://github.com/ilya-simonenko/agterm-session-search
cd agterm-session-search
./install.sh                 # the search overlay on ctrl+opt+p
./install.sh --emoji-names   # plus the session-naming hook
```

Then run File ▸ Reload Keymap (or `agtermctl keymap reload`) and press ⌃⌥P.

The installer copies three files to `~/.config/agterm/scripts`, adds one marked block to
`~/.config/agterm/keymap.conf`, and with `--emoji-names` adds one hook to Claude Code's SessionStart, Stop
and SessionEnd. Re-running it replaces its own earlier edits, so it is also how you upgrade or change an
option. It backs up `settings.json` to `settings.json.bak-session-search` before each edit.

| Option | Effect |
| --- | --- |
| `--chord CHORD` | another chord, e.g. `ctrl+p` (first move the built-in switcher with a keymap line such as `map ctrl+opt+p session_palette`) |
| `--emoji-names` | register the session-naming hook; a later run without it removes the hook |
| `--config-dir DIR` | your agterm config directory, if you moved it in Settings |
| `--dest DIR` | where the scripts go |
| `--link` | symlink the scripts to this checkout instead of copying them |
| `--uninstall [--purge]` | remove everything the installer added; `--purge` also deletes the cache |

Running Claude Code sessions pick up a hook change after `/hooks` or a restart.

## Emoji session names

With `--emoji-names`, whenever Claude Code titles a conversation or changes its title, the session it runs
in is renamed to `<emoji> <title>`. Haiku picks the emoji once per title; the picks are kept in
`~/.cache/agterm-session-search/emojis.json`, which you can edit to override one.

- A session you renamed by hand is never touched. The hook only replaces Claude's own title, the
  directory name, or a name it set itself.
- When Claude Code exits, the name is cleared, so the session goes back to following its terminal title.
- A renamed session no longer shows Claude Code's own status glyph in its name; agterm's status dot still
  shows whether it is working.

## Configuration

Environment variables, set in the keymap line or your shell profile:

| Variable | Default | Effect |
| --- | --- | --- |
| `SESSION_SEARCH_SUMMARIZE` | `1` | `0` turns off every model call |
| `SESSION_SEARCH_MODEL` | `haiku` | the summary model |
| `SESSION_SEARCH_DAYS` | `30` | how far back past conversations go |
| `SESSION_EMOJI_MODEL` | `haiku` | the emoji model |
| `CLAUDE_DIRS` | `~/.claude` | Claude Code config directories to read, space separated |

`session-search.py --dump` prints the index as JSON without opening anything, which is the quickest way to
see what a search will find.

## Limits

- Scrollback is searched for open sessions only; it is gone once a session closes.
- Sessions in closed windows are not indexed.
- A reopened session comes back as a fresh shell in its directory unless it is reopened within agterm's
  undo grace; Claude Code is not restarted in it.
- Resuming a past conversation needs its directory to still exist; a deleted git worktree cannot be resumed.
- Each nested `claude -p` call for a summary or emoji runs without agterm's environment, so it fires no
  agterm status hooks.

## Development

```sh
./install.sh --link   # the installed scripts follow your checkout
make lint test
```

The tests run the installer against a throwaway `HOME`; they never touch your agterm or Claude Code config.

## License

MIT
