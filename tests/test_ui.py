"""The demo page in a real browser (an installed Chrome or Edge, through Playwright): the guided
story, free chat, the team inbox and the phone layout. Skipped when Playwright or a browser is
not available."""
from __future__ import annotations

from typing import Any

import pytest
from conftest import ScriptedModel, free_port, serve, text, tool

from support.agent import Agent
from support.web import create_app


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        for channel in ("chrome", "msedge"):
            try:
                launched = p.chromium.launch(channel=channel)
                break
            except Exception:
                continue
        else:
            pytest.skip("no installed Chrome or Edge for Playwright")
        yield launched
        launched.close()


def start_server(agent: Agent) -> tuple[str, Any]:
    port = free_port()
    return f"http://127.0.0.1:{port}", serve(create_app(agent), port)


@pytest.fixture(scope="module")
def site():
    url, server = start_server(Agent())
    yield url
    server.should_exit = True


def open_page(browser, url, width=1440, height=900, mobile=False):
    context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(url + "/")
    page.get_by_role("button", name="Start the demo").wait_for()
    return context, page, errors


@pytest.fixture
def page(browser, site):
    context, page, errors = open_page(browser, site)
    yield page
    context.close()
    assert errors == []


def button(page, name):
    return page.get_by_role("button", name=name, exact=True)


def test_the_first_screen_says_what_it_is_and_offers_one_step(page):
    assert "It answers your customers. Your team approves the money." in page.locator("#story").inner_text()
    assert page.locator(".story-actions button").count() == 2  # Start, and Skip to free chat
    assert page.evaluate("getComputedStyle(document.querySelector('.layout')).pointerEvents") == "none"
    assert "fictional" in page.locator("body").inner_text().lower()


def test_the_whole_story_in_six_steps(page):
    button(page, "Start the demo").click()
    button(page, "Send Maya's message").click()
    page.locator(".story-title", has_text="Your turn: approve the refund.").wait_for()
    card = page.locator(".req").first
    assert "$182.05" in card.inner_text() and "Return fee" in card.inner_text() and "Worked out by the store's code" in card.inner_text()
    assert "ring" in (button(page, "Approve refund").get_attribute("class") or "")
    button(page, "Approve refund").click()
    page.locator(".story-title", has_text="Maya hears back in seconds.").wait_for()
    assert "$182.05" in page.locator(".event").inner_text()
    button(page, "Next: try to trick it").click()
    button(page, "Send the trick").click()
    page.locator(".story-title", has_text="The trick got nothing.").wait_for()
    assert "Refunds created: 0" in page.locator("#story").inner_text()
    assert "Earlier in this demo" in page.locator("#panel").inner_text()  # Maya's approved return stays visible
    button(page, "Next: catch a bad reply").click()
    button(page, "Send the customer's message").click()
    page.locator(".story-title", has_text="Stopped before the customer saw it.").wait_for()
    assert "A draft was stopped by a check" in page.locator("#thread").inner_text()
    assert "$50.00 store credit" not in page.locator("#thread").inner_text()
    assert "$50.00 store credit" in page.locator(".held-box").inner_text()
    button(page, "See what it cost").click()
    assert "Cost shows when the live AI answers" in page.locator("#story").inner_text()  # this server has no key
    button(page, "Try it yourself").click()
    page.locator(".story-title", has_text="Ask anything a customer would").wait_for()
    assert not page.locator("#input").is_disabled() and page.locator(".chip").count() >= 6


def test_no_jargon_while_under_the_hood_is_closed(page):
    button(page, "Start the demo").click()
    button(page, "Send Maya's message").click()
    page.locator(".story-title", has_text="Your turn").wait_for()
    visible = page.evaluate("document.body.innerText").lower()
    for word in ("token", "tool call", "guided", "handoff", "r-1", "t-1", "simulation"):
        assert word not in visible, word


def test_rejecting_needs_a_reason_shown_in_the_card(page):
    button(page, "Start the demo").click()
    button(page, "Send Maya's message").click()
    page.locator(".story-title", has_text="Your turn").wait_for()
    assert page.locator(".req .input").count() == 0  # the reason field only opens for a rejection
    button(page, "Reject").click()
    button(page, "Send rejection").click()
    assert "Write a short reason" in page.locator(".field-error").inner_text()
    page.locator(".req .input").fill("The tags were removed.")
    button(page, "Send rejection").click()
    page.locator(".event.rejected").wait_for()
    assert "The tags were removed." in page.locator(".event.rejected").inner_text()


def test_free_chat_with_the_test_customer_and_sources(page):
    page.get_by_role("button", name="Skip to free chat").click()
    button(page, "Use these").click()
    assert "#1042" in page.locator("#input").input_value()
    page.locator("#input").fill("Do you ship to the UK?")
    page.locator("#input").press("Enter")
    page.locator(".source").wait_for()
    page.locator(".source").first.click()
    assert "United States and Canada" in page.locator(".quote").inner_text()


def test_the_page_keeps_its_place_after_a_reload(page):
    button(page, "Start the demo").click()
    button(page, "Send Maya's message").click()
    page.locator(".story-title", has_text="Your turn").wait_for()
    page.reload()
    page.locator(".story-title", has_text="Your turn").wait_for()
    assert page.locator(".req").count() == 1


def test_on_a_phone_the_story_sits_at_the_bottom_and_switches_views(browser, site):
    context, page, errors = open_page(browser, site, 390, 844, mobile=True)
    button(page, "Start the demo").click()
    button(page, "Send Maya's message").click()
    page.locator(".story-title", has_text="Your turn").wait_for()
    assert page.locator(".team-pane").is_visible() and page.locator(".customer-pane").is_hidden()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    box = page.locator("[data-approve]").bounding_box()
    sheet = page.locator("#story").bounding_box()
    assert box and sheet and box["y"] + box["height"] <= sheet["y"]  # Approve is not under the story sheet
    button(page, "Approve refund").click()
    page.locator(".story-title", has_text="Maya hears back in seconds.").wait_for()
    assert page.locator(".customer-pane").is_visible()
    context.close()
    assert errors == []


def test_with_a_live_model_the_reply_is_marked_ai_and_the_inbox_shows_the_cost(browser):
    live = ScriptedModel([tool(1, "search_help_center", query="ship to UK")], [text("We only ship to the US and Canada for now.")])
    url, server = start_server(Agent(live=live))
    try:
        context, page, errors = open_page(browser, url)
        page.get_by_role("button", name="Skip to free chat").click()
        page.locator("#input").fill("Do you ship to the UK?")
        page.locator("#input").press("Enter")
        page.locator(".bot .tag", has_text="AI").wait_for()
        assert "AI cost of this chat: $0.0" in " ".join(page.locator("#cost-line").inner_text().split())
        assert "Checked the help center" in page.locator("#thread").inner_text()
        context.close()
        assert errors == []
    finally:
        server.should_exit = True
