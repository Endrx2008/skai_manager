# skai_manager — procedura guidata di SKAI e SKAI Manager

Il centro di controllo di **SKAI**, l'IA locale di [Skorpion OS](https://github.com/Endrx2008/skorpion_os):
la procedura guidata che parte al primo avvio e SKAI Manager per tutto il resto.
*English: [README.md](README.md).*

## In Skorpion OS

È già installato in `~/.launcher/`: non c'è nulla da scaricare e non esiste una release.

- **Procedura guidata di SKAI** (`wizard.py`, tutte le edizioni) — parte al primo avvio. Installa Ollama
  (lo `install.sh` ufficiale, chiede la password di sudo), permette a Ollama di rispondere al browser e
  ai componenti aggiuntivi (`OLLAMA_ORIGINS=*`) e scarica il modello scelto tra le schede Qwen 2.5.
  Finita la configurazione si cancella da sola; fino ad allora SKAI Manager non fa nulla.
- **SKAI Manager** (`skai_manager.py`, **Alt + F9**, tutte le edizioni):
  - **Modelli** — scarica un modello Qwen 2.5 (3B, 7B, 14B *consigliato*, 32B, 72B) o qualsiasi modello
    cercato per nome nella libreria di Ollama, e rimuove i modelli. Ogni app Skorpion usa il modello
    installato con **più parametri**, quindi ne basta uno alla volta: SKAI Manager lo ricorda prima di ogni download.
  - **Acceso / Spento** — ferma o avvia il servizio locale `ollama.service`.
  - **PC remoto** — usa l'Ollama di un altro computer: `skai_remote.py`, avviato dal servizio utente
    `skai-remote.service`, ascolta su `127.0.0.1:11434` e passa ogni connessione a quel computer, così
    tutte le app continuano a funzionare senza modifiche. L'indirizzo è salvato in `~/.config/skai/remote.json`.

Internet serve per installare Ollama e scaricare i modelli; dopo, SKAI funziona offline.

## File

| File | Cos'è |
|---|---|
| `wizard.py` | procedura guidata di SKAI (primo avvio) |
| `skai_manager.py` | SKAI Manager |
| `skai_remote.py` | l'inoltro usato da PC remoto |
| `skcommon.py` | temi e riconoscimento del desktop, lo stesso file di [launcher](https://github.com/Endrx2008/launcher) |

## Avvio a mano

```sh
python3 ~/.launcher/skai_manager.py
```

Servono Python 3, PySide6 e `requests`, già presenti in Skorpion OS.

## Licenza

GPL-3.0-or-later, vedi [LICENSE](LICENSE). © 2026 Enrico Miot (EndrX), fondatore e manutentore di Skorpion OS.
