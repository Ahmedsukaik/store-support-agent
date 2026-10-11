# Store Support Agent

An AI support agent for an online store. It answers customers in the store's chat, looks up
their orders, and starts returns, exchanges and cancellations, while a person on the team
approves every refund and every change.

**Live demo: https://store-support-agent-demo.vercel.app**
Press "Start the demo": six short steps take one return from the customer's chat to the
refund, and you are the one who approves it. Then you try to trick it, watch a bad reply get
stopped, and see what the chat cost. After that you can chat freely. Each visitor gets a
private copy of the store.

![The demo: a customer asks for a return, the team approves it, the customer is told](docs/img/tour.gif)

**For a store owner, in three lines:** customers get answers at any hour about orders, returns
and your policies. Your team only handles what needs a person, with the facts already
gathered. Nothing involving money happens without someone on your team clicking Approve.

## What it does

A store's support team answers the same questions all day: where is my order, can I return
this, do you ship to my country. This agent answers them in the chat, from the store's own
policies and order data, and passes anything that needs judgment to a person.

- **Policy questions** are answered from the help center, with the passage it quoted.
- **Orders** are shown only after the customer proves they are theirs (order number plus the
  email on that order).
- **Returns, exchanges, cancellations and address changes** become requests in the team
  inbox. The store works out the refund; a person approves or rejects it; the customer is told.
- **Damaged items, lost parcels, exceptions and upset customers** go to a person, with a
  summary they can act on.
- **Every reply is checked** before the customer sees it, and every step is logged.
- **The team sees the cost** of every chat, measured on that chat.

The store, Copperleaf Outdoor, is fictional: every name, order and email is made up. In a
real project `support/store.py` is replaced by calls to the store's own systems (for example
Shopify's Admin API and a helpdesk), and the rest stays.

## How it fits together

```
 customer chat                     support team inbox
      |                                  ^      |
      v                                  |      | approve / reject / reply
 +-----------+   tools (8)   +-------------------+------------+
 |  Claude   | ------------> | store rules, in code          |
 | Haiku 5.5 |  read: help   |  - identity check             |
 | (or the   |  center,      |  - own orders only            |
 |  guided   |  products,    |  - return window, final sale  |
 |  rules)   |  orders       |  - refund computed here       |
 +-----------+  ask: return, |  - requests wait for a person |
      |         cancel,      +-------------------------------+
      v         address,                 |
 reply checks   hand off            audit log, cost
 (amounts, "done" claims)
```

## What the code enforces, whatever the model says

The prompt asks the model to behave. These rules are enforced in code, so they hold even if a
customer talks the model into something:

1. **No order details before verification.** `get_order` and every request tool refuse until
   `verify_customer` matched an order number with the email on that order. A wrong email gets
   the same answer as a missing order, so the chat cannot be used to find out who ordered what.
   The third failed attempt locks verification for the chat and hands it to a person.
2. **Customers only see their own orders.** Another customer's order looks exactly like an
   order that does not exist.
3. **The model never sets an amount.** It names the items and the reason; the store checks the
   return window, final sale items, quantities and stock, and computes the refund (including
   the $6.95 return fee for a change of mind or the wrong size, and the shipping refund for a
   wrong or defective order). When refunds on one order add up to more than $300, the request
   is flagged for a manager.
4. **A person approves every refund and every change.** The request tools only create a
   request. Approving it is the only code path that changes an order, and it checks again at
   that moment (for example that the exchange size is still in stock).
5. **Every reply is checked before the customer sees it.** Every money amount in it must come
   from the store (order data, help center, the team's messages); amounts the customer typed
   do not count, and an amount called a refund must be one the store worked out or the team named. The
   reply must not say a refund, cancellation or change is done unless the team approved one of
   that kind. These checks catch the common English phrasings; they are a safety net under
   rules 1 to 4, not a replacement. A reply that fails is held: the customer is told a teammate
   will check, and the team sees the draft and the reason.
6. **Some cases always reach a person.** The code hands the chat over after a third failed
   verification, a held reply, a refusal or cut off reply from the model, or more than six
   steps for one message. The model is told to hand over damaged items, lost parcels, warranty
   claims and customers who ask, and the store's answers point it there. While a person has the
   chat, the model is not called and request tools are refused.
7. **Every step is logged**: each tool call, refusal and decision, with who made it. Email
   addresses in the log and in summaries are masked.

## Live AI or guided rules

With an API key, replies come from **Claude Haiku 5.5**. Each step is one API call with
thinking switched off and low effort (fast and cheap), tool inputs held to strict schemas,
and the instructions and tools cached and shared by every visitor. Help center passages are
passed as search results, so a reply can cite the exact sentence. The page shows the model,
the tool calls and the cost of every reply (all the model calls behind it).

Without a key, or when the key's budget is used up, the model is busy or down, a chat grows
too long, or one connection sends too many messages, the **guided rules** answer instead:
fixed rules with no AI that cover the demo's suggested questions and close variations, through
the same tools and the same checks. The page says when that happens. The guided rules also
stand in for Claude in the tests.

## Quick start

Needs Python 3.10 or newer. Developed and tested on Windows 11 with Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python demo.py                   # then open http://127.0.0.1:8300
```

Without an API key this runs the guided rules, at no cost. For live replies, copy
`.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Use a key from a separate Anthropic
workspace with a monthly spend limit (Console: Settings > Workspaces > Spend limits); when the
limit is reached, the demo moves to the guided rules on its own.

| Setting | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | not set | not set means guided rules only |
| `SUPPORT_SESSION_SECRET` | random per process | at least 32 characters; set it when more than one server instance runs |
| `SUPPORT_CLAUDE_EFFORT` | `low` | `low`, `medium` or `high` (higher levels cannot run with thinking off) |
| `SUPPORT_STRICT_TOOLS` | `on` | strict tool schemas |
| `SUPPORT_LIVE_AI` | `on` | `off` forces the guided rules |
| `SUPPORT_CLAUDE_MODEL` | `claude-haiku-5-5` | the only model this build is set up for (it runs with thinking off) |

Only these project names are read; generic names such as `ANTHROPIC_MODEL` or `CLAUDE_EFFORT`,
which other tools often set, are ignored.

## Tests and evaluation

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # 363 tests
python -m evals              # conversation scenarios, guided rules
python -m evals --live       # the same scenarios with Claude (needs a key; costs a few cents)
python -m evals --url URL    # through a deployed demo
```

The **tests** cover the store's rules (verification, privacy, return window, final sale, fees,
stock, manager flag, cancellations, addresses, approvals), the tool runner with malformed and
hostile input, the reply checks (more than 40 phrasings that must and must not be held), the
chat replay (signatures, edited chats, step and message limits, tags only the system may use),
the agent's tool loop and its fallbacks (budget used up, rate limited, errors, refusals, empty
or cut off replies), the Claude request and reply handling (including the real `anthropic`
SDK against a local fake server), the HTTP API (limits, oversized and malformed requests, a
slow model call not blocking other visitors), and the page in a real browser (desktop and
phone).

The **evaluation** is 27 conversations with what must and must not happen: which tools ran,
whether a request reached the team with the right amount, whether the chat went to a person
for the right reason, and what replies must never say (including attempts to see another
customer's order and to talk the agent into a refund).

| Run | Result |
|---|---|
| Guided rules (no AI) | 22 of 22 passed; 5 scenarios need a language model and are skipped |
| Claude Haiku 5.5, through the live demo (11 Oct 2026) | 27 of 27 passed; all 27 conversations together cost $0.0091 |

What is not covered: a real store's API, real customer traffic, load, channels other than web
chat (email, SMS), and conversations longer than the demo's 25 messages.

## How it is built

| File | What it is |
|---|---|
| `support/store.py` | the store: orders, stock, requests, tickets, and every rule above |
| `support/tools.py` | the eight tools: definitions for the model, and the code that runs them |
| `support/helpcenter.py` + `helpcenter/*.md` | the store's policies, searched by keyword, returned as search results |
| `support/checks.py` | the checks on every reply |
| `support/session.py` | the chat as signed events, replayed on every request; what the page shows |
| `support/agent.py` | one request: replay, apply one action, call the model once |
| `support/models.py` | Claude, and the guided rules |
| `support/prompts.py` | the instructions |
| `support/web.py` + `public/` | the demo: API and page |
| `support/demo_data.py` | the fictional store's catalog, customers and orders |
| `evals/` | the conversation scenarios and their runner |
| `tests/` | 363 tests |
| `docs/USER_GUIDE.md` | for the support team |

The server keeps nothing between requests. The browser holds its chat as a list of events,
signed by the server, and sends it with every request; the server checks the signature and
replays the chat against a fresh copy of the store. Tool results are never stored, so every
replayed tool call goes through the same rules again.

In this demo the visitor plays both the customer and the team, so one page shows both sides.
A real deployment serves the customer chat and the team inbox separately, with the team signed
in.

## Before using this for real

- **Connect the real systems.** Replace `support/store.py` with calls to the store's API,
  using a read-only key for lookups and a separate key, used only on approval, for changes.
- **Verify customers properly.** Order number plus email is a demo-level check. For a real
  store, use the customer's logged-in session or a signed link from the order email, and count
  failed attempts on the server.
- **Keep conversations on the server.** The demo keeps each chat in the visitor's browser. A
  real deployment stores conversations, requests and the audit log in a database (for
  example Postgres), with a retention period.
- **Share the limits.** The per-visitor limits here are per server instance; use a shared store
  (for example Redis) and keep a spend limit on the API key.
- **Translate the help center** for other languages. Claude answers in the customer's
  language, but citations quote the articles as written, and the reply checks read English.
- **Re-run the evaluation** on every prompt or model change, and add real chats to it.
- **Measure the cost on real traffic.** The page shows the cost of each chat; real chats are
  longer or shorter than the demo's.

## Adapting it to another store

1. Rewrite the read and request methods of `StoreState` in `support/store.py` for the store's
   API. Keep the rule: tools can read and ask; only an approval writes.
2. Put the store's policies in `support/helpcenter/` as short Markdown articles.
3. Adjust the tools in `support/tools.py` to the new fields, and the scenarios in `evals/`.
4. Run the tests and the evaluation.
