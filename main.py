"""HTTP/UI backend entrypoint. Forwards to ``karma.interfaces.http.server``."""

from __future__ import annotations

import sys

try:
    from karma.interfaces.http.server import main
except ModuleNotFoundError as exc:
    if exc.name in {"flask", "openai", "pydantic", "yaml"}:
        print(
            f"Missing Python dependency '{exc.name}'. "
            "Run with the repo virtualenv (`.venv/bin/python main.py`) "
            "or install dependencies (`python3 -m pip install -r requirements.txt`).",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    raise

if __name__ == "__main__":
    main()
