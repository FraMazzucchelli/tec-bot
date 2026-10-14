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
    return KnowledgeBase.from_dir("manuals")


def test_ricerca_manuali_trova_la_sezione_giusta():
    hits = kb().search("batteria tampone scarica")
    assert hits
    assert "Batteria" in hits[0].passage.title


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


def test_health():
    client, _ = make_client([])
    assert client.get("/health").json()["status"] == "ok"
