"""The scenario file stays valid, and the guided rules pass every scenario meant for them."""
from __future__ import annotations

import pytest

from evals.__main__ import LocalRunner, judge, load

SCENARIOS = load()
KEYS = {"id", "say", "modes", "tools_all", "no_tools", "request", "no_request", "ticket", "no_ticket",
        "reply_any", "reply_none", "held_ok", "no_done_claim"}


def test_scenarios_are_well_formed():
    assert len({s["id"] for s in SCENARIOS}) == len(SCENARIOS) >= 25
    for s in SCENARIOS:
        assert set(s) <= KEYS, s["id"]
        assert s["say"] and all(isinstance(m, str) and m for m in s["say"])


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if "guided" in s.get("modes", ["live", "guided"])],
                         ids=lambda s: s["id"])
def test_the_guided_rules_pass_their_scenarios(scenario):
    view = LocalRunner(live=False).chat(scenario["say"])
    assert judge(scenario, view, "guided") == []


def test_the_judge_catches_what_matters():
    scenario = {"id": "x", "say": ["hi"], "request": {"kind": "return", "amount": "$182.05"}, "reply_none": ["refunded"]}
    view = {"transcript": [{"kind": "assistant", "text": "You've been refunded.", "steps": [], "mode": "live", "held": False,
                            "problems": []}], "requests": [], "tickets": [], "metrics": {}}
    assert judge(scenario, view, "live") == ["expected a pending return request $182.05 (got none)", "a reply says 'refunded'"]
    view["transcript"][0]["mode"] = "guided"
    assert "a reply came from the guided fallback, not the live model" in judge(scenario, view, "live")
