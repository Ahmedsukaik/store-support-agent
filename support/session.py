"""A demo chat is a list of events, kept by the browser. Every request replays them.

    customer   {"type": "customer", "text": "..."}
    assistant  {"type": "assistant", "content": [text / tool_use blocks], "stop_reason", "model", "mode", "usage", "ms"}
    team       {"type": "team", "action": "approve" | "reject" | "reply" | "return_to_ai", ...}

The server signs the event list (an HMAC with a secret only the server knows) and refuses a
chat whose signature does not match, so the browser cannot add or edit events. Even so, tool
results are never stored: on replay every tool call runs again, through the same rules,
against a fresh copy of the store, so the state always follows from the rules.

Nothing is kept on the server between requests, so visitors never see each other's chats.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .checks import HELD_REPLY, amounts, check_reply, tool_source_text
from .demo_data import money, nice_date
from .helpcenter import HelpCenter, default_help_center
from .models import cost_usd
from .store import StoreError, StoreState, mask_email
from .tools import TOOL_NAMES, run_tool

log = logging.getLogger("support.session")
MAX_EVENTS = 160
MAX_CUSTOMER_MESSAGES = 25
MAX_TEXT = 800
MAX_STEPS = 6              # model calls per customer message; then the chat goes to a person
MAX_TOOL_CALLS = 6         # tool calls in one model step
MAX_BLOCKS = 40
MAX_REPLY_TEXT = 8000      # characters of text in one model step
MAX_TOOL_INPUT = 3000
TOOL_ID = re.compile(r"toolu_[A-Za-z0-9_]{1,60}")
SYSTEM_TAG = re.compile(r"</?\s*(?:team_message|store_update)\b[^>]*>?", re.I)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2060-\u2064\ufeff\ud800-\udfff]")
SIG = re.compile(r"[0-9a-f]{64}")
CUT_OFF = ("refusal", "max_tokens", "model_context_window_exceeded")
TOO_MANY_STEPS = ("I'm taking too long on this one, so I've passed our chat to the support team. A person will reply right "
                  "here. Our team works Monday to Friday, 8 am to 6 pm Pacific time.")
HANDED_OFF = ("I've passed our chat to the support team. A person will reply right here. Our team works Monday to "
              "Friday, 8 am to 6 pm Pacific time.")


class SessionError(Exception):
    """The session cannot be continued (edited, too long, or broken). The page starts a new chat."""


# ---------------------------------------------------------------------------- signing
def _secret() -> bytes:
    configured = os.environ.get("SUPPORT_SESSION_SECRET", "")
    if len(configured) >= 32:
        return configured.encode()
    if os.environ.get("VERCEL"):  # several instances: a random secret per instance would end chats at random
        raise RuntimeError("Set SUPPORT_SESSION_SECRET (32 characters or more) for this deployment.")
    log.warning("SUPPORT_SESSION_SECRET is not set (or shorter than 32 characters): using a random secret for this "
                "process, so chats end when it restarts. Set it when running more than one instance.")
    return secrets.token_bytes(32)


_SECRET = _secret()


def signature(session: dict[str, Any]) -> str:
    body = json.dumps({"v": session.get("v"), "started": session.get("started"), "events": session.get("events")},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()


def signed(session: dict[str, Any]) -> dict[str, Any]:
    out = {"v": session["v"], "started": session["started"], "events": session["events"]}
    out["sig"] = signature(out)
    return out


@dataclass
class Replay:
    state: StoreState
    help_center: HelpCenter
    messages: list[dict[str, Any]] = field(default_factory=list)   # finished user/assistant messages
    pending: list[dict[str, Any]] = field(default_factory=list)    # content of the next user message
    transcript: list[dict[str, Any]] = field(default_factory=list)
    source_amounts: set[int] = field(default_factory=set)           # amounts the store gave the assistant, for the checks
    team_amounts: set[int] = field(default_factory=set)             # amounts in the team's messages
    steps: list[dict[str, Any]] = field(default_factory=list)       # tool calls since the last reply
    turn_cost: float = 0.0                                          # cost of the model calls since the last reply
    tool_ids: set[str] = field(default_factory=set)
    steps_since_customer: int = 0
    customer_messages: int = 0
    awaiting: bool = False   # the last assistant step called tools: the model must be called again
    stats: dict[str, Any] = field(default_factory=lambda: {"live_calls": 0, "guided_calls": 0, "input_tokens": 0,
                                                           "output_tokens": 0, "cache_read_input_tokens": 0,
                                                           "cache_creation_input_tokens": 0, "cost_usd": 0.0,
                                                           "ms": [], "tool_calls": 0, "held": 0, "replies": 0})

    def next_messages(self) -> list[dict[str, Any]]:
        """The messages to send to the model now."""
        if not self.pending:
            raise SessionError("nothing to answer")
        return self.messages + [{"role": "user", "content": list(self.pending)}]

    def last_customer_text(self) -> str:
        return next((t["text"] for t in reversed(self.transcript) if t["kind"] == "customer"), "")


def new_session(today: date | None = None) -> dict[str, Any]:
    return signed({"v": 1, "started": (today or date.today()).isoformat(), "events": []})


def start(session: Any, help_center: HelpCenter | None = None, today: date | None = None, verify: bool = True) -> Replay:
    """Check the session's envelope and signature, and replay its events. `verify=False` is
    for tests of the replay rules themselves."""
    if not isinstance(session, dict) or session.get("v") != 1 or not isinstance(session.get("events"), list):
        raise SessionError("not a demo session")
    if verify:
        sig = session.get("sig")
        try:
            valid = isinstance(sig, str) and SIG.fullmatch(sig) is not None and \
                hmac.compare_digest(sig.encode(), signature(session).encode())
        except ValueError:  # NaN or another value JSON cannot carry
            valid = False
        if not valid:
            raise SessionError("the chat was changed outside this server")
    try:
        started = date.fromisoformat(str(session.get("started")))
    except ValueError as e:
        raise SessionError("bad start date") from e
    today = today or date.today()
    if not today - timedelta(days=30) <= started <= today + timedelta(days=1):
        raise SessionError("this chat is too old")
    if len(session["events"]) > MAX_EVENTS:
        raise SessionError("this chat is too long")
    replay = Replay(StoreState.new(started), help_center or default_help_center())
    for event in session["events"]:
        apply(replay, event)
    return replay


def apply(r: Replay, event: Any, strict: bool = False) -> None:
    """Apply one event. `strict` (a new team action) raises StoreError instead of skipping it."""
    if not isinstance(event, dict):
        raise SessionError("bad event")
    r.state.step = len(r.transcript)
    kind = event.get("type")
    if kind == "customer":
        _customer(r, event)
    elif kind == "assistant":
        _assistant(r, event)
    elif kind == "team":
        _team(r, event, strict)
    else:
        raise SessionError("unknown event")


def strip_tags(text: str) -> str:
    """Remove control and invisible characters, then the tags only the system may use, until
    none is left: "<team_<team_message>message>" must not turn into a tag after one pass."""
    text = CONTROL.sub("", text)
    previous = None
    while previous != text:
        previous, text = text, SYSTEM_TAG.sub("", text)
    return text.strip()


def clean_customer_text(text: Any) -> str:
    if not isinstance(text, str):
        raise SessionError("bad message")
    text = strip_tags(text).replace("<", "\u2039").replace(">", "\u203a")  # nothing tag-like reaches the model
    if not text or len(text) > MAX_TEXT:
        raise SessionError("message is empty or too long")
    return text


def _customer(r: Replay, event: dict[str, Any]) -> None:
    text = clean_customer_text(event.get("text"))
    r.customer_messages += 1
    if r.customer_messages > MAX_CUSTOMER_MESSAGES:
        raise SessionError("this chat has reached its message limit")
    paused = r.state.ai_paused
    r.pending.append({"type": "text", "text": text})
    r.transcript.append({"kind": "customer", "text": text, "to_team": paused})
    r.steps_since_customer, r.awaiting = 0, False
    if paused:
        r.state.log("customer", "message to the team", text)


def _validate_blocks(r: Replay, content: Any, allow_empty: bool) -> list[dict[str, Any]]:
    if not isinstance(content, list) or len(content) > MAX_BLOCKS:
        raise SessionError("bad assistant turn")
    out, ids, text_size = [], set(), 0
    for b in content:
        if not isinstance(b, dict):
            raise SessionError("bad block")
        if b.get("type") == "text":
            text = b.get("text")
            if not isinstance(text, str):
                raise SessionError("bad text block")
            if not text.strip():
                continue  # the API refuses blank text when it is sent back
            text_size += len(text)
            block: dict[str, Any] = {"type": "text", "text": text}
            cites = b.get("citations")
            if isinstance(cites, list):
                block["citations"] = [{"type": "search_result_location", "source": str(c.get("source", ""))[:120],
                                       "title": str(c.get("title", ""))[:160], "cited_text": str(c.get("cited_text", ""))[:600]}
                                      for c in cites[:8] if isinstance(c, dict)]
            out.append(block)
        elif b.get("type") == "tool_use":
            tid, name, data = b.get("id"), b.get("name"), b.get("input")
            if not isinstance(tid, str) or not TOOL_ID.fullmatch(tid) or tid in r.tool_ids or tid in ids:
                raise SessionError("bad tool id")
            if name not in TOOL_NAMES or not isinstance(data, dict) or len(json.dumps(data)) > MAX_TOOL_INPUT:
                raise SessionError("bad tool call")
            ids.add(tid)
            out.append({"type": "tool_use", "id": tid, "name": name, "input": data})
        else:
            raise SessionError("bad block type")
    if text_size > MAX_REPLY_TEXT or len(ids) > MAX_TOOL_CALLS or (not out and not allow_empty):
        raise SessionError("bad assistant turn")
    r.tool_ids |= ids
    return out


def _assistant(r: Replay, event: dict[str, Any]) -> None:
    if not r.pending:
        raise SessionError("assistant turn without anything to answer")
    if r.steps_since_customer >= MAX_STEPS:
        raise SessionError("too many steps")
    stop = str(event.get("stop_reason") or "end_turn")
    cut_off = stop in CUT_OFF
    blocks = _validate_blocks(r, event.get("content"), allow_empty=cut_off)
    if cut_off:  # a refusal or a cut off reply: nothing in it is used, tool calls included
        blocks = [b for b in blocks if b["type"] == "text"]
    r.steps_since_customer += 1
    r.messages.append({"role": "user", "content": r.pending})
    r.pending = []
    r.messages.append({"role": "assistant", "content": [{k: v for k, v in b.items() if k != "citations"} for b in blocks]
                       or [{"type": "text", "text": HELD_REPLY}]})
    mode = "live" if event.get("mode") == "live" else "guided"
    model = str(event.get("model") or mode)[:60]
    raw_usage = event.get("usage")
    usage = {str(k): int(v) for k, v in raw_usage.items()
             if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10_000_000} if isinstance(raw_usage, dict) else {}
    _count(r, mode, model, usage, event.get("ms"))
    if isinstance(event.get("note"), str):
        r.transcript.append({"kind": "system", "text": event["note"][:300]})
    calls = [b for b in blocks if b["type"] == "tool_use"]
    if calls:
        was_paused = r.state.ai_paused
        for call in calls:
            result = run_tool(r.state, r.help_center, call["name"], call["input"])
            r.pending.append({"type": "tool_result", "tool_use_id": call["id"], "content": result.content,
                              **({"is_error": True} if result.is_error else {})})
            if not result.is_error:  # a refusal can repeat what the model sent; it is not a source
                r.source_amounts |= amounts(tool_source_text(result.content))
            r.steps.append({"tool": call["name"], "label": result.label, "ok": not result.is_error})
            r.stats["tool_calls"] += 1
        r.awaiting = True
        if r.state.ai_paused and not was_paused:
            # Handed to a person (by the model, or by the code after a third failed verification): the code
            # says so, and the model is not called again while a person has the chat.
            _close_turn(r, HANDED_OFF)
            _reply(r, HANDED_OFF, [], mode, model)
        elif r.steps_since_customer >= MAX_STEPS:
            r.state.hand_off("too_many_steps", "The assistant needed more than six steps for one message.", "normal", source="system")
            _close_turn(r, TOO_MANY_STEPS)
            _reply(r, TOO_MANY_STEPS, [], mode, model, held=True, problems=["needed more than six steps for one message"])
        return
    text = "".join(b["text"] for b in blocks if b["type"] == "text").strip()
    citations = [c for b in blocks if b["type"] == "text" for c in b.get("citations", [])]
    approved = {req["kind"] for req in r.state.requests if req["status"] == "approved"}
    problems: list[str]
    checked: list[str]
    if stop == "refusal":
        problems, checked = ["the model declined to answer"], []
    elif cut_off:
        problems, checked = ["the reply was cut off"], []
    elif not text:
        problems, checked = ["the reply was empty"], []
    else:
        store_refunds = {q["amount"] for q in r.state.requests if q["amount"]} | \
            {q["details"]["fee"] for q in r.state.requests if q["kind"] == "return" and q["details"].get("fee")} | r.team_amounts
        verdict = check_reply(text, r.source_amounts | r.team_amounts, approved, store_refunds)
        problems, checked = verdict.problems, verdict.checked
    if problems:
        reason = "model_refused" if stop == "refusal" else "reply_held"
        r.state.hand_off(reason, f"The assistant's reply was held: it {problems[0]}.", "normal", source="system",
                         draft=text or None)
        r.messages[-1]["content"] = [{"type": "text", "text": HELD_REPLY}]  # what the customer actually saw
        r.stats["held"] += 1
        _reply(r, HELD_REPLY, [], mode, model, held=True, problems=problems, draft=text or None)
    else:
        _reply(r, text, citations, mode, model, checked=checked)
    r.awaiting = False


def _close_turn(r: Replay, text: str) -> None:
    """Record the tool results and the reply the code wrote, so the next customer message starts a
    new turn (instead of following tool results the model never answered)."""
    r.messages += [{"role": "user", "content": r.pending}, {"role": "assistant", "content": [{"type": "text", "text": text}]}]
    r.pending = []
    r.awaiting = False


def _count(r: Replay, mode: str, model: str, usage: dict[str, int], ms: Any) -> None:
    s = r.stats
    s["live_calls" if mode == "live" else "guided_calls"] += 1
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        s[k] += usage.get(k, 0)
    cost = cost_usd(model, usage) if mode == "live" else 0.0
    s["cost_usd"] += cost
    r.turn_cost += cost
    if isinstance(ms, int) and 0 <= ms < 600_000 and mode == "live":
        s["ms"].append(ms)


def _reply(r: Replay, text: str, citations: list[dict[str, Any]], mode: str, model: str,
           held: bool = False, problems: list[str] | None = None, draft: str | None = None,
           checked: list[str] | None = None) -> None:
    seen, sources = set(), []
    for c in citations:
        key = (c["source"], c["cited_text"])
        if key not in seen:
            seen.add(key)
            sources.append({"title": c["title"], "source": c["source"], "quote": c["cited_text"]})
    r.transcript.append({"kind": "assistant", "text": text, "sources": sources, "steps": r.steps, "mode": mode,
                         "model": model, "held": held, "problems": problems or [], "draft": draft,
                         "checks": checked or [], "cost_usd": round(r.turn_cost, 6)})  # every model call behind it
    r.steps, r.turn_cost = [], 0.0
    r.stats["replies"] += 1


def _team(r: Replay, event: dict[str, Any], strict: bool) -> None:
    action = event.get("action")
    state = r.state
    before = len(state.notices)
    try:
        if action in ("approve", "reject"):
            request = state.decide(str(event.get("request_id", ""))[:12], action == "approve", str(event.get("note") or ""))
            update = (f"<store_update>The team {request['status']} request {request['id']} "
                      f"({request['kind'].replace('_', ' ')}, order #{request['order']}).</store_update>")
        elif action == "reply":
            if state.open_ticket() is None:
                raise StoreError("no_open_ticket", "Replies are for chats that are with the team.")
            state.team_reply(strip_tags(str(event.get("text") or "")))
            update = f"<team_message>{state.notices[-1]['text']}</team_message>"
        elif action == "return_to_ai":
            state.return_to_assistant()
            update = "<store_update>The team handed the chat back to you.</store_update>"
        else:
            raise SessionError("unknown team action")
    except StoreError:
        if strict:
            raise
        state.log("team", str(action), "replayed action refused", "refused")
        return
    for notice in state.notices[before:]:
        r.transcript.append({"kind": "notice", "notice": notice["kind"], "text": notice["text"]})
        r.team_amounts |= amounts(notice["text"])  # what the team says counts, refunds included
    r.pending.append({"type": "text", "text": update})


# ---------------------------------------------------------------------------- what the page shows
def view(r: Replay) -> dict[str, Any]:
    state = r.state
    transcript = list(r.transcript)
    if r.awaiting:
        transcript.append({"kind": "working", "steps": r.steps})
    s = r.stats
    ms = s["ms"]
    requests = []
    for req in state.requests:
        order = state.orders[req["order"]]
        item = {k: req[k] for k in ("id", "kind", "order", "status", "flags", "checks", "note")}
        item["customer"] = f"{order['address']['name']} ({mask_email(order['email'])})"
        item["amount"] = money(req["amount"]) if req["amount"] is not None else None
        d = req["details"]
        if req["kind"] == "return":
            item["title"] = ("Exchange" if d["resolution"] == "exchange" else "Return") + f" for order #{req['order']}"
            item["lines"] = [f"{li['qty']} x {li['product']} ({li['size']}), {money(li['price'])}" for li in d["lines"]]
            item["detail"] = (f"Reason: {d['reason'].replace('_', ' ')}. " +
                              (f"Exchange for size {d['exchange_size']}." if d["resolution"] == "exchange" else
                               f"Refund {money(req['amount'])}" + (f" after the {money(d['fee'])} return fee." if d["fee"] else ".")))
        elif req["kind"] == "cancellation":
            item["title"] = f"Cancel order #{req['order']}"
            item["lines"] = [f"{li['qty']} x {li['name']}, {money(li['price'])}" for li in order["lines"]]
            item["detail"] = f"Customer's reason: {d['reason'] or 'not given'}. Full refund {money(req['amount'])}."
        else:
            item["title"] = f"New address for order #{req['order']}"
            item["lines"] = [f"From: {state.address_line(d['old'])}", f"To: {state.address_line(d['new'])}"]
            item["detail"] = "Check the new address with the customer if anything looks unusual."
        requests.append(item)
    orders = [{"number": o["number"], "customer": o["address"]["name"], "placed": nice_date(o["placed"]),
               "status": state.status_text(o), "total": money(o["total"]),
               "items": [f"{li['qty']} x {li['name']}" + (f" ({li['returned_qty']} return approved)" if li["returned_qty"] else "")
                         for li in o["lines"]]}
              for o in sorted(state.orders.values(), key=lambda o: o["number"])]
    return {
        "transcript": transcript, "awaiting": r.awaiting, "ai_paused": state.ai_paused,
        "verified": mask_email(state.verified_email) if state.verified_email else None,
        "requests": requests, "tickets": state.tickets, "orders": orders, "audit": state.audit[-100:],
        "metrics": {"customer_messages": r.customer_messages, "replies": s["replies"], "tool_calls": s["tool_calls"],
                    "live_calls": s["live_calls"], "guided_calls": s["guided_calls"], "held": s["held"],
                    "input_tokens": s["input_tokens"] + s["cache_read_input_tokens"] + s["cache_creation_input_tokens"],
                    "cached_tokens": s["cache_read_input_tokens"], "output_tokens": s["output_tokens"],
                    "cost_usd": round(s["cost_usd"], 5), "avg_ms": int(sum(ms) / len(ms)) if ms else None,
                    "handed_off": bool(state.tickets),
                    "pending": sum(1 for q in state.requests if q["status"] == "pending")},
    }
