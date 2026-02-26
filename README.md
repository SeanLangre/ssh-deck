 Plan to implement                                                                    │
│                                                                                      │
│ SSH GUI Manager - Implementation Plan                                                │
│                                                                                      │
│ Context                                                                              │
│                                                                                      │
│ Current ssh_gui.py has hardcoded servers and commands. Goal: make it a flexible SSH  │
│ management tool with dynamic server configs, custom commands, and full log history.  │
│                                                                                      │
│ Steps (implement one at a time per user request)                                     │
│                                                                                      │
│ Step 1: Server Config Persistence                                                    │
│                                                                                      │
│ - Add import json, os to ssh_gui.py                                                  │
│ - Define SERVERS_FILE = "servers.json" (same directory as script)                    │
│ - Add load_servers() — reads JSON file, returns list of server dicts. Returns [] if  │
│ file missing.                                                                        │
│ - Add save_servers(servers) — writes list to JSON file with indent                   │
│ - Server dict format: {"name": "...", "host": "...", "port": 22, "username": "...",  │
│ "key_path": "..."}                                                                   │
│ - Replace hardcoded server1()/server2() functions with a servers list loaded from    │
│ JSON                                                                                 │
│ - File: /path/to/Python-Tkinter-Paramiko/ssh_gui.py                    │
│                                                                                      │
│ Step 2: Add/Edit/Remove Server Dialogs                                               │
│                                                                                      │
│ - Toplevel popup with Entry fields for name, host, port, username, key_path          │
│ - Add button opens empty dialog, saves new server to list + JSON                     │
│ - Edit button opens dialog pre-filled with selected server's data                    │
│ - Delete button removes selected server with confirmation                            │
│                                                                                      │
│ Step 3: Server Selector Dropdown + Command Input                                     │
│                                                                                      │
│ - Replace hardcoded buttons with ttk.Combobox for server selection                   │
│ - Add tk.Entry for typing commands + Send button (Enter key binding)                 │
│ - Send dispatches the command to the selected server                                 │
│                                                                                      │
│ Step 4: Improved Output Panel                                                        │
│                                                                                      │
│ - Color-coded output using ScrolledText tags (green=output, red=error, blue=command) │
│ - Use app.after() for thread-safe UI updates instead of direct widget inserts        │
│                                                                                      │
│ Step 5: Better Command History                                                       │
│                                                                                      │
│ - Timestamped entries showing server name + command + truncated response             │
│ - Clear History button                                                               │
│ - Optionally save history to file                                                    │
│                                                                                      │
│ Verification                                                                         │
│                                                                                      │
│ - python ssh_gui.py opens the UI                                                     │
│ - Adding a server creates/updates servers.json                                       │
│ - Selecting server + typing command + Send shows output and logs to history          │
│ - Bad connections show errors without crashing   