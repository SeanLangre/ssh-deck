#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog, colorchooser
import threading
import paramiko
import json
import os
from datetime import datetime
import time

# ---------- Config ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVERS_FILE = os.path.join(SCRIPT_DIR, "servers.json")
SCRIPTS_DIR = os.path.join(SCRIPT_DIR, "scripts")


# ---------- Server Persistence ----------
def load_servers():
    if not os.path.exists(SERVERS_FILE):
        return []
    with open(SERVERS_FILE, "r") as f:
        return json.load(f)


def save_servers(servers):
    with open(SERVERS_FILE, "w") as f:
        json.dump(servers, f, indent=2)


# ---------- Script Helpers ----------
def get_server_scripts_dir(server_name):
    return os.path.join(SCRIPTS_DIR, server_name)


def load_script_content(server_name, script_filename):
    path = os.path.join(get_server_scripts_dir(server_name), script_filename)
    with open(path, "r") as f:
        return f.read().strip()


def label_to_script_name(label):
    import re
    name = label.lower().strip()
    name = re.sub(r'[^a-z0-9]+', '-', name).strip('-')
    return name + ".sh"


# ---------- SSH Function ----------
def run_ssh_command(app, result_text, server, command):
    host = server["host"]
    username = server["username"]
    key_path = server["key_path"]
    port = server.get("port", 22)
    name = server.get("name", host)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start_time = datetime.now()

    def update_ui(tag, text):
        app.after(0, lambda: _insert_tagged(result_text, text, tag))

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


def _insert_tagged(widget, text, tag):
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

        self.cmd_listbox = tk.Listbox(cmd_list_frame, height=6, width=55)
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

        # Create the script file if it doesn't exist
        scripts_dir = get_server_scripts_dir(server_name)
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
        script_path = os.path.join(get_server_scripts_dir(server_name), cmd["script"])
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

        self.servers = load_servers()

        self._build_ui()
        self._refresh_server_combo()

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
                                command=lambda c=cmd["script"]: self._run_quick_command(c))
                if color:
                    btn.config(bg=color, activebackground=color)
                r, c = divmod(i, cols)
                btn.grid(row=r, column=c, padx=2, pady=2, sticky="nsew")
            for c in range(cols):
                grid.columnconfigure(c, weight=1)

    def _run_quick_command(self, script_filename):
        idx = self.server_combo.current()
        if idx < 0:
            return
        server = self.servers[idx]
        try:
            command = load_script_content(server["name"], script_filename)
        except FileNotFoundError:
            messagebox.showerror("Script Not Found",
                                 f"Script file not found:\nscripts/{server['name']}/{script_filename}")
            return
        output_widget = self._get_output_widget(server["name"])
        thread = threading.Thread(
            target=run_ssh_command,
            args=(self.root, output_widget, server, command),
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
            save_servers(self.servers)
            self._refresh_server_combo()

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
    root = tk.Tk()
    SSHManagerApp(root)
    root.mainloop()
