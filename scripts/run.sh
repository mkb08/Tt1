#!/usr/bin/env bash
# Keep a bot alive on Android: hold a wake lock and restart on crash.
# Usage:  scripts/run.sh paper --config config.json
#         scripts/run.sh copy --config copy.json
# Run it inside tmux so it survives closing the Termux window:
#         tmux new -s tt 'scripts/run.sh paper --config config.json'
cd "$(dirname "$0")/.." && mkdir -p logs
command -v termux-wake-lock >/dev/null && termux-wake-lock
trap 'command -v termux-wake-unlock >/dev/null && termux-wake-unlock' EXIT
while true; do
  python -m tt "$@" && break          # clean exit (Ctrl-C) stops the loop
  echo "$(date) tt exited with $?; restarting in 30s" | tee -a logs/restarts.log
  sleep 30
done
