// The drill: a week of an outreach sequence, compressed. Five targets go through one sequence
// and take five different paths (finishes, replies, bounces, runs late, replies after the
// end). Each simulated day has a "morning", when a brief session seals and says what is due,
// and a "day", when that day's scripted actions are logged. The date is passed to everything
// (`--on`, and `Date:` in the brief's prompt), so a day takes as long as the sessions take:
// about two minutes live, a fraction of a second against the stand-in database.
//
//   node tools/drill.mjs mock
//       The whole week against the stand-in database, sealing with the script. Seconds.
//   node tools/drill.mjs expect --run 0001 --start 2027-01-04
//       What the model says: what is due each morning, and how each target ends up.
//   node tools/drill.mjs day 2 --run 0001 --start 2027-01-04 --out DIR --inbox data/users/ID/as/inbox
//       Build that day's rows and print the writes to send. `again` lists a repeat of a step
//       already in `writes`: send it too, at the same moment, and expect it to be refused.
//   node tools/drill.mjs grade --run 0001 --start 2027-01-04 --dir EXPORT
//       Compare an export of the log (EXPORT/ix, EXPORT/stream, EXPORT/brief) with the model.
//
// The model below is written independently of the script: it is what the script, the skill
// and the sessions are being checked against. test/drill.md has the live procedure.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { BIN, PRE, REG } from "../test/impl.mjs";

const STEPS = [
  { channel: "email", wait_days: 0, what: "Intro email" },
  { channel: "email", wait_days: 2, what: "Follow-up email" },
  { channel: "call", wait_days: 2, what: "Call" },
];
const TARGETS = {
  a: ["client:aspen-example", "Aspen Example Corporation"],
  b: ["client:hazel-example-industries", "Hazel Example Industries"],
  c: ["client:maple-example", "Maple Example LLC"],
  d: ["client:rowan-example", "Rowan Example Group"],
  e: ["client:willow-example", "Willow Example"],
};
// What the user does on each simulated day. [kind, target, ...]
const DAYS = {
  0: [["account", "d"], ["account", "e"], ["define"],
      ["enroll", "a"], ["enroll", "b"], ["enroll", "c"], ["enroll", "d"], ["enroll", "e"],
      ["step", "a", 1], ["step", "b", 1], ["step", "c", 1], ["step", "d", 1], ["step", "e", 1],
      ["end", "c", "bounced", 1, "The address bounced"]],
  2: [["step", "a", 2], ["step", "b", 2], ["step", "e", 2], ["again", "a", 2]],
  3: [["end", "b", "replied", 2, "Asked for pricing"], ["contact", "b", "email", "Replied asking for pricing"], ["step", "d", 2]],
  4: [["step", "a", 3], ["step", "e", 3]],
  5: [["step", "d", 3], ["end", "e", "replied", 3, "Called back, wants a demo"]],
  6: [["end", "a", "no_reply", 3, "No answer after the call"]],
};
const MORNINGS = [1, 2, 3, 4, 5, 6, 7];
const LAST = 7;

const addDays = (d, n) => new Date(Date.parse(d + "T00:00:00Z") + n * 86400000).toISOString().slice(0, 10);
const entityOf = (seq, key) => `enrollment:${seq}/${TARGETS[key][0].replace(":", ".")}`;

/* ---------- the model ---------- */

function model(run, start) {
  const seq = "drill-" + run;
  const enr = {}, mornings = {};
  for (let day = 0; day <= LAST; day++) {
    const date = addDays(start, day);
    if (MORNINGS.includes(day)) {
      mornings[date] = Object.values(enr).filter((e) => e.state === "active" && e.due <= date)
        .map((e) => ({ entity: e.entity, step: e.step + 1, due: e.due, channel: STEPS[e.step].channel }))
        .sort((x, y) => (x.entity < y.entity ? -1 : 1));
    }
    for (const [kind, key, x, y] of DAYS[day] || []) {
      const e = enr[key];
      if (kind === "enroll") {
        enr[key] = { entity: entityOf(seq, key), step: 0, due: addDays(date, STEPS[0].wait_days), state: "active", rows: [["sequence_enrolled", 0, date]] };
      } else if (kind === "step") {
        e.step = x;
        e.rows.push(["sequence_step_done", x, date]);
        if (x < STEPS.length) e.due = addDays(date, STEPS[x].wait_days);
        else { e.due = null; e.state = "finished"; }
      } else if (kind === "end") {
        e.state = x;
        e.due = null;
        e.rows.push(["sequence_ended", y, date]);
      }
    }
  }
  const steps = {}, ends = {};
  for (const e of Object.values(enr)) for (const [verb, n, ] of e.rows) {
    if (verb === "sequence_step_done") steps[`${seq}/${n}`] = (steps[`${seq}/${n}`] || 0) + 1;
    if (verb === "sequence_ended") ends[`${seq}/${n}/${e.state}`] = (ends[`${seq}/${n}/${e.state}`] || 0) + 1;
  }
  return { seq, enr, mornings, steps, ends, enrolled: Object.keys(enr).length };
}

/* ---------- building a day's rows with the script ---------- */

function build(run, start, day, outDir) {
  const seq = "drill-" + run, date = addDays(start, day), steps = JSON.stringify(STEPS);
  const made = [];
  const call = (dir, ...args) => JSON.parse(execFileSync(BIN, [...PRE, args[0], "--registry", REG, "--out", dir, "--source", "system", "--actor", "drill", ...args.slice(1)], { encoding: "utf8" }));
  const main = path.join(outDir, "day-" + day), twice = path.join(outDir, "day-" + day + "-again");
  for (const [kind, key, x, y, z] of DAYS[day] || []) {
    const [target, label] = key ? TARGETS[key] : [];
    const enrol = ["--sequence", seq, "--target", target, "--subject", `${label}: Drill ${run}`, "--on", date];
    let row;
    if (kind === "account") row = call(main, "row", "--verb", "account_updated", "--entity", target, "--subject", label, "--payload", '{"owner":"Sam","stage":"prospect"}');
    else if (kind === "define") row = call(main, "seq-define", "--sequence", seq, "--subject", "Drill " + run, "--steps", steps);
    else if (kind === "enroll") row = call(main, "seq-enroll", ...enrol, "--steps", steps, "--owner", "Sam");
    else if (kind === "step") row = call(main, "seq-step", ...enrol, "--steps", steps, "--step", String(x));
    else if (kind === "again") row = { ...call(twice, "seq-step", ...enrol, "--steps", steps, "--step", String(x)), again: true };
    else if (kind === "end") row = call(main, "seq-end", ...enrol, "--outcome", x, "--step", String(y), "--what", z);
    else if (kind === "contact") row = call(main, "row", "--verb", "contact_logged", "--entity", target, "--subject", label, "--payload", JSON.stringify({ channel: x, what: y, direction: "inbound" }));
    made.push(row);
  }
  return { date, made };
}

/* ---------- grading an export against the model ---------- */

function grade(m, dir, withBriefs) {
  const load = (sub) => {
    const d = path.join(dir, sub), out = {};
    if (fs.existsSync(d)) for (const f of fs.readdirSync(d)) if (f.endsWith(".json")) out[f.slice(0, -5)] = JSON.parse(fs.readFileSync(path.join(d, f), "utf8"));
    return out;
  };
  const ix = load("ix"), stream = Object.values(load("stream")).sort((x, y) => x.id - y.id), briefs = load("brief");
  let bad = 0;
  const check = (cond, what, got) => { if (!cond) { bad++; console.log("FAIL  " + what + (got === undefined ? "" : "  got: " + JSON.stringify(got))); } else console.log("ok    " + what); };
  const state = Object.fromEntries(Object.values(ix).filter((d) => d.entity).map((d) => [d.entity, d]));

  for (const [key, e] of Object.entries(m.enr)) {
    const s = state[e.entity] || {}, name = TARGETS[key][1];
    check(s.f_state === e.state && s.f_step === e.step, `${name}: ends ${e.state} after step ${e.step}`, [s.f_state, s.f_step]);
    check((s.f_next_due || null) === e.due, `${name}: ${e.due ? "next due " + e.due : "nothing more due"}`, s.f_next_due || null);
    const rows = stream.filter((r) => r.entity === e.entity).map((r) => [r.verb, r.payload.step, r.payload.on]);
    check(JSON.stringify(rows) === JSON.stringify(e.rows), `${name}: ${e.rows.length} rows, each step once, on the right days`, rows);
  }
  const field = (doc, f) => Object.fromEntries(((ix[doc] || {}).field?.[f] || []).filter((x) => x.v.startsWith(m.seq)).map((x) => [x.v, x.n]));
  check(JSON.stringify(field("v.sequence_step_done", "seq_step")) === JSON.stringify(m.steps), "tally: how many reached each step", field("v.sequence_step_done", "seq_step"));
  const ends = field("v.sequence_ended", "seq_step_outcome");
  check(Object.keys(m.ends).length === Object.keys(ends).length && Object.entries(m.ends).every(([k, v]) => ends[k] === v), "tally: which step drew each outcome", ends);
  check(field("v.sequence_enrolled", "sequence")[m.seq] === m.enrolled, `tally: ${m.enrolled} enrolled`, field("v.sequence_enrolled", "sequence"));

  if (withBriefs) for (const [date, due] of Object.entries(m.mornings)) {
    const b = briefs[date];
    if (!b) { check(false, `brief of ${date}: exists`); continue; }
    const items = (b.items || []).filter((i) => typeof i.entity === "string" && i.entity.startsWith(`enrollment:${m.seq}/`)).sort((x, y) => (x.entity < y.entity ? -1 : 1));
    const got = items.map((i) => ({ entity: i.entity, step: i.step, due: i.due }));
    check(JSON.stringify(got) === JSON.stringify(due.map(({ entity, step, due }) => ({ entity, step, due }))), `brief of ${date}: ${due.length} due, the right steps`, got);
    const drafted = (i) => typeof i.draft_body === "string" && i.draft_body.trim().length > 20;
    const want = Object.fromEntries(due.map((x) => [x.entity, x.channel]));
    check(items.every((i) => (want[i.entity] === "email" ? drafted(i) : !drafted(i) && typeof i.notes === "string" && i.notes.length > 10)),
      `brief of ${date}: a draft for each email step, notes and no draft for each call`, items.map((i) => [i.entity, drafted(i)]));
  }
  console.log(bad ? `${bad} FAILED` : "drill passed");
  return bad;
}

/* ---------- the whole week against the stand-in database ---------- */

async function mock() {
  const { mockDb } = await import("../test/mockdb.mjs");
  const run = "mock", start = "2027-01-04", m = model(run, start);
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "rowkeep-drill-"));
  const ME = "u_abcdefghijklmnopqrstuv", INBOX = `data/users/${ME}/as/inbox`;
  const db = mockDb(), ver = new Map();
  let seq = 0, bad = 0;
  const bin = (...args) => execFileSync(BIN, [...PRE, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  async function batch(writes) {
    for (const w of writes) {
      const p = w.collection + "/" + w.doc_id, have = ver.get(p) || 0;
      if (have && w.if_version !== have) throw new Error(`version_mismatch on ${p}`);
      if (!have && w.if_version) throw new Error(`pinned a document that does not exist: ${p}`);
    }
    for (const w of writes) {
      const p = w.collection + "/" + w.doc_id;
      if (w.op === "delete") { await db.doc(p).delete(); ver.delete(p); }
      else { await db.doc(p).set(w.data ?? JSON.parse(fs.readFileSync(w.file_path, "utf8"))); ver.set(p, (ver.get(p) || 0) + 1); }
    }
  }
  function exportDocs(dir, collection, ids) {
    fs.mkdirSync(dir, { recursive: true });
    for (const [p, d] of db._docs) {
      if (!p.startsWith(collection + "/")) continue;
      const id = p.slice(collection.length + 1);
      if (id.includes("/") || (ids && !ids.includes(id))) continue;
      fs.writeFileSync(path.join(dir, id + ".json"), JSON.stringify(d));
    }
  }
  async function sealAll() {
    for (;;) {
      const dir = path.join(work, "seal" + ++seq);
      exportDocs(path.join(dir, "inbox"), INBOX);
      const keys = JSON.parse(bin("seal-keys", "--registry", REG, "--inbox", path.join(dir, "inbox")));
      if (!keys.rows) return;
      exportDocs(path.join(dir, "ix"), "ix", keys.keys);
      const versions = keys.keys.filter((k) => ver.has("ix/" + k)).map((k) => k + "=" + ver.get("ix/" + k)).join(",");
      const args = ["seal", "--registry", REG, "--inbox", path.join(dir, "inbox"), "--ix", path.join(dir, "ix"), "--out", path.join(dir, "out"), "--account", ME];
      if (versions) args.push("--versions", versions);
      bin(...args);
      await batch(JSON.parse(fs.readFileSync(path.join(dir, "out", "plan.json"), "utf8")).batch);
    }
  }
  for (let day = 0; day <= LAST; day++) {
    const date = addDays(start, day);
    if (MORNINGS.includes(day)) {
      await sealAll();
      const due = (await db.collection("ix").where("f_next_due", "<=", date).limit(1000).get()).docs.map((d) => d.data())
        .filter((d) => d.type === "enrollment" && d.f_state === "active" && d.f_sequence === m.seq)
        .map((d) => ({ entity: d.entity, step: d.f_step + 1, due: d.f_next_due })).sort((x, y) => (x.entity < y.entity ? -1 : 1));
      const want = m.mornings[date].map(({ entity, step, due }) => ({ entity, step, due }));
      if (JSON.stringify(due) !== JSON.stringify(want)) { bad++; console.log(`FAIL  morning of ${date}: due list`, JSON.stringify(due)); }
      else console.log(`ok    morning of ${date}: ${want.length} due`);
    }
    for (const row of build(run, start, day, work).made) {
      let refused = false;
      try { await batch([{ op: "set", collection: INBOX, doc_id: row.doc_id, file_path: row.file }]); } catch { refused = true; }
      if (refused !== !!row.again) { bad++; console.log(`FAIL  day ${day}: ${row.entity} was ${refused ? "refused" : "accepted"}`); }
    }
  }
  await sealAll();
  const out = path.join(work, "export");
  for (const c of ["ix", "stream"]) exportDocs(path.join(out, c), c);
  bad += grade(m, out, false);
  fs.rmSync(work, { recursive: true, force: true });
  process.exit(bad ? 1 : 0);
}

/* ---------- command line ---------- */

const [cmd, ...rest] = process.argv.slice(2);
const flags = {}, plain = [];
for (let i = 0; i < rest.length; i++) rest[i].startsWith("--") ? (flags[rest[i].slice(2)] = rest[++i]) : plain.push(rest[i]);
const need = (k) => flags[k] ?? (console.error(`${cmd} needs --${k}`), process.exit(2));

if (cmd === "mock") await mock();
else if (cmd === "expect") {
  const m = model(need("run"), need("start"));
  console.log(JSON.stringify({ sequence: m.seq, mornings: m.mornings, final: Object.fromEntries(Object.entries(m.enr).map(([k, e]) => [TARGETS[k][1], { state: e.state, step: e.step, due: e.due }])), steps: m.steps, ends: m.ends }, null, 1));
} else if (cmd === "day") {
  const day = Number(plain[0]), { date, made } = build(need("run"), need("start"), day, need("out"));
  const write = (r) => ({ op: "set", collection: need("inbox"), doc_id: r.doc_id, file_path: r.file });
  console.log(JSON.stringify({ day, date, writes: made.filter((r) => !r.again).map(write), again: made.filter((r) => r.again).map(write) }, null, 1));
} else if (cmd === "grade") process.exit(grade(model(need("run"), need("start")), need("dir"), true) ? 1 : 0);
else { console.log("usage: node tools/drill.mjs mock | expect | day N | grade   (see the top of this file)"); process.exit(2); }
