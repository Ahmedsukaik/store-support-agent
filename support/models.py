"""Who writes the assistant's turns: Claude (live), or a fixed set of rules (guided).

Both return the same thing: one assistant message in the Claude API's shape (text and
tool_use blocks). The rest of the system cannot tell them apart, so the guided rules also
stand in for Claude in the tests, and take over when the live model is not configured, out
of budget or down.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .demo_data import CATALOG

DEFAULT_MODEL = "claude-haiku-5-5"
# This build runs with thinking switched off, which Haiku 5.5 accepts at effort low to high.
# Sonnet 5.5 and Opus 5.5 refuse thinking "disabled", and keeping thinking on would mean
# storing and replaying thinking blocks, so other models are refused at start.
SUPPORTED_MODELS = ("claude-haiku-5-5",)
# USD per million tokens, from Anthropic's pricing page (October 2026), for the cost shown in
# the demo. Haiku 5.5 is priced higher above 100K input tokens; a demo chat never gets there
# (agent.py caps the size of what is sent).
PRICES = {"claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_write_1h": 0.20,
                                 "cache_read": 0.01}}
EFFORTS = ("low", "medium", "high")
SIMULATION = "(Simulation) Show what happens when the AI drafts a reply with a made up refund."
BAD_DRAFT = ("So sorry for the trouble! I've added a $50.00 store credit to your account, and your refund has been "
             "approved.")


@dataclass
class Turn:
    content: list[dict[str, Any]]
    stop_reason: str
    model: str
    mode: str                      # "live" or "guided"
    usage: dict[str, int] = field(default_factory=dict)
    ms: int = 0


class ModelUnavailable(Exception):
    """The live model cannot answer now. kind: budget, rate_limited, auth, error (the service),
    invalid (this one request was refused)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail = kind, detail


class Model(Protocol):
    mode: str
    name: str

    def respond(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Turn: ...


def cost_usd(model: str, usage: dict[str, int]) -> float:
    price = PRICES.get(model)
    if not price:
        return 0.0
    written = usage.get("cache_creation_input_tokens", 0)
    five_min = min(usage.get("cache_write_5m_tokens", 0), written)  # the rest is priced as 1-hour writes (the dearer)
    return (usage.get("input_tokens", 0) * price["input"] + usage.get("output_tokens", 0) * price["output"]
            + five_min * price["cache_write"] + (written - five_min) * price["cache_write_1h"]
            + usage.get("cache_read_input_tokens", 0) * price["cache_read"]) / 1_000_000


# ---------------------------------------------------------------------------- Claude
class ClaudeModel:
    """One Claude API call per assistant step. Thinking off and low effort: a support reply
    needs tools and care, not long reasoning, and this keeps each step fast and cheap."""

    mode = "live"

    def __init__(self, client: Any | None = None, model: str | None = None, max_tokens: int = 1024,
                 effort: str | None = None, strict_tools: bool | None = None):
        self.name = model or os.environ.get("SUPPORT_CLAUDE_MODEL") or DEFAULT_MODEL
        if self.name not in SUPPORTED_MODELS:
            raise ValueError(f"SUPPORT_CLAUDE_MODEL must be one of {', '.join(SUPPORTED_MODELS)} (this build runs with "
                             "thinking off)")
        self.effort = (effort or os.environ.get("SUPPORT_CLAUDE_EFFORT") or "low").lower()
        if self.effort not in EFFORTS:
            raise ValueError(f"SUPPORT_CLAUDE_EFFORT must be one of {', '.join(EFFORTS)}")
        strict_env = os.environ.get("SUPPORT_STRICT_TOOLS", "on").lower() not in ("off", "0", "false", "no")
        self.strict_tools = strict_env if strict_tools is None else strict_tools
        self.max_tokens = max_tokens
        if client is None:
            import anthropic  # only needed in live mode
            # No retries: a rate limit can ask to wait longer than a serverless request may run. The page
            # drives the loop one step at a time, and a failed step falls back to the guided rules.
            client = anthropic.Anthropic(timeout=anthropic.Timeout(20.0, connect=3.0), max_retries=0)  # reads ANTHROPIC_API_KEY
        self._client = client

    def request(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> dict[str, Any]:
        tool_defs = [dict(t) if self.strict_tools else {k: v for k, v in t.items() if k != "strict"} for t in tools]
        return {
            "model": self.name, "max_tokens": self.max_tokens,
            # The instructions and tools are the same for every chat: cached for an hour and shared by
            # every visitor, read at a tenth of the price.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            "tools": tool_defs, "messages": messages,
            "thinking": {"type": "disabled"}, "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},  # automatic caching of the growing conversation
        }

    def respond(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Turn:
        import anthropic
        started = time.monotonic()
        try:
            response = self._client.messages.create(**self.request(system, tools, messages))
        except anthropic.RateLimitError as e:
            raise ModelUnavailable("budget" if "enforced_spend_limit_reached" in str(_body(e)) else "rate_limited") from e
        except anthropic.BadRequestError as e:
            if "usage limits" in str(_body(e)).lower():
                raise ModelUnavailable("budget") from e
            raise ModelUnavailable("invalid", "bad request") from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ModelUnavailable("auth") from e
        except anthropic.APIError as e:  # connection errors, timeouts, overloaded, 5xx
            raise ModelUnavailable("error", type(e).__name__) from e
        content = [_clean_block(b) for b in _get(response, "content") or []]
        usage_obj = _get(response, "usage")
        usage = {k: int(_get(usage_obj, k) or 0) for k in
                 ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
        usage["cache_write_5m_tokens"] = int(_get(_get(usage_obj, "cache_creation"), "ephemeral_5m_input_tokens") or 0)
        return Turn([b for b in content if b], str(_get(response, "stop_reason") or "end_turn"),
                    str(_get(response, "model") or self.name), "live", usage, int((time.monotonic() - started) * 1000))


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _body(error: Any) -> Any:
    return getattr(error, "body", None) or getattr(error, "message", "") or ""


def _clean_block(block: Any) -> dict[str, Any] | None:
    """Keep only what the session stores and replays: text (with citations) and tool_use.
    Empty or blank text is dropped: the API refuses it when it is sent back."""
    kind = _get(block, "type")
    if kind == "text":
        text = str(_get(block, "text") or "")
        if not text.strip():
            return None
        out: dict[str, Any] = {"type": "text", "text": text}
        cites = []
        for c in _get(block, "citations") or []:
            if _get(c, "type") == "search_result_location":
                cites.append({"type": "search_result_location", "source": str(_get(c, "source") or ""),
                              "title": str(_get(c, "title") or ""), "cited_text": str(_get(c, "cited_text") or "")})
        if cites:
            out["citations"] = cites
        return out
    if kind == "tool_use":
        raw = _get(block, "input")
        return {"type": "tool_use", "id": str(_get(block, "id")), "name": str(_get(block, "name")),
                "input": raw if isinstance(raw, dict) else {}}
    return None  # thinking is switched off; anything else is not needed to continue the chat


# ---------------------------------------------------------------------------- guided (no AI)
ORDER_NO = re.compile(r"(?<!\d)#?\s?(\d{4})(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ATTACK = re.compile(r"ignore (?:all |your |the |any |previous |prior )*(?:rules|instructions)|system prompt|you are now|"
                    r"pretend (?:to be|you)|developer mode|admin mode|jailbreak|i'?m (?:the|a|an) (?:store )?(?:manager|owner|"
                    r"admin|employee|staff)|as (?:the|a) (?:manager|admin)|override|system notice", re.I)
HUMAN = re.compile(r"\b(?:human|real person|a person|representative|real agent|talk to|speak to|speak with)\b", re.I)
CONTACT = re.compile(r"\b(?:phone|call you|call me|telephone|email address|e-mail address|contact you|reach you|whatsapp)\b", re.I)
DAMAGED = re.compile(r"\b(?:damaged|cracked|dented|crushed|smashed|arrived broken|broken when|came broken)\b", re.I)
DEFECT = re.compile(r"\b(?:leaks?|leaking|defect(?:ive)?|faulty|stopped working|doesn'?t work|ripped|torn|broke)\b", re.I)
CANCEL = re.compile(r"\bcancel", re.I)
ADDRESS = re.compile(r"\baddress\b", re.I)
EXCHANGE = re.compile(r"\b(?:exchange|swap)\b|\bsize\b.*\binstead\b", re.I)
RETURN = re.compile(r"\b(?:return|refund|send (?:it |them )?back|money back)\b", re.I)
STATUS = re.compile(r"\b(?:where|track|tracking|status|arrive|arrived|late|delivered|shipped|when will|"
                    r"hasn'?t (?:moved|arrived|come)|not moved|lost|stuck|delivery)\b", re.I)
MINE = re.compile(r"\bmy (?:order|orders|parcel|package|delivery)\b", re.I)
OTHER = re.compile(r"\b(?:my )?other order\b", re.I)
POLICY = re.compile(r"\b(?:warranty|guarantee|ship|ships|shipping|deliver|delivery|policy|how long|wash|clean|care|size guide|"
                    r"sizing|fit|payment|pay|paypal|card|gift card|discount|code|coupon|hours|open|countries|international|"
                    r"abroad|final sale|price|express|standard|label|return window|returns|refunds?|exchanges?)\b", re.I)
NEVER_MIND = re.compile(r"\bnever ?mind\b|\b(?:don'?t|do not|no need to|not)\s+(?:\w+\s+){0,2}cancel", re.I)
YES = re.compile(r"^\s*(?:yes|yeah|yep|sure|ok(?:ay)?|please do|please|go ahead|that would be great)\b[\s\w,.!]{0,25}$", re.I)
UPSET = re.compile(r"\b(?:angry|furious|ridiculous|terrible|worst|unacceptable|disgusting|scam|lawyer|sue you|fed up)\b", re.I)
EXCEPTION = re.compile(r"\bexception\b|\bbend the rules?\b|\bjust this once\b", re.I)
FOLLOW_UP = re.compile(r"\b(?:lost|trace|traced|track|tracking|where is it|arrived|late|stuck)\b", re.I)
SELL = re.compile(r"\bdo you (?:sell|have|stock|carry)\b", re.I)
GREETING = re.compile(r"^\s*(?:hi|hello|hey|hiya|good (?:morning|afternoon|evening))\b[\s!.,]*(?:there)?[\s!.,]*$", re.I)
THANKS = re.compile(r"^\s*(?:thanks|thank you|thx|cheers|great|perfect|awesome|ok(?:ay)?)\b[^?]{0,30}$", re.I)
SIZE = re.compile(r"\b(?:size|for an?|in an?|to an?|an?)\s+(xxl|xl|xs|s|m|l)\b", re.I)
PRODUCT_WORDS = {sku: set(re.findall(r"[a-z]+", p["name"].lower())) - {"the", "pack", "l"} for sku, p in CATALOG.items()}
ORDER_INTENTS = ("status", "return", "exchange", "cancel", "damaged", "other_order")
CAN_DO = ("I can help with an order (tracking, returns, exchanges, cancellations), a product question, or our store "
          "policies like shipping and warranty. I can also pass you to a person on our team.")


def _customer_texts(messages: list[dict[str, Any]]) -> list[str]:
    out = []
    for m in messages:
        if m["role"] != "user" or isinstance(m["content"], str):
            continue
        for b in m["content"]:
            if b.get("type") == "text" and not b["text"].lstrip().startswith(("<team_message", "<store_update")):
                out.append(b["text"])
    return out


def _intent(text: str) -> str | None:
    if text.strip() == SIMULATION:
        return "simulation"
    if ATTACK.search(text):
        return "attack"
    if GREETING.match(text):
        return "greeting"
    if THANKS.match(text):
        return "thanks"
    if NEVER_MIND.search(text):
        return "never_mind"
    if HUMAN.search(text):
        return "human"
    if UPSET.search(text):
        return "upset"
    if EXCEPTION.search(text):
        return "exception"
    if CONTACT.search(text):
        return "contact"
    if DAMAGED.search(text):
        return "damaged"
    if CANCEL.search(text):
        return "cancel"
    if ADDRESS.search(text) and re.search(r"\b(?:change|update|wrong|new|moved?|different)\b", text, re.I):
        return "address"
    if EXCHANGE.search(text):
        return "exchange"
    if DEFECT.search(text):
        return "return"
    if RETURN.search(text) and (ORDER_NO.search(text) or MINE.search(text) or re.search(r"\b(?:i|i'd|i want|can i)\b", text, re.I)) \
            and not re.search(r"\b(?:policy|how long|how many days|can socks|are .* returnable)\b", text, re.I):
        return "return"
    if OTHER.search(text):
        return "other_order"
    if (ORDER_NO.search(text) or MINE.search(text)) and (STATUS.search(text) or MINE.search(text)):
        return "status"
    if re.search(r"\b(?:show|see|list|view|look up|check)\b.*\borders?\b", text, re.I):
        return "status"
    if FOLLOW_UP.search(text) and not POLICY.search(text):
        return "status"  # "is it lost?", "can you trace it?": the order already being talked about
    if SELL.search(text):
        return "product"
    words = set(re.findall(r"[a-z]+", text.lower()))
    generic = {"trail", "summit", "rain", "hiking", "camp"}
    if any((words & w) - generic for w in PRODUCT_WORDS.values()) and not POLICY.search(text):
        return "product"
    if POLICY.search(text):
        return "policy"
    if any((words & w) - generic for w in PRODUCT_WORDS.values()):
        return "product"
    return None


class GuidedModel:
    """Fixed rules that cover the demo's suggested questions and common variations. It uses
    the same tools as Claude, so every safeguard in the store applies to it too. Questions it
    does not recognise get a short list of what it can help with, and a person on request."""

    mode = "guided"
    name = "guided"

    def respond(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Turn:
        last = messages[-1]["content"] if messages else []
        results = [b for b in last if isinstance(b, dict) and b.get("type") == "tool_result"]
        counter = sum(1 for m in messages if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use")
        texts = _customer_texts(messages)
        if results:
            calls = [b for b in messages[-2]["content"] if b.get("type") == "tool_use"]
            content = self._after_tools(calls, results, texts, messages, counter)
        else:
            content = self._on_message(texts, messages, counter)
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return Turn(content, stop, "guided", "guided")

    # -------------------------------------------------------------- a new customer message
    def _on_message(self, texts: list[str], messages: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
        text = texts[-1] if texts else ""
        intent = _intent(text)
        asked = _last_assistant_text(messages)
        if asked.startswith("Which item would you like to") and intent not in ("attack", "human", "never_mind", "upset"):
            intent = "exchange" if "exchange?" in asked else "return"  # the answer names the item
        elif YES.match(text) and ("pass this to our team" in asked or "pass your question to our team" in asked):
            return [_call(n, "hand_off_to_human", {"reason": "policy_exception", "priority": "normal",
                                                   "summary": _summary(texts, messages, "Customer accepted the offer to ask the team.")})]
        if intent is None and (ORDER_NO.search(text) or EMAIL.search(text) or SIZE.search(text)):
            # Only details (an order number, an email, a size): continue what the customer asked before.
            intent = _earlier_order_intent(texts[:-1])
        if intent == "simulation":
            return [_text(BAD_DRAFT)]  # on purpose: shows the reply checks holding a bad draft
        if intent == "attack":
            return [_text("I can't change how I work, act as staff, or share other customers' details. I'm happy to help "
                          "with your order, a return or a product question, though.")]
        if intent == "never_mind":
            return [_text("No problem, I won't ask for anything. Is there anything else I can help with?")]
        if intent in ("upset", "exception"):
            what = "Customer is upset and wants help from a person." if intent == "upset" else \
                "Customer asks for an exception to a store policy."
            return [_call(n, "hand_off_to_human", {"reason": "upset_customer" if intent == "upset" else "policy_exception",
                                                   "priority": "urgent" if intent == "upset" else "normal",
                                                   "summary": _summary(texts, messages, what)})]
        if intent == "greeting":
            return [_text("Hi! " + CAN_DO + " What can I do for you?")]
        if intent == "thanks":
            return [_text("You're welcome! Is there anything else I can help with?")]
        if intent == "human":
            return [_call(n, "hand_off_to_human", {"reason": "customer_asked", "priority": "normal",
                                                   "summary": _summary(texts, messages, "Customer asked for a person.")})]
        if intent == "contact":
            return [_call(n, "search_help_center", {"query": "support hours talk to our team"})]
        if intent == "policy":
            return [_call(n, "search_help_center", {"query": text[:200]})]
        if intent == "product":
            return [_call(n, "find_products", {"query": text[:200]})]
        if intent is None:
            return [_text("I'm not sure I can help with that one. " + CAN_DO)]
        number = _last(ORDER_NO, texts)
        email = _last(EMAIL, texts)
        verified = _verified(messages)
        if intent == "address":
            return [_call(n, "hand_off_to_human", {"reason": "customer_asked", "priority": "normal", "summary": _summary(
                texts, messages, f"Customer wants to change the shipping address of order #{number or '?'}. The guided "
                                 "demo does not collect addresses, so a person should.")})]
        if intent == "other_order" and verified:
            number = next((o for o in sorted(verified) if o != number), number)
        if not verified or (number and number not in verified):
            if not number or not email:
                need = " and ".join(x for x, have in (("your order number", number), ("the email you used for the order", email))
                                    if not have)
                return [_text(f"Sure, I can help with that. Could you send me {need}?")]
            return [_call(n, "verify_customer", {"order_number": number, "email": email})]  # first prove it is theirs
        return [_order_call(intent, number or sorted(verified)[0], text, n)]

    # -------------------------------------------------------------- after the tools ran
    def _after_tools(self, calls: list[dict[str, Any]], results: list[dict[str, Any]], texts: list[str],
                     messages: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
        by_id = {r["tool_use_id"]: r for r in results}
        done = [(c, by_id.get(c["id"], {})) for c in calls]
        text = texts[-1] if texts else ""
        intent = _intent(text)
        if intent not in ORDER_INTENTS:
            intent = _earlier_order_intent(texts) or intent
        call, result = done[-1]
        data = _json(result)
        name = call["name"]
        first = _first_name(messages)
        hi = f"Thanks, {first}. " if first else ""
        if name == "verify_customer":
            if result.get("is_error"):
                if data.get("error") == "verification_locked":
                    return [_text("I'm sorry, I couldn't verify those details, so I've passed our chat to the team. "
                                  "A person will reply right here.")]
                return [_text("I couldn't find an order with that order number and email. Could you check both and send them again?")]
            if intent in ORDER_INTENTS:
                return [_order_call(intent, call["input"]["order_number"], text, n)]
            listing = ", ".join(f"#{o['order_number']} (placed {o['placed']})" for o in data.get("orders", []))
            return [_text(f"{hi}I found your orders. {listing}. What would you like to do?")]
        if name == "search_help_center":
            return [_policy_answer(result, text)]
        if name == "find_products":
            return [_text(_product_answer(data, text))]
        if name == "hand_off_to_human":
            return [_text("I've passed this to our support team" + (f" (ticket {data['ticket_id']})" if "ticket_id" in data else "")
                          + ". A person will reply right here in this chat. Our team works Monday to Friday, 8 am to 6 pm Pacific time.")]
        if result.get("is_error"):
            message = data.get("message", "That isn't possible.")
            if "hand" in message.lower() and "person" in message.lower():
                reason = "damaged_item" if data.get("error") == "damaged_needs_person" else \
                    "warranty_claim" if "warranty" in message.lower() else "policy_exception"
                if reason != "policy_exception":
                    return [_call(n, "hand_off_to_human", {"reason": reason, "priority": "normal",
                                                           "summary": _summary(texts, messages, message)})]
            offer = " I can pass this to our team if you'd like them to take a look." if "person" in message.lower() else ""
            return [_text(f"I'm sorry, I can't do that. {_customer_part(message)}{offer}")]
        if name == "request_cancellation":
            return [_text(f"{hi}I've asked our team to cancel order #{call['input']['order_number']} (request {data['request_id']}). "
                          f"They usually confirm within 2 business hours, and the order won't ship while the request is open. "
                          f"If it's approved, {data['refund_if_approved']} goes back to your original payment method.")]
        if name == "request_return":
            if "exchange_for_size" in data:
                return [_text(f"{hi}I've sent your exchange request for size {data['exchange_for_size']} to our team "
                              f"(request {data['request_id']}). {data['next_step']}")]
            fee = f", after the {data['return_fee_deducted']} return shipping fee" if data.get("return_fee_deducted") else ""
            return [_text(f"{hi}I've sent your return request to our team (request {data['request_id']}). If it's approved, "
                          f"your refund will be {data['refund_if_approved']}{fee}, back to your original payment method "
                          f"5 to 7 business days after the return arrives. {data['next_step']}")]
        if name == "get_order":
            if intent == "damaged":
                return [_call(n, "hand_off_to_human", {"reason": "damaged_item", "priority": "normal", "summary": _summary(
                    texts, messages, f"Order #{data['order_number']}: customer reports an item arrived damaged. Ask for a "
                                     "photo of the item and the box.")})]
            if intent in ("return", "exchange"):
                return [self._return_call(data, texts, intent, n)]
            if data.get("policy_note", "").startswith("Tracking has not updated"):
                return [_call(n, "hand_off_to_human", {"reason": "lost_parcel", "priority": "normal", "summary": _summary(
                    texts, messages, f"Order #{data['order_number']}: {data['policy_note'].split(' Hand')[0]}")})]
            return [_text(hi + _status_answer(data))]
        return [_text(f"{hi}Is there anything else I can help with?")]

    def _return_call(self, order: dict[str, Any], texts: list[str], intent: str, n: int) -> dict[str, Any]:
        # Only what the customer said for this request counts, from the message that asked for it.
        start = max((i for i, t in enumerate(texts) if _intent(t) in ORDER_INTENTS), default=0)
        texts = texts[start:]
        said = set(re.findall(r"[a-z]+", " ".join(texts).lower()))
        named = [i for i in order["items"] if said & (set(re.findall(r"[a-z]+", i["product"].lower())) - {"the", "pack"})]
        picked = named or (order["items"] if len(order["items"]) == 1 else [])  # never guess between items
        if not picked:
            listing = "; ".join(f"{i['product']} ({i['return_status'][0].lower() + i['return_status'][1:]})"
                                for i in order["items"])
            return _text(f"Which item would you like to {'exchange' if intent == 'exchange' else 'return'}? Order "
                         f"#{order['order_number']} has: {listing}.")
        joined = " ".join(texts)
        size = SIZE.findall(joined)
        if intent == "exchange" or (size and re.search(r"\bexchange|instead\b", joined, re.I)):
            return _call(n, "request_return", {"order_number": order["order_number"], "reason": "wrong_size",
                                               "resolution": "exchange", "exchange_size": size[-1].upper() if size else "",
                                               "items": [{"line_id": picked[0]["line_id"], "quantity": 1}]})
        low = joined.lower()
        reason = "wrong_size" if re.search(r"too (?:small|big|large|tight|loose)|wrong size|doesn'?t fit|\bfit\b", low) else \
            "defective" if DEFECT.search(low) else \
            "wrong_item" if re.search(r"wrong item|not what i ordered", low) else "changed_mind"
        return _call(n, "request_return", {"order_number": order["order_number"], "reason": reason, "resolution": "refund",
                                           "items": [{"line_id": i["line_id"], "quantity": i["quantity"]} for i in picked]})


def _earlier_order_intent(texts: list[str]) -> str | None:
    return next((i for t in reversed(texts) if (i := _intent(t)) in ORDER_INTENTS), None)


def _order_call(intent: str, number: str, text: str, n: int) -> dict[str, Any]:
    if intent == "cancel":
        reason = EMAIL.sub("", text).strip()[:100]
        return _call(n, "request_cancellation", {"order_number": number, "reason": reason})
    return _call(n, "get_order", {"order_number": number})  # status, returns, exchanges, damage: open the order first


INSTRUCTION = re.compile(r"^(?:Offer|Hand|Ask|Use|Do not|Tell|Say|Say which)\b")


def _customer_part(message: str) -> str:
    """A tool's refusal without the parts addressed to the assistant ("Offer to...", "Use the...")."""
    sentences = re.split(r"(?<=\.)\s+", message)
    kept = [s for s in sentences if not INSTRUCTION.match(s)]
    return " ".join(kept) or "That isn't possible here."


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _call(n: int, name: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_guided_{n + 1:03d}", "name": name, "input": data}


def _last(pattern: re.Pattern[str], texts: list[str]) -> str:
    for t in reversed(texts):
        found = pattern.findall(t)
        if found:
            return found[-1].lower()
    return ""


def _json(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
    try:
        data = json.loads(content or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _tool_pairs(messages: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    calls = {b["id"]: b for m in messages if m["role"] == "assistant" for b in m["content"] if b.get("type") == "tool_use"}
    return [(calls[b["tool_use_id"]], b) for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_result" and b.get("tool_use_id") in calls]


def _verified(messages: list[dict[str, Any]]) -> set[str]:
    """Order numbers of the verified customer, from earlier verify_customer results."""
    orders: set[str] = set()
    for call, result in _tool_pairs(messages):
        if call["name"] == "verify_customer" and not result.get("is_error"):
            orders = {o["order_number"] for o in _json(result).get("orders", [])}
    return orders


def _first_name(messages: list[dict[str, Any]]) -> str:
    name = ""
    for call, result in _tool_pairs(messages):
        if call["name"] == "verify_customer" and not result.get("is_error"):
            name = _json(result).get("first_name", "")
    return name


def _summary(texts: list[str], messages: list[dict[str, Any]], what: str) -> str:
    who = _first_name(messages)
    said = EMAIL.sub("[email]", texts[-1])[:160] if texts else ""
    return f"{what} " + (f"Verified customer: {who}. " if who else "Customer not verified. ") + (f'Last message: "{said}"' if said else "")


def _policy_answer(result: dict[str, Any], question: str) -> dict[str, Any]:
    from .helpcenter import words
    blocks = [b for b in result.get("content") or [] if isinstance(b, dict) and b.get("type") == "search_result"]
    if not blocks:
        return _text("I'm not sure about that one, and I don't want to guess. I can pass your question to our team if you like.")
    top = blocks[0]
    all_sentences = [c["text"] for c in top["content"]]
    asked = set(words(question))
    ranked = sorted(range(len(all_sentences)), key=lambda i: (-len(asked & set(words(all_sentences[i]))), i))
    sentences = [all_sentences[i] for i in sorted(ranked[:2])]  # the two that match best, in reading order
    return {"type": "text", "text": " ".join(sentences),
            "citations": [{"type": "search_result_location", "source": top["source"], "title": top["title"],
                           "cited_text": " ".join(sentences)}]}


def _product_answer(data: dict[str, Any], question: str) -> str:
    products = data.get("products") or []
    if not products:
        return "I couldn't find that product in our catalog. Could you tell me a bit more about what you're looking for?"
    p = products[0]
    asked = set(re.findall(r"[a-z]+", question.lower())) - {"the", "is", "it", "a", "does", "do", "you", "have"}
    specs = [s for s in p["specs"] if asked & set(re.findall(r"[a-z]+", s.lower()))] or p["specs"][:2]
    stock = (f" In stock: {', '.join(p['in_stock'])}." if p["in_stock"] and p["in_stock"] != ["One size"] else
             " It's in stock." if p["in_stock"] else
             f" It's out of stock right now{', restock expected ' + p['restock_expected'] if p.get('restock_expected') else ''}.")
    out = f" Out of stock: {', '.join(p['out_of_stock'])}." if p["in_stock"] and p["out_of_stock"] else ""
    return f"The {p['product']} is {p['price']}. {'. '.join(s.rstrip('.') for s in specs[:2])}.{stock}{out}"


def _status_answer(order: dict[str, Any]) -> str:
    items = ", ".join(i["product"] for i in order["items"])
    head = f"Order #{order['order_number']} ({items}): {order['status']}."
    track = order.get("tracking")
    if track:
        latest = track["events"][-1]
        head += f" {track['carrier']} tracking {track['tracking_number']}, latest update {latest['date']}: {latest['event']}."
    if order.get("policy_note"):
        head += " " + order["policy_note"].removeprefix("Past the estimated delivery date. ")
    return head


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] == "assistant":
            return " ".join(b.get("text", "") for b in m["content"] if b.get("type") == "text")
    return ""
