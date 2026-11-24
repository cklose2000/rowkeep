/* Rowkeep viewer logic for the activity stream. No DOM here, so it runs in Node tests too.
   The format is specified in docs/ACTIVITY-STREAM.md.

   Every document this page shows was written by someone else and may be malformed. Each
   kind is checked where it is read (sealedRow, waitingRow, readIndex, briefDoc): a
   document that does not have the form the page reads is left out on its own and counted,
   so that one bad document cannot blank a panel. */
(function (g) {
  "use strict";
  var A = (g.PAS = {});

  function isObj(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
  function isText(v) { return typeof v === "string"; }
  function isCount(v) { return typeof v === "number" && Number.isSafeInteger(v) && v >= 0; }
  function optText(v) { return v === undefined || v === null || isText(v); }
  /* A value as text, whatever it is. Never String(object): a stored object may carry a
     member named toString that is not a function. */
  function text(v) {
    if (isText(v)) return v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    if (v === null || v === undefined) return "";
    try { return JSON.stringify(v); } catch (e) { return ""; }
  }
  /* A calendar date written YYYY-MM-DD that exists: not the 31st of February. */
  function isDate(v) {
    if (!isText(v) || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
    var t = Date.parse(v + "T00:00:00Z");
    return !isNaN(t) && new Date(t).toISOString().slice(0, 10) === v;
  }
  /* An object whose every value is a whole number: a tally's counts by status, by day. */
  function countMap(v) {
    return isObj(v) && Object.keys(v).every(function (k) { return typeof v[k] === "number" && Number.isSafeInteger(v[k]); });
  }
  /* A tally's list of values: [{v: text or true/false, n: count, s: {field: total}}]. */
  function valueList(v) {
    return Array.isArray(v) && v.every(function (x) {
      return isObj(x) && (isText(x.v) || typeof x.v === "boolean") && isCount(x.n) && (x.s === undefined || countMap(x.s));
    });
  }

  /* The bytes a fingerprint is taken over: sorted keys, no spaces, exactly what Python's
     json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) writes. */
  function canonical(v) {
    if (v === null) return "null";
    if (typeof v === "boolean") return v ? "true" : "false";
    if (typeof v === "number") {
      if (!Number.isSafeInteger(v)) throw new Error("not a whole number: " + v);
      return String(v);
    }
    if (typeof v === "string") return JSON.stringify(v);
    if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
    if (typeof v === "object") {
      return "{" + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ":" + canonical(v[k]); }).join(",") + "}";
    }
    throw new Error("cannot fingerprint a " + typeof v);
  }

  /* The row as it was fingerprinted: without the fingerprint itself and the copied-up p_ fields. */
  function core(row) {
    var out = {};
    Object.keys(row).forEach(function (k) {
      if (k !== "fingerprint" && k.indexOf("p_") !== 0) out[k] = row[k];
    });
    return out;
  }

  async function sha256Hex(text) {
    var bytes = new TextEncoder().encode(text);
    var digest = await g.crypto.subtle.digest("SHA-256", bytes);
    return Array.prototype.map.call(new Uint8Array(digest), function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
  }

  async function fingerprintOf(row) {
    return sha256Hex(canonical(core(row)));
  }

  /* The tip of the chain, ix/head, if it has the form of one: {id, n, fingerprint}. */
  function validHead(d) {
    if (!isObj(d) || !isCount(d.id) || !isCount(d.n) || !isText(d.fingerprint)) return null;
    if (d.id > 0 && !/^[0-9a-f]{64}$/.test(d.fingerprint)) return null;
    /* A log holds every row from 1, so its tip counts as many rows as its newest row's number. */
    if (d.n !== d.id) return null;
    return d;
  }
  /* What makes one tip the same as another: its row, its count and its fingerprint. A
     verdict is about the tip it was computed against, and about no other. */
  function tipSig(d) {
    var h = validHead(d);
    return h ? h.id + "|" + h.n + "|" + h.fingerprint : "";
  }

  /* Check a run of sealed rows, oldest first, against each other and against the tip.
     Resolves {state, ok, from, to, checked, problems: [text], why}. `state` is one of:
       "verified"    every row given was recomputed, they chain, and the last is the tip;
       "empty"       the tip is at row 0 and no rows were given: nothing has been sealed;
       "broken"      a row was changed, is missing, or is not the row the tip records;
       "unverified"  no verdict could be reached, and `why` says why not: there is no valid
                     tip, or the rows given do not reach it.
     Only "verified" is a clean result, and it speaks for rows `from` to `to` and no others. */
  async function verify(rows, head) {
    var problems = [], previous = null, expect = null, failed = "";
    rows = Array.isArray(rows) ? rows : [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      if (expect !== null && row.id !== expect) problems.push("Row " + expect + " is missing.");
      expect = row.id + 1;
      if (previous !== null && row.previous !== previous) problems.push("Row " + row.id + " does not point at the row before it.");
      if (row.id === 1 && row.previous !== "") problems.push("Row 1 should have nothing before it.");
      var fp;
      /* A browser that cannot compute a fingerprint has checked nothing: that is "not
         verified", and never "this row has been changed". */
      try { fp = await fingerprintOf(row); } catch (e) { failed = (e && e.message) || String(e); break; }
      if (fp !== row.fingerprint) problems.push("Row " + row.id + " has been changed since it was sealed.");
      previous = row.fingerprint;
    }
    var last = rows.length ? rows[rows.length - 1] : null;
    var tip = validHead(head), why = "";
    if (!tip) why = rows.length ? "rows are stored, but the tip the index keeps (ix/head) is missing or not in the form of one" : "the tip the index keeps (ix/head) is missing or not in the form of one";
    else if (tip.id === 0 && !last) why = "";
    else if (!last) why = "the tip says row " + tip.id + " is the newest, and no rows came back to check";
    else if (last.id !== tip.id) why = "the newest row read (" + last.id + ") is not the tip the index records (" + tip.id + ")";
    else if (last.fingerprint !== tip.fingerprint) problems.push("Row " + last.id + " is not the row the tip records: the fingerprints differ.");
    if (failed) { problems = []; why = "this browser could not compute a fingerprint (" + failed + "), so no row was checked"; }
    var state = problems.length ? "broken" : why ? "unverified" : last ? "verified" : "empty";
    return {
      state: state,
      ok: state === "verified",
      from: rows.length ? rows[0].id : 0,
      to: last ? last.id : 0,
      checked: rows.length,
      problems: problems,
      why: why
    };
  }

  /* A one-line reading of a payload: the `what`/note first, then the other fields. */
  function detail(payload) {
    if (!isObj(payload)) return "";
    var names = ["what", "note", "decision", "claim"], lead = "", from = "";
    for (var i = 0; i < names.length && !lead; i++) {
      if (isText(payload[names[i]]) && payload[names[i]]) { lead = payload[names[i]]; from = names[i]; }
    }
    var rest = Object.keys(payload).sort().filter(function (k) { return k !== from; }).map(function (k) {
      return k + ": " + (payload[k] === null ? "null" : text(payload[k]));
    });
    return [lead].concat(rest).filter(Boolean).join(" · ");
  }

  function isoMs(ts) { var t = isText(ts) ? Date.parse(ts) : NaN; return isNaN(t) ? 0 : t; }

  /* A sealed row is shown only if it has the fields of one, each of the right kind. */
  function sealedRow(d) {
    if (!isObj(d) || !isCount(d.id) || d.id < 1) return null;
    if (!isText(d.ts) || !isText(d.verb) || !isText(d.entity) || !isText(d.fingerprint) || !isText(d.previous)) return null;
    if (!optText(d.subject) || !optText(d.status) || !optText(d.actor) || !optText(d.source)) return null;
    if (d.payload !== undefined && !isObj(d.payload)) return null;
    return d;
  }

  /* An inbox document is usable when it has the fields a row needs, each of the right kind. */
  function waitingRow(id, d) {
    if (!isObj(d) || !isText(d.verb) || !isText(d.entity)) return null;
    if (!optText(d.ts) || !optText(d.subject) || !optText(d.status) || !optText(d.actor) || !optText(d.source)) return null;
    if (d.payload !== undefined && d.payload !== null && !isObj(d.payload)) return null;
    return {
      doc: id, id: null, ts: d.ts || "", verb: d.verb, entity: d.entity,
      subject: d.subject || "", status: d.status || "",
      payload: d.payload || {}, source: d.source || "", actor: d.actor || "",
      waiting: true
    };
  }

  /* Sealed and waiting rows together, newest first, filtered.
     f = {verb, actor, text, show: "all" | "sealed" | "waiting"} */
  function view(sealed, waiting, f) {
    f = f || {};
    var text = (f.text || "").toLowerCase();
    var rows = [];
    if (f.show !== "waiting") rows = rows.concat(sealed);
    if (f.show !== "sealed") rows = rows.concat(waiting);
    rows = rows.filter(function (r) {
      if (f.verb && r.verb !== f.verb) return false;
      if (f.actor && r.actor !== f.actor) return false;
      if (text && (r.entity + " " + (r.subject || "")).toLowerCase().indexOf(text) < 0) return false;
      return true;
    });
    rows.sort(function (x, y) {
      /* Waiting rows have no number yet; they are newer than anything sealed. */
      if (!!x.waiting !== !!y.waiting) return x.waiting ? -1 : 1;
      if (x.waiting) return isoMs(y.ts) - isoMs(x.ts);
      return y.id - x.id;
    });
    return rows;
  }

  /* A verb's tally, if it has the form of one. */
  function tallyDoc(d) {
    if (!isObj(d) || !isText(d.verb) || !isCount(d.n)) return null;
    if (d.actor !== undefined && !valueList(d.actor)) return null;
    if (["status", "source", "day", "sum"].some(function (k) { return d[k] !== undefined && !countMap(d[k]); })) return null;
    if (d.field !== undefined && !(isObj(d.field) && Object.keys(d.field).every(function (k) { return valueList(d.field[k]); }))) return null;
    if (!optText(d.last_ts)) return null;
    return d;
  }

  /* An entity's state, if it has the form of one. Its current details (f_ fields) are text,
     whole numbers, true or false, as the script keeps them. */
  function entityDoc(d) {
    if (!isObj(d) || !isText(d.entity) || !isCount(d.n)) return null;
    if (!optText(d.type) || !optText(d.subject) || !optText(d.status) || !optText(d.last_verb) || !optText(d.last_ts)) return null;
    if (d.last_id !== undefined && !isCount(d.last_id)) return null;
    if (d.names !== undefined && !(Array.isArray(d.names) && d.names.every(isText))) return null;
    var fine = Object.keys(d).every(function (k) {
      if (k.indexOf("f_") !== 0) return true;
      var v = d[k];
      return v === null || isText(v) || typeof v === "boolean" || (typeof v === "number" && Number.isSafeInteger(v));
    });
    return fine ? d : null;
  }

  /* Split the index collection into its three kinds of document. `bad` counts the
     documents left out because they do not have the form of their kind. */
  function readIndex(docs) {
    var out = { head: null, verbs: [], entities: [], bad: 0 };
    (docs || []).forEach(function (doc) {
      var d = doc && doc.data, k = isObj(d) && isText(d.k) ? d.k : doc && isText(doc.id) ? doc.id : "";
      var kept = null;
      if (k === "head") { kept = validHead(d); if (kept) out.head = kept; }
      else if (k.indexOf("v.") === 0) { kept = tallyDoc(d); if (kept) out.verbs.push(kept); }
      else if (k.indexOf("e.") === 0) { kept = entityDoc(d); if (kept) out.entities.push(kept); }
      else return;
      if (!kept) out.bad++;
    });
    out.verbs.sort(function (x, y) { return y.n - x.n || x.verb.localeCompare(y.verb); });
    out.entities.sort(function (x, y) { return (y.last_id || 0) - (x.last_id || 0); });
    return out;
  }

  /* The current details an entity state carries (its f_ fields) as [label, text] pairs,
     the ones people look for first at the front. Money in cents is shown with its currency. */
  function recordFields(e) {
    var lead = ["stage", "owner", "account", "role", "amount_cents", "close_date", "next_step", "next_due"];
    var keys = Object.keys(e || {}).filter(function (k) { return k.indexOf("f_") === 0 && k !== "f_currency" && k !== "f_merged_into"; })
      .map(function (k) { return k.slice(2); });
    keys.sort(function (x, y) {
      var a = lead.indexOf(x), b = lead.indexOf(y);
      return (a < 0 ? 99 : a) - (b < 0 ? 99 : b) || (x < y ? -1 : x > y ? 1 : 0);
    });
    return keys.map(function (k) {
      return amount(k, e["f_" + k], e.f_currency) || [k.replace(/_/g, " "), text(e["f_" + k])];
    });
  }

  /* A field that holds money as [label, text], or null if it does not. Money is a whole
     number under a name ending in _cents: the label is the name without that ending, and
     the text is the amount with two decimals, after its currency code where one is given.
     ("amount_cents", 4231000, "USD") is ["amount", "USD 42,310.00"]. */
  function amount(name, v, currency) {
    if (!isText(name) || name.slice(-6) !== "_cents" || typeof v !== "number") return null;
    var money = (v / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return [name.slice(0, -6).replace(/_/g, " ") || "amount", (isText(currency) && currency ? currency + " " : "") + money];
  }

  /* The currency a verb's summed amounts are in, read from its tally of the `currency`
     field: the code, where every row of the verb carries the same one; null where the rows
     carry more than one, so that a total of them is a sum of different currencies; and ""
     where the tally does not say (the verb counts no currency, or not every row has one). */
  function tallyCurrency(t) {
    var list = isObj(t) && isObj(t.field) ? t.field.currency : null;
    if (!Array.isArray(list) || !list.length) return "";
    if (list.length > 1) return null;
    return isText(list[0].v) && list[0].v !== "(other)" && list[0].n === t.n ? list[0].v : "";
  }

  /* Whether an entity's state is a record. Sealing writes the same fields for every entity
     (a purchase has `names` and a `type` too), so nothing in a state says "record" outright.
     Three things do: it carries a current detail (an f_ field); or its type is one the
     skill keeps for records (a company, a person, a deal); or its newest row used a verb
     that only makes or changes a record. The last two are what find a record that has no
     details yet. A purchase, a note or a document is none of the three. */
  var RECORD_TYPES = ["client", "vendor", "partner", "person", "deal"];
  var RECORD_VERBS = ["account_updated", "contact_updated", "deal_updated", "deal_closed", "record_updated"];
  function isRecord(e) {
    if (!isObj(e)) return false;
    return Object.keys(e).some(function (k) { return k.indexOf("f_") === 0; })
      || RECORD_TYPES.indexOf(e.type) >= 0 || RECORD_VERBS.indexOf(e.last_verb) >= 0;
  }

  /* Entities that are records and have not been merged away, with or without details. */
  function records(entities, type) {
    return entities.filter(function (e) {
      if (e.f_merged_into) return false;
      if (type && e.type !== type) return false;
      return isRecord(e);
    }).sort(function (x, y) {
      return text(x.type).localeCompare(text(y.type)) || text(x.subject || x.entity).localeCompare(text(y.subject || y.entity));
    });
  }

  /* Everything with a next step: records and running sequence enrollments. `today` is
     "YYYY-MM-DD" in the viewer's own time zone. `late` is days past due (0 today, negative
     for later, null with no date). Dated ones first, oldest due first; then the undated.
     A due date that is not a real date is shown as its own case (`badDate`), never as a
     date. `stepNo`, `sequence` and `lastId` say where the record stands, so that a
     prepared item can be matched to exactly that. */
  function due(entities, today) {
    var out = entities.filter(function (e) {
      if (e.f_merged_into) return false;
      if (e.type === "enrollment" && e.f_state !== "active") return false;
      return isText(e.f_next_due) || isText(e.f_next_step);
    }).map(function (e) {
      var given = isText(e.f_next_due) ? e.f_next_due : "", d = isDate(given) ? given : "";
      var late = d && isDate(today) ? Math.round((Date.parse(today + "T00:00:00Z") - Date.parse(d + "T00:00:00Z")) / 86400000) : null;
      return { entity: e.entity, label: e.subject || e.entity, type: e.type || "", step: isText(e.f_next_step) ? e.f_next_step : "",
               due: d, badDate: !!given && !d, owner: isText(e.f_owner) ? e.f_owner : "", late: late,
               stepNo: isCount(e.f_step) ? e.f_step + 1 : null, sequence: isText(e.f_sequence) ? e.f_sequence : "",
               lastId: isCount(e.last_id) ? e.last_id : null };
    });
    out.sort(function (x, y) {
      if (!!x.due !== !!y.due) return x.due ? -1 : 1;
      return (x.due < y.due ? -1 : x.due > y.due ? 1 : 0) || x.label.localeCompare(y.label);
    });
    return out;
  }

  /* How many of a due list are due now (today or late), counted over the whole list and
     not over whatever part of it is drawn. */
  function dueCounts(list) {
    var now = 0, undated = 0;
    list.forEach(function (x) { if (x.late === null) undated++; else if (x.late >= 0) now++; });
    return { now: now, later: list.length - now - undated, undated: undated };
  }

  /* A daily brief, if it has the form of one: a real date, the row it was made at (`tip`)
     and a list of items. One that lacks any of the three is not shown at all, because its
     items could not be matched to what is due now. Items that are not objects naming an
     entity are left out. */
  function briefDoc(d) {
    if (!isObj(d) || !isDate(d.date) || !isCount(d.tip) || !Array.isArray(d.items)) return null;
    return {
      date: d.date,
      tip: d.tip,
      items: d.items.filter(function (it) { return isObj(it) && isText(it.entity); })
    };
  }

  /* The prepared item for something due, from the newest daily brief, but only while it is
     about exactly what is still due. An item must name the next step it was written for,
     and that must be the record's next step now. For a sequence step it must also name the
     step number and the sequence_defined row it was worked out from (`definition_id`), and
     both must still hold: the enrollment stands at that step and has no row newer than the
     brief's `tip`, and the sequence's newest row is that definition (or a row of another
     kind that the brief had already seen, at or before its `tip`). Anything else is a draft for a step that
     is no longer the one due, and is dropped. */
  function prepared(brief, x, entities) {
    var items = brief && Array.isArray(brief.items) ? brief.items : [];
    var tip = brief && isCount(brief.tip) ? brief.tip : null;
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (!isObj(it) || it.entity !== x.entity) continue;
      if (!isText(it.next_step) || it.next_step !== x.step) continue;
      if (tip !== null && x.lastId !== null && x.lastId > tip) continue;
      if (x.type === "enrollment") {
        if (!isCount(it.step) || it.step !== x.stepNo || !isCount(it.definition_id)) continue;
        var seq = null;
        (entities || []).forEach(function (e) { if (e.entity === "sequence:" + x.sequence) seq = e; });
        if (!seq || !isCount(seq.last_id)) continue;
        /* The sequence's newest row is the definition the item names; or it is some other
           row (a note, an alias) that the brief had already seen when it chose that one. */
        var same = seq.last_id === it.definition_id;
        var seen = seq.last_verb !== "sequence_defined" && tip !== null && seq.last_id > it.definition_id && seq.last_id <= tip;
        if (!same && !seen) continue;
      }
      return it;
    }
    return null;
  }

  A.due = due;
  A.dueCounts = dueCounts;
  A.prepared = prepared;
  A.briefDoc = briefDoc;
  A.tipSig = tipSig;
  A.recordFields = recordFields;
  A.amount = amount;
  A.tallyCurrency = tallyCurrency;
  A.records = records;
  A.isRecord = isRecord;
  A.RECORD_VERBS = RECORD_VERBS;
  A.canonical = canonical;
  A.core = core;
  A.fingerprintOf = fingerprintOf;
  A.validHead = validHead;
  A.verify = verify;
  A.detail = detail;
  A.text = text;
  A.isDate = isDate;
  A.sealedRow = sealedRow;
  A.waitingRow = waitingRow;
  A.view = view;
  A.readIndex = readIndex;
  A.isoMs = isoMs;
})(typeof globalThis !== "undefined" ? globalThis : this);
