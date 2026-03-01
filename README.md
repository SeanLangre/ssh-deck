# SSH GUI Manager

A lightweight desktop SSH management tool built with Python, Tkinter, and Paramiko. Manage multiple remote servers, run commands, and execute scripts — all from a single GUI.

## Features

- **Server Management** — Add, edit, and delete SSH server configurations stored in `servers.json`
- **Quick Command Buttons** — Define per-server shortcut buttons with custom labels, colors, and project grouping
- **Script-Based Commands** — Store reusable shell scripts per server in `scripts/<server-name>/`
- **Live Streaming Output** — Real-time stdout/stderr with color-coded tags (blue = command, green = output, red = error)
- **Auto Pop-Out Window** — Long output (>20 lines) automatically opens in a separate window to keep the main panel clean
- **Per-Server Output Tabs** — Each server has its own output panel, switching when you select a different server
- **Command Input** — Type and send ad-hoc commands to the selected server
- **Command History** — Timestamped log of executed commands saved to `history.json`

## Requirements

- Python 3
- [Paramiko](https://www.paramiko.org/)

```
pip install paramiko
```

Tkinter is included with most Python installations. On Debian/Ubuntu, install it with:

```
sudo apt install python3-tk
```

## Usage

```
python3 ssh_gui.py
```

Or use the included launch script:

```
./start.sh
```

## Project Structure

```
├── ssh_gui.py          # Main application
├── servers.json        # Server configurations (auto-generated)
├── history.json        # Command history (auto-generated)
├── start.sh            # Launch script
└── scripts/            # Per-server script directories
    ├── server-a/
    │   ├── git-pull.sh
    │   └── build.sh
    └── server-b/
        └── deploy.sh
```

## Server Configuration

Servers are managed through the GUI (Add/Edit/Delete buttons). Each server stores:

| Field | Description |
|-------|-------------|
| Name | Display name and scripts folder name |
| Host | Hostname or IP address |
| Port | SSH port (default: 22) |
| Username | SSH login user |
| Key Path | Path to SSH private key |
| Login Shell | Wrap commands in `bash -l` to load user profile |

## Quick Commands

Each server can have custom command buttons configured through the Edit dialog:

- **Label** — Button text
- **Script** — Shell script stored in `scripts/<server-name>/`
- **Color** — Custom button color for visual grouping
- **Project** — Optional project tag shown as a prefix
