#!/bin/bash
# init-state.sh: creates the clerk's state folder (ORCH_STATE_DIR, default ~/.local/state/agent-orchestration/<owner>-<repo>; see lib.sh) and copies in the
# templates (ledger.md, triggers.md) from ../templates, plus an empty skip.txt. Never overwrites a file that is already there. Prints the folder.
# The folder is outside git on purpose: the ledger and triggers hold session ids. Run it from inside the checkout.
# shellcheck disable=SC2034
OR_MIN_CHECKS_OPTIONAL=1  # read by lib.sh: this script merges nothing, so it needs no check count
# shellcheck source=/dev/null
. "$(dirname "$0")/lib.sh"
mkdir -p "$OR_STATE_DIR" || exit 1
for f in ledger.md triggers.md; do
  [ -e "$OR_STATE_DIR/$f" ] || cp "$OR_SCRIPT_DIR/../templates/$f" "$OR_STATE_DIR/$f" || exit 1
done
[ -e "$OR_STATE_DIR/skip.txt" ] || printf '# PR numbers the sweep leaves alone, one per line; anything after the number is a note (who asked, and why).\n' > "$OR_STATE_DIR/skip.txt" || exit 1
echo "$OR_STATE_DIR"
