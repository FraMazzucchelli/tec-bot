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

## Messa online (Railway)

I tecnici non installano nulla: ARGO gira su un server e loro chiamano un numero
o scrivono al bot. Il repo contiene un `Dockerfile` pronto.

1. Su [railway.com](https://railway.com) → *New Project* → *Deploy from GitHub repo* → `tec-bot`.
2. In *Variables* inserisci le variabili di `.env.example` (almeno `ANTHROPIC_API_KEY`,
   `TWILIO_AUTH_TOKEN`, `PUBLIC_BASE_URL`).
3. In *Settings → Networking* genera un dominio pubblico e mettilo in `PUBLIC_BASE_URL`
   (es. `https://tec-bot-production.up.railway.app`, senza `/` finale).
4. Controlla che `https://<dominio>/health` risponda `{"status":"ok",...}`.

Evita i piani che "addormentano" il server quando è inattivo: Twilio aspetta circa
15 secondi e una chiamata durante il risveglio fallisce.

## Operatore umano

Se il tecnico chiede di parlare con una persona, o la situazione è urgente, ARGO usa
lo strumento `passa_a_operatore`:
- **al telefono** dice "Ti passo un collega" e trasferisce la chiamata (`<Dial>`);
  se l'operatore non risponde entro 25 secondi avvisa e chiude;
- **in chat** comunica il numero da chiamare.

Il numero si imposta con `HUMAN_SUPPORT_NUMBER` in formato internazionale (`+39...`),
**solo nelle variabili del server**: il repo è pubblico. Su Twilio abilita le chiamate
in uscita verso l'Italia in *Voice → Settings → Geo permissions*; con l'account di
prova il numero dell'operatore deve essere tra i *Verified Caller IDs*.

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

Metti i manuali nella cartella `manuals/` (anche in sottocartelle):
- **PDF** con testo selezionabile: indicizzati pagina per pagina, ARGO cita "manuale, pag. N".
  Le scansioni (solo immagini) vanno prima passate in un OCR.
- **Markdown / testo**: i titoli (`#`, `##`) diventano le sezioni citate. Ottimi per
  procedure interne e FAQ scritte da voi.

Poi `git add manuals && git commit && git push`: Railway rifà il deploy e all'avvio il log
riporta quante sezioni ha caricato. I manuali di prova usati dai test automatici sono in `tests/fixtures/manuals/`.

**Attenzione:** se il repo è pubblico, i manuali sono visibili a chiunque. Per
documentazione interna o manuali dei produttori rendi il repo privato
(GitHub → *Settings → General → Danger Zone → Change visibility*).

## Pagina di presentazione e "Fatti chiamare"

`https://<dominio>/` è la pagina che presenta ARGO. In fondo c'è il modulo **Fatti chiamare**:
chi prova ARGO inserisce il proprio cellulare e il codice di accesso, e Twilio lo chiama dal
numero USA (per lui è una chiamata in arrivo, gratuita). Utile anche quando l'operatore
telefonico blocca le chiamate verso gli USA. Il vecchio indirizzo `/chiamami` porta qui.

Variabili: `TWILIO_VOICE_FROM` (il numero Twilio, es. `+16812562315`) e `CALLBACK_PASSWORD`
(il codice di accesso da dare a chi deve provare). Protezioni:
- solo cellulari italiani (`+39 3…`); altri numeri vanno elencati in `CALLBACK_NUMBERS`, separati da virgola;
- una chiamata ogni `CALLBACK_COOLDOWN_SECONDS` secondi per numero e al massimo `CALLBACK_MAX_PER_HOUR` in totale. Di default sono a `0` = nessun limite (fase di test): prima di condividere il codice di accesso impostali, ad esempio 180 e 20;
- ogni chiamata si chiude dopo 5 minuti.

## Manuale su WhatsApp durante la chiamata

Al telefono il tecnico può chiedere "mandamela su WhatsApp": ARGO invia al numero
chiamante l'intera sezione del manuale (o pagina del PDF) sull'argomento, via Twilio.

Variabili: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`
(per le prove il numero della **WhatsApp Sandbox** di Twilio, es. `whatsapp:+14155238886`).

Limiti di WhatsApp:
- **Sandbox**: ogni tecnico deve prima scrivere `join <parola-chiave>` al numero della
  sandbox (la trovi in *Messaging → Try it out → Send a WhatsApp message*); l'iscrizione
  scade dopo qualche giorno e va rinnovata.
- **In produzione** serve un mittente WhatsApp Business approvato. WhatsApp permette
  messaggi liberi solo entro 24 ore dall'ultimo messaggio del tecnico; altrimenti serve
  un modello (template) approvato da Meta.
- Chiamate con numero nascosto: l'invio non è possibile e ARGO lo dice al tecnico.

## Argomenti ammessi

ARGO risponde solo su temi tecnici (impianti di sicurezza, interventi, ticket). Alle
domande fuori tema risponde con una frase cortese e riporta il discorso sull'impianto;
ignora anche i tentativi di fargli cambiare ruolo o ignorare le istruzioni. Il vincolo
è nel prompt di sistema in `src/argo/assistant.py` (sezione "Argomenti ammessi").

## Prossimi passi

- Cronologia conversazioni persistente (oggi è in memoria e si perde al riavvio).
- Risposte in streaming o messaggio di attesa al telefono: Twilio attende al massimo
  ~15 secondi la risposta del webhook.
- Import di manuali PDF e ricerca vettoriale.
