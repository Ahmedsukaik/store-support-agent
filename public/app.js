/* Store Support Agent demo page. The server keeps nothing: this page holds the chat (the
   "session") and sends it with every request. See support/session.py. */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const S = { config: null, session: null, view: null, live: true, busy: false, tab: "approvals", touring: false,
              seenRequests: new Set(), seenTickets: new Set(), openQuotes: new Set(), drafts: {}, pane: "customer" };

  const svg = (box, body) => `<svg viewBox="${box}" aria-hidden="true" focusable="false">${body}</svg>`;
  const ICON = {
    check: svg("0 0 16 16", '<path d="M3 8.5l3.2 3L13 4.5"/>'),
    cross: svg("0 0 16 16", '<path d="M4 4l8 8M12 4l-8 8"/>'),
    Jackets: svg("0 0 48 48", '<path d="M17 8l7 5 7-5 9 6-4 9-3-2v19H14V21l-3 2-4-9z"/><path d="M24 13v27"/>'),
    Packs: svg("0 0 48 48", '<rect x="13" y="12" width="22" height="28" rx="7"/><path d="M19 12v-3a5 5 0 0 1 10 0v3M17 26h14v8H17z"/>'),
    Tents: svg("0 0 48 48", '<path d="M4 38L24 9l20 29z"/><path d="M24 9l-6 29M24 9l6 29M18 38l6-10 6 10"/>'),
    Hiking: svg("0 0 48 48", '<path d="M16 6l6 36M32 6l-6 36"/><path d="M13 12h6M29 12h6"/>'),
    "Camp kitchen": svg("0 0 48 48", '<path d="M12 22h24v6a10 10 0 0 1-10 10h-4a10 10 0 0 1-10-10z"/><path d="M18 16c0-3 3-3 3-6M26 16c0-3 3-3 3-6M36 25h5"/>'),
    Camp: svg("0 0 48 48", '<path d="M10 18h28l-4 10H14zM14 28l-4 12M34 28l4 12M18 28l12 12M30 28L18 40"/>'),
  };
  const TINTS = { Jackets: "#2f7d5f", Packs: "#c8692a", Tents: "#3d4a8f", Hiking: "#7a5c2e", "Camp kitchen": "#a33d2b", Camp: "#4f6b2a" };

  // ------------------------------------------------------------------ helpers
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function format(text) {
    const lines = esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").split("\n");
    let html = "", inList = false;
    for (const line of lines) {
      const item = line.match(/^\s*[-•*]\s+(.*)$/);
      if (item) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${item[1]}</li>`; continue; }
      if (inList) { html += "</ul>"; inList = false; }
      html += line + "\n";
    }
    if (inList) html += "</ul>";
    return html.replace(/\n+$/, "");
  }
  const store = {
    get(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: fine */ } },
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isPhone = () => window.matchMedia("(max-width: 900px)").matches;
  let toastTimer = null;
  function toast(msg, ms = 4200) {
    const el = $("#toast"); el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, ms);
  }
  function announce(msg) { const el = $("#announce"); el.textContent = ""; setTimeout(() => { el.textContent = msg; }, 50); }
  async function post(path, body) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(data.message || "Something went wrong."); err.kind = data.error; throw err; }
    return data;
  }

  // ------------------------------------------------------------------ talking to the server
  async function newChat(message) {
    const data = await post("/api/new");
    S.session = data.session; S.view = data.view; S.drafts = {}; S.tab = "approvals";
    S.seenRequests.clear(); S.seenTickets.clear(); S.openQuotes.clear();
    store.set("support-demo-session", S.session);
    render();
    if (message) toast(message);
  }
  async function step(action) {
    let data;
    try {
      data = await post("/api/step", { session: S.session, action, live: S.live });
    } catch (err) {
      if (err.kind === "session") { await newChat(err.message); return null; }
      throw err;
    }
    S.session = data.session; S.view = data.view;
    store.set("support-demo-session", S.session);
    render();
    const last = S.view.transcript[S.view.transcript.length - 1];
    if (last && last.kind === "assistant") announce("Assistant: " + last.text);
    else if (last && last.kind === "notice") announce("Update: " + last.text);
    return data;
  }
  async function send(text) {
    text = text.trim();
    if (!text || S.busy) return;
    if (text.length > 800) { toast("Please keep messages under 800 characters."); return; }
    setBusy(true);
    showPending(text);
    try {
      let data = await step({ type: "customer", text });
      let guard = 0;
      while (data && data.more && guard++ < 8) data = await step({ type: "continue" });
    } catch (err) {
      toast(err.message); render(); $("#input").value = text; autosize();
    } finally { setBusy(false); if (!S.touring && !isPhone()) $("#input").focus({ preventScroll: true }); }
  }
  async function team(action, extra) {
    if (S.busy) return false;
    setBusy(true);
    try {
      await step({ type: "team", action, ...extra });
      if (isPhone() && !S.touring && ["approve", "reject", "reply"].includes(action)) {
        toast("Sent. Open the customer view to see what they got.");
      }
      $("#tab-body").focus({ preventScroll: true });
      return true;
    } catch (err) { toast(err.message); return false; }
    finally { setBusy(false); }
  }
  function setBusy(b) {
    S.busy = b;
    $("#send").disabled = b;
    document.querySelectorAll(".card .btn, .starter, .chip, .person-btn, #play-tour").forEach((x) => { x.disabled = b; });
  }

  // ------------------------------------------------------------------ rendering: the chat
  function showPending(text) {
    const box = $("#messages");
    box.querySelector(".welcome")?.remove();
    box.insertAdjacentHTML("beforeend", `<div class="msg customer"><span class="sr-only">You said: </span><div class="bubble" dir="auto">${esc(text)}</div></div>`);
    box.insertAdjacentHTML("beforeend", `<div class="working"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>Thinking</div>`);
    box.scrollTop = box.scrollHeight;
    $("#input").value = ""; autosize();
  }
  function stepsHtml(steps) {
    return steps.map((s) => `<span class="step ${s.ok ? "" : "no"}">${s.ok ? ICON.check : ICON.cross}${esc(s.label)}</span>`).join("");
  }
  function starterButton(s, i, cls) {
    const kind = s.kind === "safety" ? "attack" : s.kind === "simulation" ? "sim" : "";
    return `<button type="button" class="${cls} ${kind}" data-suggest="${i}">${esc(s.label)}</button>`;
  }
  function renderChat() {
    const box = $("#messages");
    const items = S.view ? S.view.transcript : [];
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    let html = "";
    if (!items.length) {
      const starters = (S.config?.suggestions || []).slice(0, 8).map((s, i) => starterButton(s, i, "starter")).join("");
      html = `<div class="welcome"><p>Hi! I'm the Copperleaf assistant. I can track an order, start a return or exchange, and answer questions about our gear and policies. A person on our team approves every refund.</p><div class="starter-grid">${starters}</div></div>`;
    }
    items.forEach((it, idx) => {
      if (it.kind === "customer") {
        html += `<div class="msg customer ${it.to_team ? "to-team" : ""}"><span class="sr-only">You said: </span><div class="bubble" dir="auto">${esc(it.text)}</div>${it.to_team ? '<div class="meta-row end"><span class="tag">Sent to the team</span></div>' : ""}</div>`;
      } else if (it.kind === "assistant") {
        const who = it.mode === "live" ? `${esc(prettyModel(it.model))}${it.cost_usd ? " · $" + it.cost_usd.toFixed(4) : ""}` : "Guided rules, no AI";
        const sources = (it.sources || []).map((s, j) => `<button type="button" class="src" data-quote="${idx}-${j}" aria-expanded="${S.openQuotes.has(`${idx}-${j}`)}">${esc(s.title)}</button>`).join("");
        const quotes = (it.sources || []).map((s, j) => S.openQuotes.has(`${idx}-${j}`)
          ? `<div class="quote"><div class="qt">${esc(s.title)}</div>“${esc(s.quote)}”</div>` : "").join("");
        html += `<div class="msg bot ${it.held ? "held" : ""}"><span class="sr-only">Assistant: </span><div class="bubble" dir="auto">${format(it.text)}</div>
          ${it.steps?.length ? `<div class="meta-row">${stepsHtml(it.steps)}</div>` : ""}
          ${sources ? `<div class="meta-row">${sources}</div>` : ""}${quotes}
          <div class="meta-row">${it.held ? '<span class="tag warn">Draft held by a safety check: the team sees why</span>' : `<span class="tag">${who}</span>`}</div></div>`;
      } else if (it.kind === "notice") {
        if (it.notice === "team_reply") {
          html += `<div class="msg teammate"><div class="who">Jordan from the Copperleaf team</div><div class="bubble" dir="auto">${esc(it.text)}</div></div>`;
        } else {
          const head = it.notice === "approved" || it.notice === "rejected" ? "Update from the team" : "";
          html += `<div class="notice ${esc(it.notice)}">${head ? `<b>${head}</b>` : ""}${esc(it.text)}</div>`;
        }
      } else if (it.kind === "system") {
        html += `<div class="notice system">${esc(it.text)}</div>`;
      } else if (it.kind === "working") {
        html += `<div class="working"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>Working on it<ul>${(it.steps || []).map((s) => `<li>${stepsHtml([s])}</li>`).join("")}</ul></div>`;
      }
    });
    const before = box.dataset.count || "0";
    box.innerHTML = html;
    box.dataset.count = String(items.length);
    const lastKind = items.length ? items[items.length - 1].kind : "";
    const lastCustomer = [...box.querySelectorAll(".msg.customer")].pop();
    if ((lastKind === "assistant" || lastKind === "working") && lastCustomer) {
      // Show the question and the start of the answer, not the end of a long reply.
      box.scrollTop = Math.max(0, lastCustomer.offsetTop - 10);
    } else if (nearBottom || String(items.length) !== before) {
      box.scrollTop = box.scrollHeight;
    }
    document.body.classList.toggle("chatting", items.length > 0);
    const full = (S.view?.metrics?.customer_messages || 0) >= 25;
    $("#input").disabled = full;
    if (!items.length) box.scrollTop = 0;
    const paused = S.view?.ai_paused;
    $("#chat-status").textContent = paused ? "A person from the team has this chat." : "Assistant online. A person reviews every refund.";
    $("#input").placeholder = full ? "Message limit reached: start a new chat" : paused ? "Write to the team"
      : isPhone() ? "Ask about an order or a return" : "Ask about an order, a return or a product";
    renderSuggestions(items.length > 0);
  }
  function prettyModel(m) {
    if (!m) return "Claude";
    const x = m.match(/^claude-(\w+)-(\d+)-(\d+)/);
    return x ? `Claude ${x[1][0].toUpperCase()}${x[1].slice(1)} ${x[2]}.${x[3]}` : m;
  }
  function renderSuggestions(show) {
    const el = $("#suggestions");
    el.innerHTML = show ? (S.config?.suggestions || []).map((s, i) => starterButton(s, i, "chip")).join("") : "";
  }

  // ------------------------------------------------------------------ rendering: the team inbox
  function renderInbox() {
    const v = S.view || { requests: [], tickets: [], orders: [], audit: [], metrics: {} };
    const pending = v.requests.filter((r) => r.status === "pending").length;
    const open = v.tickets.filter((t) => t.status === "open").length;
    setBadge("#badge-approvals", pending); setBadge("#badge-handoffs", open); setBadge("#switch-badge", pending + open);
    // Jump to what just arrived, so the visitor sees it.
    const freshReq = v.requests.find((r) => !S.seenRequests.has(r.id));
    const freshTicket = v.tickets.find((t) => !S.seenTickets.has(t.id));
    if (freshTicket) S.tab = "handoffs"; else if (freshReq) S.tab = "approvals";
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === S.tab)));
    const body = $("#tab-body");
    body.setAttribute("aria-labelledby", `tab-${S.tab}`);
    const keepScroll = body.scrollTop;
    if (S.tab === "approvals") body.innerHTML = approvals(v, freshReq);
    else if (S.tab === "handoffs") body.innerHTML = handoffs(v, freshTicket);
    else if (S.tab === "stats") body.innerHTML = stats(v.metrics || {});
    else if (S.tab === "orders") body.innerHTML = orders(v.orders);
    else body.innerHTML = activity(v.audit);
    body.scrollTop = freshReq || freshTicket ? 0 : keepScroll;
    v.requests.forEach((r) => S.seenRequests.add(r.id));
    v.tickets.forEach((t) => S.seenTickets.add(t.id));
    for (const [id, value] of Object.entries(S.drafts)) { const el = document.getElementById(id); if (el) el.value = value; }
  }
  function setBadge(sel, n) { const el = $(sel); el.hidden = !n; el.textContent = n; }
  const checksHtml = (checks) => checks?.length ? `<ul class="checks">${checks.map((c) => `<li>${ICON.check}<span>${esc(c.label)}</span></li>`).join("")}</ul>` : "";
  const SAFEGUARDS = `<div class="card guard"><div class="card-title">What the code enforces, whatever the AI says</div><ul class="checks">
    <li>${ICON.check}<span>No order details until the customer gives the order number and the email on that order.</span></li>
    <li>${ICON.check}<span>Customers only ever see their own orders.</span></li>
    <li>${ICON.check}<span>The AI never sets an amount: the store works out every refund from the order and the policy.</span></li>
    <li>${ICON.check}<span>Every refund, cancellation and address change waits for a person.</span></li>
    <li>${ICON.check}<span>Each reply is checked before the customer sees it: no made up amounts, no "it's done" before it is.</span></li>
    <li>${ICON.check}<span>Damaged items, lost parcels and exceptions go to a person, with a summary.</span></li>
    <li>${ICON.check}<span>Every step is logged: see the Activity tab.</span></li></ul></div>`;

  function approvals(v, fresh) {
    if (!v.requests.length) return `<div class="empty"><b>No requests yet</b>When the assistant starts a return, an exchange, a cancellation or an address change, it lands here. Nothing happens to the order until a person approves it.</div>${SAFEGUARDS}`;
    return [...v.requests].reverse().map((r) => `
      <article class="card ${fresh && fresh.id === r.id ? "fresh" : ""}" aria-label="${esc(r.title)}">
        <div class="card-top"><div><div class="card-title">${esc(r.title)}</div><div class="card-sub">${esc(r.id)} · ${esc(r.customer)}</div></div>
          <div class="right">${r.amount ? `<div class="amount">${esc(r.amount)}</div>` : ""}<span class="pill ${esc(r.status)}">${esc(r.status)}</span></div></div>
        <ul class="lines">${r.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
        <div class="detail">${esc(r.detail)}</div>
        ${checksHtml(r.checks)}
        ${r.flags.map((f) => `<div class="flag">${esc(f)}</div>`).join("")}
        ${r.status === "pending" ? `
          <div class="actions"><button type="button" class="btn primary" data-approve="${esc(r.id)}">Approve</button>
          <button type="button" class="btn danger" data-reject="${esc(r.id)}">Reject</button></div>
          <label class="sr-only" for="note-${esc(r.id)}">Reason for the customer</label>
          <textarea class="note-input" id="note-${esc(r.id)}" rows="2" maxlength="300" dir="auto" placeholder="Reason for the customer (needed to reject)"></textarea>`
          : `<div class="decided">${r.status === "approved" ? "Approved" : r.status === "rejected" ? "Rejected" : "Replaced"}${r.note ? `: ${esc(r.note)}` : ""}</div>`}
      </article>`).join("");
  }
  function handoffs(v, fresh) {
    if (!v.tickets.length) return `<div class="empty"><b>No handoffs</b>The assistant passes a chat to a person when it should: a damaged item, a lost parcel, an exception, an upset customer, or a reply that failed a safety check.</div>`;
    return [...v.tickets].reverse().map((t) => {
      const reason = t.reason.replace(/_/g, " ");
      const heldReply = t.draft ? `<div class="held-box"><b>Draft held before the customer saw it.</b> ${esc(t.summary)}<q dir="auto">${esc(t.draft)}</q></div>` : "";
      return `<article class="card ${fresh && fresh.id === t.id ? "fresh" : ""}" aria-label="Handoff ${esc(t.id)}">
        <div class="card-top"><div><div class="card-title">${esc(t.id)}: ${esc(reason[0].toUpperCase() + reason.slice(1))}</div>
          <div class="card-sub">${t.customer ? "Verified customer " + esc(t.customer) : "Customer not verified"} · ${t.source === "system" ? "opened by a safety check" : "opened by the assistant"}</div></div>
          <div>${t.priority === "urgent" ? '<span class="pill urgent">urgent</span> ' : ""}<span class="pill ${esc(t.status)}">${esc(t.status)}</span></div></div>
        ${t.draft ? heldReply : `<div class="detail summary">${esc(t.summary)}</div>`}
        ${t.status === "open" ? `
          <label class="sr-only" for="reply-${esc(t.id)}">Reply to the customer</label>
          <textarea class="reply-input" id="reply-${esc(t.id)}" rows="2" maxlength="1000" dir="auto" placeholder="Reply to the customer as Jordan from the team"></textarea>
          <div class="actions"><button type="button" class="btn primary" data-reply="${esc(t.id)}">Send reply</button>
          <button type="button" class="btn" data-back="1">Hand back to the assistant</button></div>` : '<div class="decided">Closed: the assistant is back in the chat.</div>'}
      </article>`;
    }).join("");
  }
  function stats(m) {
    const tokens = (n) => (n || 0).toLocaleString("en-US");
    const cost = m.cost_usd ? "$" + m.cost_usd.toFixed(4) : "$0";
    const live = S.config?.live;
    const perThousand = m.cost_usd && m.customer_messages ? (m.cost_usd * 1000).toFixed(2) : null;
    const projection = perThousand
      ? `<div class="stat wide accent"><div class="k">What this would cost your store</div><div class="v">$${perThousand}</div><div class="s">in AI usage for 1,000 chats like this one, based on what this chat cost.</div></div>`
      : `<div class="stat wide accent"><div class="k">What this would cost your store</div><div class="v">${live ? "Ask a question" : "n/a"}</div><div class="s">${live ? "Chat with the live AI and this shows the AI cost of 1,000 chats like yours, based on your chat." : "Not shown in guided mode: these replies come from fixed rules with no AI. Switch to Live AI to see a real cost."}</div></div>`;
    return `<div class="stats">
      <div class="stat wide"><div class="k">AI cost of this chat so far</div><div class="v">${cost}</div>
        <div class="s">${m.live_calls || 0} live model call${m.live_calls === 1 ? "" : "s"}${m.guided_calls ? `, ${m.guided_calls} guided (free)` : ""}. ${live ? esc(prettyModel(S.config.model)) + " prices." : "Live AI is not configured on this server."}</div></div>
      ${projection}
      <div class="stat"><div class="k">Customer messages</div><div class="v">${m.customer_messages || 0}</div></div>
      <div class="stat"><div class="k">Tool calls</div><div class="v">${m.tool_calls || 0}</div><div class="s">every one checked by code</div></div>
      <div class="stat"><div class="k">Tokens read</div><div class="v">${tokens(m.input_tokens)}</div><div class="s">${tokens(m.cached_tokens)} from cache (90% cheaper)</div></div>
      <div class="stat"><div class="k">Tokens written</div><div class="v">${tokens(m.output_tokens)}</div></div>
      <div class="stat"><div class="k">Average reply step</div><div class="v">${m.avg_ms ? (m.avg_ms / 1000).toFixed(1) + " s" : "n/a"}</div></div>
      <div class="stat"><div class="k">Drafts held by a check</div><div class="v">${m.held || 0}</div></div>
      <div class="stat"><div class="k">Waiting for approval</div><div class="v">${m.pending || 0}</div></div>
      <div class="stat"><div class="k">Needed a person</div><div class="v">${m.handed_off ? "Yes" : "No"}</div></div>
    </div>
    <p class="explain">The model never sets an amount and never writes to the store. It can only ask; code checks every request against the policy, works out the refund, and a person approves it. Every reply is checked before the customer sees it: money amounts must come from the order data or the policy, and the reply must not claim something is done when it is not.</p>`;
  }
  function orders(list) {
    return `<p class="explain" style="margin:0">This visitor's private copy of the store. Approvals change it; start a new chat to reset it.</p><div class="olist">${(list || []).map((o) => `
      <div class="orow"><div class="top"><span>#${esc(o.number)} · ${esc(o.customer)}</span><span>${esc(o.total)}</span></div>
      <div class="st">${esc(o.status)}</div><div class="st">${o.items.map(esc).join(", ")}</div></div>`).join("")}</div>`;
  }
  function activity(audit) {
    if (!audit?.length) return `<div class="empty"><b>Nothing yet</b>Every tool call, refusal and decision is logged here, with who did it.</div>`;
    return `<ul class="audit">${[...audit].reverse().map((a) => `<li class="${a.outcome === "refused" ? "refused-row" : ""}"><span class="actor ${esc(a.actor)}">${esc(a.actor)}</span>
      <span dir="auto">${esc(a.action.replace(/_/g, " "))}: ${esc(a.detail)}</span><span class="out ${a.outcome === "refused" ? "refused" : ""}">${esc(a.outcome)}</span></li>`).join("")}</ul>`;
  }

  function renderProducts() {
    const list = (S.config?.products || []).filter((p) => ["NL-RJ", "TH-40", "RG-2", "AL-ST", "SM-TP", "CC-1"].includes(p.sku));
    $("#products").innerHTML = list.map((p) => `<div class="product"><div class="art" style="color:${TINTS[p.category] || "#2f7d5f"};background:${(TINTS[p.category] || "#2f7d5f")}14">${ICON[p.category] || ""}</div>
      <div class="meta"><div class="name">${esc(p.name)}</div><div class="price">${esc(p.price)}</div></div></div>`).join("");
    $("#people").innerHTML = `<div class="people-head">Pick a customer to ask about their order</div>` + (S.config?.identities || []).map((p, i) => `
      <button type="button" class="person-btn" data-person="${i}"><span class="pn">${esc(p.name)}</span>
      <span><code>${esc(p.email)}</code> · ${p.orders.map((o) => "#" + esc(o)).join(", ")}</span><span class="ps">${esc(p.story)}</span></button>`).join("");
  }
  function renderMode() {
    const cfg = S.config || {};
    const liveBtn = $("#mode-live"), guidedBtn = $("#mode-guided");
    if (!cfg.live) { S.live = false; liveBtn.disabled = true; liveBtn.title = "No API key is configured on this server"; }
    $("#mode-live-label").innerHTML = cfg.live ? `Live AI<span class="model-name"> · ${esc(prettyModel(cfg.model))}</span>` : "Live AI (off)";
    liveBtn.setAttribute("aria-pressed", String(S.live));
    guidedBtn.setAttribute("aria-pressed", String(!S.live));
  }
  function render() { renderChat(); renderInbox(); renderMode(); }

  // ------------------------------------------------------------------ the guided tour
  async function tour() {
    if (S.busy || S.touring) return;
    if (S.view?.transcript.length && !window.confirm("The tour starts a new chat. Continue?")) return;
    S.touring = true;
    document.body.classList.add("touring");
    try {
      await newChat();
      setPane("customer");
      toast("1 of 3: a customer asks to return a jacket that's too small.", 5000);
      await sleep(1200);
      await send(S.config.suggestions.find((s) => s.label === "Return a jacket").text);
      await sleep(2600);
      if (isPhone()) { setPane("team"); await sleep(600); }
      toast("2 of 3: the request waits in the team inbox. The store worked out the refund. A person approves it.", 5200);
      document.querySelector(".card .btn.primary")?.classList.add("spot");
      await sleep(4200);
      const id = S.view.requests[0]?.id;
      if (id) await team("approve", { request_id: id, note: "" });
      if (isPhone()) { await sleep(500); setPane("customer"); }
      toast("3 of 3: the customer gets the update in the chat. Your turn: try any question.", 6000);
      document.querySelector(".notice.approved")?.classList.add("spot");
    } catch (err) { toast(err.message); }
    finally { S.touring = false; document.body.classList.remove("touring"); }
  }

  // ------------------------------------------------------------------ events
  function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 120) + "px"; }
  function togglePeople(show) {
    const el = $("#people"); const open = show ?? el.hidden;
    el.hidden = !open; $("#show-people").setAttribute("aria-expanded", String(open));
  }
  function wire() {
    $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
    $("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#input").value); } });
    $("#input").addEventListener("input", autosize);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#people").hidden) { togglePeople(false); $("#show-people").focus(); } });
    document.addEventListener("click", (e) => {
      const t = e.target.closest("button");
      if (!t) {
        if (!e.target.closest("#people")) togglePeople(false);
        return;
      }
      if (t.dataset.suggest !== undefined) {
        const s = S.config.suggestions[+t.dataset.suggest];
        if (s) send(s.text);
      } else if (t.dataset.person !== undefined) {
        const p = S.config.identities[+t.dataset.person];
        togglePeople(false); setPane("customer");
        $("#input").value = `Where is my order #${p.orders[0]}? My email is ${p.email}`; autosize(); $("#input").focus();
      } else if (t.dataset.quote) {
        const key = t.dataset.quote;
        S.openQuotes.has(key) ? S.openQuotes.delete(key) : S.openQuotes.add(key);
        const top = $("#messages").scrollTop;
        renderChat();
        $("#messages").scrollTop = top;
        document.querySelector(`[data-quote="${key}"]`)?.focus({ preventScroll: true });
      }
      else if (t.dataset.approve) {
        const id = t.dataset.approve;
        team("approve", { request_id: id, note: "" }).then((ok) => { if (ok) delete S.drafts[`note-${id}`]; });
      } else if (t.dataset.reject) {
        const id = t.dataset.reject;
        const note = $(`#note-${id}`)?.value.trim();
        if (!note) { toast("Write a short reason for the customer first."); $(`#note-${id}`)?.focus(); return; }
        team("reject", { request_id: id, note }).then((ok) => { if (ok) delete S.drafts[`note-${id}`]; });
      } else if (t.dataset.reply) {
        const key = `reply-${t.dataset.reply}`;
        const value = $(`#${key}`)?.value.trim();
        if (!value) { toast("Write the reply first."); return; }
        team("reply", { text: value }).then((ok) => { if (ok) delete S.drafts[key]; });
      } else if (t.dataset.back) team("return_to_ai", {});
      else if (t.dataset.tab) { S.tab = t.dataset.tab; renderInbox(); }
      else if (t.dataset.pane) setPane(t.dataset.pane);
    });
    document.addEventListener("input", (e) => { if (e.target.matches(".note-input, .reply-input")) S.drafts[e.target.id] = e.target.value; });
    $("#new-chat").addEventListener("click", () => { if (!S.busy && !S.touring) newChat("New chat: the store is back to its starting state."); });
    $("#mode-live").addEventListener("click", () => { S.live = true; store.set("support-demo-live", true); renderMode(); });
    $("#mode-guided").addEventListener("click", () => { S.live = false; store.set("support-demo-live", false); renderMode(); });
    $("#hide-howto").addEventListener("click", () => { $("#howto").hidden = true; store.set("support-demo-howto", false); });
    $("#show-people").addEventListener("click", (e) => { e.stopPropagation(); togglePeople(); });
    $("#play-tour").addEventListener("click", tour);
    $("#top-tour").addEventListener("click", tour);
    document.querySelector(".tabs").addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const tabs = [...document.querySelectorAll(".tabs button")];
      const i = tabs.findIndex((b) => b.dataset.tab === S.tab);
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      S.tab = next.dataset.tab; renderInbox(); next.focus();
    });
    window.addEventListener("resize", () => { if (S.view) renderChat(); });
  }
  function setPane(pane) {
    S.pane = pane;
    document.querySelectorAll(".switcher button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.pane === pane)));
    $("#pane-customer").classList.toggle("active", pane === "customer");
    $("#pane-team").classList.toggle("active", pane === "team");
    if (pane === "customer") { const box = $("#messages"); box.scrollTop = box.scrollHeight; }
    if (!S.touring) $("#toast").hidden = true;
  }

  async function init() {
    wire(); setPane("customer");
    if (store.get("support-demo-howto") === false) $("#howto").hidden = true;
    try {
      const res = await fetch("/api/config");
      S.config = await res.json();
    } catch { toast("The demo server is not reachable."); return; }
    const savedLive = store.get("support-demo-live");
    S.live = S.config.live && savedLive !== false;
    renderProducts(); renderMode();
    const saved = store.get("support-demo-session");
    if (saved && saved.started) { S.session = saved; try { await step({ type: "view" }); } catch { /* start fresh below */ } }
    if (!S.view) await newChat();
  }
  init();
})();
