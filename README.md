# skai_manager — SKAI setup wizard and SKAI Manager

The control centre of **SKAI**, the local AI of [Skorpion OS](https://github.com/Endrx2008/skorpion_os):
the setup wizard that runs at first boot, and SKAI Manager for everything after it.
*Italiano: [README.it.md](README.it.md).*

## In Skorpion OS

Already installed in `~/.launcher/`: nothing to download, and there is no release.

- **SKAI setup wizard** (`wizard.py`, every edition) — runs at first boot. It installs Ollama (official
  `install.sh`, asks for the sudo password), lets Ollama answer the browser and the add-ons
  (`OLLAMA_ORIGINS=*`) and downloads the model chosen from the Qwen 2.5 cards. When the setup is over
  it deletes itself; until then SKAI Manager does nothing.
- **SKAI Manager** (`skai_manager.py`, **Alt + F9**, every edition):
  - **Models** — download a Qwen 2.5 model (3B, 7B, 14B *recommended*, 32B, 72B) or any model found by
    name in the Ollama library, and remove models. Every Skorpion app uses the installed model with
    the **most parameters**, so one model at a time is enough: SKAI Manager reminds you before each download.
  - **On / Off** — stops or starts the local `ollama.service`.
  - **Remote PC** — use the Ollama of another computer: `skai_remote.py`, started by the user service
    `skai-remote.service`, listens on `127.0.0.1:11434` and passes every connection to that computer,
    so every app keeps working unchanged. The address is kept in `~/.config/skai/remote.json`.

The internet is needed to install Ollama and to download models; after that SKAI works offline.

## Files

| File | What it is |
|---|---|
| `wizard.py` | SKAI setup wizard (first boot) |
| `skai_manager.py` | SKAI Manager |
| `skai_remote.py` | the forwarder used by Remote PC |
| `skcommon.py` | themes and desktop detection, the same file as in [launcher](https://github.com/Endrx2008/launcher) |

## Run by hand

```sh
python3 ~/.launcher/skai_manager.py
```

Needs Python 3, PySide6 and `requests`, all part of Skorpion OS.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). © 2026 Enrico Miot (EndrX), founder and maintainer of Skorpion OS.
