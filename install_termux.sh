#!/usr/bin/env bash
# One-time setup on Android (Termux from F-Droid or GitHub, not the old Play
# Store build). Usage:  bash install_termux.sh            core only (stdlib)
#                       bash install_termux.sh --ccxt     + exchange trading
set -euo pipefail

pkg update -y
pkg install -y python git tmux termux-api

if [[ "${1:-}" == "--ccxt" ]]; then
  # Prebuilt cryptography avoids a long Rust compile on the phone. The sync
  # ccxt client only needs cryptography + requests, so skip its async extras
  # (aiohttp, uvloop, orjson, coincurve) which would need compiling.
  pkg install -y python-cryptography
  pip install requests certifi
  pip install --no-deps ccxt
  python -c "import ccxt; print('ccxt', ccxt.__version__, 'ok')"
fi

python -m unittest discover -s tests -t . -q && python -m tt demo
echo
echo "Next: termux-wake-lock, then see README.md (Workflow)."
