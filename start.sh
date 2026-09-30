#!/bin/sh
cd "$(dirname "$0")"

#also need global sudo apt install python3-tk

[ -d venv ] || python3 -m venv venv
. venv/bin/activate

python -c "import paramiko" 2>/dev/null || pip install -r requirements.txt
python ssh_deck.py
