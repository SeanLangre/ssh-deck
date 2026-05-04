#!/bin/sh

cd /path/to/Python-Tkinter-Paramiko

source venv/bin/activate

#also need global sudo apt install python3-tk

pip install -r requirements.txt
python ssh_gui.py