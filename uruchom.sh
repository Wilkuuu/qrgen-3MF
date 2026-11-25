#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Zainstaluj Python 3.11 lub nowszy."
  exit 1
fi

py_ok="$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')"
if [[ "$py_ok" != "1" ]]; then
  echo "Potrzebny jest Python 3.11 lub nowszy. Masz: $(python3 --version)"
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Tworzę środowisko i instaluję biblioteki..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python app.py
