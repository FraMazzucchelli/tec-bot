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
| Telegram | `setWebhook` (vedi sotto) | `POST {URL}/telegram/webhook` |

## Sicurezza dei webhook

Ogni webhook rifiuta con **403** le richieste che non arrivano davvero da Twilio o Telegram.

**Twilio (telefono e WhatsApp)** – firma `X-Twilio-Signature`, verificata con l'Auth Token.
1. Copia l'**Auth Token** dalla console Twilio (pagina iniziale dell'account) in `TWILIO_AUTH_TOKEN`.
2. Metti in `PUBLIC_BASE_URL` l'indirizzo pubblico **identico** a quello inserito in Twilio
   (stesso schema `https`, stesso host, nessuna `/` finale). Se cambia anche solo un
   carattere la firma non torna e ogni chiamata riceve 403.
3. Se cambi URL del tunnel (es. nuovo indirizzo ngrok) aggiorna sia Twilio sia `PUBLIC_BASE_URL`.

**Telegram** – header `X-Telegram-Bot-Api-Secret-Token`.
1. Scegli una stringa casuale (lettere, numeri, `_` e `-`, fino a 256 caratteri), es.
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`, e mettila in `TELEGRAM_WEBHOOK_SECRET`.
2. Registra il webhook passando lo stesso valore:

   ```bash
   curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d "url=$PUBLIC_BASE_URL/telegram/webhook" \
     -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
   ```

Per prove in locale senza Twilio/Telegram puoi mettere `SKIP_SIGNATURE_CHECK=true`.
**Mai in produzione.**

## Manuali

Metti i manuali in `manuals/` come file Markdown o testo. I titoli (`#`, `##`) diventano
le sezioni che ARGO cita nelle risposte. `manuals/esempio-centrale.md` è solo un esempio.

## Prossimi passi

- Cronologia conversazioni persistente (oggi è in memoria e si perde al riavvio).
- Risposte in streaming o messaggio di attesa al telefono: Twilio attende al massimo
  ~15 secondi la risposta del webhook.
- Import di manuali PDF e ricerca vettoriale.
