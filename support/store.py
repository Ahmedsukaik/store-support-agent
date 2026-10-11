"""The store as the support team and the assistant see it: orders, stock, requests, tickets.

This is a stand-in for the store's own system (for example Shopify's Admin API plus a
helpdesk). Every rule that matters is enforced here, in code: who may see an order, whether
an item can be returned, how much is refunded, and that a person approves every refund and
change. The assistant can only ask; it never sets an amount and never writes on its own.

A StoreState lives for one demo session. It is rebuilt from the session's events on every
request (see session.py), so it must be deterministic: no clock, no randomness. "Today" is
the session's start date, and ids are numbered (R-1, T-1).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .demo_data import (
    CATALOG,
    CUSTOMERS,
    DAMAGE_REPORT_DAYS,
    LOST_AFTER_DAYS,
    MANAGER_REVIEW_OVER,
    RETURN_FEE,
    RETURN_WINDOW_DAYS,
    SHIPS_TO,
    build_orders,
    money,
    nice_date,
)

MAX_FAILED_VERIFICATIONS = 3
RETURN_REASONS = ("changed_mind", "wrong_size", "defective", "wrong_item", "damaged_in_transit", "other")
FEE_REASONS = ("changed_mind", "wrong_size")  # the help center charges the fee for these two
FREE_REASONS = ("defective", "wrong_item")
HANDOFF_REASONS = ("customer_asked", "damaged_item", "lost_parcel", "warranty_claim", "verification_failed",
                   "policy_exception", "upset_customer", "cannot_resolve", "other")
SYSTEM_REASONS = ("reply_held", "model_refused", "too_many_steps")  # hand-offs the code makes, not the model
POSTAL = {"US": re.compile(r"\d{5}(-\d{4})?"), "CA": re.compile(r"[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d")}


class StoreError(Exception):
    """A request the store refuses. `code` is stable for tests; the message is for the model or the team."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def mask_emails(text: str) -> str:
    """Every email address in a free text, masked: logs and summaries never hold a full address."""
    return EMAIL_IN_TEXT.sub(lambda m: mask_email(m.group(0)), str(text))


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


def normalize_order_number(raw: str) -> str:
    return re.sub(r"\D", "", str(raw or ""))[:12]


@dataclass
class StoreState:
    today: date
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    stock: dict[str, dict[str, int]] = field(default_factory=dict)
    verified_email: str | None = None
    failed_verifications: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    tickets: list[dict[str, Any]] = field(default_factory=list)
    notices: list[dict[str, Any]] = field(default_factory=list)  # messages shown to the customer by the system or the team
    audit: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0  # index of the event being applied, for ordering in the views

    @classmethod
    def new(cls, today: date) -> StoreState:
        return cls(today=today, orders=build_orders(today),
                   stock={sku: dict(p["sizes"]) for sku, p in CATALOG.items()})

    # ------------------------------------------------------------------ helpers
    def log(self, actor: str, action: str, detail: str, outcome: str = "ok") -> None:
        self.audit.append({"step": self.step, "actor": actor, "action": action, "detail": mask_emails(detail)[:300],
                           "outcome": outcome})

    def days_since(self, iso: str) -> int:
        return (self.today - date.fromisoformat(iso)).days

    def open_ticket(self) -> dict[str, Any] | None:
        return next((t for t in self.tickets if t["status"] == "open"), None)

    @property
    def ai_paused(self) -> bool:
        return self.open_ticket() is not None

    def customer(self) -> dict[str, str] | None:
        return CUSTOMERS.get(self.verified_email or "")

    def _own_order(self, number: str) -> dict[str, Any]:
        if not self.verified_email:
            raise StoreError("not_verified", "The customer is not verified yet. Ask for the order number and the "
                                             "email used for the order, then call verify_customer.")
        order = self.orders.get(normalize_order_number(number))
        if order is None or order["email"] != self.verified_email:
            # The same answer whether the order does not exist or belongs to someone else.
            raise StoreError("order_not_found", "No order with that number for this customer.")
        return order

    def _pending(self, kind: str, order: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["kind"] == kind and r["order"] == order and r["status"] == "pending"]

    def _pending_qty(self, order: str, line_id: str) -> int:
        return sum(li["qty"] for r in self._pending("return", order) for li in r["details"]["lines"] if li["line_id"] == line_id)

    # ------------------------------------------------------------------ reads
    def verify_customer(self, order_number: str, email: str) -> dict[str, Any]:
        if self.failed_verifications >= MAX_FAILED_VERIFICATIONS:
            raise StoreError("verification_locked", "Verification is locked after 3 failed attempts in this chat, and "
                                                    "the chat was passed to a person. Do not try again.")
        number = normalize_order_number(order_number)
        address = str(email or "").strip().lower()
        order = self.orders.get(number)
        if order is None or order["email"] != address:
            self.failed_verifications += 1
            left = MAX_FAILED_VERIFICATIONS - self.failed_verifications
            self.log("assistant", "verify_customer", f"order #{number or '?'}, {mask_email(address)}", "no match")
            if not left:
                self.hand_off("verification_failed", "Three failed verification attempts in this chat.", "normal", source="system")
                raise StoreError("verification_locked", "The order number and email do not match an order, and that was "
                                                        "the third attempt, so the chat was passed to a person. Tell the "
                                                        "customer a teammate will reply here.")
            raise StoreError("no_match", "The order number and email do not match an order. Ask the customer to check "
                                         f"both, without saying which one is wrong. {left} attempt(s) left.")
        self.verified_email = address
        self.log("assistant", "verify_customer", f"order #{number}, {mask_email(address)}", "verified")
        mine = [o for o in self.orders.values() if o["email"] == address]
        return {"verified": True, "first_name": CUSTOMERS[address]["first_name"],
                "orders": [{"order_number": o["number"], "placed": nice_date(o["placed"]), "status": self.status_text(o)}
                           for o in sorted(mine, key=lambda o: o["placed"], reverse=True)]}

    def status_text(self, order: dict[str, Any]) -> str:
        ship = order["shipment"]
        if order["status"] == "cancelled":
            return "Cancelled"
        if order["status"] == "processing":
            return "Processing, not shipped yet"
        if ship and ship["delivered"]:
            return f"Delivered on {nice_date(ship['delivered'])}"
        late = self.days_since(ship["estimated"]) if ship else 0
        text = f"In transit, estimated delivery {nice_date(ship['estimated'])}" if ship else "In transit"
        return text + (f" ({late} day{'s' if late != 1 else ''} past the estimate)" if late > 0 else "")

    def return_status(self, order: dict[str, Any], line: dict[str, Any]) -> str:
        if line["returned_qty"] >= line["qty"]:
            return "Return approved"
        if self._pending_qty(order["number"], line["id"]):
            return "Return requested, waiting for team review"
        if line["final_sale"]:
            return "Final sale, not returnable"
        delivered = (order["shipment"] or {}).get("delivered")
        if not delivered:
            return "Not delivered yet"
        last_day = date.fromisoformat(delivered).toordinal() + RETURN_WINDOW_DAYS
        if self.today.toordinal() > last_day:
            return f"Return window closed on {nice_date(date.fromordinal(last_day).isoformat())}"
        return f"Returnable until {nice_date(date.fromordinal(last_day).isoformat())}"

    def get_order(self, order_number: str) -> dict[str, Any]:
        order = self._own_order(order_number)
        ship = order["shipment"]
        view: dict[str, Any] = {
            "order_number": order["number"], "placed": nice_date(order["placed"]), "status": self.status_text(order),
            "items": [{"line_id": li["id"], "product": li["name"], "size": li["size"], "quantity": li["qty"],
                       "price": money(li["price"]), "return_status": self.return_status(order, li)} for li in order["lines"]],
            "subtotal": money(order["subtotal"]), "shipping": money(order["shipping_cost"]), "total": money(order["total"]),
            "shipping_method": order["shipping_method"], "ship_to": self.address_line(order["address"]),
            "paid_with": order["payment"],
        }
        if ship:
            last = ship["events"][-1]
            view["tracking"] = {"carrier": ship["carrier"], "tracking_number": ship["tracking"],
                                "events": [{"date": nice_date(e["date"]), "event": e["text"]} for e in ship["events"]]}
            if not ship["delivered"]:
                quiet = self.days_since(last["date"])
                view["tracking"]["days_since_last_update"] = quiet
                if quiet >= LOST_AFTER_DAYS:
                    view["policy_note"] = (f"Tracking has not updated for {quiet} days, so by policy this parcel is "
                                           "treated as lost. Hand the conversation to a person (reason lost_parcel): "
                                           "they open a trace and offer a free replacement or a full refund.")
                elif self.days_since(ship["estimated"]) > 0:
                    view["policy_note"] = ("Past the estimated delivery date. By policy it usually arrives within 2 "
                                           "business days; after 3 business days late the team asks the carrier to trace it.")
            elif self.days_since(ship["delivered"]) <= DAMAGE_REPORT_DAYS:
                view["damage_report_open_until"] = nice_date(
                    date.fromordinal(date.fromisoformat(ship["delivered"]).toordinal() + DAMAGE_REPORT_DAYS).isoformat())
        open_requests = [r for r in self.requests if r["order"] == order["number"] and r["status"] == "pending"]
        if open_requests:
            view["open_requests"] = [{"request_id": r["id"], "type": r["kind"], "status": "waiting for team review"}
                                     for r in open_requests]
        self.log("assistant", "get_order", f"order #{order['number']}")
        return view

    @staticmethod
    def address_line(a: dict[str, str]) -> str:
        parts = [a["name"], a["line1"], a.get("line2", ""), a["city"], f"{a['region']} {a['postal_code']}".strip(), a["country"]]
        return ", ".join(p for p in parts if p)

    def find_products(self, query: str) -> dict[str, Any]:
        q = set(re.findall(r"[a-z0-9]+", str(query).lower())) - {"the", "a", "do", "you", "have", "is", "it", "in", "of", "for"}
        scored = []
        for sku, p in CATALOG.items():
            hay = set(re.findall(r"[a-z0-9]+", f"{p['name']} {p['category']} {p['summary']} {' '.join(p['specs'])}".lower()))
            name = set(re.findall(r"[a-z0-9]+", p["name"].lower()))
            score = len(q & hay) + 2 * len(q & name)
            if score:
                scored.append((score, sku))
        scored.sort(key=lambda s: -s[0])
        products = []
        for _, sku in scored[:3]:
            p = CATALOG[sku]
            stock = self.stock[sku]
            item: dict[str, Any] = {"product": p["name"], "price": money(p["price"]), "summary": p["summary"], "specs": p["specs"],
                                    "in_stock": [s for s, n in stock.items() if n > 0],
                                    "out_of_stock": [s for s, n in stock.items() if n <= 0]}
            if p.get("final_sale"):
                item["final_sale"] = True
            if p.get("restock_in_days") and not item["in_stock"]:
                item["restock_expected"] = nice_date(date.fromordinal(self.today.toordinal() + p["restock_in_days"]).isoformat())
            products.append(item)
        self.log("assistant", "find_products", str(query), f"{len(products)} found")
        return {"products": products} if products else {"products": [], "note": "No product matched. Do not guess."}

    # ------------------------------------------------------------------ requests (a person approves each one)
    def _new_request(self, kind: str, order: str, details: dict[str, Any], amount: int | None,
                     checks: list[tuple[str, bool]], flags: list[str]) -> dict[str, Any]:
        request = {"id": f"R-{len(self.requests) + 1}", "kind": kind, "order": order, "status": "pending",
                   "created_step": self.step, "details": details, "amount": amount, "flags": flags,
                   "checks": [{"label": label, "ok": ok} for label, ok in checks], "note": None, "decided_step": None}
        self.requests.append(request)
        return request

    def request_return(self, order_number: str, items: list[dict[str, Any]], reason: str, resolution: str,
                       exchange_size: str | None = None) -> dict[str, Any]:
        order = self._own_order(order_number)
        if reason not in RETURN_REASONS:
            raise StoreError("bad_reason", f"reason must be one of {', '.join(RETURN_REASONS)}.")
        if resolution not in ("refund", "exchange"):
            raise StoreError("bad_resolution", "resolution must be refund or exchange.")
        if reason == "damaged_in_transit":
            raise StoreError("damaged_needs_person", "Items damaged in transit are handled by a person, who needs a photo "
                                                     "of the item and the box. Hand the conversation to a person "
                                                     "(reason damaged_item) with a short summary.")
        wanted_key = sorted((str(i.get("line_id", "")), i.get("quantity", 1)) for i in items
                            if isinstance(i.get("quantity", 1), int) and not isinstance(i.get("quantity", 1), bool))
        replacing: list[dict[str, Any]] = []
        for r in self._pending("return", order["number"]):
            if sorted((li["line_id"], li["qty"]) for li in r["details"]["lines"]) != wanted_key or len(wanted_key) != len(items):
                continue
            if r["details"]["resolution"] == resolution and r["details"]["reason"] == reason \
                    and r["details"].get("exchange_size") == (exchange_size if resolution == "exchange" else None):
                return self._return_result(r, order)  # asked again (a retry, or the customer repeating)
            fits_beside = all(q <= li["qty"] - li["returned_qty"] - self._pending_qty(order["number"], line_id)
                              for line_id, q in wanted_key for li in order["lines"] if li["id"] == line_id)
            if not fits_beside:  # the same units with a new reason or resolution: a correction, not a second return
                replacing.append(r)
        ship = order["shipment"] or {}
        if order["status"] != "delivered" or not ship.get("delivered"):
            hint = "It can be cancelled instead, before it ships." if order["status"] == "processing" else \
                   "It can be returned after it arrives."
            raise StoreError("not_delivered", f"Order #{order['number']} has not been delivered. {hint}")
        age = self.days_since(ship["delivered"])
        if not items:
            raise StoreError("no_items", "Say which items to return (line_id and quantity from get_order).")
        lines, seen = [], set()
        for wanted in items[:10]:
            line_id = str(wanted.get("line_id", ""))
            qty = wanted.get("quantity", 1)
            line = next((li for li in order["lines"] if li["id"] == line_id), None)
            if line is None or line_id in seen:
                raise StoreError("bad_line", f"Order #{order['number']} has no item with that line_id, or it is listed "
                                             "twice. Use the line_id values from get_order.")
            seen.add(line_id)
            if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
                raise StoreError("bad_quantity", "quantity must be a whole number of at least 1.")
            if line["final_sale"]:
                raise StoreError("final_sale", f"{line['name']} is final sale and cannot be returned (help center: "
                                               "Returns and exchanges > Final sale items).")
            replaced_qty = sum(li["qty"] for r in replacing for li in r["details"]["lines"] if li["line_id"] == line_id)
            available = line["qty"] - line["returned_qty"] - self._pending_qty(order["number"], line_id) + replaced_qty
            if qty > available:
                raise StoreError("too_many", f"The {line['name']} on this order has already been returned, or a return "
                                             "for it is waiting for review." if not available else
                                             f"Only {available} of the {line['name']} can still be returned.")
            lines.append((line, qty))
        if age > RETURN_WINDOW_DAYS:
            raise StoreError("window_closed",
                             f"Delivered {age} days ago, outside the {RETURN_WINDOW_DAYS}-day return window. "
                             + ("A defect is covered by the 2-year warranty: hand off to a person (reason warranty_claim)."
                                if reason == "defective" else "Offer to pass the request to a person (reason policy_exception) "
                                                              "if the customer wants an exception."))
        checks = [("Customer verified with order number and email", True),
                  (f"Delivered {nice_date(ship['delivered'])}, {age} days ago: inside the {RETURN_WINDOW_DAYS}-day window", True),
                  ("No final sale items", True)]
        flags: list[str] = []
        new_size = None
        if resolution == "exchange":
            if len(lines) != 1 or lines[0][1] != 1:
                raise StoreError("exchange_one_item", "An exchange is for one item at a time.")
            line = lines[0][0]
            sizes = self.stock[line["sku"]]
            new_size = str(exchange_size or "")
            if list(sizes) == ["One size"]:
                raise StoreError("no_sizes", f"{line['name']} comes in one size only, so it can be refunded but not exchanged.")
            others = ", ".join(s for s in sizes if s != line["size"])
            if new_size == line["size"]:
                raise StoreError("same_size", f"The {line['name']} on this order is already size {line['size']}. "
                                              f"Other sizes: {others}.")
            if new_size not in sizes:
                raise StoreError("bad_size", f"The {line['name']} does not come in that size. Sizes: {others}.")
            if sizes[new_size] <= 0:
                in_stock = [s for s, n in sizes.items() if n > 0 and s != line["size"]]
                raise StoreError("size_out_of_stock", f"Size {new_size} of {line['name']} is out of stock. In stock: "
                                                      f"{', '.join(in_stock) or 'none'}. Offer a refund instead.")
            checks.append((f"Size {new_size} in stock ({sizes[new_size]} left)", True))
            refund, fee = 0, 0
        else:
            fee = RETURN_FEE if reason in FEE_REASONS else 0
            items_total = sum(li["price"] * q for li, q in lines)
            returnable = [li for li in order["lines"] if not li["final_sale"]]
            whole_order = (len(lines) == len(returnable) and all(q == li["qty"] for li, q in lines)
                           and not any(li["returned_qty"] for li in order["lines"]))
            shipping_back = order["shipping_cost"] if reason in FREE_REASONS and whole_order else 0
            refund = max(0, items_total - fee + shipping_back)
            earlier = sum(r["amount"] or 0 for r in self.requests if r["order"] == order["number"] and r not in replacing
                          and r["status"] in ("pending", "approved") and r["kind"] in ("return", "cancellation"))
            if refund + earlier > MANAGER_REVIEW_OVER:
                flags.append(f"Refunds on this order over {money(MANAGER_REVIEW_OVER)}: needs a manager")
        if reason == "other":
            flags.append("Reason given as other: check it with the customer")
        details = {"lines": [{"line_id": li["id"], "product": li["name"], "size": li["size"], "qty": q,
                              "price": li["price"]} for li, q in lines],
                   "reason": reason, "resolution": resolution, "exchange_size": new_size, "fee": fee}
        request = self._new_request("return", order["number"], details, refund if resolution == "refund" else None,
                                    checks, flags)
        for old in replacing:  # only now that the new request exists
            old["status"], old["note"], old["decided_step"] = "replaced", f"Replaced by {request['id']}", self.step
        return self._return_result(request, order)

    def _return_result(self, request: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
        d = request["details"]
        self.log("assistant", "request_return", f"order #{order['number']}, {d['resolution']}, {d['reason']}",
                 f"{request['id']} pending")
        result: dict[str, Any] = {"request_id": request["id"], "status": "waiting for team review",
                                  "next_step": f"A person on the team reviews it, usually within one business day. Once it is "
                                               f"approved, a prepaid return label is emailed to {mask_email(order['email'])}."}
        if d["resolution"] == "refund":
            result["refund_if_approved"] = money(request["amount"])
            if request["details"]["fee"]:
                result["return_fee_deducted"] = money(request["details"]["fee"])
            result["refund_timing"] = "to the original payment method, 5 to 7 business days after the return arrives"
        else:
            result["exchange_for_size"] = request["details"]["exchange_size"]
        return result

    def request_cancellation(self, order_number: str, reason: str) -> dict[str, Any]:
        order = self._own_order(order_number)
        if order["status"] == "cancelled":
            raise StoreError("already_cancelled", f"Order #{order['number']} is already cancelled.")
        if order["status"] != "processing":
            raise StoreError("already_shipped", f"Order #{order['number']} has already shipped, so it cannot be cancelled. "
                                                "It can be returned after it arrives.")
        existing = self._pending("cancellation", order["number"])
        request = existing[0] if existing else self._new_request(
            "cancellation", order["number"], {"reason": mask_emails(str(reason))[:200]}, order["total"],
            [("Customer verified with order number and email", True), ("Order not shipped yet", True)],
            [f"Refunds on this order over {money(MANAGER_REVIEW_OVER)}: needs a manager"] if order["total"] > MANAGER_REVIEW_OVER else [])
        self.log("assistant", "request_cancellation", f"order #{order['number']}", f"{request['id']} pending")
        return {"request_id": request["id"], "status": "waiting for team review", "refund_if_approved": money(order["total"]),
                "next_step": "A person on the team confirms cancellations, usually within 2 business hours. The order "
                             "will not ship while the request is open."}

    def request_address_change(self, order_number: str, address: dict[str, Any]) -> dict[str, Any]:
        order = self._own_order(order_number)
        if order["status"] != "processing":
            raise StoreError("already_shipped", f"Order #{order['number']} has already shipped, so its address cannot change.")
        clean = {k: re.sub(r"[^\w\s.,'#/-]", "", str(address.get(k) or "")).strip()[:100]
                 for k in ("name", "line1", "line2", "city", "region", "postal_code", "country")}
        clean["country"] = clean["country"].upper()
        missing = [k for k in ("name", "line1", "city", "region", "postal_code", "country") if not clean[k]]
        if missing:
            raise StoreError("address_incomplete", f"The new address is missing: {', '.join(missing)}. Ask the customer.")
        if clean["country"] not in SHIPS_TO:
            raise StoreError("country_not_served", "We ship only to the United States (US) and Canada (CA).")
        if not POSTAL[clean["country"]].fullmatch(clean["postal_code"]):
            raise StoreError("bad_postal_code", f"That is not a valid {clean['country']} postal code. Ask the customer.")
        existing = self._pending("address_change", order["number"])
        for old in existing:  # a newer address replaces a pending one
            old["status"], old["note"], old["decided_step"] = "replaced", "Replaced by a newer request", self.step
        request = self._new_request("address_change", order["number"], {"old": dict(order["address"]), "new": clean}, None,
                                    [("Customer verified with order number and email", True), ("Order not shipped yet", True),
                                     (f"Ships to {clean['country']}", True)], [])
        self.log("assistant", "request_address_change", f"order #{order['number']}", f"{request['id']} pending")
        return {"request_id": request["id"], "status": "waiting for team review", "new_address": self.address_line(clean),
                "next_step": "A person on the team confirms address changes, usually within 2 business hours."}

    def hand_off(self, reason: str, summary: str, priority: str, source: str = "assistant", draft: str | None = None) -> dict[str, Any]:
        if reason not in HANDOFF_REASONS and not (source == "system" and reason in SYSTEM_REASONS):
            reason = "other"
        summary = mask_emails(summary)
        draft = mask_emails(draft) if draft else draft
        if priority not in ("normal", "urgent"):
            priority = "normal"
        ticket = self.open_ticket()
        if ticket is None:
            ticket = {"id": f"T-{len(self.tickets) + 1}", "status": "open", "reason": reason, "priority": priority,
                      "summary": str(summary)[:600], "created_step": self.step, "source": source, "draft": draft,
                      "customer": mask_email(self.verified_email) if self.verified_email else None}
            self.tickets.append(ticket)
        else:
            ticket["summary"] = (ticket["summary"] + " | " + str(summary)[:300])[:900]
            if priority == "urgent":
                ticket["priority"] = "urgent"
            if draft:
                ticket["draft"] = draft
        self.log(source, "hand_off", f"{ticket['id']} {reason} ({priority})")
        return {"ticket_id": ticket["id"], "status": "passed to the team",
                "next_step": "Tell the customer a person from the team will reply in this chat. Support hours are Monday "
                             "to Friday, 8 am to 6 pm Pacific time. Do not promise an outcome."}

    # ------------------------------------------------------------------ the team's side
    def decide(self, request_id: str, approve: bool, note: str = "") -> dict[str, Any]:
        request = next((r for r in self.requests if r["id"] == request_id), None)
        if request is None:
            raise StoreError("no_request", f"There is no request {request_id}.")
        if request["status"] != "pending":
            raise StoreError("already_decided", f"{request_id} is already {request['status']}.")
        note = str(note or "").strip()[:300]
        order = self.orders[request["order"]]
        if not approve:
            if not note:
                raise StoreError("note_required", "Write a short reason for the customer when rejecting.")
            request.update(status="rejected", note=note, decided_step=self.step)
            self.notice("rejected", f"Update on your {self._kind_text(request)} for order #{order['number']}: the team "
                                    f"could not approve it. {note}")
            self.log("team", "reject", f"{request_id}: {note}")
            return request
        if request["kind"] == "return":
            d = request["details"]
            if d["resolution"] == "exchange":
                line = next(li for li in order["lines"] if li["id"] == d["lines"][0]["line_id"])
                if self.stock[line["sku"]].get(d["exchange_size"], 0) <= 0:
                    raise StoreError("size_gone", f"Size {d['exchange_size']} sold out since the request. Reject it, or ask "
                                                  "the customer about a refund.")
                self.stock[line["sku"]][d["exchange_size"]] -= 1
            for wanted in d["lines"]:
                line = next(li for li in order["lines"] if li["id"] == wanted["line_id"])
                line["returned_qty"] += wanted["qty"]
            what = ", ".join(li["product"] for li in d["lines"])
            if d["resolution"] == "exchange":
                text = (f"Good news: your exchange for {what} (size {d['exchange_size']}) is approved. A prepaid return label "
                        f"is on its way to {mask_email(order['email'])}, and the new size ships as soon as the carrier scans your return.")
            else:
                text = (f"Good news: your return for {what} is approved. A prepaid return label is on its way to "
                        f"{mask_email(order['email'])}. Your refund of {money(request['amount'])} goes back to your "
                        f"{order['payment']} within 5 to 7 business days after the return arrives.")
        elif request["kind"] == "cancellation":
            if order["status"] != "processing":
                raise StoreError("not_processing", "The order is no longer waiting to ship, so it cannot be cancelled.")
            order["status"] = "cancelled"
            text = (f"Your order #{order['number']} is cancelled. A refund of {money(request['amount'])} goes back to your "
                    f"{order['payment']} within 5 to 7 business days.")
        else:
            if order["status"] != "processing":
                raise StoreError("not_processing", "The order is no longer waiting to ship (it shipped or was cancelled), "
                                                   "so the address cannot change.")
            order["address"] = dict(request["details"]["new"])
            text = f"The shipping address for order #{order['number']} is updated to {self.address_line(order['address'])}."
        request.update(status="approved", note=note or None, decided_step=self.step)
        self.notice("approved", text)
        self.log("team", "approve", request_id + (f" ({note})" if note else ""))
        return request

    @staticmethod
    def _kind_text(request: dict[str, Any]) -> str:
        if request["kind"] == "return":
            return "exchange request" if request["details"]["resolution"] == "exchange" else "return request"
        return {"cancellation": "cancellation request", "address_change": "address change"}[request["kind"]]

    def notice(self, kind: str, text: str) -> None:
        self.notices.append({"step": self.step, "kind": kind, "text": text})

    def team_reply(self, text: str) -> None:
        text = str(text or "").strip()[:1000]
        if not text:
            raise StoreError("empty_reply", "The reply is empty.")
        self.notices.append({"step": self.step, "kind": "team_reply", "text": text})
        self.log("team", "reply", text[:80])

    def return_to_assistant(self) -> None:
        ticket = self.open_ticket()
        if ticket is None:
            raise StoreError("no_open_ticket", "This chat is not with the team.")
        ticket["status"], ticket["closed_step"] = "closed", self.step
        self.failed_verifications = 0  # a person had the chat; the assistant may verify again
        self.notices.append({"step": self.step, "kind": "back_to_ai", "text": "The assistant is back in this chat."})
        self.log("team", "return_to_assistant", ticket["id"])

    def snapshot(self) -> dict[str, Any]:
        """A JSON-friendly copy, for the page."""
        return copy.deepcopy({"requests": self.requests, "tickets": self.tickets, "audit": self.audit})
