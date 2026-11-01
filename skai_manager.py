#!/usr/bin/env python3

# Copyright (C) 2026 Enrico Miot
# Licensed under GNU GPL v3 or later — see /usr/share/common-licenses/GPL-3
# SPDX-License-Identifier: GPL-3.0-or-later

"""SKAI Manager (Alt+F9): manages the local AI of Skorpion OS after the
SKAI setup wizard is done (while ~/.launcher/wizard.py still exists it
does nothing).

  Models     download Ollama models (the wizard's Qwen 2.5 family, or any
             model found by name in the Ollama library) and remove them.
  On / Off   stop or start the local Ollama service.
  Remote PC  use the Ollama of another computer instead of the local one
             (skai_remote.py forwards 127.0.0.1:11434 to it).

Every Skorpion app uses the BIGGEST installed model, so one model at a
time is enough: the app warns about that before every download."""

import os
import re
import sys
import json
import html
import time
import shutil
import socket
import pathlib
import ipaddress
import subprocess
from urllib.parse import quote_plus

import requests

from PySide6 import QtWidgets, QtGui, QtCore, QtNetwork
from PySide6.QtCore import Qt, Signal

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # skcommon.py
import skcommon  # noqa: E402
from skcommon import PALETTES, FONT  # noqa: E402

# Wayland app_id: ~/.config/hypr/hyprland.lua matches it to float and center the window
APP_ID = "skorpion-skai-manager"
WINDOW_TITLE = "SKAI Manager"
INSTANCE_NAME = f"skorpion-skai-manager-{os.getuid()}"

WIZARD = HERE / "wizard.py"            # deletes itself when the SKAI setup is over
REMOTE_SCRIPT = HERE / "skai_remote.py"

OLLAMA_PORT = 11434
LOCAL_URL = f"http://127.0.0.1:{OLLAMA_PORT}"   # where every Skorpion app looks
OLLAMA_UNIT = "ollama.service"
REMOTE_UNIT = "skai-remote.service"
CONFIG_HOME = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
REMOTE_FILE = CONFIG_HOME / "skai" / "remote.json"
USER_UNIT_FILE = CONFIG_HOME / "systemd" / "user" / REMOTE_UNIT
MODELS_DIR = "/usr/share/ollama/.ollama/models"  # set by the wizard's systemd override

LIBRARY_URL = "https://ollama.com"
REGISTRY_URL = "https://registry.ollama.ai/v2"

# Same family and texts as the SKAI setup wizard:
# (ollama_tag, display_name, size, badge, description, hw_requirements)
MODELS = [
    ("qwen2.5:3b", "Qwen 2.5  ·  3B", "~2 GB", "LIGHT",
     "Compact 3-billion-parameter model. Great for machines with limited resources: "
     "responds quickly even without a dedicated GPU. Suited to summaries, simple Q&A "
     "and basic text assistance. Lower quality on complex reasoning.",
     "RAM: 6 GB+  |  GPU: optional  |  Modern CPU sufficient"),
    ("qwen2.5:7b", "Qwen 2.5  ·  7B", "~4.7 GB", "BALANCED",
     "The balance point of the family: good quality on coding, text analysis and "
     "multi-turn dialogue without professional hardware. A good choice for recent laptops.",
     "RAM: 10 GB+  |  GPU: 6 GB VRAM (optional, improves speed)"),
    ("qwen2.5:14b", "Qwen 2.5  ·  14B", "~9 GB", "RECOMMENDED",
     "SKAI's default model: solid reasoning, reliable code and advanced contextual "
     "understanding, while still running on mid-range desktops.",
     "RAM: 16 GB+  |  GPU: 8 GB VRAM recommended  |  CPU: 6+ cores"),
    ("qwen2.5:32b", "Qwen 2.5  ·  32B", "~20 GB", "ADVANCED",
     "Close to large cloud models: multi-step reasoning, math, complex code and long "
     "documents. Needs dedicated hardware, not suited to laptops or entry-level PCs.",
     "RAM: 32 GB+  |  GPU: 16-24 GB VRAM  |  CPU: 8+ cores"),
    ("qwen2.5:72b", "Qwen 2.5  ·  72B", "~46 GB", "MAXIMUM QUALITY",
     "The most powerful Qwen 2.5 model that runs locally, comparable to premium cloud "
     "services. Needs a dedicated workstation or server; slower answers.",
     "RAM: 64 GB+  |  GPU: 40-48 GB VRAM  |  Server/WS"),
]
BADGE_IDS = ["badge_light", "badge_balanced", "badge_default", "badge_advanced", "badge_max"]

ONE_MODEL_NOTE = (
    "The whole Skorpion suite always uses the <b>biggest</b> installed model. "
    "Keeping more than one model only takes disk space, and a smaller model "
    "downloaded next to a bigger one is never used. To move to a smaller model, "
    "remove the bigger one."
)


# ──────────────────────────────────────────────────────────────────────────
# HELPERS (no Qt: they run in worker threads)
# ──────────────────────────────────────────────────────────────────────────

class Cancelled(Exception):
    pass


def human_size(n) -> str:
    if n is None:
        return "unknown size"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{n:.1f} {unit}"
        n /= 1024


def full_name(name: str) -> str:
    """'qwen2.5' -> 'qwen2.5:latest' (how Ollama lists it)."""
    name = name.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def short_name(name: str) -> str:
    return name[:-len(":latest")] if name.endswith(":latest") else name


def best_model(models):
    """The model every Skorpion app picks: the biggest one (see launcher.py)."""
    return max(models, key=lambda m: m.get("size", 0)) if models else None


def run(argv, timeout=30, input_text=None):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           input=input_text)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(argv[:3])} timed out"


def systemctl(*args, user=False):
    return run(["systemctl"] + (["--user"] if user else []) + list(args))


def unit_value(prop, unit, user=False):
    rc, out = systemctl("show", "-p", prop, "--value", unit, user=user)
    return out.strip() if rc == 0 else ""


def ollama_unit_exists() -> bool:
    return unit_value("LoadState", OLLAMA_UNIT) == "loaded"


def ollama_installed() -> bool:
    return shutil.which("ollama") is not None or ollama_unit_exists()


def ollama_active() -> bool:
    return systemctl("is-active", OLLAMA_UNIT)[1] == "active"


def ollama_enabled() -> bool:
    return systemctl("is-enabled", OLLAMA_UNIT)[1] == "enabled"


def remote_running() -> bool:
    return systemctl("is-active", REMOTE_UNIT, user=True)[1] == "active"


def user_ollama_pids():
    """`ollama serve` started by this user (the wizard's fallback when the
    systemd service could not start)."""
    rc, out = run(["pgrep", "-u", str(os.getuid()), "-x", "ollama"])
    return [int(p) for p in out.split()] if rc == 0 else []


def api_version(base, timeout=4):
    r = requests.get(f"{base}/api/version", timeout=timeout)
    r.raise_for_status()
    return r.json().get("version", "?")


def api_models(base, timeout=6):
    r = requests.get(f"{base}/api/tags", timeout=timeout)
    r.raise_for_status()
    return [{"name": m.get("name") or m.get("model", "?"), "size": m.get("size", 0)}
            for m in r.json().get("models", [])]


def load_remote():
    try:
        return json.loads(REMOTE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_remote(cfg):
    REMOTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMOTE_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def read_state():
    """Everything the window shows, read in one go (worker thread)."""
    st = {
        "installed": ollama_installed(),
        "unit": ollama_unit_exists(),
        "remote_cfg": load_remote(),
        "remote_on": remote_running(),
        "api": False, "version": None, "models": [], "error": "",
    }
    st["active"] = st["unit"] and ollama_active()
    st["enabled"] = st["unit"] and ollama_enabled()
    st["user_serve"] = bool(user_ollama_pids())
    try:
        st["version"] = api_version(LOCAL_URL)
        st["api"] = True
        st["models"] = api_models(LOCAL_URL)
    except Exception as e:
        st["error"] = str(e)
    return st


def parse_endpoint(text):
    """'192.168.1.20', 'pc.lan:11434', 'http://[fe80::1]:8080/' -> (host, port)."""
    t = text.strip()
    if not t:
        raise ValueError("Write the IP address or the name of the other computer.")
    if t.lower().startswith("https://"):
        raise ValueError("HTTPS addresses are not supported: use the plain Ollama "
                         "address of the other computer (http, usually port 11434).")
    t = re.sub(r"^http://", "", t, flags=re.I).split("/", 1)[0]
    port = OLLAMA_PORT
    m = re.fullmatch(r"\[([0-9a-fA-F:.]+)\](?::(\d+))?", t)
    if m:
        host, port = m.group(1), int(m.group(2) or port)
    elif t.count(":") == 1:
        host, p = t.split(":")
        if not p.isdigit():
            raise ValueError(f"'{p}' is not a port number.")
        port = int(p)
    else:
        host = t   # a name, an IPv4 address or a bare IPv6 address
    if not (0 < port < 65536):
        raise ValueError("The port must be between 1 and 65535.")
    if not re.fullmatch(r"[A-Za-z0-9.\-:]+", host or ""):
        raise ValueError(f"'{host}' is not a valid address.")
    try:
        ip = ipaddress.ip_address(host)
        local = ip.is_loopback or ip.is_unspecified
    except ValueError:
        local = host.lower() in ("localhost", socket.gethostname().lower())
    if local:
        raise ValueError("That is this computer. Write the address of the OTHER computer.")
    return host, port


def endpoint_url(host, port):
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def search_library(query):
    """Models of the Ollama library that match the query:
    [{"name", "desc", "sizes", "caps"}]."""
    r = requests.get(f"{LIBRARY_URL}/search?q={quote_plus(query)}", timeout=15,
                     headers={"User-Agent": "skai-manager"})
    r.raise_for_status()
    page = r.text
    results, seen = [], set()
    for m in re.finditer(r'<a href="/library/([\w.\-]+)"(.*?)</a>', page, re.S):
        name, block = m.group(1), m.group(2)
        if name in seen:
            continue
        seen.add(name)
        desc = re.search(r"<p[^>]*line-clamp[^>]*>(.*?)</p>", block, re.S)
        desc = html.unescape(re.sub(r"<[^>]+>", "", desc.group(1))).strip() if desc else ""
        sizes = [html.unescape(s).strip() for s in
                 re.findall(r"bg-\[#ddf4ff\][^>]*>([^<]+)</span>", block)]
        sizes = [s for s in sizes if "cloud" not in s.lower()]
        caps = [html.unescape(s).strip() for s in
                re.findall(r"bg-indigo-50[^>]*>([^<]+)</span>", block)]
        results.append({"name": name, "desc": desc, "sizes": sizes, "caps": caps})
    return results


def manifest_size(name):
    """(exists, size in bytes) of a model in the Ollama registry.
    exists is None when the registry can't tell (e.g. hf.co/... models)."""
    model, _, tag = full_name(name).rpartition(":")
    parts = model.split("/")
    if len(parts) > 2 or (len(parts) == 2 and "." in parts[0]):
        return None, None      # another registry (hf.co/...): Ollama checks it itself
    path = model if len(parts) == 2 else f"library/{model}"
    r = requests.get(f"{REGISTRY_URL}/{path}/manifests/{tag}", timeout=15, headers={
        "Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    if r.status_code == 404:
        return False, None
    r.raise_for_status()
    data = r.json()
    layers = data.get("layers", [])
    return True, sum(int(l.get("size", 0)) for l in layers) or None


def free_space(path=MODELS_DIR):
    for p in (path, os.path.dirname(path), "/usr/share", "/"):
        try:
            return shutil.disk_usage(p).free
        except OSError:
            continue
    return None


def run_sudo(password, *argv, timeout=90):
    if password is None:
        return run(["sudo", "-n", *argv], timeout=timeout)
    return run(["sudo", "-S", "-p", "", *argv], timeout=timeout, input_text=password + "\n")


def sudo_needs_password() -> bool:
    return run(["sudo", "-n", "true"], timeout=10)[0] != 0


def sudo_password_ok(password) -> bool:
    return run(["sudo", "-S", "-p", "", "true"], timeout=15,
               input_text=password + "\n")[0] == 0


def wait_for(check, seconds, step=0.5):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if check():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def port_free(port=OLLAMA_PORT) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def api_up() -> bool:
    try:
        api_version(LOCAL_URL, timeout=2)
        return True
    except Exception:
        return False


# ── jobs: functions run by Job (first argument = the Job) ─────────────────

def job_pull(job, name):
    job.report(None, f"Starting the download of {name}…")
    with requests.post(f"{LOCAL_URL}/api/pull", json={"model": name, "name": name,
                                                       "stream": True},
                       stream=True, timeout=(5, 900)) as r:
        job.response = r
        if r.status_code != 200:
            try:
                msg = r.json().get("error", r.text)
            except Exception:
                msg = r.text
            raise RuntimeError(msg or f"HTTP {r.status_code}")
        for line in r.iter_lines():
            if job.cancelled:
                raise Cancelled()
            if not line:
                continue
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(data["error"])
            status = data.get("status", "")
            total, done = data.get("total"), data.get("completed")
            if status == "success":
                job.report(100, f"{name} downloaded.")
                return name
            if total and done is not None and total > 1_000_000:
                job.report(int(done * 100 / total),
                           f"Downloading {name}: {human_size(done)} of {human_size(total)}")
            elif status:
                job.report(None, status[:1].upper() + status[1:] + "…")
    if job.cancelled:
        raise Cancelled()
    raise RuntimeError("The download stopped before it was complete.")


def job_delete(job, names):
    for name in names:
        job.report(None, f"Removing {name}…")
        r = requests.delete(f"{LOCAL_URL}/api/delete", json={"model": name, "name": name},
                            timeout=60)
        if r.status_code not in (200, 404):
            try:
                msg = r.json().get("error", r.text)
            except Exception:
                msg = r.text
            raise RuntimeError(f"{name}: {msg}")
    return names


def job_service(job, turn_on, password):
    if turn_on:
        job.report(None, "Starting Ollama…")
        if ollama_unit_exists():
            rc, out = run_sudo(password, "systemctl", "enable", "--now", OLLAMA_UNIT)
            if rc != 0:
                raise RuntimeError(f"systemctl enable --now ollama failed:\n{out}")
        elif shutil.which("ollama"):
            subprocess.Popen(["ollama", "serve"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            raise RuntimeError("Ollama is not installed.")
        if not wait_for(api_up, 30):
            raise RuntimeError("Ollama was started but does not answer yet. "
                               "Wait a little and press Refresh.")
        return True
    job.report(None, "Turning Ollama off…")
    if ollama_unit_exists() and (ollama_active() or ollama_enabled()):
        rc, out = run_sudo(password, "systemctl", "disable", "--now", OLLAMA_UNIT)
        if rc != 0:
            raise RuntimeError(f"systemctl disable --now ollama failed:\n{out}")
    for pid in user_ollama_pids():
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    if not wait_for(lambda: not api_up(), 15):
        raise RuntimeError("Ollama is still answering: it was started in some other way.")
    return False


def write_remote_unit():
    USER_UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_UNIT_FILE.write_text(
        "[Unit]\n"
        "Description=SKAI remote link (127.0.0.1:11434 -> the Ollama of another computer)\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={sys.executable} {REMOTE_SCRIPT}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n", encoding="utf-8")


def job_test_remote(job, host, port):
    base = endpoint_url(host, port)
    job.report(None, f"Contacting {host}:{port}…")
    try:
        version = api_version(base, timeout=6)
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Nothing answers on {host}:{port}. Check the address, that "
                           "Ollama runs there listening on the network "
                           "(OLLAMA_HOST=0.0.0.0) and that its firewall lets the port through.")
    except requests.exceptions.Timeout:
        raise RuntimeError(f"{host}:{port} does not answer (timeout).")
    except Exception:
        raise RuntimeError(f"{host}:{port} answers, but it is not an Ollama server.")
    return {"version": version, "models": api_models(base, timeout=10)}


def job_connect(job, host, port, password):
    was_enabled = ollama_unit_exists() and (ollama_enabled() or ollama_active())
    cfg = load_remote()
    if not cfg.get("connected"):
        cfg["local_was_enabled"] = was_enabled
    save_remote(cfg)
    try:
        if was_enabled:
            job.report(None, "Turning the local Ollama off…")
            rc, out = run_sudo(password, "systemctl", "disable", "--now", OLLAMA_UNIT)
            if rc != 0:
                raise RuntimeError(f"Could not turn the local Ollama off:\n{out}")
        for pid in user_ollama_pids():
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        systemctl("stop", REMOTE_UNIT, user=True)
        if not wait_for(port_free, 15):
            raise RuntimeError(f"Port {OLLAMA_PORT} is still busy on this computer.")
        job.report(None, f"Linking this computer to {host}:{port}…")
        cfg.update({"host": host, "port": port, "connected": True})
        save_remote(cfg)
        write_remote_unit()
        systemctl("daemon-reload", user=True)
        rc, out = systemctl("enable", "--now", REMOTE_UNIT, user=True)
        if rc != 0 or not wait_for(api_up, 15):
            raise RuntimeError("The link to the other computer did not start"
                               + (f":\n{out}" if out else "."))
    except Exception:
        try:
            job_disconnect(job, password)   # put the local Ollama back
        except Exception:
            pass
        raise
    return True


def job_disconnect(job, password):
    job.report(None, "Removing the link to the other computer…")
    systemctl("disable", "--now", REMOTE_UNIT, user=True)
    cfg = load_remote()
    restore = cfg.get("local_was_enabled", True)
    cfg["connected"] = False
    save_remote(cfg)
    if restore and ollama_unit_exists():
        job.report(None, "Turning the local Ollama back on…")
        wait_for(port_free, 10)
        rc, out = run_sudo(password, "systemctl", "enable", "--now", OLLAMA_UNIT)
        if rc != 0:
            raise RuntimeError(f"The link is removed, but the local Ollama did not start:\n{out}")
        wait_for(api_up, 30)
    return True


# ──────────────────────────────────────────────────────────────────────────
# QT PLUMBING
# ──────────────────────────────────────────────────────────────────────────

class Job(QtCore.QThread):
    progress = Signal(object, str)   # percent (None = unknown), text
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self.fn, self.args = fn, args
        self.cancelled = False
        self.response = None

    def report(self, pct, text):
        self.progress.emit(pct, text)

    def cancel(self):
        self.cancelled = True
        try:
            if self.response is not None:
                self.response.close()
        except Exception:
            pass

    def run(self):
        try:
            result = self.fn(self, *self.args)
        except Cancelled:
            self.failed.emit("")
            return
        except Exception as e:
            if self.cancelled:
                self.failed.emit("")
            else:
                self.failed.emit(str(e) or e.__class__.__name__)
            return
        self.done.emit(result)


def stylesheet(theme):
    c = PALETTES[theme]
    return f"""
QWidget {{ background-color: {c['bg']}; color: {c['text']}; font-family: {FONT}; font-size: 12px; }}
QLabel {{ background: transparent; }}
QLabel#title {{ color: {c['accent']}; font-size: 24px; font-weight: bold; letter-spacing: 4px; }}
QLabel#subtitle {{ color: {c['dim']}; font-size: 11px; letter-spacing: 2px; }}
QLabel#section {{ color: {c['accent2']}; font-size: 12px; font-weight: bold; letter-spacing: 1px; }}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#error {{ color: {c['red']}; }}
QLabel#ok {{ color: {c['green']}; }}
QLabel#statKey {{ color: {c['dim']}; font-size: 10px; letter-spacing: 1px; }}
QLabel#statValue {{ font-size: 13px; font-weight: bold; }}
QLabel#statValue[tone="ok"] {{ color: {c['green']}; }}
QLabel#statValue[tone="off"] {{ color: {c['red']}; }}
QLabel#statValue[tone="remote"] {{ color: {c['blue']}; }}
QLabel#bigState {{ font-size: 20px; font-weight: bold; letter-spacing: 2px; }}
QLabel#bigState[tone="ok"] {{ color: {c['green']}; }}
QLabel#bigState[tone="off"] {{ color: {c['red']}; }}
QLabel#bigState[tone="remote"] {{ color: {c['blue']}; }}
QLabel#modelName {{ font-size: 13px; font-weight: bold; }}
QLabel#small {{ color: {c['muted']}; font-size: 11px; }}
QLabel#hw {{ color: {c['accent2']}; font-size: 10px; }}
QFrame#stat, QFrame#row, QFrame#card {{
    background-color: {c['bg2']}; border: 1px solid {c['border']}; border-radius: 6px; }}
QFrame#card:hover {{ border-color: {c['accent']}; }}
QFrame#card[installed="true"] {{ border: 1px solid {c['green']}; }}
QFrame#card:disabled {{ border-color: {c['border']}; }}
QFrame#note {{ background-color: {c['bg2']}; border: 1px solid {c['border']};
    border-left: 3px solid {c['accent']}; border-radius: 4px; }}
QFrame#warn {{ background-color: {c['bg2']}; border: 1px solid {c['red']};
    border-left: 3px solid {c['red']}; border-radius: 4px; }}
QFrame#stat *, QFrame#row *, QFrame#card *, QFrame#note *, QFrame#warn * {{
    background: transparent; border: none; }}
QFrame#row QPushButton#danger {{ border: 1px solid {c['border']}; padding: 4px 14px; }}
QFrame#row QPushButton#danger:hover {{ border-color: {c['red']}; }}
QLabel#inUse, QLabel#badge_default {{ color: {c['accent']}; border: 1px solid {c['accent']};
    border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
QLabel#badge_light {{ color: {c['green']}; border: 1px solid {c['green']}; border-radius: 3px;
    padding: 1px 6px; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
QLabel#badge_balanced {{ color: {c['blue']}; border: 1px solid {c['blue']}; border-radius: 3px;
    padding: 1px 6px; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
QLabel#badge_advanced {{ color: #cc88ff; border: 1px solid #cc88ff; border-radius: 3px;
    padding: 1px 6px; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
QLabel#badge_max {{ color: {c['red']}; border: 1px solid {c['red']}; border-radius: 3px;
    padding: 1px 6px; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
QFrame#card QLabel#badge_light, QFrame#card QLabel#badge_balanced, QFrame#card QLabel#badge_default,
QFrame#card QLabel#badge_advanced, QFrame#card QLabel#badge_max {{ border-width: 1px; border-style: solid; }}
QPushButton#primary {{ background-color: {c['accent']}; color: {c['on_accent']}; border: none;
    border-radius: 4px; padding: 9px 22px; font-weight: bold; letter-spacing: 1px; }}
QPushButton#primary:hover {{ background-color: {c['accent2']}; }}
QPushButton#primary:disabled {{ background-color: {c['disabled']}; color: {c['dim']}; }}
QPushButton, QPushButton#secondary {{ background-color: transparent; color: {c['muted']};
    border: 1px solid {c['border']}; border-radius: 4px; padding: 8px 18px; letter-spacing: 1px; }}
QPushButton:hover, QPushButton#secondary:hover {{ border-color: {c['accent']}; color: {c['text']}; }}
QPushButton:disabled {{ color: {c['disabled']}; border-color: {c['border']}; }}
QPushButton#danger {{ color: {c['red']}; }}
QPushButton#danger:hover {{ border-color: {c['red']}; color: {c['red']}; }}
QPushButton#variant {{ padding: 5px 12px; color: {c['blue']}; }}
QPushButton#theme {{ padding: 6px 14px; font-size: 11px; }}
QPushButton#secondary:disabled, QPushButton#danger:disabled, QPushButton#variant:disabled,
QPushButton#theme:disabled, QFrame#row QPushButton#danger:disabled {{
    color: {c['dim']}; border-color: {c['border']}; }}
QFrame#card[off="true"] QLabel#modelName, QFrame#card[off="true"] QLabel#small {{ color: {c['dim']}; }}
QLineEdit {{ background-color: {c['bg2']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 4px; padding: 8px 12px; font-size: 13px; selection-background-color: {c['accent']}; }}
QLineEdit:focus {{ border-color: {c['accent']}; }}
QListWidget {{ background-color: {c['bg2']}; border: 1px solid {c['border']}; border-radius: 4px;
    padding: 4px; outline: none; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 3px; }}
QListWidget::item:hover {{ background-color: {c['hover']}; }}
QListWidget::item:selected {{ background-color: {c['select']}; color: {c['text']}; }}
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['dim']}; padding: 9px 22px; border: none;
    border-bottom: 2px solid transparent; font-weight: bold; letter-spacing: 1px; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QProgressBar {{ background-color: {c['bg2']}; border: 1px solid {c['border']}; border-radius: 3px; }}
QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 3px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {c['bg']}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background-color: {c['bg2']}; color: {c['text']}; border: 1px solid {c['accent']}; padding: 6px; }}
QMessageBox, QDialog {{ background-color: {c['bg']}; }}
"""


def label(text, obj=None, wrap=False, rich=False):
    w = QtWidgets.QLabel(text)
    if obj:
        w.setObjectName(obj)
    if wrap:
        w.setWordWrap(True)
    if rich:
        w.setTextFormat(Qt.TextFormat.RichText)
    else:
        w.setTextFormat(Qt.TextFormat.PlainText)
    return w


def button(text, obj=None, slot=None):
    b = QtWidgets.QPushButton(text)
    if obj:
        b.setObjectName(obj)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def note(html_text, obj="note"):
    f = QtWidgets.QFrame()
    f.setObjectName(obj)
    lay = QtWidgets.QVBoxLayout(f)
    lay.setContentsMargins(14, 10, 14, 10)
    lbl = label(html_text, wrap=True, rich=True)
    lbl.setOpenExternalLinks(False)
    lay.addWidget(lbl)
    f.text_label = lbl
    return f


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


class ClickFrame(QtWidgets.QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled() \
                and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class PasswordDialog(QtWidgets.QDialog):
    """Asks for the user's password (sudo), like the setup wizard did."""

    def __init__(self, parent, reason):
        super().__init__(parent)
        self.setWindowTitle("Authentication required")
        self.setModal(True)
        self.setMinimumWidth(420)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        lay.addWidget(label("🔒  Authentication required", "section"))
        lay.addWidget(label(reason + "\nEnter your user password.", "muted", wrap=True))
        self.edit = QtWidgets.QLineEdit()
        self.edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText("Password…")
        lay.addWidget(self.edit)
        self.err = label("Wrong password, try again.", "error")
        self.err.hide()
        lay.addWidget(self.err)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Cancel", "secondary", self.reject))
        ok = button("Confirm", "primary", self._check)
        ok.setDefault(True)
        row.addWidget(ok)
        lay.addLayout(row)
        self.edit.returnPressed.connect(self._check)
        self.password = None

    def _check(self):
        pwd = self.edit.text()
        if pwd:
            QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            ok = sudo_password_ok(pwd)
            QtWidgets.QApplication.restoreOverrideCursor()
            if ok:
                self.password = pwd
                self.accept()
                return
        self.err.show()
        self.edit.clear()
        self.edit.setFocus()


# ──────────────────────────────────────────────────────────────────────────
# MAIN WINDOW
# ──────────────────────────────────────────────────────────────────────────

class SkaiManager(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.theme = skcommon.load_theme()
        self.state = None
        self.busy_job = None          # the one action running (download, remove …)
        self.side_jobs = set()        # searches, checks, refreshes
        self.remote_test = None       # (host, port, result) of the last good test
        self.search_results = []
        self.resize(960, 760)
        self.setMinimumSize(780, 560)
        self._build()
        self._apply_theme()
        self.refresh()

    # ── layout ─────────────────────────────────────────────────────────────
    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(label("SKAI MANAGER", "title"))
        header.addStretch()
        self.btn_refresh = button("↻  REFRESH", "theme", self.refresh)
        self.btn_refresh.setToolTip("Read the state again (F5)")
        header.addWidget(self.btn_refresh)
        self.btn_theme = button("", "theme", self.toggle_theme)
        self.btn_theme.setToolTip("Switch between the dark and the light theme (Ctrl+T)")
        header.addWidget(self.btn_theme)
        root.addLayout(header)
        sub = label("SKORPION_OS  ·  LOCAL AI MODELS AND SERVICE", "subtitle")
        root.addWidget(sub)

        stats = QtWidgets.QHBoxLayout()
        stats.setSpacing(10)
        self.stat_where = self._stat(stats, "AI RUNS ON")
        self.stat_ollama = self._stat(stats, "OLLAMA")
        self.stat_model = self._stat(stats, "MODEL IN USE")
        root.addLayout(stats)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._models_tab(), "MODELS")
        self.tabs.addTab(self._service_tab(), "ON / OFF")
        self.tabs.addTab(self._remote_tab(), "REMOTE PC")
        root.addWidget(self.tabs, 1)

        # progress of the running action
        self.progress_box = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(self.progress_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        prow = QtWidgets.QHBoxLayout()
        self.progress_label = label("", "muted")
        prow.addWidget(self.progress_label, 1)
        self.btn_cancel = button("Cancel", "secondary", self.cancel_job)
        prow.addWidget(self.btn_cancel)
        pl.addLayout(prow)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        pl.addWidget(self.progress_bar)
        self.progress_box.hide()
        root.addWidget(self.progress_box)

        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+T"), self, self.toggle_theme)
        QtGui.QShortcut(QtGui.QKeySequence("F5"), self, self.refresh)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, self.close)

    def _stat(self, row, title):
        f = QtWidgets.QFrame()
        f.setObjectName("stat")
        lay = QtWidgets.QVBoxLayout(f)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(2)
        lay.addWidget(label(title, "statKey"))
        value = label("…", "statValue")
        lay.addWidget(value)
        row.addWidget(f, 1)
        return value

    @staticmethod
    def _scroll(inner):
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(inner)
        return area

    def _models_tab(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(4, 14, 12, 8)
        lay.setSpacing(10)

        self.installed_title = label("INSTALLED MODELS", "section")
        lay.addWidget(self.installed_title)
        self.installed_box = QtWidgets.QVBoxLayout()
        self.installed_box.setSpacing(6)
        lay.addLayout(self.installed_box)
        lay.addWidget(note("ⓘ  " + ONE_MODEL_NOTE))

        lay.addSpacing(6)
        lay.addWidget(label("QWEN 2.5 FAMILY  ·  THE MODELS OFFERED BY THE SETUP WIZARD", "section"))
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(8)
        self.cards = {}
        for i, (tag, name, size, badge, desc, hw) in enumerate(MODELS):
            card = ClickFrame()
            card.setObjectName("card")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setToolTip(f"{desc}\n\n{hw}\n\nClick to download {tag}.")
            cl = QtWidgets.QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.setSpacing(4)
            cl.addWidget(label(name, "modelName"))
            b = label(badge, BADGE_IDS[i])
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            brow = QtWidgets.QHBoxLayout()
            brow.addWidget(b)
            brow.addStretch()
            cl.addLayout(brow)
            state = label(size, "small")
            cl.addWidget(state)
            card.state_label = state
            card.size_text = size
            card.clicked.connect(lambda t=tag: self.request_download(t))
            grid.addWidget(card, 0, i)
            self.cards[tag] = card
        lay.addLayout(grid)
        self.qwen_hint = label("Hover a card for details. Click it to download that model.", "small")
        lay.addWidget(self.qwen_hint)

        lay.addSpacing(6)
        lay.addWidget(label("FIND A MODEL BY NAME", "section"))
        lay.addWidget(label("Any model of the Ollama library (ollama.com/library), e.g. "
                            "llama3.2, mistral, gemma3, phi4, or an exact name such as "
                            "qwen2.5:7b.", "small", wrap=True))
        srow = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Model name…")
        self.search_edit.returnPressed.connect(self.search)
        srow.addWidget(self.search_edit, 1)
        self.btn_search = button("SEARCH", "primary", self.search)
        srow.addWidget(self.btn_search)
        lay.addLayout(srow)
        self.search_status = label("", "small", wrap=True)
        lay.addWidget(self.search_status)
        self.results = QtWidgets.QListWidget()
        self.results.setMinimumHeight(170)
        self.results.setWordWrap(True)
        self.results.currentRowChanged.connect(self._show_variants)
        self.results.hide()
        lay.addWidget(self.results)
        self.variant_box = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(self.variant_box)
        vl.setContentsMargins(0, 0, 0, 0)
        self.variant_title = label("", "muted", wrap=True)
        vl.addWidget(self.variant_title)
        self.variant_row = QtWidgets.QHBoxLayout()
        self.variant_row.setSpacing(6)
        vl.addLayout(self.variant_row)
        self.variant_box.hide()
        lay.addWidget(self.variant_box)
        lay.addStretch()
        return self._scroll(page)

    def _service_tab(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(4, 18, 12, 8)
        lay.setSpacing(14)
        lay.addWidget(label("LOCAL OLLAMA SERVICE", "section"))
        self.service_state = label("…", "bigState")
        lay.addWidget(self.service_state)
        self.service_text = label("", "muted", wrap=True)
        lay.addWidget(self.service_text)
        row = QtWidgets.QHBoxLayout()
        self.btn_service = button("", "primary", self.toggle_service)
        row.addWidget(self.btn_service)
        row.addStretch()
        lay.addLayout(row)
        lay.addWidget(note(
            "<b>Turn off</b> stops Ollama and frees the memory it uses: the models stay on "
            "disk and nothing has to be downloaded again. It stays off after a restart, until "
            "you turn it on here. While it is off the SKAI features of the Skorpion apps "
            "(launcher assistant, Firefox extension, AI sidebars) don't work.<br><br>"
            "<b>Remove</b> (in the Models tab) deletes a model from disk: to use it again "
            "it has to be downloaded again."))
        lay.addStretch()
        return self._scroll(page)

    def _remote_tab(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(4, 18, 12, 8)
        lay.setSpacing(12)
        lay.addWidget(label("USE THE AI OF ANOTHER COMPUTER", "section"))
        lay.addWidget(note(
            "<b>⚠  FOR EXPERIENCED USERS — this requires technical knowledge.</b><br><br>"
            "The other computer must run Ollama listening on the network "
            "(<code>OLLAMA_HOST=0.0.0.0</code> in its service), with its firewall letting the "
            "port through (11434 by default) and the model you want already downloaded there. "
            "For the Firefox extension it also needs <code>OLLAMA_ORIGINS=*</code>.<br><br>"
            "While connected, the local Ollama is turned off and <b>every</b> Skorpion app sends "
            "its requests, and so your texts, to that computer. The connection is "
            "<b>not encrypted</b>: use it only on a network you trust (your home LAN or a VPN). "
            "The other computer must stay on, or the SKAI features stop working.", "warn"))
        lay.addWidget(label("Address of the other computer (IP or name, optional :port)", "muted"))
        row = QtWidgets.QHBoxLayout()
        self.remote_edit = QtWidgets.QLineEdit()
        self.remote_edit.setPlaceholderText("e.g. 192.168.1.20  or  workstation.lan:11434")
        self.remote_edit.textEdited.connect(self._remote_edited)
        self.remote_edit.returnPressed.connect(self.test_remote)
        row.addWidget(self.remote_edit, 1)
        self.btn_test = button("TEST", "secondary", self.test_remote)
        row.addWidget(self.btn_test)
        lay.addLayout(row)
        self.remote_result = label("", "muted", wrap=True)
        lay.addWidget(self.remote_result)
        brow = QtWidgets.QHBoxLayout()
        self.btn_connect = button("CONNECT", "primary", self.connect_remote)
        brow.addWidget(self.btn_connect)
        self.btn_disconnect = button("DISCONNECT  ·  USE THIS COMPUTER AGAIN", "secondary",
                                     self.disconnect_remote)
        brow.addWidget(self.btn_disconnect)
        brow.addStretch()
        lay.addLayout(brow)
        self.remote_state = label("", "muted", wrap=True)
        lay.addWidget(self.remote_state)
        lay.addStretch()
        return self._scroll(page)

    # ── theme ──────────────────────────────────────────────────────────────
    def _apply_theme(self):
        QtWidgets.QApplication.instance().setStyleSheet(stylesheet(self.theme))
        self.btn_theme.setText("☀  LIGHT" if self.theme == "dark" else "☾  DARK")

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        skcommon.save_theme(self.theme)
        self._apply_theme()

    # ── jobs ───────────────────────────────────────────────────────────────
    def start_side(self, fn, *args, done=None, failed=None):
        job = Job(fn, *args, parent=self)
        self.side_jobs.add(job)
        if done:
            job.done.connect(done)
        if failed:
            job.failed.connect(failed)
        job.finished.connect(lambda j=job: self.side_jobs.discard(j))
        job.start()
        return job

    def start_action(self, fn, *args, done=None, cancellable=False, title=""):
        if self.busy_job:
            return None
        job = Job(fn, *args, parent=self)
        self.busy_job = job
        job.progress.connect(self._on_progress)
        if done:
            job.done.connect(done)
        job.failed.connect(lambda msg, t=title: self._action_failed(t, msg))
        job.finished.connect(self._action_finished)
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("Working…")
        self.btn_cancel.setVisible(cancellable)
        self.btn_cancel.setEnabled(True)
        self.progress_box.show()
        self._update_enabled()
        job.start()
        return job

    def _on_progress(self, pct, text):
        if pct is None:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, pct)))
        self.progress_label.setText(text)

    def _action_failed(self, title, msg):
        if msg:
            self.warn(title or "Error", msg)

    def _action_finished(self):
        self.busy_job = None
        self.progress_box.hide()
        self.refresh()

    def cancel_job(self):
        if self.busy_job:
            self.btn_cancel.setEnabled(False)
            self.progress_label.setText("Stopping…")
            self.busy_job.cancel()

    # ── dialogs ────────────────────────────────────────────────────────────
    def warn(self, title, text):
        QtWidgets.QMessageBox.warning(self, title, text)

    def info(self, title, text):
        QtWidgets.QMessageBox.information(self, title, text)

    def choose(self, title, text, buttons, icon=QtWidgets.QMessageBox.Icon.Question):
        """buttons: [(text, role)]; returns the index of the clicked one or None.
        The default is always the last button (the safe one)."""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(text)
        made = [box.addButton(t, role) for t, role in buttons]
        box.setDefaultButton(made[-1])
        box.setEscapeButton(made[-1])
        box.exec()
        clicked = box.clickedButton()
        return made.index(clicked) if clicked in made else None

    def ask_password(self, reason):
        """None = no password needed, False = cancelled, else the password."""
        if not sudo_needs_password():
            return None
        dlg = PasswordDialog(self, reason)
        skcommon.prepare_window(dlg)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return False
        return dlg.password

    # ── state ──────────────────────────────────────────────────────────────
    def refresh(self):
        self.btn_refresh.setEnabled(False)
        self.start_side(lambda job: read_state(), done=self._got_state,
                        failed=lambda m: self.btn_refresh.setEnabled(True))

    def _got_state(self, st):
        self.state = st
        self.btn_refresh.setEnabled(True)
        remote = st["remote_on"]
        cfg = st["remote_cfg"]
        where = f"{cfg.get('host')}:{cfg.get('port')}" if cfg.get("host") else ""

        def tone(lbl, t):
            lbl.setProperty("tone", t)
            repolish(lbl)

        if remote:
            self.stat_where.setText(f"REMOTE  {where}")
            tone(self.stat_where, "remote")
        else:
            self.stat_where.setText("THIS COMPUTER")
            tone(self.stat_where, "")
        if remote:
            self.stat_ollama.setText("REACHABLE" if st["api"] else "NOT REACHABLE")
            tone(self.stat_ollama, "remote" if st["api"] else "off")
        elif st["api"]:
            self.stat_ollama.setText(f"RUNNING  v{st['version']}")
            tone(self.stat_ollama, "ok")
        elif not st["installed"]:
            self.stat_ollama.setText("NOT INSTALLED")
            tone(self.stat_ollama, "off")
        else:
            self.stat_ollama.setText("OFF")
            tone(self.stat_ollama, "off")
        best = best_model(st["models"])
        self.stat_model.setText(short_name(best["name"]) if best else "—")

        self._fill_installed(st, best)
        self._fill_cards(st)
        self._fill_service(st)
        self._fill_remote(st)
        self._update_enabled()

    def _can_manage_models(self):
        st = self.state
        return bool(st and st["api"] and not st["remote_on"])

    def _update_enabled(self):
        st = self.state or {}
        idle = self.busy_job is None
        models_ok = self._can_manage_models() and idle
        for card in self.cards.values():
            card.setEnabled(models_ok)
            if card.property("off") != (not models_ok):
                card.setProperty("off", not models_ok)
                for w in [card] + card.findChildren(QtWidgets.QLabel):
                    repolish(w)
        for b in self.findChildren(QtWidgets.QPushButton, "variant"):
            b.setEnabled(models_ok)
        for b in self.findChildren(QtWidgets.QPushButton, "danger"):
            b.setEnabled(models_ok)
        self.btn_service.setEnabled(idle and st.get("installed", False)
                                    and not st.get("remote_on", False))
        self.btn_test.setEnabled(idle)
        linked = bool(st.get("remote_on") or st.get("remote_cfg", {}).get("connected", False))
        self.btn_connect.setText("CONNECT TO THIS ONE INSTEAD" if linked else "CONNECT")
        self.btn_connect.setEnabled(idle and self.remote_test is not None)
        self.btn_disconnect.setVisible(linked)
        self.btn_disconnect.setEnabled(idle)

    def _fill_installed(self, st, best):
        clear_layout(self.installed_box)
        if st["remote_on"]:
            cfg = st["remote_cfg"]
            self.installed_title.setText(f"MODELS ON THE OTHER COMPUTER  ({cfg.get('host')})")
        else:
            self.installed_title.setText("INSTALLED MODELS")
        if not st["api"]:
            if st["remote_on"]:
                msg = ("The other computer does not answer. Check that it is on, or "
                       "disconnect in the Remote PC tab.")
            elif not st["installed"]:
                msg = ("Ollama is not installed on this computer (the SKAI setup was skipped), "
                       "so models can't be downloaded here. You can still use the AI of "
                       "another computer: see the Remote PC tab.")
            else:
                msg = "Ollama is off. Turn it on in the ON / OFF tab to see and manage the models."
            self.installed_box.addWidget(label(msg, "muted", wrap=True))
            return
        if not st["models"]:
            self.installed_box.addWidget(label(
                "No model installed: the SKAI features don't work until you download one "
                "(see below).", "muted", wrap=True))
            return
        for m in sorted(st["models"], key=lambda m: -m["size"]):
            row = QtWidgets.QFrame()
            row.setObjectName("row")
            row.setMinimumHeight(46)
            rl = QtWidgets.QHBoxLayout(row)
            rl.setContentsMargins(14, 8, 10, 8)
            rl.addWidget(label(short_name(m["name"]), "modelName"))
            if best and m["name"] == best["name"]:
                badge = label("IN USE", "inUse")
                badge.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,
                                    QtWidgets.QSizePolicy.Policy.Fixed)
                rl.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
            rl.addStretch()
            rl.addWidget(label(human_size(m["size"]), "small"))
            if not st["remote_on"]:
                rm = button("Remove", "danger", lambda _=False, n=m["name"]: self.request_remove(n))
                rm.setToolTip("Delete this model from disk (it has to be downloaded again to use it)")
                rl.addWidget(rm)
            self.installed_box.addWidget(row)
        if st["remote_on"]:
            self.installed_box.addWidget(label(
                "The models are managed on the other computer: downloads and removals are "
                "disabled while you are connected.", "small", wrap=True))
        elif len(st["models"]) > 1:
            self.installed_box.addWidget(label(
                f"Only {short_name(best['name'])} is used. The other models just take disk "
                "space: you can remove them.", "small", wrap=True))

    def _fill_cards(self, st):
        names = {m["name"] for m in st["models"]} if not st["remote_on"] else set()
        for tag, card in self.cards.items():
            installed = full_name(tag) in names
            card.setProperty("installed", installed)
            card.state_label.setText(f"✓ installed  ·  {card.size_text}" if installed
                                     else card.size_text)
            repolish(card)
        if st["remote_on"]:
            self.qwen_hint.setText("Disabled while this computer uses the AI of another computer.")
        elif not st["api"]:
            self.qwen_hint.setText("Turn Ollama on (ON / OFF tab) to download models.")
        else:
            self.qwen_hint.setText("Hover a card for details. Click it to download that model.")

    def _fill_service(self, st):
        tone = ""
        if st["remote_on"]:
            self.service_state.setText("OFF  ·  USING ANOTHER COMPUTER")
            tone = "remote"
            self.service_text.setText(
                "The local Ollama is off because this computer uses the AI of another one. "
                "Disconnect in the Remote PC tab to use this computer again.")
            self.btn_service.setText("TURN ON")
        elif not st["installed"]:
            self.service_state.setText("NOT INSTALLED")
            tone = "off"
            self.service_text.setText(
                "Ollama is not installed on this computer: the SKAI setup was skipped. "
                "You can still use the AI of another computer (Remote PC tab).")
            self.btn_service.setText("TURN ON")
        elif st["api"]:
            self.service_state.setText("ON")
            tone = "ok"
            self.service_text.setText("Ollama is running and the Skorpion apps can use the AI.")
            self.btn_service.setText("TURN OFF")
        else:
            self.service_state.setText("OFF")
            tone = "off"
            self.service_text.setText("Ollama is off: the SKAI features of the Skorpion apps "
                                      "don't work until you turn it on.")
            self.btn_service.setText("TURN ON")
        self.service_state.setProperty("tone", tone)
        repolish(self.service_state)

    def _fill_remote(self, st):
        cfg = st["remote_cfg"]
        if cfg.get("host") and not self.remote_edit.text():
            h, p = cfg["host"], cfg.get("port", OLLAMA_PORT)
            self.remote_edit.setText(h if p == OLLAMA_PORT else
                                     (f"[{h}]:{p}" if ":" in h else f"{h}:{p}"))
        if st["remote_on"]:
            best = best_model(st["models"])
            self.remote_state.setText(
                f"● Connected to {cfg.get('host')}:{cfg.get('port')}. "
                + (f"The Skorpion apps use its model {short_name(best['name'])}."
                   if best else "It has no model: download one there.")
                + ("" if st["api"] else "  It does not answer right now."))
            self.remote_state.setObjectName("ok" if st["api"] else "error")
        elif cfg.get("connected"):
            self.remote_state.setText("The link to the other computer is set but not running. "
                                      "Disconnect, then connect again.")
            self.remote_state.setObjectName("error")
        else:
            self.remote_state.setText("Not connected: the AI runs on this computer.")
            self.remote_state.setObjectName("muted")
        repolish(self.remote_state)

    # ── models: download ───────────────────────────────────────────────────
    def request_download(self, name):
        if self.busy_job:
            return
        st = self.state
        if not st or st["remote_on"]:
            self.info("Download", "Models can't be downloaded while this computer uses the "
                      "AI of another computer.")
            return
        if not st["api"]:
            self.info("Download", "Ollama is off. Turn it on in the ON / OFF tab first.")
            return
        name = name.strip()
        if full_name(name) in {m["name"] for m in st["models"]}:
            self.info("Download", f"{short_name(full_name(name))} is already installed.")
            return
        if "cloud" in full_name(name).rsplit(":", 1)[-1].lower():
            self.info("Download", f"{name} is a cloud model: it does not run on this "
                      "computer, so SKAI can't use it.")
            return
        self.start_action(lambda job, n: manifest_size(n), name, title="Download",
                          done=lambda res, n=name: self._confirm_download(n, res))
        self.progress_label.setText(f"Looking up {name} on ollama.com…")

    def _confirm_download(self, name, res):
        exists, size = res
        if exists is False:
            QtCore.QTimer.singleShot(0, lambda: self.warn(
                "Model not found", f"Ollama has no downloadable model called “{name}”.\n\n"
                "Check the name with Search. Some models of the library run only in "
                "Ollama's cloud and can't be downloaded."))
            return
        QtCore.QTimer.singleShot(0, lambda: self._decide_download(name, size))

    def _decide_download(self, name, size):
        if self.busy_job:   # the size check has not fully finished yet
            QtCore.QTimer.singleShot(50, lambda: self._decide_download(name, size))
            return
        models = self.state["models"] if self.state else []
        best = best_model(models)
        nice = short_name(full_name(name))
        size_txt = human_size(size) if size else "unknown size"
        free = free_space()
        if size and free is not None and size * 1.05 > free:
            extra = ""
            if models:
                extra = "<br><br>Removing the installed models first would free " \
                        f"{human_size(sum(m['size'] for m in models))}."
            self.choose("Not enough disk space",
                        f"<b>{html.escape(nice)}</b> needs {size_txt}, but only "
                        f"{human_size(free)} are free.{extra}",
                        [("OK", QtWidgets.QMessageBox.ButtonRole.RejectRole)],
                        QtWidgets.QMessageBox.Icon.Warning)
            return
        replace = []
        if not best:
            if self.choose("Download", f"Download <b>{html.escape(nice)}</b> ({size_txt})?",
                           [("Download", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                            ("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)]) != 0:
                return
        elif size and size <= best["size"]:
            bigger = [m["name"] for m in models if m["size"] >= size]
            bigger_txt = ", ".join(short_name(n) for n in bigger)
            answer = self.choose(
                "This model would not be used",
                f"<b>{html.escape(nice)}</b> ({size_txt}) is smaller than "
                f"<b>{html.escape(short_name(best['name']))}</b> ({human_size(best['size'])}), "
                "the model you use now.<br><br>"
                "The whole Skorpion suite always uses the <b>biggest</b> installed model, so "
                f"{html.escape(nice)} would never be used while {html.escape(bigger_txt)} "
                "is installed.<br><br>"
                f"<b>Replace</b> downloads {html.escape(nice)} and then removes "
                f"{html.escape(bigger_txt)}.",
                [("Replace", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                 ("Download anyway", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                 ("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)],
                QtWidgets.QMessageBox.Icon.Warning)
            if answer == 0:
                replace = bigger
            elif answer != 1:
                return
        else:
            others = ", ".join(short_name(m["name"]) for m in models)
            bigger_note = ("It is bigger than what you have, so the suite will switch to it."
                           if size else "Its size is unknown: the suite will use it only if "
                           "it is bigger than the installed models.")
            if self.choose(
                    "Download",
                    f"Download <b>{html.escape(nice)}</b> ({size_txt})?<br><br>{bigger_note}"
                    "<br><br>The Skorpion suite always uses only the biggest model: keeping "
                    f"{html.escape(others)} too is useless, you will be offered to remove "
                    "it when the download is done.",
                    [("Download", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                     ("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)]) != 0:
                return
        self.start_action(job_pull, full_name(name), cancellable=True, title="Download failed",
                          done=lambda n, r=replace: self._downloaded(n, r))

    def _downloaded(self, name, replace):
        QtCore.QTimer.singleShot(0, lambda: self._after_download(name, replace))

    def _after_download(self, name, replace):
        if self.busy_job:
            QtCore.QTimer.singleShot(50, lambda: self._after_download(name, replace))
            return
        if replace:
            self.start_action(job_delete, replace, title="Remove failed")
            return
        # read the new list, then offer to drop the models that are no longer used
        self.start_side(lambda job: read_state(),
                        done=lambda st, n=name: self._offer_cleanup(st, n))

    def _offer_cleanup(self, st, name):
        self._got_state(st)
        best = best_model(st["models"])
        if not best or best["name"] != name:
            if best and len(st["models"]) > 1:
                self.info("Downloaded", f"{short_name(name)} is installed, but "
                          f"{short_name(best['name'])} is bigger, so the suite keeps using "
                          f"{short_name(best['name'])}.")
            return
        unused = [m for m in st["models"] if m["name"] != name]
        if not unused:
            self.info("Downloaded", f"{short_name(name)} is ready. The Skorpion apps use it now.")
            return
        listed = "<br>".join(f"•  {html.escape(short_name(m['name']))}  ({human_size(m['size'])})"
                             for m in unused)
        if self.choose("Downloaded",
                       f"<b>{html.escape(short_name(name))}</b> is ready and the Skorpion apps "
                       f"use it now.<br><br>These models are no longer used:<br>{listed}<br><br>"
                       f"Remove them to free {human_size(sum(m['size'] for m in unused))}?",
                       [("Remove them", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                        ("Keep them", QtWidgets.QMessageBox.ButtonRole.RejectRole)]) == 0:
            self.start_action(job_delete, [m["name"] for m in unused], title="Remove failed")

    # ── models: remove ─────────────────────────────────────────────────────
    def request_remove(self, name):
        if self.busy_job or not self._can_manage_models():
            return
        models = self.state["models"]
        best = best_model(models)
        size = next((m["size"] for m in models if m["name"] == name), 0)
        rest = [m for m in models if m["name"] != name]
        if len(rest) == 0:
            effect = ("It is your only model: the SKAI features of the Skorpion apps stop "
                      "working until you download another one.")
        elif best and best["name"] == name:
            nxt = best_model(rest)
            effect = (f"It is the model in use: the suite will switch to "
                      f"<b>{html.escape(short_name(nxt['name']))}</b>.")
        else:
            effect = "It is not the model in use, so nothing changes for the Skorpion apps."
        if self.choose("Remove model",
                       f"Remove <b>{html.escape(short_name(name))}</b> ({human_size(size)})?"
                       f"<br><br>It is deleted from disk: to use it again it has to be "
                       f"downloaded again.<br><br>{effect}",
                       [("Remove", QtWidgets.QMessageBox.ButtonRole.DestructiveRole),
                        ("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)],
                       QtWidgets.QMessageBox.Icon.Warning) == 0:
            self.start_action(job_delete, [name], title="Remove failed")

    # ── models: search ─────────────────────────────────────────────────────
    def search(self):
        query = self.search_edit.text().strip()
        if not query:
            return
        self.btn_search.setEnabled(False)
        self.search_status.setText(f"Searching “{query}” on ollama.com…")
        self.results.clear()
        self.results.hide()
        self.variant_box.hide()

        def work(job, q):
            exact = None
            if re.fullmatch(r"[\w.\-/]+(:[\w.\-]+)?", q):
                try:
                    exists, size = manifest_size(q)
                    if exists:
                        exact = {"name": short_name(full_name(q)), "size": size}
                except Exception:
                    pass
            base = q.split(":", 1)[0].split("/")[-1]
            return exact, search_library(base)

        self.start_side(work, query, done=self._got_results, failed=self._search_failed)

    def _search_failed(self, msg):
        self.btn_search.setEnabled(True)
        self.search_status.setText(f"The search failed (is the Internet connection up?)\n{msg}")

    def _got_results(self, res):
        self.btn_search.setEnabled(True)
        exact, found = res
        self.search_results = []
        if exact:
            self.search_results.append({"name": exact["name"], "exact": True,
                                        "size": exact["size"], "desc": "", "sizes": [],
                                        "caps": []})
        self.search_results += found
        if not self.search_results:
            self.search_status.setText("Ollama has no model with that name or a similar one.")
            return
        self.search_status.setText(f"{len(self.search_results)} result(s). Select one to see "
                                   "its versions.")
        for r in self.search_results:
            if r.get("exact"):
                text = f"{r['name']}   ·   exact match, {human_size(r['size'])}"
            else:
                extra = []
                if r["sizes"]:
                    extra.append(" ".join(r["sizes"]))
                if r["caps"]:
                    extra.append(", ".join(r["caps"]))
                text = r["name"] + ("   ·   " + "   ·   ".join(extra) if extra else "")
                if r["desc"]:
                    text += "\n" + r["desc"]
            item = QtWidgets.QListWidgetItem(text)
            if not r.get("exact") and "embedding" in [c.lower() for c in r["caps"]]:
                item.setToolTip("Embedding model: it can't chat, the SKAI features can't use it.")
            self.results.addItem(item)
        self.results.show()
        self.results.setCurrentRow(0)

    def _show_variants(self, row):
        clear_layout(self.variant_row)
        if row < 0 or row >= len(self.search_results):
            self.variant_box.hide()
            return
        r = self.search_results[row]
        if r.get("exact"):
            tags = [r["name"]]
        else:
            tags = [f"{r['name']}:{s}" for s in r["sizes"]] or [r["name"]]
        caps = [c.lower() for c in r.get("caps", [])]
        title = f"Versions of {r['name']} (bigger = better answers, but more RAM and disk). " \
                "Click one to download it."
        if "embedding" in caps:
            title = (f"{r['name']} is an embedding model: it can't chat, so the SKAI features "
                     "can't use it.")
        self.variant_title.setText(title)
        for t in tags:
            b = button(t.split(":", 1)[1] if ":" in t and not r.get("exact") else t, "variant",
                       lambda _=False, n=t: self.request_download(n))
            b.setToolTip(f"Download {t}")
            b.setEnabled(self._can_manage_models() and self.busy_job is None
                         and "embedding" not in caps)
            self.variant_row.addWidget(b)
        self.variant_row.addStretch()
        self.variant_box.show()

    # ── service on / off ───────────────────────────────────────────────────
    def toggle_service(self):
        st = self.state
        if self.busy_job or not st or st["remote_on"] or not st["installed"]:
            return
        turn_on = not st["api"]
        if not turn_on:
            if self.choose("Turn Ollama off",
                           "Turn the local Ollama off?<br><br>The models stay on disk. "
                           "The SKAI features of the Skorpion apps stop working until you turn "
                           "it on again here (it stays off after a restart too).",
                           [("Turn off", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                            ("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)]) != 0:
                return
        password = None
        if st["unit"] and (turn_on or st["active"] or st["enabled"]):
            password = self.ask_password(
                "Root permissions are needed to " + ("start" if turn_on else "stop")
                + " the Ollama service.")
            if password is False:
                return
        self.start_action(job_service, turn_on, password,
                          title="Could not turn Ollama " + ("on" if turn_on else "off"))

    # ── remote computer ────────────────────────────────────────────────────
    def _remote_edited(self):
        self.remote_test = None
        self.remote_result.setText("")
        self._update_enabled()

    def test_remote(self):
        if self.busy_job:
            return
        try:
            host, port = parse_endpoint(self.remote_edit.text())
        except ValueError as e:
            self.remote_test = None
            self.remote_result.setObjectName("error")
            self.remote_result.setText(str(e))
            repolish(self.remote_result)
            self._update_enabled()
            return
        self.remote_test = None
        self.remote_result.setObjectName("muted")
        self.remote_result.setText(f"Contacting {host}:{port}…")
        repolish(self.remote_result)
        self.btn_test.setEnabled(False)
        self.start_side(job_test_remote, host, port,
                        done=lambda res, h=host, p=port: self._tested(h, p, res),
                        failed=self._test_failed)

    def _test_failed(self, msg):
        self.remote_test = None
        self.remote_result.setObjectName("error")
        self.remote_result.setText("✗  " + msg)
        repolish(self.remote_result)
        self._update_enabled()

    def _tested(self, host, port, res):
        best = best_model(res["models"])
        if best:
            text = (f"✓  Ollama {res['version']} answers on {host}:{port}, with "
                    f"{len(res['models'])} model(s). The Skorpion apps would use "
                    f"{short_name(best['name'])} ({human_size(best['size'])}).")
            self.remote_result.setObjectName("ok")
        else:
            text = (f"✓  Ollama {res['version']} answers on {host}:{port}, but it has NO "
                    "model: download one on that computer first, or the SKAI features "
                    "won't work.")
            self.remote_result.setObjectName("error")
        self.remote_result.setText(text)
        repolish(self.remote_result)
        self.remote_test = (host, port, res)
        self._update_enabled()

    def connect_remote(self):
        if self.busy_job or not self.remote_test:
            return
        host, port, res = self.remote_test
        st = self.state or {}
        local = ""
        if st.get("installed"):
            local = ("<br><br>The local Ollama will be <b>turned off</b> (your local models "
                     "stay on disk) and turned on again when you disconnect.")
        if self.choose("Connect to another computer",
                       f"Use the AI of <b>{html.escape(host)}:{port}</b> for every Skorpion "
                       f"app?{local}<br><br>Your requests travel unencrypted on the network: "
                       "do this only on a network you trust.",
                       [("Connect", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                        ("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)],
                       QtWidgets.QMessageBox.Icon.Warning) != 0:
            return
        password = None
        if st.get("unit") and (st.get("active") or st.get("enabled")):
            password = self.ask_password("Root permissions are needed to turn off the local "
                                         "Ollama service.")
            if password is False:
                return
        self.start_action(job_connect, host, port, password, title="Connection failed")

    def disconnect_remote(self):
        if self.busy_job:
            return
        st = self.state or {}
        restore = st.get("remote_cfg", {}).get("local_was_enabled", True) and st.get("unit")
        if self.choose("Disconnect",
                       "Stop using the other computer?"
                       + ("<br><br>The local Ollama will be turned on again." if restore else
                          "<br><br>This computer has no local Ollama running, so the SKAI "
                          "features will stop working."),
                       [("Disconnect", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                        ("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)]) != 0:
            return
        password = None
        if restore:
            password = self.ask_password("Root permissions are needed to start the local "
                                         "Ollama service again.")
            if password is False:
                return
        self.remote_test = None
        self.start_action(job_disconnect, password, title="Disconnect")

    # ── window ─────────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.busy_job:
            if self.busy_job.fn is job_pull:
                if self.choose("Download running", "A download is running. Stop it and close?",
                               [("Stop and close", QtWidgets.QMessageBox.ButtonRole.AcceptRole),
                                ("Keep going", QtWidgets.QMessageBox.ButtonRole.RejectRole)]) != 0:
                    event.ignore()
                    return
                self.busy_job.cancel()
                self.busy_job.wait(5000)
            else:
                self.info("Please wait", "An operation is running: wait for it to finish.")
                event.ignore()
                return
        event.accept()


# ──────────────────────────────────────────────────────────────────────────
# WIZARD NOT DONE YET
# ──────────────────────────────────────────────────────────────────────────

class NotYet(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        QtWidgets.QApplication.instance().setStyleSheet(stylesheet(skcommon.load_theme()))
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(32, 26, 32, 22)
        lay.setSpacing(14)
        lay.addWidget(label("SKAI MANAGER", "title"))
        lay.addWidget(label(
            "The SKAI setup is not finished yet.\n\n"
            "SKAI Manager becomes available once the SKAI setup wizard has run "
            "(it opens by itself at login). Complete it, or choose “Don't install SkAi”, "
            "then press Alt+F9 again.", "muted", wrap=True))
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Close", "primary", self.close))
        lay.addLayout(row)
        self.setFixedWidth(560)
        self.setMinimumHeight(300)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


# ──────────────────────────────────────────────────────────────────────────
# SINGLE INSTANCE: a second Alt+F9 closes the open window (like the launcher)
# ──────────────────────────────────────────────────────────────────────────

def toggle_running_instance() -> bool:
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(INSTANCE_NAME)
    if not sock.waitForConnected(1500):
        return False
    sock.write(b"toggle")
    sock.flush()
    connected = QtNetwork.QLocalSocket.LocalSocketState.ConnectedState
    if sock.state() == connected and sock.bytesToWrite():
        sock.waitForBytesWritten(1000)
    if sock.state() == connected:
        sock.disconnectFromServer()
    return True


def listen_for_toggle(window):
    QtNetwork.QLocalServer.removeServer(INSTANCE_NAME)  # stale socket of a crashed run
    server = QtNetwork.QLocalServer(window)
    server.newConnection.connect(window.close)
    server.listen(INSTANCE_NAME)
    return server


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(WINDOW_TITLE)
    QtGui.QGuiApplication.setDesktopFileName(APP_ID)
    if toggle_running_instance():
        sys.exit(0)
    window = NotYet() if WIZARD.exists() else SkaiManager()
    server = listen_for_toggle(window)  # noqa: F841 (kept alive)
    skcommon.prepare_window(window)
    skcommon.center_window(window)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
