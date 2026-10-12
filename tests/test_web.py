"""The web demo's HTTP API, as the page uses it."""
from __future__ import annotations

from typing import Any

import pytest
from conftest import MAYA, ScriptedModel, text
from fastapi.testclient import TestClient

from support.agent import Agent
from support.web import MAX_BODY, PER_VISITOR, Limiter, create_app, live_model_from_env


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Agent()))


def send(client: TestClient, session: dict[str, Any], action: dict[str, Any], **extra: Any) -> dict[str, Any]:
    res = client.post("/api/step", json={"session": session, "action": action, **extra})
    assert res.status_code == 200, res.text
    return res.json()


def say(client: TestClient, session: dict[str, Any], message: str, **extra: Any) -> dict[str, Any]:
    out = send(client, session, {"type": "customer", "text": message}, **extra)
    while out["more"]:
        out = send(client, out["session"], {"type": "continue"}, **extra)
    return out


def test_config_lists_the_suggestions_identities_and_whether_live_ai_is_on(client):
    cfg = client.get("/api/config").json()
    assert cfg["live"] is False and cfg["model"] is None and cfg["store"] == "Copperleaf Outdoor"
    assert len(cfg["suggestions"]) >= 8 and {i["email"] for i in cfg["identities"]} >= {MAYA}
    assert all(set(p) == {"sku", "name", "price", "category", "summary"} for p in cfg["products"])


def test_a_whole_conversation_over_http(client):
    session = client.post("/api/new", json={}).json()["session"]
    out = say(client, session, "I'd like to return the rain jacket from order #1042, it's too small. " + MAYA)
    assert out["view"]["requests"][0]["status"] == "pending"
    out = send(client, out["session"], {"type": "team", "action": "approve", "request_id": "R-1"})
    assert out["view"]["requests"][0]["status"] == "approved"
    assert out["view"]["transcript"][-1]["notice"] == "approved"


def test_the_page_can_reload_and_show_the_same_chat(client):
    session = client.post("/api/new", json={}).json()["session"]
    out = say(client, session, "Do you ship to the UK?")
    again = send(client, out["session"], {"type": "view"})
    assert again["view"]["transcript"] == out["view"]["transcript"] and again["session"] == out["session"]


def test_a_broken_session_asks_the_page_to_start_again(client):
    res = client.post("/api/step", json={"session": {"v": 1, "started": "2026-10-12", "events": [{"type": "robot"}]},
                                         "action": {"type": "view"}})
    assert res.status_code == 409 and res.json()["error"] == "session"


def test_an_impossible_team_action_is_explained(client):
    session = client.post("/api/new", json={}).json()["session"]
    res = client.post("/api/step", json={"session": session, "action": {"type": "team", "action": "approve", "request_id": "R-1"}})
    assert res.status_code == 409 and res.json() == {"error": "action", "message": "There is no request R-1."}


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"x" * (MAX_BODY + 1)], ids=["not json", "a list", "too big"])
def test_bad_bodies_are_refused(client, raw):
    res = client.post("/api/step", content=raw, headers={"Content-Type": "application/json"})
    assert res.status_code == 409 and res.json()["error"] == "action"


def test_a_visitor_over_the_limit_is_moved_to_the_guided_rules(monkeypatch):
    monkeypatch.setattr("support.web.PER_VISITOR", (1, 600))
    live = ScriptedModel([text("Hi from Claude.")], [text("Hi again from Claude.")])
    client = TestClient(create_app(Agent(live=live)))
    session = client.post("/api/new", json={}).json()["session"]
    first = say(client, session, "Hello", live=True)
    assert first["view"]["transcript"][-1]["mode"] == "live"
    second = say(client, first["session"], "Do you ship to the UK?", live=True)
    assert second["view"]["transcript"][-1]["mode"] == "guided" and len(live.calls) == 1


def test_visitors_are_counted_separately():
    limiter = Limiter()
    assert all(limiter.allow("a", 2, 60) for _ in range(2)) and not limiter.allow("a", 2, 60)
    assert limiter.allow("b", 2, 60)
    assert PER_VISITOR[0] >= 30  # enough for a long chat: each message is two or three model calls


def test_live_ai_needs_a_key_and_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert live_model_from_env() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-local-test-not-a-real-key")
    monkeypatch.setenv("SUPPORT_LIVE_AI", "off")
    assert live_model_from_env() is None
    monkeypatch.setenv("SUPPORT_LIVE_AI", "on")
    assert live_model_from_env() is not None


def test_the_page_and_its_files_are_served_locally(client):
    page = client.get("/")
    assert page.status_code == 200 and "Store Support Agent" in page.text
    assert client.get("/app.js").status_code == 200 and client.get("/style.css").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}


def test_the_page_has_no_contact_details():
    import re
    from pathlib import Path
    public = Path(__file__).resolve().parent.parent / "public"
    for name in ("index.html", "app.js"):
        page = (public / name).read_text(encoding="utf-8")
        emails = re.findall(r"[\w.+-]+@[a-z][\w-]*\.[a-z]{2,}", page)
        assert all(e.endswith("@example.com") for e in emails), name  # only the fictional demo customers
        assert "mailto:" not in page and "tel:" not in page and "wa.me" not in page


def test_a_slow_model_call_does_not_block_other_visitors():
    import threading
    import time

    import httpx
    from conftest import free_port, serve

    class Slow:
        mode, name = "live", "claude-haiku-5-5"

        def respond(self, system, tools, messages):
            time.sleep(2.5)
            from support.models import Turn
            return Turn([text("Hi.")], "end_turn", self.name, "live")

    port = free_port()
    server = serve(create_app(Agent(live=Slow())), port)
    try:
        base = f"http://127.0.0.1:{port}"
        session = httpx.post(base + "/api/new", json={}).json()["session"]
        worker = threading.Thread(target=lambda: httpx.post(base + "/api/step", timeout=10, json={
            "session": session, "action": {"type": "customer", "text": "Hello"}, "live": True}))
        worker.start()
        time.sleep(0.3)
        started = time.monotonic()
        assert httpx.get(base + "/healthz").status_code == 200
        assert time.monotonic() - started < 1.2  # blocked, it would wait for the model (about 2.2 s)
        worker.join()
    finally:
        server.should_exit = True


def test_every_kind_of_request_is_counted_per_visitor(monkeypatch):
    monkeypatch.setattr("support.web.PER_VISITOR_REQUESTS", (3, 600))
    client = TestClient(create_app(Agent()))
    session = client.post("/api/new", json={}).json()["session"]
    codes = [client.post("/api/step", json={"session": session, "action": {"type": "view"}}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 409]


def test_deeply_nested_json_is_refused_cleanly(client):
    deep = b"[" * 5000 + b"]" * 5000
    res = client.post("/api/step", content=b'{"session": ' + deep + b"}", headers={"Content-Type": "application/json"})
    assert res.status_code == 409
    nested: dict = {}
    cursor = nested
    for _ in range(30):
        cursor["a"] = {}
        cursor = cursor["a"]
    assert client.post("/api/step", json={"session": nested, "action": {"type": "view"}}).status_code == 409


def test_an_unexpected_error_is_a_clean_500(monkeypatch):
    class Broken(Agent):
        def handle(self, *args, **kwargs):
            raise RuntimeError("boom")

    client = TestClient(create_app(Broken()), raise_server_exceptions=False)
    res = client.post("/api/step", json={"session": {}, "action": {"type": "view"}})
    assert res.status_code == 500 and res.json()["error"] == "server" and "boom" not in res.text


# ------------------------------------------------------------------ requests from other sites, IPv6 visitors, headers
@pytest.mark.parametrize("path", ["/api/new", "/api/step"])
@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"])
def test_api_posts_that_are_not_json_are_refused(client, path, content_type):
    headers = {"Content-Type": content_type} if content_type else {}
    res = client.post(path, content=b'{"session": {}, "action": {"type": "view"}}', headers=headers)
    assert res.status_code == 415 and res.json()["error"] == "request" and len(res.json()["message"]) < 80


@pytest.mark.parametrize("path", ["/api/new", "/api/step"])
def test_api_posts_marked_cross_site_by_the_browser_are_refused(client, path):
    res = client.post(path, json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403 and res.json()["error"] == "forbidden"
    assert client.post(path, content=b"{}", headers={"Sec-Fetch-Site": "cross-site", "Content-Type": "text/plain"}).status_code == 403


@pytest.mark.parametrize("fetch_site", [None, "same-origin", "same-site", "none"])
def test_the_page_own_json_posts_still_work(client, fetch_site):
    headers = {"Content-Type": "application/json; charset=utf-8"} | ({"Sec-Fetch-Site": fetch_site} if fetch_site else {})
    new = client.post("/api/new", content=b"{}", headers=headers)
    assert new.status_code == 200
    step = client.post("/api/step", json={"session": new.json()["session"], "action": {"type": "view"}}, headers=headers)
    assert step.status_code == 200
    assert client.get("/api/config", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200  # reading is not refused


def test_a_refused_request_reaches_neither_the_model_nor_the_limits(monkeypatch):
    monkeypatch.setattr("support.web.PER_VISITOR_REQUESTS", (1, 600))
    live = ScriptedModel([text("Hi from Claude.")])
    client = TestClient(create_app(Agent(live=live)))
    session = client.post("/api/new", json={}).json()["session"]
    body = {"session": session, "action": {"type": "customer", "text": "Hello"}, "live": True}
    assert client.post("/api/step", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/step", content=__import__("json").dumps(body), headers={"Content-Type": "text/plain"}).status_code == 415
    assert live.calls == []
    assert client.post("/api/step", json=body).status_code == 200  # the one allowed request was not used up


def test_ipv6_visitors_are_counted_by_their_64(monkeypatch):
    from support.web import visitor_key
    assert visitor_key("2001:db8:1:2:aaaa::1") == visitor_key("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert visitor_key("[2001:db8:1:2::1]") == "2001:db8:1:2::/64"
    assert visitor_key("2001:db8:1:3::1") != visitor_key("2001:db8:1:2::1")
    assert visitor_key("203.0.113.7") == "203.0.113.7" and visitor_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert visitor_key("203.0.113.8") != visitor_key("203.0.113.7")  # IPv4 stays one address per visitor
    assert visitor_key("not an address") == "not an address"
    monkeypatch.setattr("support.web.PER_VISITOR_REQUESTS", (2, 600))
    monkeypatch.setenv("VERCEL", "1")
    client = TestClient(create_app(Agent()))
    session = client.post("/api/new", json={}).json()["session"]

    def step(address: str) -> int:
        return client.post("/api/step", json={"session": session, "action": {"type": "view"}},
                           headers={"x-vercel-forwarded-for": address}).status_code

    assert [step(f"2001:db8:5:6::{i}") for i in range(1, 4)] == [200, 200, 409]  # a new address in the same /64 is the same visitor
    assert step("2001:db8:5:7::1") == 200  # the next /64 is someone else
    assert [step(f"198.51.100.{i}") for i in range(1, 4)] == [200, 200, 200]


def test_the_page_gets_the_same_security_headers_here_as_on_vercel(client):
    import json
    from pathlib import Path

    from support.web import SECURITY_HEADERS
    config = json.loads((Path(__file__).resolve().parent.parent / "vercel.json").read_text(encoding="utf-8"))
    on_vercel = {h["key"]: h["value"] for h in config["headers"][0]["headers"]}
    assert config["headers"][0]["source"] == "/(.*)" and on_vercel == SECURITY_HEADERS
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp and "unsafe-inline" not in csp
    assert "font-src https://fonts.gstatic.com" in csp and "style-src 'self' https://fonts.googleapis.com" in csp
    for res in (client.get("/"), client.get("/app.js"), client.get("/api/config"), client.post("/api/new", content=b"{}")):
        for name, value in SECURITY_HEADERS.items():
            assert res.headers[name] == value, (res.request.url, name)


def test_the_page_needs_no_inline_script_or_style():
    import re
    from pathlib import Path
    public = Path(__file__).resolve().parent.parent / "public"
    page = (public / "index.html").read_text(encoding="utf-8")
    assert all(' src="' in tag for tag in re.findall(r"<script\b[^>]*>", page))
    for name in ("index.html", "app.js"):
        source = (public / name).read_text(encoding="utf-8")
        assert not re.search(r"\sstyle=", source), name
        assert not re.search(r"<[^>]+\son[a-z]+=", source), name  # no onclick= and the like
