"""The demo page in a real browser (an installed Chrome or Edge, through Playwright).
Skipped when Playwright or a browser is not available."""
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


@pytest.fixture
def page(browser, site):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(site + "/")
    page.locator(".starter").first.wait_for()
    yield page
    context.close()
    assert errors == []


def last_bot(page) -> Any:
    bots = page.locator(".msg.bot")
    return bots.nth(bots.count() - 1)


def test_the_page_opens_with_starters_and_the_guided_mode_when_no_key_is_set(page):
    assert page.locator(".starter").count() == 8
    assert page.locator("#mode-live").is_disabled() and page.locator("#mode-guided").get_attribute("aria-pressed") == "true"
    assert "No requests yet" in page.locator("#tab-body").inner_text()
    assert "What the code enforces" in page.locator("#tab-body").inner_text()


def test_customer_asks_team_approves_customer_sees_the_update(page):
    page.get_by_role("button", name="Return a jacket").click()
    page.locator(".card .btn.primary").wait_for()
    assert "$182.05" in last_bot(page).inner_text()
    assert page.locator(".msg.bot .step").count() == 3
    assert page.locator("#badge-approvals").inner_text() == "1"
    page.locator(".card .btn.primary").click()
    notice = page.locator(".notice.approved")
    notice.wait_for()
    assert "$182.05" in notice.inner_text() and page.locator(".pill.approved").count() == 1
    assert page.locator("#badge-approvals").is_hidden()


def test_rejecting_needs_a_reason(page):
    page.get_by_role("button", name="Cancel an order").click()
    page.locator(".card .btn.danger").wait_for()
    page.locator(".card .btn.danger").click()
    assert "reason" in page.locator("#toast").inner_text().lower()
    page.locator(".note-input").fill("This order already left our warehouse.")
    page.locator(".card .btn.danger").click()
    page.locator(".notice.rejected").wait_for()
    assert "already left our warehouse" in page.locator(".notice.rejected").inner_text()


def test_a_hand_off_lets_the_team_reply_and_hand_back(page):
    page.get_by_role("button", name="Arrived damaged").click()
    page.locator(".reply-input").wait_for()
    assert page.locator(".tabs [data-tab=handoffs]").get_attribute("aria-selected") == "true"
    page.locator(".reply-input").fill("Hi Priya, Jordan here. Could you send a photo of the stove and the box?")
    page.get_by_role("button", name="Send reply").click()
    page.locator(".msg.teammate").wait_for()
    assert "Jordan" in page.locator(".msg.teammate").inner_text()
    assert page.locator("#input").get_attribute("placeholder") == "Write to the team"
    page.get_by_role("button", name="Hand back to the assistant").click()
    page.locator(".notice.back_to_ai").wait_for()


def test_sources_open_to_show_the_quoted_policy(page):
    page.locator("#input").fill("Do you ship to the UK?")
    page.locator("#input").press("Enter")
    page.locator(".src").wait_for()
    page.locator(".src").first.click()
    assert "United States and Canada" in page.locator(".quote").inner_text()


def test_the_chat_survives_a_reload_and_new_chat_resets_the_store(page):
    page.get_by_role("button", name="Return a jacket").click()
    page.locator(".card").first.wait_for()
    page.reload()
    page.locator(".card").first.wait_for()
    assert page.locator(".msg.customer").count() == 1
    page.locator("#new-chat").click()
    page.locator(".starter").first.wait_for()
    assert page.locator(".card:not(.guard)").count() == 0


def test_on_a_phone_the_customer_and_team_views_switch(browser, site):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.goto(site + "/")
    page.locator(".starter").first.wait_for()
    assert page.locator("#pane-team").is_hidden() and page.locator(".switcher").is_visible()
    page.get_by_role("button", name="Where is my order?").click()
    last_bot(page).wait_for()
    page.get_by_role("button", name="Team inbox").click()
    assert page.locator("#pane-team").is_visible() and page.locator("#pane-customer").is_hidden()
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= 390
    context.close()


def test_with_a_live_model_the_reply_shows_the_model_and_its_cost(browser):
    live = ScriptedModel([tool(1, "search_help_center", query="ship to UK")],
                         [text("We only ship to the US and Canada for now.")])
    url, server = start_server(Agent(live=live))
    try:
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(url + "/")
        page.locator(".starter").first.wait_for()
        assert page.locator("#mode-live").get_attribute("aria-pressed") == "true"
        assert "Claude Haiku 5.5" in page.locator("#mode-live").inner_text()
        page.locator("#input").fill("Do you ship to the UK?")
        page.locator("#input").press("Enter")
        last_bot(page).wait_for()
        meta = last_bot(page).inner_text()
        assert "Claude Haiku 5.5 · $0.0" in meta and "Searched the help center" in meta
        page.get_by_role("tab", name="Cost").click()
        assert "AI cost of this chat so far" in page.locator("#tab-body").inner_text()
        context.close()
    finally:
        server.should_exit = True
