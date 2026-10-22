#!/bin/sh
# OCS-in-a-Kit installer launcher for macOS and Linux.
# Usage: ./install.sh [options]   (see: ./install.sh --help)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1 && "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    exec "$PY" "$DIR/ocs_kit_installer.py" "$@"
  fi
done
echo "Python 3.8 or newer is required." >&2
case "$(uname -s)" in
  Darwin) echo "Install it with: xcode-select --install   (or brew install python, or https://www.python.org/downloads/)" >&2 ;;
  *)      echo "Install it with: sudo apt install python3   (Fedora: sudo dnf install python3)" >&2 ;;
esac
exit 1
