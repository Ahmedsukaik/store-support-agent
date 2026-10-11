from __future__ import annotations

import socket
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from support.agent import Agent  # noqa: E402
from support.models import GuidedModel, Turn  # noqa: E402
from support.session import new_session  # noqa: E402
from support.store import StoreState  # noqa: E402

TODAY = date(2026, 10, 12)  # a Monday; the store's dates are counted back from it
MAYA = "maya.lopez@example.com"
DANIEL = "daniel.kim@example.com"
PRIYA = "priya.shah@example.com"
SAM = "sam.okafor@example.com"
ELENA = "elena.rossi@example.com"


@pytest.fixture
def state() -> StoreState:
    return StoreState.new(TODAY)


@pytest.fixture
def maya(state: StoreState) -> StoreState:
    state.verify_customer("1042", MAYA)
    return state


class Chat:
    """Drives an Agent the way the page does: send, then 'continue' while more is true."""

    def __init__(self, agent: Agent | None = None, live: bool = True):
        self.agent = agent or Agent()
        self.live = live
        self.session: dict[str, Any] = new_session(TODAY)
        self.last: dict[str, Any] = {}

    def do(self, action: dict[str, Any], allowed: bool = True) -> dict[str, Any]:
        self.last = self.agent.handle(self.session, action, want_live=self.live, visitor_allowed=allowed, today=TODAY)
        self.session = self.last["session"]
        return self.last

    def say(self, text: str, allowed: bool = True) -> dict[str, Any]:
        out = self.do({"type": "customer", "text": text}, allowed)
        guard = 0
        while out["more"] and guard < 10:
            out = self.do({"type": "continue"}, allowed)
            guard += 1
        return out

    def team(self, action: str, **extra: Any) -> dict[str, Any]:
        return self.do({"type": "team", "action": action, **extra})

    @property
    def view(self) -> dict[str, Any]:
        return self.last["view"]

    def replies(self) -> list[dict[str, Any]]:
        return [t for t in self.view["transcript"] if t["kind"] == "assistant"]

    def reply(self) -> str:
        return self.replies()[-1]["text"]

    def steps(self) -> list[str]:
        return [s["tool"] for s in self.replies()[-1]["steps"]]


@pytest.fixture
def chat() -> Chat:
    return Chat()


class ScriptedModel:
    """Stands in for Claude: returns prepared turns in order. Each item is a list of blocks, a
    Turn, an exception, or a function of the messages that returns one of those."""

    mode = "live"
    name = "claude-haiku-5-5"

    def __init__(self, *turns: Any):
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def respond(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Turn:
        self.calls.append(messages)
        item = self.turns.pop(0) if self.turns else [{"type": "text", "text": "Is there anything else I can help with?"}]
        if callable(item) and not isinstance(item, Turn):
            item = item(messages)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Turn):
            return item
        stop = "tool_use" if any(b["type"] == "tool_use" for b in item) else "end_turn"
        return Turn(item, stop, self.name, "live", {"input_tokens": 1200, "output_tokens": 60,
                                                     "cache_read_input_tokens": 2400}, 900)


def tool(n: int, name: str, **data: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_test_{n:03d}", "name": name, "input": data}


def text(t: str, **extra: Any) -> dict[str, Any]:
    return {"type": "text", "text": t, **extra}


def last_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in messages[-1]["content"] if b.get("type") == "tool_result"]


# ------------------------------------------------------------------ a real server in a thread
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve(app: Any, port: int) -> Any:
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    return server


GUIDED = GuidedModel()
