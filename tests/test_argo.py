from types import SimpleNamespace

from fastapi.testclient import TestClient

from argo.assistant import Assistant
from argo.config import Settings
from argo.knowledge import KnowledgeBase, Passage
from argo.main import create_app
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


def make_client(responses):
    claude = FakeClaude(responses)
    assistant = Assistant(claude, "m", kb(), None)
    app = create_app(Settings(telegram_bot_token="x"), assistant)
    return TestClient(app), claude


def test_voce_chiamata_in_arrivo():
    client, _ = make_client([])
    r = client.post("/voice/incoming", data={"CallSid": "CA1", "From": "+39333"})
    assert r.status_code == 200
    assert "<Gather" in r.text and 'language="it-IT"' in r.text


def test_voce_risposta_e_escape_xml():
    client, _ = make_client([([text_block("Controlla zona <3> & morsetto")], "end_turn")])
    r = client.post("/voice/respond", data={"CallSid": "CA1", "SpeechResult": "sensore in allarme"})
    assert "&lt;3&gt; &amp; morsetto" in r.text


def test_whatsapp_risponde_in_twiml():
    client, _ = make_client([([text_block("Ciao tecnico")], "end_turn")])
    r = client.post("/whatsapp/webhook", data={"From": "whatsapp:+39333", "Body": "aiuto"})
    assert "<Message>Ciao tecnico</Message>" in r.text


def test_health():
    client, _ = make_client([])
    assert client.get("/health").json()["status"] == "ok"
