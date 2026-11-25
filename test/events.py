"""python test/events.py            the tests
python test/events.py --measure [N]   what one event of N households (40) costs, measured

Tests of the event commands (ops/EVENTS.md in the skill; scripts/rowkeep_events.py): an
event of 40 invented households set up, a round recorded and recorded again, answers,
a bounce, closing with no reply, and what is due on a given date.

Nothing here touches a real log. A folder stands in for the database: each document is a
file, each has a version, and a plan is applied to it as the database applies a batch,
all of it or none. That is also what lets a run be counted: every read, every write and
every command a session would make.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# The helpers of the unit tests, and the script as they load it.
from unit import BUNDLED, ME, SCRIPT, command, get, rowkeep  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "events")
HOUSEHOLDS = os.path.join(FIXTURES, "households.json")
ANSWERS = os.path.join(FIXTURES, "answers.json")
EVENT = "garden-party"
INBOX = "data/users/%s/as/inbox" % ME
# Noon on the day the invitations go out; every command is run a second after the last.
START = rowkeep.ts_secs("2026-11-25T12:00:00+00:00") * 1000


class Conflict(Exception):
    """A write was pinned to a version the document is not at: nothing was written."""


class Log:
    """A folder standing in for one log's database."""

    def __init__(self, root, run=command):
        self.root, self.run_command = root, run
        self.versions, self.reads, self.writes, self.commands, self.made = {}, 0, 0, 0, 0
        os.makedirs(os.path.join(root, "db"))

    def at(self, collection):
        path = os.path.join(self.root, "db", *collection.split("/"))
        os.makedirs(path, exist_ok=True)
        return path

    def new(self, name):
        """A folder that does not exist yet, for one command's --out or one read."""
        self.made += 1
        return os.path.join(self.root, "work", "%03d-%s" % (self.made, name))

    def run(self, *args):
        self.commands += 1
        os.environ["ROWKEEP_TEST_NOW_MS"] = str(START + self.commands * 1000)
        return self.run_command(*args)

    def ids(self, collection):
        return sorted(name[:-5] for name in os.listdir(self.at(collection)) if name.endswith(".json"))

    def doc(self, collection, doc_id):
        return get(self.at(collection), doc_id + ".json")

    def send(self, plan_path):
        """One write of a plan's batch (or, from plan-inline.json, its ops): every entry
        or none, as the database applies a batch."""
        plan = get(plan_path)
        entries = []
        for entry in plan["ops"] if "ops" in plan else plan["batch"]:
            collection, _, doc_id = entry["path"].rpartition("/") if "path" in entry else (entry["collection"], "", entry["doc_id"])
            pinned = entry.get("if_version", entry.get("ifVersion"))
            text = rowkeep.canonical(entry["data"]) if "data" in entry else None
            entries.append((entry["op"], collection, doc_id, None if pinned is None else int(pinned), entry.get("file_path"), text))
        assert 1 <= len(entries) <= 50, "a batch holds 1 to 50 writes, and this one has %d" % len(entries)
        for _, collection, doc_id, pinned, _, _ in entries:
            if pinned is not None and self.versions.get(collection + "/" + doc_id, 0) != pinned:
                raise Conflict(collection + "/" + doc_id)
        for op, collection, doc_id, _, source, text in entries:
            path, name = os.path.join(self.at(collection), doc_id + ".json"), collection + "/" + doc_id
            if op == "delete":
                os.remove(path)
                del self.versions[name]
                continue
            if text is None:
                shutil.copyfile(source, path)
            else:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
            self.versions[name] = self.versions.get(name, 0) + 1
        self.writes += 1
        return plan

    def read(self, collection, keep=lambda doc: True):
        """One read: the documents of a collection that pass `keep`, saved to a new folder
        as a read with out_dir saves them. Returns (the folder, their versions)."""
        self.reads += 1
        folder = self.new("read")
        os.makedirs(folder)
        versions = []
        for doc_id in self.ids(collection):
            if keep(self.doc(collection, doc_id)):
                shutil.copyfile(os.path.join(self.at(collection), doc_id + ".json"), os.path.join(folder, doc_id + ".json"))
                versions.append("%s=%d" % (doc_id, self.versions[collection + "/" + doc_id]))
        return folder, ",".join(versions)

    def event_reads(self):
        """The two reads every event command is given: the user's own inbox, and then the
        event's index documents. Returns (the index folder, the inbox folder)."""
        inbox = self.read(INBOX)[0]
        return self.read("ix", lambda doc: doc.get("f_sequence") == EVENT)[0], inbox

    def seal(self):
        """Seal the inbox as ops/SEALER.md's rounds do, until nothing is waiting. Returns
        the plan of each round."""
        plans = []
        while True:
            inbox, inbox_versions = self.read(INBOX)
            if inbox_versions == "":
                # Listed, and empty: this inbox is done, and no command is run to learn it.
                return plans
            ok, said = self.run("seal-keys", "--registry", BUNDLED, "--inbox", inbox)
            assert ok, said
            keys = rowkeep.parse(said.strip().split("\n")[-1])
            if keys["rows"] == 0:
                return plans
            ix, versions = self.read("ix", lambda doc: doc.get("k") in keys["keys"])
            out = self.new("seal")
            more = (["--versions", versions] if versions else []) + ["--inbox-versions", inbox_versions]
            ok, said = self.run("seal", "--registry", BUNDLED, "--inbox", inbox, "--ix", ix, "--out", out, "--account", ME, *more)
            assert ok, said
            plan = self.send(os.path.join(out, "plan.json"))
            # The tip is read back and compared with the plan.
            self.reads += 1
            assert self.doc("ix", "head")["id"] == plan["last_id"]
            plans.append(plan)

    def check(self):
        """The full check's verdict: its last line when it found nothing wrong."""
        ok, said = command("check", "--registry", BUNDLED, "--stream", self.at("stream"), "--ix", self.at("ix"))
        return said.strip().split("\n")[-1] if ok else said

    def copy(self, root):
        """This log as it stands, in another folder."""
        shutil.copytree(self.root, root, dirs_exist_ok=True)
        other = Log.__new__(Log)
        other.__dict__.update(self.__dict__, root=root, versions=dict(self.versions))
        return other


def first_line(said):
    return rowkeep.parse(said.split("\n")[0])


class EventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.log = Log(self.dir)
        os.environ["ROWKEEP_TEST_RANDOM"] = "7"
        self.sizes = {rowkeep.name_key(h["label"]).replace(" ", "-"): int(h["party_size"]) for h in get(HOUSEHOLDS)}
        self.everyone = sorted(self.sizes)

    def tearDown(self):
        for name in ("ROWKEEP_TEST_NOW_MS", "ROWKEEP_TEST_RANDOM"):
            os.environ.pop(name, None)
        self.tmp.cleanup()

    def assertIntact(self):
        verdict = self.log.check()
        self.assertTrue(verdict.endswith("; chain intact"), verdict)

    def write(self, name, value):
        """A file holding a value, as a session writes one for a --NAME-file option."""
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(value if isinstance(value, str) else rowkeep.canonical(value))
        return path

    def event(self, cmd, *more, reads=None, store=True):
        """Run one event command on fresh reads of the log (or on `reads`), and store what
        it planned. Returns (its result line, everything it said)."""
        ix, inbox = reads or self.log.event_reads()
        args = [cmd, "--registry", BUNDLED, "--event-file", self.write("event.txt", EVENT + "\n"), "--ix", ix, "--inbox", inbox]
        if cmd != "event-status":
            args += ["--out", self.log.new(cmd), "--account", ME, "--source", "chat", "--actor", "claude.ai"]
        ok, said = self.log.run(*args, *more)
        self.assertTrue(ok, said)
        result = first_line(said)
        for plan in result.get("plans", []) if store else []:
            self.log.send(plan)
        return result, said

    def refused(self, cmd, *more):
        ix, inbox = self.log.event_reads()
        args = [cmd, "--registry", BUNDLED, "--event", EVENT, "--ix", ix, "--inbox", inbox]
        if cmd != "event-status":
            args += ["--out", self.log.new(cmd), "--account", ME, "--source", "chat", "--actor", "claude.ai"]
        before = self.log.ids(INBOX)
        ok, why = self.log.run(*args, *more)
        self.assertIsNone(ok, why)
        self.assertEqual(self.log.ids(INBOX), before)
        return why

    started = None

    @classmethod
    def tearDownClass(cls):
        if cls.started is not None:
            shutil.rmtree(cls.started.root, True)

    def start(self, seal=True):
        """The event set up for the 40 households of the fixture, enrolled on 25 November.
        Sealed, it is made once and each test takes a copy: sealing 82 rows is seven
        rounds, which is most of what these tests would otherwise spend their time on."""
        if seal and EventTests.started is not None:
            self.log = EventTests.started.copy(self.dir)
            return None
        result, said = self.event(
            "event-setup", "--subject-file", self.write("name.txt", "Garden party"), "--households-file", HOUSEHOLDS,
            "--on", "2026-11-25", "--rsvp-by", "2026-12-08", "--owner-file", self.write("owner.txt", "Robin"),
        )
        if seal:
            self.log.seal()
            EventTests.started = self.log.copy(tempfile.mkdtemp(prefix="rowkeep-events-"))
        return result, said

    def steps(self):
        """The steps the event was set up with, as event-setup wrote them beside its plan."""
        work = os.path.join(self.dir, "work")
        return [os.path.join(work, name, "steps.json") for name in sorted(os.listdir(work)) if name.endswith("event-setup")][0]

    def round(self, step, on, households=None, **more):
        return self.event("event-round", "--steps-file", self.steps(), "--step", str(step), "--on", on, "--households-file", self.write("round.json", households or self.everyone), **more)

    def answers(self, given, on, *more, **options):
        return self.event("event-answer", "--answers-file", given if isinstance(given, str) else self.write("answers.json", given), "--on", on, *more, **options)

    def status(self, on):
        result, said = self.event("event-status", "--on", on)
        return result

    def now(self, on, *names):
        counts = self.status(on)["now"]
        return [int(counts[name]) for name in names]

    def tally(self, verb, field):
        return {entry["v"]: int(entry["n"]) for entry in self.log.doc("ix", "v." + verb)["field"][field]}

    # ---------- setup ----------

    def test_setup_of_forty_households(self):
        result, said = self.start(seal=False)
        # One definition, the event's own record, and a record and an enrollment for each
        # household: 82 rows, in two batches, from one command.
        self.assertEqual((self.log.commands, self.log.writes, result["rows"], result["batches"]), (1, 2, 82, 2))
        self.assertEqual((len(result["added"]), len(result["enrolled"]), result["already"]), (40, 40, []))
        self.assertEqual(result["households"], [rowkeep.name_key(h["label"]).replace(" ", "-") for h in get(HOUSEHOLDS)])
        self.assertTrue(said.strip().endswith("event garden-party: 40 households given; 40 added, 40 enrolled, 0 updated, 0 on record already; planned, not stored"), said)
        self.assertEqual(len(self.log.ids(INBOX)), 82)
        self.assertEqual(rowkeep.steps_in(get(self.steps())), rowkeep.parse('[{"channel":"email","wait_days":0,"what":"Invitation"},{"channel":"email","wait_days":7,"what":"Reminder"},{"channel":"email","wait_days":3,"what":"Last call"}]'))
        # A household's record holds its label and its party size, and no way to reach it.
        docs = [self.log.doc(INBOX, doc_id) for doc_id in self.log.ids(INBOX)]
        ash = [doc for doc in docs if doc["entity"] == "household:garden-party.ash-and-briar"][0]
        self.assertEqual((ash["verb"], ash["subject"], ash["payload"]), ("record_updated", "Ash and Briar", {"sequence": EVENT, "party_size": 2.0}))
        self.assertFalse(any("@" in rowkeep.canonical(doc) for doc in docs))
        # Before anything is sealed the event is all there, as waiting rows.
        status = self.status("2026-11-25")
        self.assertEqual((status["now"]["households"], status["now"]["people"], status["now"]["silent"], status["now"]["due"]), (40, 76, 40, 40))
        self.assertEqual((status["sealed"]["households"], status["waiting_rows"], status["labels"]["wren"]), (0, 82, "Wren"))
        # Asked again while the rows wait, it finds them and adds nothing.
        again, said = self.start(seal=False)
        self.assertEqual((again["rows"], len(again["already"]), again["plans"]), (0, 40, []))
        self.assertTrue(said.strip().endswith("0 added, 0 enrolled, 0 updated, 40 on record already; nothing to store"), said)
        # Sealed by the ordinary rounds, twelve enrollments or sixteen records at a time.
        plans = self.log.seal()
        self.assertEqual((len(plans), sum(int(plan["sealed"]) for plan in plans), self.log.ids(INBOX)), (7, 82, []))
        self.assertIntact()
        status = self.status("2026-11-25")
        self.assertEqual((status["sealed"], status["waiting_rows"]), (status["now"], 0))
        self.assertEqual(self.tally("sequence_enrolled", "sequence"), {EVENT: 40})
        # And asked again once they are sealed: the same.
        again, _ = self.start(seal=False)
        self.assertEqual((again["rows"], len(again["already"])), (0, 40))
        # The day the answers are due is a follow-up on the event's own record, which is
        # how the daily brief comes to list it.
        event = [doc for doc in map(lambda k: self.log.doc("ix", k), self.log.ids("ix")) if doc.get("entity") == "event:garden-party"][0]
        self.assertEqual((event["f_next_due"], event["f_next_step"], event["subject"]), ("2026-12-08", "Close the RSVPs and take the headcount", "Garden party"))

    def test_a_household_added_later_and_one_that_grew(self):
        self.start()
        more = [{"label": "Rook and Sedge", "party_size": 2, "id": "late-guests"}, {"label": "Wren", "party_size": 2}, {"label": "Ash and Briar", "party_size": 2}]
        result, _ = self.event("event-setup", "--subject-file", self.write("name.txt", "Garden party"), "--households-file", self.write("more.json", more), "--on", "2026-11-27")
        # No second definition: one new record and enrollment, and one new party size.
        self.assertEqual((result["rows"], result["added"], result["enrolled"], result["updated"], result["already"]), (3, ["late-guests"], ["late-guests"], ["wren"], ["ash-and-briar"]))
        self.assertEqual(self.now("2026-11-27", "households", "people"), [41, 79])

    # ---------- a round ----------

    def test_a_round_is_one_command_and_one_batch(self):
        self.start()
        commands, writes = self.log.commands, self.log.writes
        result, said = self.round(1, "2026-11-25")
        self.assertEqual((self.log.commands - commands, self.log.writes - writes), (1, 1))
        self.assertEqual((result["rows"], result["batches"], result["recorded"], result["already"], result["ended"]), (40, 1, self.everyone, [], []))
        self.assertTrue(said.strip().endswith("step 1: 40 households named; 40 to record, 0 on record already (0 of them waiting), 0 ended, 0 not at this step, 0 not enrolled; planned, not stored"), said)
        # Each row is the row seq-step makes for that household, to the byte.
        ok, one = self.log.run(
            "seq-step", "--registry", BUNDLED, "--out", self.log.new("one"), "--sequence", EVENT, "--target", "household:garden-party.wren", "--steps-file", self.steps(),
            "--step", "1", "--on", "2026-11-25", "--subject", "Wren: Garden party", "--source", "chat", "--actor", "claude.ai",
        )
        one = rowkeep.parse(one)
        made = self.log.doc(INBOX, one["doc_id"])
        for doc in (made, one["data"]):
            # Made a second apart, they differ in when they were written and in nothing else.
            for name in ("ts", "at", "ck"):
                del doc[name]
        self.assertEqual(made, one["data"])
        self.assertEqual((made["payload"]["next_step"], made["payload"]["next_due"]), ("Step 2 of 3, email: Reminder", "2026-12-02"))
        # The reminder is due a week on, and not a day before.
        self.assertEqual(self.now("2026-12-01", "due", "open"), [0, 40])
        status = self.status("2026-12-02")
        self.assertEqual((status["now"]["due"], status["who"]["due"][0]), (40, {"household": "alder-and-bracken", "step": 2, "due": "2026-12-02"}))
        # Forty step rows are four seals of twelve, twelve, twelve and four.
        plans = self.log.seal()
        self.assertEqual([int(plan["sealed"]) for plan in plans], [12, 12, 12, 4])
        self.assertIntact()
        self.assertEqual(self.tally("sequence_step_done", "seq_step"), {"garden-party/1": 40})

    def test_the_same_round_again_adds_nothing(self):
        self.start()
        # What a session read before the round was recorded: kept, to be used late.
        stale = self.log.event_reads()
        self.round(1, "2026-11-25")
        waiting = self.log.ids(INBOX)
        # Again while the rows wait: each household is listed, and no row is made.
        result, said = self.round(1, "2026-11-25")
        self.assertEqual((result["rows"], result["recorded"], result["waiting"], result["plans"]), (0, [], self.everyone, []))
        self.assertTrue(said.strip().endswith("0 to record, 40 on record already (40 of them waiting), 0 ended, 0 not at this step, 0 not enrolled; nothing to store"), said)
        self.assertEqual(self.log.ids(INBOX), waiting)
        # Again once they are sealed: the same, from the index.
        self.log.seal()
        rows = len(self.log.ids("stream"))
        result, _ = self.round(1, "2026-11-25")
        self.assertEqual((result["rows"], result["already"], result["waiting"]), (0, self.everyone, []))
        # A round named for 40 of which 3 are new to it records the 3.
        result, _ = self.round(2, "2026-12-02", households=self.everyone[:3])
        result, _ = self.round(2, "2026-12-02")
        self.assertEqual((result["rows"], result["waiting"], len(result["recorded"])), (37, self.everyone[:3], 37))
        self.log.seal()
        # And a session that read the log before the first round, and stores its rows
        # after: the seal drops every one of them as a repeat. The once-only check is the
        # seal's, as before; the command only saves the trip.
        rows = len(self.log.ids("stream"))
        result, _ = self.round(1, "2026-11-25", reads=stale)
        self.assertEqual((result["rows"], len(self.log.ids(INBOX))), (40, 40))
        plans = self.log.seal()
        self.assertEqual((sum(len(plan["duplicates"]) for plan in plans), sum(int(plan["sealed"]) for plan in plans)), (40, 0))
        self.assertEqual((len(self.log.ids("stream")), self.log.ids(INBOX)), (rows, []))
        self.assertIntact()
        self.assertEqual(self.tally("sequence_step_done", "seq_step"), {"garden-party/1": 40, "garden-party/2": 40})

    # ---------- answers ----------

    def test_a_yes_with_fewer_coming_a_no_and_a_bounce(self):
        self.start()
        self.round(1, "2026-11-25")
        self.log.seal()
        writes = self.log.writes
        result, said = self.answers(ANSWERS, "2026-11-26")
        # Nine answers: a row on each household's record and the end of its sequence.
        self.assertEqual((result["rows"], result["batches"], self.log.writes - writes), (18, 1, 1))
        self.assertEqual((len(result["yes"]), result["no"]), (6, ["elm-and-fern", "yarrow-and-aster", "dale-and-ember"]))
        docs = {(doc["verb"], doc["entity"]): doc["payload"] for doc in map(lambda k: self.log.doc(INBOX, k), self.log.ids(INBOX))}
        # A yes from a household of two, one of whom is coming.
        self.assertEqual(docs[("record_updated", "household:garden-party.cedar-and-dune")], {"sequence": EVENT, "rsvp": "yes", "coming": 1.0, "on": "2026-11-26"})
        ended = docs[("sequence_ended", "enrollment:garden-party/household.garden-party.cedar-and-dune")]
        self.assertEqual((ended["outcome"], ended["step"], ended["seq_step_outcome"], ended["next_due"]), ("replied", 1.0, "garden-party/1/replied", None))
        # A yes that does not say how many means the whole household; a no means none.
        self.assertEqual(docs[("record_updated", "household:garden-party.ash-and-briar")]["coming"], 2.0)
        self.assertEqual(docs[("record_updated", "household:garden-party.elm-and-fern")], {"sequence": EVENT, "rsvp": "no", "coming": 0.0, "on": "2026-11-26"})
        self.assertEqual(self.now("2026-11-26", "yes", "no", "coming", "silent", "bounced"), [6, 3, 9, 31, 0])
        # A bounce ends the sequence and writes nothing on the household.
        result, _ = self.answers([{"household": "pine-and-quill", "answer": "bounced"}], "2026-11-26")
        self.assertEqual((result["rows"], result["bounced"]), (1, ["pine-and-quill"]))
        self.assertEqual(self.now("2026-11-26", "yes", "no", "bounced", "silent", "coming"), [6, 3, 1, 30, 9])
        # The reminder, named for everyone, goes on record for those still unanswered.
        result, _ = self.round(2, "2026-12-02")
        self.assertEqual((len(result["recorded"]), len(result["ended"]), "cedar-and-dune" in result["ended"], "pine-and-quill" in result["ended"]), (30, 10, True, True))
        # The same answers again add nothing; one that changed adds its one row.
        result, _ = self.answers(ANSWERS, "2026-11-27")
        self.assertEqual((result["rows"], len(result["already"])), (0, 9))
        result, _ = self.answers([{"household": "cedar-and-dune", "answer": "yes", "coming": 2}, {"household": "pine-and-quill", "answer": "bounced"}], "2026-11-28")
        self.assertEqual((result["rows"], result["yes"], result["already"]), (1, ["cedar-and-dune"], ["pine-and-quill"]))
        # A reply from an address that bounced: the first ending of the sequence stands.
        result, _ = self.answers([{"household": "ash-and-briar", "answer": "no_reply"}], "2026-11-28")
        self.assertEqual((result["rows"], result["ended"]), (0, ["ash-and-briar"]))
        before = self.status("2026-12-02")
        self.assertEqual((before["now"]["coming"], before["sealed"]["coming"], before["sealed"]["yes"]), (10, 0, 0))
        self.log.seal()
        self.assertIntact()
        after = self.status("2026-12-02")
        self.assertEqual((after["now"], after["sealed"], after["waiting_rows"]), (before["now"], before["now"], 0))
        # The tallies sealing keeps say how the event is going, with no row read.
        self.assertEqual(self.tally("sequence_ended", "seq_outcome"), {"garden-party/replied": 9, "garden-party/bounced": 1})
        self.assertEqual(self.tally("sequence_ended", "seq_step_outcome"), {"garden-party/1/replied": 9, "garden-party/1/bounced": 1})

    def test_closing_with_no_reply_and_who_attended(self):
        self.start()
        self.round(1, "2026-11-25")
        self.answers(ANSWERS, "2026-11-26")
        # The host closes the event: no reply from the 31 still unanswered, and the
        # follow-up on the event's own record is cleared.
        result, said = self.answers(self.write("none.json", [{"household": "wren", "attended": 1}]), "2026-12-08", "--close")
        self.assertEqual((result["rows"], len(result["no_reply"]), result["attended"], result["closed"]), (33, 31, ["wren"], True))
        self.assertEqual(self.now("2026-12-08", "yes", "no", "no_reply", "silent", "due", "coming"), [6, 3, 31, 0, 0, 9])
        self.log.seal()
        event = [doc for doc in map(lambda k: self.log.doc("ix", k), self.log.ids("ix")) if doc.get("entity") == "event:garden-party"][0]
        self.assertTrue("f_next_step" not in event and "f_next_due" not in event and event["f_closed_on"] == "2026-12-08", event)
        self.assertEqual(self.tally("sequence_ended", "seq_outcome"), {"garden-party/replied": 9, "garden-party/no_reply": 31})
        # Closing again finds nothing to close. --close needs no list of answers.
        result, _ = self.event("event-answer", "--close", "--on", "2026-12-09")
        self.assertEqual((result["rows"], result["no_reply"]), (0, []))
        # A yes that comes after the close is kept on the household; its sequence ended.
        result, _ = self.answers([{"household": "nettle-and-oak", "answer": "yes"}], "2026-12-09")
        self.assertEqual((result["rows"], result["yes"]), (1, ["nettle-and-oak"]))
        self.assertEqual(self.now("2026-12-09", "yes", "no_reply", "coming"), [7, 30, 11])
        # After the event: who came. A household that said yes and did not come is a 0.
        came = [{"household": "ash-and-briar", "attended": 2}, {"household": "cedar-and-dune", "attended": 0}, {"household": "wren", "attended": 1}]
        result, _ = self.answers(came, "2026-12-14")
        self.assertEqual((result["rows"], result["attended"], result["already"]), (2, ["ash-and-briar", "cedar-and-dune"], ["wren"]))
        status = self.status("2026-12-14")
        self.assertEqual((status["now"]["attended"], status["now"]["attended_people"]), (2, 3))
        self.assertEqual(status["who"]["attended"], [{"household": "ash-and-briar", "people": 2}, {"household": "wren", "people": 1}])
        self.log.seal()
        self.assertIntact()

    # ---------- what is due ----------

    def test_what_is_due_on_a_given_date(self):
        self.start()
        # Enrolled on the 25th with no wait before the invitation: it is due that day.
        self.assertEqual(self.now("2026-11-24", "due", "open"), [0, 40])
        self.assertEqual(self.now("2026-11-25", "due", "open"), [40, 0])
        # The invitation goes to 37; three are held back.
        held = ["nettle-and-oak", "reed-and-thorn", "umber-and-vale"]
        sent = [hid for hid in self.everyone if hid not in held]
        self.round(1, "2026-11-25", households=sent)
        self.answers(ANSWERS, "2026-11-26")
        answered = {a["household"] for a in get(ANSWERS)}
        silent = [hid for hid in sent if hid not in answered]
        status = self.status("2026-12-02")
        due = {entry["household"]: (int(entry["step"]), entry["due"]) for entry in status["who"]["due"]}
        self.assertEqual(len(due), 31)
        self.assertEqual({hid for hid, at in due.items() if at == (1, "2026-11-25")}, set(held))
        self.assertEqual({hid for hid, at in due.items() if at == (2, "2026-12-02")}, set(silent))
        # The reminder goes out a day late, so the last call is due three days after that.
        self.round(2, "2026-12-03", households=silent)
        status = self.status("2026-12-05")
        self.assertEqual(([entry["household"] for entry in status["who"]["due"]], status["now"]["open"]), (held, 28))
        self.assertEqual(status["who"]["open"][0], {"household": silent[0], "step": 3, "due": "2026-12-06"})
        self.assertEqual(self.now("2026-12-06", "due", "open"), [31, 0])
        # Sealed, the plain index query the skill and the daily brief use finds the same.
        self.log.seal()
        found = [doc for doc in map(lambda k: self.log.doc("ix", k), self.log.ids("ix")) if doc.get("f_next_due", "9") <= "2026-12-06" and doc.get("type") == "enrollment" and doc.get("f_state") == "active"]
        self.assertEqual(sorted(doc["f_target"][len("household:garden-party.") :] for doc in found), sorted(held + silent))
        # After the last call nothing more is due: those households wait for the close.
        self.round(3, "2026-12-06", households=silent)
        self.assertEqual(self.now("2026-12-20", "due", "open", "finished", "silent"), [3, 0, 28, 31])
        # A step out of order is listed, not recorded.
        result, _ = self.round(2, "2026-12-07", households=held)
        self.assertEqual((result["rows"], result["not_next"]), (0, held))

    # ---------- refusals ----------

    def test_what_is_refused(self):
        setup = ["--subject", "Garden party", "--on", "2026-11-25"]
        for label, word in [("Ash at someone@example.com", "address"), ("Briar 555 010 0199", "telephone"), ("", "label")]:
            why = self.refused("event-setup", *setup, "--households-file", self.write("bad.json", [{"label": label, "party_size": 2}]))
            self.assertIn(word, why)
        twice = [{"label": "Ash and Briar", "party_size": 2}, {"label": "ash & briar", "party_size": 2}]
        self.assertIn("entries 1 and 2 would both be the household", self.refused("event-setup", *setup, "--households-file", self.write("twice.json", twice)))
        self.assertIn("party_size", self.refused("event-setup", *setup, "--households-file", self.write("size.json", [{"label": "Ash", "party_size": 0}])))
        self.assertIn("none can be made from its label", self.refused("event-setup", *setup, "--households-file", self.write("id.json", [{"label": "日本語", "party_size": 1}])))
        self.start()
        steps = self.steps()
        # A household that is not one of the event's is a mistake, not an empty record.
        why = self.refused("event-round", "--steps-file", steps, "--step", "1", "--households-file", self.write("who.json", ["ash-and-briar", "nobody"]))
        self.assertIn('"nobody" is not a household of event garden-party', why)
        self.assertIn("named more than once", self.refused("event-round", "--steps-file", steps, "--step", "1", "--households-file", self.write("who.json", ["wren", "wren"])))
        self.assertIn("event-round needs --steps", self.refused("event-round", "--step", "1", "--households-file", self.write("who.json", ["wren"])))
        self.assertIn("from 1 to 3", self.refused("event-round", "--steps-file", steps, "--step", "4", "--households-file", self.write("who.json", ["wren"])))
        two = self.write("two.json", '[{"channel":"email","wait_days":0,"what":"Invitation"},{"channel":"email","wait_days":7,"what":"Reminder"}]')
        self.assertIn("was enrolled with 3 steps, and the steps given are 2", self.refused("event-round", "--steps-file", two, "--step", "1", "--households-file", self.write("who.json", ["wren"])))
        self.assertIn("is on record with 3 steps", self.refused("event-setup", *setup, "--steps-file", two, "--households-file", HOUSEHOLDS))
        # More coming than the household holds, an answer that is not one, and no answer.
        for entry, word in [
            ({"household": "wren", "answer": "yes", "coming": 2}, "party size, 1"), ({"household": "wren", "answer": "maybe"}, "must be one of"),
            ({"household": "wren"}, "needs an answer"), ({"household": "wren", "answer": "no", "coming": 1}, "coming goes with the answer yes"),
            ({"household": "wren", "answer": "yes", "note": "by the gate"}, "nothing else"),
        ]:
            self.assertIn(word, self.refused("event-answer", "--answers-file", self.write("a.json", [entry])))
        self.assertIn("needs --answers, or --close", self.refused("event-answer"))
        # An option a command does not have is refused, as for every command.
        self.assertIn("has no option", self.refused("event-status", "--close"))
        self.assertIn("has no option", self.refused("event-round", "--steps-file", steps, "--step", "1", "--households-file", self.write("who.json", ["wren"]), "--subject", "x"))
        # Nothing is written over: --out is a new, empty folder.
        used = self.log.new("used")
        os.makedirs(used)
        open(os.path.join(used, "plan.json"), "w").close()
        ix, inbox = self.log.event_reads()
        ok, why = command("event-answer", "--registry", BUNDLED, "--event", EVENT, "--ix", ix, "--inbox", inbox, "--out", used, "--account", ME, "--source", "chat", "--actor", "claude.ai", "--close")
        self.assertTrue(ok is None and "is not a new, empty folder" in why, why)
        # A waiting row that is not what its checksum says counts for nothing, and is named.
        self.round(1, "2026-11-25", households=["wren"])
        doc_id = self.log.ids(INBOX)[0]
        doc = self.log.doc(INBOX, doc_id)
        doc["payload"]["step"] = 2.0
        with open(os.path.join(self.log.at(INBOX), doc_id + ".json"), "w", encoding="utf-8", newline="") as f:
            f.write(rowkeep.canonical(doc))
        status = self.status("2026-11-26")
        self.assertEqual((status["unusable"], status["waiting_rows"], status["now"]["due"]), ([doc_id], 0, 40))

    # ---------- from a chat ----------

    def test_from_a_chat_the_plan_is_ops_and_confirm_compares(self):
        households = self.write("two.json", [{"label": "Ash and Briar", "party_size": 2}, {"label": "Wren", "party_size": 1}])
        setup = [
            "event-setup", "--registry", BUNDLED, "--event", EVENT, "--subject", "Garden party", "--households-file", households, "--on", "2026-11-25",
            "--account", ME, "--source", "chat", "--actor", "claude.ai", "--inline",
        ]
        # Folders nothing was ingested into show nothing of what a chat read.
        plain = [self.log.new("plain-ix"), self.log.new("plain-inbox")]
        for folder in plain:
            os.makedirs(folder)
        ok, why = command(*setup, "--ix", plain[0], "--inbox", plain[1], "--out", self.log.new("no"))
        self.assertTrue(ok is None and "brought in with ingest" in why, why)

        def ingested(collection, docs, name, damaged=0):
            """A read result as a chat gets one, saved and passed through ingest."""
            result = self.write(name + ".txt", '{"docs":[%s],"nextCursor":null}' % ",".join('{"path":%s,"version":1,"data":%s}' % (rowkeep.json_text(collection + "/" + doc_id), rowkeep.canonical(doc)) for doc_id, doc in docs))
            folder = self.log.new(name)
            ok, said = command("ingest", "--from", result, "--out", folder, "--collection", collection)
            self.assertTrue(rowkeep.parse(said)["complete"] is True and len(rowkeep.parse(said)["damaged"]) == damaged, said)
            return folder

        out = self.log.new("chat")
        ok, said = command(*setup, "--ix", ingested("ix", [], "ix"), "--inbox", ingested(INBOX, [], "inbox"), "--out", out)
        self.assertTrue(ok, said)
        result = first_line(said)
        # The definition, and a record and an enrollment for each of the two.
        self.assertEqual((result["rows"], result["plans"]), (5, [os.path.join(out, "plan-inline.json")]))
        self.assertIn('send the 5 entries of "ops"', said)
        plan = self.log.send(result["plans"][0])
        self.assertEqual((len(plan["ops"]), len(plan["after"]), len(self.log.ids(INBOX))), (5, 5, 5))
        # Read back, the inbox is what was planned; one character off, and it is not.
        back = [(doc_id, self.log.doc(INBOX, doc_id)) for doc_id in self.log.ids(INBOX)]
        os.makedirs(os.path.join(self.dir, "back", "data", "users", ME, "as"))
        shutil.move(ingested(INBOX, back, "back-inbox"), os.path.join(self.dir, "back", "data", "users", ME, "as", "inbox"))
        ok, said = command("confirm", "--plan", result["plans"][0], "--read", os.path.join(self.dir, "back"))
        self.assertTrue(ok and said.strip().endswith("5 documents read back and identical to the plan; as planned"), said)
        back[0][1]["subject"] = "Ash and Brier"
        os.makedirs(os.path.join(self.dir, "off", "data", "users", ME, "as"))
        shutil.move(ingested(INBOX, back, "off-inbox", damaged=1), os.path.join(self.dir, "off", "data", "users", ME, "as", "inbox"))
        ok, said = command("confirm", "--plan", result["plans"][0], "--read", os.path.join(self.dir, "off"))
        self.assertTrue(ok is False and "PROBLEM" in said and said.strip().endswith("4 documents read back and identical to the plan; 1 problems"), said)


# ---------- measuring ----------


def in_a_process(*args):
    """Run one command as a session does: a new Python process each time."""
    done = subprocess.run([sys.executable, "-B", SCRIPT] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return (done.returncode == 0, done.stdout.decode("utf-8")) if done.returncode in (0, 1) else (None, done.stderr.decode("utf-8"))


def measure(count):
    """One event of `count` households, run as a session with file exchange runs it, each
    command a process of its own, and counted: commands, reads, writes, seconds."""
    households = [{"label": "Household %s" % "".join("abcdefghij"[int(c)] for c in "%03d" % n), "party_size": 2 - (n % 10 == 9)} for n in range(count)]
    with tempfile.TemporaryDirectory() as folder:
        log = Log(folder, in_a_process)
        files = {}
        for name, value in [("event", EVENT), ("name", "Garden party"), ("households", rowkeep.canonical(households))]:
            files[name] = os.path.join(folder, name + ".txt")
            with open(files[name], "w", encoding="utf-8", newline="") as f:
                f.write(value)
        lines = []

        def phase(name, work):
            before = (log.commands, log.reads, log.writes, time.perf_counter())
            rows = work()
            took = time.perf_counter() - before[3]
            lines.append("%-34s %4d rows  %3d commands  %3d reads  %3d writes  %6.2f s" % (name, rows, log.commands - before[0], log.reads - before[1], log.writes - before[2], took))

        def event(cmd, *more):
            ix, inbox = log.event_reads()
            out = log.new(cmd)
            ok, said = log.run(cmd, "--registry", BUNDLED, "--event-file", files["event"], "--ix", ix, "--inbox", inbox, "--out", out, "--account", ME, "--source", "code", "--actor", "claude-code", *more)
            assert ok, said
            result = first_line(said)
            for plan in result["plans"]:
                log.send(plan)
            return out, result

        def setup():
            out, result = event("event-setup", "--subject-file", files["name"], "--households-file", files["households"], "--on", "2026-11-25")
            files["steps"] = os.path.join(out, "steps.json")
            files["ids"] = os.path.join(folder, "ids.json")
            with open(files["ids"], "w", encoding="utf-8", newline="") as f:
                f.write(rowkeep.canonical(result["households"]))
            return int(result["rows"])

        def sealing():
            return sum(int(plan["sealed"]) for plan in log.seal())

        def one_at_a_time():
            # The same round built the old way: one seq-step for each household. Building
            # only; storing and checking each row would be three more calls a household.
            for n, hid in enumerate(rowkeep.parse(open(files["ids"], encoding="utf-8").read())):
                ok, said = log.run(
                    "seq-step", "--registry", BUNDLED, "--out", log.new("one"), "--sequence", EVENT, "--target", "household:%s.%s" % (EVENT, hid),
                    "--steps-file", files["steps"], "--step", "2", "--on", "2026-12-02", "--source", "code", "--actor", "claude-code",
                )
                assert ok, said
            return count

        phase("setup: build and store", setup)
        phase("setup: seal", sealing)
        phase("round 1: build and store", lambda: int(event("event-round", "--steps-file", files["steps"], "--step", "1", "--on", "2026-11-25", "--households-file", files["ids"])[1]["rows"]))
        phase("round 1: seal", sealing)
        phase("round 1 again: build and store", lambda: int(event("event-round", "--steps-file", files["steps"], "--step", "1", "--on", "2026-11-25", "--households-file", files["ids"])[1]["rows"]))
        phase("for comparison, seq-step each", one_at_a_time)
        check = log.check()
    os.environ.pop("ROWKEEP_TEST_NOW_MS", None)
    print("%d households, Python %s, each command a new process; reads and writes are database calls" % (count, sys.version.split()[0]))
    for line in lines:
        print(line)
    print("check of the whole log afterwards: " + check)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--measure"]:
        measure(int(sys.argv[2]) if len(sys.argv) > 2 else 40)
    else:
        unittest.main()
