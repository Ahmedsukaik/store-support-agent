"""The reply checks, the tool definitions and the tool runner, and the help center search."""
from __future__ import annotations

import json

import pytest
from conftest import MAYA

from support.checks import amounts, check_reply, done_claim
from support.helpcenter import HelpCenter, load_passages
from support.tools import TOOLS, run_tool

HC = HelpCenter()


# ------------------------------------------------------------------ checks on replies
def test_amounts_are_read_in_every_usual_form():
    assert amounts("$182.05, $189, $1,250.5 and US$6.95") == {18205, 18900, 125050, 695}
    assert amounts("CA$500, $  75, 182 USD, 50 dollars, $182,05") == {50000, 7500, 18200, 5000, 18205}
    assert amounts("order #1042 costs nothing, 30 days, size 10") == set()


def test_a_reply_may_only_use_amounts_the_store_gave():
    ok = check_reply("Your refund will be $182.05.", '{"refund_if_approved": "$182.05"}', approved=set())
    assert ok.ok and ok.checked == ["1 amount found in the sources", "no claim that something is already done"]
    bad = check_reply("I can add $50 store credit and refund $182.05.", '{"refund_if_approved": "$182.05"}', set())
    assert not bad.ok and bad.problems[0] == ("mentions $50.00, which is not in the order data, the help center or "
                                              "the team's messages")


def test_an_item_price_cannot_be_passed_off_as_the_refund():
    sources = '{"price": "$189.00"} {"refund_if_approved": "$182.05", "return_fee_deducted": "$6.95"}'
    bad = check_reply("Your refund will be $189.00.", sources, set())
    assert bad.problems == ["calls $189.00 a refund, but the store worked out a different amount"]
    assert check_reply("The jacket was $189.00; your refund will be $182.05, after the $6.95 fee.", sources, set()).ok
    policy = "<passage>Below $75, standard shipping costs $6.95.</passage>"
    assert check_reply("Refunds include shipping only for defects; orders under $75 pay $6.95.", policy, set()).ok


def test_189_and_189_00_are_the_same_amount():
    assert check_reply("It was $189.", "price $189.00", set()).ok


@pytest.mark.parametrize("reply", [
    "I've refunded your order.",
    "I have cancelled order #1051 for you.",
    "Your refund has been issued.",
    "Great news, the return has been approved!",
    "Your order is cancelled.",
    "We've processed your refund.",
    "The refund is on its way.",
    "I have updated your address.",
    "If you have any questions, just ask. Your refund has been issued.",
    "If you have any questions, your refund has been issued anyway.",
    "Your order #1051 is now cancelled.",
    "I have issued a full refund of your order.",
    "All set: your refund of the jacket is on its way.",
    "I've gone ahead and cancelled it.",
    "Your cancellation is confirmed.",
    "You've been refunded.",
    "It's been cancelled.",
    "It has been successfully cancelled.",
    "I went ahead and refunded it.",
    "Refund issued.",
    "The cancellation went through.",
    "Great news after all the waiting - your refund has been issued.",
    "The team approved your return, so your refund is coming.",
    "Your return was accepted.",
    "We've accepted your return.",
    "I've added a store credit to your account.",
])
def test_a_reply_must_not_say_something_is_done_before_a_person_approved_it(reply):
    verdict = check_reply(reply, "", approved=set())
    assert not verdict.ok and any("the team has not approved that" in p for p in verdict.problems), done_claim(reply)
    assert check_reply(reply, "", approved=True).ok


def test_sums_and_negations_next_to_refund_are_not_refund_amounts():
    sources = '{"price": "$189.00", "refund_if_approved": "$182.05", "return_fee_deducted": "$6.95"}'
    assert check_reply("Your refund will be $189.00 minus the $6.95 fee, so $182.05.", sources, set()).ok
    assert check_reply("You won't get the full $189.00 refund, because of the $6.95 fee.", sources, set()).ok


def test_amounts_from_the_team_count_as_refunds():
    assert check_reply("As Jordan said, we'll refund you $20.00 for the delay.", "<team>We'll refund you $20 for the delay.</team>",
                       set()).ok
    assert not check_reply("Your refund will be $14.95.", "Express shipping costs $14.95.", set()).ok


def test_an_amount_on_its_way_is_a_claim():
    sources = '{"refund_if_approved": "$182.05"}'
    assert not check_reply("Your $182.05 is on its way back to your card.", sources, set()).ok
    assert check_reply("Your $182.05 is on its way back to your card.", sources, {"return"}).ok


def test_the_kind_of_a_claim_comes_from_its_whole_clause():
    assert not check_reply("Your refund has been processed.", "", approved={"address_change"}).ok


def test_one_approval_does_not_unlock_claims_of_another_kind():
    assert not check_reply("Your order has been cancelled.", "", approved={"return"}).ok
    assert not check_reply("I've updated your address.", "", approved={"cancellation"}).ok
    assert check_reply("Your refund has been approved.", "", approved={"return"}).ok
    assert check_reply("Your order has been cancelled.", "", approved={"cancellation"}).ok


@pytest.mark.parametrize("reply", [
    "I've sent your return request to the team. Once it's approved, you'll get a label.",
    "If it's approved, your refund goes back to your card.",
    "Your order has been delivered.",
    "I've asked the team to cancel it.",
    "Once your return is approved, we email you a prepaid return label.",
    "Your refund will be issued after your return is approved by the team.",
    "We'll let you know when the cancellation has been approved.",
    "Nothing has been approved yet.",
    "No refund has been issued yet; it is waiting for the team's review.",
    "I can't confirm that your refund has been approved.",
    "Your refund is processed 5 to 7 business days after the return arrives.",
    "The refund is issued to your original payment method once the return arrives.",
    "Your request is approved or rejected by a person, usually within one business day.",
    "Thanks, Maya. I've sent your return request to our team (request R-1).",
    "Your return request has been sent to our team.",
    "It's been sent for review.",
    "Your cancellation request has been sent to the team.",
    "Your order #1051 is confirmed and still processing.",
    "Order #1043 was processed and picked up by ParcelGo.",
    "I've added your new address to a change request for the team.",
    "I've processed your return request and passed it to the team.",
    "Your cancellation request is confirmed as received.",
])
def test_describing_the_next_step_is_fine(reply):
    assert check_reply(reply, "", approved=set()).ok, done_claim(reply)


# ------------------------------------------------------------------ tool definitions
def _objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


def test_tool_schemas_follow_the_strict_tool_use_rules():
    banned = {"minimum", "maximum", "minLength", "maxLength", "multipleOf", "$ref"}
    for t in TOOLS:
        assert t["strict"] is True and t["description"]
        for obj in _objects(t["input_schema"]):
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) <= set(obj["properties"])
        assert not banned & set(json.dumps(t["input_schema"]).replace('"', " ").split()), t["name"]


def test_every_tool_has_a_runner(state):
    for t in TOOLS:
        result = run_tool(state, HC, t["name"], {})
        assert "unknown tool" not in result.label


# ------------------------------------------------------------------ running tools with untrusted input
def test_an_unknown_tool_is_refused_and_logged(state):
    result = run_tool(state, HC, "delete_all_orders", {"confirm": True})
    assert result.is_error and json.loads(result.content)["error"] == "unknown_tool"
    assert state.audit[-1]["outcome"] == "refused"


@pytest.mark.parametrize("raw", [None, "text", 42, ["list"], {"order_number": {"$gt": ""}, "email": ["x"]}])
def test_malformed_input_is_refused_not_crashing(state, raw):
    result = run_tool(state, HC, "verify_customer", raw)
    assert result.is_error and json.loads(result.content)["error"] == "no_match"


def test_a_refusal_goes_back_to_the_model_as_an_error_with_the_reason(maya):
    result = run_tool(maya, HC, "request_return", {"order_number": "1042", "items": [{"line_id": "L2", "quantity": 1}],
                                                   "reason": "changed_mind", "resolution": "refund"})
    data = json.loads(result.content)
    assert result.is_error and data["error"] == "final_sale" and result.label.startswith("Refused: ")


def test_items_that_are_not_objects_are_ignored(maya):
    result = run_tool(maya, HC, "request_return", {"order_number": "1042", "items": ["L1", 3, None],
                                                   "reason": "changed_mind", "resolution": "refund"})
    assert json.loads(result.content)["error"] == "no_items"


def test_labels_say_what_happened(state):
    assert run_tool(state, HC, "verify_customer", {"order_number": "#1042", "email": MAYA}).label == "Verified Maya with order #1042"
    assert run_tool(state, HC, "get_order", {"order_number": "1042"}).label == "Opened order #1042"
    label = run_tool(state, HC, "request_return", {"order_number": "1042", "items": [{"line_id": "L1", "quantity": 1}],
                                                   "reason": "wrong_size", "resolution": "refund"}).label
    assert label == "Sent return request R-1 for team review (refund $182.05)"
    assert run_tool(state, HC, "hand_off_to_human", {"reason": "customer_asked", "summary": "x", "priority": "normal"}).label \
        == "Passed the chat to the team (T-1)"


def test_help_center_results_are_search_result_blocks_with_citations(state):
    result = run_tool(state, HC, "search_help_center", {"query": "how long do I have to return something"})
    assert isinstance(result.content, list) and result.passages
    block = result.content[0]
    assert block["type"] == "search_result" and block["citations"] == {"enabled": True}
    assert block["source"].startswith("helpcenter/") and all(c["type"] == "text" for c in block["content"])
    assert "30 days" in result.text()


def test_a_question_the_help_center_cannot_answer_says_so(state):
    result = run_tool(state, HC, "search_help_center", {"query": "zzz qqq"})
    assert isinstance(result.content, str) and "Do not guess" in result.content


# ------------------------------------------------------------------ help center search
def test_every_article_section_becomes_a_passage_with_sentences():
    passages = load_passages()
    assert len(passages) >= 30 and len({p.source for p in passages}) == len(passages)
    assert all(p.sentences and p.article and p.section for p in passages)


@pytest.mark.parametrize("question, expected", [
    ("How long do I have to return something?", "returns#return-window"),
    ("Do you ship to the UK?", "shipping#where-we-ship"),
    ("my stove arrived broken", "damaged#damaged-in-transit"),
    ("can I return socks", "returns#final-sale-items"),
    ("how much is express shipping", "shipping#shipping-costs"),
    ("my parcel is stuck", "shipping#lost-parcels"),
    ("what does the warranty cover", "warranty#what-is-covered"),
    ("can I change my delivery address", "order-changes#changing-the-shipping-address"),
    ("how do I wash my rain jacket", "care#rain-jackets"),
    ("when is your team available", "contact#support-hours"),
])
def test_common_questions_find_the_right_section_in_the_top_three(question, expected):
    found = [p.source for p in HC.search(question)]
    assert any(s.endswith(expected) for s in found), found
