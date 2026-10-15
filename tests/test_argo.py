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
        if isinstance(kwargs.get("system"), list):  # prompt in cache: lo riportiamo a testo
            kwargs = {**kwargs, "system": "".join(b["text"] for b in kwargs["system"])}
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


def test_chiamami_rifiuta_numeri_esteri(monkeypatch):
    chiamate = fake_twilio(monkeypatch)
    client = make_callback_client()
    assert esito(client, {"numero": "+447700900123", "codice": "segreta"}) == "numero"
    assert chiamate == []


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


# --- Chat sul sito -----------------------------------------------------------

from argo.channels.web import WebChatLimiter  # noqa: E402
from argo.info import InfoAssistant, load_info  # noqa: E402

SESSIONE = "abcdef0123456789abcdef"


def web_client(claude=None, info_claude=None, tickets=None, **settings_kwargs):
    settings = Settings(_env_file=None, **settings_kwargs)
    assistant = Assistant(claude or FakeClaude([]), "m", kb(), tickets)
    info = InfoAssistant(info_claude or FakeClaude([]), "m", "ARGO costa X al mese.", tickets)
    return TestClient(create_app(settings, assistant, info))


def test_chat_info_risponde_dal_documento():
    info_claude = FakeClaude([([text_block("ARGO risponde dai manuali.")], "end_turn")])
    client = web_client(info_claude=info_claude)
    r = client.post("/chat/web", json={"modo": "info", "sessione": SESSIONE, "testo": "Cosa fa?"})
    assert r.status_code == 200
    assert r.json() == {"risposta": "ARGO risponde dai manuali."}
    call = info_claude.calls[0]
    assert "ARGO costa X al mese." in call["system"]
    assert [t["name"] for t in call["tools"]] == ["lascia_contatto"]


def test_chat_info_lascia_contatto_apre_ticket():
    tickets = FakeTickets()
    info_claude = FakeClaude([
        ([tool_block("lascia_contatto", {
            "nome": "Mario Rossi", "azienda": "Impianti Srl",
            "contatto": "mario@impianti.it", "richiesta": "demo per 12 tecnici",
        })], "tool_use"),
        ([text_block("Fatto, ti ricontattiamo.")], "end_turn"),
    ])
    client = web_client(info_claude=info_claude, tickets=tickets)
    r = client.post("/chat/web", json={"modo": "info", "sessione": SESSIONE, "testo": "Sì, va bene"})
    assert r.json()["risposta"] == "Fatto, ti ricontattiamo."
    summary, description, contact = tickets.created[0]
    assert summary == "[Contatto commerciale] Impianti Srl"
    assert "demo per 12 tecnici" in description and contact == "mario@impianti.it"


def test_chat_prova_solo_ricerca_manuali():
    tickets = FakeTickets()
    claude = FakeClaude([
        ([tool_block("apri_ticket", {"titolo": "x", "descrizione": "y"})], "tool_use"),
        ([text_block("Nella prova non apro ticket.")], "end_turn"),
    ])
    client = web_client(claude=claude, tickets=tickets)
    r = client.post("/chat/web", json={"modo": "prova", "sessione": SESSIONE, "testo": "Apri un ticket"})
    assert r.json()["risposta"] == "Nella prova non apro ticket."
    assert [t["name"] for t in claude.calls[0]["tools"]] == ["cerca_manuali"]
    assert "prova pubblica" in claude.calls[0]["system"]
    assert tickets.created == []  # lo strumento non disponibile non viene eseguito


def test_chat_prova_richiede_codice():
    claude = FakeClaude([([text_block("ok")], "end_turn")])
    client = web_client(claude=claude, callback_password="segreto")
    body = {"modo": "prova", "sessione": SESSIONE, "testo": "ciao"}
    assert client.post("/chat/web", json=body).status_code == 403
    assert client.post("/chat/web", json={**body, "codice": "segreto"}).status_code == 200
    # la modalità informazioni resta aperta (e non consuma la risposta preparata)
    info_claude = FakeClaude([([text_block("info")], "end_turn")])
    client = web_client(info_claude=info_claude, callback_password="segreto")
    assert client.post("/chat/web", json={**body, "modo": "info"}).status_code == 200


def test_chat_valida_richiesta():
    client = web_client(web_chat_max_chars=10)
    post = lambda **b: client.post("/chat/web", json={"modo": "info", "sessione": SESSIONE, "testo": "ciao", **b})
    assert post(modo="altro").status_code == 400
    assert post(sessione="corta").status_code == 400
    assert post(testo="   ").status_code == 400
    assert post(testo="x" * 11).status_code == 400
    assert web_client(web_chat_enabled=False).post(
        "/chat/web", json={"modo": "info", "sessione": SESSIONE, "testo": "ciao"}
    ).status_code == 403


def test_chat_limiti():
    limiter = WebChatLimiter(max_per_hour=3, max_per_session=2)
    for _ in range(2):
        assert limiter.check("a", now=0) is None
        limiter.record("a", now=0)
    assert limiter.check("a", now=10) == "conversazione"
    assert limiter.check("b", now=10) is None
    limiter.record("b", now=10)
    assert limiter.check("c", now=20) == "limite"
    assert limiter.check("c", now=3700) is None  # dopo un'ora si riparte


def test_chat_errore_claude():
    client = web_client()
    client.app.state.info_assistant.client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(RuntimeError("giù")))
    )
    r = client.post("/chat/web", json={"modo": "info", "sessione": SESSIONE, "testo": "ciao"})
    assert r.status_code == 502


def test_chat_nuova_conversazione_cancella_cronologia():
    info_claude = FakeClaude([([text_block("ok")], "end_turn")])
    client = web_client(info_claude=info_claude)
    client.post("/chat/web", json={"modo": "info", "sessione": SESSIONE, "testo": "ciao"})
    info = client.app.state.info_assistant
    assert info._history
    client.post("/chat/web/nuova", json={"modo": "info", "sessione": SESSIONE})
    assert not info._history


def test_landing_ha_la_chat():
    html = web_client().get("/").text
    assert 'id="chat-log"' in html and "{{" not in html
    html = web_client(callback_password="x").get("/").text
    assert 'id="chat-codice"' in html


def test_file_informazioni(tmp_path):
    assert "ARGO" in load_info(tmp_path / "manca.md")
    (tmp_path / "info.md").write_text("Prezzi su richiesta", encoding="utf-8")
    assert load_info(tmp_path / "info.md") == "Prezzi su richiesta"
    assert Path("info/argo.md").is_file()


# --- ConversationRelay (VOICE_MODE=relay) ------------------------------------

import json  # noqa: E402

from argo.channels import relay as relay_module  # noqa: E402


def make_relay_client(responses, numero=OPERATORE, **kwargs):
    claude = FakeClaude(responses)
    assistant = Assistant(claude, "m", kb(), None, numero)
    settings = Settings(
        _env_file=None,
        twilio_auth_token=TWILIO_TOKEN,
        public_base_url=PUBLIC_URL,
        human_support_number=numero,
        voice_mode="relay",
        **kwargs,
    )
    return TestClient(create_app(settings, assistant)), claude


def relay_headers(path="/voice/relay"):
    return {"X-Twilio-Signature": twilio_signature(TWILIO_TOKEN, "wss://argo.example.com" + path, [])}


SETUP = {"type": "setup", "callSid": "CA7", "from": "+393331112222", "to": "+15550001111", "direction": "inbound"}


def test_relay_twiml_in_arrivo():
    client, _ = make_relay_client(
        [], relay_tts_provider="ElevenLabs", relay_voice="VOCE_IT",
        relay_speech_model="flux-general-multi", relay_eot_threshold="0.7",
    )
    r = post_twilio(client, "/voice/incoming", {"CallSid": "CA1", "From": "+39333"})
    assert "<Gather" not in r.text
    assert 'url="wss://argo.example.com/voice/relay"' in r.text
    assert 'language="it-IT"' in r.text and 'voice="VOCE_IT"' in r.text
    assert 'ttsProvider="ElevenLabs"' in r.text
    assert 'speechModel="flux-general-multi"' in r.text and 'eotThreshold="0.7"' in r.text
    assert f'action="{PUBLIC_URL}/voice/relay-fine"' in r.text


def test_relay_risponde_al_tecnico():
    client, claude = make_relay_client([([text_block("Controlla il fusibile F2.")], "end_turn")])
    with client.websocket_connect("/voice/relay", headers=relay_headers()) as ws:
        ws.send_json(SETUP)
        ws.send_json({"type": "prompt", "voicePrompt": "la sirena non suona", "last": True})
        assert ws.receive_json() == {"type": "text", "token": "Controlla il fusibile F2.", "last": False}
        assert ws.receive_json() == {"type": "text", "token": "", "last": True}
    assert claude.calls[0]["messages"][0]["content"] == "la sirena non suona"
    assert "telefono" in claude.calls[0]["system"]


def test_relay_frase_di_attesa_se_lento(monkeypatch):
    import time as _time

    monkeypatch.setattr(relay_module, "FILLER_AFTER_SECONDS", 0.05)
    client, claude = make_relay_client([([text_block("Fatto.")], "end_turn")])
    original = claude.create

    def slow(**kwargs):
        _time.sleep(0.3)
        return original(**kwargs)

    claude.create = slow
    with client.websocket_connect("/voice/relay", headers=relay_headers()) as ws:
        ws.send_json(SETUP)
        ws.send_json({"type": "prompt", "voicePrompt": "batteria", "last": True})
        assert ws.receive_json()["token"] == relay_module.FILLER_TEXT
        assert ws.receive_json()["token"] == "Fatto."
        assert ws.receive_json()["last"] is True


def test_relay_passa_all_operatore():
    client, _ = make_relay_client([
        ([tool_block("passa_a_operatore", {"motivo": "urgente"})], "tool_use"),
        ([text_block("Ti passo un collega.")], "end_turn"),
    ])
    with client.websocket_connect("/voice/relay", headers=relay_headers()) as ws:
        ws.send_json(SETUP)
        ws.send_json({"type": "prompt", "voicePrompt": "voglio un collega", "last": True})
        end = ws.receive_json()
    assert end["type"] == "end"
    data = json.loads(end["handoffData"])
    assert data == {"azione": "operatore", "testo": "Ti passo un collega."}
    r = post_twilio(client, "/voice/relay-fine", {"HandoffData": end["handoffData"], "SessionStatus": "ended"})
    assert f">{OPERATORE}</Dial>" in r.text and "Ti passo un collega." in r.text


def test_relay_saluto_finale():
    client, _ = make_relay_client([])
    with client.websocket_connect("/voice/relay", headers=relay_headers()) as ws:
        ws.send_json(SETUP)
        ws.send_json({"type": "prompt", "voicePrompt": "Grazie ciao", "last": True})
        end = ws.receive_json()
    assert json.loads(end["handoffData"])["azione"] == "chiudi"
    r = post_twilio(client, "/voice/relay-fine", {"HandoffData": end["handoffData"]})
    assert "Buon lavoro" in r.text and "<Hangup/>" in r.text


def test_relay_fine_senza_handoff_riaggancia():
    client, _ = make_relay_client([])
    r = post_twilio(client, "/voice/relay-fine", {"SessionStatus": "failed"})
    assert r.text.endswith("<Response><Hangup/></Response>")


def test_relay_firma_non_valida():
    import pytest
    from starlette.websockets import WebSocketDisconnect

    client, _ = make_relay_client([])
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/voice/relay", headers={"X-Twilio-Signature": "falsa"}) as ws:
            ws.receive_json()


def test_relay_chiamata_in_uscita_usa_destinatario():
    client, claude = make_relay_client([([text_block("Ok.")], "end_turn")])
    assistant = client.app.state.assistant
    with client.websocket_connect("/voice/relay", headers=relay_headers()) as ws:
        ws.send_json({**SETUP, "direction": "outbound-api", "from": "+15550001111", "to": "+393334445555"})
        ws.send_json({"type": "prompt", "voicePrompt": "ciao", "last": True})
        ws.receive_json()
    assert assistant._phones["voce:CA7"] == "+393334445555"


def test_voice_mode_non_valido():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, voice_mode="boh")


class FakeStream:
    """Simula client.messages.stream dell'SDK Anthropic."""

    def __init__(self, chunks, final):
        self.text_stream = iter(chunks)
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.final


class StreamingClaude(FakeClaude):
    def stream(self, **kwargs):
        response = self.create(**kwargs)
        chunks = [b.text for b in response.content if b.type == "text"]
        # spezza il testo in due pezzi, come farebbe lo streaming vero
        pieces = [c for t in chunks for c in (t[: len(t) // 2], t[len(t) // 2 :])]
        return FakeStream(pieces, response)


def test_converse_in_streaming_passa_il_testo_a_pezzi():
    claude = StreamingClaude([
        ([text_block("Controllo la scheda."), tool_block("cerca_manuali", {"query": "batteria"})], "tool_use"),
        ([text_block("La batteria deve superare 12,4 V.")], "end_turn"),
    ])
    pieces = []
    answer = Assistant(claude, "m", kb(), None).converse("s", "batteria", "voce", on_text=pieces.append)
    assert answer == "La batteria deve superare 12,4 V."
    assert "".join(pieces) == "Controllo la scheda. La batteria deve superare 12,4 V."
    assert len(pieces) == 5
    assert claude.calls[0]["system"]  # il prompt in cache torna testo nel finto client


def test_prompt_di_sistema_in_cache():
    calls = []

    class Spy:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=[text_block("ok")], stop_reason="end_turn")

    Assistant(Spy(), "m", kb(), None).reply("s", "ciao", "telegram")
    assert calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_relay_streaming_e_operatore_trattiene_il_testo():
    claude = StreamingClaude([
        ([tool_block("passa_a_operatore", {"motivo": "urgente"})], "tool_use"),
        ([text_block("Ti passo un collega.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), None, OPERATORE)
    settings = Settings(_env_file=None, twilio_auth_token=TWILIO_TOKEN, public_base_url=PUBLIC_URL,
                        human_support_number=OPERATORE, voice_mode="relay")
    client = TestClient(create_app(settings, assistant))
    with client.websocket_connect("/voice/relay", headers=relay_headers()) as ws:
        ws.send_json(SETUP)
        ws.send_json({"type": "prompt", "voicePrompt": "collega", "last": True})
        end = ws.receive_json()
    # nessun pezzo detto a voce: la frase la dice /voice/relay-fine prima del trasferimento
    assert end["type"] == "end"
    assert json.loads(end["handoffData"]) == {"azione": "operatore", "testo": "Ti passo un collega."}


# --- App (PWA) ---------------------------------------------------------------

from argo.channels.app import TokenLimiter, access_token  # noqa: E402


def make_app_client(**kwargs):
    settings = Settings(
        _env_file=None,
        twilio_account_sid="AC1", twilio_api_key="SK1", twilio_api_secret="segreto-api",
        twilio_twiml_app_sid="AP1", **kwargs,
    )
    return TestClient(create_app(settings, Assistant(FakeClaude([]), "m", kb(), None)))


def decode_jwt(token):
    import base64

    def part(p):
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))

    header, payload, _ = token.split(".")
    return part(header), part(payload)


def test_access_token_firmato():
    import base64
    import hashlib
    import hmac

    token = access_token("AC1", "SK1", "segreto", "tecnico", "AP1", ttl=600, now=1000)
    header, payload = decode_jwt(token)
    assert header["cty"] == "twilio-fpa;v=1" and header["alg"] == "HS256"
    assert payload["iss"] == "SK1" and payload["sub"] == "AC1" and payload["exp"] == 1600
    assert payload["grants"]["identity"] == "tecnico"
    assert payload["grants"]["voice"]["outgoing"]["application_sid"] == "AP1"
    signing, sig = token.rsplit(".", 1)
    expected = base64.urlsafe_b64encode(hmac.new(b"segreto", signing.encode(), hashlib.sha256).digest()).rstrip(b"=")
    assert sig.encode() == expected


def test_app_token():
    client = make_app_client()
    r = client.post("/app/token", json={})
    assert r.status_code == 200
    _, payload = decode_jwt(r.json()["token"])
    assert payload["grants"]["identity"].startswith("argo-app-")


def test_app_token_codice_e_configurazione():
    client = make_app_client(callback_password="x")
    assert client.post("/app/token", json={"codice": "no"}).status_code == 403
    assert client.post("/app/token", json={"codice": "x"}).status_code == 200
    client = make_app_client(twilio_twiml_app_sid="")
    assert client.post("/app/token", json={}).status_code == 503
    client = make_app_client(app_enabled=False)
    assert client.post("/app/token", json={}).status_code == 403


def test_app_limite_token():
    limiter = TokenLimiter(2)
    assert limiter.allow(now=0) and limiter.allow(now=1)
    assert not limiter.allow(now=2)
    assert limiter.allow(now=3700)


def test_app_pagina_e_pwa():
    client = make_app_client(callback_password="x")
    html = client.get("/app").text
    assert "var NEEDS_CODE = true;" in html and "{{" not in html
    assert 'rel="manifest"' in html
    manifest = client.get("/app/manifest.webmanifest").json()
    assert manifest["start_url"] == "/app" and manifest["display"] == "standalone"
    assert client.get("/app/icon-192.png").headers["content-type"] == "image/png"
    assert client.get("/app/icon-64.png").status_code == 404
    sw = client.get("/app/sw.js")
    assert sw.headers["service-worker-allowed"] == "/app" and "/app/token" not in sw.text


def test_app_cellulare_per_whatsapp():
    class FakeMessenger:
        def __init__(self):
            self.sent = []

        def send_whatsapp(self, to, body):
            self.sent.append((to, body))

    messenger = FakeMessenger()
    claude = FakeClaude([
        ([tool_block("invia_manuale_whatsapp", {"testo": "*Scheda*"})], "tool_use"),
        ([text_block("Inviata.")], "end_turn"),
    ])
    assistant = Assistant(claude, "m", kb(), None, messenger=messenger)
    settings = Settings(_env_file=None, twilio_auth_token=TWILIO_TOKEN, public_base_url=PUBLIC_URL)
    client = TestClient(create_app(settings, assistant))
    post_twilio(client, "/voice/incoming", {"CallSid": "CA5", "From": "client:argo-app-1", "Telefono": "333 123 4567"})
    post_twilio(client, "/voice/respond", {"CallSid": "CA5", "From": "client:argo-app-1", "SpeechResult": "mandamela"})
    assert messenger.sent[0][0] == "+393331234567"


def test_app_cellulare_non_valido_ignorato():
    from argo.channels.app import phone_for_call, remember_phone

    state = SimpleNamespace()
    settings = Settings(_env_file=None)
    remember_phone(state, "CA1", "12", settings)  # troppo corto
    assert phone_for_call(state, "CA1", "client:x") == "client:x"
    remember_phone(state, "CA3", "+44 7700 900123", settings)  # estero: va bene
    assert phone_for_call(state, "CA3", "client:x") == "+447700900123"
    remember_phone(state, "CA2", "0039 333 1234567", settings)
    assert phone_for_call(state, "CA2", "client:x") == "+393331234567"
