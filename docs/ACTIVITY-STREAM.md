# The activity stream format

This is the format of the database: an append-only stream of rows, each saying that something
happened to something. It is the kind of table an event-sourced system keeps in SQL, carried
into a document store that has no SQL, no triggers and no locks. This file says what is
stored and why; the behaviour described here is what `test/stream.mjs` checks.

## A row

| Field | Meaning | Rule |
|---|---|---|
| `id` | Position in the stream, from 1, no gaps | Given by the sealer |
| `ts` | When it happened, UTC | `2026-11-22T19:05:01+00:00`, seconds |
| `verb` | What happened | Must be in the verb registry |
| `entity` | The thing it happened to | `type:id`, or one lowercase word |
| `subject` | A readable label for the entity | Free text, up to 300 characters |
| `status` | `ok`, `fail`, `pending` or `info` | Defaults to the verb's own |
| `payload` | The details | A JSON object holding at least the verb's required fields |
| `idempotency_key` | Makes a repeat of the same event harmless | Optional text; the same key is stored once |
| `source` | Which surface wrote it | `chat`, `code`, `page`, `system` or `import` |
| `actor` | Who did it | An agent's name, or `human` |
| `account` | Whose account wrote it | The platform's user id for the inbox the row came from |
| `previous` | Fingerprint of the row before it | Empty for row 1 |
| `fingerprint` | SHA-256 of this row | Over the canonical JSON of every field above |

**Canonical JSON** is what Python produces with
`json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`: sorted keys and
no spaces. That choice means a fingerprint can be recomputed in a few lines of any language. So that Python,
JavaScript and anything else all produce the same bytes, payloads are restricted: keys are lowercase
ASCII (`a-z`, digits, `_`), numbers are whole numbers no larger than 2^53 − 1
(9,007,199,254,740,991) either way, and money is whole
cents or a decimal written as text. Nothing else in a payload is restricted.

The script reads JSON the same strict way wherever it reads it. A number is taken for
exactly what its text says or not at all: `1`, `1.0`, `1e2` and `-0` are whole numbers and
are written `1`, `1`, `100` and `0`, as they always were; `1.0000000000000001`, `1e-999` and
`9007199254740993`, which a reader holding numbers as floats would round to a neighbour,
are refused where they are read, so that two different documents cannot share one
fingerprint. A key given twice in one object is refused, and so is a Unicode escape that
is not four hexadecimal digits together.

Each row's `previous` is the fingerprint of the row before it, so changing or removing any
sealed row breaks every fingerprint after it. The store cannot prevent an editor from
overwriting a document; the chain makes it detectable.

## The verb registry

The registry lists every verb. The script takes it from the skill (`registry/verbs.json`,
beside the script, or the file `--registry` names). The log also holds a copy, the document
`state/verbs`, which setup writes for people to read and for a session to recognise a log
by: nothing reads the registry from it, so editing that copy changes nothing. One entry:

```json
{"verbs": {"purchase_approved": {
  "description": "Approved a purchase.",
  "required": ["amount_cents", "currency", "vendor"],
  "status": "ok", "group": "money",
  "tally": ["vendor", "currency"], "sum": ["amount_cents"], "index": ["vendor"]}}}
```

- `required`: payload fields a row must carry.
- `tally`: payload fields whose values are counted.
- `sum`: whole-number payload fields that are totalled.
- `index`: payload fields copied to the top of the stored document as `p_<field>`, because
  the database can only filter on top-level fields.

- `current`: payload fields kept on the entity as its current details (`["*"]`: every plain
  one). See "Records" below.
- `names`: payload fields holding another name for the entity.
- `dates`: payload fields that hold a calendar date, written `2026-11-27` (added in version
  0.7.0; a registry without it is read as before).

A list that names a field twice is refused: a field listed twice under `sum` would be added
twice to every total.

**What a new row may hold in these fields** is checked when the row is made and again when
it is sealed. A field under `sum`, when the row carries it, is a whole number. A field under
`tally` is text, or true or false; a number is refused (write it as text), and so are the
texts `true` and `false` and the text `(other)`, the name the tally keeps for everything past
its 200th value, so that each bucket of a tally holds one kind of value. A field under
`dates` is a real date from 1970 to 9999, or null. Rows sealed before version 0.7.0 were not
held to these, and are recounted as they were counted: there, an amount written as text
counts the row and adds nothing, and the value true and the text `true` share a bucket.

`tally`, `sum` and `index` are how this store answers what `GROUP BY`, `SUM` and `WHERE`
would answer in SQL: the counting is done as each row is sealed, and kept.

## Where things are kept

| Collection | Document | Holds |
|---|---|---|
| `data/users/<account>/as/inbox` | one per unsealed row | A row without `id`, `account`, `previous`, `fingerprint`, plus `at` and `ck` (below). Anyone's Claude can add one, from any surface |
| `stream` | `0000000042` | A sealed row, plus its `p_` fields |
| `ix` | `head` | The tip of the chain: last `id`, its `fingerprint`, row count |
| `ix` | `v.<verb>` | Tallies for one verb: count, by status, by source, by actor, by day, each `tally` field by value, each `sum` field's total |
| `ix` | `e.<hash>` | Latest state of one entity: type, row count, first and last id, last verb, status, subject, its names, and its current details as `f_<field>` |
| `ix` | `i.<hash>` | Marks an idempotency key as used, and by which row. The hash covers the verb, the entity and the key, so a key belongs to one verb on one entity |
| `state` | `verbs` | The registry, plus `ck` when setup wrote it with version 0.5.0 or later |
| `members` | `<user id>` | `{"member": true, "auto_log": ..., "announce": ...}`: one per person on the log, holding that person's own choices about logging (see "Sharing one log between people") |
| `brief` | `<date>` | The daily brief, if a session is scheduled to write one: `{date, made_at, tip, sealed, waiting, note, items}`. `tip` is the `id` of `ix/head` when it was worked out; each item names its `entity`, the record's `next_step` and, for a sequence step, the `step` number and the `definition_id` (the `sequence_defined` row) it was worked out from. The log's page shows an item only while all of those still hold |

`state/verbs` and the user's own `members` document are the log's first documents. `rowkeep first-docs` writes them for setup
(below). A log set up before version 0.7.0 also holds `state/settings`, which the script
no longer writes (see "Versions").

Every `ix` document carries its own id in a field `k`, so one query
(`where k in [...]`) fetches the tip, the verb tallies and the entity states a seal needs.

## The checksum field, `ck`

Some sessions cannot pass a file to or from the database: a chat reads a document as text in
the conversation and writes one by putting it in the call. Every document such a session has
to read back is therefore carried by hand, and one miscopied character in a tally would
otherwise be built on by every later seal. So every inbox document and every `ix` document
holds one more field, and from version 0.5.0 so do the vault's documents (`rows/<id>` and
`vault/head`) and the two documents setup writes under `state`:

- `ck` is the first 12 hexadecimal characters, in lowercase, of the SHA-256 of the
  document's canonical JSON, taken without `ck` itself. It covers every other field, `k`
  and `at` included. Anything else under that name (null, a number, capitals, eleven
  characters) is a wrong checksum, not a missing one; so is the `ck` of a document whose
  canonical JSON cannot be written at all.
- **Sealed rows hold none and are unchanged.** A row has its fingerprint, and its bytes are
  the format. Sealing drops `ck` from an inbox document as it drops `at`, so a row sealed
  from a document with a checksum is byte for byte the row sealed from one without.
- These commands recompute `ck`, and nothing else does (a read of the database verifies
  nothing by itself, and the two pages do not look at `ck`): `ingest`, for every document
  it writes, listing a mismatch as `damaged`; `seal-keys` and `seal`, for every inbox
  document and every index document they are given; `confirm`, for the planned document
  and for the one read back; `check` and `rebuild`, for every index document; `match` and
  the sequence commands, for a document given to them; `vault-plan`, `vault-check` and
  `vault-restore`, for the vault's documents; and any command given `state/verbs` as its
  registry. A waiting row whose checksum is wrong is skipped, with the reason, like any
  other invalid inbox document, and stays where it is. An index document whose checksum is
  wrong stops the seal, by name, before anything is planned. `check` reports it as a
  problem. `rebuild` rewrites it. `match` and the sequence commands refuse it.
  (`ingest` also records the SHA-256 of each file it writes, in the read manifest: see
  below. That ties a file to a read; `ck` ties a document to its writer.)
- A document that holds no `ck` was written before version 0.4.0 (a vault or `state`
  document: before 0.5.0) and is taken as it is.
  `check` and `rebuild` do not count a missing checksum as a difference, so an older log
  checks clean, and its index documents gain checksums as seals rewrite them.
  `rebuild --checksums` rewrites the rest in one go. Vault documents are never rewritten:
  an older chunk of rows stays without a checksum, which costs nothing, because the rows in
  it are checked by their fingerprints; the tip gains one at the next copy.
- When a seal is given `--inline` (below), which says the documents were carried by hand, it
  takes only documents that hold a checksum: a waiting row without one is skipped, and an
  index document without one stops the seal.

The checksum is not a signature. It has no secret in it, and anyone who can write a document
can write a matching `ck`. It catches accidents of copying, which is all it is for.

Exactly what a matching `ck` shows: that the document's own contents are, to within 48
bits, the contents its writer hashed. It says nothing about the document's path (an index
document also names itself in `k`, which is what ties it to its name), nothing about its
version, nothing about whether it is still the current document, and nothing about whether
the read it came from was complete. Those are settled by other means: the version by the
database when a pinned write is sent; which read a file is of, and whether that read was
whole, by the manifest `ingest` writes (below). Nothing a script can do shows that a read
was current.

## Writing: inbox, then seal

1. **Append** (any surface, two calls). `rowkeep row ...` checks the row against the registry,
   drops payload fields named like credentials and masks text that has the shape of one (a
   net, not a guarantee; see [SECURITY.md](../SECURITY.md)), and writes one small file; that
   file is stored in the writer's inbox. With an idempotency key, the document id is derived
   from the verb, the entity and the key, so a second copy is refused by the store while the
   first waits. (The store refuses to overwrite an existing document unless the write names
   the version it read. This was observed on claude.ai in November 2026 and is what
   `test/mockdb.mjs` imitates; it is not documented behaviour.)
2. **Seal** (the single writer). The sealer takes inbox rows oldest first (by `ts`; then by
   `at`, the millisecond the row was written; then, for two rows written in the same
   millisecond, in the order of their document ids, which is arbitrary and always the
   same) and, for each, gives it the next `id`, the tip's fingerprint as `previous`, and its own `fingerprint`; then
   updates the tip, the verb tally and the entity state. Rows, tallies and inbox deletions go
   in one batch, pinned to the versions that were read. If anyone else sealed in between, the
   batch is refused whole and nothing changes. That is what stands in for a write lock: there
   is no moment at which two writers can both extend the same tip.

Tallies are therefore always exact for sealed rows. Unsealed rows are few and can be counted
directly.

A row is skipped, with the reason, and stays in the inbox when it is not valid (its file is
not named like a document, its `at` is not a whole number, it does not pass the registry),
when it would take a total past 2^53 − 1, when it would make a tally or an entity state
larger than 200,000 bytes (the database takes 256 KiB; see "A document over the size
budget" below), or when it is a sequence row that is not a valid next move (see
"Sequences"). Only the totals a row moves are looked at: the verb's own, and the one bucket
of each counted field the row is counted in. If documents were waiting and none of them can
be sealed (none is a valid row, or none of the rows a round took up fits the index as it
stands), `seal` prints `NO PROGRESS`, plans nothing and exits 1; an inbox folder that holds
nothing ends `nothing to seal`, exit 0. `--leave ID,...`, given to `seal-keys` and to
`seal`, passes over the named inbox documents so that the rows behind them are sealed; an
id that is not in the folder is refused.

The last line `seal` prints states the outcome: how many rows are planned and under which
ids, and how many were dropped as repeats, skipped, left as `--leave` asked, and remain for
another round. `plan.json` holds the same as `sealed`, `first_id`, `last_id`, `duplicates`,
`skipped`, `left` and `remaining`. Every command that plans ends the same way: the outcome
on the last line, with any override in force (`REWIND`, `PARTIAL RESTORE`), and the same
facts in the plan (`"override": "rewind"`, `"partial"` or null).

### A document over the size budget

No plan makes an index document larger than 200,000 bytes. A log sealed by version 0.6.0
or earlier may hold a tally that is over already (a verb that counts many long values).
Such a log is carried on as it is: `check` recounts it as before; `rebuild` leaves a
document alone when it is right, whatever its size, and where one that is over must be
rewritten it writes it at its size or below, with a line starting `note:`, and refuses
only a rewrite that would make it larger; `seal` seals a row that leaves the document its
size (a value it counts already), with the same note, and skips, with the reason, a row
that would make it larger. Rows with new values of that verb's counted fields therefore
stay in the inbox: to go on logging them, register a new verb for them, with fewer counted
fields or shorter values.

### Sealing without file exchange

A session whose database tool saves reads to files and writes from files never copies a
document itself. A chat has neither, and seals with two additions to the same procedure:

- **`rowkeep ingest --from FILE --out DIR`** takes one read result, saved to FILE as the text
  it came back as, and writes each document's contents as bare JSON to `DIR/<document id>.json`,
  which is what a read that saves files leaves. It accepts four layouts and no other: the
  one a chat's read tools return (the tool's own object, holding a notice, `count` and a
  cursor, then a tag with one document on each line inside it); one object holding the
  documents as a list under `docs` (or `documents`), with its cursor; a JSON array of
  documents; or one document alone. Each document is an object with its id under `id` or
  `doc_id` or as the last part of `path`, its contents under `data`, and its version under
  `version` or `ifVersion`; where it is named, or its version given, more than once, the
  names must agree, and an entry in which they do not is refused. With `--id` the file
  holds that one document: the tool's wrapper around it (`data` beside nothing but an id,
  a version, `exists`, `updatedAt` or `createdAt`), or its bare contents, of which no
  field is dropped, or `{"exists": false}`, a read that found no such document. A `count`,
  wherever the result gives one, must be the number of documents in it. Anything else is
  refused whole: words around the JSON, an array without its closing bracket, an object
  with two lists, a line inside the tag that is not one whole document. Lines an app puts
  above the tool's output are set aside only if there are at most four and none of them
  could be part of a document (no bracket, no `"name":`, no comma at its end). The
  collection must be known, from the documents' paths or from `--collection`, which an
  empty result always needs. It prints one line: how many
  documents it wrote, each one it `refused` and why, each one that is `damaged` (its `ck`
  does not match), how many are `unchecked` (they hold no `ck`), `versions`, a text such
  as `head=7,v.note_added=3` ready to give to `seal`, so that no version is typed,
  `complete`, the `collection` and the `page`. The file is untrusted: its size is looked
  at before it is read, the usual limit on nesting applies, a document's id becomes a file
  name only if it is made of letters, digits, `_`, `.` and `-` and does not end in a dot,
  no entry of an id that appears twice is used (whatever else is wrong with one of them),
  and DIR must be new or empty. A damaged document is still written, so that the command
  that reads it next names it and refuses it.
- **A read in pages.** A result that ends with a cursor is one page. The next page is read
  with that cursor and ingested into the same DIR with `--page 2`, the one after with
  `--page 3`: a later page is taken only into a folder that holds the pages before it, of
  the same collection, the last of them ending with a cursor, and it may not repeat a
  document. A read is complete only from page 1 to a page with an empty cursor; a last
  page ingested alone cannot be given as page 2 of nothing. (What `ingest` cannot know is
  whether a result given to it without `--page` really was the first page: that rests on
  the session saying so.)
- **The read manifest.** Beside the documents `ingest` writes `DIR/_read.manifest`, and
  for each later page `DIR/_read.<N>.manifest`. One is canonical JSON on one line:

  ```json
  {"absent": null, "ck": "0123456789ab", "collection": "ix", "complete": true,
   "declared": 2, "documents": [{"id": "head", "sha256": "<64 hex>", "version": 7},
   {"id": "v.note_added", "sha256": "<64 hex>", "version": 3}], "more": false,
   "page": 1, "rowkeep_read": 2, "why": ""}
  ```

  `collection` is always there. `documents` lists each file written: its id, the version
  it was read at (null where the result gave none) and the SHA-256 of the file's bytes,
  which are the canonical JSON of the document's contents. `declared` is the count the
  result gave, or null. `complete` says whether the folder now holds all there was to
  read: true only of a result that carries an empty cursor, had none of its documents
  refused, and is page 1 or follows the pages before it; `why` says why not. `more` is
  true where the page ended with a cursor and a next page may follow. `absent` names the
  one document a read with `--id` looked for and did not find. `rowkeep_read` is 2; a
  manifest written by version 0.7.0 has 1 and no digests. `ck` is the manifest's own
  checksum.

  Every command that reads a folder holding a manifest checks the folder against it
  first: each file listed must be there under exactly that name with exactly those bytes,
  and no other `.json` file may be there. A difference is refused, naming the document,
  with nothing planned: read again into a new folder. A manifest from version 0.7.0 is
  refused the same way, since it ties no file to the read. A folder with no manifest is
  one a tool saved files into, and is read as before.

  `seal --inline` needs both its folders to hold one: `--inbox` from a read of exactly the
  inbox of `--account` (or of `--inbox-collection`), and `--ix` from a complete read of
  collection `ix`, an empty result included (a tally lost on the way would otherwise be
  taken for one that does not exist yet). It takes the versions from them.
  `vault-plan --inline` takes the tip's version from one, which must be of collection
  `vault`. `confirm` goes by them for removals.

  What a manifest shows: that the files in a folder are the documents of one read of one
  named collection, with these ids, versions and contents, and whether that read said it
  was whole. What it cannot show: that the read was current, that a query was not
  narrowed by a filter, that a result given without `--page` was the first page, or that
  the text saved was the text the tool returned.
- **`rowkeep seal --inline`** writes, besides `plan.json`, a file `plan-inline.json` holding
  the same writes in the same order with each document inside its entry:
  `{"op": "set", "path": "ix/head", "data": {...}, "ifVersion": 7}`, or
  `{"op": "delete", "path": "data/users/<account>/as/inbox/<id>", "ifVersion": 1}`. That is
  the form a chat's write tool takes, and the `ops` array is sent as one write. The plan
  also holds `after`: the `id` and `fingerprint` that `ix/head` must hold once the write has
  been applied. The session reads `ix/head` back and compares.
- **`rowkeep confirm --plan FILE --read DIR`** compares what a write left in the database
  with what was planned. FILE is a `plan-inline.json`; DIR holds the collections the plan
  wrote to, each read back and passed through `ingest` into `DIR/<collection>`, so that a
  document sits at `DIR/<its path>.json`. Every document the plan set must be there and
  equal to the plan's, as canonical JSON, and each of the two must match its own `ck`. A
  document the plan removed counts as gone only where the folder holds the manifest of a
  complete read of exactly its collection, or of a read of that one document (`--id`, with
  `--collection`) that found none (`{"exists": false}`); a folder that is empty, or
  missing, or filled by hand, or holds a read of another collection, shows nothing. Each
  folder and file on the way from DIR to a document is taken from the folder's own
  listing by its exact name, so `ix/A.json` is never found for `ix/a`, whatever the file
  system. A plan that holds no `ops` is refused: there is nothing to call as planned. It
  ends `as planned`; or lists each difference on a line starting
  `PROBLEM` and exits 1; or, when nothing differs and a removal could not be checked,
  lists each on a line starting `NOT CONFIRMED`, ends `not confirmed` and exits 1. A
  document that differs and no longer matches its own `ck` was miscopied in the write or in
  reading it back; one that differs and still matches its `ck` was written by someone else.
  The vault copy and setup use it after every write a chat makes; it reads any
  `plan-inline.json`, a seal's included.

What this does and does not catch is set out in [SECURITY.md](../SECURITY.md).

## Reading without SQL

| The question | How it is answered |
|---|---|
| How many of each verb, by source, by status, by day? | Read `ix/v.<verb>`: one small document |
| How many rows in all, and is the chain intact up to where? | Read `ix/head` |
| Which vendor got the most approvals, and the total spend? | The `tally` and `sum` sections of `ix/v.purchase_approved` |
| What is the latest on one entity? What is still pending? | Read `ix/e.<hash>`, or query `ix` on `status` |
| The rows for one entity, verb, actor or indexed field | Query `stream` on that top-level field |
| Anything else | The viewer page scans the newest rows in the browser; beyond that, read the rows and compute |

## Records: current details and names

A stream says what happened. A CRM, or any workflow built on records, also needs to say how
things stand now and which record a name refers to. Both are kept on the entity's state
document as rows are sealed, so they cost no extra documents and no extra reads at seal time.

**Current details.** A verb's `current` list names payload fields that describe the entity
rather than the event: a deal's `stage`, an account's `owner`. Each is copied to the state
document as `f_<field>`. The newest value wins, a `null` clears the field, and fields a row
does not mention are left alone. Only text, whole numbers and true/false are kept (text is
cut at 300 characters), up to 60 fields per entity. On a record that is full, a row's
clears and its new values for fields the record has are applied first, and then the
fields the record did not have, in name order, while there is room; `seal` names any that
found none. Because they are top-level fields, the
database can filter on them: every deal at one stage, everything due by a date.

**Names.** The entity's first label is its name; a verb's `names` fields add more (the
starter registry has `alias_added`). Up to twelve are kept in `names`, each with a matching
key in `nks`. The key is the name in lowercase with accents and punctuation removed, `&` read
as `and`, a leading `the` dropped and trailing legal forms dropped (`inc`, `corp`, `llc`,
`ltd`, `gmbh` and the like): "ALDER EXAMPLE Corp." and "The Alder Example Company" both
give `alder example`. A name is
put in composed form (NFC) first, so that an accent typed as a letter and a combining mark
gives the key the composed letter gives, and the Greek sigma that ends a word is the sigma
inside one. One query,
`nks array-contains "alder example"`, finds every record that goes by a name.

**Which record a name means.** `rowkeep match` turns a name into its key, and given the state
documents of that type, scores them: 100 for the same key, 90 where one name's words contain
the other's, 88 for initials, otherwise the Jaro-Winkler similarity of the keys. The verdict
is `use` only when exactly one record has the same key. Anything less is `ask`, a question for
a person, whose answer is then logged as an alias so the question is not asked twice. A
record is matched by its stored keys (`nks`), which are what the query finds it by, so the
two give the same answer. A merge is followed only to a record that is among those given,
of the same type, and not round in a circle; otherwise the verdict is `ask`, with the
reason. A candidate that is a row still waiting in an inbox must be a row `seal` would
take; one that is not is listed as `ignored`, and the verdict is then `ask`. So is
anything given as a candidate that names no entity (the tip, tallies and markers of a
folder saved from `ix` aside): `new` is said only of candidates that could be read, and a
folder named with `--ix` that does not exist is an error. Two
records with the same key are reported as a possible duplicate, and `entity_merged` marks one
as standing for the other. Nothing here rewrites history: an alias and a merge are rows like
any other, and the scoring can be replaced without touching what is stored.

Both are derived from the rows alone, so `check` recounts them and `rebuild` restores them.

## Sequences

A sequence is steps in order, each some days after the one before, that many targets are
taken through: an outreach cadence, a proposal chase, a renewal run-up. It needs nothing new
in the store, only four verbs and the commands that build their rows:

| Row | Entity | What it carries |
|---|---|---|
| `sequence_defined` | `sequence:<id>` | `steps`: `[{channel, wait_days, what}]`. The newest one is in force |
| `sequence_enrolled` | `enrollment:<id>/<target type>.<target id>` | step 0, the first step and its due date, state `active` |
| `sequence_step_done` | the same enrollment | the step number, the next step and its due date; `finished` after the last |
| `sequence_ended` | the same enrollment | the outcome and the step it came after; nothing more is due |

The enrollment's current details (`f_step`, `f_next_step`, `f_next_due`, `f_state`) are the
state machine, so "what is due today" is one filtered read of `ix`. The tallies count how many
targets reached each step and which step drew each outcome, so conversion by step needs no
pass over the rows.

Each of these rows has an idempotency key built from the sequence, the target and the step,
which is what makes a step happen once. While the row waits, a second copy is refused by the
store. Once it is sealed, its marker document `ix/i.<hash>` exists, and a session checks for
it before recording the step again. The skill never sends a step: the assistant prepares a
draft, a person sends it, and the row is written when that person says it went out. The
refusal by the store protects one inbox only. Two people can each write a row for the same
step in their own inboxes before either is sealed; the seal then takes the first and drops
the second as a repeat of a key that is sealed already, as it drops a repeat of any other
verb: it is listed under `duplicates` and removed from the inbox. Until the log
has a claim that is shared and a receipt of delivery, a row is not a safe thing to send on.

The store has no triggers. A sequence advances when a person says a step was done. What is
due is worked out when a session asks: a person, or a session run on a schedule, which
lists and drafts and records nothing.

A row of one of the four verbs must be a row the commands would have made: its entity, its
step, its tally labels and its key are worked out again from its sequence and target when
it is checked, and the outcome of an ending is one of `replied`, `booked`, `stopped`,
`bounced`, `unsubscribed`, `no_reply`. At `seal` it must also be a valid next move from
where the enrollment stands: an enrollment for a target not yet enrolled; step N while
the state is `active` and N - 1 steps are done; an ending while the state is `active` or
`finished`, after the number of steps that are done. A step must also agree with the
sequence the target was enrolled in: the enrollment records how many steps that sequence
had (`f_step_count`; where an older enrollment holds none, the definition's count is used
if its record was read), and a step finishes the enrollment after that many and only then,
and otherwise names the next of that many. So a step built from another list of steps
cannot end an enrollment early or run it on past its end; an enrollment is finished with
the steps it was started with, and a revised definition is for targets enrolled after it.
(Only the count is in the index: a step built from a list of the same length with other
waits is not told apart.) A row that is not a valid move is skipped, with
the reason, and stays in the inbox, so a step that arrives after the sequence ended does
not set it going again. Rows sealed before version 0.7.0 are replayed as they stand.

Rows written in the same second are sealed in the order they were written: each inbox
document carries `at`, the time it was written in milliseconds, which is used only for that
and is not part of the sealed row.

## The vault

A log lives in one artifact, and an artifact can be deleted or damaged. The vault is a second
artifact that holds a copy of every row that had been sealed as of its last completed copy,
in documents of up to 200 rows, plus one tip document. Because sealed rows never change, a
copy only ever adds documents of rows, and none of those is rewritten. The tip,
`vault/head`, is rewritten at each copy: it is the one document of a vault that changes. The index is not copied: `rebuild` derives all of it from the rows.

A copy first checks that the log's row at the vault's tip is still the row the vault holds,
and that the new rows chain on from it. If not, it refuses and raises an alarm: the log has
been changed. `vault-check` compares every row of the log against the vault's copy. So the
vault is also a witness: to alter history unnoticed, both artifacts would have to be changed,
and on a shared log the vault is not shared. The tip is what a check is compared against.
It is not itself evidence that the chain is intact: it says where the last copy ended, and
only a check that replays the rows (`vault-check`) says whether they agree with it.

A restore writes the vault's rows into a new, empty artifact and rebuilds the index. It is
given the vault's tip as well as its rows, because only the tip says how many rows there
are: one chunk of two is a chain from row 1 with nothing wrong in it. Rows that stop short
of the tip are refused; `--partial` restores them all the same and says which rows, and
that the vault held more. The
result holds the sealed rows the vault held, row for row, and an index derived afresh from
them by `rebuild`. That index is what a recount of those rows gives, not a copy of the lost
log's index documents, which the vault never held. Rows that were still waiting in an inbox
are not in the vault and do not come back.
The procedures are in [the skill's `ops/VAULT.md`](../plugin/skills/rowkeep/ops/VAULT.md).

### The vault's documents

- `rows/<10-digit first id>`: `{k, first, last, n, rows, ck}`. `k` is `c.` plus the
  document's id, `rows` the sealed rows from `first` to `last` without their `p_` fields.
- `vault/head`: `{k: "head", id, fingerprint, n, ts, source, copied_at, ck}`. `source` is
  the link of the log the vault copies, as `--source` gave it. From version 0.7.1
  `vault-plan` refuses a tip whose `source` is another link (one slash at the end aside),
  so that a vault found by its title is not filled from the wrong log; `--moved` says the
  log was restored to a new link, and the tip names that link from then on. An empty vault
  has no tip and nothing to compare, which is why the skill asks before a first copy. `n` is how
  many rows the vault holds. A vault holds every row from 1, so `n` always equals `id`.
  Every command that is given a tip holds it to that, whatever the mode and whether or
  not it carries `ck`: `k` is `head`, `id` is a whole number from 1, `n` equals `id`, and
  `fingerprint` is 64 hexadecimal digits. `vault-plan` and `vault-restore` refuse anything
  else; `vault-check` reports it.

`ck` is the checksum described above, added in version 0.5.0. `vault-plan` refuses a tip
whose `ck` is wrong; `vault-check` reports a wrong `ck` on either kind of document as a
problem; `vault-restore` refuses to restore from a document whose `ck` is wrong. All three
take a document with no `ck` as it is. `vault-check` and `vault-restore` also hold a chunk
to its name: `rows/<id>` must start at row `<id>` and name itself `c.<id>`.

Every command that is given a folder of rows (`check`, `rebuild`, `vault-plan`,
`vault-check`, `vault-restore`) reads it the same strict way: each row an object, in the
file its id names, following the row before it by one, pointing at that row's fingerprint,
and carrying the fingerprint of its own contents. `vault-check --stream` holds the log's
rows the vault does not have yet to the same, and `vault-plan` every row it is given,
those before the vault's tip included. What `vault-plan` compares with the vault is one
row, the row at the vault's tip, and it says so; `vault-check` compares the rest.

`vault-check --partial --stream DIR` is given only part of the log. The first row given
has no row before it in the folder, so it is anchored to the vault: it must be a row the
vault holds (and is compared with it), or the row after one the vault holds, whose
fingerprint its `previous` must be. Rows the vault holds neither of nor the one before
cannot be anchored, and that is a problem, not a pass. A clean partial run has its own
last words, `partial check: the vault's rows and tip hold together, and rows A to B of the
log agree with them; no other row of the log was compared`, and never `vault intact`.

### Copying without file exchange

A chat copies to the vault as it seals: read results saved as text and turned into files by
`ingest`, and a plan whose entries it sends itself.

- **`rowkeep vault-plan --inline`** writes, besides `plan.json`, a file `plan-inline.json`:
  `{copied, first_id, last_id, remaining, after, ops}`. `ops` is two entries in the form a
  chat's write tool takes, to be sent to the vault as one write: a `set` of one new `rows`
  document, and a `set` of `vault/head` with `ifVersion`, the version that was read (no
  `ifVersion` when the vault was empty). `after` is what `vault/head` must then hold:
  `{path, id, fingerprint, n, ck}`. `remaining` counts the rows that were given and not
  planned. `--versions head=N`, as `ingest` prints it, is accepted in place of
  `--head-version N`.
- **What is verified first.** The rows reached the folder by hand and hold no checksum, so
  they are verified by what they are: the row at the vault's tip must carry the fingerprint
  the vault holds and that fingerprint must be the one its contents give; each new row must
  have the next id, point at the fingerprint of the row before it, and carry the fingerprint
  of its own contents. One changed character in any of them breaks one of these, and the
  copy is refused with `ALARM` before anything is planned. The command prints what it
  checked. The tip is verified by its `ck`. A tip with no `ck`, from an older vault, is
  held to what every tip is (above); its `id` and `fingerprint` are verified against
  the log's row in any case.
- **One round is one small document.** With `--inline` a plan takes at most 10 rows and
  6,000 bytes of rows (`--max` changes the first; a single longer row still goes, alone).
  A chat carries every copied row three times: in, out, and back in to confirm. The limit
  is set by the write, the one leg that is stored as typed. Sealing five rows from a chat
  sends about 6,000 characters in one write, which is the size that has been done in a real
  chat; ten ordinary rows are about 4,500. With the read before it and the read back after
  it, a round of ten rows carries about 16,000 characters, against about 8,000 for a round
  of sealing; a slip in either read costs only a retry. More rows take more rounds. A chat's vault therefore holds more, smaller `rows` documents
  than one written from files; the rows in them, and what a restore gives, are the same.
- **Afterwards** the session reads both documents back and runs `confirm` (above), which is
  what catches a slip in the write itself.

## Setup's first documents

`rowkeep first-docs --out DIR --account USER_ID [--auto-log yes|no] [--announce yes|no] [--inline]`
writes the two documents a new log starts with, and the one write that stores them:
`state/verbs` (the registry file as it is, plus `ck`) and the user's own member document,
`members/<USER_ID>`, which holds `{"member": true, "auto_log": <true or false>,
"announce": <true or false>}` (`auto_log` is false and `announce` true unless given), with
no `ck`. `plan.json` holds the `batch` for a session with file exchange. With `--inline`,
`plan-inline.json` holds `{documents, auto_log, announce, after, ops}`: `ops` is sent as
one write, and `after` lists the `ck` of each document that holds one. Both leave the same
two documents. `ck` on `state/verbs` is verified by `ingest` and `confirm`, when a session
reads it back by hand, and by any command given that document as its registry.

**Whose choice automatic logging is.** Whether rows are logged without asking
(`auto_log`) and whether each is announced (`announce`) is each person's own choice, kept
in that person's own member document. From version 0.7.1 the log's rules let nobody else
write it, the owner included (see "Sharing one log between people" for what that rests on).
The two fields are strict booleans. A member document that holds only
`{"member": true}`, as every log made before version 0.7.0 does, means the defaults:
`auto_log` false, `announce` true.

## Sharing one log between people

Each person has their own inbox, and the path is what proves who wrote a row: the database's
access rules let a person write under `data/users/<their own id>` and nowhere else. When a
row is sealed, the id of the inbox it came from becomes its `account`.

`members/<user id>` is the list of people on the log. Each person's Claude adds its own entry
the first time it logs (again, only its own: the rules refuse any other id). A sealer reads
the list to find the inboxes. The entry also holds that person's choices about logging
(see "Setup's first documents").

The rules, declared when the page is published:

| Path | Read | Write |
|---|---|---|
| everything, unless listed below | anyone the log is shared with | owner and editors |
| `data/users` (every inbox) | owner and editors | owner and editors |
| `data/users/<own id>` | that person | that person, if a contributor or above |
| `members` | anyone the log is shared with | nobody: the level `self`, which no one holds (owner and editors, on a log made before version 0.7.1) |
| `members/<own id>` | anyone the log is shared with | that person, if a contributor or above |

So a contributor can append and can read the stream, and cannot seal, rewrite a tally, or
write as anyone else. Sealing is for the owner and editors, or a scheduled session running as
one of them (see [the skill's `ops/SEALER.md`](../plugin/skills/rowkeep/ops/SEALER.md)).

What this does and does not protect against is set out in [SECURITY.md](../SECURITY.md). In
short: the rules stop a contributor from sealing, rewriting the index or writing as someone
else. They bind the owner and an editor in one place only, another person's member
document, and not for good: either can republish the page with looser rules. That the
level `self` refuses the owner is the platform's rule, seen once on a one-person test log
and not yet with two accounts. The fingerprint chain has no secret in it, so an editor who changes
a sealed row can also recompute every fingerprint after it, and `check` on the log alone
would then pass. What catches that is the vault: a copy the editor cannot reach, whose tip
no longer matches.

## Repairing the index

The rows are the record; the tallies and entity states are derived from them. `rowkeep rebuild`
recomputes every index document from the rows and plans a write of only the ones that differ,
or whose checksum is wrong, and the removal of any other document in collection `ix`,
whatever its name. With `--checksums` it also rewrites the ones that hold no checksum.

Because the index is derived from whatever rows are given, the rows are verified first,
by the same replay `check` runs, and whatever `check` would report of a row is refused: a
rebuild from a row whose contents were changed would write the change into every count.
That includes a row whose filterable copies (`p_` fields) do not match its payload:
`rebuild` writes only the index and cannot put a row right. Every file of a plan is looked
at before any is written, the batch files included, and the plan is written last. If the
index it is given puts the tip past the last row it is given, rows are missing from the
read, and it refuses to plan a tip that moves back; `--rewind` says the log really was cut
back.

## Checking

`rowkeep check` replays the rows it is given: each sits in the file its id names, ids have no
gaps, each `previous` matches, each fingerprint recomputes, the tip matches the last row,
and (given the whole stream) every tally equals a recount from the rows, every checksum an
index document holds matches it, and collection `ix` holds no other document.

It says only what it verified. A full check with the index ends `chain intact`. Without
`--ix` it ends `rows intact, index not checked`. With `--partial` (only the newest rows
were given) it checks those rows against each other and the checksum of each index
document it was given, says that earlier rows and the recount were not verified, and ends
`This was not a full check; no problems in what was given`. Given no rows, with or without
an index, it reports `PROBLEM no rows were given, so nothing was verified` and exits 1.
An option it does not have, or
one whose value is missing, is refused: a mistyped `--ix` is not a check without an index.

## Versions

Stream rows have not changed since the first version: their bytes and their fingerprints
are the format. Version 0.5.0 added only the `ck` field on the vault's documents and on
`state/verbs` and `state/settings`, and the commands above.

- **A 0.4.0 log and vault under 0.5.0:** nothing to do. Documents without `ck` are read as
  they are, in every command; the vault's tip gains a `ck` at the next copy.
- **A 0.4.0 script on what 0.5.0 wrote:** it reads all of it. `ck` on a vault or `state`
  document is one more field that 0.4.0 does not look at, so its `vault-plan`,
  `vault-check` and `vault-restore` work unchanged, and they do not notice a wrong `ck`
  there. A copy planned by 0.4.0 writes a tip and chunks without `ck`, which 0.5.0 accepts.
  0.4.0 has no `vault-plan --inline`, `first-docs` or `confirm`.
- **A 0.6.0 log, vault and inbox under 0.7.0.** Sealed rows and vault documents are read as
  they are, and `check` gives the verdict about the rows that 0.6.0 gave: rows that 0.7.0
  would not take at the door (an amount as text, a counted number, a date that is no date,
  a sequence row out of order) are history, and are recounted as they were counted. The
  credential filter is no longer run over sealed rows when they are recounted. Three
  things differ:
  - *Index documents `rebuild` rewrites.* An entity state (`ix/e.<hash>`) is derived
    differently in two cases: its record has a name typed with a combining accent, or a
    Greek name with a final sigma, or a character Unicode folds into another when composed
    (its `nks` changes: `e clair` becomes `eclair`); or its record held 60 current details
    when one row both cleared a detail and set a new one (the new one is now kept). `check`
    names each such document as differing from a recount, and `rebuild` rewrites exactly
    those. Until then a name search by the new key does not find the record by its old one.
  - *Waiting rows `seal` now skips.* A row left in an inbox by 0.6.0 that breaks a rule
    above stays there, with the reason, as does one in a file not named like a document.
  - *`state/settings`.* Version 0.7.0 does not write it; an existing one is left where it
    is. Each person's choices are read from their own member document, and a member
    document that holds only `{"member": true}` means the defaults.

  `vault-restore` and `vault-check` need the vault's tip; `ingest` takes only the four
  layouts above and a new or empty folder; `confirm` no longer counts a removal it could
  not check as planned; `personalize` writes `rowkeep-personal.skill`; no command writes
  over a file.
- **A 0.6.0 or 0.7.0 log, vault and inbox under 0.7.1.** Nothing stored is read
  differently: rows, index documents, vault documents and waiting rows are taken as
  0.7.0 took them, with these exceptions.
  - *A vault's tip* must be a tip in every mode (see "The vault's documents"). A tip whose
    `n` is not its `id`, or that does not name itself `head`, was taken by 0.7.0 from
    files; it is refused now. No version of the script wrote such a tip.
  - *A step of a sequence* built from another number of steps than the enrollment was made
    with is skipped at `seal`, where 0.7.0 sealed it. An enrollment under a definition
    that has since been revised to another length is finished with the steps it was
    enrolled with.
  - *An index document over 200,000 bytes* in an older log no longer stops `rebuild` when
    it is right; see "A document over the size budget".
  - *`rebuild`* refuses a stream in which a row's `p_` fields do not match its payload,
    which `check` always reported and 0.7.0's `rebuild` passed over.
  - *`check`* given no rows and an empty index reports a problem, where 0.7.0 ended
    `chain intact`; a log with no rows yet has nothing to check.
  - *`seal`* exits 1 with `NO PROGRESS` when the inbox holds only documents that are not
    valid rows, where 0.7.0 ended `nothing to seal (N skipped)` with exit 0.
  - *`vault-plan`* refuses a vault whose tip names another log as its `source`, where
    0.7.0 did not compare; a log restored to a new link is copied with `--moved`, once.
  - *`today`* is new: it prints the date in a named time zone, given by file, and refuses
    a zone it does not know. The daily brief uses it in place of `TZ=<zone> date`.
  - *The pages.* The log's page takes a tip only if its count is its newest row's number,
    and a daily brief only if it holds a real `date`, a `tip` and a list of `items`.
  - *The access rules.* A log published by 0.7.1 has `members` with `write` `self`; one
    published earlier has `write` `admin` and keeps it until its owner has the rules
    declared again (the skill's `setup/SETUP.md`). Nothing in the database differs.

  Work folders are not stored data, and are held to more: a folder `ingest` 0.7.0 filled
  (its manifest holds no digests) is refused wherever it is read, and is ingested again;
  `ingest` needs the collection (`--collection` where the result gives no paths); and
  `seal --inline` needs a manifest in both its folders.
- **Before 0.6.0 the project was called Packlog.** Logs and vaults made then are the same format and work as they are. Their two artifacts are titled `Packlog` and `Packlog Vault`: retitle them `Rowkeep` and `Rowkeep Vault`, by republishing each page, which changes nothing in the database, so that the skill finds them by title.
