#!/bin/bash
# Runs install.sh against a throwaway HOME and checks the keymap and Claude settings it leaves behind.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HOME; HOME="$(mktemp -d)"
trap 'rm -rf "$HOME"' EXIT
unset CLAUDE_CONFIG_DIR
fail() { echo "FAIL: $*" >&2; exit 1; }

mkdir -p "$HOME/.config/agterm" "$HOME/.claude"
printf 'map cmd+shift+d toggle_split\n' > "$HOME/.config/agterm/keymap.conf"
cat > "$HOME/.claude/settings.json" <<'JSON'
{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-hook.sh"}]}],
 "SessionStart": [{"hooks": [{"type": "command", "command": "/x/scripts/claude-emoji-name.py"}]}]}}
JSON
# an install from before the hook was renamed
mkdir -p "$HOME/.config/agterm/scripts"
touch "$HOME/.config/agterm/scripts/claude-emoji-name.py"

"$ROOT/install.sh" --emoji-names >/dev/null
"$ROOT/install.sh" --emoji --chord ctrl+opt+s >/dev/null

keymap="$HOME/.config/agterm/keymap.conf"
[ "$(grep -c '^command "Search Sessions"' "$keymap")" = 1 ] || fail "keymap entry not unique"
grep -q 'ctrl+opt+s' "$keymap" || fail "chord not updated"
grep -q '^map cmd+shift+d toggle_split$' "$keymap" || fail "user keymap line lost"
[ -x "$HOME/.config/agterm/scripts/session-search.py" ] || fail "script not installed"

settings="$HOME/.claude/settings.json"
for e in SessionStart Stop SessionEnd; do
    n=$(jq --arg e "$e" '[.hooks[$e][].hooks[] | select(.command | endswith("claude-session-emoji.py"))] | length' "$settings")
    [ "$n" = 1 ] || fail "$e has $n emoji hooks"
done
jq -e '[.. | .command? // empty | select(endswith("claude-emoji-name.py"))] == []' "$settings" >/dev/null \
    || fail "the old hook name survived the upgrade"
[ -e "$HOME/.config/agterm/scripts/claude-emoji-name.py" ] && fail "the old script survived the upgrade"
[ -x "$HOME/.config/agterm/scripts/session-background.py" ] || fail "background renderer not installed"
jq -e '.model == "opus"' "$settings" >/dev/null || fail "unrelated setting lost"
jq -e '[.hooks.Stop[].hooks[].command] | index("other-hook.sh")' "$settings" >/dev/null || fail "other hook lost"

"$ROOT/install.sh" >/dev/null
[ "$(jq '[.. | .command? // empty | select(endswith("claude-session-emoji.py"))] | length' "$settings")" = 0 ] \
    || fail "reinstall without --emoji kept the hook"

"$ROOT/install.sh" --uninstall >/dev/null
grep -q 'Search Sessions' "$keymap" && fail "keymap entry survived uninstall"
[ -e "$HOME/.config/agterm/scripts/session-search.py" ] && fail "script survived uninstall"
jq -e '.hooks.Stop | length == 1' "$settings" >/dev/null || fail "uninstall touched other hooks"
echo "install tests passed"
