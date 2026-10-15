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
- `src/argo/channels/relay.py` – websocket per Twilio ConversationRelay (`VOICE_MODE=relay`).
- `src/argo/channels/chat.py` – webhook Telegram e WhatsApp.
- `src/argo/channels/web.py` – chat sul sito (modalità informazioni e prova sui manuali).
- `src/argo/info.py` + `info/argo.md` – assistente informativo per le aziende interessate.

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
(codice di accesso; se vuota il modulo è aperto a tutti, `CALLBACK_ENABLED=false` lo disattiva). Protezioni:
- solo cellulari italiani (`+39 3…`);
- una chiamata ogni `CALLBACK_COOLDOWN_SECONDS` secondi per numero e al massimo `CALLBACK_MAX_PER_HOUR` in totale. Di default sono a `0` = nessun limite (fase di test): prima di condividere il codice di accesso impostali, ad esempio 180 e 20;
- ogni chiamata si chiude dopo 5 minuti.

## Manuale su WhatsApp durante la chiamata

Al telefono il tecnico può chiedere "mandamela su WhatsApp": ARGO invia al numero
chiamante l'intera sezione del manuale (o pagina del PDF) sull'argomento, via Twilio.

Variabili: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`
(per le prove il numero della **WhatsApp Sandbox** di Twilio, `whatsapp:+14155238886`). La sandbox si attiva solo dalla console legacy: https://www.twilio.com/console/sms/whatsapp/sandbox. Invia al massimo un messaggio ogni 3 secondi: i testi lunghi partono a pezzi distanziati.

Limiti di WhatsApp:
- **Sandbox**: ogni tecnico deve prima scrivere `join <parola-chiave>` al numero della
  sandbox (la trovi in *Messaging → Try it out → Send a WhatsApp message*); l'iscrizione
  scade dopo qualche giorno e va rinnovata.
- **In produzione** serve un mittente WhatsApp Business approvato. WhatsApp permette
  messaggi liberi solo entro 24 ore dall'ultimo messaggio del tecnico; altrimenti serve
  un modello (template) approvato da Meta.
- Chiamate con numero nascosto: l'invio non è possibile e ARGO lo dice al tecnico.

## Privacy (GDPR)

- Numeri di telefono mascherati nei log (`logging_privacy.py`).
- Conversazioni in memoria cancellate dopo 30 minuti di inattività.
- Pagina web senza risorse di terzi (niente Google Fonts, nessun cookie), con informativa breve
  sotto il modulo; `PRIVACY_POLICY_URL` aggiunge il link all'informativa completa del titolare.
- Il saluto dichiara che risponde un assistente automatico (AI Act).
- L'assistente non chiede né scrive nei ticket dati personali non necessari.

## Argomenti ammessi

ARGO risponde solo su temi tecnici (impianti di sicurezza, interventi, ticket). Alle
domande fuori tema risponde con una frase cortese e riporta il discorso sull'impianto;
ignora anche i tentativi di fargli cambiare ruolo o ignorare le istruzioni. Il vincolo
è nel prompt di sistema in `src/argo/assistant.py` (sezione "Argomenti ammessi").

## Chiamate con ConversationRelay (voce fluida)

Con `VOICE_MODE=relay` le chiamate passano da Twilio **ConversationRelay** invece che da
`<Gather>`/`<Say>`: il riconoscimento vocale e la voce sono in streaming, il tecnico può
interrompere ARGO e non c'è più il limite di ~15 secondi per la risposta. Se Claude ci
mette più di 1,5 secondi (es. ricerca nei manuali) ARGO dice "Un attimo, controllo."

1. Twilio → *Voice → Settings → Privacy & Security*: accetta il
   *Predictive and Generative AI/ML Features Addendum* e salva.
2. Imposta `VOICE_MODE=relay` sul server. Il numero resta configurato su
   `POST {URL}/voice/incoming`: il resto lo decide ARGO.
3. Voce: `RELAY_TTS_PROVIDER` (`Amazon`, `Google`, `ElevenLabs`) e `RELAY_VOICE`.
   Predefinito `Amazon` + `Bianca-Neural`, la stessa voce di oggi; con `ElevenLabs`
   metti in `RELAY_VOICE` l'ID di una voce italiana della libreria ElevenLabs.
4. `RELAY_HINTS`: parole che il riconoscimento deve capire bene (nomi dei modelli).
5. Meno pause dopo che il tecnico smette di parlare: `RELAY_SPEECH_MODEL` (es. un modello
   Deepgram `flux`) e `RELAY_EOT_THRESHOLD` (0.5–0.9, più basso = risponde prima).
   Vuoti = valori predefiniti di Twilio.

La risposta di Claude arriva a Twilio **in streaming**: la voce parte con la prima
frase, anche prima della ricerca nei manuali ("Controllo la scheda…"). Se il tecnico
parla sopra ARGO, il resto della risposta non viene detto. Il prompt di sistema è in
**cache** (prompt caching di Anthropic), così ogni turno parte più velocemente.
Per tempi di risposta ancora più brevi si può provare un modello più veloce con
`CLAUDE_MODEL` (es. `claude-haiku-5-5`).

Flusso: `/voice/incoming` risponde con `<Connect><ConversationRelay url="wss://…/voice/relay">`;
il websocket `/voice/relay` (firma `X-Twilio-Signature` verificata) riceve le frasi del
tecnico e risponde con il testo di Claude. Per passare all'operatore o salutare ARGO
chiude la sessione e Twilio chiama `/voice/relay-fine`, che fa `<Dial>` o `<Hangup>`.
Per tornare al vecchio flusso basta `VOICE_MODE=gather`.

## App ARGO (PWA)

All'indirizzo `/app` c'è una web app installabile sul telefono ("Aggiungi alla schermata
Home"): un pulsante per **parlare con ARGO via internet**, senza chiamare un numero.
Il tecnico sceglie tra due voci:

- **Voce del telefono (gratis)** – riconoscimento e sintesi vocale li fa il browser
  (Chrome o Safari); ad ARGO arriva solo il testo (`POST /app/messaggio`), con lo stesso
  comportamento del telefono: risposte brevi, manuali, ticket, WhatsApp. Il collega si
  chiama con un pulsante che compare nell'app. **Nessun costo Twilio e nessun numero
  di telefono**: è il modo consigliato per sviluppare e provare. Non serve configurare nulla.
  Limiti: voce del telefono meno naturale, non si può interrompere ARGO mentre parla.
- **Voce Twilio** – chiamata vera dal browser con il Twilio Voice SDK, stesso flusso del
  numero (ConversationRelay se attivo). Si attiva configurando le variabili qui sotto;
  finché mancano, nell'app compare "non configurata".

Configurazione su Twilio per la voce Twilio, una volta sola:
1. *Account → API keys & tokens → Create API key* (tipo Standard): copia SID (`SK…`)
   e secret in `TWILIO_API_KEY` e `TWILIO_API_SECRET`. Il secret si vede solo una volta.
2. *Voice → TwiML Apps → Create new TwiML App*: Voice Request URL
   `{PUBLIC_BASE_URL}/voice/incoming`, metodo POST. Copia il SID (`AP…`) in
   `TWILIO_TWIML_APP_SID`.
3. Per passare a un collega dall'app serve che le chiamate verso l'Italia siano abilitate
   (*Voice → Settings → Geo permissions*), come per il numero.

Se è impostato `CALLBACK_PASSWORD`, l'app chiede lo stesso codice (lo ricorda sul
telefono). `APP_MAX_CALLS_PER_HOUR` limita le chiamate dall'app (predefinito 30, `0` = nessun limite).
Il tecnico può indicare nell'app il proprio cellulare (facoltativo, resta salvato solo sul
suo telefono): arriva a Twilio come parametro `Telefono` della chiamata e ARGO lo usa,
solo per quella chiamata, per mandare le schede su WhatsApp. Va bene qualsiasi numero:
senza prefisso si intende italiano (`333 123 4567`), gli esteri con il prefisso (`+44 …`).
Senza numero ARGO dice che non può inviare.

## Chat sul sito

La pagina iniziale ha una sezione **Chiedi ad ARGO** con due modalità:

- **Informazioni sul servizio** – per le aziende interessate: cosa fa ARGO, cosa serve,
  come si attiva, come funzionano i prezzi. Risponde **solo** dal file `info/argo.md`
  (variabile `INFO_FILE`): aggiorna lì prezzi e condizioni, senza toccare il codice.
  Le parti tra `[ ]` sono considerate "da definire" e l'assistente non le legge.
  Se il visitatore vuole un preventivo o una demo, dopo il suo consenso lo strumento
  `lascia_contatto` apre su Jira un ticket `[Contatto commerciale] <azienda>`.
- **Prova sui manuali** – la stessa ARGO del telefono, ma può solo cercare nei manuali:
  niente ticket, operatore o WhatsApp, perché la pagina è pubblica. Se è impostato
  `CALLBACK_PASSWORD` chiede lo stesso codice di accesso del modulo "Fatti chiamare".

Endpoint: `POST /chat/web` con JSON `{"modo": "info"|"prova", "sessione", "testo", "codice"}`,
risposta `{"risposta": ...}` oppure `{"errore": ..., "codice": ...}`.

Ogni messaggio è una chiamata a Claude, quindi ci sono dei limiti (in memoria, per un solo server):

| Variabile | Predefinito | Significato |
|---|---|---|
| `WEB_CHAT_ENABLED` | `true` | `false` disattiva la chat |
| `WEB_CHAT_MAX_CHARS` | `800` | lunghezza massima di un messaggio |
| `WEB_CHAT_MAX_MESSAGES` | `30` | messaggi per conversazione |
| `WEB_CHAT_MAX_PER_HOUR` | `300` | messaggi all'ora in totale, `0` = nessun limite |

## Prossimi passi

- Cronologia conversazioni persistente (oggi è in memoria e si perde al riavvio).
- Risposte in streaming o messaggio di attesa al telefono: Twilio attende al massimo
  ~15 secondi la risposta del webhook.
- Import di manuali PDF e ricerca vettoriale.
