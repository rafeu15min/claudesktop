"""claudesktop CLI: `doctor` (read-only diagnostics), `serve` (run the MCP server),
and `panic` (kill switch — stops ydotoold immediately, independent of the MCP
server's own state, so it works even if a tool call is stuck)."""

from __future__ import annotations

import json
import subprocess
import sys


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("doctor", "serve", "panic"):
        print("usage: claudesktop <doctor|serve|panic>", file=sys.stderr)
        sys.exit(1)

    from .env import ensure_session_env

    ensure_session_env()

    command = sys.argv[1]

    if command == "doctor":
        from .doctor import check_environment

        result = check_environment()
        print(json.dumps(result, indent=2))
        sys.exit(0 if all(result.values()) else 1)

    elif command == "serve":
        from .server import main as serve_main

        serve_main()

    elif command == "panic":
        subprocess.run(["systemctl", "--user", "stop", "ydotool.service"])
        subprocess.run(
            ["notify-send", "-u", "critical", "claudesktop", "PANIC: ydotoold stopped"],
            check=False,
        )
