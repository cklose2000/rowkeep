"""rowkeep_events: the event commands of the rowkeep script (ops/EVENTS.md in the skill).

This file is not run on its own. rowkeep.py, beside it, loads it when an event command is
asked for and hands it its own names as `rk`; this file imports nothing, so it can read,
write and print only through that script, under that script's rules.

A hosted event is kept with the verbs the registry has. The event is a sequence: its
steps are the invitation and the reminders after it. Each household is a record
(`household:<event>.<id>`, made with `record_updated`) and is enrolled in the sequence.
A round of sending is one `sequence_step_done` row for each household it went to; an
answer ends that household's sequence and, for a yes or a no, is kept on its record.

What these commands add is bulk. One command reads what the log holds about the event
(one read of the index, one of the user's own inbox), works out for every household what
is already on record, and writes every row still missing as documents and one plan, to be
stored in one batch for each 50 rows. The sequence rows are built by the code the seq-*
commands use, so a row made here is the row made one at a time, and sealing takes it, or
drops it as a repeat, exactly as it would that one.
"""

USAGE = """
EVENTS: a hosted event (see ops/EVENTS.md in the skill). The event is a sequence, each
household a record enrolled in it. All take --event ID --ix DIR --inbox DIR: DIR/ix holds
the index documents of the event (query ix: f_sequence == ID) and the other the user's own
inbox, both read whole; an empty read is an empty folder. The three that build rows also
take --out DIR --account USER_ID --source ... --actor NAME [--on 2026-11-27] [--inline],
write every row as an inbox document and the plan beside them, and store nothing: send
each plan's "batch" as one batch (with --inline, the "ops" of plan-inline.json as one
write; the two folders must then be ones `ingest` filled from whole reads). More than 50
rows are planned as part-01, part-02 ..., one batch each. A household is named by its id.

rowkeep event-setup --subject NAME --households JSON [--steps JSON] [--owner NAME] [--rsvp-by DATE]
    Define the event's sequence, if it is not defined, and for each household of
    [{"label": "Ash and Briar", "party_size": 2}, ...] make its record and enroll it, if
    that is not done. A label is a name, never an address or a number. An "id" may be
    given; otherwise it is made from the label. Steps default to Invitation (wait 0),
    Reminder (wait 7), Last call (wait 3), by email; they are written to DIR/steps.json.
    --rsvp-by keeps the day the answers are due as a follow-up on the event's own record.
rowkeep event-round --steps JSON --step N --households JSON
    Record that step N went out to the households of ["ash-and-briar", ...] on --on. A
    household that has the step on record already, sealed or waiting, is listed and no
    row is made for it; so is one whose sequence has ended, or for which N is not next.
rowkeep event-answer [--answers JSON] [--close]
    Record answers: [{"household": "ash-and-briar", "answer": "yes", "coming": 1}, ...].
    An answer is yes (coming: how many, the party size if left out), no, bounced or
    no_reply; it ends the household's sequence. "attended": N records who came. --close
    gives no_reply to every household still unanswered and clears the event's follow-up.
rowkeep event-status [--on 2026-11-27]
    Print what the two reads show, as one line of JSON: counts by answer, the headcount,
    who attended, and for each household still unanswered the step that is next and when
    it is due (due on or before --on, or open); then the same in words. "now" counts
    waiting rows as sealing would take them; "sealed" counts sealed rows alone.
"""

DEFAULT_STEPS = '[{"channel":"email","wait_days":0,"what":"Invitation"},{"channel":"email","wait_days":7,"what":"Reminder"},{"channel":"email","wait_days":3,"what":"Last call"}]'
ANSWERS = ["yes", "no", "bounced", "no_reply"]
MAX_HOUSEHOLDS = 200
MAX_PARTY = 20
CLOSE_STEP = "Close the RSVPs and take the headcount"
RANDOM_IDS = 36 ** 6


def run(rk, cmd, flags):
    return {"event-setup": setup, "event-round": round_done, "event-answer": answer, "event-status": status}[cmd](rk, flags)


# ---------- what the log holds about one event ----------


class Event:
    """One event as two reads show it. `households` and `enrolled` are {household id: state
    document}, with the user's own waiting rows applied as sealing would apply them;
    `sealed` is the same pair from sealed rows alone."""


def whole_read(rk, path, name, inline, collection):
    """A folder `ingest` filled says which read its files are of: it must be a whole read of
    the right collection. With --inline every document came by hand, so it must be one."""
    manifest = rk.read_manifest(path)
    if manifest is None:
        if inline:
            raise rk.Refused(f"--{name}: with --inline, every read is saved as text and brought in with ingest, an empty result included (with --collection). Nothing was planned")
        return
    fits = manifest["collection"] == collection if "/" not in collection or collection.startswith("data/") else manifest["collection"].endswith(collection)
    if not fits:
        raise rk.Refused(f"--{name}: {rk.show_path(path)} holds a read of collection {rk.show(manifest['collection'])}. Nothing was planned")
    if not manifest["complete"]:
        raise rk.Refused(f"--{name}: the read that was ingested into {rk.show_path(path)} was not complete: {manifest['why']}. Read it again, whole, into a new folder")


def view(rk, slug, docs):
    """({household id: its record's state}, {household id: its enrollment's state}) among
    index documents, each of which must sit under the name its entity gives it."""
    households, enrolled = {}, {}
    prefix = f"household:{slug}."
    for key, doc in docs.items():
        entity = rk.text_of(doc, "entity") or ""
        if rk.text_of(doc, "f_sequence") != slug or rk.entity_doc(entity) != key:
            continue
        if entity.startswith(prefix):
            households[entity[len(prefix) :]] = doc
        elif rk.text_of(doc, "type") == "enrollment" and (rk.text_of(doc, "f_target") or "").startswith(prefix):
            enrolled[doc["f_target"][len(prefix) :]] = doc
    return households, enrolled


def load(rk, flags, cmd):
    ev = Event()
    ev.reg = rk.load_registry(flags, cmd)
    ev.slug = rk.need(flags, "event", cmd)
    if not rk.is_slug(ev.slug):
        raise rk.Refused(f"--event {rk.show(ev.slug)} must be a short id in lowercase letters, digits and hyphens, like spring-party")
    ev.inline = "inline" in flags
    ev.account = None
    if cmd != "event-status":
        ev.account = rk.trim(rk.need(flags, "account", cmd))
        if not rk.is_actor(ev.account):
            raise rk.Refused("--account must be the writer's user id")
    ev.collection = f"data/users/{ev.account}/as/inbox" if ev.account else "/as/inbox"
    ix_dir, inbox_dir = rk.folder(flags, "ix", cmd), rk.folder(flags, "inbox", cmd)
    whole_read(rk, ix_dir, "ix", ev.inline, "ix")
    whole_read(rk, inbox_dir, "inbox", ev.inline, ev.collection)
    if flags.get("on") is not None:
        if rk.parse_date(flags["on"]) is None:
            raise rk.Refused(f"--on {rk.show(flags['on'])} must be a date like 2026-11-27, from 1970 to 9999")
        ev.on = flags["on"]
    else:
        ev.on = rk.date_of(rk.now_secs() // 86400)

    # The index, as it was read. A document that did not arrive whole stops the command,
    # as it stops a seal: everything below is worked out from these.
    docs = {}
    for key, doc in rk.docs_in(ix_dir):
        if not isinstance(doc, dict) or rk.checksum_problem(doc) is not None or (rk.CHECKSUM in doc and rk.text_of(doc, "k") != key):
            raise rk.Refused(f"ix/{rk.show(key)} is not an index document that arrived whole. Nothing was planned. Read it again, into a new folder")
        if ev.inline and rk.CHECKSUM not in doc:
            raise rk.Refused(f"ix/{rk.show(key)} holds no checksum (ck), so a miscopy of it could not be caught. Nothing was planned")
        docs[key] = doc
    ev.sealed = view(rk, ev.slug, docs)

    # The user's own waiting rows on top, oldest first, each as a seal would take it: a
    # document a seal would skip, and a sequence row that is not a valid next move from
    # where its enrollment stands, puts nothing on record here either.
    rows, ev.unusable, ev.waiting = [], [], 0
    for doc_id, doc, why in rk.docs_each(inbox_dir):
        try:
            if why is not None:
                raise rk.Refused(why)
            if rk.checksum_problem(rk.as_obj(doc)) is not None or (ev.inline and rk.CHECKSUM not in doc):
                raise rk.Refused("checksum")
            at = rk.at_of(doc)
            draft = rk.check_draft(doc, ev.reg)
            if at is None or rk.too_far_ahead(draft.ts):
                raise rk.Refused("time")
            rows.append((draft.ts, at, doc_id, draft))
        except rk.Refused:
            ev.unusable.append(doc_id)
    rows.sort(key=lambda row: row[:3])
    for _, _, doc_id, draft in rows:
        if rk.sequence_move_problem(docs.get, draft) is not None:
            ev.unusable.append(doc_id)
            continue
        key = rk.entity_doc(draft.entity)
        docs[key] = rk.entity_json(docs.get(key), 0, draft, ev.reg[draft.verb])
        if draft.payload.get("sequence") == ev.slug or draft.entity == "sequence:" + ev.slug:
            ev.waiting += 1
    ev.docs = docs
    ev.households, ev.enrolled = view(rk, ev.slug, docs)
    return ev


def whole(rk, x, least, most):
    return rk.is_number(x) and rk.is_whole(x) and least <= x <= most


def listed(rk, flags, name, cmd):
    """A JSON list given for an option: 1 to MAX_HOUSEHOLDS entries."""
    items = rk.parse_flag(rk.need(flags, name, cmd), "--" + name)
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_HOUSEHOLDS:
        raise rk.Refused(f"--{name} must be a JSON list of 1 to {MAX_HOUSEHOLDS} entries")
    return items


def known(rk, ev, ids):
    """Every household named must be one of this event's, and named once: a mistyped id is
    refused, never taken for a household with nothing on record."""
    for hid in ids:
        if not isinstance(hid, str) or hid not in ev.households:
            raise rk.Refused(f"{rk.show(hid)} is not a household of event {ev.slug} in what was read. Nothing was planned. A household is named by its id, as event-setup and event-status print it")
        if ids.count(hid) > 1:
            raise rk.Refused(f"the household {rk.show(hid)} is named more than once. Nothing was planned")


# ---------- the rows, and the plan that stores them ----------


class Rows:
    """The rows one command builds, each a waiting document for the user's own inbox."""

    def __init__(self, rk, ev, flags, cmd):
        self.rk, self.ev, self.cmd, self.docs, self.ids = rk, ev, cmd, [], set()
        self.out = rk.need(flags, "out", cmd)
        if rk.os.path.lexists(self.out) and (not rk.os.path.isdir(self.out) or rk.os.listdir(self.out)):
            raise rk.Refused(f"{rk.show_path(self.out)}: is not a new, empty folder. Nothing was written")
        self.common = {name: flags[name] for name in ("source", "actor") if rk.flag(flags, name) is not None}
        self.now, self.random = rk.now_ms(), rk.random_number()

    def add(self, draft):
        doc_id, doc = self.rk.waiting_doc(draft, self.now, (self.random + len(self.docs)) % RANDOM_IDS)
        if doc_id in self.ids:
            raise self.rk.Refused(f"two rows of this command would be one document, {self.rk.show(doc_id)}. Nothing was written")
        self.ids.add(doc_id)
        self.docs.append((doc_id, doc))

    def record(self, entity, subject, payload):
        """A record_updated row, as `row` makes it."""
        m = dict(self.common, verb="record_updated", entity=entity, subject=subject, ts=self.rk.iso_utc(self.now // 1000), payload=payload)
        self.add(self.rk.check_draft(m, self.ev.reg))

    def seq(self, cmd, **options):
        """A sequence row, as the seq command of that name makes it."""
        given = dict(self.common, sequence=self.ev.slug, on=self.ev.on)
        given.update({name: value for name, value in options.items() if value is not None})
        self.add(self.rk.seq_draft(cmd, given, self.ev.reg)[0])

    def write(self, result, verdict, extra=()):
        """Write the documents and the plans, print the result as one line of JSON, how to
        send each plan, and the verdict. Nothing is stored by this."""
        rk, plans = self.rk, []
        parts = [self.docs[at : at + rk.MAX_WRITES] for at in range(0, len(self.docs), rk.MAX_WRITES)]
        result = dict(result, event=self.ev.slug, rows=rk.num(len(self.docs)), batches=rk.num(len(parts)), waiting_rows=rk.num(self.ev.waiting), unusable=self.ev.unusable)
        summary = rk.display_json(result)[1:-1]
        for at, part in enumerate(parts):
            plan = rk.Plan(self.out if len(parts) == 1 else rk.os.path.join(self.out, "part-%02d" % (at + 1)))
            for doc_id, doc in part:
                plan.set(self.ev.collection, doc_id, doc)
            after = "[%s]" % ",".join('{"path":%s,"ck":%s}' % (rk.json_text(self.ev.collection + "/" + doc_id), rk.json_text(doc[rk.CHECKSUM])) for doc_id, doc in part)
            plans.append((plan, plan.write(summary + ',"part":%d' % (at + 1), after if self.ev.inline else None)))
        if extra:
            rk.new_files(self.out, list(extra))
        rk.say(rk.display_json(dict(result, plans=[paths[1] if self.ev.inline else paths[0] for _, paths in plans])))
        for plan, paths in plans:
            plan.say_how(*paths)
        if plans and self.ev.inline:
            rk.say("then read the inbox back, whole, and ingest it: confirm compares it with each plan")
        if self.ev.unusable:
            rk.warn(f"note: {len(self.ev.unusable)} waiting documents put nothing on record here, as a seal would skip them: {', '.join(rk.show(x) for x in self.ev.unusable[:10])}")
        rk.say(verdict + ("; planned, not stored" if plans else "; nothing to store"))
        return True


def counted(n, one, many=None):
    return f"{n} {one if n == 1 else many or one + 's'}"


# ---------- event-setup ----------


def slug_of(rk, label):
    """An id for a household, from its label, as `match` suggests one for a record; None
    where the label holds letters an id cannot."""
    key = rk.name_key(label)
    if key == "" or any(c != " " and c not in rk.ASCII_LOWER + rk.ASCII_DIGITS for c in key):
        return None
    slug = key.replace(" ", "-")[:40].strip("-")
    return slug if rk.is_slug(slug) else None


def households_given(rk, flags, cmd):
    """[(id, label, party size)] from --households, checked. A label is a name: what looks
    like an address or a telephone number is refused, since no row may hold one."""
    out, seen = [], {}
    for at, item in enumerate(listed(rk, flags, "households", cmd), 1):
        if not isinstance(item, dict) or any(name not in ("label", "party_size", "id") for name in item):
            raise rk.Refused(f"--households: entry {at} must be an object holding label, party_size and, if you choose one, id, and nothing else. Nothing was planned")
        label = rk.one_spaced(rk.text_of(item, "label") or "")
        if label == "" or len(label) > 120:
            raise rk.Refused(f"--households: entry {at} needs a label of up to 120 characters: the name the host calls the household by")
        if "@" in label or "://" in label or sum(1 for c in label if c.isdigit()) >= 7:
            raise rk.Refused(f"--households: the label of entry {at} holds what looks like an address or a telephone number. A label is a name; contact details are kept out of the log. Nothing was planned")
        if not whole(rk, item.get("party_size"), 1, MAX_PARTY):
            raise rk.Refused(f"--households: entry {at} needs party_size, a whole number from 1 to {MAX_PARTY}")
        hid = item.get("id", slug_of(rk, label))
        if not (isinstance(hid, str) and rk.is_slug(hid)):
            raise rk.Refused(f"--households: entry {at} needs an id in lowercase letters, digits and hyphens: " + ("the one given is not" if "id" in item else "none can be made from its label"))
        if hid in seen:
            raise rk.Refused(f"--households: entries {seen[hid]} and {at} would both be the household {rk.show(hid)}: give each an id of its own. Nothing was planned")
        seen[hid] = at
        out.append((hid, label, int(item["party_size"])))
    return out


def setup(rk, flags):
    cmd = "event-setup"
    ev = load(rk, flags, cmd)
    rows = Rows(rk, ev, flags, cmd)
    name = rk.one_spaced(rk.need(flags, "subject", cmd))
    if len(name) > 120:
        raise rk.Refused("--subject is the event's name, of up to 120 characters")
    steps_text = flags["steps"] if rk.flag(flags, "steps") is not None else DEFAULT_STEPS
    steps = rk.read_steps({"steps": steps_text}, cmd)
    steps_text = rk.canonical(rk.steps_json(steps))
    owner = rk.flag(flags, "owner")
    given = households_given(rk, flags, cmd)

    # One number of steps for every household of an event: a round is built from one list.
    sequence = ev.docs.get(rk.entity_doc("sequence:" + ev.slug))
    counts = {rk.num_of(doc, "f_step_count") for doc in list(ev.enrolled.values()) + ([sequence] if sequence else [])}
    counts.discard(0)
    if counts - {len(steps)}:
        raise rk.Refused(f"event {ev.slug} is on record with {sorted(counts)[0]} steps, and the steps given are {len(steps)}. Nothing was planned. Give the steps of its sequence_defined row")
    if sequence is None and not ev.enrolled:
        rows.seq("seq-define", steps=steps_text, subject=name)

    added, updated, enrolled, already = [], [], [], []
    for hid, label, size in given:
        entity = f"household:{ev.slug}.{hid}"
        have = ev.households.get(hid)
        if have is None:
            rows.record(entity, label, {"sequence": ev.slug, "party_size": rk.num(size)})
            added.append(hid)
        elif rk.name_key(rk.text_of(have, "subject") or "") != rk.name_key(label):
            raise rk.Refused(f"the household {rk.show(hid)} of event {ev.slug} is on record under another label. Nothing was planned. Give this one an id of its own, or the label it has")
        elif rk.num_of(have, "f_party_size") != size:
            rows.record(entity, rk.text_of(have, "subject"), {"sequence": ev.slug, "party_size": rk.num(size)})
            updated.append(hid)
        if hid not in ev.enrolled:
            rows.seq("seq-enroll", target=entity, steps=steps_text, subject=f"{label}: {name}", owner=owner)
            enrolled.append(hid)
        elif hid not in added + updated:
            already.append(hid)

    by = rk.flag(flags, "rsvp-by")
    if by is not None:
        if rk.parse_date(by) is None:
            raise rk.Refused(f"--rsvp-by {rk.show(by)} must be a date like 2026-11-27")
        have = ev.docs.get(rk.entity_doc("event:" + ev.slug))
        if have is None or rk.text_of(have, "f_rsvp_by") != by:
            rows.record("event:" + ev.slug, name, {"sequence": ev.slug, "rsvp_by": by, "next_step": CLOSE_STEP, "next_due": by})

    result = {"households": [hid for hid, _, _ in given], "added": added, "updated": updated, "enrolled": enrolled, "already": already}
    verdict = f"event {ev.slug}: {counted(len(given), 'household')} given; {len(added)} added, {len(enrolled)} enrolled, {len(updated)} updated, {len(already)} on record already"
    return rows.write(result, verdict, [(rk.os.path.join(rows.out, "steps.json"), steps_text + "\n")])


# ---------- event-round ----------


def round_done(rk, flags):
    cmd = "event-round"
    ev = load(rk, flags, cmd)
    rows = Rows(rk, ev, flags, cmd)
    steps_text = rk.need(flags, "steps", cmd)
    steps = rk.read_steps({"steps": steps_text}, cmd)
    step = rk.parse_unsigned(rk.need(flags, "step", cmd))
    if step is None or not 1 <= step <= len(steps):
        raise rk.Refused(f"--step must be the number of the step that went out, from 1 to {len(steps)}")
    ids = listed(rk, flags, "households", cmd)
    known(rk, ev, ids)
    sealed = ev.sealed[1]
    done, already, waiting, ended, not_next, not_enrolled = [], [], [], [], [], []
    for hid in ids:
        at = ev.enrolled.get(hid)
        if at is None:
            not_enrolled.append(hid)
            continue
        if rk.num_of(at, "f_step_count") != len(steps):
            raise rk.Refused(f"the household {rk.show(hid)} was enrolled with {rk.num_of(at, 'f_step_count')} steps, and the steps given are {len(steps)}. Nothing was planned. Give the steps of the event's sequence_defined row")
        if rk.num_of(at, "f_step") >= step:
            # On record already: sealed, or waiting in the inbox that was read.
            (already if hid in sealed and rk.num_of(sealed[hid], "f_step") >= step else waiting).append(hid)
        elif rk.text_of(at, "f_state") != "active":
            ended.append(hid)
        elif rk.num_of(at, "f_step") != step - 1:
            not_next.append(hid)
        else:
            rows.seq("seq-step", target=f"household:{ev.slug}.{hid}", steps=steps_text, step=str(step), subject=rk.text_of(at, "subject"))
            done.append(hid)
    result = {"step": rk.num(step), "on": ev.on, "recorded": done, "already": already, "waiting": waiting, "ended": ended, "not_next": not_next, "not_enrolled": not_enrolled}
    verdict = (
        f"event {ev.slug}, step {step}: {counted(len(ids), 'household')} named; {len(done)} to record, {len(already) + len(waiting)} on record already"
        f" ({len(waiting)} of them waiting), {len(ended)} ended, {len(not_next)} not at this step, {len(not_enrolled)} not enrolled"
    )
    return rows.write(result, verdict)


# ---------- event-answer ----------


def answers_given(rk, ev, flags, cmd):
    """[(household id, answer or None, how many are coming or None, how many attended or
    None)] from --answers, checked against each household's party size."""
    out = []
    items = listed(rk, flags, "answers", cmd) if rk.flag(flags, "answers") is not None else []
    known(rk, ev, [item.get("household") if isinstance(item, dict) else None for item in items])
    for at, item in enumerate(items, 1):
        if any(name not in ("household", "answer", "coming", "attended") for name in item):
            raise rk.Refused(f"--answers: entry {at} may hold household, answer, coming and attended, and nothing else. Nothing was planned")
        said, coming, attended = item.get("answer"), item.get("coming"), item.get("attended")
        size = rk.num_of(ev.households[item["household"]], "f_party_size")
        if said is None and attended is None:
            raise rk.Refused(f"--answers: entry {at} needs an answer ({', '.join(ANSWERS)}) or attended")
        if said is not None and said not in ANSWERS:
            raise rk.Refused(f"--answers: the answer of entry {at} must be one of: {', '.join(ANSWERS)}")
        if coming is not None and (said != "yes" or not whole(rk, coming, 1, size)):
            raise rk.Refused(f"--answers: entry {at}: coming goes with the answer yes, and is a whole number from 1 to the household's party size, {size}. If the household grew, give event-setup its new party_size first. Nothing was planned")
        if attended is not None and not whole(rk, attended, 0, MAX_PARTY):
            raise rk.Refused(f"--answers: entry {at}: attended is how many of the household came, a whole number from 0 to {MAX_PARTY}")
        coming = {"yes": size if coming is None else int(coming), "no": 0}.get(said)
        out.append((item["household"], said, coming, None if attended is None else int(attended)))
    return out


def answer(rk, flags):
    cmd = "event-answer"
    ev = load(rk, flags, cmd)
    rows = Rows(rk, ev, flags, cmd)
    close = "close" in flags
    given = answers_given(rk, ev, flags, cmd)
    if not given and not close:
        raise rk.Refused("event-answer needs --answers, or --close, or both")
    if close:
        # Closing: no reply from every household that is still unanswered.
        named = {hid for hid, _, _, _ in given}
        given += [(hid, "no_reply", None, None) for hid in sorted(ev.enrolled) if hid not in named and hid in ev.households and rk.text_of(ev.enrolled[hid], "f_state") in ("active", "finished")]
    made = {name: [] for name in ANSWERS + ["attended", "already", "ended", "not_enrolled"]}
    for hid, said, coming, attended in given:
        have, at = ev.households[hid], ev.enrolled.get(hid)
        entity = f"household:{ev.slug}.{hid}"
        stands = rk.text_of(at, "f_state") if at is not None else None
        payload = {}
        if said in ("yes", "no") and (rk.text_of(have, "f_rsvp") != said or have.get("f_coming") != rk.num(coming)):
            payload.update({"rsvp": said, "coming": rk.num(coming)})
        if attended is not None and have.get("f_attended") != rk.num(attended):
            payload["attended"] = rk.num(attended)
            made["attended"].append(hid)
        before = len(rows.docs)
        if payload:
            payload.update({"sequence": ev.slug, "on": ev.on})
            rows.record(entity, rk.text_of(have, "subject") or "", payload)
        if said is not None:
            outcome = "replied" if said in ("yes", "no") else said
            if stands in ("active", "finished"):
                rows.seq("seq-end", target=entity, outcome=outcome, step=str(rk.num_of(at, "f_step")), subject=rk.text_of(at, "subject"))
            elif at is None:
                made["not_enrolled"].append(hid)
            elif outcome != "replied" and stands != outcome:
                # Its sequence ended another way before this: the first ending stands.
                made["ended"].append(hid)
                continue
            if len(rows.docs) > before and (stands in ("active", "finished") or "rsvp" in payload):
                made[said].append(hid)
        if len(rows.docs) == before:
            made["already"].append(hid)
    if close:
        have = ev.docs.get(rk.entity_doc("event:" + ev.slug))
        if have is not None and rk.text_of(have, "f_next_step") is not None:
            rows.record("event:" + ev.slug, rk.text_of(have, "subject") or "", {"sequence": ev.slug, "next_step": None, "next_due": None, "closed_on": ev.on})
    result = dict(made, on=ev.on, closed=close)
    verdict = (
        f"event {ev.slug}: {len(made['yes'])} yes, {len(made['no'])} no, {len(made['bounced'])} bounced, {len(made['no_reply'])} no reply and"
        f" {len(made['attended'])} attended to record; {len(made['already'])} on record already, {len(made['ended'])} ended another way before"
    )
    return rows.write(result, verdict)


# ---------- event-status ----------


def tally(rk, households, enrolled, on):
    """The counts for one view of an event, and who is in each."""
    out = {name: [] for name in ANSWERS + ["silent", "other", "not_enrolled", "due", "open", "finished", "attended"]}
    people = coming = came = 0
    for hid in sorted(households):
        have, at = households[hid], enrolled.get(hid)
        stands = rk.text_of(at, "f_state") if at is not None else None
        people += rk.num_of(have, "f_party_size")
        said = rk.text_of(have, "f_rsvp")
        if said == "yes":
            coming += rk.num_of(have, "f_coming")
            out["yes"].append({"household": hid, "coming": rk.num(rk.num_of(have, "f_coming"))})
        elif said == "no":
            out["no"].append(hid)
        elif at is None:
            out["not_enrolled"].append(hid)
        elif stands in ("bounced", "no_reply"):
            out[stands].append(hid)
        elif stands not in ("active", "finished"):
            # Ended by hand some other way, with no answer kept on the household.
            out["other"].append(hid)
        else:
            out["silent"].append(hid)
            due = rk.text_of(at, "f_next_due")
            if stands == "finished" or due is None:
                out["finished"].append(hid)
            else:
                out["due" if due <= on else "open"].append({"household": hid, "step": rk.num(rk.num_of(at, "f_step") + 1), "due": due})
        if rk.num_of(have, "f_attended") > 0:
            came += rk.num_of(have, "f_attended")
            out["attended"].append({"household": hid, "people": rk.num(rk.num_of(have, "f_attended"))})
    counts = {name: rk.num(len(out[name])) for name in out}
    counts.update({"households": rk.num(len(households)), "people": rk.num(people), "coming": rk.num(coming), "attended_people": rk.num(came)})
    return counts, out


def status(rk, flags):
    ev = load(rk, flags, "event-status")
    counts, who = tally(rk, ev.households, ev.enrolled, ev.on)
    sealed, _ = tally(rk, ev.sealed[0], ev.sealed[1], ev.on)
    result = {
        "event": ev.slug, "on": ev.on, "now": counts, "sealed": sealed, "waiting_rows": rk.num(ev.waiting), "unusable": ev.unusable, "who": who,
        "labels": {hid: rk.text_of(doc, "subject") or "" for hid, doc in ev.households.items()},
    }
    rk.say(rk.display_json(result))
    n = {name: int(value) for name, value in counts.items()}
    rk.say(
        f"event {ev.slug} on {ev.on}: {counted(n['households'], 'household')}, {counted(n['people'], 'person', 'people')}; {n['yes']} yes ({n['coming']} coming), {n['no']} no,"
        f" {n['bounced']} bounced, {n['no_reply']} no reply, {n['silent']} unanswered ({n['due']} due, {n['open']} not yet due, {n['finished']} with every step done);"
        f" {counted(n['attended'], 'household')} attended ({n['attended_people']} people); {counted(ev.waiting, 'waiting row')} counted, {int(sealed['households'])} households sealed"
    )
    return True
