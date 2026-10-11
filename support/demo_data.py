"""The fictional store: catalog, customers and orders.

Copperleaf Outdoor does not exist. Every name, email, address and order here is made up for
the demo. Dates are relative to the day a demo session starts, so a return window that is
open today is still open when someone tries the demo next month.

Money is in cents (int) everywhere in the code, and formatted only when shown.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

STORE_NAME = "Copperleaf Outdoor"
SHIPS_TO = ("US", "CA")
FREE_SHIPPING_OVER = 7500        # standard shipping is free from $75
STANDARD_SHIPPING = 695
FREE_EXPRESS_OVER = 25000        # express shipping is free from $250
EXPRESS_SHIPPING = 1495
RETURN_WINDOW_DAYS = 30
RETURN_FEE = 695            # deducted from refunds for change of mind or wrong size
DAMAGE_REPORT_DAYS = 7      # damaged items must be reported within 7 days of delivery
LOST_AFTER_DAYS = 7         # no tracking update for this many days: treated as a lost parcel
MANAGER_REVIEW_OVER = 30000  # refunds above $300 are flagged for a manager

# sku -> product. `sizes` maps size -> units in stock; one-size products use {"One size": n}.
CATALOG: dict[str, dict[str, Any]] = {
    "NL-RJ": {
        "name": "Northline Rain Jacket", "price": 18900, "category": "Jackets",
        "summary": "Waterproof 3-layer shell for hiking in the rain.",
        "specs": ["Waterproof rating 20,000 mm", "Taped seams", "Pit zips for ventilation",
                  "Packs into its own pocket", "Weight 410 g (size M)", "Fits true to size, room for a fleece underneath"],
        "sizes": {"XS": 4, "S": 6, "M": 0, "L": 3, "XL": 2},
    },
    "BC-DJ": {
        "name": "Basecamp Down Jacket", "price": 25900, "category": "Jackets",
        "summary": "Warm 800-fill down jacket for cold camps.",
        "specs": ["800-fill responsibly sourced down", "Water-resistant shell (DWR), not waterproof",
                  "Weight 560 g (size M)", "Two zip hand pockets and one inside pocket"],
        "sizes": {"S": 2, "M": 5, "L": 4, "XL": 0, "XXL": 1},
    },
    "TH-40": {
        "name": "Trailhead 40L Backpack", "price": 14900, "category": "Packs",
        "summary": "40-litre pack for long day hikes and overnights.",
        "specs": ["Water-resistant 210D nylon, not waterproof", "Rain cover sold separately (Trail Rain Cover 40L)",
                  "Hydration sleeve fits a bladder up to 3 L", "Hip belt pockets", "Weight 1.3 kg"],
        "sizes": {"S/M torso": 7, "M/L torso": 5},
    },
    "TH-RC": {
        "name": "Trail Rain Cover 40L", "price": 1900, "category": "Packs",
        "summary": "Rain cover for 30 to 45 litre packs.",
        "specs": ["Fits packs from 30 to 45 litres", "Weight 110 g"],
        "sizes": {"One size": 0}, "restock_in_days": 9,
    },
    "RG-2": {
        "name": "Ridge 2 Tent", "price": 32900, "category": "Tents",
        "summary": "Light 2-person, 3-season tent.",
        "specs": ["Sleeps 2", "3-season", "Packed weight 2.1 kg", "Fly waterproof rating 3,000 mm",
                  "Two doors and two vestibules", "Freestanding"],
        "sizes": {"One size": 6},
    },
    "EX-4": {
        "name": "Expedition 4 Tent", "price": 64900, "category": "Tents",
        "summary": "4-person, 4-season tent for snow and wind.",
        "specs": ["Sleeps 4", "4-season", "Packed weight 5.4 kg", "Fly waterproof rating 5,000 mm", "Snow flaps"],
        "sizes": {"One size": 2},
    },
    "SM-TP": {
        "name": "Summit Trekking Poles", "price": 8900, "category": "Hiking",
        "summary": "Carbon trekking poles, sold as a pair.",
        "specs": ["Carbon, 3 sections", "Adjustable from 105 to 135 cm", "Weight 465 g per pair"],
        "sizes": {"One size": 12},
    },
    "MR-HS": {
        "name": "Merino Hiking Socks (2 pack)", "price": 2400, "category": "Hiking",
        "summary": "Cushioned merino wool hiking socks.",
        "specs": ["70% merino wool", "Cushioned sole", "Final sale for hygiene reasons"],
        "sizes": {"S": 10, "M": 14, "L": 9}, "final_sale": True,
    },
    "AL-ST": {
        "name": "Alpine Stove", "price": 7900, "category": "Camp kitchen",
        "summary": "Compact canister stove.",
        "specs": ["Output 2,600 W", "Boils 1 litre of water in about 3.5 minutes", "Weight 95 g",
                  "Fuel canister not included"],
        "sizes": {"One size": 9},
    },
    "FC-230": {
        "name": "Fuel Canister 230 g", "price": 800, "category": "Camp kitchen",
        "summary": "Isobutane and propane fuel for canister stoves.",
        "specs": ["230 g of fuel", "Cannot be shipped back, so it is final sale"],
        "sizes": {"One size": 40}, "final_sale": True,
    },
    "CC-1": {
        "name": "Camp Chair", "price": 5900, "category": "Camp",
        "summary": "Folding camp chair.",
        "specs": ["Weight 1.1 kg", "Holds up to 120 kg", "Packs to 35 x 12 cm"],
        "sizes": {"One size": 15},
    },
    "HB-2": {
        "name": "Hydration Bladder 2L", "price": 3500, "category": "Hiking",
        "summary": "2-litre water bladder with drinking tube.",
        "specs": ["2 litres", "BPA free", "Fits the Trailhead 40L hydration sleeve"],
        "sizes": {"One size": 20},
    },
}

CUSTOMERS: dict[str, dict[str, str]] = {
    "maya.lopez@example.com": {"first_name": "Maya", "last_name": "Lopez"},
    "daniel.kim@example.com": {"first_name": "Daniel", "last_name": "Kim"},
    "priya.shah@example.com": {"first_name": "Priya", "last_name": "Shah"},
    "sam.okafor@example.com": {"first_name": "Sam", "last_name": "Okafor"},
    "elena.rossi@example.com": {"first_name": "Elena", "last_name": "Rossi"},
}

# The identities the demo page offers to visitors, so they can try order questions.
DEMO_IDENTITIES = [
    {"name": "Maya Lopez", "email": "maya.lopez@example.com", "orders": ["1042", "1043"],
     "story": "#1042 delivered 12 days ago (rain jacket and socks). #1043 is a day late."},
    {"name": "Daniel Kim", "email": "daniel.kim@example.com", "orders": ["1051", "1038"],
     "story": "#1051 placed today, not shipped yet. #1038 delivered 45 days ago."},
    {"name": "Priya Shah", "email": "priya.shah@example.com", "orders": ["1060"],
     "story": "#1060 delivered 2 days ago (a stove and fuel)."},
    {"name": "Sam Okafor", "email": "sam.okafor@example.com", "orders": ["1047"],
     "story": "#1047 shipped 12 days ago; tracking has not moved for 10 days."},
    {"name": "Elena Rossi", "email": "elena.rossi@example.com", "orders": ["1055"],
     "story": "#1055 delivered 8 days ago: a $649 tent."},
]

_ADDRESSES = {
    "maya.lopez@example.com": {"name": "Maya Lopez", "line1": "214 Alder Street", "line2": "", "city": "Portland",
                               "region": "OR", "postal_code": "97205", "country": "US"},
    "daniel.kim@example.com": {"name": "Daniel Kim", "line1": "88 Harbour Road", "line2": "Apt 5", "city": "Vancouver",
                               "region": "BC", "postal_code": "V6B 1A1", "country": "CA"},
    "priya.shah@example.com": {"name": "Priya Shah", "line1": "17 Cedar Lane", "line2": "", "city": "Boulder",
                               "region": "CO", "postal_code": "80302", "country": "US"},
    "sam.okafor@example.com": {"name": "Sam Okafor", "line1": "402 Birch Avenue", "line2": "", "city": "Austin",
                               "region": "TX", "postal_code": "78704", "country": "US"},
    "elena.rossi@example.com": {"name": "Elena Rossi", "line1": "9 Granite Way", "line2": "", "city": "Denver",
                                "region": "CO", "postal_code": "80211", "country": "US"},
}

_PAYMENTS = {
    "maya.lopez@example.com": "Visa ending 1881",
    "daniel.kim@example.com": "Mastercard ending 5307",
    "priya.shah@example.com": "Visa ending 6620",
    "sam.okafor@example.com": "PayPal",
    "elena.rossi@example.com": "Amex ending 3009",
}


def _line(line_id: str, sku: str, qty: int = 1, size: str = "One size") -> dict[str, Any]:
    product = CATALOG[sku]
    return {"id": line_id, "sku": sku, "name": product["name"], "size": size, "qty": qty, "price": product["price"],
            "final_sale": bool(product.get("final_sale")), "returned_qty": 0}


def build_orders(today: date) -> dict[str, dict[str, Any]]:
    """All orders, with dates counted back from `today`. Returns a fresh copy every call."""
    def d(days_ago: int) -> str:
        return (today - timedelta(days=days_ago)).isoformat()

    def order(number: str, email: str, placed: int, lines: list[dict[str, Any]], status: str,
              shipment: dict[str, Any] | None, method: str = "Standard (3 to 5 business days)") -> dict[str, Any]:
        subtotal = sum(li["price"] * li["qty"] for li in lines)
        if method.startswith("Express"):
            shipping = 0 if subtotal >= FREE_EXPRESS_OVER else EXPRESS_SHIPPING
        else:
            shipping = 0 if subtotal >= FREE_SHIPPING_OVER else STANDARD_SHIPPING
        return {"number": number, "email": email, "placed": d(placed), "status": status, "lines": lines,
                "subtotal": subtotal, "shipping_cost": shipping, "total": subtotal + shipping,
                "shipping_method": method, "address": dict(_ADDRESSES[email]), "payment": _PAYMENTS[email],
                "shipment": shipment}

    def shipment(shipped: int, estimated: int, delivered: int | None, events: list[tuple[int, str]],
                 tracking: str) -> dict[str, Any]:
        return {"carrier": "ParcelGo", "tracking": tracking, "shipped": d(shipped), "estimated": d(estimated),
                "delivered": d(delivered) if delivered is not None else None,
                "events": [{"date": d(n), "text": text} for n, text in events]}

    orders = [
        order("1042", "maya.lopez@example.com", 16,
              [_line("L1", "NL-RJ", size="M"), _line("L2", "MR-HS", size="M")], "delivered",
              shipment(15, 12, 12, [(15, "Picked up by ParcelGo"), (13, "Out for delivery"),
                                    (12, "Delivered, left at front door")], "PGX-K4417A")),
        order("1043", "maya.lopez@example.com", 5, [_line("L1", "TH-40", size="S/M torso")], "in_transit",
              shipment(4, 1, None, [(4, "Picked up by ParcelGo"), (3, "Departed Reno sorting center"),
                                    (1, "Arrived at Portland delivery station")], "PGX-M2291B")),
        order("1051", "daniel.kim@example.com", 0,
              [_line("L1", "RG-2"), _line("L2", "SM-TP")], "processing", None, method="Express (1 to 2 business days)"),
        order("1038", "daniel.kim@example.com", 50, [_line("L1", "BC-DJ", size="L")], "delivered",
              shipment(49, 45, 45, [(49, "Picked up by ParcelGo"), (45, "Delivered, signed by D. Kim")],
                       "PGX-T6612C")),
        order("1060", "priya.shah@example.com", 6, [_line("L1", "AL-ST"), _line("L2", "FC-230", qty=2)], "delivered",
              shipment(5, 2, 2, [(5, "Picked up by ParcelGo"), (2, "Delivered, left with reception")],
                       "PGX-R7740D")),
        order("1047", "sam.okafor@example.com", 13, [_line("L1", "CC-1", qty=2), _line("L2", "HB-2")], "in_transit",
              shipment(12, 8, None, [(12, "Picked up by ParcelGo"), (10, "Departed Phoenix sorting center")],
                       "PGX-W9921E")),
        order("1055", "elena.rossi@example.com", 11, [_line("L1", "EX-4")], "delivered",
              shipment(10, 8, 8, [(10, "Picked up by ParcelGo"), (8, "Delivered, signed by E. Rossi")],
                       "PGX-H1185F")),
    ]
    return {o["number"]: o for o in orders}


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def nice_date(iso: str) -> str:
    """'2026-10-14' -> 'Wed 14 Oct'. Tool results show dates this way, and so do replies."""
    day = date.fromisoformat(iso)
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"
