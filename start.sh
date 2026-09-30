#!/bin/sh
. venv/bin/activate

#also need global sudo apt install python3-tk

python -c "import paramiko" 2>/dev/null || pip install -r requirements.txt
python ssh_gui.py
