"""Serve exact read fixtures for the clerk's dry gate, with a complete command audit."""

import json
import os
import sys
from pathlib import Path

program, *args = sys.argv[1:]
request = [program, *args]
responses = json.loads(Path(os.environ["GATE_FIXTURE_RESPONSES"]).read_text())
for expected, fixture_output, fixture_status in responses:
    if request == expected:
        output, status = fixture_output, fixture_status
        break
else:
    output, status = "", 99
with Path(os.environ["GATE_FIXTURE_AUDIT"]).open("a") as audit:
    print(json.dumps({"command": request, "matched": status != 99}), file=audit)
sys.stdout.write(output)
sys.exit(status)
