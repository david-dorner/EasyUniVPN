"""Entry point for ``python -m easyunivpn`` and the PyInstaller-frozen exe."""

from easyunivpn.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
