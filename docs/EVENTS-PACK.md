# The events pack: what was built, what it costs, what is open

A note for review, written with the change on the `events-pack` branch. Nothing here has
been run against a live log: every figure is offline, against a folder standing in for
the database (`test/events.py`). No release was made and no version was changed.

## What it is

The record-keeping half of a hosted event: who was invited, what went out to whom, who
answered, the headcount, who came. Rowkeep still sends nothing and holds no addresses. A
separate skill may send the invitations; it tells Rowkeep afterwards.

- `plugin/skills/rowkeep/ops/EVENTS.md`: the text the skill loads when an event is in play.
- `plugin/skills/rowkeep/scripts/rowkeep_events.py`: four commands, `event-setup`,
  `event-round`, `event-answer`, `event-status`, loaded by `rowkeep.py`.
- `plugin/skills/rowkeep/ops/BRIEF.md`: a new section, "An event's steps".
- `test/events.py` and `test/fixtures/events/`: 40 invented households and nine tests.

## The design

**No new verb and no new field in the registry.** The event is a sequence. A household is
a record made with `record_updated` (`household:<event>.<id>`: a label, `sequence`,
`party_size`, later `rsvp`, `coming`, `attended`), enrolled in that sequence. A round of
sending is one `sequence_step_done` row a household; an answer is `sequence_ended`, plus a
`record_updated` on the household for a yes or a no.

A verb of its own for answers was considered and left out, because the existing verbs
carry the need better. A tally counts rows, so an answer that changes (a yes that becomes
a no, two coming that become one) would need a correcting row to keep a tally right. A
record's current details take the newest value, which is what a headcount wants. The
headcount is therefore one read of the event's records, added up by the script.

**Bulk is in the build and the store, not in the seal.** Each command is given two reads
(the user's inbox, then `ix` where `f_sequence` is the event's id: households and
enrollments both carry that field, so one query returns the whole event). It applies the
waiting rows on top of the sealed state with the code `seal` uses, decides for every
household what is already on record, and writes every missing row and one plan. The
sequence rows come from the function the `seq-*` commands now share (`seq_draft`), so a
row built in bulk is the row built one at a time.

**What is not weakened.**

- Once-only: checked by the command from the two reads, and again by the seal, whose
  marker check is untouched. A round stored twice from stale reads is sealed once: the
  test stores 40 repeats and the seal drops all 40.
- The fingerprint chain: `seal` is unchanged, and so is its limit of one batch a round.
- By file: every value from the host goes in as `--NAME-file`; a command line holds paths,
  a date, a step number and the user's id.

## Measured

`python test/events.py --measure 40`, Windows 11, Python 3.11.5, each command a new
process. "Reads" and "writes" are database calls a session with file exchange would make.
Wall time includes the stand-in's own file copies, which on this machine cost about a
millisecond a file.

| 40 households | Rows | Commands | Reads | Writes | Wall time |
|---|---|---|---|---|---|
| Setup: build and store | 81 | 1 | 2 | 2 | 1.3 s |
| Setup: seal (6 rounds) | 81 | 12 | 19 | 6 | 11.3 s |
| One round: build and store | 40 | 1 | 2 | 1 | 1.9 s |
| One round: seal (4 rounds) | 40 | 8 | 13 | 4 | 6.3 s |
| The same round again | 0 | 1 | 2 | 0 | 0.9 s |
| For comparison: `seq-step` once a household, building only | 40 | 40 | 0 | 0 | 10.8 s |

So a round for 40 households is **on record after 1 command and 1 write** (4 tool calls
with the two reads), where one row at a time was 40 commands, 80 reads for the once-only
check and 40 writes. Sealing it is another 8 commands and 4 writes (25 tool calls),
unchanged from before, because a seal is one batch of at most 50 writes and a sequence
row takes four of them: 12 rows a round.

At 100 households: a round is 1 command, 2 reads and 2 writes to store (4.6 s), and 9
seal rounds (18 commands, 28 reads, 9 writes, 22.6 s). Setup is 201 rows in 5 writes.

## Where SKILL.md conflicts with this use

None of these sentences was changed. One sentence was added to `SKILL.md`, in "Other
procedures", pointing to `ops/EVENTS.md` and saying that file lists where it differs.

1. **"You never send a step. Whatever tools you have, anything going to another person is
   a draft for the user to send."** In a conversation that also holds a skill which sends
   through the host's mail on the host's word, this sentence forbids what that skill
   does. `EVENTS.md` says only that Rowkeep sends nothing. **Decided (owner,
   2026-11-25):** the sentence now reads "Rowkeep sends nothing; a sending skill may, on
   the user's word", and a step a sending skill sent is recorded only once it holds the
   send's message id.
2. **"The step is recorded when the user says, in this conversation, that it went out."**
   When an assistant sent the step itself, it knows from the send's result. `EVENTS.md`
   keeps the host's word as the reason for the row; whether one instruction ("send these
   and record them") may cover both is not said anywhere.
3. **"Store each of these rows with its own `set`, never in a batch with others."** The
   event commands store a round as one batch. `EVENTS.md` states this as an exception and
   gives the replacement for what the rule protected (on a refusal, read again and run
   again; never resend).
4. **"The once-only check": `get` the marker and the inbox document of each row.** The
   event commands make the check for all households from two reads. Stated as an
   exception.
5. **"Four commands build these rows. Never write these rows with `row`."** Seven
   commands now build them, through one function. Stated as an exception.
6. **"When the user only reports that the target replied ... offer to end the sequence
   ... and write neither until they say yes."** For an event, one yes covers a list of
   answers. Consistent with the rule, different from the one-at-a-time flow it describes.
7. **Rule 3 speaks of text read from the log.** A reply read from mail is not stored
   text, but it is someone else's text all the same. `EVENTS.md` treats it as rule 3
   treats stored text: shown to the host, recorded on their yes.
8. **"Anything about a third person beyond their name, employer and role" needs asking,
   and there is "no way to erase one row".** A household's answer and attendance are
   facts about private people, readable by everyone the log is shared with, for good.
   `EVENTS.md` asks for a yes once before an event is set up in a shared log, and says
   these rows are never logged automatically. `PRIVACY.md` does not mention guests.
9. **The description, and the table under "Records", name companies, contacts, deals and
   outreach.** Neither mentions an event or a `household:` record, so the skill is loaded
   for an event only when the user, or another skill, names Rowkeep. The description is
   at 976 of 1024 characters.
10. **"The rowkeep script ...: one readable Python file."** It is now two: `rowkeep.py`
    was 12.8 KB under the 256 KiB a plugin's file may be, and the event commands are 30
    KB. The second file imports nothing and works only through the first. `SECURITY.md`,
    `docs/REVIEW.md` and `plugin/README.md` say so now; this sentence of `SKILL.md` does
    not.
11. **"What is due": `ix` where `f_next_due` is on or before today.** On reminder day
    this returns each silent household as a record of its own. `EVENTS.md` says to give
    them as one line an event and step.
12. **`ops/BRIEF.md`, steps 4 and 5:** three reads and one draft for each enrollment
    due. For 28 households that is some 84 reads and 28 drafts of one message. The new
    section there has the brief read once for the event and write one draft.
13. **Sealing from a chat is 5 rows a round.** A chat can store a round (one write), and
    should not be the session that seals it: 40 rows would be 8 rounds of seven steps.
14. **"A target can go through a given sequence once."** A household whose message
    bounced cannot be enrolled again in the same event; it is added under a new id.

## Open questions

1. **A seal of several batches from one set of reads.** The remaining cost is the seal:
   25 tool calls for a round of 40. One read of the inbox and of every index document
   the rows touch, then four batches in a row, would be about 11. It needs each later
   batch pinned to the version the one before it leaves `ix/head` and the verb's tally
   at, which means relying on a version going up by exactly one a write. A wrong guess
   fails closed (a version conflict, nothing written), but this could not be tested
   offline and was not built.
2. **Does a `set` of an inbox document that already exists overwrite it?** `SKILL.md`
   says such a `set` is refused; the access rules in `setup/capabilities.json` hold no
   rule that would refuse it. If it overwrites, a batch stored from stale reads replaces
   a waiting row with a later copy of the same row, which is harmless but should be
   known. Not verified against the database.
3. **Names.** The record type `household`, the id form `<event>.<id>`, and `sequence` as
   the field that ties a household to its event (chosen because enrollments already
   carry `f_sequence`, so one query returns both).
4. **A yes and a no both end the sequence as `replied`.** Telling them apart in the
   tallies would take two new outcomes, which is a change to the registry's vocabulary.
5. **The event's own record** (`event:<event>`, with the day answers are due as a
   follow-up) is optional and small. Keep it, or leave deadlines to the sending skill?
6. **The second script file,** and whether `rowkeep.py` should shed something instead.
7. **Whether the description should name events,** in the 48 characters left.
8. **CI time.** `test/events.py` takes about two minutes on this Windows machine, nearly
   all of it file opens; it has not been run on the CI image.
