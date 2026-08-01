"""``python -m codesense``.

Kept to one line so the entry point and the CLI cannot drift apart; the
argument parsing is in `codesense.cli`.
"""

from codesense.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
