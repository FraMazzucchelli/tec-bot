from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from argo.assistant import Assistant
from argo.config import Settings
from argo.knowledge import KnowledgeBase, Passage
from argo.main import create_app
from argo.security import twilio_signature
from argo.tickets import Ticket


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


class FakeClaude:
    """Restituisce in ordine le risposte preparate e registra le chiamate."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content, stop = self.responses.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop)


class FakeTickets:
    def __init__(self):
        self.created = []

    def create(self, summary, description, reporter_contact):
        self.created.append((summary, description, reporter_contact))
        return Ticket(key="ASS-1", url="https://x.atlassian.net/browse/ASS-1")


def kb():
    # manuali di prova (inventati), separati da quelli veri in manuals/
    return KnowledgeBase.from_dir(Path(__file__).parent / "fixtures" / "manuals")


def test_ricerca_manuali_trova_la_sezione_giusta():
    hits = kb().search("batteria tampone scarica")
    assert hits
    assert "Batteria" in hits[0].passage.title


def test_manuale_pdf(tmp_path):
    import pytest

    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(tmp_path / "centrale.pdf"))
    c.drawString(72, 800, "Codice errore E07: guasto linea seriale")
    c.showPage()
    c.drawString(72, 800, "Sirena esterna: verificare il fusibile F2")
    c.save()
    (tmp_path / "rotto.pdf").write_bytes(b"non un pdf")

    base = KnowledgeBase.from_dir(tmp_path)
    assert base.search("fusibile sirena")[0].passage.title == "centrale, pag. 2"


def test_ricerca_vuota_senza_risultati():
    assert KnowledgeBase([Passage("a", "b", "testo")]).search("zzz") == []


def test_risposta_con_ricerca_nei_manuali():
    claude = FakeClaude([
        ([tool_block("cerca_manuali", {"query": "batteria bassa"})], "tool_use"),
        ([text_block("Misura la batteria: deve superare 12,4 V.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), None)
    answer = assistant.reply("s1", "la centrale dice batteria bassa", "telegram")

    assert "12,4" in answer
    # la cronologia è condivisa: il risultato dello strumento è il penultimo messaggio
    tool_result = claude.calls[1]["messages"][-2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "12,4" in tool_result["content"]


def test_apertura_ticket():
    tickets = FakeTickets()
    claude = FakeClaude([
        ([tool_block("apri_ticket", {"titolo": "Sirena muta", "descrizione": "Cliente Rossi"})], "tool_use"),
        ([text_block("Ho aperto il ticket ASS-1.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), tickets)
    answer = assistant.reply("telegram:42", "apri un ticket", "telegram")

    assert "ASS-1" in answer
    assert tickets.created[0][0] == "Sirena muta"


def test_regole_confronti_nel_prompt():
    claude = FakeClaude([([text_block("Ok.")], "end_turn")])
    Assistant(claude, "m", kb(), None).reply("s", "differenza tra DT-400 e DT-800?", "voce")
    system = claude.calls[0]["system"]
    assert "Confronti tra modelli" in system and "Mai opinioni" in system
    assert "richiesta di consiglio" in system


def test_stile_voce_nel_prompt():
    claude = FakeClaude([([text_block("Ok.")], "end_turn")])
    Assistant(claude, "m", kb(), None).reply("s", "ciao", "voce")
    assert "telefono" in claude.calls[0]["system"]


TWILIO_TOKEN = "token-di-prova"
PUBLIC_URL = "https://argo.example.com"


def make_client(responses):
    claude = FakeClaude(responses)
    assistant = Assistant(claude, "m", kb(), None)
    settings = Settings(
        telegram_bot_token="x",
        telegram_webhook_secret="segreto",
        twilio_auth_token=TWILIO_TOKEN,
        public_base_url=PUBLIC_URL,
        skip_signature_check=False,
    )
    return TestClient(create_app(settings, assistant)), claude


def post_twilio(client, path, data, signature=None):
    """Simula Twilio: firma la richiesta come farebbe lui."""
    sig = signature or twilio_signature(TWILIO_TOKEN, PUBLIC_URL + path, data.items())
    return client.post(path, data=data, headers={"X-Twilio-Signature": sig})


def test_firma_twilio_esempio_calcolato_a_mano():
    import base64
    import hashlib
    import hmac

    # payload costruito a mano: URL + parametri ordinati per nome, chiave+valore
    payload = "https://x.it/voice/incoming" + "AccountSidAC1" + "CallSidCA1" + "From+39333"
    atteso = base64.b64encode(hmac.new(b"tok", payload.encode(), hashlib.sha1).digest()).decode()
    params = {"From": "+39333", "CallSid": "CA1", "AccountSid": "AC1"}.items()
    assert twilio_signature("tok", "https://x.it/voice/incoming", params) == atteso


def test_voce_chiamata_in_arrivo():
    client, _ = make_client([])
    r = post_twilio(client, "/voice/incoming", {"CallSid": "CA1", "From": "+39333"})
    assert r.status_code == 200
    assert "<Gather" in r.text and 'language="it-IT"' in r.text


def test_voce_firma_errata_rifiutata():
    client, claude = make_client([])
    r = post_twilio(client, "/voice/respond", {"CallSid": "CA1", "SpeechResult": "x"}, signature="falsa")
    assert r.status_code == 403
    assert claude.calls == []


def test_voce_senza_firma_rifiutata():
    client, _ = make_client([])
    assert client.post("/voice/incoming", data={"CallSid": "CA1"}).status_code == 403


def test_voce_parametri_manomessi_rifiutati():
    client, _ = make_client([])
    sig = twilio_signature(TWILIO_TOKEN, PUBLIC_URL + "/voice/respond", {"SpeechResult": "a"}.items())
    r = post_twilio(client, "/voice/respond", {"SpeechResult": "b"}, signature=sig)
    assert r.status_code == 403


def test_voce_risposta_e_escape_xml():
    client, _ = make_client([([text_block("Controlla zona <3> & morsetto")], "end_turn")])
    r = post_twilio(client, "/voice/respond", {"CallSid": "CA1", "SpeechResult": "sensore in allarme"})
    assert "&lt;3&gt; &amp; morsetto" in r.text


def test_whatsapp_risponde_in_twiml():
    client, _ = make_client([([text_block("Ciao tecnico")], "end_turn")])
    r = post_twilio(client, "/whatsapp/webhook", {"From": "whatsapp:+39333", "Body": "aiuto"})
    assert "<Message>Ciao tecnico</Message>" in r.text


def test_whatsapp_firma_errata_rifiutata():
    client, _ = make_client([])
    r = post_twilio(client, "/whatsapp/webhook", {"Body": "aiuto"}, signature="falsa")
    assert r.status_code == 403


def test_telegram_token_errato_rifiutato():
    client, claude = make_client([])
    update = {"message": {"text": "ciao", "chat": {"id": 1}}}
    r = client.post("/telegram/webhook", json=update,
                    headers={"X-Telegram-Bot-Api-Secret-Token": "sbagliato"})
    assert r.status_code == 403
    assert claude.calls == []


def test_telegram_token_corretto_accettato():
    client, _ = make_client([])
    r = client.post("/telegram/webhook", json={"message": {}},
                    headers={"X-Telegram-Bot-Api-Secret-Token": "segreto"})
    assert r.status_code == 200


OPERATORE = "+390000000000"  # numero finto per i test


def test_operatore_in_chat_restituisce_numero():
    claude = FakeClaude([
        ([tool_block("passa_a_operatore", {"motivo": "chiede una persona"})], "tool_use"),
        ([text_block("Chiama il collega.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), None, OPERATORE)
    assistant.reply("telegram:1", "voglio parlare con una persona", "telegram")
    risultato = claude.calls[1]["messages"][-2]["content"][0]["content"]
    assert OPERATORE in risultato
    assert assistant.take_handoff("telegram:1") is False


def test_operatore_non_configurato():
    claude = FakeClaude([
        ([tool_block("passa_a_operatore", {"motivo": "x"})], "tool_use"),
        ([text_block("Apro un ticket?")], "end_turn"),
    ])
    Assistant(claude, "m", kb(), None, "").reply("s", "persona", "voce")
    assert "Nessun operatore" in claude.calls[1]["messages"][-2]["content"][0]["content"]


def make_voice_client(responses, numero=OPERATORE):
    claude = FakeClaude(responses)
    assistant = Assistant(claude, "m", kb(), None, numero)
    settings = Settings(
        twilio_auth_token=TWILIO_TOKEN,
        public_base_url=PUBLIC_URL,
        human_support_number=numero,
    )
    return TestClient(create_app(settings, assistant))


def test_voce_trasferisce_all_operatore():
    client = make_voice_client([
        ([tool_block("passa_a_operatore", {"motivo": "urgente"})], "tool_use"),
        ([text_block("Ti passo un collega.")], "end_turn"),
    ])
    r = post_twilio(client, "/voice/respond", {"CallSid": "CA9", "SpeechResult": "operatore"})
    assert f">{OPERATORE}</Dial>" in r.text
    assert "Ti passo un collega." in r.text
    assert "<Gather" not in r.text


def test_voce_errore_claude_trasferisce():
    class Rotto:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            raise RuntimeError("API giù")

    assistant = Assistant(Rotto(), "m", kb(), None, OPERATORE)
    settings = Settings(twilio_auth_token=TWILIO_TOKEN, public_base_url=PUBLIC_URL,
                        human_support_number=OPERATORE)
    client = TestClient(create_app(settings, assistant))
    r = post_twilio(client, "/voice/respond", {"CallSid": "CA1", "SpeechResult": "aiuto"})
    assert "<Dial" in r.text


def test_dial_status():
    client = make_voice_client([])
    assert "<Say" not in post_twilio(client, "/voice/dial-status", {"DialCallStatus": "completed"}).text
    assert "non è raggiungibile" in post_twilio(client, "/voice/dial-status", {"DialCallStatus": "no-answer"}).text


class FakeMessenger:
    def __init__(self):
        self.sent = []

    def send_whatsapp(self, to_number, body):
        self.sent.append((to_number, body))


def test_invio_manuale_whatsapp_sezione_intera():
    messenger = FakeMessenger()
    claude = FakeClaude([
        ([tool_block("invia_manuale_whatsapp", {"argomento": "cartuccia Spora Placida inserimento"})], "tool_use"),
        ([text_block("Te l'ho mandata.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), None, "", messenger)
    assistant.reply("voce:CA1", "mandamela su whatsapp", "voce", phone="+393331112222")

    to, body = messenger.sent[0]
    assert to == "+393331112222"
    assert "3.4 Inserimento della cartuccia" in body
    assert "Ruotare la ghiera" in body and "3 attivazioni" in body  # entrambi i paragrafi


def test_invio_whatsapp_testo_rielaborato():
    messenger = FakeMessenger()
    testo = "*Nebbiogeno DT-800*\n• Riscaldamento da freddo: max 12 minuti\nFonte: scheda tecnica Serie LAN"
    claude = FakeClaude([
        ([tool_block("invia_manuale_whatsapp", {"testo": testo})], "tool_use"),
        ([text_block("Te l'ho mandato.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), None, "", messenger, "AI ASSISTANT di TEKGROUP")
    assistant.reply("voce:CA1", "mandamelo su whatsapp", "voce", phone="+393331112222")
    to, body = messenger.sent[0]
    assert to == "+393331112222"
    assert body.startswith("📘 *AI ASSISTANT di TEKGROUP*") and testo in body
    assert "|" not in body


def test_invio_manuale_chiamata_anonima():
    messenger = FakeMessenger()
    claude = FakeClaude([
        ([tool_block("invia_manuale_whatsapp", {"argomento": "ugello"})], "tool_use"),
        ([text_block("ok")], "end_turn"),
    ])
    Assistant(claude, "m", kb(), None, "", messenger).reply("voce:CA2", "x", "voce", phone="anonymous")
    assert messenger.sent == []


def test_split_message():
    from argo.messaging import split_message

    parts = split_message("riga\n" * 1000)
    assert len(parts) > 1 and all(len(p) <= 1500 for p in parts)
    assert "".join(split_message("x" * 4000)) == "x" * 4000


def make_callback_client(**extra):
    settings = Settings(
        twilio_auth_token=TWILIO_TOKEN,
        twilio_account_sid="AC123",
        twilio_voice_from="+15550001111",
        public_base_url=PUBLIC_URL,
        human_support_number=OPERATORE,
        callback_password="segreta",
        **extra,
    )
    return TestClient(create_app(settings, Assistant(FakeClaude([]), "m", kb(), None)))


def test_pagina_di_presentazione():
    client = make_callback_client()
    r = client.get("/")
    assert r.status_code == 200 and "Fatti chiamare da ARGO" in r.text
    assert client.get("/chiamami", follow_redirects=False).headers["location"] == "/#chiamami"


def test_normalizzazione_numero():
    from argo.channels.callback import normalize_number

    assert normalize_number("333 123 4567") == "+393331234567"
    assert normalize_number("0039 333-123-4567") == "+393331234567"
    assert normalize_number("+39 333 1234567") == "+393331234567"


def esito(client, data):
    """Invia il modulo e restituisce il codice di esito del redirect (Post/Redirect/Get)."""
    from urllib.parse import parse_qs, urlparse

    r = client.post("/chiamami", data=data, follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.endswith("#chiamami") and "333" not in location  # niente numero nell'URL
    return parse_qs(urlparse(location).query)["esito"][0]


def test_chiamami_codice_errato(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest_fail())
    assert esito(make_callback_client(), {"numero": "3331234567", "codice": "no"}) == "codice"


def test_chiamami_solo_cellulari_italiani(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest_fail())
    client = make_callback_client()
    for numero in ["+44 7700 900123", "051 123456", "+39 899 123456", "abc"]:
        assert esito(client, {"numero": numero, "codice": "segreta"}) == "numero", numero


def fake_twilio(monkeypatch):
    import httpx

    chiamate = []

    def finto_post(url, data, auth, timeout):
        chiamate.append((url, data))
        return httpx.Response(201, json={"sid": "CA1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", finto_post)
    return chiamate


def test_chiamami_avvia_chiamata(monkeypatch):
    chiamate = fake_twilio(monkeypatch)
    client = make_callback_client()
    r = client.post("/chiamami", data={"numero": "333 123 4567", "codice": "segreta"})
    assert "Ti sto chiamando" in r.text  # dopo il redirect
    url, data = chiamate[0]
    assert url.endswith("/Accounts/AC123/Calls.json")
    assert data["To"] == "+393331234567" and data["From"] == "+15550001111"
    assert data["Url"] == PUBLIC_URL + "/voice/incoming"
    assert data["TimeLimit"] == "300"


def test_ricaricare_la_pagina_non_richiama(monkeypatch):
    chiamate = fake_twilio(monkeypatch)
    client = make_callback_client()
    assert esito(client, {"numero": "3331234567", "codice": "segreta"}) == "chiamata"
    for _ in range(3):  # refresh della pagina di esito = GET, nessuna nuova chiamata
        assert "Ti sto chiamando" in client.get("/?esito=chiamata").text
    assert len(chiamate) == 1


def test_chiamami_limiti(monkeypatch):
    chiamate = fake_twilio(monkeypatch)
    client = make_callback_client(callback_max_per_hour=2, callback_cooldown_seconds=180)
    ok = {"codice": "segreta"}
    assert esito(client, {**ok, "numero": "3331111111"}) == "chiamata"
    assert esito(client, {**ok, "numero": "3331111111"}) == "attendi"
    assert esito(client, {**ok, "numero": "3332222222"}) == "chiamata"
    assert esito(client, {**ok, "numero": "3333333333"}) == "limite"
    assert len(chiamate) == 2


def test_chiamami_numero_extra_autorizzato(monkeypatch):
    chiamate = fake_twilio(monkeypatch)
    client = make_callback_client(callback_numbers="+44 7700 900123")
    assert esito(client, {"numero": "+447700900123", "codice": "segreta"}) == "chiamata"
    assert chiamate[0][1]["To"] == "+447700900123"


def test_chiamami_senza_codice(monkeypatch):
    chiamate = fake_twilio(monkeypatch)
    client = make_callback_client(callback_password="")
    assert 'name="codice"' not in client.get("/").text
    assert esito(client, {"numero": "333 123 4567"}) == "chiamata"
    assert chiamate[0][1]["To"] == "+393331234567"


def test_chiamami_disattivato(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest_fail())
    client = make_callback_client(callback_password="", callback_enabled=False)
    assert esito(client, {"numero": "3331234567"}) == "disattivato"


def test_esito_sconosciuto_ignorato():
    html = make_callback_client().get("/?esito=<script>").text
    assert "<script>" not in html and 'class="msg' not in html


def test_chiamami_senza_limiti_di_default(monkeypatch):
    chiamate = fake_twilio(monkeypatch)
    client = make_callback_client()
    for _ in range(30):
        assert esito(client, {"numero": "3331111111", "codice": "segreta"}) == "chiamata"
    assert len(chiamate) == 30


def test_limiter_scade():
    from argo.channels.callback import CallLimiter

    limiter = CallLimiter(per_number_seconds=180, max_per_hour=1)
    limiter.record("+39333", now=0)
    assert limiter.check("+39333", now=100) is not None
    assert limiter.check("+39333", now=3601) is None


def test_chiamata_in_uscita_usa_il_destinatario():
    messenger = FakeMessenger()
    claude = FakeClaude([
        ([tool_block("invia_manuale_whatsapp", {"argomento": "pulizia ugello"})], "tool_use"),
        ([text_block("Inviata.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), None, "", messenger)
    settings = Settings(twilio_auth_token=TWILIO_TOKEN, public_base_url=PUBLIC_URL)
    client = TestClient(create_app(settings, assistant))
    post_twilio(client, "/voice/respond", {
        "CallSid": "CA7", "From": "+15550001111", "To": "+393330000000",
        "Direction": "outbound-api", "SpeechResult": "mandamelo su whatsapp",
    })
    assert messenger.sent[0][0] == "+393330000000"


def pytest_fail():
    raise AssertionError("Twilio non doveva essere chiamato")


def test_health():
    client, _ = make_client([])
    assert client.get("/health").json()["status"] == "ok"


# --- privacy / GDPR ---


def test_numeri_mascherati_nei_log():
    import logging

    from argo.logging_privacy import MaskingFormatter, mask_phones

    assert mask_phones("To +393331234567 non valido") == "To +39******* non valido"
    record = logging.LogRecord("x", logging.ERROR, "f", 1, "sessione whatsapp:%s", ("+393331234567",), None)
    assert "1234567" not in MaskingFormatter("%(message)s").format(record)


def test_conversazioni_inattive_cancellate():
    import argo.assistant as mod

    claude = FakeClaude([([text_block("ok")], "end_turn")] * 2)
    assistant = Assistant(claude, "m", kb(), None)
    assistant.reply("vecchia", "ciao", "telegram", phone="+39333")
    assistant._last_seen["vecchia"] -= mod.SESSION_TTL_SECONDS + 1
    assistant.reply("nuova", "ciao", "telegram")
    assert "vecchia" not in assistant._history and "vecchia" not in assistant._phones


def test_pagina_senza_risorse_esterne_e_con_informativa():
    client = make_callback_client(privacy_policy_url="https://example.com/privacy", company_name="ACME")
    html = client.get("/").text
    assert "googleapis" not in html and "gstatic" not in html
    assert "usato da ACME solo per questa chiamata" in html
    assert 'href="https://example.com/privacy"' in html


def test_saluto_dichiara_assistente_automatico():
    client, _ = make_client([])
    r = post_twilio(client, "/voice/incoming", {"CallSid": "CA1", "From": "+39333"})
    assert "assistente automatico" in r.text
