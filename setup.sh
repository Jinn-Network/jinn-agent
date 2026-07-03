#!/bin/sh
# jinn-agent setup — one-time install (deps, sandboxing, agent core).
# Thin wrapper over the upstream core's installer, made fork-aware
# (mono#1360):
#   - state and bundled skills land in the jinn-agent home, not the
#     upstream one;
#   - a stock upstream install's `hermes` command link is preserved
#     (the installer would otherwise repoint it at this repo's venv);
#   - the command this fork puts on PATH is `jinn-agent`.
set -e
cd "$(dirname "$0")"
REPO_DIR="$(pwd)"

# Same home resolution as bin/jinn-agent: the installer (skills sync,
# state seeding) must target the home the runtime will actually read.
if [ -z "$HERMES_HOME" ]; then
  export HERMES_HOME="${JINN_AGENT_HOME:-$HOME/.jinn-agent}"
fi

# The installer runs `ln -sf <repo>/venv/bin/hermes ~/.local/bin/hermes`,
# clobbering any stock install's command link. Park the existing entry
# (symlink or file) and put it back afterwards — also on failure.
LINK_DIR="$HOME/.local/bin"
PARKED=""
if [ -e "$LINK_DIR/hermes" ] || [ -L "$LINK_DIR/hermes" ]; then
  PARKED="$LINK_DIR/.hermes.jinn-setup-parked"
  mv "$LINK_DIR/hermes" "$PARKED"
fi
restore_hermes_link() {
  rm -f "$LINK_DIR/hermes"
  if [ -n "$PARKED" ] && { [ -e "$PARKED" ] || [ -L "$PARKED" ]; }; then
    mv "$PARKED" "$LINK_DIR/hermes"
  fi
}
trap restore_hermes_link EXIT

./setup-hermes.sh "$@"

mkdir -p "$LINK_DIR"
ln -sf "$REPO_DIR/bin/jinn-agent" "$LINK_DIR/jinn-agent"

printf '\n%s\n' "jinn-agent is installed."
echo ""
echo "Next steps (ignore any instructions above that name another command):"
echo ""
echo "  1. Reload your shell so ~/.local/bin is on PATH:"
echo "     source ~/.zshrc   (or your shell's rc file)"
echo ""
echo "  2. Configure a model provider:"
echo "     jinn-agent setup"
echo ""
echo "  3. Start:"
echo "     jinn-agent"
