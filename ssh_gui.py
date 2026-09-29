#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog, colorchooser, filedialog
import threading
import paramiko
import json
import os
import sys
from datetime import datetime
import time
import re

_ANSI_RE = re.compile(r'\x1b[\[\(][0-9;?]*[a-zA-Z]|\x1b[=>]|\x1b\][^\x07]*\x07|\r')
_BLANK_LINES_RE = re.compile(r'\n{3,}')

def strip_ansi(text):
    text = _ANSI_RE.sub('', text)
    text = _BLANK_LINES_RE.sub('\n\n', text)
    return text

# ---------- Config ----------
if getattr(sys, "frozen", False):
    # Packaged build (PyInstaller/AppImage): the bundle is read-only, so keep
    # config in the user's config dir.
    SCRIPT_DIR = os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
        "ssh-gui-manager")
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVERS_FILE = os.path.join(SCRIPT_DIR, "servers.json")
SCRIPTS_DIR = os.path.join(SCRIPT_DIR, "scripts")
ICON_CANDIDATES = [
    "/usr/share/icons/HighContrast/48x48/apps/utilities-terminal.png",
    "/usr/share/icons/hicolor/48x48/apps/utilities-terminal.png",
]


# ---------- Server Persistence ----------
# Config is stored split across the scripts folder:
#   scripts/<server>/server.json          -> connection fields (no commands)
#   scripts/<server>/<project>/commands.json -> that project's command list
# Commands without a project live under the "ALL" folder. The in-memory
# server dict keeps the old flat shape ({..., "commands": [...]}) so the rest
# of the app is unchanged; each command carries its "project" key.
SERVER_META_FIELDS = ("host", "port", "username", "key_path", "login_shell")
COMMANDS_FILENAME = "commands.json"
SERVER_META_FILENAME = "server.json"


def load_servers():
    # One-time migration from a legacy flat servers.json.
    if os.path.exists(SERVERS_FILE):
        with open(SERVERS_FILE, "r") as f:
            legacy = json.load(f)
        save_servers(legacy)
        os.rename(SERVERS_FILE, SERVERS_FILE + ".migrated")
        return legacy

    if not os.path.isdir(SCRIPTS_DIR):
        return []

    servers = []
    for name in sorted(os.listdir(SCRIPTS_DIR)):
        server_dir = os.path.join(SCRIPTS_DIR, name)
        meta_path = os.path.join(server_dir, SERVER_META_FILENAME)
        if not os.path.isfile(meta_path):
            continue
        with open(meta_path, "r") as f:
            server = json.load(f)
        server["name"] = name

        commands = []
        for project in sorted(os.listdir(server_dir)):
            cmds_path = os.path.join(server_dir, project, COMMANDS_FILENAME)
            if not os.path.isfile(cmds_path):
                continue
            with open(cmds_path, "r") as f:
                for cmd in json.load(f):
                    cmd["project"] = project
                    commands.append(cmd)
        server["commands"] = commands
        servers.append(server)
    return servers


def save_server(server):
    """Write one server's split config, replacing its stale command files."""
    server_dir = get_server_scripts_dir(server["name"])
    os.makedirs(server_dir, exist_ok=True)

    meta = {k: server[k] for k in SERVER_META_FIELDS if k in server}
    with open(os.path.join(server_dir, SERVER_META_FILENAME), "w") as f:
        json.dump(meta, f, indent=2)

    # Group commands by project folder (missing project -> "ALL").
    groups = {}
    for cmd in server.get("commands", []):
        project = cmd.get("project") or "ALL"
        stored = {k: v for k, v in cmd.items() if k != "project"}
        groups.setdefault(project, []).append(stored)

    # Remove commands.json files for projects that no longer have commands.
    for project in os.listdir(server_dir):
        proj_dir = os.path.join(server_dir, project)
        cmds_path = os.path.join(proj_dir, COMMANDS_FILENAME)
        if os.path.isfile(cmds_path) and project not in groups:
            os.remove(cmds_path)

    for project, cmds in groups.items():
        proj_dir = os.path.join(server_dir, project)
        os.makedirs(proj_dir, exist_ok=True)
        with open(os.path.join(proj_dir, COMMANDS_FILENAME), "w") as f:
            json.dump(cmds, f, indent=2)


def save_servers(servers):
    for server in servers:
        save_server(server)


# ---------- Config Export / Import ----------
# The export is one JSON blob: every server (with its commands) plus the text
# of every script under scripts/, so importing it rebuilds the same setup.
EXPORT_FORMAT = "ssh-gui-config"
EXPORT_VERSION = 1


def export_config(servers):
    scripts = {}
    if os.path.isdir(SCRIPTS_DIR):
        for dirpath, _, filenames in os.walk(SCRIPTS_DIR):
            for filename in sorted(filenames):
                # server.json / commands.json are rebuilt from "servers".
                if filename in (SERVER_META_FILENAME, COMMANDS_FILENAME):
                    continue
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, SCRIPTS_DIR).replace(os.sep, "/")
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        scripts[rel] = f.read()
                except (OSError, UnicodeDecodeError):
                    continue  # skip binary/unreadable files
    return json.dumps({
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "servers": servers,
        "scripts": scripts,
    }, indent=2)


def _safe_scripts_path(rel):
    """Resolve rel under SCRIPTS_DIR, refusing anything that escapes it."""
    root = os.path.realpath(SCRIPTS_DIR)
    path = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, path]) != root or path == root:
        raise ValueError(f"Unsafe path in config: {rel}")
    return path


def parse_config(text):
    """Validate an exported config; returns (servers, scripts) or raises ValueError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Not valid JSON: {e}")
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise ValueError("This is not an SSH GUI config export.")
    servers = data.get("servers", [])
    scripts = data.get("scripts", {})
    if not isinstance(servers, list) or not isinstance(scripts, dict):
        raise ValueError("Config is malformed.")
    for server in servers:
        if not isinstance(server, dict) or not server.get("name"):
            raise ValueError("Every server needs a name.")
        _safe_scripts_path(server["name"])
    for rel in scripts:
        _safe_scripts_path(rel)
    return servers, scripts


def write_imported_scripts(scripts):
    for rel, content in scripts.items():
        path = _safe_scripts_path(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# ---------- Script Helpers ----------
def get_server_scripts_dir(server_name):
    return os.path.join(SCRIPTS_DIR, server_name)


def get_script_dir(server_name, project):
    return os.path.join(SCRIPTS_DIR, server_name, project or "ALL")


# Shared scripts are written once with a $PROJECT placeholder. A project's
# command may name a script that only exists in a _shared folder; we fall back
# to it and inject PROJECT=<remote dir> at run time. Two _shared locations are
# supported: per-server (scripts/<server>/_shared) and global (scripts/_shared),
# so a script shared across every server lives in one place.
SHARED_DIR_NAME = "_shared"


def project_remote_dir(project):
    """Remote project directory name (/path/to/Projects/<dir>) for a project label."""
    return (project or "").lower()


def get_script_path(server_name, project, script_filename):
    """Resolve a script: project folder, then server _shared, then global _shared."""
    candidates = [
        os.path.join(get_script_dir(server_name, project), script_filename),
        os.path.join(SCRIPTS_DIR, server_name, SHARED_DIR_NAME, script_filename),
        os.path.join(SCRIPTS_DIR, SHARED_DIR_NAME, script_filename),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]  # non-existent; report the missing project-folder path


def load_script_content(server_name, project, script_filename):
    path = get_script_path(server_name, project, script_filename)
    with open(path, "r") as f:
        content = f.read().strip()
    # When served from _shared, bind $PROJECT to this project's remote dir.
    if os.path.basename(os.path.dirname(path)) == SHARED_DIR_NAME:
        content = f"PROJECT={project_remote_dir(project)}\n{content}"
    return content


# A script whose first line is "#@download" is not run remotely; instead it
# copies a remote file or folder to this machine over SFTP:
#   #@download
#   remote: C:\path\on\server\SomeFolder
#   local: ~/Downloads/some-folder
DOWNLOAD_MARKER = "#@download"


def parse_download_directive(content):
    """Return {"remote", "local"} if content is a download directive, else None."""
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    # Skip the PROJECT= line injected for _shared scripts.
    if lines and lines[0].startswith("PROJECT="):
        project = lines.pop(0).split("=", 1)[1]
    else:
        project = ""
    if not lines or lines[0] != DOWNLOAD_MARKER:
        return None
    fields = {}
    for ln in lines[1:]:
        if ln.startswith("#") or ":" not in ln:
            continue
        key, value = ln.split(":", 1)
        fields[key.strip().lower()] = value.strip().replace("$PROJECT", project)
    if "remote" not in fields or "local" not in fields:
        return None
    return fields


def to_sftp_path(path):
    """Windows paths (C:\\foo\\bar) become /C:/foo/bar for the OpenSSH SFTP server."""
    if re.match(r"^[A-Za-z]:[\\/]", path):
        return "/" + path.replace("\\", "/")
    return path


def label_to_script_name(label):
    import re
    name = label.lower().strip()
    name = re.sub(r'[^a-z0-9]+', '-', name).strip('-')
    return name + ".sh"


POPOUT_LINE_THRESHOLD = 20


# ---------- SSH Function ----------
def run_ssh_command(app, result_text, server, command):
    host = server["host"]
    username = server["username"]
    key_path = server["key_path"]
    port = server.get("port", 22)
    name = server.get("name", host)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_time = datetime.now()

    # Mutable state for auto-popout
    state = {"widget": result_text, "lines": 0, "popped": False, "win": None}
    popout_ready = threading.Event()

    def _save_widget_log(widget, parent):
        content = widget.get("1.0", "end-1c")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "server"
        timestamp_for_file = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=parent,
            title="Save Log As",
            defaultextension=".txt",
            initialfile=f"{safe_name}-log-{timestamp_for_file}.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            messagebox.showerror("Save Failed", f"Could not save log:\n{e}", parent=parent)
            return
        messagebox.showinfo("Log Saved", f"Saved log to:\n{path}", parent=parent)

    def _create_popout():
        """Create popout window on main thread."""
        win = tk.Toplevel(app)
        win.title(f"{name} — {command[:60]}")
        win.geometry("900x600")

        controls = tk.Frame(win)
        controls.pack(fill=tk.X, padx=6, pady=(6, 0))
        tk.Button(
            controls,
            text="Save Log...",
            command=lambda: _save_widget_log(w, win),
            width=10,
        ).pack(side=tk.RIGHT)

        w = scrolledtext.ScrolledText(win, height=20, bg="#1e1e1e", fg="#d4d4d4",
                                      insertbackground="white", font=("Consolas", 10))
        w.tag_config("output", foreground="#4ec9b0")
        w.tag_config("error", foreground="#f44747")
        w.tag_config("cmd", foreground="#569cd6")
        w.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        # Copy existing content from main widget
        content = result_text.get("1.0", tk.END)
        if content.strip():
            w.insert(tk.END, content, "output")
            w.see(tk.END)
        state["widget"] = w
        state["win"] = win
        # Leave a note in the main output
        _insert_tagged(result_text, f"↗ Output moved to new window (>{POPOUT_LINE_THRESHOLD} lines)\n", "cmd")
        popout_ready.set()

    def update_ui(tag, text):
        text = strip_ansi(text)
        if not text:
            return
        # Count non-empty output lines
        if tag == "output":
            state["lines"] += sum(1 for ln in text.splitlines() if ln.strip())
            if not state["popped"] and state["lines"] > POPOUT_LINE_THRESHOLD:
                state["popped"] = True
                popout_ready.clear()
                app.after(0, _create_popout)
                popout_ready.wait(timeout=5)
        app.after(0, lambda: _insert_tagged(state["widget"], text, tag))

    def elapsed():
        delta = datetime.now() - start_time
        total_secs = int(delta.total_seconds())
        mins, secs = divmod(total_secs, 60)
        return f"{mins}m{secs:02d}s" if mins else f"{secs}s"

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        update_ui("cmd", f"\n[{timestamp}] {name} >> {command}\n")

        client.connect(hostname=host, port=port, username=username, key_filename=key_path)

        # Wrap in login shell to load user profile (PATH, etc.)
        if server.get("login_shell", True):
            escaped = command.replace("'", "'\\''")
            exec_cmd = f"bash -l -c '{escaped}'"
        else:
            exec_cmd = command

        stdin, stdout, stderr = client.exec_command(exec_cmd, get_pty=True)
        channel = stdout.channel

        # Stream stdout line-by-line in real time
        buf = ""
        while not channel.exit_status_ready() or channel.recv_ready():
            if channel.recv_ready():
                chunk = channel.recv(4096).decode(errors="replace")
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    update_ui("output", line + "\n")
            else:
                time.sleep(0.1)

        # Flush any remaining data
        while channel.recv_ready():
            chunk = channel.recv(4096).decode(errors="replace")
            buf += chunk
        if buf:
            update_ui("output", buf + "\n")

        # Read any remaining stderr (when not using pty, stderr may have content)
        error = stderr.read().decode(errors="replace")
        if error:
            update_ui("error", f"STDERR:\n{error}")

        # Report exit code and elapsed time
        exit_code = channel.recv_exit_status()
        if exit_code == 0:
            update_ui("output", f"\n✓ Completed successfully ({elapsed()})\n")
        else:
            update_ui("error", f"\n✗ FAILED — exit code {exit_code} ({elapsed()})\n")

        client.close()

    except Exception as e:
        update_ui("error", f"\nError: {str(e)} ({elapsed()})\n")


def run_sftp_download(app, result_text, server, remote, local):
    """Copy a remote file or folder (recursively) to a local path over SFTP."""
    import stat
    name = server.get("name", server["host"])
    local = os.path.expanduser(local)
    start_time = datetime.now()

    def log(tag, text):
        app.after(0, lambda: (_insert_tagged(result_text, text, tag), result_text.see(tk.END)))

    def elapsed():
        mins, secs = divmod(int((datetime.now() - start_time).total_seconds()), 60)
        return f"{mins}m{secs:02d}s" if mins else f"{secs}s"

    counts = {"files": 0, "bytes": 0}

    def fetch(sftp, rpath, lpath):
        attr = sftp.stat(rpath)
        if stat.S_ISDIR(attr.st_mode):
            os.makedirs(lpath, exist_ok=True)
            for entry in sftp.listdir_attr(rpath):
                fetch(sftp, f"{rpath}/{entry.filename}", os.path.join(lpath, entry.filename))
        else:
            sftp.get(rpath, lpath)
            counts["files"] += 1
            counts["bytes"] += attr.st_size
            log("output", f"  {os.path.relpath(lpath, local) if lpath != local else os.path.basename(lpath)}"
                          f" ({attr.st_size / 1024:.0f} KB)\n")

    timestamp = start_time.strftime("%Y-%m-%d %H:%M:%S")
    log("cmd", f"\n[{timestamp}] {name} download\n  {remote}\n  -> {local}\n")
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=server["host"], port=server.get("port", 22),
                       username=server["username"], key_filename=server["key_path"])
        sftp = client.open_sftp()
        fetch(sftp, to_sftp_path(remote), local)
        sftp.close()
        client.close()
        log("output", f"\n✓ Downloaded {counts['files']} files "
                      f"({counts['bytes'] / 1024 / 1024:.1f} MB) to {local} ({elapsed()})\n")
    except Exception as e:
        log("error", f"\nError: {e} ({elapsed()})\n")


def _insert_tagged(widget, text, tag):
    # Suppress consecutive blank lines
    if text.strip() == '':
        try:
            last = widget.get("end-3l linestart", "end-1c")
            if last.strip() == '':
                return
        except Exception:
            pass
    widget.insert(tk.END, text, tag)
    widget.see(tk.END)


# ---------- Server Dialog ----------
class ServerDialog(tk.Toplevel):
    def __init__(self, parent, title="Server", server=None, callback=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.callback = callback

        fields = [
            ("Name", "name", ""),
            ("Host", "host", ""),
            ("Port", "port", "22"),
            ("Username", "username", ""),
            ("Key Path", "key_path", ""),
        ]

        self.entries = {}
        for i, (label, key, default) in enumerate(fields):
            tk.Label(self, text=label + ":").grid(row=i, column=0, padx=5, pady=3, sticky="e")
            entry = tk.Entry(self, width=40)
            entry.grid(row=i, column=1, columnspan=2, padx=5, pady=3)
            if server and key in server:
                entry.insert(0, str(server[key]))
            else:
                entry.insert(0, default)
            self.entries[key] = entry

        row = len(fields)

        # --- Login shell checkbox ---
        self.login_shell_var = tk.BooleanVar(value=server.get("login_shell", True) if server else True)
        tk.Checkbutton(self, text="Login shell (load profile — uncheck for Windows)",
                       variable=self.login_shell_var).grid(row=row, column=0, columnspan=3, padx=5, pady=3, sticky="w")
        row += 1

        # --- Commands section ---
        tk.Label(self, text="Quick Commands (scripts):", font=("Arial", 10, "bold")).grid(
            row=row, column=0, columnspan=3, padx=5, pady=(10, 3), sticky="w")
        row += 1

        cmd_list_frame = tk.Frame(self)
        cmd_list_frame.grid(row=row, column=0, columnspan=3, padx=5, pady=3, sticky="ew")

        self.cmd_listbox = tk.Listbox(cmd_list_frame, height=12, width=55)
        self.cmd_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cmd_scroll = tk.Scrollbar(cmd_list_frame, command=self.cmd_listbox.yview)
        cmd_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.cmd_listbox.config(yscrollcommand=cmd_scroll.set)
        self.cmd_listbox.bind("<Double-1>", lambda e: self._edit_cmd())
        self._editing_idx = None

        self.commands = []
        if server and "commands" in server:
            for cmd in server["commands"]:
                self.commands.append(cmd)
                self._insert_cmd_listbox_item(cmd)

        row += 1

        cmd_btn_frame = tk.Frame(self)
        cmd_btn_frame.grid(row=row, column=0, columnspan=3, padx=5, pady=3)

        tk.Button(cmd_btn_frame, text="Add Cmd", command=self._add_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Edit Cmd", command=self._edit_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Rename", command=self._rename_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Color", command=self._recolor_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Project", command=self._set_cmd_project, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Remove", command=self._remove_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Move Up", command=self._move_cmd_up, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Move Down", command=self._move_cmd_down, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Open Folder", command=self._open_scripts_folder, width=10).pack(side=tk.LEFT, padx=2)

        row += 1

        # --- Save / Cancel ---
        btn_frame = tk.Frame(self)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=10)
        tk.Button(btn_frame, text="Save", command=self._save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=self.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _add_cmd(self):
        server_name = self.entries["name"].get().strip()
        if not server_name:
            messagebox.showinfo("No Name", "Enter a server name first.", parent=self)
            return

        label = simpledialog.askstring("Add Command", "Label:", parent=self)
        if not label or not label.strip():
            return
        label = label.strip()

        project = simpledialog.askstring("Add Command", "Project:", parent=self)
        if project is None:
            return
        project = project.strip()

        color = colorchooser.askcolor(title="Button Color", parent=self)
        if not color[1]:
            return
        hex_color = color[1]

        script = label_to_script_name(label)

        # Create the script file if it doesn't exist (under the project subfolder)
        scripts_dir = get_script_dir(server_name, project)
        os.makedirs(scripts_dir, exist_ok=True)
        script_path = os.path.join(scripts_dir, script)
        if not os.path.exists(script_path):
            with open(script_path, "w") as f:
                f.write("# Add your commands here\n")

        cmd_data = {"label": label, "script": script, "color": hex_color}
        if project:
            cmd_data["project"] = project
        self.commands.append(cmd_data)
        self._refresh_cmd_listbox()

    def _edit_cmd(self):
        sel = self.cmd_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        cmd = self.commands[idx]
        server_name = self.entries["name"].get().strip()
        if not server_name:
            messagebox.showinfo("No Name", "Enter a server name first.", parent=self)
            return
        script_path = get_script_path(server_name, cmd.get("project"), cmd["script"])
        if not os.path.exists(script_path):
            os.makedirs(os.path.dirname(script_path), exist_ok=True)
            with open(script_path, "w") as f:
                f.write("# Add your commands here\n")
        import subprocess
        subprocess.Popen(["xdg-open", script_path])

    def _rename_cmd(self):
        sel = self.cmd_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        cmd = self.commands[idx]
        new_label = tk.simpledialog.askstring("Rename Command", "New label:", initialvalue=cmd["label"], parent=self)
        if new_label and new_label.strip():
            cmd["label"] = new_label.strip()
            self._refresh_cmd_listbox()
            self.cmd_listbox.selection_set(idx)

    def _recolor_cmd(self):
        sel = self.cmd_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        cmd = self.commands[idx]
        current = cmd.get("color")
        color = colorchooser.askcolor(title="Button Color", initialcolor=current, parent=self)
        if color[1]:
            cmd["color"] = color[1]
            self._refresh_cmd_listbox()
            self.cmd_listbox.selection_set(idx)

    def _set_cmd_project(self):
        sel = self.cmd_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        cmd = self.commands[idx]
        current = cmd.get("project", "")
        project = simpledialog.askstring("Set Project", "Project:", initialvalue=current, parent=self)
        if project is None:
            return
        project = project.strip()
        if project:
            cmd["project"] = project
        else:
            cmd.pop("project", None)
        self._refresh_cmd_listbox()
        self.cmd_listbox.selection_set(idx)

    def _open_scripts_folder(self):
        server_name = self.entries["name"].get().strip()
        if not server_name:
            messagebox.showinfo("No Name", "Enter a server name first.", parent=self)
            return
        scripts_dir = get_server_scripts_dir(server_name)
        os.makedirs(scripts_dir, exist_ok=True)
        import subprocess
        subprocess.Popen(["xdg-open", scripts_dir])

    def _remove_cmd(self):
        sel = self.cmd_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.commands.pop(idx)
        self.cmd_listbox.delete(idx)

    def _move_cmd_up(self):
        sel = self.cmd_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        idx = sel[0]
        self.commands[idx - 1], self.commands[idx] = self.commands[idx], self.commands[idx - 1]
        self._refresh_cmd_listbox()
        self.cmd_listbox.selection_set(idx - 1)

    def _move_cmd_down(self):
        sel = self.cmd_listbox.curselection()
        if not sel or sel[0] >= len(self.commands) - 1:
            return
        idx = sel[0]
        self.commands[idx], self.commands[idx + 1] = self.commands[idx + 1], self.commands[idx]
        self._refresh_cmd_listbox()
        self.cmd_listbox.selection_set(idx + 1)

    def _insert_cmd_listbox_item(self, cmd):
        project = cmd.get("project", "")
        color = cmd.get("color", "")
        prefix = f"[{project}] " if project else ""
        self.cmd_listbox.insert(tk.END, f"{prefix}{cmd['label']}  |  {cmd['script']}  {color}")
        if color:
            self.cmd_listbox.itemconfig(self.cmd_listbox.size() - 1, fg=color)

    def _refresh_cmd_listbox(self):
        self.cmd_listbox.delete(0, tk.END)
        for cmd in self.commands:
            self._insert_cmd_listbox_item(cmd)

    def _save(self):
        data = {}
        for key, entry in self.entries.items():
            val = entry.get().strip()
            if key == "port":
                try:
                    val = int(val)
                except ValueError:
                    messagebox.showerror("Invalid Port", "Port must be a number.", parent=self)
                    return
            data[key] = val

        if not data["name"] or not data["host"] or not data["username"] or not data["key_path"]:
            messagebox.showerror("Missing Fields", "Name, Host, Username, and Key Path are required.", parent=self)
            return

        data["login_shell"] = self.login_shell_var.get()
        data["commands"] = self.commands

        # Ensure scripts directory exists for this server
        if data["name"]:
            os.makedirs(get_server_scripts_dir(data["name"]), exist_ok=True)

        if self.callback:
            self.callback(data)
        self.destroy()


# ---------- Main App ----------
class SSHManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SSH GUI Manager")
        self.root.geometry("950x500")
        self._app_icon = None
        self._set_window_icon()

        self.servers = load_servers()

        self._build_ui()
        self._refresh_server_combo()

    def _set_window_icon(self):
        for path in ICON_CANDIDATES:
            if not os.path.exists(path):
                continue
            try:
                self._app_icon = tk.PhotoImage(file=path)
                self.root.iconphoto(True, self._app_icon)
                return
            except tk.TclError:
                continue

    def _create_output_widget(self, parent):
        """Create a new ScrolledText output widget with standard tags."""
        widget = scrolledtext.ScrolledText(parent, height=20, bg="#1e1e1e", fg="#d4d4d4",
                                           insertbackground="white", font=("Consolas", 10))
        widget.tag_config("output", foreground="#4ec9b0")
        widget.tag_config("error", foreground="#f44747")
        widget.tag_config("cmd", foreground="#569cd6")
        return widget

    def _get_output_widget(self, server_name):
        """Get or create the output widget for a given server."""
        if server_name not in self.server_outputs:
            widget = self._create_output_widget(self.out_frame)
            self.server_outputs[server_name] = widget
        return self.server_outputs[server_name]

    def _switch_output(self):
        """Show the output widget for the currently selected server."""
        idx = self.server_combo.current()
        if idx < 0:
            return

        server_name = self.servers[idx]["name"]
        target_widget = self._get_output_widget(server_name)

        # Hide current, show target
        if self.current_output is not target_widget:
            if self.current_output is not None:
                self.current_output.pack_forget()
            target_widget.pack(fill=tk.BOTH, expand=True)
            self.current_output = target_widget
            self.result_text = target_widget

    def _build_ui(self):
        # --- Top bar: server selector + management buttons ---
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text="Server:").pack(side=tk.LEFT, padx=(0, 5))
        self.server_combo = ttk.Combobox(top_frame, state="readonly", width=30)
        self.server_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.server_combo.bind("<<ComboboxSelected>>", lambda e: self._on_server_selected())

        tk.Button(top_frame, text="Add", command=self._add_server, width=6).pack(side=tk.LEFT, padx=2)
        tk.Button(top_frame, text="Edit", command=self._edit_server, width=6).pack(side=tk.LEFT, padx=2)
        tk.Button(top_frame, text="Delete", command=self._delete_server, width=6).pack(side=tk.LEFT, padx=2)
        tk.Button(top_frame, text="SSH", command=self._open_ssh_terminal, width=6,
                  bg="#1d1d1d", fg="#00ff00", activebackground="#1d1d1d").pack(side=tk.LEFT, padx=(10, 2))
        tk.Button(top_frame, text="Import", command=self._import_config, width=7).pack(side=tk.RIGHT, padx=2)
        tk.Button(top_frame, text="Copy Config", command=self._copy_config, width=10).pack(side=tk.RIGHT, padx=2)

        # --- Quick command buttons (dynamic per server) ---
        self.btn_frame = tk.Frame(self.root)
        self.btn_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        # --- Command input bar ---
        cmd_frame = tk.Frame(self.root)
        cmd_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        tk.Label(cmd_frame, text="Command:").pack(side=tk.LEFT, padx=(0, 5))
        self.cmd_entry = tk.Entry(cmd_frame)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.cmd_entry.bind("<Return>", lambda e: self._send_command())

        tk.Button(cmd_frame, text="Send", command=self._send_command, width=8).pack(side=tk.LEFT)

        # --- Output panel ---
        self.out_frame = tk.Frame(self.root)
        self.out_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        tk.Label(self.out_frame, text="Output", font=("Arial", 12)).pack(anchor="w")

        # Per-server output widgets
        self.server_outputs = {}  # server_name -> ScrolledText
        self.current_output = None
        self.result_text = None

    def _on_server_selected(self):
        self._refresh_buttons()
        self._switch_output()

    def _open_ssh_terminal(self):
        idx = self.server_combo.current()
        if idx < 0:
            messagebox.showinfo("No Server", "Select a server first.")
            return
        s = self.servers[idx]
        ssh_cmd = ["ssh"]
        if s.get("key_path"):
            ssh_cmd += ["-i", s["key_path"]]
        if s.get("port") and int(s["port"]) != 22:
            ssh_cmd += ["-p", str(s["port"])]
        ssh_cmd.append(f"{s['username']}@{s['host']}")
        import subprocess, shutil
        if not shutil.which("x-terminal-emulator"):
            messagebox.showerror("Terminal Not Found", "x-terminal-emulator is not installed or not in PATH.")
            return
        subprocess.Popen(["x-terminal-emulator", "-t", f"ssh {s['name']}", "-e", *ssh_cmd])

    # --- Quick command buttons ---
    def _refresh_buttons(self):
        for widget in self.btn_frame.winfo_children():
            widget.destroy()

        idx = self.server_combo.current()
        if idx < 0:
            return

        server = self.servers[idx]
        commands = server.get("commands", [])

        if not commands:
            tk.Label(self.btn_frame, text="No quick commands configured. Edit the server to add some.",
                     fg="gray").pack(anchor="w")
            return

        # Group commands by project
        from collections import OrderedDict
        groups = OrderedDict()
        for cmd in commands:
            project = cmd.get("project", "")
            groups.setdefault(project, []).append(cmd)

        cols = 5
        for project, cmds in groups.items():
            if project:
                tk.Label(self.btn_frame, text=project, font=("Arial", 9, "bold")).pack(anchor="w", padx=2, pady=(4, 0))
            grid = tk.Frame(self.btn_frame)
            grid.pack(fill=tk.X, padx=2, pady=(0, 2))
            for i, cmd in enumerate(cmds):
                color = cmd.get("color")
                btn = tk.Button(grid, text=cmd["label"],
                                command=lambda s=cmd["script"], p=cmd.get("project"): self._run_quick_command(s, p))
                if color:
                    btn.config(bg=color, activebackground=color)
                r, c = divmod(i, cols)
                btn.grid(row=r, column=c, padx=2, pady=2, sticky="nsew")
            for c in range(cols):
                grid.columnconfigure(c, weight=1)

    def _run_quick_command(self, script_filename, project=None):
        idx = self.server_combo.current()
        if idx < 0:
            return
        server = self.servers[idx]
        try:
            command = load_script_content(server["name"], project, script_filename)
        except FileNotFoundError:
            rel = os.path.join(server["name"], project or "ALL", script_filename)
            messagebox.showerror("Script Not Found",
                                 f"Script file not found:\nscripts/{rel}")
            return
        # Open a separate window for each script run
        win = tk.Toplevel(self.root)
        win.title(f"{server['name']} — {script_filename}")
        win.geometry("900x600")
        output_widget = self._create_output_widget(win)
        output_widget.pack(fill=tk.BOTH, expand=True)
        download = parse_download_directive(command)
        if download:
            target, args = run_sftp_download, (download["remote"], download["local"])
        else:
            target, args = run_ssh_command, (command,)
        thread = threading.Thread(
            target=target,
            args=(self.root, output_widget, server, *args),
        )
        thread.start()

    # --- Server management ---
    def _refresh_server_combo(self):
        names = [s["name"] for s in self.servers]
        self.server_combo["values"] = names
        if names:
            self.server_combo.current(0)
        self._refresh_buttons()
        self._switch_output()

    def _add_server(self):
        ServerDialog(self.root, title="Add Server", callback=self._on_server_added)

    def _on_server_added(self, data):
        self.servers.append(data)
        save_servers(self.servers)
        self._refresh_server_combo()
        self.server_combo.current(len(self.servers) - 1)
        self._refresh_buttons()

    def _edit_server(self):
        idx = self.server_combo.current()
        if idx < 0:
            messagebox.showinfo("No Server", "Select a server to edit.")
            return
        server = self.servers[idx]
        ServerDialog(self.root, title="Edit Server", server=server,
                     callback=lambda data: self._on_server_edited(idx, data))

    def _on_server_edited(self, idx, data):
        old_name = self.servers[idx]["name"]
        new_name = data["name"]
        # Migrate output widget if server was renamed
        if old_name != new_name and old_name in self.server_outputs:
            self.server_outputs[new_name] = self.server_outputs.pop(old_name)
        # Rename scripts folder if server was renamed
        if old_name != new_name:
            old_dir = get_server_scripts_dir(old_name)
            new_dir = get_server_scripts_dir(new_name)
            if os.path.isdir(old_dir):
                os.rename(old_dir, new_dir)
        self.servers[idx] = data
        save_servers(self.servers)
        self._refresh_server_combo()
        self.server_combo.current(idx)
        self._refresh_buttons()
        self._switch_output()

    def _delete_server(self):
        idx = self.server_combo.current()
        if idx < 0:
            messagebox.showinfo("No Server", "Select a server to delete.")
            return
        name = self.servers[idx]["name"]
        if messagebox.askyesno("Confirm Delete", f"Delete server '{name}'?"):
            # Clean up the output widget for this server
            if name in self.server_outputs:
                widget = self.server_outputs.pop(name)
                if self.current_output is widget:
                    widget.pack_forget()
                    self.current_output = None
                    self.result_text = None
                widget.destroy()
            self.servers.pop(idx)
            # Config now lives in the server's folder, so remove it entirely.
            server_dir = get_server_scripts_dir(name)
            if os.path.isdir(server_dir):
                import shutil
                shutil.rmtree(server_dir)
            save_servers(self.servers)
            self._refresh_server_combo()

    # --- Config export / import ---
    def _copy_config(self):
        text = export_config(self.servers)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("Config Copied",
                            f"Copied config for {len(self.servers)} server(s) to the clipboard.")

    def _import_config(self):
        win = tk.Toplevel(self.root)
        win.title("Import Config")
        win.geometry("700x500")
        win.grab_set()

        tk.Label(win, text="Paste an exported config below (or load it from a file):").pack(
            anchor="w", padx=6, pady=(6, 0))
        box = scrolledtext.ScrolledText(win, font=("Consolas", 10))
        box.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Pre-fill from the clipboard when it already holds an export.
        try:
            clip = self.root.clipboard_get()
            parse_config(clip)
            box.insert("1.0", clip)
        except (tk.TclError, ValueError):
            pass

        def load_file():
            path = filedialog.askopenfilename(
                parent=win, title="Open Config",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
            if not path:
                return
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError as e:
                messagebox.showerror("Open Failed", str(e), parent=win)
                return
            box.delete("1.0", tk.END)
            box.insert("1.0", content)

        def do_import():
            try:
                servers, scripts = parse_config(box.get("1.0", "end-1c"))
            except ValueError as e:
                messagebox.showerror("Invalid Config", str(e), parent=win)
                return
            existing = {s["name"] for s in self.servers}
            replaced = [s["name"] for s in servers if s["name"] in existing]
            msg = f"Import {len(servers)} server(s) and {len(scripts)} script file(s)?"
            if replaced:
                msg += "\n\nThese servers will be overwritten:\n  " + "\n  ".join(replaced)
            if scripts:
                msg += "\n\nScript files with the same path will be overwritten."
            if not messagebox.askyesno("Confirm Import", msg, parent=win):
                return
            try:
                write_imported_scripts(scripts)
                for server in servers:
                    idx = next((i for i, s in enumerate(self.servers)
                                if s["name"] == server["name"]), None)
                    if idx is None:
                        self.servers.append(server)
                    else:
                        self.servers[idx] = server
                save_servers(self.servers)
            except (OSError, ValueError) as e:
                messagebox.showerror("Import Failed", str(e), parent=win)
                return
            self._refresh_server_combo()
            win.destroy()
            messagebox.showinfo("Import Complete", f"Imported {len(servers)} server(s).")

        btns = tk.Frame(win)
        btns.pack(pady=(0, 6))
        tk.Button(btns, text="Load File...", command=load_file, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btns, text="Import", command=do_import, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btns, text="Cancel", command=win.destroy, width=10).pack(side=tk.LEFT, padx=5)

    # --- Command execution ---
    def _send_command(self):
        idx = self.server_combo.current()
        if idx < 0:
            messagebox.showinfo("No Server", "Add and select a server first.")
            return
        command = self.cmd_entry.get().strip()
        if not command:
            return
        server = self.servers[idx]
        self.cmd_entry.delete(0, tk.END)
        output_widget = self._get_output_widget(server["name"])
        thread = threading.Thread(
            target=run_ssh_command,
            args=(self.root, output_widget, server, command),
        )
        thread.start()


# ---------- Entry Point ----------
if __name__ == "__main__":
    root = tk.Tk(className="SshGuiManager")
    SSHManagerApp(root)
    root.mainloop()
