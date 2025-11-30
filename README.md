# 🚀 Raw Request Sender

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-passing-brightgreen)

A powerful, modular Python utility for automating raw HTTP request replays. Designed for flexibility, it supports **proxy rotation**, **placeholder substitution**, and **parallel proxy checking**.

Everything is file-based (plain text), making it easy to version control and edit with any text editor.

---

## ✨ Features

- **📝 Raw Requests**: Just copy-paste raw HTTP from your browser dev tools (Fiddler/Burp style).
- **🔄 Smart Rotation**: Rotates placeholders (`{name}`) sequentially or randomly.
- **🛡️ Proxy Management**: Pins working proxies, automatically drops dead ones, and supports failover.
- **⚡ Parallel Checker**: Built-in tool to check thousands of proxies concurrently.
- **🔌 Plug & Play**: No complex databases. Just text files.
- **🔍 Response Dumping**: Inspect responses in the console or save them to files.

---

## 🚀 Quick Start

### 1. Install
```bash
python3 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 2. Create a Request
Create a file `requests/my_request.txt`:
```http
POST /api/login HTTP/1.1
Host: example.com
Content-Type: application/json

{"username": "admin", "token": "{my_token}"}
```

### 3. Add Placeholders
Create `placeholders/my_token.txt`:
```text
token_123
token_456
token_789
```

### 4. Run
```bash
python requester.py
```
*The script will loop through your requests every 30 seconds (configurable).*

---

## 📖 User Guide

### 📂 Request Files (`requests/*.txt`)
- **Format**: Standard HTTP format. Request line + Headers + Empty Line + Body.
- **URL Handling**: If the request line has a full URL (`GET https://...`), it's used. Otherwise, it combines `SCHEME` + `Host` header.
- **Ignored Files**: Files starting with `example` (e.g., `requests/example_login.txt`) are skipped.

### 🧩 Placeholders (`{name}`)
- **File-based**: Define values in `placeholders/name.txt` (one per line).
- **Dynamic (Built-in)**:
  - `{uuid}`: Generates a random UUID v4.
  - `{timestamp}`: Current UNIX timestamp (seconds).
  - `{random_int:min:max}`: Random integer between min and max (e.g., `{random_int:100:999}`).
- **Faker (Realistic Data)**:
  - *Requires `pip install Faker` (included in requirements).*
  - `{email}`, `{first_name}`, `{last_name}`, `{user_agent}`, `{country}`.
  - `{faker:method}`: Calls any Faker method, e.g., `{faker:ipv4}`, `{faker:phone_number}`, `{faker:address}`.
- **Rotation**: File-based values support **Sequential** (default) or **Random** rotation (see `config.py`).

### 🌐 Proxy System (`proxies.txt`)
- **Formats**: `ip:port`, `user:pass@ip:port`, `http://...`, `socks5://...`.
- **Logic**: 
  - The script **pins** a working proxy until it fails.
  - If a proxy fails (connection error or 400+ status), it is **removed** from `proxies.txt`.
  - If SSL fails, it retries once without verification.
- **Direct Mode**: Run with `--direct` to ignore proxies entirely.

### 🛠 CLI Commands

| Command | Description |
| :--- | :--- |
| `python requester.py` | Start the main loop (sends requests every 30s). |
| `python requester.py --direct` | Run without proxies. |
| `python requester.py --workers 20` | Set number of parallel threads (default: 10). |
| `python requester.py --check` | Run the **Parallel Proxy Checker** to prune dead proxies. |
| `python requester.py --response` | Print full responses to the console. |
| `python requester.py --response out.txt` | Save responses to `responses/out.txt`. |

---

## ⚙️ Configuration
Edit `config.py` to tweak the behavior:

```python
INTERVAL_SECONDS = 30       # Time between loops
PROXY_CHECK_WORKERS = 32    # Threads for proxy checking
VERIFY_TLS = True           # Set False to ignore SSL errors globally
PLACEHOLDER_ROTATION = "sequential" # or "random"
```

---

## 💻 Development

The project is structured as a modular package in `src/`.

### Project Structure
```text
├── config.py           # ⚙️ Settings
├── requester.py        # 🚀 Entry Point
├── proxies.txt         # 🛡️ Proxy List
├── requests/           # 📂 HTTP Templates
├── placeholders/       # 🧩 Data for injection
├── src/                # 🧠 Core Logic
│   ├── app.py          # Main Loop
│   ├── network.py      # HTTP Engine
│   └── ...
└── tests/              # 🧪 Unit Tests
```

### Running Tests
Ensure code quality with the included test suite:
```bash
pip install -r requirements.txt
export PYTHONPATH=.
pytest -v
```

---

## ❓ Troubleshooting

- **"Proxy list exhausted"**: All proxies failed. Run `python requester.py --check` to find working ones or add fresh proxies.
- **InsecureRequestWarning**: TLS verification was skipped. Update your proxies or set `VERIFY_TLS = False`.
- **No requests sent?**: Ensure your file inside `requests/` does *not* start with `example`.

---

*Happy Requesting!* 🕵️‍♂️
