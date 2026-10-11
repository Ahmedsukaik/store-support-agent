"""The store's rules: who sees what, what can be returned, how much is refunded, who decides."""
from __future__ import annotations

import pytest
from conftest import DANIEL, ELENA, MAYA, PRIYA, SAM, TODAY

from support.demo_data import RETURN_FEE, nice_date
from support.store import StoreError, StoreState, mask_email, normalize_order_number


def refused(fn, *args, **kwargs) -> StoreError:
    with pytest.raises(StoreError) as caught:
        fn(*args, **kwargs)
    return caught.value


# ------------------------------------------------------------------ verification and privacy
def test_verification_needs_the_order_number_and_the_email_on_that_order(state):
    result = state.verify_customer("#1042", "  Maya.Lopez@Example.com ")
    assert result["verified"] and result["first_name"] == "Maya"
    assert [o["order_number"] for o in result["orders"]] == ["1043", "1042"]  # newest first
    assert state.verified_email == MAYA


def test_a_wrong_email_does_not_say_which_part_is_wrong(state):
    e = refused(state.verify_customer, "1042", DANIEL)
    assert e.code == "no_match" and "without saying which one is wrong" in e.message
    e2 = refused(state.verify_customer, "9999", MAYA)
    assert e2.code == "no_match" and e2.message.split(".")[0] == e.message.split(".")[0]
    assert state.verified_email is None


def test_the_third_failed_attempt_locks_verification_and_hands_the_chat_to_a_person(state):
    for code in ("no_match", "no_match", "verification_locked"):
        assert refused(state.verify_customer, "1042", "someone@example.com").code == code
    assert state.tickets[0]["reason"] == "verification_failed" and state.tickets[0]["source"] == "system" and state.ai_paused
    e = refused(state.verify_customer, "1042", MAYA)  # even the right details are refused now
    assert e.code == "verification_locked" and state.verified_email is None


def test_orders_are_hidden_until_verification(state):
    e = refused(state.get_order, "1042")
    assert e.code == "not_verified"


def test_another_customers_order_looks_exactly_like_a_missing_one(maya):
    other = refused(maya.get_order, "1051")     # Daniel's order
    missing = refused(maya.get_order, "7777")   # no such order
    assert other.code == missing.code == "order_not_found" and other.message == missing.message


def test_switching_to_another_verified_customer_drops_the_first_ones_orders(maya):
    maya.verify_customer("1051", DANIEL)
    assert refused(maya.get_order, "1042").code == "order_not_found"
    assert maya.get_order("1051")["order_number"] == "1051"


def test_order_numbers_are_read_loosely_and_emails_are_masked():
    assert normalize_order_number("Order #1042") == normalize_order_number(" 10 42") == "1042"
    assert mask_email("maya.lopez@example.com") == "m***@example.com"
    assert mask_email("not-an-email") == "***"


# ------------------------------------------------------------------ what get_order shows
def test_a_late_parcel_shows_how_late_it_is_and_the_policy(maya):
    order = maya.get_order("1043")
    assert order["status"].startswith("In transit") and "(1 day past the estimate)" in order["status"]
    assert "usually arrives within 2 business days" in order["policy_note"]
    assert order["tracking"]["days_since_last_update"] == 1


def test_tracking_that_has_not_moved_for_a_week_is_treated_as_lost(state):
    state.verify_customer("1047", SAM)
    order = state.get_order("1047")
    assert order["tracking"]["days_since_last_update"] == 10
    assert "treated as lost" in order["policy_note"] and "lost_parcel" in order["policy_note"]


def test_return_status_per_item(maya):
    items = {i["line_id"]: i for i in maya.get_order("1042")["items"]}
    last_day = nice_date(TODAY.replace(day=30).isoformat())  # delivered 30 Sep + 30 days = 30 Oct
    assert items["L1"]["return_status"] == f"Returnable until {last_day}"
    assert items["L2"]["return_status"] == "Final sale, not returnable"


def test_a_recent_delivery_shows_until_when_damage_can_be_reported(state):
    state.verify_customer("1060", PRIYA)
    assert state.get_order("1060")["damage_report_open_until"]


# ------------------------------------------------------------------ returns: the store computes the refund
def test_a_return_for_the_wrong_size_deducts_the_return_fee(maya):
    result = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund")
    assert result["status"] == "sent to the team, waiting for their review"
    assert result["refund_if_approved"] == "$182.05" and result["return_fee_deducted"] == "$6.95"
    request = maya.requests[0]
    assert request["amount"] == 18900 - RETURN_FEE and request["status"] == "pending"
    assert any("inside the 30-day window" in c["label"] for c in request["checks"])


def test_a_defective_item_is_refunded_in_full(maya):
    result = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "defective", "refund")
    assert result["refund_if_approved"] == "$189.00" and "return_fee_deducted" not in result


def test_shipping_is_refunded_only_for_a_wrong_or_defective_whole_order(maya):
    maya.orders["1042"]["shipping_cost"] = 695
    wrong = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_item", "refund")
    assert wrong["refund_if_approved"] == "$195.95"  # the only returnable item, so the whole order
    state = StoreState.new(TODAY)
    state.verify_customer("1042", MAYA)
    state.orders["1042"]["shipping_cost"] = 695
    mind = state.request_return("1042", [{"line_id": "L1", "quantity": 1}], "changed_mind", "refund")
    assert mind["refund_if_approved"] == "$182.05"


def test_final_sale_items_cannot_be_returned(maya):
    e = refused(maya.request_return, "1042", [{"line_id": "L2", "quantity": 1}], "changed_mind", "refund")
    assert e.code == "final_sale"


def test_the_return_window_is_enforced_and_points_defects_to_the_warranty(state):
    state.verify_customer("1038", DANIEL)
    e = refused(state.request_return, "1038", [{"line_id": "L1", "quantity": 1}], "changed_mind", "refund")
    assert e.code == "window_closed" and "45 days" in e.message and "policy_exception" in e.message
    e = refused(state.request_return, "1038", [{"line_id": "L1", "quantity": 1}], "defective", "refund")
    assert "warranty_claim" in e.message


def test_items_damaged_in_transit_go_to_a_person(state):
    state.verify_customer("1060", PRIYA)
    e = refused(state.request_return, "1060", [{"line_id": "L1", "quantity": 1}], "damaged_in_transit", "refund")
    assert e.code == "damaged_needs_person" and "damaged_item" in e.message


def test_orders_that_have_not_arrived_cannot_be_returned(maya, state):
    assert "after it arrives" in refused(maya.request_return, "1043", [{"line_id": "L1", "quantity": 1}],
                                         "changed_mind", "refund").message
    state.verify_customer("1051", DANIEL)
    assert "cancelled instead" in refused(state.request_return, "1051", [{"line_id": "L1", "quantity": 1}],
                                          "changed_mind", "refund").message


@pytest.mark.parametrize("items, code", [
    ([], "no_items"),
    ([{"line_id": "L9", "quantity": 1}], "bad_line"),
    ([{"line_id": "L1", "quantity": 1}, {"line_id": "L1", "quantity": 1}], "bad_line"),
    ([{"line_id": "L1", "quantity": 0}], "bad_quantity"),
    ([{"line_id": "L1", "quantity": True}], "bad_quantity"),
    ([{"line_id": "L1", "quantity": "1"}], "bad_quantity"),
    ([{"line_id": "L1", "quantity": 2}], "too_many"),
])
def test_return_items_are_checked(maya, items, code):
    assert refused(maya.request_return, "1042", items, "changed_mind", "refund").code == code


@pytest.mark.parametrize("reason, resolution, code", [("because", "refund", "bad_reason"), ("changed_mind", "cash", "bad_resolution")])
def test_return_reason_and_resolution_must_be_known(maya, reason, resolution, code):
    assert refused(maya.request_return, "1042", [{"line_id": "L1", "quantity": 1}], reason, resolution).code == code


def test_asking_twice_gives_the_same_request(maya):
    first = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund")
    again = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund")
    assert first == again and len(maya.requests) == 1


def test_an_approved_return_cannot_be_requested_again(maya):
    maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund")
    maya.decide("R-1", True)
    assert refused(maya.request_return, "1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund").code == "too_many"
    assert maya.get_order("1042")["items"][0]["return_status"] == "Return approved"


def test_refunds_over_300_dollars_are_flagged_for_a_manager(state):
    state.verify_customer("1055", ELENA)
    result = state.request_return("1055", [{"line_id": "L1", "quantity": 1}], "changed_mind", "refund")
    assert result["refund_if_approved"] == "$642.05"
    assert state.requests[0]["flags"] == ["Refunds on this order over $300.00: needs a manager"]


def test_the_manager_flag_counts_every_refund_on_the_order(maya):
    maya.orders["1042"]["lines"][0].update(qty=2, price=25900)  # two $259 jackets
    maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "defective", "refund")
    second = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund")
    assert maya.requests[0]["flags"] == [] and second and maya.requests[1]["id"] == "R-2"
    assert maya.requests[1]["flags"] == ["Refunds on this order over $300.00: needs a manager"]


def test_the_fee_applies_only_to_a_change_of_mind_or_the_wrong_size(maya):
    other = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "other", "refund")
    assert other["refund_if_approved"] == "$189.00" and "return_fee_deducted" not in other
    assert maya.requests[0]["flags"] == ["Reason given as other: check it with the customer"]


def test_a_new_reason_for_the_same_items_replaces_the_pending_request(maya):
    maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "changed_mind", "refund")
    newer = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "defective", "refund")
    assert [r["status"] for r in maya.requests] == ["replaced", "pending"]
    assert newer["refund_if_approved"] == "$189.00"


def test_refusals_do_not_repeat_what_was_sent(maya):
    e = refused(maya.request_return, "1042", [{"line_id": "$500.00 store credit", "quantity": 1}], "changed_mind", "refund")
    assert "$500" not in e.message


def test_odd_quantities_never_crash_the_duplicate_check(maya):
    maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "changed_mind", "refund")
    e = refused(maya.request_return, "1042", [{"line_id": "L1", "quantity": None}, {"line_id": "L1", "quantity": 1}],
                "changed_mind", "refund")
    assert e.code in ("bad_quantity", "bad_line")


def test_emails_are_masked_in_the_activity_log_and_in_summaries(state):
    state.log("customer", "message to the team", "I'm maya.lopez@example.com, please help")
    state.hand_off("customer_asked", "Customer maya.lopez@example.com wants a person.", "normal")
    assert "maya.lopez@" not in str(state.audit) and "m***@example.com" in state.audit[0]["detail"]
    assert "maya.lopez@" not in state.tickets[0]["summary"]


# ------------------------------------------------------------------ exchanges: the store checks stock
def test_an_exchange_needs_the_size_in_stock(maya):
    result = maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "exchange", "L")
    assert result["exchange_for_size"] == "L" and maya.requests[0]["amount"] is None
    assert maya.stock["NL-RJ"]["L"] == 3  # reserved only when a person approves
    maya.decide("R-1", True)
    assert maya.stock["NL-RJ"]["L"] == 2


def test_exchange_sizes_are_checked(maya):
    assert refused(maya.request_return, "1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "exchange", "M").code == "same_size"
    assert refused(maya.request_return, "1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "exchange", "XXXL").code == "bad_size"
    maya.stock["NL-RJ"]["L"] = 0
    e = refused(maya.request_return, "1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "exchange", "L")
    assert e.code == "size_out_of_stock" and "XS, S, XL" in e.message


def test_a_size_that_sold_out_before_approval_cannot_be_approved(maya):
    maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "exchange", "XL")
    maya.stock["NL-RJ"]["XL"] = 0
    assert refused(maya.decide, "R-1", True).code == "size_gone"
    assert maya.requests[0]["status"] == "pending"


def test_one_size_products_can_be_refunded_but_not_exchanged(state):
    state.verify_customer("1055", ELENA)
    assert refused(state.request_return, "1055", [{"line_id": "L1", "quantity": 1}], "wrong_size", "exchange", "L").code == "no_sizes"


# ------------------------------------------------------------------ cancellations and address changes
def test_an_order_that_has_not_shipped_can_be_cancelled_after_approval(state):
    state.verify_customer("1051", DANIEL)
    result = state.request_cancellation("1051", "ordered the wrong tent")
    assert result["refund_if_approved"] == "$418.00"
    assert state.request_cancellation("1051", "again") == result and len(state.requests) == 1
    assert state.orders["1051"]["status"] == "processing"  # nothing changes before a person approves
    state.decide("R-1", True)
    assert state.orders["1051"]["status"] == "cancelled"
    assert "$418.00" in state.notices[-1]["text"] and "Mastercard ending 5307" in state.notices[-1]["text"]
    assert refused(state.request_cancellation, "1051", "x").code == "already_cancelled"


def test_a_shipped_order_cannot_be_cancelled(maya):
    assert refused(maya.request_cancellation, "1043", "too slow").code == "already_shipped"


NEW_ADDRESS = {"name": "Daniel Kim", "line1": "12 Pine St", "line2": "", "city": "Seattle", "region": "WA",
               "postal_code": "98101", "country": "us"}


def test_an_address_change_is_checked_and_applied_only_after_approval(state):
    state.verify_customer("1051", DANIEL)
    result = state.request_address_change("1051", NEW_ADDRESS)
    assert result["new_address"] == "Daniel Kim, 12 Pine St, Seattle, WA 98101, US"
    assert state.orders["1051"]["address"]["city"] == "Vancouver"
    state.decide("R-1", True)
    assert state.orders["1051"]["address"]["city"] == "Seattle"


@pytest.mark.parametrize("change, code", [
    ({"city": ""}, "address_incomplete"),
    ({"country": "GB"}, "country_not_served"),
    ({"postal_code": "SW1A 1AA"}, "bad_postal_code"),
])
def test_bad_addresses_are_refused(state, change, code):
    state.verify_customer("1051", DANIEL)
    assert refused(state.request_address_change, "1051", {**NEW_ADDRESS, **change}).code == code


def test_a_canadian_postal_code_is_accepted_and_a_newer_address_replaces_a_pending_one(state):
    state.verify_customer("1051", DANIEL)
    state.request_address_change("1051", NEW_ADDRESS)
    state.request_address_change("1051", {**NEW_ADDRESS, "city": "Toronto", "region": "ON", "postal_code": "M5V 2T6", "country": "CA"})
    assert [r["status"] for r in state.requests] == ["replaced", "pending"]


# ------------------------------------------------------------------ hand-offs and the team's decisions
def test_a_hand_off_pauses_the_assistant_and_collects_follow_ups(maya):
    first = maya.hand_off("damaged_item", "Stove arrived broken.", "normal")
    assert maya.ai_paused and first["status"] == "passed to a person on the team" and maya.tickets[0]["id"] == "T-1"
    maya.hand_off("upset_customer", "Customer is upset.", "urgent")
    ticket = maya.tickets[0]
    assert len(maya.tickets) == 1 and ticket["priority"] == "urgent" and "Customer is upset." in ticket["summary"]
    assert ticket["customer"] == "m***@example.com"


def test_only_the_code_can_open_a_held_reply_ticket(state):
    state.hand_off("reply_held", "x", "normal")
    assert state.tickets[0]["reason"] == "other"
    state.return_to_assistant()
    state.hand_off("reply_held", "x", "normal", source="system", draft="draft text")
    assert state.tickets[1]["reason"] == "reply_held" and state.tickets[1]["draft"] == "draft text"


def test_rejecting_needs_a_reason_and_tells_the_customer(maya):
    maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund")
    assert refused(maya.decide, "R-1", False).code == "note_required"
    maya.decide("R-1", False, "The tags were removed.")
    assert maya.requests[0]["status"] == "rejected"
    assert maya.notices[-1]["kind"] == "rejected" and "The tags were removed." in maya.notices[-1]["text"]
    assert refused(maya.decide, "R-1", True).code == "already_decided"
    assert refused(maya.decide, "R-9", True).code == "no_request"


def test_an_approved_return_tells_the_customer_the_amount_and_where_the_label_goes(maya):
    maya.request_return("1042", [{"line_id": "L1", "quantity": 1}], "wrong_size", "refund")
    maya.decide("R-1", True)
    notice = maya.notices[-1]["text"]
    assert "$182.05" in notice and "m***@example.com" in notice and "Visa ending 1881" in notice


def test_team_replies_and_handing_back(maya):
    assert refused(maya.team_reply, "  ").code == "empty_reply"
    assert refused(maya.return_to_assistant).code == "no_open_ticket"
    maya.hand_off("customer_asked", "Wants a person.", "normal")
    maya.team_reply("Hi Maya, Jordan here.")
    maya.return_to_assistant()
    assert not maya.ai_paused and maya.notices[-1]["kind"] == "back_to_ai"


# ------------------------------------------------------------------ products
def test_product_search_shows_stock_and_restock_dates(state):
    jacket = state.find_products("rain jacket")["products"][0]
    assert jacket["product"] == "Northline Rain Jacket" and "M" in jacket["out_of_stock"] and "L" in jacket["in_stock"]
    cover = state.find_products("rain cover")["products"][0]
    assert cover["in_stock"] == [] and cover["restock_expected"]
    assert state.find_products("socks")["products"][0]["final_sale"] is True
    assert state.find_products("kayak")["products"] == []


def test_the_store_is_the_same_every_time_for_the_same_day():
    assert StoreState.new(TODAY).orders == StoreState.new(TODAY).orders
