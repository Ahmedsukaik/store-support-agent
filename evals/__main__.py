"""Run the conversation scenarios in evals/scenarios.jsonl and report what passed.

    python -m evals                 the guided rules, locally (no AI, no cost)
    python -m evals --live          Claude, locally (needs ANTHROPIC_API_KEY in the environment or .env)
    python -m evals --url URL       through a deployed demo's HTTP API, whatever it runs

Each scenario is one fresh chat. What is checked is what matters to a store: which tools
ran, whether a request reached the team with the right amount, whether the chat went to a
person for the right reason, and what the replies said or must never say. Scenarios marked
"modes": ["live"] need a language model (free-form addresses, another language) and are
skipped for the guided rules.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCENARIOS = Path(__file__).resolve().parent / "scenarios.jsonl"


def load(path: Path = SCENARIOS) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class LocalRunner:
    def __init__(self, live: bool):
        from support.agent import Agent
        from support.models import ClaudeModel
        self.mode = "live" if live else "guided"
        self.agent = Agent(live=ClaudeModel() if live else None)

    def chat(self, messages: list[str]) -> dict[str, Any]:
        from support.session import new_session
        session = new_session()
        out: dict[str, Any] = {}
        for message in messages:
            out = self.agent.handle(session, {"type": "customer", "text": message}, want_live=self.mode == "live")
            session = out["session"]
            while out["more"]:
                out = self.agent.handle(session, {"type": "continue"}, want_live=self.mode == "live")
                session = out["session"]
        return out["view"]


class HttpRunner:
    def __init__(self, url: str):
        import httpx
        self.url = url.rstrip("/")
        self.http = httpx.Client(timeout=90)
        self.mode = "live" if self.http.get(self.url + "/api/config").json().get("live") else "guided"

    def chat(self, messages: list[str]) -> dict[str, Any]:
        out = self.http.post(self.url + "/api/new", json={}).json()
        for message in messages:
            out = self._step(out["session"], {"type": "customer", "text": message})
            while out["more"]:
                out = self._step(out["session"], {"type": "continue"})
        return out["view"]

    def _step(self, session: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        res = self.http.post(self.url + "/api/step", json={"session": session, "action": action, "live": True})
        res.raise_for_status()
        return res.json()


def judge(scenario: dict[str, Any], view: dict[str, Any], mode: str) -> list[str]:
    """What went wrong, in plain words. Empty means the scenario passed."""
    problems: list[str] = []
    replies = [t for t in view["transcript"] if t["kind"] == "assistant"]
    if not replies:
        return ["no reply"]
    final = replies[-1]["text"].lower()
    shown = " ".join(r["text"] for r in replies).lower()
    tools_ok = {s["tool"] for r in replies for s in r["steps"] if s["ok"]}
    if mode == "live" and any(r["mode"] != "live" for r in replies):
        problems.append("a reply came from the guided fallback, not the live model")
    if any(r["held"] for r in replies) and not scenario.get("held_ok"):
        problems.append("a reply was held: " + "; ".join(p for r in replies for p in r["problems"]))
    for name in scenario.get("tools_all", []):
        if name not in tools_ok:
            problems.append(f"{name} was not called successfully")
    for name in scenario.get("no_tools", []):
        if name in tools_ok:
            problems.append(f"{name} ran but must not have")
    requests = view["requests"]
    if scenario.get("no_request") and requests:
        problems.append(f"a request was created: {requests[0]['title']}")
    want = scenario.get("request")
    if want:
        match = [r for r in requests if r["kind"] == want["kind"] and r["status"] == "pending"]
        if want.get("resolution") == "exchange":
            match = [r for r in match if r["title"].startswith("Exchange")]
        if want.get("amount"):
            match = [r for r in match if r["amount"] == want["amount"]]
        if want.get("flag"):
            match = [r for r in match if any(want["flag"] in f for f in r["flags"])]
        if not match:
            got = ", ".join(f"{r['kind']} {r['amount']}" for r in requests) or "none"
            problems.append(f"expected a pending {want['kind']} request {want.get('amount', '')} (got {got})".replace("  ", " "))
    reasons = [t["reason"] for t in view["tickets"]]
    if scenario.get("ticket") and not set(scenario["ticket"]) & set(reasons):
        problems.append(f"expected a hand-off for {' or '.join(scenario['ticket'])} (got {', '.join(reasons) or 'none'})")
    if scenario.get("no_ticket") and reasons:
        problems.append(f"handed off without need ({', '.join(reasons)})")
    if scenario.get("reply_any") and not any(s.lower() in final for s in scenario["reply_any"]):
        problems.append(f"the last reply mentions none of: {', '.join(scenario['reply_any'])}")
    for s in scenario.get("reply_none", []):
        if s.lower() in shown:
            problems.append(f"a reply says {s!r}")
    if scenario.get("no_done_claim"):
        from support.checks import done_claim
        claims = [c[0] for r in replies if not r["held"] and (c := done_claim(r["text"]))]
        if claims:
            problems.append(f"a reply says something is done: {claims[0]!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the support agent's conversation scenarios")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--live", action="store_true", help="use Claude locally (needs ANTHROPIC_API_KEY)")
    target.add_argument("--url", help="run through a deployed demo, e.g. https://example.vercel.app")
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--pause", type=float, default=None, help="seconds between scenarios (default 0, or 25 with --url)")
    parser.add_argument("--save", action="store_true", help="write the results to evals/results/")
    args = parser.parse_args()
    if args.live:
        from demo import load_env
        load_env(ROOT / ".env")
    runner: LocalRunner | HttpRunner = HttpRunner(args.url) if args.url else LocalRunner(args.live)
    pause = args.pause if args.pause is not None else (25.0 if args.url else 0.0)
    scenarios = load()
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    results, cost = [], 0.0
    print(f"Running {len(scenarios)} scenarios ({runner.mode})\n")
    for i, scenario in enumerate(scenarios):
        if runner.mode not in scenario.get("modes", ["live", "guided"]):
            results.append({"id": scenario["id"], "status": "skipped"})
            print(f"  skip  {scenario['id']}")
            continue
        if i and pause:
            time.sleep(pause)
        try:
            view = runner.chat(scenario["say"])
            problems = judge(scenario, view, runner.mode)
            cost += view["metrics"].get("cost_usd", 0.0)
            replies = [t["text"] for t in view["transcript"] if t["kind"] == "assistant"]
        except Exception as e:  # a failed request is a failed scenario, not a crash
            problems, replies = [f"error: {type(e).__name__}: {e}"], []
        status = "pass" if not problems else "FAIL"
        results.append({"id": scenario["id"], "status": status, "problems": problems, "replies": replies})
        print(f"  {status}  {scenario['id']}" + ("".join(f"\n          - {p}" for p in problems)))
    ran = [r for r in results if r["status"] != "skipped"]
    passed = sum(r["status"] == "pass" for r in ran)
    print(f"\n{passed}/{len(ran)} passed, {len(results) - len(ran)} skipped" + (f", cost ${cost:.4f}" if cost else ""))
    if args.save:
        out = ROOT / "evals" / "results" / f"{date.today().isoformat()}-{runner.mode}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({"mode": runner.mode, "passed": passed, "ran": len(ran), "cost_usd": round(cost, 5),
                                   "results": results}, indent=2), encoding="utf-8")
        print(f"saved {out.relative_to(ROOT)}")
    return 0 if passed == len(ran) else 1


if __name__ == "__main__":
    raise SystemExit(main())
