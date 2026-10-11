"""One request from the page: replay the chat, apply one action, call the model once if needed.

The browser drives the tool loop: when a step ends with tool calls, the response says
`more: true` and the page asks for the next step. Each request is short (one model call at
most), the page can show each tool call as it happens, and no request holds a server open.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .helpcenter import HelpCenter
from .models import BAD_DRAFT, SIMULATION, GuidedModel, Model, ModelUnavailable, Turn
from .prompts import SYSTEM_PROMPT
from .session import (
    MAX_CUSTOMER_MESSAGES,
    MAX_EVENTS,
    MAX_STEPS,
    Replay,
    SessionError,
    apply,
    clean_customer_text,
    signed,
    start,
    view,
)
from .store import StoreError
from .tools import TOOLS

# How long the live model rests after a failure, for every visitor of this server instance.
# A request the API refused as invalid ("invalid") is a problem of that one chat: no rest.
COOLDOWN = {"budget": 15 * 60, "auth": 15 * 60, "rate_limited": 30, "error": 30}
MAX_PROMPT_CHARS = 120_000  # about 30K tokens: far below Haiku 5.5's higher price above 100K
NOTES = {
    "budget": "The live AI is resting for now, so fixed rules are answering. Checks and approvals work the same way.",
    "auth": "The live AI is unavailable right now, so fixed rules are answering. Checks and approvals work the same way.",
    "rate_limited": "The live AI is busy, so fixed rules answered this one. Checks and approvals work the same way.",
    "error": "The live AI didn't answer in time, so fixed rules answered this one. Checks and approvals work the same way.",
    "invalid": "The live AI couldn't take this step, so fixed rules answered it. Checks and approvals work the same way.",
    "empty": "The live AI sent an empty reply, so fixed rules answered this one.",
    "visitor_limit": "You've sent a lot of messages, so fixed rules answer for now. Checks and approvals work the same way.",
    "too_long": "This chat is long for the live demo, so fixed rules answer from here. Start a new chat for the live AI.",
    "busy": "The live demo is very busy, so fixed rules are answering. Checks and approvals work the same way.",
}


class ActionError(Exception):
    """The action is not possible now (for example approving a request twice). Shown to the visitor."""


@dataclass
class Agent:
    live: Model | None = None
    guided: Model = field(default_factory=GuidedModel)
    help_center: HelpCenter | None = None
    _down_until: float = 0.0
    _down_kind: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def live_available(self) -> bool:
        return self.live is not None and time.monotonic() >= self._down_until

    def handle(self, session: Any, action: Any, want_live: bool = True, visitor_allowed: bool = True,
               today: date | None = None, limit_note: str = "visitor_limit") -> dict[str, Any]:
        replay = start(session, self.help_center, today)
        events = list(session["events"])
        kind = action.get("type") if isinstance(action, dict) else None
        call_model = False
        if kind in ("customer", "team") and len(events) > MAX_EVENTS - MAX_STEPS - 2:
            raise ActionError("This demo chat is as long as it can get. Start a new chat to keep trying.")
        if kind == "customer":
            if replay.customer_messages >= MAX_CUSTOMER_MESSAGES:
                raise ActionError(f"This demo chat has reached its limit of {MAX_CUSTOMER_MESSAGES} messages. "
                                  "Start a new chat to keep trying.")
            try:
                event = {"type": "customer", "text": clean_customer_text(action.get("text"))}  # stored clean
                apply(replay, event)
            except SessionError as e:  # a bad new message is refused; the chat itself stays
                raise ActionError("That message can't be sent. Write it again in plain words.") from e
            events.append(event)
            call_model = not replay.state.ai_paused
        elif kind == "continue":
            if not replay.awaiting:
                raise ActionError("There is no reply in progress.")
            call_model = True
        elif kind == "view":  # the page reloaded: just show the chat again
            pass
        elif kind == "team":
            event = {k: str(action.get(k))[:1000] for k in ("action", "request_id", "note", "text")
                     if isinstance(action.get(k), str)}  # strings only: they sign and round trip exactly
            event["type"] = "team"
            try:
                apply(replay, event, strict=True)
            except StoreError as e:
                raise ActionError(e.message) from e
            events.append(event)
        else:
            raise ActionError("Unknown action.")
        if call_model and kind == "customer" and action.get("simulate") == "bad_draft":
            # The demo shows the reply checks at work: a draft that invents a credit, written on purpose.
            draft: dict[str, Any] = {"type": "assistant", "content": [{"type": "text", "text": BAD_DRAFT}], "stop_reason": "end_turn",
                     "model": "simulation", "mode": "guided", "usage": {}, "ms": 0, "simulated": True}
            apply(replay, draft)
            events.append(draft)
        elif call_model:
            events.append(self._step(replay, want_live, visitor_allowed, limit_note))
        return {"session": signed({"v": 1, "started": session["started"], "events": events}), "view": view(replay),
                "more": replay.awaiting}

    def _step(self, replay: Replay, want_live: bool, visitor_allowed: bool, limit_note: str = "visitor_limit") -> dict[str, Any]:
        messages = replay.next_messages()
        note = None
        turn: Turn | None = None
        if replay.last_customer_text() == SIMULATION:
            want_live = False  # the simulated bad draft always comes from the guided rules
        if want_live and self.live is not None:
            if not visitor_allowed:
                note = NOTES.get(limit_note, NOTES["visitor_limit"])
            elif len(json.dumps(messages)) > MAX_PROMPT_CHARS:
                note = NOTES["too_long"]
            elif not self.live_available():
                note = NOTES.get(self._down_kind, NOTES["error"])
            else:
                try:
                    turn = self.live.respond(SYSTEM_PROMPT, TOOLS, messages)
                except ModelUnavailable as e:
                    if e.kind in COOLDOWN:
                        with self._lock:
                            self._down_until = time.monotonic() + COOLDOWN[e.kind]
                            self._down_kind = e.kind
                    note = NOTES.get(e.kind, NOTES["error"])
                if turn is not None and not turn.content and turn.stop_reason not in ("refusal", "max_tokens",
                                                                                       "model_context_window_exceeded"):
                    turn, note = None, NOTES["empty"]
        if turn is None:
            turn = self.guided.respond(SYSTEM_PROMPT, TOOLS, messages)
        event: dict[str, Any] = {"type": "assistant", "content": turn.content, "stop_reason": turn.stop_reason,
                                 "model": turn.model, "mode": turn.mode, "usage": turn.usage, "ms": turn.ms}
        if note and not _already_noted(replay, note):
            event["note"] = note
        try:
            apply(replay, event)
        except SessionError:
            # The model produced something the session refuses (too many tool calls, an odd block). Answer
            # this step with the guided rules instead, and say so.
            event = {"type": "assistant", "content": self.guided.respond(SYSTEM_PROMPT, TOOLS, messages).content,
                     "stop_reason": "end_turn", "model": "guided", "mode": "guided", "usage": {}, "ms": 0}
            if not _already_noted(replay, NOTES["invalid"]):
                event["note"] = NOTES["invalid"]
            apply(replay, event)
        return event


def _already_noted(replay: Replay, note: str) -> bool:
    return any(item.get("kind") == "system" and item.get("text") == note for item in replay.transcript[-6:])
