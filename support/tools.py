"""The assistant's tools: their definitions for the model, and the code that runs them.

Every call goes through `run_tool`, which treats the input as untrusted: it may come from a
model that was talked into something, or from a browser that edited its own session. The
store (store.py) decides what is allowed; this module only translates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .demo_data import money
from .helpcenter import HelpCenter, Passage
from .store import HANDOFF_REASONS, RETURN_REASONS, StoreError, StoreState, mask_email, normalize_order_number

ORDER = {"type": "string", "description": "The order number, for example 1042."}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}


TOOLS: list[dict[str, Any]] = [
    _tool("search_help_center",
          "Search the store's help center (shipping, returns, exchanges, damaged items, warranty, order changes, "
          "payments, sizing, product care, support hours). Returns passages to quote and cite. Use it for every "
          "policy question, and answer only from what it returns.",
          {"query": {"type": "string", "description": "A short search query in plain words."}}, ["query"]),
    _tool("find_products",
          "Look up products in the catalog: price, specs, sizes in stock, final sale, restock date.",
          {"query": {"type": "string", "description": "Product name or what the customer is looking for."}}, ["query"]),
    _tool("verify_customer",
          "Verify the customer before showing or changing any order: the order number and the email used for that "
          "order must match. On success it returns the customer's first name and their orders. Never reveal order "
          "details before this succeeds. After 3 failed attempts it is locked for this chat.",
          {"order_number": ORDER, "email": {"type": "string", "description": "The email address used for the order."}},
          ["order_number", "email"]),
    _tool("get_order",
          "Get one of the verified customer's orders: items with line_id and return status, totals, shipping "
          "address, payment method, tracking events and any policy note. Only works for the verified customer's orders.",
          {"order_number": ORDER}, ["order_number"]),
    _tool("request_return",
          "Ask the team to approve a return or an exchange for items of a delivered order. Not for items that "
          "arrived damaged (hand those to a person). The store checks the return window, final sale items and "
          "stock, and computes the refund; you never state an amount the result does not give. A person approves "
          "every request, so it is never done yet.",
          {"order_number": ORDER,
           "items": {"type": "array", "description": "Items to return, using line_id values from get_order.",
                     "items": {"type": "object", "additionalProperties": False, "required": ["line_id", "quantity"],
                               "properties": {"line_id": {"type": "string"}, "quantity": {"type": "integer"}}}},
           "reason": {"type": "string", "enum": list(RETURN_REASONS)},
           "resolution": {"type": "string", "enum": ["refund", "exchange"]},
           "exchange_size": {"type": "string", "description": "For an exchange: the size the customer wants instead."}},
          ["order_number", "items", "reason", "resolution"]),
    _tool("request_cancellation",
          "Ask the team to cancel an order that has not shipped yet. A person confirms it, so it is not cancelled yet.",
          {"order_number": ORDER, "reason": {"type": "string", "description": "The customer's reason, in a few words."}},
          ["order_number", "reason"]),
    _tool("request_address_change",
          "Ask the team to change the shipping address of an order that has not shipped yet. Collect the full new "
          "address from the customer first. A person confirms it.",
          {"order_number": ORDER,
           "address": {"type": "object", "additionalProperties": False,
                       "required": ["name", "line1", "line2", "city", "region", "postal_code", "country"],
                       "properties": {"name": {"type": "string"}, "line1": {"type": "string"},
                                      "line2": {"type": "string", "description": "Apartment or unit; an empty string "
                                                                                "if the customer gave none (do not ask)."},
                                      "city": {"type": "string"},
                                      "region": {"type": "string", "description": "State or province code, e.g. OR or BC."},
                                      "postal_code": {"type": "string"},
                                      "country": {"type": "string", "enum": ["US", "CA"]}}}},
          ["order_number", "address"]),
    _tool("hand_off_to_human",
          "Pass the conversation to a person on the support team, with a summary they can act on without "
          "re-reading the chat. After this, the team replies in the chat and you stop answering.",
          {"reason": {"type": "string", "enum": list(HANDOFF_REASONS)},
           "summary": {"type": "string", "description": "Who the customer is (if verified), the order, what they want, "
                                                         "and what you already checked. Two or three sentences."},
           "priority": {"type": "string", "enum": ["normal", "urgent"]}},
          ["reason", "summary", "priority"]),
]
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


@dataclass
class ToolResult:
    content: str | list[dict[str, Any]]   # what goes back to the model in the tool_result block
    is_error: bool
    label: str                             # one line for the page, e.g. "Opened order #1043"
    passages: list[Passage] = field(default_factory=list)

    def text(self) -> str:
        """Plain text of the result, for the checks that compare a reply with its sources."""
        if isinstance(self.content, str):
            return self.content
        return " ".join(p.text() for p in self.passages)


def _s(value: Any, limit: int = 300) -> str:
    return value[:limit] if isinstance(value, str) else ""


def run_tool(state: StoreState, help_center: HelpCenter, name: str, raw_input: Any) -> ToolResult:
    data = raw_input if isinstance(raw_input, dict) else {}
    if name.startswith("request_") and state.ai_paused:
        state.log("assistant", name[:40], "the chat is with the team", "refused")
        return ToolResult(json.dumps({"error": "with_team", "message": "The chat is with a person on the team now. Do not "
                                      "start requests; the team handles it."}), True, "Refused: the chat is with the team.")
    try:
        if name == "search_help_center":
            query = _s(data.get("query"), 200)
            passages = help_center.search(query)
            state.log("assistant", "search_help_center", query[:80], f"{len(passages)} passage(s)")
            if not passages:
                return ToolResult("No help center article matched. Do not guess: say you are not sure and offer to pass "
                                  "the question to the team.", False, "Checked the help center: no answer there")
            return ToolResult([p.block() for p in passages], False, "Checked the help center", passages)
        if name == "find_products":
            query = _s(data.get("query"), 200)
            found = state.find_products(query)
            return ToolResult(json.dumps(found), False, "Checked the catalog")
        if name == "verify_customer":
            number = _s(data.get("order_number"), 40)
            result = state.verify_customer(number, _s(data.get("email"), 200))
            return ToolResult(json.dumps(result), False, f"Email matches order #{normalize_order_number(number)}")
        if name == "get_order":
            result = state.get_order(_s(data.get("order_number"), 40))
            return ToolResult(json.dumps(result), False, f"Opened order #{result['order_number']}")
        if name == "request_return":
            raw_items = data.get("items")
            items = [i for i in raw_items if isinstance(i, dict)] if isinstance(raw_items, list) else []
            result = state.request_return(_s(data.get("order_number"), 40), items, _s(data.get("reason"), 40),
                                          _s(data.get("resolution"), 20), _s(data.get("exchange_size"), 20) or None)
            what = f"refund {result['refund_if_approved']}" if "refund_if_approved" in result else \
                f"exchange for size {result['exchange_for_size']}"
            return ToolResult(json.dumps(result), False, f"Return request sent to your team ({what})")
        if name == "request_cancellation":
            result = state.request_cancellation(_s(data.get("order_number"), 40), _s(data.get("reason"), 200))
            return ToolResult(json.dumps(result), False, "Cancellation request sent to your team")
        if name == "request_address_change":
            raw_address = data.get("address")
            address = raw_address if isinstance(raw_address, dict) else {}
            result = state.request_address_change(_s(data.get("order_number"), 40), address)
            return ToolResult(json.dumps(result), False, "Address change sent to your team")
        if name == "hand_off_to_human":
            result = state.hand_off(_s(data.get("reason"), 40), _s(data.get("summary"), 600), _s(data.get("priority"), 10))
            return ToolResult(json.dumps(result), False, "Passed to a person on your team")
        state.log("assistant", name[:40] or "?", "unknown tool", "refused")
        return ToolResult(json.dumps({"error": "unknown_tool", "message": f"There is no tool named {name!r}."}), True,
                          f"Refused an unknown tool {name[:40]!r}")
    except StoreError as e:
        state.log("assistant", name, _first_sentence(e.message), "refused")
        label = "Order number and email don't match" if e.code in ("no_match", "verification_locked") else \
            f"Not allowed: {_first_sentence(e.message)}"
        return ToolResult(json.dumps({"error": e.code, "message": e.message}), True, label)


def _first_sentence(text: str) -> str:
    head = text.split(". ")[0]
    return head if head.endswith(".") else head + "."


__all__ = ["TOOLS", "TOOL_NAMES", "ToolResult", "run_tool", "mask_email", "money"]
