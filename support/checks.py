"""Checks on every reply before the customer sees it. Plain code, no model.

1. Every money amount in the reply must appear in what the store gave the assistant in this
   chat: tool results, help center passages, or the team's messages. Amounts the customer
   typed do not count. An amount called a refund must be a refund the store worked out or one
   the team named, so a reply cannot pass off an item's price as the refund.
2. The reply must not say a refund, cancellation or change is done unless the team
   approved one of that kind in this chat.

These checks catch the common phrasings in English; they are a safety net under the store's
rules, not a replacement for them. A held reply is not shown to the customer: they are told a
teammate will check, and the team sees the draft and the reason.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

NUM = r"(\d{1,3}(?:,\d{3})+|\d{1,9})(?:\.(\d{1,2})|,(\d{2}))?(?!,?\d)"
CUR = r"(?:[A-Z]{1,3})?[$€£]"  # $, US$, AU$, USD$, C$, euro, pound
MONEY = re.compile(r"(?<![\w.])" + CUR + r"\s{0,3}" + NUM
                   + r"|(?<![\w.$])(?:USD|CAD|AUD|EUR|GBP)\s?" + NUM
                   + r"|(?<![\w.$])" + NUM + r"\s?(?:[$€£](?!\d)|(?:USD|CAD|AUD|EUR|GBP|dollars?|bucks)\b|-dollar\b)", re.I)
AMOUNT = r"(" + CUR + r"\s{0,3}" + NUM + r")"
REFUND_PATTERNS = [re.compile(p, re.I) for p in (
    # "refund will be $182.05", "Your refund: $189.00", "Refund - $189.00", "refund (once approved) is $189.00"
    r"\brefund(?:ed|s)?\b[^.!?\n$€£]{0,40}?" + AMOUNT,
    # "$189 refund", "$189.00 will be refunded", "$189.00 has been refunded"
    AMOUNT + r"\s+(?:(?:will|would|should|can)\s+be\s+|is\s+|was\s+|has\s+been\s+)?(?:full\s+|partial\s+)?refund(?:ed)?\b",
    # "you'll get $189.00 back"
    r"\bget\s+(?:all\s+)?" + AMOUNT + r"\s+back\b",
)]
ARITHMETIC_AFTER = re.compile(r"\s*(?:minus|less|before)\b", re.I)  # "$189.00 minus the fee" is not the refund
DONE = r"(?:refunded|cancell?ed|approved|accepted|issued|credited)"
ADVERBS = r"(?:\s+(?:now|already|just|successfully|fully|been|all))*"
NOT_REQUEST = r"(?!\s+request)"  # "your return request is confirmed as received" describes the request, not the return
BE = r"(?:\s+is|\s+was|\s+has\s+been|\s+have\s+been|'s|'s\s+been)"
DONE_CLAIMS = [re.compile(p, re.I) for p in (
    # I've refunded / we have cancelled / I went ahead and refunded / I've issued a full refund / I've added a credit
    r"\b(?:i|we)(?:'ve|\s+have)?" + ADVERBS + r"\s+(?:gone\s+ahead\s+and\s+|went\s+ahead\s+and\s+)?"
    r"(?:refunded|cancell?ed|approved|accepted|credited|issued\s+(?:\w+\s+){0,2}refund|initiated\s+(?:\w+\s+){0,2}refund"
    + NOT_REQUEST + r"|(?:added|applied)(?:\s+[\w$.,]+){0,3}?\s+(?:credit|discount|refund|code|coupon|voucher)|"
    r"processed\s+(?:\w+\s+){0,2}(?:refund|return|cancellation)" + NOT_REQUEST + r"|"
    r"changed\s+(?:the|your)\s+(?:\w+\s+)?address|updated\s+(?:the|your)\s+(?:\w+\s+)?address)\b",
    # has been cancelled / you've been refunded / it's been approved
    r"(?:\b(?:has|have)|'s|'ve)(?:\s+(?:now|already|just))?\s+been(?:\s+(?:now|already|just|successfully|fully))?\s+" + DONE + r"\b",
    # the return (for order #1042) is approved / the cancellation has been confirmed / your refund's processed
    r"\b(?:return|refund|exchange|address|cancellation)\b" + NOT_REQUEST + r"(?:\s+[#\w$.,]+){0,3}?" + BE + ADVERBS +
    r"\s+(?:cancell?ed|refunded|approved|accepted|processed|changed|updated|confirmed|complete|completed|done)\b",
    # your order is cancelled / your order was successfully cancelled / your order's fully refunded
    r"\border\b(?:\s+[#\w$.,]+){0,3}?" + BE + ADVERBS + r"\s+(?:cancell?ed|refunded)\b",
    # the team approved your return / they've accepted it
    r"\b(?:team|manager|they|we)(?:'ve|\s+have|\s+has)?\s+(?:already\s+|now\s+)?(?:approved|accepted|refunded|cancell?ed)\b",
    # the refund is on its way / was sent / is being processed / your $182.05 is on its way
    r"\brefund\b(?:\s+[#\w$.,]+){0,4}?(?:" + BE + r")\s+(?:on\s+(?:its|the)\s+way)\b",
    r"\brefund\b(?:\s+[#\w$.,]+){0,4}?\s+(?:was|has\s+been)\s+(?:sent|issued|credited|added)\b",
    r"\brefund\b(?:\s+[#\w$.,]+){0,3}?\s+is\s+(?:now\s+)?being\s+(?:processed|issued|sent)\b",
    r"[$€£][\d,.]+\d(?:'s|\s+is|\s+was)\s+on\s+(?:its|the)\s+way\b",
    # "Refund issued." / "Return approved!" / "Cancellation confirmed." / "Cancelled your order for you."
    r"\b(?:refund|return|exchange|cancellation)\s+(?:issued|sent|processed|approved|accepted|confirmed|complete|completed)\b",
    r"(?:^|[.!?]\s+)(?:cancell?ed|refunded)\s+(?:your|the)\s+(?:order|refund|item|purchase)\b",
    r"\b(?:cancellation|refund|return|change)\s+went\s+through\b",
)]
NEGATION = re.compile(r"\b(?:no|not|nothing|never|yet\s+to|can't|cannot|won't|isn't|hasn't|haven't|didn't|unable)\b|n't\b", re.I)
NOT_A_NEGATION = re.compile(r"\bno\s+(?:problem|worries|trouble)\b", re.I)
CONDITION_BEFORE = re.compile(r"\b(?:once|if|when|whenever|after|until|as\s+soon\s+as|before|unless)\b", re.I)
CONDITION_AFTER = re.compile(r"\b(?:once|when|whenever|after|until|as\s+soon\s+as)\b", re.I)  # "is issued once the return arrives"
CLAUSE_END = re.compile(r"[.!?;:\n]|,|\s[-–—]\s")
INVISIBLE = re.compile(r"[​-‏⁠-⁤﻿*_`]")
HELD_REPLY = ("I want a teammate to double-check this before I answer, so I've passed our chat to the support team. "
              "They'll reply right here. Our team works Monday to Friday, 8 am to 6 pm Pacific time.")


def normalize(text: str) -> str:
    """Full width signs to plain ones, invisible characters and Markdown emphasis removed:
    '＄500', '$\\u200b500' and '$**500**' all read as $500."""
    return INVISIBLE.sub("", unicodedata.normalize("NFKC", text or ""))


def _cents(whole: str, dot: str, comma: str) -> int:
    return int(whole.replace(",", "")) * 100 + int((dot or comma or "0").ljust(2, "0"))


def amounts(text: str) -> set[int]:
    """Money amounts in cents: '$189', '$189.00', '$1,189.5', 'US$6.95', '182 USD', '$182,05', '500$'."""
    found = set()
    for m in MONEY.finditer(normalize(text)):
        g = m.groups()
        for i in range(0, 9, 3):
            if g[i]:
                found.add(_cents(g[i], g[i + 1], g[i + 2]))
                break
    return found


def _clause(text: str, start: int, end: int) -> tuple[str, str]:
    """The text of the clause around text[start:end]: what comes before it and after it."""
    begin = max([0] + [e.end() for e in CLAUSE_END.finditer(text, 0, start)])
    after = CLAUSE_END.search(text, end)
    return text[begin:start], text[end:after.start() if after else len(text)]


def _negated(before: str) -> bool:
    return bool(NEGATION.search(NOT_A_NEGATION.sub("", before)))


def refund_amounts(text: str) -> set[int]:
    """Amounts the reply calls a refund. Not when the amount is part of a sum ('$189.00 minus
    the fee') or the clause is negated ('you won't get the full $189.00 refund')."""
    found = set()
    text = normalize(text)
    for pattern in REFUND_PATTERNS:
        for m in pattern.finditer(text):
            before, _ = _clause(text, m.start(), m.end())
            if _negated(before + " " + m.group(0)) or ARITHMETIC_AFTER.match(text, m.end(1)):
                continue
            found |= amounts(m.group(1))
    return found


def _kind(phrase: str, before: str) -> str:
    def of(text: str) -> str | None:
        text = text.lower()
        return "cancellation" if "cancel" in text else "address" if "address" in text else \
            "refund" if re.search(r"refund|return|exchange|credit|\$", text) else None
    return of(phrase) or of(before[-40:]) or "any"


def done_claims(reply: str) -> list[tuple[str, str]]:
    """Every phrase that says a refund, cancellation or change is already done, with its kind
    (refund, cancellation, address or any). A phrase is not a claim when its clause is a
    condition ("once your return is approved", "the refund is issued once the return arrives")
    or a negation ("nothing has been approved yet")."""
    reply = normalize(reply)
    found, seen = [], set()
    for pattern in DONE_CLAIMS:
        for m in pattern.finditer(reply):
            if m.start() in seen:
                continue
            before, after = _clause(reply, m.start(), m.end())
            if _negated(before) or CONDITION_BEFORE.search(before) or CONDITION_AFTER.search(after) \
                    or re.match(r"\s+or\s+\w+ed\b", after) or NEGATION.search(m.group(0)):
                continue
            seen.add(m.start())
            found.append((m.group(0).strip(" .!?"), _kind(m.group(0), before)))
    return found


def done_claim(reply: str) -> tuple[str, str] | None:
    claims = done_claims(reply)
    return claims[0] if claims else None


def _store_refunds_from_text(sources: str) -> set[int]:
    found = set()
    for key in ("refund_if_approved", "return_fee_deducted"):
        for value in re.findall(r'"' + key + r'":\s*"([^"]+)"', sources):
            found |= amounts(value)
    for chunk in re.findall(r"<team>(.*?)</team>", sources, re.S):
        found |= amounts(chunk)
    return found


@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)   # what passed, shown on the team's side


def check_reply(reply: str, sources: str | set[int], approved: set[str] | bool,
                store_refunds: set[int] | None = None) -> Verdict:
    """`sources`: the amounts the store gave the assistant in this chat (or the text they are
    in). `store_refunds`: the refunds and fees the store worked out, and amounts the team named.
    `approved`: the kinds of request the team approved ("return", "cancellation",
    "address_change"); True means any."""
    if isinstance(sources, str):
        store_refunds = _store_refunds_from_text(sources) if store_refunds is None else store_refunds
        sources = amounts(sources)
    store_refunds = store_refunds or set()
    problems, checked = [], []
    said = amounts(reply)
    unsupported = sorted(said - sources)
    if unsupported:
        problems.append("mentions " + ", ".join(f"${c / 100:,.2f}" for c in unsupported) +
                        ", which is not in the order data, the help center or the team's messages")
    else:
        wrong_refund = sorted(refund_amounts(reply) - store_refunds)
        if wrong_refund:
            problems.append("calls " + ", ".join(f"${c / 100:,.2f}" for c in wrong_refund) +
                            " a refund, but the store worked out a different amount")
        elif said:
            checked.append(f"{len(said)} amount{'s' if len(said) != 1 else ''} found in the sources")
    kinds = {"return", "cancellation", "address_change"} if approved is True else set(approved or ())
    claims = done_claims(reply or "")
    for phrase, kind in claims:
        allowed = bool(kinds) if kind == "any" else \
            ("cancellation" in kinds) if kind == "cancellation" else \
            ("address_change" in kinds) if kind == "address" else bool(kinds & {"return", "cancellation"})
        if not allowed:
            problems.append(f'says "{phrase}", but the team has not approved that in this chat')
            break
    if not claims:
        checked.append("no claim that something is already done")
    return Verdict(not problems, problems, checked)


def tool_source_text(content: str | list[dict[str, Any]]) -> str:
    """How a tool result is written into `sources`. Help center passages are kept as plain text."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        sentences = [str(c.get("text", "")) for c in block.get("content", []) if isinstance(c, dict)]
        parts.append(" ".join(sentences))
    return " ".join(parts)
