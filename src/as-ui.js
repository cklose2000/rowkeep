/* Rowkeep page: a read-only view of the activity stream. All data values are set with textContent. */
(function () {
  "use strict";
  var A = window.PAS;
  var WINDOW = 400;  /* newest sealed rows held in the page */
  var READ = 1000;   /* the most documents one read of a collection returns */
  var TRIES = 3;     /* reads of the stream before saying the tip was not reached */
  var DRAWN = { due: 200, records: 300, rows: 200, pending: 100 };
  var S = { db: null, user: null, me: null, ready: false, headExists: false,
            ix: { head: null, verbs: [], entities: [] }, sealed: [], waiting: [],
            chain: null, brief: null, checkedTip: null, filterSig: "", typeSig: null, queued: false, loading: false, again: false,
            readError: "", briefError: "", stale: false, note: "",
            bad: { head: 0, verbs: 0, entities: 0, inbox: 0, stream: 0, brief: 0, panels: 0 },
            cut: { verbs: false, entities: false, inbox: false } };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function int(n) { return Math.round(n || 0).toLocaleString("en-US"); }
  function two(n) { return n < 10 ? "0" + n : "" + n; }
  function when(ts) {
    var ms = A.isoMs(ts);
    if (!ms) return "not recorded";
    var d = new Date(ms);
    return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate()) + " " + two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
  }
  function say(text, kind) {
    var s = $("status");
    s.textContent = text;
    s.dataset.kind = kind || "";
  }
  function explain(e) {
    var code = e && e.code, msg = (e && e.message) || String(e);
    if (code === "resource_exhausted") return "Too many calls at once. Wait a few seconds and reload.";
    if (code === "revoked" || code === "not_granted") return "This view no longer has access to the log.";
    if (code === "unavailable") return "The log can't be reached right now. Try again.";
    return msg;
  }
  function pause(ms) { return new Promise(function (done) { setTimeout(done, ms); }); }
  /* A summed field and its total: money as an amount ("amount USD 5,700.00"), anything
     else as the whole number it is ("minutes 90"). `mid` goes between the two. */
  function summed(f, n, currency, mid) {
    var m = A.amount(f, n, currency);
    return m ? m[0] + mid + m[1] : f + mid + int(n);
  }
  /* "Elm Example Expo 2 (amount USD 5,700.00)": a value, its row count, and its share of
     each sum. `currency` is the one the verb's rows are all in, if there is one. In the
     tally of the currency field itself (`own`), each value is the currency of its share. */
  function pairs(list, currency, own) {
    return (list || []).map(function (x) {
      var code = own ? (typeof x.v === "string" && x.v !== "(other)" ? x.v : "") : currency;
      var sums = Object.keys(x.s || {}).sort().map(function (f) { return summed(f, x.s[f], code, " "); }).join(", ");
      return A.text(x.v) + " " + int(x.n) + (sums ? " (" + sums + ")" : "");
    }).join(", ");
  }
  function counts(map) {
    return Object.keys(map || {}).sort(function (x, y) { return map[y] - map[x]; }).map(function (k) { return k + " " + int(map[k]); }).join(", ");
  }

  function schedule() {
    if (S.queued) return;
    S.queued = true;
    (window.requestAnimationFrame || setTimeout)(function () { S.queued = false; render(); }, 16);
  }

  /* One panel that cannot be drawn must not take the others with it. */
  function render() {
    $("btn-reload").disabled = !S.ready || S.loading;
    var failed = 0;
    [renderGauges, renderTallies, renderFilters, renderRows, renderPending, renderRecords, renderDue].forEach(function (panel) {
      try { panel(); } catch (e) { failed++; }
    });
    S.bad.panels = failed;
    renderStatus();
  }

  /* What the page can say it did, and nothing more. A good result names the rows that were
     recomputed; anything else says "not verified" and why. */
  function renderStatus() {
    var bad = S.bad.head + S.bad.verbs + S.bad.entities + S.bad.inbox + S.bad.stream + S.bad.brief;
    var notes = [];
    if (bad) notes.push(int(bad) + (bad === 1 ? " document" : " documents") + " could not be shown: " + (bad === 1 ? "it does" : "they do") + " not have the form this page reads. Ask your assistant to check the log.");
    if (S.bad.panels) notes.push(int(S.bad.panels) + (S.bad.panels === 1 ? " panel" : " panels") + " could not be drawn.");
    if (S.briefError) notes.push("The daily brief could not be read, so no prepared drafts or notes are shown: " + S.briefError);
    $("bad-note").textContent = notes.join(" ");
    $("bad-note").hidden = !notes.length;
    if (!S.ready) return;
    var c = S.chain, lead = S.note ? S.note + " " : "";
    if (S.readError) say(S.readError, "bad");
    else if (!c) say("Reading the log…");
    else if (c.state === "verified") say(lead + "Verified rows " + int(c.from) + " to " + int(c.to) + " against the tip." + (c.from > 1 ? " Earlier rows are not checked on this page." : ""), "good");
    else if (c.state === "empty") say(lead + "Nothing sealed yet.");
    else if (c.state === "broken") say("Not intact: " + c.problems[0], "bad");
    else say("Not verified: " + c.why + ".", "bad");
  }

  function renderDue() {
    var now = new Date(), today = now.getFullYear() + "-" + two(now.getMonth() + 1) + "-" + two(now.getDate());
    var list = A.due(S.ix.entities, today), body = $("due-body"), drafts = 0;
    var n = A.dueCounts(list);
    body.textContent = "";
    $("due-panel").hidden = !list.length;
    list.slice(0, DRAWN.due).forEach(function (x) {
      var tr = el("tr"), when = el("td");
      if (x.badDate) when.textContent = "date not valid";
      else if (x.late === null) when.textContent = "no date";
      else if (x.late > 0) when.appendChild(el("span", "tier bad", x.late === 1 ? "1 day late" : int(x.late) + " days late"));
      else if (x.late === 0) when.appendChild(el("span", "tier pending", "today"));
      else { when.className = "mono"; when.textContent = x.due; }
      when.title = x.due;
      tr.appendChild(when);
      var who = el("td", "clip", x.label);
      who.title = x.entity;
      tr.appendChild(who);
      tr.appendChild(el("td", "wrap", x.step));
      tr.appendChild(el("td", "clip", x.owner));
      var cell = el("td", "wrap"), it = A.prepared(S.brief, x, S.ix.entities);
      if (it && typeof it.draft_body === "string" && it.draft_body) {
        var d = el("details");
        d.appendChild(el("summary", "", typeof it.draft_subject === "string" && it.draft_subject ? it.draft_subject : "Draft"));
        d.appendChild(el("div", "draft", it.draft_body));
        cell.appendChild(d);
        drafts++;
      } else if (it && typeof it.notes === "string") cell.textContent = it.notes;
      tr.appendChild(cell);
      body.appendChild(tr);
    });
    var text = int(n.now) + " due now, " + int(n.later) + " later, " + int(n.undated) + " undated.";
    if (list.length > DRAWN.due) text += " The first " + int(DRAWN.due) + " of " + int(list.length) + " are listed.";
    if (S.cut.entities) text += " Only the first " + int(READ) + " records were read, so there may be more.";
    if (S.brief && S.brief.date) text += " Prepared by the brief of " + S.brief.date + (drafts ? ": " + int(drafts) + (drafts === 1 ? " draft" : " drafts") + " shown, none sent." : ".");
    $("due-note").textContent = text;
  }

  function renderRecords() {
    var all = A.records(S.ix.entities, ""), types = {};
    all.forEach(function (e) { if (e.type) types[e.type] = 1; });
    var names = Object.keys(types).sort(), sig = names.join("|");
    if (sig !== S.typeSig) { S.typeSig = sig; fill($("q-type"), "Every type", names); }
    var list = A.records(S.ix.entities, $("q-type").value);
    var body = $("rec-body");
    body.textContent = "";
    $("rec-panel").hidden = !all.length;
    list.slice(0, DRAWN.records).forEach(function (e) {
      var tr = el("tr");
      var name = el("td", "clip", e.subject || e.entity);
      name.title = e.entity + ((e.names || []).length > 1 ? " · also known as " + e.names.slice(1).join(", ") : "");
      tr.appendChild(name);
      tr.appendChild(el("td", "clip", e.type || ""));
      tr.appendChild(el("td", "wrap", A.recordFields(e).map(function (p) { return p[0] + ": " + p[1]; }).join(" · ") || "no details yet"));
      tr.appendChild(el("td", "mono", when(e.last_ts)));
      tr.appendChild(el("td", "num", int(e.n)));
      body.appendChild(tr);
    });
    $("rec-note").textContent = int(list.length) + (list.length === 1 ? " record" : " records")
      + (list.length > DRAWN.records ? ", first " + int(DRAWN.records) + " shown" : "") + ". Each line is the newest value of every detail logged against it."
      + (S.cut.entities ? " Only the first " + int(READ) + " records were read, so there may be more." : "");
  }

  function renderGauges() {
    var head = S.ix.head, sealedCount = head ? head.n : 0;
    $("g-rows").textContent = S.ready ? int(sealedCount + S.waiting.length) : "—";
    $("g-rows-sub").textContent = int(sealedCount) + " sealed, " + int(S.waiting.length) + " waiting" + (S.cut.inbox ? " (first " + int(READ) + " read)" : "");

    var c = S.chain, value = $("g-chain"), sub = $("g-chain-sub");
    value.dataset.kind = "";
    if (!S.ready) { value.textContent = "—"; sub.textContent = "checked in your browser"; }
    else if (!c) { value.textContent = "Checking"; sub.textContent = "recomputing fingerprints"; }
    else if (c.state === "verified") {
      value.textContent = "Verified";
      value.dataset.kind = "good";
      sub.textContent = "rows " + int(c.from) + " to " + int(c.to) + " recomputed here and matched to the tip" + (c.from > 1 ? "; earlier rows not checked here" : "");
    } else if (c.state === "empty") { value.textContent = "Empty"; sub.textContent = "nothing sealed yet"; }
    else if (c.state === "broken") {
      value.textContent = "Broken";
      value.dataset.kind = "bad";
      sub.textContent = c.problems[0];
    } else {
      value.textContent = "Not verified";
      value.dataset.kind = "bad";
      sub.textContent = c.why;
    }

    var pending = S.ix.entities.filter(function (e) { return e.status === "pending"; }).length;
    $("g-pending").textContent = S.ready ? int(pending) : "—";
    $("g-pending-sub").textContent = (pending === 1 ? "thing awaiting follow-up" : "things awaiting follow-up") + (S.cut.entities ? ", among the first " + int(READ) + " records" : "");

    var newest = S.waiting.concat(S.sealed).reduce(function (m, r) { return Math.max(m, A.isoMs(r.ts)); }, 0);
    $("g-last").textContent = newest ? when(new Date(newest).toISOString()).slice(5, 16) : "—";
    $("g-last-sub").textContent = newest ? when(new Date(newest).toISOString()).slice(0, 4) + ", your local time" : "no activity yet";
  }

  function renderTallies() {
    var body = $("tally-body"), verbs = S.ix.verbs;
    body.textContent = "";
    $("tally-table").hidden = !verbs.length;
    $("tally-empty").hidden = !!verbs.length || !S.ready;
    verbs.forEach(function (v) {
      var tr = el("tr");
      tr.appendChild(el("td", "clip", v.verb));
      tr.appendChild(el("td", "num", int(v.n)));
      tr.appendChild(el("td", "wrap", pairs(v.actor)));
      tr.appendChild(el("td", "wrap", counts(v.source)));
      /* Money is shown as money. A total over rows in more than one currency is not one
         figure in any of them, and says so; the currency tally beside it has each one's own. */
      var parts = [], currency = A.tallyCurrency(v), mixed = currency === null;
      Object.keys(v.sum || {}).sort().forEach(function (f) {
        parts.push(summed(f, v.sum[f], currency, " total ") + (mixed && A.amount(f, v.sum[f]) ? " (mixed currencies)" : ""));
      });
      Object.keys(v.field || {}).sort().forEach(function (f) { parts.push(f + ": " + pairs(v.field[f], currency, f === "currency")); });
      var st = counts(v.status);
      if (st && st.indexOf("ok ") !== 0 || Object.keys(v.status || {}).length > 1) parts.push("status: " + st);
      tr.appendChild(el("td", "wrap", parts.join(" · ")));
      tr.appendChild(el("td", "mono", when(v.last_ts)));
      body.appendChild(tr);
    });
    var total = S.ix.head ? S.ix.head.n : 0;
    $("tally-note").textContent = verbs.length
      ? int(total) + " sealed rows across " + verbs.length + (verbs.length === 1 ? " verb" : " verbs") + ". These are the tallies as the index stores them; this page does not recount them."
        + (S.cut.verbs ? " Only the first " + int(READ) + " tallies were read, so there may be more." : "")
      : "";
  }

  function fill(sel, first, values) {
    var keep = sel.value;
    sel.textContent = "";
    var o = el("option", "", first);
    o.value = "";
    sel.appendChild(o);
    values.forEach(function (v) {
      var opt = el("option", "", v);
      opt.value = v;
      sel.appendChild(opt);
    });
    sel.value = keep;
    if (sel.value !== keep) sel.value = "";
  }
  function renderFilters() {
    var verbs = {}, actors = {};
    S.sealed.concat(S.waiting).forEach(function (r) { verbs[r.verb] = 1; if (r.actor) actors[r.actor] = 1; });
    var v = Object.keys(verbs).sort(), a = Object.keys(actors).sort(), sig = v.join("|") + "/" + a.join("|");
    if (sig === S.filterSig) return;
    S.filterSig = sig;
    fill($("q-verb"), "Any verb", v);
    fill($("q-actor"), "Any actor", a);
  }

  function renderRows() {
    var rows = A.view(S.sealed, S.waiting, {
      verb: $("q-verb").value, actor: $("q-actor").value, text: $("q-text").value.trim(), show: $("q-show").value
    });
    var body = $("row-body"), table = $("row-table"), empty = $("row-empty");
    body.textContent = "";
    if (!rows.length) {
      table.hidden = true;
      empty.hidden = false;
      empty.textContent = !S.ready ? "Reading the log…"
        : (S.sealed.length || S.waiting.length) ? "No rows match these filters."
        : "Nothing logged yet. Rows appear here once Claude records them with the rowkeep skill.";
      $("row-summary").textContent = "";
      return;
    }
    rows.slice(0, DRAWN.rows).forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("td", "num", r.waiting ? "" : int(r.id)));
      tr.appendChild(el("td", "mono", when(r.ts)));
      tr.appendChild(el("td", "clip", r.verb));
      var ent = el("td", "clip", r.subject || r.entity);
      ent.title = r.entity;
      tr.appendChild(ent);
      tr.appendChild(el("td", "clip", r.actor || ""));
      tr.appendChild(el("td", "wrap", A.detail(r.payload)));
      var st = el("td");
      st.appendChild(el("span", "tier " + (r.waiting ? "pending" : r.status === "fail" ? "bad" : r.status === "pending" ? "pending" : "packed"),
        r.waiting ? "waiting" : (r.status || "ok")));
      tr.appendChild(st);
      body.appendChild(tr);
    });
    table.hidden = false;
    empty.hidden = true;
    var head = S.ix.head, sealedTotal = head ? head.n : 0;
    var text = int(rows.length) + (rows.length === 1 ? " row" : " rows") + " shown";
    if (rows.length > DRAWN.rows) text = "Newest " + int(DRAWN.rows) + " of " + int(rows.length) + " matching rows";
    if (sealedTotal > S.sealed.length) text += ". This page holds the newest " + int(S.sealed.length) + " of " + int(sealedTotal) + " sealed rows; ask your assistant for older ones";
    if (S.cut.inbox) text += ". Only the first " + int(READ) + " waiting rows were read, so there may be more";
    if (S.stale) text += ". The sealed rows listed are from an earlier read: the newest read failed";
    $("row-summary").textContent = text + ".";
  }

  function renderPending() {
    var list = S.ix.entities.filter(function (e) { return e.status === "pending"; });
    var body = $("pend-body");
    body.textContent = "";
    $("pend-panel").hidden = !list.length;
    list.slice(0, DRAWN.pending).forEach(function (e) {
      var tr = el("tr");
      var ent = el("td", "clip", e.subject || e.entity);
      ent.title = e.entity;
      tr.appendChild(ent);
      var id = el("td", "clip", e.entity);
      id.title = e.entity;
      tr.appendChild(id);
      tr.appendChild(el("td", "clip", e.last_verb || ""));
      tr.appendChild(el("td", "mono", when(e.last_ts)));
      tr.appendChild(el("td", "num", int(e.n)));
      body.appendChild(tr);
    });
    $("pend-note").textContent = "Things whose most recent row left them pending: " + int(list.length) + "."
      + (list.length > DRAWN.pending ? " The first " + int(DRAWN.pending) + " are listed." : "")
      + (S.cut.entities ? " Only the first " + int(READ) + " records were read, so there may be more." : "");
  }

  /* ---------- loading ---------- */

  /* Read the newest sealed rows and check them against the tip. Resolves true if the rows
     were read. If the newest row read is not the tip, a seal may have landed in between,
     so it reads again: at most TRIES times, and then says the tip was not reached. */
  async function loadSealed(force) {
    if (S.loading) { S.again = true; return false; }
    /* A verdict stands only for the tip it was computed against: its row, its count and
       its fingerprint, not its row number alone. */
    if (!force && S.chain && S.chain.state !== "unverified" && A.tipSig(S.ix.head) === S.checkedTip) return true;
    S.loading = true;
    S.again = false;
    var read = false;
    for (var attempt = 1; attempt <= TRIES; attempt++) {
      var rows = [], bad = 0, found = 0;
      try {
        var qs = await S.db.collection("stream").orderBy("id", "desc").limit(WINDOW).get();
        found = qs.docs.length;
        qs.docs.forEach(function (d) { var r = A.sealedRow(d.data()); if (r) rows.push(r); else bad++; });
      } catch (e) {
        /* Nothing was checked by this read, and any rows on screen are from an earlier one. */
        read = false;
        S.readError = explain(e);
        S.stale = S.sealed.length > 0;
        S.chain = { state: "unverified", problems: [], from: 0, to: 0, why: "the rows could not be read (" + S.readError + ")" };
        S.checkedTip = null;
        break;
      }
      read = true;
      S.readError = "";
      S.stale = false;
      rows.sort(function (x, y) { return x.id - y.id; });
      S.sealed = rows;
      S.bad.stream = bad;
      S.chain = null;
      schedule();
      var used = S.ix.head, usedSig = A.tipSig(used), hadTip = S.headExists;
      var chain = await A.verify(rows, used);
      /* Computing fingerprints takes time, and the tip may have changed meanwhile. The
         verdict is shown only if the tip it was computed against is still the tip. */
      var moved = A.tipSig(S.ix.head) !== usedSig || S.headExists !== hadTip;
      if (moved) chain = { state: "unverified", problems: [], from: 0, to: 0, why: "the tip changed while the rows were being checked" };
      /* Documents came back and none is a row: that is not an empty log. */
      else if (found && !rows.length) chain = { state: "unverified", problems: [], from: 0, to: 0, why: "the stream holds " + int(found) + (found === 1 ? " document, and it is not" : " documents, and none is") + " in the form of a row" };
      /* "Empty" is said only of a read that came back with nothing, on a log with no tip. */
      else if (chain.state === "unverified" && !S.headExists && !found) chain.state = "empty";
      S.chain = chain;
      S.checkedTip = moved ? null : usedSig;
      if (attempt === TRIES || (!moved && (chain.state !== "unverified" || !S.ix.head))) break;
      schedule();
      await pause(250);
    }
    S.loading = false;
    schedule();
    if (S.again) return loadSealed(false);
    return read;
  }

  function subscribe() {
    function dead(e) { S.readError = explain(e); if (!S.ready) say(S.readError, "bad"); schedule(); }
    /* The index is read in three parts, so that a log with more index documents than one
       read returns still shows its tip and its tallies. Keys are "head", "v.<verb>",
       "e.<hash>" and "i.<hash>"; "/" is the character after ".", so each range is one prefix. */
    function part(prefix) { return S.db.collection("ix").where("k", ">=", prefix + ".").where("k", "<", prefix + "/").limit(READ); }
    function docs(qs) { return qs.docs.map(function (d) { return { id: d.id, data: d.data() }; }); }
    S.db.doc("ix/head").onSnapshot(function (snap) {
      takeHead(snap);
      schedule();
      if (S.ready) loadSealed(false);
    }, dead);
    part("v").onSnapshot(function (qs) {
      var ix = A.readIndex(docs(qs));
      S.ix.verbs = ix.verbs; S.bad.verbs = ix.bad; S.cut.verbs = qs.docs.length >= READ;
      schedule();
    }, dead);
    part("e").onSnapshot(function (qs) {
      var ix = A.readIndex(docs(qs));
      S.ix.entities = ix.entities; S.bad.entities = ix.bad; S.cut.entities = qs.docs.length >= READ;
      schedule();
    }, dead);
    if (S.me && S.me.id) {
      S.db.collection("data/users/" + S.me.id + "/as/inbox").limit(READ).onSnapshot(function (qs) {
        var rows = [], bad = 0;
        qs.docs.forEach(function (d) { var r = A.waitingRow(d.id, d.data()); if (r) rows.push(r); else bad++; });
        S.waiting = rows; S.bad.inbox = bad; S.cut.inbox = qs.docs.length >= READ;
        schedule();
      }, dead);
    }
    /* The newest daily brief, if a brief session has run: its drafts sit beside what is due. */
    S.db.collection("brief").orderBy("date", "desc").limit(1).onSnapshot(function (qs) {
      var raw = qs.docs.length ? qs.docs[0].data() : null;
      S.brief = raw ? A.briefDoc(raw) : null;
      S.bad.brief = raw && !S.brief ? 1 : 0;
      S.briefError = "";
      schedule();
    }, function (e) {
      /* A brief that could not be read is said, not passed over: nothing prepared is shown. */
      S.brief = null;
      S.briefError = explain(e);
      schedule();
    });
  }

  /* The tip as a snapshot gives it: kept only if it has the form of one. */
  function takeHead(snap) {
    S.headExists = !!snap.exists;
    S.ix.head = snap.exists ? A.validHead(snap.data()) : null;
    S.bad.head = snap.exists && !S.ix.head ? 1 : 0;
  }

  async function boot() {
    var slow = setTimeout(function () {
      if (!S.ready) say("The log hasn't answered yet. Rows will appear here when it does.");
    }, 10000);
    function stop(text) { clearTimeout(slow); say(text, "bad"); $("row-empty").textContent = text; }

    if (!window.claude || !window.claude.use) return stop("The log is only available when this page is opened where it was published.");
    var got = await Promise.all([window.claude.use("db"), window.claude.use("user")]);
    S.db = got[0];
    S.user = got[1];
    if (!S.db) return stop("The log isn't available in this view. Sign in and open the page again.");
    /* Only the viewer's id is used, to find their own waiting rows. It becomes part of a
       path, so it is taken only if it looks like an id. */
    if (S.user) {
      try {
        var id = typeof S.user.id === "function" ? await S.user.id() : ((await S.user.me()) || {}).id;
        S.me = typeof id === "string" && /^[A-Za-z0-9_-]{1,100}$/.test(id) ? { id: id } : null;
      } catch (e) { S.me = null; }
    }
    subscribe();
    try {
      takeHead(await S.db.doc("ix/head").get());
    } catch (e) {
      return stop(explain(e));
    }
    clearTimeout(slow);
    S.ready = true;
    say("Reading the log…");
    schedule();
    loadSealed(true);
  }

  $("btn-reload").addEventListener("click", function () {
    if (!S.db || S.loading) return;
    S.note = "";
    say("Reloading…");
    loadSealed(true).then(function (read) {
      /* "Reloaded" is said only of a read that came back; a failed one keeps its error. */
      S.note = read ? "Reloaded." : "";
      schedule();
    });
  });
  ["q-verb", "q-actor", "q-show", "q-type"].forEach(function (id) { $(id).addEventListener("change", schedule); });
  $("q-text").addEventListener("input", schedule);

  render();
  boot().catch(function (e) { say(explain(e), "bad"); });
})();
