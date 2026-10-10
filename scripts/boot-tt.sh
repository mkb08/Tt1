#!/data/data/com.termux/files/usr/bin/sh
# Copy to ~/.termux/boot/ (needs the Termux:Boot app) to start the bot when
# the phone boots. Edit the path and command first.
termux-wake-lock
cd ~/Tt1 && tmux new -d -s tt 'scripts/run.sh paper --config config.json'
