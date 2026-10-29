#!/bin/bash
# Install or remove agterm-session-search. Re-running is safe: every edit replaces its own earlier one.
set -euo pipefail

usage() {
    cat <<'EOF'
usage: install.sh [options]

  --chord CHORD       keymap chord for the search overlay (default: ctrl+opt+p)
  --emoji-names       also register the Claude Code hook that names sessions "<emoji> <title>"
  --dest DIR          where the scripts go (default: ~/.config/agterm/scripts)
  --config-dir DIR    agterm config directory holding keymap.conf (default: ~/.config/agterm)
  --link              symlink the scripts instead of copying them (for working on this repo)
  --uninstall         remove the scripts, the keymap entry and the hooks; --purge also drops the cache
EOF
}

CHORD="ctrl+opt+p"
EMOJI=0
LINK=0
UNINSTALL=0
PURGE=0
CONFIG_DIR="$HOME/.config/agterm"
DEST=""
while [ $# -gt 0 ]; do
    case "$1" in
        --chord) CHORD="$2"; shift ;;
        --emoji-names) EMOJI=1 ;;
        --dest) DEST="$2"; shift ;;
        --config-dir) CONFIG_DIR="$2"; shift ;;
        --link) LINK=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --purge) PURGE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done
DEST="${DEST:-$CONFIG_DIR/scripts}"

REPO="$(cd "$(dirname "$0")" && pwd)"
KEYMAP="$CONFIG_DIR/keymap.conf"
CLAUDE_SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
HOOK_CMD="$DEST/claude-emoji-name.py"
BEGIN="# >>> agterm-session-search >>>"
END="# <<< agterm-session-search <<<"
FILES=(session-search.py session-search.html claude-emoji-name.py)

need() { command -v "$1" >/dev/null || { echo "missing dependency: $1" >&2; exit 1; }; }
need python3

# drop our marked block from the keymap, leaving everything else byte for byte
strip_keymap() {
    [ -f "$KEYMAP" ] || return 0
    awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$KEYMAP" > "$KEYMAP.tmp"
    mv "$KEYMAP.tmp" "$KEYMAP"
}

# remove every hook entry running our script, then optionally add it back to the three events
edit_hooks() {
    local add="$1"
    [ -f "$CLAUDE_SETTINGS" ] || { [ "$add" = 1 ] || return 0; echo '{}' > "$CLAUDE_SETTINGS"; }
    need jq
    cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.bak-session-search"
    jq --arg cmd "$HOOK_CMD" --argjson add "$add" '
        def ours: (.command // "") | endswith("claude-emoji-name.py");
        .hooks = ((.hooks // {})
            | with_entries(.value |= (map(.hooks |= map(select(ours | not))) | map(select(.hooks | length > 0)))))
        | if $add == 1 then
            reduce ("SessionStart", "Stop", "SessionEnd") as $e (.;
                .hooks[$e] += [{"hooks": [{"type": "command", "command": $cmd}]}])
          else . end
        | .hooks |= with_entries(select(.value | length > 0))
    ' "$CLAUDE_SETTINGS.bak-session-search" > "$CLAUDE_SETTINGS.tmp"
    mv "$CLAUDE_SETTINGS.tmp" "$CLAUDE_SETTINGS"
}

if [ "$UNINSTALL" = 1 ]; then
    strip_keymap
    edit_hooks 0
    for f in "${FILES[@]}"; do rm -f "$DEST/$f"; done
    [ "$PURGE" = 1 ] && rm -rf "$HOME/.cache/agterm-session-search"
    echo "removed. Reload the keymap (File > Reload Keymap) and restart running Claude Code sessions."
    exit 0
fi

mkdir -p "$DEST" "$CONFIG_DIR"
for f in "${FILES[@]}"; do
    rm -f "$DEST/$f"
    if [ "$LINK" = 1 ]; then ln -s "$REPO/bin/$f" "$DEST/$f"; else cp "$REPO/bin/$f" "$DEST/$f"; fi
done
chmod +x "$DEST/session-search.py" "$DEST/claude-emoji-name.py"

strip_keymap
{
    echo "$BEGIN"
    echo "# full-text session search: name, context, Claude Code summary and conversation, scrollback"
    echo "command \"Search Sessions\" $CHORD $DEST/session-search.py \"\$AGT_SESSION_ID\""
    echo "$END"
} >> "$KEYMAP"

edit_hooks "$EMOJI"

echo "installed to $DEST"
echo "next: File > Reload Keymap in agterm, then press $CHORD."
[ "$EMOJI" = 1 ] && echo "emoji names: restart running Claude Code sessions (or run /hooks) to pick up the hook."
exit 0
