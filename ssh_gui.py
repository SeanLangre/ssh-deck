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
HISTORY_FILE = os.path.join(SCRIPT_DIR, "history.json")


# ---------- Server Persistence ----------
def load_servers():
    if not os.path.exists(SERVERS_FILE):
        return []
    with open(SERVERS_FILE, "r") as f:
        return json.load(f)


def save_servers(servers):
    with open(SERVERS_FILE, "w") as f:
        json.dump(servers, f, indent=2)


# ---------- History Persistence ----------
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r") as f:
        return json.load(f)


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# ---------- SSH Function ----------
def run_ssh_command(app, result_text, history_text, server, command, history):
    host = server["host"]
    username = server["username"]
    key_path = server["key_path"]
    port = server.get("port", 22)
    name = server.get("name", host)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def update_ui(tag, text):
        app.after(0, lambda: _insert_tagged(result_text, text, tag))

    def update_history(entry_text):
        app.after(0, lambda: _append_history(history_text, entry_text))

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        update_ui("cmd", f"\n[{timestamp}] {name} >> {command}\n")

        client.connect(hostname=host, port=port, username=username, key_filename=key_path)
        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode()
        error = stderr.read().decode()
        client.close()

        if output:
            update_ui("output", output)
        if error:
            update_ui("error", f"STDERR:\n{error}")

        # Truncate response for history display
        preview = (output[:80] + "...") if len(output) > 80 else output
        preview = preview.replace("\n", " ").strip()
        entry_text = f"[{timestamp}] {name} >> {command}  |  {preview}\n"
        update_history(entry_text)

        # Persist to history file
        history.append({
            "timestamp": timestamp,
            "server": name,
            "command": command,
            "output_preview": preview,
        })
        save_history(history)

    except Exception as e:
        update_ui("error", f"\nError: {str(e)}\n")
        entry_text = f"[{timestamp}] {name} >> {command}  |  ERROR: {str(e)}\n"
        update_history(entry_text)


def _insert_tagged(widget, text, tag):
    widget.insert(tk.END, text, tag)
    widget.see(tk.END)


def _append_history(widget, text):
    widget.insert(tk.END, text)
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
            entry.grid(row=i, column=1, padx=5, pady=3)
            if server and key in server:
                entry.insert(0, str(server[key]))
            else:
                entry.insert(0, default)
            self.entries[key] = entry

        btn_frame = tk.Frame(self)
        btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=10)
        tk.Button(btn_frame, text="Save", command=self._save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=self.destroy, width=10).pack(side=tk.LEFT, padx=5)

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
        self.history = load_history()

        self._build_ui()
        self._refresh_server_combo()
        self._load_history_display()

    def _build_ui(self):
        # --- Top bar: server selector + management buttons ---
        top_frame = tk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(top_frame, text="Server:").pack(side=tk.LEFT, padx=(0, 5))
        self.server_combo = ttk.Combobox(top_frame, state="readonly", width=30)
        self.server_combo.pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(top_frame, text="Add", command=self._add_server, width=6).pack(side=tk.LEFT, padx=2)
        tk.Button(top_frame, text="Edit", command=self._edit_server, width=6).pack(side=tk.LEFT, padx=2)
        tk.Button(top_frame, text="Delete", command=self._delete_server, width=6).pack(side=tk.LEFT, padx=2)

        # --- Command input bar ---
        cmd_frame = tk.Frame(self.root)
        cmd_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        tk.Label(cmd_frame, text="Command:").pack(side=tk.LEFT, padx=(0, 5))
        self.cmd_entry = tk.Entry(cmd_frame)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.cmd_entry.bind("<Return>", lambda e: self._send_command())

        tk.Button(cmd_frame, text="Send", command=self._send_command, width=8).pack(side=tk.LEFT)

        # --- Main content: output (left) + history (right) ---
        content = tk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        content.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        # Output panel
        out_frame = tk.Frame(content)
        tk.Label(out_frame, text="Output", font=("Arial", 12)).pack(anchor="w")
        self.result_text = scrolledtext.ScrolledText(out_frame, height=20, width=60, bg="#1e1e1e", fg="#d4d4d4",
                                                     insertbackground="white", font=("Consolas", 10))
        self.result_text.pack(fill=tk.BOTH, expand=True)
        self.result_text.tag_config("output", foreground="#4ec9b0")
        self.result_text.tag_config("error", foreground="#f44747")
        self.result_text.tag_config("cmd", foreground="#569cd6")
        content.add(out_frame, stretch="always")

        # History panel
        hist_frame = tk.Frame(content)
        hist_top = tk.Frame(hist_frame)
        hist_top.pack(fill=tk.X)
        tk.Label(hist_top, text="Command History", font=("Arial", 12)).pack(side=tk.LEFT, anchor="w")
        tk.Button(hist_top, text="Clear", command=self._clear_history, width=6).pack(side=tk.RIGHT)

        self.history_text = scrolledtext.ScrolledText(hist_frame, height=20, width=35, font=("Consolas", 9))
        self.history_text.pack(fill=tk.BOTH, expand=True)
        content.add(hist_frame, stretch="never")

    # --- Server management ---
    def _refresh_server_combo(self):
        names = [s["name"] for s in self.servers]
        self.server_combo["values"] = names
        if names:
            self.server_combo.current(0)

    def _add_server(self):
        ServerDialog(self.root, title="Add Server", callback=self._on_server_added)

    def _on_server_added(self, data):
        self.servers.append(data)
        save_servers(self.servers)
        self._refresh_server_combo()
        self.server_combo.current(len(self.servers) - 1)

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
            args=(self.root, self.result_text, self.history_text, server, command, self.history),
        )
        thread.start()

    # --- History ---
    def _load_history_display(self):
        for entry in self.history:
            line = f"[{entry['timestamp']}] {entry['server']} >> {entry['command']}  |  {entry.get('output_preview', '')}\n"
            self.history_text.insert(tk.END, line)
        self.history_text.see(tk.END)

    def _clear_history(self):
        if messagebox.askyesno("Clear History", "Clear all command history?"):
            self.history.clear()
            save_history(self.history)
            self.history_text.delete("1.0", tk.END)


# ---------- Entry Point ----------
if __name__ == "__main__":
    root = tk.Tk()
    SSHManagerApp(root)
    root.mainloop()
