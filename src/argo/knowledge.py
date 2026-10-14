"""Ricerca nei manuali tecnici.

Prima versione volutamente semplice: i manuali (.md / .txt) vengono divisi in
paragrafi e cercati per parole chiave con un punteggio stile TF-IDF.
Quando i manuali cresceranno si potrà sostituire con una ricerca vettoriale
mantenendo la stessa interfaccia `search()`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

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
                if path.suffix.lower() in {".md", ".txt"} and path.is_file():
                    passages.extend(_split(path, root))
        return cls(passages)

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
