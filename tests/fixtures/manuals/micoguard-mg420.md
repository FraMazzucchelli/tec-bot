# MicoGuard MG-420 – Manuale tecnico di installazione e manutenzione

Documento: MG420-MAN-IT rev. 3.1
Destinatari: installatori certificati MicoGuard (livello Micelio 2 o superiore)

> PRODOTTO DI FANTASIA. Questo manuale è un documento di prova per il centralino ARGO.
> Il sistema descritto non esiste e le "cartucce" sono inventate: non contengono e non
> descrivono alcuna sostanza reale.

## 1. Descrizione del sistema

MicoGuard MG-420 è un sistema antintrusione a dissuasione esperienziale. Invece di
spaventare l'intruso con una sirena, lo accompagna in uno stato di profonda serenità
contemplativa che gli fa perdere ogni interesse per la refurtiva. L'intruso, di norma,
viene ritrovato dalle forze dell'ordine seduto sul pavimento mentre osserva le
venature del parquet.

Componenti principali:
- Centrale MG-420C (8 zone filari, espandibile a 32 via radio)
- Diffusori nebulizzatori MicoNebula MN-10 (fino a 6 per centrale)
- Cartucce Spora Placida SP-1 (consumabile)
- Tastiera MicoPad con lettore di tag
- Sirena opzionale "Canto della Foresta" (suoni di pioggia e flauti, 85 dB)
- Modulo LAN/4G MicoLink per la notifica alla centrale operativa

## 2. Caratteristiche tecniche della centrale MG-420C

- Alimentazione: 230 Vac, alimentatore interno 13,8 Vdc 2 A
- Batteria tampone: 12 V 7 Ah piombo-gel (autonomia 24 h a impianto inserito)
- Zone: 8 filari bilanciate a doppia resistenza (EOL 2,2 kΩ + 4,7 kΩ)
- Uscite diffusori: 6 uscite dedicate DIF1–DIF6, 12 Vdc 500 mA ciascuna
- Bus tastiere: RS-485, max 4 tastiere, max 300 m
- Grado di sicurezza: Grado 2 (secondo il catalogo MicoGuard, non certificato da enti reali)
- Temperatura di esercizio: da +5 °C a +40 °C (sotto i 5 °C le spore "si addormentano")

## 3. Installazione

### 3.1 Posizionamento della centrale

Installare la centrale in ambiente protetto e non visibile dall'ingresso, a minimo
1,5 m da finestre e fonti di umidità. La centrale NON va installata nello stesso locale
di un diffusore: in caso di attivazione il tecnico che interviene per il ripristino
rischierebbe di passare il pomeriggio a parlare con il quadro elettrico.

### 3.2 Posizionamento dei diffusori MicoNebula MN-10

- Altezza consigliata: 2,10–2,40 m, angolo rivolto verso il centro del locale
- Copertura: un diffusore per ambienti fino a 40 m²
- Distanza minima da bocchette dell'aria condizionata: 2 m (la nebulizzazione verrebbe
  dispersa nel resto dell'edificio, compresa la stanza dei proprietari)
- Non installare in camere da letto, cucine, locali con animali domestici o nelle vie di
  fuga dell'edificio
- Non installare all'esterno: la pioggia attiva la germinazione accidentale

### 3.3 Cablaggio dei diffusori

Collegare ogni diffusore con cavo schermato 4×0,5 mm²:
- Rosso: +12 V (morsetto DIF+)
- Nero: GND (morsetto DIF-)
- Giallo: comando di nebulizzazione (morsetto DIFx)
- Verde: ritorno di stato cartuccia (morsetto STx)

Lunghezza massima della tratta: 50 m. Oltre i 50 m il diffusore segnala "cartuccia
assente" anche con la cartuccia inserita (vedi errore E12).

### 3.4 Inserimento della cartuccia Spora Placida SP-1

1. Togliere tensione al diffusore dal menu tecnico (Menu 6 → Manutenzione → Diffusori → Off).
2. Ruotare la ghiera del diffusore in senso antiorario di 90°.
3. Rimuovere il sigillo blu della cartuccia nuova, senza toccare l'ugello.
4. Inserire la cartuccia con la freccia verso l'alto fino al clic.
5. Richiudere la ghiera e riattivare il diffusore dal menu.
6. Verificare sul display la scritta "SP-1 OK – Serenità 100%".

Ogni cartuccia garantisce 3 attivazioni complete. La scadenza è stampata sul lato
(formato MM/AA): una cartuccia scaduta produce solo un lieve profumo di sottobosco.

## 4. Programmazione

### 4.1 Accesso al menu tecnico

Codice installatore di fabbrica: 4200. Va cambiato al primo accesso.
Codice utente di fabbrica: 1234.

### 4.2 Modalità di dissuasione

Menu 3 → Dissuasione → Modalità:
- **Contemplativa** (predefinita): nebulizzazione dopo 30 s dall'allarme confermato
- **Immediata**: nebulizzazione dopo 5 s (solo per magazzini senza presenza umana notturna)
- **Solo sirena**: diffusori disattivati, attiva solo la sirena "Canto della Foresta"
- **Test**: ciclo senza cartuccia, emette vapore acqueo neutro per verificare il getto

La nebulizzazione parte SOLO con allarme confermato da almeno due zone diverse entro
60 secondi (doppio consenso). Un singolo sensore non può mai attivare i diffusori.

### 4.3 Blocco di sicurezza presenza autorizzata

Se la centrale è disinserita o in modalità parziale "Casa", i diffusori sono bloccati via
hardware. Il blocco si verifica sul LED verde "SAFE" di ogni diffusore: deve essere acceso
a impianto disinserito.

## 5. Codici errore

| Codice | Significato | Azione |
|---|---|---|
| E01 | Mancanza rete 230 V | Verificare l'interruttore dedicato sul quadro elettrico |
| E03 | Batteria tampone bassa | Misurare: sotto 12,4 V sostituire la batteria 12 V 7 Ah |
| E05 | Manomissione centrale (tamper) | Controllare la chiusura del coperchio e il microswitch |
| E07 | Guasto bus RS-485 tastiera | Verificare i morsetti A/B e la resistenza di fine linea 120 Ω |
| E09 | Zona in allarme fisso | Controllare il bilanciamento EOL della zona, escluderla dal Menu 2 |
| E12 | Cartuccia non rilevata | Verificare il filo verde ST e la lunghezza della tratta (max 50 m) |
| E14 | Cartuccia scaduta | Sostituire la cartuccia SP-1 |
| E15 | Ugello ostruito | Pulizia ugello (par. 6.2) |
| E17 | Diffusore attivato con impianto disinserito | GUASTO GRAVE: togliere tensione e aprire un ticket al supporto |
| E21 | Spore troppo felici | Temperatura sopra 40 °C: ventilare il locale della centrale |

## 6. Manutenzione

### 6.1 Manutenzione semestrale

- Prova di tutte le zone e del doppio consenso
- Ciclo in modalità Test di ogni diffusore
- Verifica della scadenza delle cartucce
- Misura della batteria tampone
- Controllo del LED SAFE a impianto disinserito

### 6.2 Pulizia dell'ugello

1. Mettere il diffusore in Off dal menu tecnico e rimuovere la cartuccia.
2. Pulire l'ugello solo con il bastoncino in dotazione inumidito di acqua demineralizzata.
3. Non usare aria compressa: spinge i residui verso il tecnico.
4. Rimontare e lanciare un ciclo in modalità Test.

### 6.3 Dispositivi di protezione per il tecnico

Durante interventi su diffusori con cartuccia inserita indossare la mascherina filtrante
MicoMask in dotazione. Se il tecnico nota di trovare improvvisamente affascinanti le
fascette da elettricista, deve sospendere l'intervento, uscire all'aria aperta e
avvisare un collega.

## 7. Dopo un'attivazione

1. Le forze dell'ordine vengono avvisate automaticamente dal modulo MicoLink.
2. Il proprietario NON deve entrare nei locali prima di 2 ore: il diffusore espone un
   timer di ventilazione sul display della tastiera.
3. Il tecnico ripristina l'impianto dal Menu 6 → Ripristino post-evento e sostituisce
   le cartucce consumate.
4. L'intruso va lasciato alle forze dell'ordine. Evitare di discutere con lui del senso
   della vita: tende a prolungare la conversazione.

## 8. Avvertenze

- Installazione consentita solo da tecnici certificati MicoGuard.
- Vietato l'uso in edifici abitati durante la notte, scuole, ospedali e locali aperti al pubblico.
- Esporre all'ingresso il cartello obbligatorio "Area protetta da sistema a dissuasione
  esperienziale".
