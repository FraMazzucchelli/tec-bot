# ARGO - centralino per l'assistenza tecnica

ARGO è il centralino automatico a cui i tecnici si rivolgono durante gli interventi
su impianti di sicurezza e antifurto. Risponde **al telefono** e in **chat**
(Telegram e WhatsApp), cerca le risposte nei **manuali tecnici** e, quando il
problema non si risolve, **apre un ticket** su Jira.

## Come funziona

```
Telefono (Twilio Voice) ─┐
Telegram (Bot API)  ─────┼──► FastAPI ──► Assistant (Claude) ──► cerca_manuali ──► manuals/
WhatsApp (Twilio)  ──────┘                                    └─► apri_ticket  ──► Jira
```

- `src/argo/assistant.py` – nucleo indipendente dal canale: conversazione con Claude,
  strumenti `cerca_manuali` e `apri_ticket`, stile di risposta per canale (al telefono
  risposte brevi, una istruzione alla volta).
- `src/argo/knowledge.py` – ricerca per parole chiave nei manuali `.md`/`.txt` della
  cartella `manuals/`. Interfaccia pensata per passare in futuro a una ricerca vettoriale.
- `src/argo/tickets.py` – creazione ticket su Jira Cloud (REST API v3).
- `src/argo/channels/voice.py` – webhook Twilio Voice con riconoscimento vocale in italiano.
- `src/argo/channels/chat.py` – webhook Telegram e WhatsApp.

## Avvio in locale

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env        # compila le chiavi
uvicorn argo.main:app --reload --port 8000
pytest
```

Per ricevere i webhook in locale esponi la porta con un tunnel (es. `ngrok http 8000`).

## Collegare i canali

| Canale | Dove si configura | Webhook |
|---|---|---|
| Telefono | Twilio → numero → *A call comes in* | `POST {URL}/voice/incoming` |
| WhatsApp | Twilio → WhatsApp sender / sandbox | `POST {URL}/whatsapp/webhook` |
| Telegram | `https://api.telegram.org/bot<TOKEN>/setWebhook?url={URL}/telegram/webhook` | |

## Manuali

Metti i manuali in `manuals/` come file Markdown o testo. I titoli (`#`, `##`) diventano
le sezioni che ARGO cita nelle risposte. `manuals/esempio-centrale.md` è solo un esempio.

## Prossimi passi

- Verifica della firma Twilio e del secret token Telegram sui webhook.
- Cronologia conversazioni persistente (oggi è in memoria e si perde al riavvio).
- Risposte in streaming o messaggio di attesa al telefono: Twilio attende al massimo
  ~15 secondi la risposta del webhook.
- Import di manuali PDF e ricerca vettoriale.
