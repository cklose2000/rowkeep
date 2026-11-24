"""python tools/make-older-log.py OLD_SCRIPT DEST

Builds the two small logs kept in test/fixtures/v060 with an older version of the script,
so that the tests can show what the current version says about a log it did not write.
OLD_SCRIPT is that version's rowkeep.py, for example

    git show <commit of version 0.6.0>:plugin/skills/rowkeep/scripts/rowkeep.py > old.py

DEST must not exist. Nothing here is needed to run the tests: the fixtures are committed.
Each log is sealed, copied to a vault and left as the folders a full read of it would give:
stream/, ix/, and the vault's rows/ and vault/.

  plain/   rows that version 0.6.0 took and version 0.7.0 would not take at the door: an
           amount written as text, a counted value that is a number, the text "true" beside
           the value true, a date that is no date, a sequence ended with an outcome that is
           not one and then stepped again. The current `check` must say of it what 0.6.0's
           said: chain intact.
  names/   rows whose index documents the current version derives differently: a name
           typed with a combining accent, a Greek name ending in a final sigma, and a
           record with sixty current details on which one row clears a detail and sets a new
           one. The current `check` names those entity states, and `rebuild` rewrites them.
"""
import importlib.util
import io
import json
import os
import shutil
import sys

old_script, dest = sys.argv[1:3]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("older", old_script)
older = importlib.util.module_from_spec(spec)
spec.loader.exec_module(older)

ME = "u_abcdefghijklmnopqrstuv"
REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "skills", "rowkeep", "registry", "verbs.json")
NOW = 1795374301000


def run(*args):
    keep, sys.stdout = sys.stdout, io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        if not older.run(list(args)):
            raise SystemExit("failed: " + " ".join(args))
        return sys.stdout.buffer.getvalue().decode("utf-8")
    finally:
        sys.stdout = keep


def log(name, rows):
    """Seal `rows` (each a dict of what the old `row` would have checked, written straight
    to the inbox so that what the old `seal` accepted is what gets sealed), in rounds, and
    copy the result to a vault."""
    home = os.path.join(dest, name)
    work = os.path.join(dest, name + "-work")
    ix = os.path.join(home, "ix")
    os.makedirs(ix)
    versions = {}
    for at in range(0, len(rows), 10):
        inbox = os.path.join(work, "inbox-%d" % at)
        os.makedirs(inbox)
        for n, row in enumerate(rows[at : at + 10], at):
            doc = dict(row, ts="2026-11-22T19:%02d:%02d+00:00" % (n // 60, n % 60), source="chat", actor="human", at=NOW + n)
            doc = older.with_checksum(older.parse(json.dumps(doc)))
            with open(os.path.join(inbox, "r%03d.json" % n), "w", encoding="utf-8", newline="") as f:
                f.write(older.canonical(doc))
        out = os.path.join(work, "out-%d" % at)
        pins = ["--versions", ",".join("%s=%d" % pair for pair in sorted(versions.items()))] if versions else []
        run("seal", "--registry", REGISTRY, "--inbox", inbox, "--ix", ix, "--out", out, "--account", ME, *pins)
        with open(os.path.join(out, "plan.json"), encoding="utf-8") as f:
            plan = json.load(f)
        if plan["skipped"] or plan["remaining"]:
            raise SystemExit("%s: the old script did not seal every row: %r" % (name, plan["skipped"]))
        for write in plan["batch"]:
            if write["op"] == "set":
                target = os.path.join(home, write["collection"], write["doc_id"] + ".json")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(write["file_path"], target)
                if write["collection"] == "ix":
                    versions[write["doc_id"]] = versions.get(write["doc_id"], 0) + 1
    vault = os.path.join(work, "vault")
    run("vault-plan", "--stream", os.path.join(home, "stream"), "--out", vault, "--source", "https://example.test/log")
    for part in ("rows", "vault"):
        shutil.copytree(os.path.join(vault, part), os.path.join(home, part))
    shutil.rmtree(work)
    print(name, run("check", "--registry", REGISTRY, "--stream", os.path.join(home, "stream"), "--ix", ix).strip())


os.environ["ROWKEEP_TEST_NOW_MS"] = str(NOW + 3600000)
if os.path.exists(dest):
    raise SystemExit(dest + " exists")

ENROLLMENT = "enrollment:trial/client.aspen-example"
STEPS = [{"channel": "email", "wait_days": 0, "what": "One"}, {"channel": "call", "wait_days": 2, "what": "Two"}]
log("plain", [
    {"verb": "purchase_approved", "entity": "po:1", "subject": "Booth", "idempotency_key": "po-1", "payload": {"amount_cents": 420000, "currency": "USD", "vendor": "Expo Co"}},
    # An amount as text: counted, and added to nothing.
    {"verb": "purchase_approved", "entity": "po:2", "subject": "Banners", "payload": {"amount_cents": "125", "currency": "USD", "vendor": "Print Shop"}},
    {"verb": "purchase_approved", "entity": "po:3", "subject": "Refund", "payload": {"amount_cents": -25, "currency": "USD", "vendor": "(other)"}},
    # The value true and the text "true" in one bucket; a number counted under its digits.
    {"verb": "claim_verified", "entity": "claim:a", "subject": "Claim A", "payload": {"passed": True}},
    {"verb": "claim_verified", "entity": "claim:b", "subject": "Claim B", "payload": {"passed": "true"}},
    {"verb": "handoff_sent", "entity": "task:1", "subject": "Task", "payload": {"to": 7}},
    # A date that is no date, kept as a current detail.
    {"verb": "account_updated", "entity": "client:aspen-example", "subject": "Aspen Example Corp.", "payload": {"owner": "Dana", "next_step": "Call", "next_due": "2026-02-31"}},
    {"verb": "alias_added", "entity": "client:aspen-example", "subject": "Aspen Example Corp.", "payload": {"alias": "Aspen Example Rockets"}},
    {"verb": "account_updated", "entity": "client:aspen-example-inc", "subject": "ASPEN EXAMPLE Incorporated", "payload": {"owner": "Sam"}},
    {"verb": "entity_merged", "entity": "client:aspen-example-inc", "subject": "ASPEN EXAMPLE Incorporated", "payload": {"merged_into": "client:aspen-example"}},
    {"verb": "note_added", "entity": "note:1", "subject": "naïve café — 日本語", "payload": {"note": "quotes \"x\", a \\ backslash, a tab\there", "tags": ["é", "ß"]}},
    {"verb": "sequence_defined", "entity": "sequence:trial", "subject": "Trial", "payload": {"steps": STEPS, "step_count": 2}},
    {"verb": "sequence_enrolled", "entity": ENROLLMENT, "subject": "Aspen Example: Trial", "idempotency_key": "seq:trial:client:aspen-example",
     "payload": {"sequence": "trial", "target": "client:aspen-example", "on": "2026-11-22", "step": 0, "step_count": 2, "next_step": "Step 1 of 2, email: One", "next_due": "2026-11-22", "state": "active"}},
    # Ended with an outcome that is not one of the six, and with no key.
    {"verb": "sequence_ended", "entity": ENROLLMENT, "subject": "Aspen Example: Trial",
     "payload": {"sequence": "trial", "target": "client:aspen-example", "on": "2026-11-22", "outcome": "bogus", "step": 0, "seq_outcome": "trial/bogus", "seq_step_outcome": "trial/0/bogus", "state": "bogus", "next_step": None, "next_due": None}},
    # And then a step, out of order, which set it going again.
    {"verb": "sequence_step_done", "entity": ENROLLMENT, "subject": "Aspen Example: Trial", "idempotency_key": "seq:trial:client:aspen-example:step:2",
     "payload": {"sequence": "trial", "target": "client:aspen-example", "on": "2026-11-23", "step": 2, "seq_step": "trial/2", "channel": "call", "what": "Two", "next_step": None, "next_due": None, "state": "finished"}},
    {"verb": "deal_closed", "entity": "deal:aspen-example-renewal", "subject": "Aspen Example renewal", "payload": {"stage": "won", "amount_cents": 1200000, "currency": "USD", "account": "client:aspen-example"}},
])
log("names", [
    # "E" and a combining acute accent: the old key is "e clair bakery".
    {"verb": "account_updated", "entity": "client:eclair", "subject": "Éclair Bakery", "payload": {"owner": "Dana"}},
    # A Greek name typed small, ending in the final sigma: the old key keeps that letter.
    {"verb": "account_updated", "entity": "client:omilos", "subject": "Ομιλος", "payload": {"owner": "Sam"}},
    # Names the old rule and the new one agree on.
    {"verb": "account_updated", "entity": "client:aspen-example", "subject": "ASPEN EXAMPLE Corp.", "payload": {"owner": "Dana"}},
    {"verb": "account_updated", "entity": "client:cafe", "subject": "Café Central", "payload": {"owner": "Sam"}},
    # Sixty current details, then one row that clears the last and sets a new one.
    {"verb": "record_updated", "entity": "project:full", "subject": "Full record", "payload": {"x%02d" % n: n for n in range(60)}},
    {"verb": "record_updated", "entity": "project:full", "subject": "Full record", "payload": {"alpha": 1, "x59": None}},
])
