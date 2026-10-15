"""Ricerca nei manuali tecnici.

Prima versione volutamente semplice: i manuali (.md / .txt) vengono divisi in
paragrafi e cercati per parole chiave con un punteggio stile TF-IDF.
Quando i manuali cresceranno si potrà sostituire con una ricerca vettoriale
mantenendo la stessa interfaccia `search()`.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# lettere + numeri, con trattino o spazio opzionale e un suffisso: DT-800.L, MG420, E07
PRODUCT_CODE = re.compile(r"\b[A-Z]{1,4}[- ]?\d{2,5}(?:\.[A-Z0-9]{1,3})?\b")
_WORD = re.compile(r"[a-zà-ù0-9]+", re.IGNORECASE)
_STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "a", "da",
    "in", "con", "su", "per", "tra", "fra", "e", "o", "che", "non", "si",
    "del", "della", "dei", "delle", "al", "alla", "ai", "alle", "nel", "nella",
    "è", "come", "cosa", "mi", "ho", "ha", "sono",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


@dataclass
class Passage:
    source: str
    title: str
    text: str


@dataclass
class Hit:
    passage: Passage
    score: float


class KnowledgeBase:
    def __init__(self, passages: list[Passage]):
        self.passages = passages
        self._tf = [Counter(_tokens(p.title + " " + p.text)) for p in passages]
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = max(len(passages), 1)
        self._idf = {term: math.log(1 + n / count) for term, count in df.items()}

    @classmethod
    def from_dir(cls, directory: str | Path) -> "KnowledgeBase":
        passages: list[Passage] = []
        root = Path(directory)
        if root.exists():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                suffix = path.suffix.lower()
                try:
                    if suffix in {".md", ".txt"}:
                        passages.extend(_split(path, root))
                    elif suffix == ".pdf":
                        passages.extend(_split_pdf(path, root))
                except Exception:
                    log.exception("Manuale non leggibile, lo salto: %s", path)
        log.info("Manuali caricati: %d sezioni da %s", len(passages), root)
        return cls(passages)

    def codes(self, limit: int = 150) -> list[str]:
        """Codici di prodotto ed errore presenti nei manuali (es. DT-800.L, MG420, E07).

        Servono alla voce: il riconoscimento vocale li riceve come suggerimenti e
        Claude li usa per capire codici trascritti male ("di ti ottocento").
        """
        seen: dict[str, str] = {}
        for passage in self.passages:
            for code in PRODUCT_CODE.findall(passage.title + " " + passage.text):
                key = re.sub(r"[^A-Z0-9]", "", code.upper())
                seen.setdefault(key, code)  # MG420 e MG-420 sono lo stesso codice
        return sorted(seen.values())[:limit]

    def search(self, query: str, limit: int = 3) -> list[Hit]:
        terms = _tokens(query)
        if not terms:
            return []
        hits: list[Hit] = []
        for passage, tf in zip(self.passages, self._tf):
            length = sum(tf.values()) or 1
            score = sum(tf[t] / length * self._idf.get(t, 0.0) for t in terms)
            if score > 0:
                hits.append(Hit(passage, score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]


def _split(path: Path, root: Path) -> list[Passage]:
    """Divide un manuale in sezioni (titoli markdown) e paragrafi."""
    source = str(path.relative_to(root))
    title = path.stem
    passages: list[Passage] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            passages.append(Passage(source, title, text))
        buffer.clear()

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            flush()
            title = line.lstrip("#").strip() or title
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return passages


PDF_PAGE_MAX_CHARS = 8000  # oltre, la pagina viene divisa ripetendo l'intestazione
PDF_HEADER_LINES = 4
_COLUMN_GAP = re.compile(r"\s{3,}")


def _page_lines(page) -> list[str]:
    """Testo della pagina riga per riga, mantenendo l'impaginazione.

    Le schede tecniche sono tabelle con un modello per colonna: l'estrazione
    "layout" tiene ogni caratteristica sulla stessa riga dei suoi valori e i
    grandi spazi tra le colonne diventano " | ".
    """
    text = page.extract_text(layout=True, x_density=4, y_density=13) or ""
    return [_COLUMN_GAP.sub(" | ", line.strip()) for line in text.splitlines() if line.strip()]


def _split_pdf(path: Path, root: Path) -> list[Passage]:
    """Un blocco per pagina, così intestazioni e valori delle tabelle restano insieme.

    Funziona solo con PDF che contengono testo: le scansioni (immagini) vanno
    prima passate in un OCR.
    """
    import pdfplumber

    source = str(path.relative_to(root))
    passages: list[Passage] = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            title = f"{path.stem}, pag. {number}"
            lines = _page_lines(page)
            if not lines:
                continue
            if sum(len(line) for line in lines) <= PDF_PAGE_MAX_CHARS:
                passages.append(Passage(source, title, "\n".join(lines)))
                continue
            # pagina molto lunga: blocchi più piccoli, ognuno con l'intestazione
            header, body = lines[:PDF_HEADER_LINES], lines[PDF_HEADER_LINES:]
            chunk: list[str] = []
            for line in body:
                chunk.append(line)
                if sum(len(x) for x in chunk) >= PDF_PAGE_MAX_CHARS // 2:
                    passages.append(Passage(source, title, "\n".join(header + chunk)))
                    chunk = []
            if chunk:
                passages.append(Passage(source, title, "\n".join(header + chunk)))
    if not passages:
        log.warning("Nessun testo estratto da %s (forse è una scansione)", path)
    return passages
