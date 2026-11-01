#!/usr/bin/env python3

# Copyright (C) 2026 Enrico Miot
# Licensed under GNU GPL v3 or later — see /usr/share/common-licenses/GPL-3
# SPDX-License-Identifier: GPL-3.0-or-later

import sys
import os
import re
import getpass
import shutil
import subprocess
import time
import json

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QTextEdit, QFrame,
    QLineEdit, QDialogButtonBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QEventLoop
from PySide6.QtGui import QFont, QColor, QPalette, QGuiApplication


# ─────────────────────────── COLORS ────────────────────────────
PALETTES = {
    "dark": dict(
        BG="#0d0d0d", BG2="#141414", ACCENT="#e05c00", ACCENT2="#ff7a1a",
        TEXT="#e8e8e8", TEXT_DIM="#666666", GREEN="#39d353", RED="#ff4444",
        BORDER="#2a2a2a", ON_ACCENT="#000000", DISABLED="#333333",
        BLUE="#4da6ff", PURPLE="#cc88ff", PINK="#ff6666",
    ),
    "light": dict(
        BG="#f6f4f1", BG2="#ffffff", ACCENT="#c75200", ACCENT2="#e05c00",
        TEXT="#1a1a1a", TEXT_DIM="#77716b", GREEN="#1a7f37", RED="#cf222e",
        BORDER="#d9d3cb", ON_ACCENT="#ffffff", DISABLED="#e3ded7",
        BLUE="#0b63c5", PURPLE="#8250df", PINK="#c93c3c",
    ),
}

# Same file as the Skorpion launcher, so both use the same theme
THEME_FILE = os.path.expanduser("~/.config/pylauncher_settings/theme_config.json")


def load_theme() -> str:
    try:
        with open(THEME_FILE, "r", encoding="utf-8") as f:
            theme = json.load(f).get("theme", "dark")
        return theme if theme in PALETTES else "dark"
    except Exception:
        return "dark"


def save_theme(theme: str):
    try:
        os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
        with open(THEME_FILE, "w", encoding="utf-8") as f:
            json.dump({"theme": theme}, f)
    except Exception:
        pass


THEME = "dark"
BG = BG2 = ACCENT = ACCENT2 = TEXT = TEXT_DIM = GREEN = RED = BORDER = ""
ON_ACCENT = DISABLED = BLUE = PURPLE = PINK = ""
STYLESHEET = ""


# ─────────────────────────── MODELS ────────────────────────────
# Each entry: (ollama_tag, display_name, size, badge, description, hw_requirements)
MODELS = [
    (
        "qwen2.5:3b",
        "Qwen 2.5  ·  3B",
        "~2 GB",
        "LIGHT",
        (
            "Compact 3-billion-parameter model from the Qwen 2.5 family.\n"
            "Great for machines with limited resources: responds quickly even\n"
            "without a dedicated GPU. Suited to summaries, simple Q&A, and basic\n"
            "text assistance. Lower quality on complex reasoning."
        ),
        "RAM: 6 GB+  |  GPU: optional  |  Modern CPU sufficient",
    ),
    (
        "qwen2.5:7b",
        "Qwen 2.5  ·  7B",
        "~4.7 GB",
        "BALANCED",
        (
            "The ideal balance point of the Qwen 2.5 family. With 7 billion\n"
            "parameters it offers good quality on coding, text analysis, and\n"
            "multi-turn dialogue without requiring professional hardware.\n"
            "Recommended as the default choice for recent laptops."
        ),
        "RAM: 10 GB+  |  GPU: 6 GB VRAM (optional, improves speed)",
    ),
    (
        "qwen2.5:14b",
        "Qwen 2.5  ·  14B",
        "~9 GB",
        "RECOMMENDED",
        (
            "SKAI's default model. 14 billion parameters guarantee solid\n"
            "reasoning, reliable code, and advanced contextual understanding.\n"
            "Great quality/weight balance: handles complex tasks while still\n"
            "running on mid-range desktops."
        ),
        "RAM: 16 GB+  |  GPU: 8 GB VRAM recommended  |  CPU: 6+ cores",
    ),
    (
        "qwen2.5:32b",
        "Qwen 2.5  ·  32B",
        "~20 GB",
        "ADVANCED",
        (
            "32 billion parameters for performance close to large cloud\n"
            "models. Excels at multi-step reasoning, math, complex code\n"
            "generation, and analysis of long documents. Requires dedicated\n"
            "hardware: not suited to laptops or entry-level PCs."
        ),
        "RAM: 32 GB+  |  GPU: 16-24 GB VRAM  |  CPU: 8+ cores",
    ),
    (
        "qwen2.5:72b",
        "Qwen 2.5  ·  72B",
        "~46 GB",
        "MAXIMUM QUALITY",
        (
            "The most powerful model in the Qwen 2.5 family available locally.\n"
            "72 billion parameters: quality comparable to premium cloud services.\n"
            "Deep reasoning, advanced creative writing, professional coding.\n"
            "Requires a dedicated workstation or server. Slower response times."
        ),
        "RAM: 64 GB+  |  GPU: 40-48 GB VRAM (e.g. A100, RTX 6000 Ada)  |  Server/WS",
    ),
]

# tag of the currently selected model (default: 14b)
_DEFAULT_MODEL_IDX = 2


def build_stylesheet() -> str:
    return f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
}}
QLabel {{
    color: {TEXT};
}}
QLabel#title {{
    color: {ACCENT};
    font-size: 28px;
    font-weight: bold;
    letter-spacing: 4px;
}}
QLabel#subtitle {{
    color: {TEXT_DIM};
    font-size: 12px;
    letter-spacing: 2px;
}}
QLabel#section {{
    color: {ACCENT2};
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 1px;
}}
QLabel#status {{
    color: {GREEN};
    font-size: 11px;
}}
QLabel#step_label {{
    color: {TEXT_DIM};
    font-size: 11px;
}}
QPushButton#primary {{
    background-color: {ACCENT};
    color: {ON_ACCENT};
    border: none;
    border-radius: 4px;
    padding: 12px 28px;
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 1px;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT2};
}}
QPushButton#primary:disabled {{
    background-color: {DISABLED};
    color: {TEXT_DIM};
}}
QPushButton#secondary {{
    background-color: transparent;
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 12px 28px;
    font-size: 13px;
    letter-spacing: 1px;
}}
QPushButton#secondary:hover {{
    border-color: {ACCENT};
    color: {TEXT};
}}
QProgressBar {{
    background-color: {BG2};
    border: 1px solid {BORDER};
    border-radius: 3px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}
QProgressBar#done::chunk {{
    background-color: {GREEN};
}}
QTextEdit {{
    background-color: {BG2};
    color: {GREEN};
    border: 1px solid {BORDER};
    border-radius: 4px;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 11px;
    padding: 8px;
}}
QFrame#separator {{
    color: {BORDER};
}}
QFrame#model_card {{
    background-color: {BG2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 0px;
}}
QFrame#model_card_selected {{
    background-color: {BG2};
    border: 2px solid {ACCENT};
    border-radius: 6px;
    padding: 0px;
}}
QLabel#badge_default {{
    color: {ACCENT};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    border: 1px solid {ACCENT};
    border-radius: 3px;
    padding: 1px 6px;
}}
QLabel#badge_light {{
    color: {GREEN};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    border: 1px solid {GREEN};
    border-radius: 3px;
    padding: 1px 6px;
}}
QLabel#badge_balanced {{
    color: {BLUE};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    border: 1px solid {BLUE};
    border-radius: 3px;
    padding: 1px 6px;
}}
QLabel#badge_advanced {{
    color: {PURPLE};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    border: 1px solid {PURPLE};
    border-radius: 3px;
    padding: 1px 6px;
}}
QLabel#badge_max {{
    color: {PINK};
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    border: 1px solid {PINK};
    border-radius: 3px;
    padding: 1px 6px;
}}
QLabel#model_name {{
    color: {TEXT};
    font-size: 14px;
    font-weight: bold;
}}
QLabel#model_size {{
    color: {TEXT_DIM};
    font-size: 11px;
}}
QLabel#model_desc {{
    color: {TEXT_DIM};
    font-size: 11px;
    line-height: 1.5;
}}
QFrame#model_card QLabel, QFrame#model_card_selected QLabel {{
    background: transparent;
}}
QPushButton#theme {{
    background-color: transparent;
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
    font-size: 11px;
    letter-spacing: 1px;
}}
QPushButton#theme:hover {{
    border-color: {ACCENT};
    color: {TEXT};
}}
QLabel#model_hw {{
    color: {ACCENT2};
    font-size: 10px;
    letter-spacing: 0.5px;
}}
"""


def set_theme(theme: str):
    """Switches every colour constant (and the stylesheet) to the theme."""
    global THEME, STYLESHEET
    THEME = theme if theme in PALETTES else "dark"
    globals().update(PALETTES[THEME])
    STYLESHEET = build_stylesheet()


def apply_palette(app):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base,            QColor(BG2))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(BG))
    palette.setColor(QPalette.ColorRole.Text,            QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button,          QColor(BG2))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(ON_ACCENT))
    app.setPalette(palette)


set_theme(load_theme())


# ─────────────────── PROGRESS BAR WITH LABEL ────────────────────
class StepBar(QWidget):
    """Progress bar with a descriptive label and percentage."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        self.lbl = QLabel(label)
        self.lbl.setObjectName("step_label")
        self.pct_lbl = QLabel("0%")
        self.pct_lbl.setObjectName("step_label")
        self.pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self.lbl)
        row.addStretch()
        row.addWidget(self.pct_lbl)
        layout.addLayout(row)

        self.bar = QProgressBar()
        self.bar.setValue(0)
        self.bar.setFixedHeight(8)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)

    def set_value(self, v: int):
        v = max(0, min(100, v))
        self.bar.setValue(v)
        self.pct_lbl.setText(f"{v}%")

    def set_label(self, text: str):
        self.lbl.setText(text)

    def mark_done(self):
        self.bar.setObjectName("done")
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {GREEN}; border-radius: 3px; }}")
        self.set_value(100)

    def mark_error(self):
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {RED}; border-radius: 3px; }}")


# ─────────────────────── OVERLAY BASE ────────────────────────────
class _OverlayBase(QWidget):
    """Full-window dimmed overlay with a centered styled panel.

    Used instead of QDialog: a QDialog is a separate top-level window,
    and on many window managers opening one makes the fullscreen main
    window lose focus and drop out of fullscreen. This overlay is a
    plain child widget of the main window, so no new top-level window
    is ever created and fullscreen is never affected."""

    accepted = Signal()
    rejected = Signal()

    def __init__(self, parent, panel_width: int):
        super().__init__(parent)
        self.setGeometry(parent.rect())

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 170))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        outer = QVBoxLayout(self)
        outer.addStretch()
        center_row = QHBoxLayout()
        center_row.addStretch()

        self.panel = QFrame()
        self.panel.setObjectName("overlay_panel")
        self.panel.setFixedWidth(panel_width)
        self.panel.setStyleSheet(STYLESHEET + f"""
            QFrame#overlay_panel {{
                background-color: {BG};
                border: 1px solid {ACCENT};
                border-radius: 6px;
            }}
            QLineEdit {{
                background-color: {BG2};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 13px;
                font-family: 'JetBrains Mono', monospace;
            }}
            QLineEdit:focus {{
                border-color: {ACCENT};
            }}
            QDialogButtonBox QPushButton {{
                min-width: 90px;
            }}
        """)
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(32, 28, 32, 24)
        self.panel_layout.setSpacing(16)

        center_row.addWidget(self.panel)
        center_row.addStretch()
        outer.addLayout(center_row)
        outer.addStretch()


# ─────────────────────── PASSWORD OVERLAY ────────────────────────
class PasswordOverlay(_OverlayBase):
    """Overlay panel that asks the user for the sudo password."""

    def __init__(self, parent):
        super().__init__(parent, panel_width=420)

        # icon + title
        row = QHBoxLayout()
        icon = QLabel("🔒")
        icon.setStyleSheet("font-size: 28px;")
        title = QLabel("Authentication required")
        title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {ACCENT};")
        row.addWidget(icon)
        row.addSpacing(10)
        row.addWidget(title)
        row.addStretch()
        self.panel_layout.addLayout(row)

        # description
        desc = QLabel(
            "Root permissions are required to install Ollama.\n"
            "Enter your user password."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {TEXT_DIM};")
        desc.setWordWrap(True)
        self.panel_layout.addWidget(desc)

        # password field
        self.pwd_input = QLineEdit()
        self.pwd_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwd_input.setPlaceholderText("Password...")
        self.pwd_input.returnPressed.connect(self.accepted.emit)
        self.panel_layout.addWidget(self.pwd_input)

        # error label (hidden by default)
        self.lbl_error = QLabel("Wrong password, try again.")
        self.lbl_error.setStyleSheet(f"color: {RED}; font-size: 11px;")
        self.lbl_error.hide()
        self.panel_layout.addWidget(self.lbl_error)

        # buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Confirm")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("secondary")
        buttons.accepted.connect(self.accepted.emit)
        buttons.rejected.connect(self.rejected.emit)
        self.panel_layout.addWidget(buttons)

        self.pwd_input.setFocus()

    def password(self) -> str:
        return self.pwd_input.text()

    def show_error(self):
        self.lbl_error.show()
        self.pwd_input.clear()
        self.pwd_input.setFocus()


# ─────────────────────────── WORKER ────────────────────────────
class InstallWorker(QObject):
    log            = Signal(str)
    ollama_progress = Signal(int)    # 0-100
    model_progress  = Signal(int)    # 0-100
    ollama_label   = Signal(str)
    model_label    = Signal(str)
    finished       = Signal(bool, str)

    def __init__(self, sudo_password: str, model_tag: str = "qwen2.5:14b", install_model: bool = True):
        super().__init__()
        self.sudo_password = sudo_password
        self.model_tag = model_tag
        self.install_model = install_model

    # ── helper: run with line-by-line output ─────────────────
    def _run_stream(self, cmd, on_line=None, input_data=None):
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if input_data else None,
            text=True, bufsize=1
        )
        if input_data:
            proc.stdin.write(input_data)
            proc.stdin.close()
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                self.log.emit(f"    {line}")
                if on_line:
                    on_line(line)
        proc.wait()
        return proc.returncode

    # ── helper: verify sudo password ──────────────────────────
    def _check_sudo(self) -> bool:
        """Tests the password with 'sudo -S true', returns True if correct."""
        try:
            result = subprocess.run(
                ["sudo", "-S", "true"],
                input=self.sudo_password + "\n",
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

    # ── helper: install Ollama with sudo -S ──────────────────────
    def _install_ollama_sudo(self) -> int:
        import tempfile, stat

        curl_bin = shutil.which("curl") or "/usr/bin/curl"
        bash_bin = shutil.which("bash") or "/bin/bash"

        # download script as a normal user
        self.ollama_label.emit("Downloading install script...")
        self.ollama_progress.emit(3)
        self.log.emit(f"[*] Downloading install.sh ...")

        tmp = tempfile.NamedTemporaryFile(suffix=".sh", delete=False, mode="wb")
        tmp_path = tmp.name
        tmp.close()

        rc_dl = self._run_stream(
            [curl_bin, "-fsSL", "-o", tmp_path, "https://ollama.com/install.sh"]
        )
        if rc_dl != 0:
            self.log.emit(f"[!] Download failed (exit {rc_dl})")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return rc_dl

        os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IXUSR | stat.S_IXGRP)
        self.ollama_progress.emit(8)
        self.log.emit("[*] Starting installation with sudo ...")
        self.ollama_label.emit("Installing...")

        # sudo -S reads the password from stdin
        rc_inst = self._run_stream(
            ["sudo", "-S", bash_bin, tmp_path],
            on_line=self._parse_ollama_line,
            input_data=self.sudo_password + "\n"
        )

        try:
            os.remove(tmp_path)
        except OSError:
            pass

        return rc_inst

    # ── parse ollama install.sh progress ───────────────────────
    def _parse_ollama_line(self, line: str):
        low = line.lower()
        if "downloading" in low:
            m = re.search(r'(\d+)\s*%', line)
            if m:
                pct = int(m.group(1))
                self.ollama_progress.emit(int(pct * 0.70))
            else:
                self.ollama_progress.emit(10)
        elif "installing ollama" in low:
            self.ollama_progress.emit(75)
            self.ollama_label.emit("Installing binary...")
        elif "creating systemd" in low or "systemd" in low:
            self.ollama_progress.emit(85)
            self.ollama_label.emit("Configuring systemd service...")
        elif "enabling" in low or "starting" in low:
            self.ollama_progress.emit(92)
            self.ollama_label.emit("Starting service...")
        elif "install complete" in low or "installed" in low:
            self.ollama_progress.emit(100)

    # ── parse ollama pull progress ─────────────────────────────
    def _parse_model_line(self, line: str):
        low = line.lower()
        if "pulling manifest" in low:
            self.model_progress.emit(2)
            self.model_label.emit("Retrieving manifest...")
        elif "verifying" in low:
            self.model_progress.emit(96)
            self.model_label.emit("Verifying checksum...")
        elif "writing manifest" in low:
            self.model_progress.emit(98)
            self.model_label.emit("Writing manifest...")
        elif "success" in low:
            self.model_progress.emit(100)
        else:
            m = re.search(r'(\d+)\s*%', line)
            if m:
                pct = int(m.group(1))
                mapped = 3 + int(pct * 0.92)
                self.model_progress.emit(mapped)
                size_m = re.search(r'([\d.]+\s*[KMGT]?B\s*/\s*[\d.]+\s*[KMGT]?B)', line)
                if size_m:
                    self.model_label.emit(f"Download: {size_m.group(1)}")

    # ── run ─────────────────────────────────────────────────────
    def run(self):
        try:
            # ══ STEP 1: install Ollama ══════════════════════════
            self.ollama_label.emit("Downloading install script...")
            self.ollama_progress.emit(3)
            self.log.emit("[*] Starting Ollama installation ...")

            rc = self._install_ollama_sudo()

            if rc != 0:
                self.ollama_label.emit("Ollama installation error")
                self.finished.emit(False, "Error during Ollama installation.\n"
                                          "Check your Internet connection and try again.")
                return

            self.ollama_progress.emit(100)
            self.ollama_label.emit("Ollama installed ✓")
            self.log.emit("[✓] Ollama installed successfully.")

            # ══ STEP 2: write systemd override for CORS / Firefox ══
            # The content is embedded in the wizard — no external file required.
            self.log.emit("[*] Configuring Ollama CORS for Firefox extensions...")
            self.model_label.emit("Configuring systemd CORS...")
            self.model_progress.emit(2)

            override_dir  = "/etc/systemd/system/ollama.service.d"
            override_file = f"{override_dir}/skorpion-env.conf"

            override_content = (
                "[Service]\n"
                'Environment="OLLAMA_ORIGINS=*"\n'
                "Environment=OLLAMA_HOST=127.0.0.1:11434\n"
                "Environment=OLLAMA_MODELS=/usr/share/ollama/.ollama/models\n"
            )

            # create the override directory
            rc_mkdir = self._run_stream(
                ["sudo", "-S", "mkdir", "-p", override_dir],
                input_data=self.sudo_password + "\n"
            )
            if rc_mkdir != 0:
                self.log.emit(f"[!] Unable to create {override_dir} (exit {rc_mkdir})")
                self.finished.emit(False, "Error while configuring the Ollama service.")
                return

            # remove the override.conf created by Ollama's official installer
            # (it's all commented out and takes precedence over our files)
            ollama_default_override = f"{override_dir}/override.conf"
            self._run_stream(
                ["sudo", "-S", "rm", "-f", ollama_default_override],
                input_data=self.sudo_password + "\n"
            )
            self.log.emit("[*] Removed the Ollama installer's override.conf (if present)")

            # write the content to a temp file, then copy it with sudo
            import tempfile
            tmp_conf = tempfile.NamedTemporaryFile(
                mode="w", suffix=".conf", delete=False
            )
            tmp_conf.write(override_content)
            tmp_conf.flush()
            tmp_conf.close()

            rc_cp = self._run_stream(
                ["sudo", "-S", "cp", tmp_conf.name, override_file],
                input_data=self.sudo_password + "\n"
            )
            try:
                os.remove(tmp_conf.name)
            except OSError:
                pass

            if rc_cp != 0:
                self.log.emit(f"[!] Writing override failed (exit {rc_cp})")
                self.finished.emit(False, "Error while writing the systemd override.")
                return

            # correct permissions: root:root 644
            self._run_stream(
                ["sudo", "-S", "chmod", "644", override_file],
                input_data=self.sudo_password + "\n"
            )
            self.log.emit(f"[✓] Override written to {override_file}")

            # daemon-reload
            rc_reload = self._run_stream(
                ["sudo", "-S", "systemctl", "daemon-reload"],
                input_data=self.sudo_password + "\n"
            )
            if rc_reload != 0:
                self.log.emit(f"[!] daemon-reload failed (exit {rc_reload})")
                self.finished.emit(False, "Error while reloading systemd.")
                return
            self.log.emit("[✓] systemctl daemon-reload completed.")

            # ══ STEP 3: start ollama.service (already with correct CORS) ══
            self.model_label.emit("Starting Ollama service...")
            self.model_progress.emit(5)
            self.log.emit("[*] Starting ollama.service via systemctl...")

            rc_start = self._run_stream(
                ["sudo", "-S", "systemctl", "start", "ollama"],
                input_data=self.sudo_password + "\n"
            )
            if rc_start != 0:
                self.log.emit(f"[!] Starting ollama.service failed (exit {rc_start}), "
                              "trying ollama serve instead...")
                subprocess.Popen(["ollama", "serve"],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)

            self.log.emit("[*] Waiting for Ollama to be ready...")
            time.sleep(3)
            self.log.emit("[✓] ollama.service started with OLLAMA_ORIGINS=* .")

            # ══ STEP 4: pull model ══════════════════════════════
            self.model_label.emit(f"Starting download of {self.model_tag}...")
            self.model_progress.emit(8)
            self.log.emit(f"[*] ollama pull {self.model_tag} ...")

            rc = self._run_stream(
                ["ollama", "pull", self.model_tag],
                on_line=self._parse_model_line
            )

            if rc != 0:
                self.model_label.emit("Model download error")
                self.finished.emit(False, "Error while downloading the model.")
                return

            self.model_progress.emit(100)
            self.model_label.emit(f"{self.model_tag} ready ✓")
            self.log.emit(f"[✓] Model {self.model_tag} ready.")

            # ══ STEP 5: fix models folder permissions ════════════
            models_dir = "/usr/share/ollama/.ollama/models"
            self.log.emit("[*] Setting permissions on Ollama models folder...")

            rc_chown = self._run_stream(
                ["sudo", "-S", "chown", "-R", "ollama:ollama", models_dir],
                input_data=self.sudo_password + "\n"
            )
            if rc_chown != 0:
                self.log.emit(f"[!] chown failed (exit {rc_chown}), continuing anyway...")
            else:
                self.log.emit(f"[✓] chown -R ollama:ollama {models_dir}")

            rc_chmod = self._run_stream(
                ["sudo", "-S", "chmod", "-R", "755", models_dir],
                input_data=self.sudo_password + "\n"
            )
            if rc_chmod != 0:
                self.log.emit(f"[!] chmod failed (exit {rc_chmod}), continuing anyway...")
            else:
                self.log.emit(f"[✓] chmod -R 755 {models_dir}")

            self.finished.emit(True, "Setup completed successfully!")

        except Exception as e:
            self.finished.emit(False, str(e))


# ──────────────────────────── UI ────────────────────────────────
class SkaiSetupWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SKAI — Skorpion_OS Setup")
        self.setMinimumSize(820, 600)
        self.setStyleSheet(STYLESHEET)
        self.showFullScreen()
        self._install_model = True
        self._selected_model_idx = _DEFAULT_MODEL_IDX  # index in MODELS
        self._screen = None
        self._build_ui()
        self._show_welcome()

    # ── build ──────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(60, 40, 60, 40)
        root.setSpacing(0)

        # Header
        header = QHBoxLayout()
        title = QLabel("SKAI")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.btn_theme = QPushButton("☀  LIGHT" if THEME == "dark" else "☾  DARK")
        self.btn_theme.setObjectName("theme")
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.setToolTip("Switch between the dark and the light theme (Ctrl+T)")
        self.btn_theme.clicked.connect(self._toggle_theme)
        header.addWidget(self.btn_theme)
        root.addLayout(header)

        # Subtitle row
        sub_row = QHBoxLayout()
        sub = QLabel("SKORPION_OS  ·  AI SETUP WIZARD")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sub_row.addStretch()
        sub_row.addWidget(sub)
        root.addLayout(sub_row)
        root.addSpacing(20)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{BORDER};")
        root.addWidget(sep)
        root.addSpacing(36)

        self.content = QVBoxLayout()
        root.addLayout(self.content)
        root.addStretch()

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("status")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_status)

    # ── Helper: shows an overlay panel and blocks until it's closed,
    # mimicking QDialog.exec() but without opening a top-level window ──
    def _run_overlay(self, overlay) -> bool:
        loop = QEventLoop()
        result = {"ok": False}

        def _on_accept():
            result["ok"] = True
            loop.quit()

        def _on_reject():
            result["ok"] = False
            loop.quit()

        overlay.accepted.connect(_on_accept)
        overlay.rejected.connect(_on_reject)
        overlay.show()
        overlay.raise_()
        loop.exec()

        overlay.hide()
        overlay.deleteLater()
        return result["ok"]

    def _clear_content(self):
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    # ── Screen 1 — welcome ─────────────────────────────────
    def _show_welcome(self):
        self._screen = self._show_welcome
        self._clear_content()

        _, name, size, _, _, _ = MODELS[self._selected_model_idx]
        msg = QLabel(
            "Welcome to Skorpion_OS.\n\n"
            "SKAI integrates a local AI model directly into your system,\n"
            "with no cloud and no external connections.\n\n"
            "To work, SKAI requires:\n"
            "  •  Ollama  (runtime for local LLMs)\n"
            f"  •  {name}  ({size} — downloaded only once)\n\n"
            "Do you want to proceed with the installation?"
        )
        msg.setWordWrap(True)
        msg.setStyleSheet(f"font-size:14px; color:{TEXT}; line-height:1.7;")
        msg.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.content.addWidget(msg)
        self.content.addSpacing(40)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        btn_yes = QPushButton("  Choose model and install  →")
        btn_yes.setObjectName("primary")
        btn_yes.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_yes.clicked.connect(self._show_model_select)

        btn_later = QPushButton("Next time  (no internet right now)")
        btn_later.setObjectName("secondary")
        btn_later.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_later.clicked.connect(QApplication.quit)

        btn_skip = QPushButton("Don't install SkAi (leave sk_os without skai services)")
        btn_skip.setObjectName("secondary")
        btn_skip.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_skip.clicked.connect(self._skip_and_exit)

        btn_row.addWidget(btn_yes)
        btn_row.addStretch()
        btn_row.addWidget(btn_later)
        btn_row.addWidget(btn_skip)
        self.content.addLayout(btn_row)

    # ── Screen 1b — model selection ───────────────────────
    def _show_model_select(self):
        self._screen = self._show_model_select
        self._clear_content()

        intro = QLabel("Choose the AI model to install:")
        intro.setObjectName("section")
        self.content.addWidget(intro)
        self.content.addSpacing(12)

        # badge objectName → color
        badge_names = ["badge_light", "badge_balanced", "badge_default",
                       "badge_advanced", "badge_max"]

        self._model_card_frames = []

        for i, (tag, name, size, badge, desc, hw) in enumerate(MODELS):
            card = QFrame()
            card.setObjectName(
                "model_card_selected" if i == self._selected_model_idx else "model_card"
            )
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setFixedHeight(110)

            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            card_layout.setSpacing(16)

            # left column: name + badge + size
            left = QVBoxLayout()
            left.setSpacing(4)
            left.setContentsMargins(0, 0, 0, 0)

            name_lbl = QLabel(name)
            name_lbl.setObjectName("model_name")
            left.addWidget(name_lbl)

            badge_lbl = QLabel(badge)
            badge_lbl.setObjectName(badge_names[i])
            badge_lbl.setFixedWidth(130)
            left.addWidget(badge_lbl)

            size_lbl = QLabel(size)
            size_lbl.setObjectName("model_size")
            left.addWidget(size_lbl)

            left.addStretch()

            sep_v = QFrame()
            sep_v.setFrameShape(QFrame.Shape.VLine)
            sep_v.setStyleSheet(f"color: {BORDER};")
            sep_v.setFixedWidth(1)

            # middle column: description
            mid = QVBoxLayout()
            mid.setSpacing(4)
            mid.setContentsMargins(0, 0, 0, 0)

            desc_lbl = QLabel(desc)
            desc_lbl.setObjectName("model_desc")
            desc_lbl.setWordWrap(True)
            mid.addWidget(desc_lbl)
            mid.addStretch()

            hw_lbl = QLabel(f"⚙  {hw}")
            hw_lbl.setObjectName("model_hw")
            mid.addWidget(hw_lbl)

            card_layout.addLayout(left, 0)
            card_layout.addWidget(sep_v)
            card_layout.addLayout(mid, 1)

            # click on the card → select
            idx_capture = i
            card.mousePressEvent = lambda _evt, idx=idx_capture: self._select_model(idx)

            self.content.addWidget(card)
            self.content.addSpacing(6)
            self._model_card_frames.append(card)

        self.content.addSpacing(20)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        self._btn_confirm_model = QPushButton("  Continue  →")
        self._btn_confirm_model.setObjectName("primary")
        self._btn_confirm_model.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_confirm_model.clicked.connect(self._show_confirm)

        btn_back = QPushButton("← Back")
        btn_back.setObjectName("secondary")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.clicked.connect(self._show_welcome)

        btn_row.addWidget(self._btn_confirm_model)
        btn_row.addWidget(btn_back)
        btn_row.addStretch()
        self.content.addLayout(btn_row)

    def _select_model(self, idx: int):
        """Updates the highlighted card and the chosen model index."""
        self._selected_model_idx = idx
        for i, frame in enumerate(self._model_card_frames):
            frame.setObjectName(
                "model_card_selected" if i == idx else "model_card"
            )
            # force Qt style refresh
            frame.style().unpolish(frame)
            frame.style().polish(frame)

    # ── Screen 1c — confirmation ─────────────────────────────────
    def _show_confirm(self):
        self._screen = self._show_confirm
        self._clear_content()
        self._install_model = True

        tag, name, size, badge, _, hw = MODELS[self._selected_model_idx]

        desc = (
            "You are about to install:\n"
            "  •  Ollama\n"
            f"  •  {name}  ({size})\n\n"
            f"Recommended hardware requirements:\n"
            f"  {hw}\n\n"
            "An Internet connection is required.\n"
            "The process may take several minutes."
        )
        lbl = QLabel(desc)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size:14px; color:{TEXT};")
        self.content.addWidget(lbl)
        self.content.addSpacing(40)

        btn_row = QHBoxLayout()
        btn_start = QPushButton("  Start installation  ")
        btn_start.setObjectName("primary")
        btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_start.clicked.connect(self._start_install)

        btn_back = QPushButton("← Back")
        btn_back.setObjectName("secondary")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.clicked.connect(self._show_model_select)

        btn_row.addWidget(btn_start)
        btn_row.addWidget(btn_back)
        btn_row.addStretch()
        self.content.addLayout(btn_row)

    # ── Screen 2 — installation ─────────────────────────────────
    def _start_install(self):
        # ── ask for password before building the screen ─────
        password = None
        show_err = False
        while True:
            overlay = PasswordOverlay(self.centralWidget())
            if show_err:
                overlay.show_error()
            if not self._run_overlay(overlay):
                self._show_confirm()
                return
            pwd = overlay.password()
            if not pwd:
                show_err = True
                continue

            try:
                result = subprocess.run(
                    ["sudo", "-S", "true"],
                    input=pwd + "\n",
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    password = pwd
                    break
                else:
                    show_err = True
                    continue
            except Exception:
                show_err = True
                continue

        self._clear_content()

        tag, name, _, _, _, _ = MODELS[self._selected_model_idx]

        # ── Ollama bar ──
        lbl_ollama = QLabel("Ollama")
        lbl_ollama.setObjectName("section")
        self.content.addWidget(lbl_ollama)
        self.content.addSpacing(4)

        self._bar_ollama = StepBar("Downloading install script...")
        self.content.addWidget(self._bar_ollama)
        self.content.addSpacing(20)

        # ── Model bar ──
        lbl_model = QLabel(f"Model  {tag}")
        lbl_model.setObjectName("section")
        self.content.addWidget(lbl_model)
        self.content.addSpacing(4)

        self._bar_model = StepBar("Waiting for Ollama...")
        self.content.addWidget(self._bar_model)
        self.content.addSpacing(20)

        # ── Log ──
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(260)
        self.content.addWidget(self.log_view)

        # ── Thread ──
        self.btn_theme.setEnabled(False)  # the install screen cannot be redrawn
        self._thread = QThread()
        self._worker = InstallWorker(sudo_password=password, model_tag=tag)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._append_log)
        self._worker.ollama_progress.connect(self._bar_ollama.set_value)
        self._worker.ollama_label.connect(self._bar_ollama.set_label)
        self._worker.model_progress.connect(self._bar_model.set_value)
        self._worker.model_label.connect(self._bar_model.set_label)
        self._worker.finished.connect(self._on_finish)
        self._thread.start()

    def _append_log(self, text: str):
        self.log_view.append(text)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def _on_finish(self, success: bool, msg: str):
        if success:
            self._bar_ollama.mark_done()
            self._bar_model.mark_done()
        else:
            self._bar_ollama.mark_error()
            self._bar_model.mark_error()

        self._thread.quit()
        self._thread.wait()
        self.btn_theme.setEnabled(True)
        self._show_finish(success, msg)

    def _show_finish(self, success: bool, msg: str):
        self._screen = lambda: self._show_finish(success, msg)
        self._clear_content()

        icon = "✓" if success else "✗"
        color = GREEN if success else RED
        lbl_icon = QLabel(icon)
        lbl_icon.setStyleSheet(f"font-size:64px; color:{color};")
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content.addWidget(lbl_icon)

        lbl_msg = QLabel(msg)
        lbl_msg.setStyleSheet(f"font-size:16px; color:{TEXT}; margin-top:16px;")
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content.addWidget(lbl_msg)

        if success:
            note = QLabel("Restart the terminal or run  `ollama serve`  to use SKAI.")
            note.setStyleSheet(f"font-size:12px; color:{TEXT_DIM}; margin-top:8px;")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content.addWidget(note)

        self.content.addSpacing(40)
        btn = QPushButton("  Close  ")
        btn.setObjectName("primary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._close_only)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        btn_row.addStretch()
        self.content.addLayout(btn_row)

        self.lbl_status.setText("Setup completed." if success else "Setup failed.")

    # ── Skip — ONLY case in which .skai is removed here ──────────
    def _skip_and_exit(self):
        self._clear_content()
        lbl = QLabel("No SkAi install. Cleaning up...")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"font-size:16px; color:{TEXT_DIM};")
        self.content.addWidget(lbl)
        QTimer.singleShot(1500, self._cleanup_skai_and_close)

    def _cleanup_skai_and_close(self):
        """Removes .skai — only when the user chose 'Do nothing'."""
        skai_dir = os.path.expanduser("~/.skai")
        if os.path.isdir(skai_dir):
            shutil.rmtree(skai_dir, ignore_errors=True)
        self._delete_self()
        QApplication.quit()

    # ── Close without touching .skai ────────────────────────────────
    def _close_only(self):
        """Closes the wizard without removing .skai, but deleting the script."""
        self._delete_self()
        QApplication.quit()

    # ── Auto-delete of the script (+ svr_setup.py) ────────────────
    def _delete_self(self):
        """Deletes wizard.py, together with svr_setup.py if an older
        release left it next to the wizard."""
        _remove_svr_setup()
        try:
            os.remove(os.path.abspath(__file__))
        except Exception:
            pass

    def _toggle_theme(self):
        if hasattr(self, '_thread') and self._thread.isRunning():
            return
        set_theme("light" if THEME == "dark" else "dark")
        save_theme(THEME)
        apply_palette(QApplication.instance())
        self.setStyleSheet(STYLESHEET)
        screen = self._screen
        self._build_ui()  # new widgets pick up the new colours
        screen()

    def keyPressEvent(self, event):
        if hasattr(self, '_thread') and self._thread.isRunning():
            return
        if event.key() == Qt.Key.Key_T and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._toggle_theme()
            return
        super().keyPressEvent(event)


# ──────────────────────────── MAIN ──────────────────────────────
def is_live_session() -> bool:
    return getpass.getuser() == "user"


def already_setup() -> bool:
    if shutil.which("ollama") is None:
        return False
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10
        )
        installed_tags = {tag for tag, *_ in MODELS}
        return any(
            any(tag in line for tag in installed_tags)
            for line in result.stdout.splitlines()
        )
    except Exception:
        return False


def _remove_svr_setup():
    """Deletes svr_setup.py from wizard.py's folder, if present."""
    try:
        server_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "svr_setup.py"
        )
        if os.path.isfile(server_path):
            os.remove(server_path)
    except Exception:
        pass


def _silent_self_delete():
    """Deletes wizard.py, together with svr_setup.py if present."""
    _remove_svr_setup()
    try:
        os.remove(os.path.abspath(__file__))
    except Exception:
        pass


def main():
    if is_live_session():
        time.sleep(4)
        sys.exit(0)

    if already_setup():
        _silent_self_delete()
        sys.exit(0)

    if os.environ.get("SKAI_DETACHED") != "1":
        env = os.environ.copy()
        env["SKAI_DETACHED"] = "1"
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            start_new_session=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("SKAI Setup")
    QGuiApplication.setDesktopFileName("skorpion-wizard")  # Wayland app_id
    apply_palette(app)

    win = SkaiSetupWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
