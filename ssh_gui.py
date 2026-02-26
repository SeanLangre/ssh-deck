#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import paramiko
import json
import os
from datetime import datetime

# ---------- Config ----------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVERS_FILE = os.path.join(SCRIPT_DIR, "servers.json")


# ---------- Server Persistence ----------
def load_servers():
    if not os.path.exists(SERVERS_FILE):
        return []
    with open(SERVERS_FILE, "r") as f:
        return json.load(f)


def save_servers(servers):
    with open(SERVERS_FILE, "w") as f:
        json.dump(servers, f, indent=2)


# ---------- SSH Function ----------
def run_ssh_command(app, result_text, server, command):
    host = server["host"]
    username = server["username"]
    key_path = server["key_path"]
    port = server.get("port", 22)
    name = server.get("name", host)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def update_ui(tag, text):
        app.after(0, lambda: _insert_tagged(result_text, text, tag))

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

        stdin, stdout, stderr = client.exec_command(exec_cmd)
        output = stdout.read().decode()
        error = stderr.read().decode()
        client.close()

        if output:
            update_ui("output", output)
        if error:
            update_ui("error", f"STDERR:\n{error}")

    except Exception as e:
        update_ui("error", f"\nError: {str(e)}\n")


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
        tk.Label(self, text="Quick Commands:", font=("Arial", 10, "bold")).grid(
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
                self.cmd_listbox.insert(tk.END, f"{cmd['label']}  |  {cmd['command']}")

        row += 1

        # Label + command entry fields
        entry_frame = tk.Frame(self)
        entry_frame.grid(row=row, column=0, columnspan=3, padx=5, pady=3, sticky="ew")

        tk.Label(entry_frame, text="Label:").pack(side=tk.LEFT)
        self.cmd_label_entry = tk.Entry(entry_frame, width=15)
        self.cmd_label_entry.pack(side=tk.LEFT, padx=(2, 5))

        tk.Label(entry_frame, text="Command:").pack(side=tk.LEFT)
        self.cmd_command_entry = tk.Entry(entry_frame, width=25)
        self.cmd_command_entry.pack(side=tk.LEFT, padx=(2, 5), fill=tk.X, expand=True)

        row += 1

        cmd_btn_frame = tk.Frame(self)
        cmd_btn_frame.grid(row=row, column=0, columnspan=3, padx=5, pady=3)

        tk.Button(cmd_btn_frame, text="Add Cmd", command=self._add_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Edit Cmd", command=self._edit_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Remove", command=self._remove_cmd, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Move Up", command=self._move_cmd_up, width=8).pack(side=tk.LEFT, padx=2)
        tk.Button(cmd_btn_frame, text="Move Down", command=self._move_cmd_down, width=8).pack(side=tk.LEFT, padx=2)

        row += 1

        # --- Save / Cancel ---
        btn_frame = tk.Frame(self)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=10)
        tk.Button(btn_frame, text="Save", command=self._save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=self.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def _add_cmd(self):
        label = self.cmd_label_entry.get().strip()
        command = self.cmd_command_entry.get().strip()
        if not label or not command:
            messagebox.showerror("Missing", "Both Label and Command are required.", parent=self)
            return

        if self._editing_idx is not None:
            # Save edit to existing command
            idx = self._editing_idx
            self.commands[idx] = {"label": label, "command": command}
            self._editing_idx = None
            self._refresh_cmd_listbox()
        else:
            self.commands.append({"label": label, "command": command})
            self.cmd_listbox.insert(tk.END, f"{label}  |  {command}")

        self.cmd_label_entry.delete(0, tk.END)
        self.cmd_command_entry.delete(0, tk.END)

    def _edit_cmd(self):
        sel = self.cmd_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        cmd = self.commands[idx]
        self._editing_idx = idx
        self.cmd_label_entry.delete(0, tk.END)
        self.cmd_label_entry.insert(0, cmd["label"])
        self.cmd_command_entry.delete(0, tk.END)
        self.cmd_command_entry.insert(0, cmd["command"])

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

    def _refresh_cmd_listbox(self):
        self.cmd_listbox.delete(0, tk.END)
        for cmd in self.commands:
            self.cmd_listbox.insert(tk.END, f"{cmd['label']}  |  {cmd['command']}")

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

    def _build_ui(self):
        # --- Top bar: server selector + management buttons ---
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text="Server:").pack(side=tk.LEFT, padx=(0, 5))
        self.server_combo = ttk.Combobox(top_frame, state="readonly", width=30)
        self.server_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.server_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_buttons())

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
        out_frame = tk.Frame(self.root)
        out_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        tk.Label(out_frame, text="Output", font=("Arial", 12)).pack(anchor="w")
        self.result_text = scrolledtext.ScrolledText(out_frame, height=20, bg="#1e1e1e", fg="#d4d4d4",
                                                     insertbackground="white", font=("Consolas", 10))
        self.result_text.pack(fill=tk.BOTH, expand=True)
        self.result_text.tag_config("output", foreground="#4ec9b0")
        self.result_text.tag_config("error", foreground="#f44747")
        self.result_text.tag_config("cmd", foreground="#569cd6")

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

        for cmd in commands:
            btn = tk.Button(self.btn_frame, text=cmd["label"],
                            command=lambda c=cmd["command"]: self._run_quick_command(c))
            btn.pack(side=tk.LEFT, padx=2, pady=2)

    def _run_quick_command(self, command):
        idx = self.server_combo.current()
        if idx < 0:
            return
        server = self.servers[idx]
        thread = threading.Thread(
            target=run_ssh_command,
            args=(self.root, self.result_text, server, command),
        )
        thread.start()

    # --- Server management ---
    def _refresh_server_combo(self):
        names = [s["name"] for s in self.servers]
        self.server_combo["values"] = names
        if names:
            self.server_combo.current(0)
        self._refresh_buttons()

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
        self.servers[idx] = data
        save_servers(self.servers)
        self._refresh_server_combo()
        self.server_combo.current(idx)
        self._refresh_buttons()

    def _delete_server(self):
        idx = self.server_combo.current()
        if idx < 0:
            messagebox.showinfo("No Server", "Select a server to delete.")
            return
        name = self.servers[idx]["name"]
        if messagebox.askyesno("Confirm Delete", f"Delete server '{name}'?"):
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
        thread = threading.Thread(
            target=run_ssh_command,
            args=(self.root, self.result_text, server, command),
        )
        thread.start()


# ---------- Entry Point ----------
if __name__ == "__main__":
    root = tk.Tk()
    SSHManagerApp(root)
    root.mainloop()
