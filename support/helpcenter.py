"""The help center: the store's policy articles, split into passages, searched with BM25.

Each passage goes back to the model as a `search_result` block with citations enabled, one
text block per sentence, so a reply can cite the exact sentence it relies on. The articles
are small and fixed, so the index is built once per process.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

ARTICLES = Path(__file__).resolve().parent / "helpcenter"
WORD = re.compile(r"[a-z0-9$]+(?:[.,][0-9]+)*")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z$0-9])")
STOP = frozenset("""a an and are as at be by can do does for from has have how i if in is it its my of on or
our so that the their them then there this to up was we what when where which who will with you your
take takes long much work works get got does did it""".split())
# A few everyday words customers use for the words in the articles.
SYNONYMS = {
    "refund": ("return",), "send": ("return", "ship"), "back": ("return",), "money": ("refund",),
    "broken": ("damaged", "defective"), "broke": ("damaged", "defective"), "cracked": ("damaged",),
    "late": ("late", "delivery"), "missing": ("lost",), "lost": ("lost",), "stuck": ("tracking", "lost"),
    "deliver": ("delivery", "ship"), "delivery": ("shipping",), "postage": ("shipping",), "abroad": ("countries",),
    "international": ("countries",), "uk": ("countries",), "europe": ("countries",), "address": ("address",),
    "cancel": ("cancelled", "cancel"), "size": ("size", "sizes", "exchange"), "swap": ("exchange",),
    "fit": ("size", "fits"), "guarantee": ("warranty",), "faulty": ("defective",), "pay": ("payment",),
    "paypal": ("paypal",), "coupon": ("discount", "code"), "promo": ("discount", "code"), "cheaper": ("price", "drops"),
    "wash": ("wash",), "clean": ("wash",), "hours": ("hours",), "human": ("person", "team"), "agent": ("person", "team"),
}


def words(text: str) -> list[str]:
    out = []
    for w in WORD.findall(text.lower()):
        if w in STOP:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


@dataclass(frozen=True)
class Passage:
    source: str      # "helpcenter/returns#return-window"
    article: str     # "Returns and exchanges"
    section: str     # "Return window"
    sentences: tuple[str, ...]

    @property
    def title(self) -> str:
        return f"{self.article} > {self.section}"

    def block(self) -> dict[str, Any]:
        return {"type": "search_result", "source": self.source, "title": self.title,
                "content": [{"type": "text", "text": s} for s in self.sentences], "citations": {"enabled": True}}

    def text(self) -> str:
        return " ".join(self.sentences)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_passages(folder: Path = ARTICLES) -> list[Passage]:
    passages: list[Passage] = []
    for path in sorted(folder.glob("*.md")):
        article = ""
        sections: list[tuple[str, list[str]]] = []  # (heading, lines)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("# "):
                article = line[2:].strip()
            elif line.startswith("## "):
                sections.append((line[3:].strip(), []))
            elif line and sections:
                sections[-1][1].append(line)
        for heading, lines in sections:
            body = " ".join(lines).strip()
            if body:
                passages.append(Passage(f"helpcenter/{path.stem}#{_slug(heading)}", article, heading,
                                        tuple(s.strip() for s in SENTENCE_END.split(body) if s.strip())))
    return passages


class HelpCenter:
    """BM25 over passages. Title words count twice, so 'return window' finds that section."""

    def __init__(self, passages: list[Passage] | None = None, k1: float = 1.2, b: float = 0.75):
        self.passages = passages if passages is not None else load_passages()
        self.k1, self.b = k1, b
        self._terms = [Counter(words(f"{p.article} {p.section} {p.section} {p.text()}")) for p in self.passages]
        self._avg = sum(sum(t.values()) for t in self._terms) / max(len(self._terms), 1)
        df: Counter[str] = Counter()
        for terms in self._terms:
            df.update(terms.keys())
        n = len(self.passages)
        self._idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def by_source(self, source: str) -> Passage | None:
        return next((p for p in self.passages if p.source == source), None)

    def search(self, query: str, limit: int = 3) -> list[Passage]:
        query_words = words(query)
        expanded = list(query_words)
        for w in query_words:
            expanded.extend(SYNONYMS.get(w, ()))
        if not expanded:
            return []
        scored = []
        for passage, terms in zip(self.passages, self._terms, strict=True):
            length = sum(terms.values())
            score = 0.0
            for w in set(expanded):
                tf = terms.get(w, 0)
                if tf:
                    weight = 1.0 if w in query_words else 0.6
                    score += weight * self._idf[w] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self._avg))
            if score > 0:
                scored.append((score, passage))
        scored.sort(key=lambda sp: -sp[0])
        if not scored:
            return []
        best = scored[0][0]
        # Keep passages that score at least a third of the best one: fewer, more relevant passages.
        return [p for s, p in scored[:limit] if s >= best / 3]


@lru_cache(maxsize=1)
def default_help_center() -> HelpCenter:
    return HelpCenter()
