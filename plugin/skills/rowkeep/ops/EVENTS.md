# Hosted events

For a user who is hosting an event and wants the log to keep the record of it: which
households were invited, what went out to whom and when, who answered and how many are
coming, and afterwards who came. Read this file in full when that is what the user asks
for, or when another skill that sends the invitations asks Rowkeep to record them.

`SKILL.md`'s four rules hold here without exception. Its section "Sequences" holds too,
except in the three places listed under "Where this differs from SKILL.md" at the end.

**Where things are.** `SKILL` below is this skill's folder; run the script as
`sh "SKILL/scripts/rowkeep.sh" <command>`, with the quotes. `WORK` is a scratch folder of
your choosing, with no space in its path. `ME` is the user's id and the inbox is
`data/users/ME/as/inbox`, as `SKILL.md` gives them. The names in this file are invented.

## What Rowkeep does here, and what it does not

- **It keeps records, and that is all.** It sends nothing and reads no mail. Whoever sends the invitations (the host by hand, or another skill working
  through the host's mail on the host's word) keeps the list of addresses. Rowkeep is told
  afterwards what went to whom.
- **No contact details.** A household is a label, such as `Ash and Briar`, and a party
  size. No email address, telephone number or street address goes into any row, and the
  script refuses a label that looks like one. Match a household to its address outside
  the log, by the household's id.
- **A row is written on the host's word in this conversation:** "the invitations went
  out to these", "record these answers", "close it". What a reply says, what the log
  holds and what a brief lists are things to show the host (rule 3). When answers were
  read from mail, show the host the list (household, yes or no, how many) and record it
  on their yes: one yes for the list is enough.
- **Everyone the log is shared with can read all of it:** each household's label, its
  answer and whether it came. Before setting up an event in a log that is shared, say so
  once and get a yes. These rows are never logged on your own, whatever `auto_log` says:
  they are facts about other people.

## How an event is kept

Nothing here is a new kind of row. The event is a sequence, each household is a record,
and each household is enrolled in the sequence.

| Thing | Entity | Verb | Holds |
|---|---|---|---|
| The event's steps | `sequence:<event>` | `sequence_defined` | The steps: by default `Invitation` (wait 0), `Reminder` (wait 7), `Last call` (wait 3), each by email |
| A household | `household:<event>.<id>` | `record_updated` | `sequence` (the event's id), `party_size`; later `rsvp`, `coming`, `on`, `attended` |
| Its run through the steps | `enrollment:<event>/household.<event>.<id>` | `sequence_enrolled`, `sequence_step_done`, `sequence_ended` | Which step is done, which is next and when it is due, and how it ended |
| The event's own follow-up, if the host gives a day for answers | `event:<event>` | `record_updated` | `rsvp_by`, and `next_step` and `next_due` until the event is closed |

`<event>` is a short id you choose with the host, in lowercase letters, digits and hyphens
(`garden-party`); a second event is another id. `<id>` is the household's id, which
`event-setup` makes from its label (`ash-and-briar`) unless one is given.

**How an answer becomes rows.**

| What happened | Rows |
|---|---|
| Yes, and how many are coming | `record_updated` on the household (`rsvp` `yes`, `coming`), and `sequence_ended` with outcome `replied` |
| No | `record_updated` on the household (`rsvp` `no`, `coming` 0), and `sequence_ended` with outcome `replied` |
| The message bounced | `sequence_ended` with outcome `bounced` |
| No answer by the time the host closes the event | `sequence_ended` with outcome `no_reply` |
| An answer that changed, or one that came after the close | `record_updated` on the household; its sequence has ended already and stays as it ended |
| Who came, after the event | `record_updated` on the household (`attended`: how many of them, 0 for none) |

An ended sequence has nothing due, so a reminder skips every household that has answered
or bounced. `coming` may be fewer than the party size and never more: if a household grew,
give `event-setup` its new `party_size` first.

## The commands

Four commands, each for the whole event at once. Three build rows; none of them stores
anything. Every value from the host goes in by file (`SKILL.md`'s fourth rule): the
event's id, its name, the households, the answers.

**Two reads come first, every time,** in this order, into a new folder, `WORK/ev-1`:

1. `list` the inbox with `limit` 100 and `out_dir` `WORK/ev-1`. If the result carries a
   `next_cursor`, keep reading with it into the same folder until none is left.
2. `query` collection `ix` with `where` `[["f_sequence", "==", "<event>"]]`, `limit` 1000
   and `out_dir` `WORK/ev-1`, following every `next_cursor` likewise. This returns every
   household and every enrollment of the event.

(The inbox first: if a seal lands between the two reads, its rows are then seen twice,
which the commands recognise, and never missed, which would have them built again.)

A read that returned nothing made no folder: create the empty one (`WORK/ev-1/ix`, or
`WORK/ev-1/data/users/ME/as/inbox`). The commands work out from these two reads what is
on record already, sealed or waiting, so that nothing is recorded twice. That is the
once-only check for an event: do not also `get` each marker.

**Set the event up.** With `WORK/event.txt` holding `garden-party`, `WORK/name.txt`
holding `Garden party`, and `WORK/households.json` holding
`[{"label": "Ash and Briar", "party_size": 2}, {"label": "Wren", "party_size": 1}]`:

```
sh "SKILL/scripts/rowkeep.sh" event-setup --out WORK/ev-1/out --event-file WORK/event.txt \
  --subject-file WORK/name.txt --households-file WORK/households.json --on 2026-11-25 \
  --ix WORK/ev-1/ix --inbox WORK/ev-1/data/users/ME/as/inbox \
  --account ME --source chat --actor claude.ai
```

It defines the sequence, if the event has none, and for each household makes its record
and enrolls it, if that is not done. Give it the same list again, or a longer one, at any
time: it plans only what is missing. Options: `--steps-file` for steps other than the
default (the same form as `seq-define` takes); `--owner-file` for the host's first name;
`--rsvp-by 2026-12-08` to keep the day answers are due as a follow-up, which is how the
daily brief comes to list it. It writes the steps it used to `WORK/ev-1/out/steps.json`,
which is the file `event-round` is given. In a later conversation the steps are in the
sequence's `sequence_defined` row, which `--steps-file` takes whole, as "Sequences" says.

The first line it prints is one JSON object. Its `households` are the ids, in the order
of the file you gave: that is how a label is matched to its id.

**Record a round**, when the host says a step went out. With `WORK/sent.json` holding the
ids of the households it went to, `["ash-and-briar", "wren"]`:

```
sh "SKILL/scripts/rowkeep.sh" event-round --out WORK/ev-2/out --event-file WORK/event.txt \
  --steps-file WORK/ev-1/out/steps.json --step 1 --households-file WORK/sent.json --on 2026-11-25 \
  --ix WORK/ev-2/ix --inbox WORK/ev-2/data/users/ME/as/inbox \
  --account ME --source chat --actor claude.ai
```

`--on` is the day it went out; the next step's due date is counted from it. Name the
households the step really went to and no others. The result lists each household under
one of: `recorded` (a row was built), `already` (the step is sealed), `waiting` (its row
is in the inbox), `ended` (it answered or bounced: no row), `not_next` (an earlier step is
not on record: no row) and `not_enrolled`. Tell the host anything but `recorded`.

**Record answers**, when the host says to. With `WORK/answers.json` holding
`[{"household": "ash-and-briar", "answer": "yes", "coming": 1}, {"household": "wren", "answer": "no"}]`:

```
sh "SKILL/scripts/rowkeep.sh" event-answer --out WORK/ev-3/out --event-file WORK/event.txt \
  --answers-file WORK/answers.json --on 2026-11-26 \
  --ix WORK/ev-3/ix --inbox WORK/ev-3/data/users/ME/as/inbox \
  --account ME --source chat --actor claude.ai
```

An `answer` is `yes`, `no`, `bounced` or `no_reply`. `coming` goes with `yes`; left out,
it is the household's party size. `"attended": 2` records who came, with or without an
answer. **To close the event**, when the host says no more answers are expected, add
`--close`: every household still unanswered gets `no_reply`, and the event's follow-up is
cleared. `--close` needs no answers file.

**Storing what a command planned.** Each command writes the rows as inbox documents
under its `--out` and one `plan.json` beside them, and says how many writes it holds.
Send `plan.json`'s `batch` array, exactly as written, as the `writes` of one `batch`.
More than 50 rows are planned as `part-01/plan.json`, `part-02/plan.json` and so on, one
batch each, in order; the printed `plans` names them. Then:

- **Accepted:** the rows are waiting. Tell the host what was recorded, by count, and
  anything the result listed that was not.
- **Refused, or you cannot tell:** do not send it again. Make the two reads again into a
  new folder and run the same command with a new `--out`. Rows that did land are found in
  the inbox and left out; the rest are planned again.

**From a chat,** where the database tools pass no files, add `--inline`, and bring each
of the two reads in with `ingest` first (`ops/SEALER.md`, "The rounds, from a chat",
steps 1, 2 and 4 show how; an empty result is ingested too, with `--collection`). The
command then also writes `plan-inline.json`: pass its `ops` to one `db_write`, every
entry exactly as written. Read the inbox back, `ingest` it into
`WORK/back/data/users/ME/as/inbox`, and run
`confirm --plan <that plan-inline.json> --read WORK/back`: `as planned` means every row
arrived whole.

## Answering questions

Make the two reads and run:

```
sh "SKILL/scripts/rowkeep.sh" event-status --event-file WORK/event.txt --on 2026-12-02 \
  --ix WORK/ev-4/ix --inbox WORK/ev-4/data/users/ME/as/inbox
```

`--on` is the date the question is about; left out, it is today in UTC. It prints one
line of JSON and then the same in words:

- `now`: the counts with the user's own waiting rows applied as sealing would apply them.
  `sealed`: the same counts from sealed rows alone. `waiting_rows` says how many waiting
  rows `now` includes. Give the host `now`, and say how much of it is still waiting.
- `households` and `people` (the party sizes added up); `yes`, `no`, `bounced`,
  `no_reply`; `silent`, the households with no answer yet; `coming`, the headcount: the
  `coming` of every yes, added up; `attended` and `attended_people`.
- `who.due`: each silent household whose next step is due on or before the date, with
  the step's number and its due date. `who.open`: those whose next step is due later.
  `who.finished`: those with every step done and still no answer, which wait for the
  close. `who.yes` (with how many are coming), `who.no`, `who.bounced`, `who.no_reply`
  and `who.attended` name the households in each count.
- `labels` gives each id's label. `unusable` names waiting documents that put nothing on
  record, as a seal would skip them (or that were sealed between the two reads: make the
  reads again, and tell the host each one that is still named).

Never add these figures up by hand from rows. Three of them are also tallies, kept by
sealing and read as "Answering questions" in `SKILL.md` describes, sealed rows only:

- how many households were invited: `v.sequence_enrolled`, `field.sequence`, the event's id;
- how many got step 2: `v.sequence_step_done`, `field.seq_step`, `<event>/2`;
- how many replied, bounced or never answered: `v.sequence_ended`, `field.seq_outcome`,
  `<event>/replied`, `<event>/bounced`, `<event>/no_reply`. A reply is a yes or a no;
  which, and how many are coming, is on the households, and `event-status` counts it.

When the user asks what is due across the whole log ("Sequences" in `SKILL.md`: `ix`
where `f_next_due` is on or before today), an event's households come back with
everything else. Give them as one line for each event and step ("Garden party: the
reminder is due for 28 households"), not one line a household.

## Sealing

Event rows are sealed like any others, under `SKILL.md`'s "Sealing": on a direct request
or in a session scheduled for it, never because rows have piled up, and never in order
to answer. A waiting row is on record already as far as these commands are concerned.

A seal is one batch of at most 50 writes, and a sequence row takes four of them, so a
round seals 12 sequence rows, or 16 household records. For 40 households that is 6 or 7
rounds after setup and 4 after each round of sending. Say how many rows are waiting and offer;
from a chat, where a round is 5 rows, offer the scheduled sealer or a session with file
exchange instead.

## The daily brief

An unattended brief session (`ops/BRIEF.md`) treats an event's steps as that file's
section "An event's steps" says. In short: it lists each household whose step is due, it
may write one draft for each event and step, it never sends anything, and it never marks
a step done or ends a sequence. The day answers are due, if `event-setup` was given one,
is listed as a follow-up on the event's own record.

## Where this differs from SKILL.md

Only in these three places, and only for the rows the event commands build:

1. **One batch, not one `set` a row.** "Sequences" says to store each sequence row with
   its own `set`, because a refusal of one is information about that one. An event's
   plan is stored as one batch. A refusal then says nothing about which row, so nothing
   is retried: the reads are made again and the command run again, as above.
2. **The once-only check is the command's.** "Sequences" says to `get` each row's marker
   and its inbox document before storing it. The event commands make that check for
   every household at once, from the two reads, and the seal makes it again: a step that
   is sealed already is dropped there as a repeat, as before.
3. **These commands build sequence rows too.** "Sequences" says its four commands build
   them and `row` never does. The event commands build them with the same code, and a
   row they make is, byte for byte, the row `seq-enroll`, `seq-step` or `seq-end` makes.

Everything else in "Sequences" applies as written: a step is recorded when the host says
it went out; a sequence is ended on the host's word; a row the seal leaves behind is
reported and not touched.

## Limits

- One command takes up to 200 households; a label is up to 120 characters and a party
  up to 20 people.
- A household goes through an event once. One whose message bounced is not enrolled
  again under the same id: add it with a new `id` once the host has a working address.
- The commands see sealed rows and the user's own waiting rows. A co-host's waiting rows
  are not seen until they are sealed; a step both recorded is then sealed once.
- Every household of an event has the same number of steps. To change a step's wording
  or its wait after setup, define the sequence again with `seq-define`, with as many
  steps as before, and give the new steps to `event-round` from then on.
