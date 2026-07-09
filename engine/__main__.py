"""Enable `python -m engine ...` as an alias for the `engine` console script."""

from engine.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
