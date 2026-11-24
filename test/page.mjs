// node test/page.mjs — the two pages, run without a browser: their scripts are loaded
// against a stand-in for the page's elements and for the database. This checks what the
// pages say: that a verdict is given only for what was verified, that one malformed
// document does not blank a panel, that counts are taken before a list is cut, and that
// the vault's page shows nothing left over from an earlier tip. It cannot check how
// anything looks; the one test of the stylesheet reads its rules as text.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import { fileURLToPath } from "node:url";
import { mockDb } from "./mockdb.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = (f) => fs.readFileSync(path.join(root, "src", f), "utf8");
let passed = 0, failed = 0;
const ok = (cond, name) => { if (cond) passed++; else { failed++; console.log("FAIL  " + name); } };
const settle = (ms = 400) => new Promise((r) => setTimeout(r, ms));
const ME = "u_abcdefghijklmnopqrstuv";
const INBOX = "data/users/" + ME + "/as/inbox/";

// Rows built the way the script builds them (the fingerprints are real).
const canon = (v) => v === null ? "null" : typeof v !== "object" ? JSON.stringify(v)
  : Array.isArray(v) ? "[" + v.map(canon).join(",") + "]"
  : "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
const sha = async (t) => Buffer.from(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(t))).toString("hex");
const ROWS = [
  { verb: "account_updated", entity: "client:alder-example", subject: "Alder Example Co", payload: { owner: "Dana", stage: "prospect" } },
  { verb: "contact_logged", entity: "client:alder-example", subject: "Alder Example Co", payload: { channel: "call", next_step: "Send the quote", next_due: "2020-01-01" } },
  { verb: "purchase_approved", entity: "po:1", subject: "Booth", payload: { amount_cents: 1000, currency: "USD", vendor: "Elm Example Expo" } },
];
// Index documents hold a checksum of themselves in `ck`, for sessions that read them back.
// It is of no use to a person, so the page must show it nowhere.
const CK = "c0ffee123456";
const TALLY = { k: "v.purchase_approved", verb: "purchase_approved", n: 1, status: { ok: 1 }, source: { chat: 1 }, day: {}, actor: [{ v: "human", n: 1 }], field: { vendor: [{ v: "Elm Example Expo", n: 1, s: { amount_cents: 1000 } }] }, sum: { amount_cents: 1000 }, last_id: 3, last_ts: "2026-11-22T19:02:00+00:00", ck: CK };
const RECORD = { k: "e.aaaaaaaaaaaaaaaaaaaa", entity: "client:alder-example", type: "client", subject: "Alder Example Co", names: ["Alder Example Co"], nks: ["alder example"], n: 2, first_id: 1, last_id: 2, last_ts: "2026-11-22T19:01:00+00:00", last_verb: "contact_logged", status: "info", f_owner: "Dana", f_stage: "prospect", f_next_step: "Send the quote", f_next_due: "2020-01-01", ck: CK };

// A log of three sealed rows with its index. `upTo` seals fewer; `head` false leaves the tip out.
async function smallLog({ upTo = 3, head = true, index = true } = {}) {
  const db = mockDb();
  let previous = "";
  for (let i = 0; i < upTo; i++) {
    const row = { id: i + 1, ts: `2026-11-22T19:0${i}:00+00:00`, status: "ok", idempotency_key: null, source: "chat", actor: "human", account: ME, previous, ...ROWS[i] };
    row.fingerprint = previous = await sha(canon(row));
    await db.doc("stream/" + String(i + 1).padStart(10, "0")).set(row);
  }
  if (head) await db.doc("ix/head").set({ k: "head", id: upTo, n: upTo, fingerprint: previous, ts: "2026-11-22T19:02:00+00:00", ck: CK });
  if (index) {
    await db.doc("ix/v.purchase_approved").set(TALLY);
    await db.doc("ix/e.aaaaaaaaaaaaaaaaaaaa").set(RECORD);
    await db.doc("ix/i.bbbbbbbbbbbbbbbbbbbbbbbb").set({ k: "i.bbbbbbbbbbbbbbbbbbbbbbbb", id: 1, ck: CK });
  }
  return db;
}

// Just enough of a page for the scripts to run.
function open(scripts, db, { user, crypto = webcrypto } = {}) {
  const elements = new Map();
  const element = (tag) => ({ tag, children: [], dataset: {}, style: {}, hidden: false, disabled: false, value: "", title: "", className: "", _text: "", listeners: {},
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(" "); },
    set textContent(v) { this._text = String(v); this.children = []; } });
  const document = { getElementById(id) { if (!elements.has(id)) elements.set(id, element("div")); return elements.get(id); }, createElement: element };
  const errors = [];
  const who = user === undefined ? { me: async () => ({ id: ME, name: "A Name" }) } : user;
  const window = { document, claude: { use: async (name) => (name === "db" ? db : name === "user" ? who : null) }, requestAnimationFrame: (fn) => setTimeout(fn, 0), crypto };
  const context = vm.createContext({ window, document, globalThis: window, console: { log() {}, error: (e) => errors.push(e) }, setTimeout, clearTimeout, Promise, Date, Math, JSON, Object, Array, String, Number, isNaN, TextEncoder, Uint8Array, crypto: webcrypto });
  window.window = window;
  for (const [name, text] of scripts) {
    try { vm.runInContext(text, context, { filename: name }); } catch (e) { errors.push(e); }
  }
  const shown = (e) => [e._text, e.title, e.value, ...e.children.map(shown)].join(" ");
  return { errors, window, document, elements, text: (id) => document.getElementById(id).textContent, el: (id) => document.getElementById(id),
    everything: () => [...elements.values()].map(shown).join(" "), click: (id) => document.getElementById(id).listeners.click() };
}
const LOG = [["as-store.js", src("as-store.js")], ["as-ui.js", src("as-ui.js")]];
const VAULT = [["vault-page.html", /<script>([\s\S]*?)<\/script>/.exec(src("vault-page.html"))[1]]];
const noErrors = (ui) => ui.errors.length === 0;
// A database whose reads of one collection are replaced, to count them or make them fail.
const withStream = (db, get) => ({ ...db, collection(name) {
  if (name !== "stream") return db.collection(name);
  return { orderBy() { return this; }, limit() { return this; }, get };
} });

// ---- 1. a small, sound log ----
{
  const db = await smallLog();
  const ui = open(LOG, db);
  await settle();
  ok(noErrors(ui), "the page's scripts run without an error: " + ui.errors.map(String).join("; "));
  ok(ui.text("g-rows") === "3", "it shows the row count from the tip (got " + JSON.stringify(ui.text("g-rows")) + ")");
  ok(ui.text("g-chain") === "Verified" && /rows 1 to 3 recomputed here and matched to the tip/.test(ui.text("g-chain-sub")),
    "it recomputes the fingerprints and says which rows it verified (got " + JSON.stringify(ui.text("g-chain") + " / " + ui.text("g-chain-sub")) + ")");
  ok(ui.text("status") === "Verified rows 1 to 3 against the tip.", "a good result is worded as what was done (got " + JSON.stringify(ui.text("status")) + ")");
  ok(!/Up to date|Intact/.test(ui.everything()), "it nowhere says \"Up to date\" or \"Intact\", which claim more than a page can check");
  ok(/purchase_approved/.test(ui.text("tally-body")) && /Elm Example Expo/.test(ui.text("tally-body")), "it lists the tallies, read as their own part of the index");
  ok(/Alder Example Co/.test(ui.text("rec-body")) && /Dana/.test(ui.text("rec-body")) && !ui.el("rec-panel").hidden, "it lists the record with its current details");
  ok(/Send the quote/.test(ui.text("due-body")) && /late/.test(ui.text("due-body")) && !ui.el("due-panel").hidden, "it shows the overdue follow-up in the Due panel");
  ok(/Booth|po:1/.test(ui.text("row-body")) && /Alder Example Co/.test(ui.text("row-body")), "it lists the rows");
  ok(!ui.everything().includes(CK) && !/\bck\b/.test(ui.everything()), "it shows the checksum field of an index document nowhere, in text or in a tooltip");
  ok(ui.el("bad-note").hidden, "with nothing malformed, it shows no note about documents it could not show");
}

// ---- 2. a verdict needs a valid tip and a matching, non-empty window ----
{
  // The tip says row 9, and the stream gives nothing back.
  const db = mockDb();
  await db.doc("ix/head").set({ k: "head", id: 9, n: 9, fingerprint: "a".repeat(64) });
  let reads = 0;
  const ui = open(LOG, withStream(db, async () => { reads++; return { docs: [] }; }));
  await settle(1200);
  ok(ui.text("g-chain") === "Not verified" && /^Not verified: the tip says row 9 is the newest, and no rows came back/.test(ui.text("status")),
    "a tip at row 9 over an empty stream is \"not verified\", with the reason (got " + JSON.stringify(ui.text("g-chain") + " / " + ui.text("status")) + ")");
  ok(reads === 3, "the stream is read at most three times when the tip is not reached, not without limit (read " + reads + " times)");
  ok(ui.el("status").dataset.kind === "bad" && !/Verified rows/.test(ui.text("status")), "and the status is not shown as a good result");
}
{
  // Rows are stored, and the tip is missing: the stream is still read.
  const db = await smallLog({ head: false });
  const ui = open(LOG, db);
  await settle();
  ok(/Alder Example Co/.test(ui.text("row-body")), "with no tip the stream is still read, and its rows are listed");
  ok(ui.text("g-chain") === "Not verified" && /rows are stored, but the tip/.test(ui.text("status")), "rows without a tip are \"not verified\", not \"empty\" (got " + JSON.stringify(ui.text("status")) + ")");
}
{
  // A tip that is not in the form of one.
  const db = await smallLog();
  await db.doc("ix/head").set({ k: "head", id: "3", n: 3, fingerprint: "nope" });
  const ui = open(LOG, db);
  await settle();
  ok(ui.text("g-chain") === "Not verified" && /1 document could not be shown/.test(ui.text("bad-note")) && !ui.el("bad-note").hidden,
    "a tip whose id is text is not used: the chain is \"not verified\" and the document is counted as one that could not be shown");
}
{
  // The newest row read is not the tip: a seal may have landed, so it reads again, a bounded number of times.
  const db = await smallLog();
  await db.doc("ix/head").set({ k: "head", id: 4, n: 4, fingerprint: "b".repeat(64) });
  let reads = 0;
  const real = db.collection("stream");
  const ui = open(LOG, withStream(db, async () => { reads++; return real.orderBy("id", "desc").limit(400).get(); }));
  await settle(1200);
  ok(reads === 3 && /^Not verified: the newest row read \(3\) is not the tip the index records \(4\)/.test(ui.text("status")),
    "a tip the rows do not reach is retried three times and then reported, with both numbers (reads " + reads + ", " + JSON.stringify(ui.text("status")) + ")");
}
{
  // A new log: no tip, no rows.
  const ui = open(LOG, mockDb());
  await settle();
  ok(ui.text("g-chain") === "Empty" && ui.text("status") === "Nothing sealed yet.", "a log with no tip and no rows is \"Empty\", neither verified nor broken (got " + JSON.stringify(ui.text("status")) + ")");
}
{
  // A row changed after it was sealed.
  const db = await smallLog();
  const row = (await db.doc("stream/0000000002").get()).data();
  row.subject = "Changed";
  await db.doc("stream/0000000002").set(row);
  const ui = open(LOG, db);
  await settle();
  ok(ui.text("g-chain") === "Broken" && /^Not intact: Row 2 has been changed/.test(ui.text("status")), "a changed row is reported as broken, by number (got " + JSON.stringify(ui.text("status")) + ")");
}
{
  // The row at the tip's number is not the row the tip records.
  const db = await smallLog();
  await db.doc("ix/head").set({ k: "head", id: 3, n: 3, fingerprint: "c".repeat(64) });
  const ui = open(LOG, db);
  await settle();
  ok(ui.text("g-chain") === "Broken" && /Row 3 is not the row the tip records/.test(ui.text("status")), "a tip with another fingerprint than its row is broken, not verified");
}

// ---- 3. Reload says "Reloaded" only of a read that came back ----
{
  const db = await smallLog();
  const real = db.collection("stream");
  let fail = false;
  const ui = open(LOG, withStream(db, async () => {
    if (fail) { const e = new Error("the read failed"); e.code = "unavailable"; throw e; }
    return real.orderBy("id", "desc").limit(400).get();
  }));
  await settle();
  fail = true;
  ui.click("btn-reload");
  await settle();
  ok(!/Reloaded/.test(ui.text("status")) && /can't be reached/.test(ui.text("status")) && ui.el("status").dataset.kind === "bad",
    "after a reload whose read failed, the status is the failure and not \"Reloaded.\" (got " + JSON.stringify(ui.text("status")) + ")");
  ok(ui.text("g-chain") !== "Verified", "and the chain is no longer shown as verified");
  fail = false;
  ui.click("btn-reload");
  await settle();
  ok(ui.text("status") === "Reloaded. Verified rows 1 to 3 against the tip.", "after a reload that worked it says so, with what was verified (got " + JSON.stringify(ui.text("status")) + ")");
}

// ---- 4. one malformed document does not blank a panel ----
{
  const db = await smallLog();
  const good = { verb: "note_added", entity: "note:sample", subject: "Healthy waiting row", payload: { note: "<b>Literal text</b>" }, ts: "2026-11-23T10:00:00+00:00", actor: "human", source: "chat", status: "info" };
  await db.doc(INBOX + "b").set(good);
  // A payload whose `what` is an object with a member named toString: text made of it must not throw.
  await db.doc(INBOX + "a").set({ ...good, subject: "Odd payload", payload: { what: { toString: null } } });
  // Documents that are not rows at all.
  await db.doc(INBOX + "c").set({ ...good, verb: 5 });
  await db.doc(INBOX + "d").set({ ...good, payload: "text" });
  const ui = open(LOG, db);
  await settle();
  ok(noErrors(ui) && /Healthy waiting row/.test(ui.text("row-body")) && /Odd payload/.test(ui.text("row-body")) && /<b>Literal text<\/b>/.test(ui.text("row-body")),
    "a waiting row whose payload holds {\"toString\": null} is shown, and so is every other row; markup in a note is shown as text");
  ok(/2 documents could not be shown/.test(ui.text("bad-note")) && !ui.el("bad-note").hidden, "the two inbox documents that are not rows are left out and counted (got " + JSON.stringify(ui.text("bad-note")) + ")");
  ok(ui.el("row-body").children.every((tr) => tr.children.every((td) => td.children.every((c) => c.tag === "span"))), "every value in the rows table is set as text: the only elements inside a cell are the status labels");
  // A tally whose `actor` is not a list, beside a good one.
  await db.doc("ix/v.note_added").set({ k: "v.note_added", verb: "note_added", n: 1, actor: {} });
  await db.doc("ix/e.cccccccccccccccccccc").set({ k: "e.cccccccccccccccccccc", entity: "client:odd", n: 1, f_stage: { nested: true } });
  await db.doc("brief/2026-11-23").set({ date: "2026-11-23", items: "not a list" });
  await settle();
  ok(/purchase_approved/.test(ui.text("tally-body")) && !/note_added/.test(ui.text("tally-body")) && /Alder Example Co/.test(ui.text("rec-body")),
    "a tally whose actor is {} is left out on its own: the good tally and the records are still listed");
  ok(/5 documents could not be shown/.test(ui.text("bad-note")), "and the count now includes the tally, the entity state and the brief (got " + JSON.stringify(ui.text("bad-note")) + ")");
  ok(/^Verified rows 1 to 3/.test(ui.text("status")) && noErrors(ui), "the chain's own verdict stands, and nothing threw");
  ok(/Send the quote/.test(ui.text("due-body")) && !/Prepared by the brief/.test(ui.text("due-note")), "a brief whose items are not a list is not used, and the Due panel is still drawn from the index");
}

// ---- 5. counts are taken before a list is cut, and a cut is said ----
{
  const db = await smallLog();
  for (let i = 0; i < 250; i++) {
    const id = "e." + String(i).padStart(20, "0");
    await db.doc("ix/" + id).set({ k: id, entity: "client:c" + i, type: "client", subject: "Client " + i, n: 1, last_id: 1, f_next_step: "Call", f_next_due: i < 240 ? "2020-01-01" : "2999-01-01" });
  }
  await db.doc("ix/e.undated0000000000000").set({ k: "e.undated0000000000000", entity: "client:undated", type: "client", subject: "Undated", n: 1, last_id: 1, f_next_step: "Think" });
  await db.doc("ix/e.baddate00000000000000").set({ k: "e.baddate00000000000000", entity: "client:baddate", type: "client", subject: "Bad date", n: 1, last_id: 1, f_next_step: "Wait", f_next_due: "2026-02-31" });
  const ui = open(LOG, db);
  await settle(600);
  ok(/^241 due now, 10 later, 2 undated\./.test(ui.text("due-note")), "\"due now\" is counted over every entry, not over the 200 that are drawn (got " + JSON.stringify(ui.text("due-note").slice(0, 60)) + ")");
  ok(ui.el("due-body").children.length === 200 && /The first 200 of 253 are listed/.test(ui.text("due-note")), "and the note says the list was cut: 200 of 253");
  ok(ui.window.PAS.due([{ entity: "x", n: 1, f_next_step: "Wait", f_next_due: "2026-02-31" }], "2026-11-23")[0].badDate === true && !/2026-02-31/.test(ui.text("due-body")),
    "a due date that is no date (the 31st of February) is not treated as one");
  ok(!/Only the first 1,000/.test(ui.text("due-note")), "with fewer records than one read returns, nothing is said about a cut read");
}
{
  const db = await smallLog();
  for (let i = 0; i < 1000; i++) {
    const id = "e." + String(i).padStart(20, "0");
    db._docs.set("ix/" + id, { k: id, entity: "client:c" + i, type: "client", subject: "Client " + i, n: 1, last_id: 1, status: "pending", f_stage: "prospect" });
  }
  for (let i = 0; i < 1000; i++) db._docs.set(INBOX + "w" + String(i).padStart(4, "0"), { verb: "note_added", entity: "note:n" + i, subject: "N", payload: { note: "n" }, ts: "2026-11-23T10:00:00+00:00" });
  const ui = open(LOG, db);
  await settle(1500);
  ok(/Only the first 1,000 records were read, so there may be more/.test(ui.text("rec-note")) && /Only the first 1,000 records/.test(ui.text("pend-note")) && /among the first 1,000 records/.test(ui.text("g-pending-sub")),
    "when a read of records comes back full, the page says there may be more, in the records, pending and gauge notes");
  ok(/Only the first 1,000 waiting rows were read/.test(ui.text("row-summary")), "and the same for waiting rows (got " + JSON.stringify(ui.text("row-summary")) + ")");
  ok(/The first 100 are listed/.test(ui.text("pend-note")) && /1,000\./.test(ui.text("pend-note")), "the pending panel says how many there are and how many it lists");
}

// ---- 6. a prepared item is shown only for exactly the step it was written for ----
{
  const P = open(LOG, mockDb()).window.PAS;
  const enrollment = { entity: "enrollment:intro/client.alder-example", type: "enrollment", subject: "Alder Example Co: Intro", n: 2, last_id: 12, f_state: "active", f_step: 1, f_sequence: "intro", f_next_step: "Step 2 of 3, email: Follow-up", f_next_due: "2020-01-01", f_target: "client:alder-example" };
  const sequence = { entity: "sequence:intro", type: "sequence", subject: "Intro", n: 1, last_id: 10, last_verb: "sequence_defined" };
  const x = P.due([enrollment], "2026-11-23")[0];
  const item = { entity: enrollment.entity, next_step: enrollment.f_next_step, step: 2, definition_id: 10, draft_body: "Hi [name], ..." };
  const brief = (it, tip = 12) => P.briefDoc({ date: "2026-11-23", tip, items: [it] });
  ok(P.prepared(brief(item), x, [enrollment, sequence]) === brief(item).items[0] || P.prepared(brief(item), x, [enrollment, sequence]).draft_body === item.draft_body,
    "an item worked out from this step and this definition is attached");
  ok(P.prepared(brief({ ...item, next_step: undefined }), x, [enrollment, sequence]) === null, "an item that names no next step is not attached (it used to be, whatever it was about)");
  ok(P.prepared(brief({ ...item, step: 1 }), x, [enrollment, sequence]) === null, "nor one written for another step number");
  ok(P.prepared(brief({ ...item, definition_id: undefined }), x, [enrollment, sequence]) === null, "nor a sequence item that does not say which definition it used");
  ok(P.prepared(brief(item), x, [enrollment, { ...sequence, last_id: 14 }]) === null, "a draft made before the sequence was redefined (a newer row on the sequence than the brief saw) is dropped");
  ok(P.prepared(brief(item), x, [{ ...enrollment }, sequence].map((e) => e)) !== null && P.prepared(brief(item, 11), P.due([enrollment], "2026-11-23")[0], [enrollment, sequence]) === null,
    "and so is one made before the enrollment's newest row");
  ok(P.prepared(brief({ ...item, definition_id: 9 }), x, [enrollment, sequence]) === null, "an item naming an older definition than the sequence's newest one is dropped");
  ok(P.prepared(brief({ ...item, definition_id: 9 }), x, [enrollment, { ...sequence, last_verb: "note_added" }]) !== null
    && P.prepared(brief({ ...item, definition_id: 9 }, 12), x, [enrollment, { ...sequence, last_verb: "note_added", last_id: 13 }]) === null,
    "a note logged against the sequence before the brief was made does not hide its drafts; one logged after does, until the next brief");
  const followUp = P.due([RECORD], "2026-11-23")[0];
  ok(P.prepared(brief({ entity: RECORD.entity, next_step: "Send the quote", notes: "Quote outstanding" }), followUp, [RECORD]) !== null
    && P.prepared(brief({ entity: RECORD.entity, next_step: "Something else", notes: "Stale" }), followUp, [RECORD]) === null,
    "a follow-up's note is shown while it is about the record's next step, and not otherwise");
}

// ---- 7. long unbroken text is wrapped, not left to stretch the layout ----
{
  const css = src("as-page.html");
  const rule = (sel) => (new RegExp("(^|\\n)" + sel.replace(/[.]/g, "\\.") + " \\{([^}]*)\\}").exec(css) || [])[2] || "";
  ok(/overflow-wrap: anywhere/.test(rule(".draft")) && /overflow-wrap: anywhere/.test(rule("td.wrap")) && /overflow-wrap: anywhere/.test(rule("summary"))
    && /overflow: hidden/.test(rule("td.clip")) && /max-width/.test(rule("td.clip")), "the stylesheet breaks a draft, its subject and a wrapping cell anywhere, and clips the other text cells");
  const db = await smallLog();
  const long = "x".repeat(3000);
  await db.doc("ix/e.aaaaaaaaaaaaaaaaaaaa").set({ ...RECORD, f_owner: long });
  await db.doc("brief/2026-11-23").set({ date: "2026-11-23", tip: 3, items: [{ entity: RECORD.entity, next_step: "Send the quote", draft_subject: long, draft_body: long }] });
  const ui = open(LOG, db);
  await settle();
  const cells = ui.el("due-body").children[0].children;
  const texty = (td) => td.className === "clip" || td.className === "wrap";
  ok(texty(cells[1]) && texty(cells[2]) && texty(cells[3]) && cells[4].className === "wrap" && cells[4].children[0].children[1].className === "draft",
    "in the Due panel every cell that holds logged text clips or wraps, and the draft is in the element that wraps");
  ok(ui.el("rec-body").children[0].children.slice(0, 3).every(texty) && ui.el("row-body").children.every((tr) => [2, 3, 4, 5].every((i) => texty(tr.children[i]))),
    "and the same in the records and rows tables");
}

// ---- 8. the page asks for the viewer's id and nothing more ----
{
  const caps = JSON.parse(fs.readFileSync(path.join(root, "plugin/skills/rowkeep/setup/capabilities.json"), "utf8"));
  ok(JSON.stringify(caps.log.user) === "{}", "the log's page declares the user capability with no scope: it uses the viewer's id and not their name");
  // The rules are pinned here whole: a rule loosened by mistake would otherwise ship unnoticed.
  const rule = (r) => `${r.path}|${r.read}|${r.write}`;
  ok(JSON.stringify(caps.log.db.rules.map(rule)) === JSON.stringify(["|view|admin", "data/users|admin|admin", "data/users/{self}|interact|interact", "members|view|self", "members/{self}|view|interact"]),
    "the log's rules: owner and editors write the stream and read every inbox, a person writes their own inbox, and a member document can be written by its own member and by nobody else (the level self, which nobody holds)");
  ok(JSON.stringify(caps.vault.db.rules.map(rule)) === JSON.stringify(["|admin|admin"]) && Object.keys(caps).join() === "log,vault",
    "the vault's one rule: owner and editors only");
  const db = await smallLog();
  await db.doc(INBOX + "b").set({ verb: "note_added", entity: "note:sample", subject: "Mine, waiting", payload: { note: "n" } });
  const ui = open(LOG, db, { user: { id: async () => ME } });
  await settle();
  ok(/Mine, waiting/.test(ui.text("row-body")), "given only id(), the page finds the viewer's own waiting rows");
  const odd = open(LOG, db, { user: { id: async () => "../../ix" } });
  await settle();
  ok(noErrors(odd) && !/Mine, waiting/.test(odd.text("row-body")) && /^Verified rows/.test(odd.text("status")), "an id that does not look like one is not put into a path");
  ok(!/A Name/.test(open(LOG, db).everything()), "and a viewer's name is shown nowhere");
}

// ---- 9. the vault's page ----
{
  const db = mockDb();
  const ref = db.doc("vault/head");
  const FP = "d".repeat(64);
  await ref.set({ k: "head", n: 1, id: 1, fingerprint: FP, ts: "2020-01-04T00:00:00+00:00", copied_at: "2020-02-03T01:00:00Z", source: "https://claude.ai/artifact/EXAMPLE<b>" });
  const ui = open(VAULT, db);
  await settle(100);
  ok(noErrors(ui) && ui.text("g-rows") === "1" && ui.text("g-tip") === "#1" && ui.text("d-fp") === FP && ui.el("d-src").children.length === 0 && /<b>/.test(ui.text("d-src")),
    "the vault's page shows the tip, and the log's link as text");
  ok(/^Showing the vault's tip as it is stored: 1 row\.$/.test(ui.text("status")) && !/Up to date/.test(ui.everything()), "its status says what it shows, not that anything is up to date (got " + JSON.stringify(ui.text("status")) + ")");
  const before = ui.text("g-when");
  ok(before !== "—", "a valid copy date is shown");
  // A malformed tip: nothing of the earlier one may stay on screen.
  await ref.set({ k: "head", n: "unknown", id: 2, fingerprint: FP, ts: "invalid", copied_at: "invalid" });
  await settle(100);
  ok(!/NaN/.test(ui.everything()) && ui.text("g-rows") === "—" && ui.text("g-tip") === "—" && ui.text("g-when") === "—" && ui.text("d-fp") === "—" && ui.text("d-src") === "—",
    "a tip whose row count is not a number shows no NaN and resets every field, the earlier date included");
  ok(/cannot be shown: its row count is not a whole number/.test(ui.text("status")) && ui.el("status").dataset.kind === "bad", "and says why");
  // Valid counts with dates that are not dates: the earlier date must not be kept.
  await ref.set({ k: "head", n: 2, id: 2, fingerprint: FP, ts: "invalid", copied_at: "invalid", source: 7 });
  await settle(100);
  ok(ui.text("g-rows") === "2" && ui.text("g-when") === "—" && ui.text("g-when-sub") === "not recorded" && ui.text("g-tip-sub") === "date sealed not recorded" && ui.text("d-src") === "—"
    && /Not recorded in a form this page reads/.test(ui.text("status")) && ui.el("status").dataset.kind !== "good",
    "an invalid date is shown as not recorded, never as the date of the tip before it");
  await ref.set({ k: "head", n: 3, id: 2, fingerprint: FP });
  await settle(100);
  ok(ui.text("g-rows") === "—" && /row count and its newest row number differ/.test(ui.text("status")), "a tip whose count is not its newest row's number is not shown");
  // The tip disappears.
  await ref.set({ k: "head", n: 1, id: 1, fingerprint: FP, ts: "2020-01-04T00:00:00+00:00", copied_at: "2020-02-03T01:00:00Z", source: "x" });
  await settle(100);
  await ref.delete();
  await settle(100);
  ok(ui.text("g-rows") === "—" && ui.text("g-tip") === "—" && ui.text("g-when") === "—" && ui.text("g-tip-sub") === "not copied yet" && ui.text("d-fp") === "—" && ui.text("d-src") === "—"
    && ui.text("status") === "Nothing has been copied into this vault yet.", "when the tip is gone, the count, the date and the fingerprint go with it");
}

// ---- 10. a record with no details is still a record ----
{
  const db = await smallLog();
  const state = (hash, entity, subject, last_verb, more = {}) => db.doc("ix/e." + hash.repeat(20)).set({ k: "e." + hash.repeat(20), entity, type: entity.split(":")[0], subject, names: [subject], nks: [subject.toLowerCase()], n: 1, first_id: 4, last_id: 4, last_ts: "2026-11-23T10:00:00+00:00", last_verb, status: "ok", ...more });
  // Made with account_updated and nothing else: the record the two-account run did not list.
  await state("b", "client:birch-example", "Birch Example Media", "account_updated");
  // The same, with a note logged against it since: its newest verb no longer says "record".
  await state("c", "client:cedar-example", "Cedar Example Print", "note_added", { n: 2 });
  // A record of a kind that has no verb of its own, made with record_updated and no fields.
  await state("d", "project:elm-example", "Elm Example Launch", "record_updated");
  // A record with no details that was merged into another: not listed.
  await state("e", "client:birch-example-media", "Birch Example Media Inc", "entity_merged", { f_merged_into: "client:birch-example" });
  // Things that are not records: a purchase, a note, a document that was given another name.
  await state("f", "po:1", "Booth", "purchase_approved");
  await state("0", "note:kickoff", "Kickoff note", "note_added");
  await state("1", "doc:q1-board-deck", "Q1 board deck", "alias_added", { names: ["Q1 board deck", "Board deck"] });
  const ui = open(LOG, db);
  await settle();
  const line = (name) => (ui.el("rec-body").children.find((tr) => tr.children[0].textContent === name) || { children: [] }).children.map((td) => td.textContent);
  ok(noErrors(ui) && /^4 records\./.test(ui.text("rec-note")), "a record with no details is counted: three of them and the one with details make 4 (got " + JSON.stringify(ui.text("rec-note").slice(0, 30)) + ")");
  ok(line("Birch Example Media")[2] === "no details yet" && line("Birch Example Media")[1] === "client", "a company made with account_updated and no details is listed, with \"no details yet\" where its details would be (got " + JSON.stringify(line("Birch Example Media")) + ")");
  ok(line("Cedar Example Print")[2] === "no details yet", "and it still is once a note has been logged against it: its type says it is a company");
  ok(line("Elm Example Launch")[2] === "no details yet", "a record of any other type is listed while its newest row is one that makes or changes a record");
  ok(/Dana/.test(line("Alder Example Co")[2]) && !/no details yet/.test(line("Alder Example Co")[2]), "a record that has details shows them, as before");
  ok(line("Birch Example Media Inc").length === 0, "a record merged into another is not listed, details or none");
  ok(line("Booth").length === 0 && line("Kickoff note").length === 0 && line("Q1 board deck").length === 0, "a purchase, a note and a document are not records and are not listed");
  const types = ui.el("q-type").children.map((o) => o.value);
  ok(types.join(",") === ",client,project", "the type filter offers the types of the records listed, and no others (got " + JSON.stringify(types) + ")");
  // The verbs the page takes as making a record are verbs of the registry that keep details.
  const reg = JSON.parse(fs.readFileSync(path.join(root, "plugin/skills/rowkeep/registry/verbs.json"), "utf8")).verbs;
  const P = ui.window.PAS;
  ok(P.RECORD_VERBS.length > 0 && P.RECORD_VERBS.every((v) => reg[v] && (reg[v].current || []).length > 0), "every verb the page reads as making a record is in the registry and keeps current details");
  ok(P.isRecord({ entity: "enrollment:x/y", type: "enrollment", n: 1, last_verb: "sequence_ended" }) === false && P.isRecord(null) === false, "an entity with no details, another type and another verb is not a record");
}

// ---- 11. the tallies show money as money ----
{
  const db = await smallLog();
  const tally = (verb, n, sum, field) => db.doc("ix/v." + verb).set({ k: "v." + verb, verb, n, status: { ok: n }, source: { chat: n }, day: {}, actor: [{ v: "human", n }], field, sum, last_id: 3, last_ts: "2026-11-22T19:02:00+00:00", ck: CK });
  // Six approvals, all in one currency: the figures the two-account run showed raw.
  await tally("purchase_approved", 6, { amount_cents: 4231000 }, {
    currency: [{ v: "USD", n: 6, s: { amount_cents: 4231000 } }],
    vendor: [{ v: "Cedar Example <b>Print</b>", n: 5, s: { amount_cents: 231000 } }, { v: "Birch Example Media", n: 1, s: { amount_cents: 4000000 } }] });
  // Three closed deals in two currencies.
  await tally("deal_closed", 3, { amount_cents: 1500000 }, {
    currency: [{ v: "USD", n: 2, s: { amount_cents: 1000000 } }, { v: "EUR", n: 1, s: { amount_cents: 500000 } }],
    stage: [{ v: "won", n: 3, s: { amount_cents: 1500000 } }] });
  // A sum that is not money, and a total that is below zero.
  await tally("time_logged", 2, { minutes: 90 }, {});
  await tally("refund_made", 1, { amount_cents: -5000 }, { currency: [{ v: "USD", n: 1, s: { amount_cents: -5000 } }] });
  // Two rows of which the tally names a currency for one only.
  await tally("fee_charged", 2, { fee_cents: 300 }, { currency: [{ v: "USD", n: 1, s: { fee_cents: 100 } }] });
  const ui = open(LOG, db);
  await settle();
  const cell = (verb) => ((ui.el("tally-body").children.find((tr) => tr.children[0].textContent === verb) || { children: [] }).children[4] || { textContent: "" }).textContent;
  ok(noErrors(ui) && cell("purchase_approved") === "amount total USD 42,310.00 · currency: USD 6 (amount USD 42,310.00) · vendor: Cedar Example <b>Print</b> 5 (amount USD 2,310.00), Birch Example Media 1 (amount USD 40,000.00)",
    "a summed _cents field is shown as an amount with the one currency its rows carry, in the total and in each value's share (got " + JSON.stringify(cell("purchase_approved")) + ")");
  ok(!/amount_cents|4,231,000|231,000\)/.test(ui.text("tally-body")), "and nowhere as a raw count of cents under the field's own name");
  ok(cell("deal_closed") === "amount total 15,000.00 (mixed currencies) · currency: USD 2 (amount USD 10,000.00), EUR 1 (amount EUR 5,000.00) · stage: won 3 (amount 15,000.00)",
    "a total over two currencies says \"mixed currencies\" and carries no code; each currency's own share carries its own (got " + JSON.stringify(cell("deal_closed")) + ")");
  ok(cell("time_logged") === "minutes total 90", "a summed field that is not money stays a whole number under its own name (got " + JSON.stringify(cell("time_logged")) + ")");
  ok(/^amount total USD -50\.00 · /.test(cell("refund_made")), "an amount below zero keeps its sign (got " + JSON.stringify(cell("refund_made")) + ")");
  ok(/^fee total 3\.00 · currency: USD 1 \(fee USD 1\.00\)$/.test(cell("fee_charged")), "where not every row carries the currency, the total is given without a code (got " + JSON.stringify(cell("fee_charged")) + ")");
  ok(ui.el("tally-body").children.length === 5 && ui.el("tally-body").children.every((tr) => tr.children.every((td) => td.children.length === 0)),
    "every tally cell is set as text: markup in a vendor's name is shown, not built");
  const P = ui.window.PAS;
  ok(P.tallyCurrency({ n: 1, field: { vendor: [{ v: "Elm Example Expo", n: 1 }] } }) === "" && P.tallyCurrency({ n: 2, field: { currency: [{ v: "(other)", n: 2 }] } }) === "" && P.tallyCurrency(null) === ""
    && P.amount("minutes", 90, "USD") === null && P.amount("amount_cents", "12", "USD") === null && P.amount("_cents", 150, "")[0] === "amount",
    "a tally that counts no currency names none; a field is money only as a whole number under a name ending in _cents");
  ok(/amount: USD 12\.50/.test(P.recordFields({ f_amount_cents: 1250, f_currency: "USD" }).map((p) => p.join(": ")).join(" ")), "the Records panel still shows a deal's amount with its currency");
}

// ---- 13. a verdict is about one tip, and each read has its own state ----
{
  // The tip's fingerprint changes and its row number does not. The page compared only the
  // number, found nothing new, and went on saying "Verified".
  const db = await smallLog();
  const ui = open(LOG, db);
  await settle();
  const tip = (await db.doc("ix/head").get()).data();
  ok(ui.text("g-chain") === "Verified", "to begin with, the three rows are verified against the tip");
  await db.doc("ix/head").set({ ...tip, fingerprint: "c".repeat(64) });
  await settle();
  ok(ui.text("g-chain") === "Broken" && /Row 3 is not the row the tip records/.test(ui.text("status")) && !/Verified rows/.test(ui.text("status")),
    "when the tip's fingerprint changes at the same row number, the rows are checked again and the verdict changes (got " + JSON.stringify(ui.text("status")) + ")");
  await db.doc("ix/head").set(tip);
  await settle();
  ok(ui.text("status") === "Verified rows 1 to 3 against the tip.", "and when the tip is put back, they are verified again");
  // A tip that counts another number of rows than its newest row's number is not a tip.
  await db.doc("ix/head").set({ ...tip, n: 5 });
  await settle();
  ok(ui.text("g-chain") === "Not verified" && /1 document could not be shown/.test(ui.text("bad-note")) && !/Verified rows/.test(ui.text("status")),
    "a tip whose count is not its newest row's number is not taken as one (got " + JSON.stringify(ui.text("g-chain") + " / " + ui.text("status")) + ")");
}
{
  // The tip changes while the fingerprints are being computed. The verdict computed
  // against the old tip must not be shown as a verdict about the new one.
  const db = await smallLog();
  const tip = (await db.doc("ix/head").get()).data();
  let hashes = 0, swapped = false;
  const slow = { subtle: { async digest(name, bytes) {
    if (++hashes === 2 && !swapped) { swapped = true; await db.doc("ix/head").set({ ...tip, fingerprint: "e".repeat(64) }); }
    return webcrypto.subtle.digest(name, bytes);
  } } };
  const ui = open(LOG, db, { crypto: slow });
  await settle(1500);
  ok(swapped && ui.text("g-chain") === "Broken" && /Row 3 is not the row the tip records/.test(ui.text("status")),
    "a tip that changed during the check is checked again, and the result is about the tip that is there now (got " + JSON.stringify(ui.text("g-chain") + " / " + ui.text("status")) + ")");
}
{
  // The browser cannot compute a fingerprint. Nothing was checked: that is not a changed row.
  const db = await smallLog();
  const ui = open(LOG, db, { crypto: { subtle: { async digest() { throw new Error("digest is not available"); } } } });
  await settle(1200);
  ok(ui.text("g-chain") === "Not verified" && /could not compute a fingerprint \(digest is not available\), so no row was checked/.test(ui.text("status")) && !/has been changed/.test(ui.everything()),
    "a hashing error is \"not verified\" with the reason, never \"has been changed since it was sealed\" (got " + JSON.stringify(ui.text("status")) + ")");
}
{
  // The first read of the stream fails: the gauge must not stay on "Checking".
  const db = await smallLog();
  const ui = open(LOG, withStream(db, async () => { const e = new Error("the read failed"); e.code = "unavailable"; throw e; }));
  await settle();
  ok(ui.text("g-chain") === "Not verified" && /the rows could not be read/.test(ui.text("g-chain-sub")) && ui.el("g-chain").dataset.kind === "bad" && /can't be reached/.test(ui.text("status")),
    "a failed read of the stream is \"Not verified\" with the reason, not \"Checking\" (got " + JSON.stringify(ui.text("g-chain") + " / " + ui.text("g-chain-sub")) + ")");
  ok(!/earlier read/.test(ui.text("row-summary")), "with no rows from an earlier read, nothing is labelled as one");
}
{
  // A read fails after one that worked: the rows still listed are labelled as old.
  const db = await smallLog();
  const real = db.collection("stream");
  let fail = false;
  const ui = open(LOG, withStream(db, async () => {
    if (fail) { const e = new Error("the read failed"); e.code = "unavailable"; throw e; }
    return real.orderBy("id", "desc").limit(400).get();
  }));
  await settle();
  fail = true;
  ui.click("btn-reload");
  await settle();
  ok(ui.text("g-chain") === "Not verified" && /Alder Example Co/.test(ui.text("row-body")) && /The sealed rows listed are from an earlier read: the newest read failed/.test(ui.text("row-summary")),
    "after a read that failed, the rows from the read before it are still listed and are said to be from an earlier read (got " + JSON.stringify(ui.text("row-summary")) + ")");
  fail = false;
  ui.click("btn-reload");
  await settle();
  ok(!/earlier read/.test(ui.text("row-summary")) && ui.text("g-chain") === "Verified", "and the label goes when a read comes back");
}
{
  // Documents in the stream, none of them a row, and no tip: that is not an empty log.
  const db = mockDb();
  await db.doc("stream/0000000001").set({ id: "one", junk: true });
  await db.doc("stream/0000000002").set({ note: "not a row" });
  const ui = open(LOG, db);
  await settle();
  ok(ui.text("g-chain") === "Not verified" && /the stream holds 2 documents, and none is in the form of a row/.test(ui.text("status")) && ui.text("status") !== "Nothing sealed yet." && /2 documents could not be shown/.test(ui.text("bad-note")),
    "rows all left out as malformed, with no tip, are \"not verified\", not \"Nothing sealed yet\" (got " + JSON.stringify(ui.text("status")) + ")");
}
{
  // The brief: only a valid one is used, and a failed read of it is said.
  const P = open(LOG, mockDb()).window.PAS;
  const good = { date: "2026-11-23", tip: 3, items: [] };
  ok(P.briefDoc(good) !== null && P.briefDoc({ ...good, items: [{ entity: "client:x" }, "junk", { entity: 5 }] }).items.length === 1, "a brief with a real date, a tip and a list of items is one; items that are not items are left out");
  ok([{ ...good, date: "2026-13-45" }, { ...good, date: "yesterday" }, { ...good, date: 20261123 }, { ...good, items: "not a list" }, { ...good, items: undefined }, { ...good, tip: undefined }, { ...good, tip: "3" }, { ...good, tip: -1 }, null, []]
    .every((d) => P.briefDoc(d) === null), "a brief with a malformed date, items that are not a list, or no tip is not a brief");
  const db = await smallLog();
  await db.doc("brief/2026-11-23").set({ date: "2026-11-23", items: [{ entity: RECORD.entity, next_step: "Send the quote", notes: "Prepared note" }] });
  const ui = open(LOG, db);
  await settle();
  ok(!/Prepared note/.test(ui.everything()) && /1 document could not be shown/.test(ui.text("bad-note")) && /Send the quote/.test(ui.text("due-body")),
    "a brief with no tip is not shown: its note does not appear, it is counted, and what is due is still listed");
  await db.doc("brief/2026-11-23").set({ date: "2026-11-23", tip: 3, items: [{ entity: RECORD.entity, next_step: "Send the quote", notes: "Prepared note" }] });
  await settle();
  ok(/Prepared note/.test(ui.text("due-body")) && /Prepared by the brief of 2026-11-23/.test(ui.text("due-note")) && ui.el("bad-note").hidden, "the same brief with its tip is shown");
  const noBrief = { ...db, collection(name) {
    if (name !== "brief") return db.collection(name);
    return { orderBy() { return this; }, limit() { return this; }, onSnapshot(next, failed) { queueMicrotask(() => { const e = new Error("no"); e.code = "unavailable"; failed(e); }); return () => {}; } };
  } };
  const blind = open(LOG, noBrief);
  await settle();
  ok(/The daily brief could not be read, so no prepared drafts or notes are shown: The log can't be reached right now/.test(blind.text("bad-note")) && !blind.el("bad-note").hidden && !/Prepared note/.test(blind.everything())
    && blind.text("g-chain") === "Verified" && /Send the quote/.test(blind.text("due-body")),
    "a brief that could not be read is said, where it used to be passed over; the rest of the page is as it was (got " + JSON.stringify(blind.text("bad-note")) + ")");
}
{
  // The tallies are what the index stores: the page does not recount them, and says so.
  const db = await smallLog();
  const ui = open(LOG, db);
  await settle();
  ok(/These are the tallies as the index stores them; this page does not recount them\./.test(ui.text("tally-note")) && !/exact/.test(ui.everything()),
    "the Tallies panel says its figures are as the index stores them, and nowhere calls them exact (got " + JSON.stringify(ui.text("tally-note")) + ")");
}
{
  // The vault's page: a read that fails leaves nothing of the earlier tip on screen.
  const db = mockDb();
  const FP = "d".repeat(64);
  await db.doc("vault/head").set({ k: "head", n: 4, id: 4, fingerprint: FP, ts: "2020-01-04T00:00:00+00:00", copied_at: "2020-02-03T01:00:00Z", source: "https://example.test/log" });
  let fail = null;
  const flaky = { ...db, doc(p) { const ref = db.doc(p); return { ...ref, onSnapshot(next, failed) { fail = failed; return ref.onSnapshot(next); } }; } };
  const ui = open(VAULT, flaky);
  await settle(100);
  ok(ui.text("g-rows") === "4" && ui.text("d-fp") === FP, "the vault's page shows a tip of four rows");
  const e = new Error("permission lost");
  fail(e);
  await settle(50);
  ok(ui.text("g-rows") === "—" && ui.text("g-tip") === "—" && ui.text("g-when") === "—" && ui.text("d-fp") === "—" && ui.text("d-src") === "—"
    && ui.text("status") === "The vault could not be read, so nothing is shown: permission lost." && ui.el("status").dataset.kind === "bad",
    "when a read of the vault fails, the count, the tip and the fingerprint read before are cleared, and the status says it was not read (got " + JSON.stringify(ui.text("status")) + ")");
}

// ---- 12. what ships is what src/ builds ----
{
  const ship = (f) => fs.readFileSync(path.join(root, "plugin/skills/rowkeep/setup", f), "utf8");
  const built = src("as-page.html").replace("/*__STORE__*/", () => "\n" + src("as-store.js")).replace("/*__UI__*/", () => "\n" + src("as-ui.js"));
  ok(built === ship("log-page.html") && src("vault-page.html") === ship("vault-page.html"), "the two pages in the skill folder are the ones src/ builds (run: sh tools/build-skill.sh)");
  const all = src("as-ui.js") + src("as-store.js") + src("vault-page.html");
  ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|\.href\s*=|setAttribute\(/.test(all), "neither page builds markup or links from data: values are set with textContent");
}

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
