# Security

Rowkeep keeps a record that is meant to be trusted later, so this file says plainly what the
design protects, what it does not, and how it has been attacked so far.

## Who can do what

A log is a Claude artifact, shared the way any artifact is. The access rules it is published
with give three kinds of people different powers:

| Who | Can | Cannot |
|---|---|---|
| Viewer | read the stream, the index and the member list | write anything |
| Contributor | add rows to their own inbox, write their own entry in the member list (which holds their own choice about automatic logging) | read anyone else's inbox, write anyone else's inbox or member entry (the member list itself is readable by everyone the log is shared with), seal, write the stream or the index |
| Owner and editors | everything else, including sealing and writing into any inbox | write another person's member entry (from version 0.7.1, as the rules read; see below); be held to these rules for good: they can republish the page with different ones |

**A member entry can be written only by its own member.** From version 0.7.1 the rule for
the member list gives its write to a level called `self`. The platform's description of
access rules says that nobody holds that level, not even the owner, and that it goes on
the list above a rule for a person's own document; the rule below it then lets each person
write their own entry. So, as the rules read, nobody can switch on another person's
automatic logging by writing their entry. Three things to know about that:

- It is the platform's rule, and it has been seen once: on 2026-11-23, from Claude Code,
  on a one-person test log given these rules, the owner's write to an entry under another
  id was refused and the owner's write to their own entry was accepted. No run with two
  real accounts has shown it yet; [test/team-run.md](test/team-run.md) has the step that
  would.
- A log made before version 0.7.1 has the older rule, under which the owner and editors
  can write any member entry. Until that log's owner asks for its rules to be brought up
  to date (the skill's `setup/SETUP.md` says how, under "Bringing an older log's rules up
  to date"), an editor could change another member's choice.
- Whatever the rules, the skill tells the assistant that a member entry cannot show who
  wrote it, and to say in one line that automatic logging is on the first time it logs
  anything on its own in a conversation, even for a person who asked not to be told each
  time. That is an instruction to a model, there so that a choice the person did not make
  cannot act silently.

One consequence: the entry of someone who has left the log cannot be removed by anyone
else. It stays on the list, and a sealing session goes on reading that person's inbox and
finding it empty or not readable.

The rules are enforced by the platform, not by this project. The log's page declares the
`user` capability with no scope: it reads the viewer's id on the artifact, to find their own
waiting rows, and not their name or address.

## What the fingerprint chain proves

Each sealed row carries the SHA-256 of its own contents and of the row before it.

- It **does** catch a row changed or removed by accident, by a bug, or by anyone who does not
  then recompute the chain. `rowkeep check` replays every fingerprint, requires the rows to
  start at 1 and each to sit in the file its id names, and recounts the whole index.
- A check says only what it verified. Given part of the stream (`--partial`) it verifies
  those rows against each other and never ends `chain intact`; given no index it ends
  `rows intact, index not checked`. An option it does not know, or one whose value is
  missing, is refused, so a mistyped option cannot turn a check into a check of nothing.
- It **does not** stop an owner or editor from rewriting history. The chain has no secret in
  it: someone who can write the stream can change a row, recompute every fingerprint after
  it and rebuild the index, and `check` on the log alone will then pass.

## What the vault adds

The vault is a second artifact holding a copy of every sealed row. Its rules let only its
owner and editors read or write it, and it should not be shared with the log's other editors.

- Bringing the vault up to date compares one row with the vault: the log's row at the
  vault's tip. A history rewritten with its fingerprints recomputed changes that row, and
  is refused with an alarm at the next copy. A copy does not compare earlier rows with the
  vault, and says so. It does check every row it is given, earlier ones included, against
  its own fingerprint and its neighbours, so a row changed in place, without the chain
  being recomputed, is refused if it is among them; a copy that is given only the rows
  from the tip onward never sees such a row.
- `rowkeep vault-check` compares every row of the log with the vault and reports rows that
  differ or are missing. It is what compares earlier rows. It checks the rows the vault
  does not hold yet as well, each against its own fingerprint and the row before it.
- A restore is given the vault's tip, which says how many rows the vault holds, and refuses
  rows that stop short of it unless told that a part is wanted.
- The vault's owner is still a single party who must be trusted. Two artifacts in one account
  are not an independent witness. Nothing here protects against that owner, against loss of
  the account, or against the platform losing data.

## What a contributor can still do

A contributor is someone you let write to the log. Within that, they can:

- **Log rows that are untrue**, under any actor name. `actor` is whatever the writer says;
  `account`, set when the row is sealed, is the inbox it came from and is not forgeable by a
  contributor. Read `account`, not `actor`, when it matters who wrote something.
- **Change a record's current details**, since details are whatever the newest row says, and
  mark one record as merged into another of the same type.
- **Fill a tally's list of distinct values** (200 per field) with junk, after which new real
  values are counted together as "(other)". The rows themselves are unaffected.
- **Log a sequence step as done** for any target, which is indistinguishable from doing it,
  provided it is the next step of an enrollment that is active: a step out of order,
  after the sequence ended, or built from another number of steps than the enrollment was
  made with, is not sealed.
- **Choose for themselves** whether their own Claude logs without asking: that choice is
  kept in their own member entry, and applies to nobody else.

If those matter to you, do not make that person a contributor.

## A shared log

Two things about a log that several people write to, said plainly.

**A contributor can stop the log being sealed.** The artifact has limits that this project
does not control: 25,000 documents and 100 MB for the whole database, as observed in
November 2026. A contributor can write to their own inbox up to those limits, by accident
or on purpose, after which no write is accepted, a seal's included. Nothing in the rules
or the script prevents it. A contributor can also leave documents in their inbox that the
sealer has to skip: rows that are not valid, rows that would take a total out of range,
sequence rows that are not a valid next move. A skipped row does not block the rows behind
it (the sealer reads every page of an inbox, and `--leave` passes over rows that cannot be
sealed), but each one is read, reported and left where it is at every seal, and only the
person whose inbox it is, or the owner or an editor working in the database itself, can
remove it.

**What a contributor can do in their own area.** The rules give each person the write of
everything under `data/users/<their id>`, not only of the inbox Rowkeep uses there. So a
contributor can change or remove their own waiting documents until they are sealed: a
waiting row is a claim, and becomes part of the record only when it is sealed. A
contributor can also write other documents anywhere in their own area. Rowkeep reads
nothing there but the inbox, and those documents count toward the artifact's limits.

**A member with no member entry is not found.** A sealing session finds the inboxes by
reading the member list. Someone whose rows reached their inbox without a member entry
ever being written (an assistant that skipped the step, a write that failed) is on no
list, and an unattended sealer never reads their inbox: the rows wait, and no report
mentions them. The skill writes the entry before a person's first row. Where one is
missing, that person asking their assistant to log anything writes it.

**In a chat, one damaged document stops the round.** A chat carries every document by
hand, and when the script reports a waiting document whose checksum does not match, the
skill tells the assistant to stop and start the round again with everything read afresh,
because the likeliest cause is a slip in copying. If the document is damaged where it is
stored, the same thing happens again, and the valid rows behind it wait. To recover: let
the round go on (the script itself skips such a document and seals the rows behind it,
and `--leave` passes over any document by name), which from version 0.7.1 the skill tells
the assistant to offer the second time; or seal from a session with file exchange, such
as Claude Code. The damaged document stays where it is until its own writer, or the owner
or an editor working in the database, removes it.

**When the artifact is full.** A write refused over a limit does not succeed on a second
try, and the skill tells the assistant to stop and give the message word for word.
Nothing already sealed is lost by it. What frees room is removing documents: waiting
documents that are not rows, and documents outside the inbox in a person's own area,
which each person can remove for themselves. Rowkeep has no command that removes sealed
rows, and removing them by hand breaks the chain from that row on. The other way out is a
new log: a last copy to the vault, then a fresh setup, with the full artifact kept as the
record of what came before.

**What one run with two accounts showed, and what it did not.** The design is that the
owner and editors can read every inbox, and so seal everyone's waiting rows, while a
contributor can write only their own inbox and their own member entry. That rests on the
access rules behaving for a second account as they read on the page. The author ran it
once, on 2026-11-23, with two accounts in a claude.ai team workspace and version 0.6.0,
and reports:

- a member given the role Contributor ("Can change shared data, not the artifact") acted
  at the database level `interact`;
- the contributor could write rows to their own inbox and their own member entry, could
  not see the owner's waiting rows (the read came back empty), and could not open the
  vault, which was not shared;
- the owner could read the contributor's waiting rows, which is what sealing a teammate's
  rows depends on;
- a member given Editor could seal, and when the owner and the editor sealed at the same
  moment, one write was refused on a version, was retried, and each row was sealed exactly
  once;
- after the member was removed from sharing, their chat got "not found" for the log, and
  their sealed rows remained;
- the full check and the vault check, run by the owner's chat, ended `chain intact` and
  `vault intact`.

That run did not show the database itself refusing anything: the operator declined the
assistant's write prompts before the database saw them.

**A second run the same day, with version 0.7.0,** did. The author reports, from the same
two accounts:

- the database refused a contributor's seal, which is a write to the stream and the
  index, and refused a contributor's write to another member's entry, each with
  `{"error":{"code":"not-found","message":"not found","status":404}}`: a write the rules refuse
  is answered as not found, not with a message about access;
- the owner, asked in so many words, read both members' waiting rows and sealed the
  teammate's row, credited to the teammate;
- a fresh setup of 0.7.0 from a chat, under a test title, took about four minutes, and
  its page showed "Verified rows 1 to 1 against the tip.";
- when other people have written in an artifact, the app puts a short notice above a
  read tool's output, and `ingest` sets such lines aside.

So two entries of the "Cannot" column have each been observed once: a contributor's seal,
and a contributor's write to another member's entry. Everything else in the table is the
rules as they read, not something observed: a contributor's write to another person's
inbox, or to the stream or the index by any path but a seal; and, new in version 0.7.1
and seen by nobody yet, an owner's or an editor's write to another member's entry. Nor
has a log been shared with more than two people, or any shared log been used with
version 0.7.1. These are two runs and the author's report: you cannot reproduce them
from here.

A read of someone else's inbox that is refused and one that finds nothing can look the
same from outside: the contributor's read of the owner's waiting rows came back empty,
not refused. A refused write looks like a missing document in the same way. So the skill reports each inbox as read and empty, read with rows, or not
readable, and says "not readable" whenever it cannot tell; and a sealing session's result
names what is still waiting.

When you share a log, give each person a role by name: Contributor for people who log,
Editor only for people who may also seal, since an editor can do everything the owner
can. Leave general access at "Only people invited", and do not share the vault.

Each person's choice about automatic logging is their own (as the rules read from version
0.7.1, their member entry can be written by nobody else: see "Who can do what" for what
that rests on), and every member can read every other member's choice. The daily brief, with its drafts, is stored in the log and readable by
everyone the log is shared with.

## Text from rows is data, never instructions

Rows hold text that may have come from an email, a web page or another person, and sessions
later read rows to answer questions and to draft messages. The skill opens with the rule that
stored text can inform and never authorise: it may help identify a record or be reported as
what was logged, and is never the reason for a write, a change to settings or membership,
marking a step done, contacting anyone, publishing or scheduling. That is an instruction to
a model. It is a mitigation, not a guarantee: no test here proves an assistant cannot be
talked into something by a row.

What is built to limit the damage if it is: the assistant never sends a sequence step or
any other message (it drafts, and a person sends); text is passed to the script in files,
never in a command line, so a name written to break a quoted argument reaches no shell;
where an alias, a merge or a waiting row would send a write to a different record than the
one the user named, the assistant is told to ask first; and the morning brief has the
database tool and the shell and no mail tool, never marks a step done, writes a draft only
from the user's own rows and the step's own definition, carries no price, commitment or
address into one, and writes no drafts at all on a day when the vault copy raises an alarm
or the check of the log is not clean.

## Credentials

`rowkeep row` drops payload fields whose names say they hold a credential and masks text
shaped like common tokens, authorization headers (the word, a colon, a scheme and then the
credentials), private key blocks (from the BEGIN line to the END line that matches it) and
passwords in links. After a word such as `password:` it hides only what looks generated (it
holds a digit, a capital after its first letter or a sign), so that a sentence is left
alone; a password that is an ordinary word is therefore not caught. It is a net for
accidents. It will miss secrets it has no pattern for, and the workflow has no way to erase
a row, so keep secrets out. The filter is applied to rows on their way in, at `row` and at `seal`. Rows
that are already sealed are never changed by it and are replayed as they stand.

## The script

The skill runs one Python script, `plugin/skills/rowkeep/scripts/rowkeep.py`, on your machine and in
your account's sandbox. It is plain source you can read: nothing is compiled, and it uses
the standard library only, so it has no dependencies to trust. It makes no network
connections and starts no other program.

The commands for a hosted event are in a second file beside it, `rowkeep_events.py`, which
the script loads when one of them is asked for (the script itself is near the size one
file of a plugin may be). That file has no `import` of its own: it reads, writes and prints
only by calling the script, so everything said here of the script holds for it.

**What it reads and writes.** It reads the files and folders it is given, and writes only
under `--out` (and, for `page` and `pack`, the one file named). What that does and does not
mean:

- A name that comes from a document becomes part of a path only if it passes one rule: up to
  100 letters, digits, `_`, `.` and `-`, starting with a letter or a digit, not ending in a
  dot, and not a name Windows keeps for a device. Document ids from a read (`ingest`), from
  an inbox folder's file names (`seal`) and from a plan's paths (`confirm`) are all held to
  it, and a path built from them is checked to lie inside the folder it was built under.
- Inside a folder it is given, it reads no file that is a symbolic link, a Windows junction
  or any other reparse point, and no file that is not a regular file; and it writes
  through no folder under `--out` that is a link. The folder you name, and a single file
  you name outright (`--from`, `--plan`, `--vault-head`, `--registry`), are your own choice
  and may be reached through a link.
- It never writes over a file. Every file is created new, and where one of a command's
  files is already there, none is written: every path, the batch files included, is
  looked at before the first file is made. Plans are written after the documents they
  name. If writing is interrupted all the same, the message says how many files were left.
- A document is found in a folder by its exact name, taken from the folder's own listing:
  the file system is never asked whether `A.json` will do for `a.json`, and a folder that
  holds two names differing only in capitals is refused.
- A file's size is looked at before any of it is read (the limit is 4 MiB), and the length
  of a number's text before it is turned into a number.
- `pack` and `personalize` take a fixed list of the files a skill ships. Whatever else is
  in the folder is not packed.

It is not a sandbox: it does not stop the session that runs it from reading or writing
anything, and a race with another process that swaps a file for a link between the check
and the read is not defended against.

**What it prints.** Every document the script reads may have been written by someone else,
and so may a file's name. It caps nesting depth before parsing and validates every field.
Wherever it prints a value that came from a document, a file name or an argument inside a
message, the value is quoted, cut at 80 characters (300 for a verb's description), and has
its control characters, Unicode line separators and direction-changing marks written as
escapes; a path is printed as it is unless it holds something that would be escaped. Every
message is one line. Two things are printed that are not messages and are not cut: the
inbox document `row` prints for a session that cannot pass files, which is canonical JSON
and so holds whatever text the row holds (control characters escaped, as JSON requires,
and nothing else); and JSON result lines such as `match`'s, in which the same characters
are written as JSON escapes. Treat what a row holds as data wherever it is shown.

## Documents carried by hand

A chat cannot pass files to or from the database, so a session sealing from a chat copies
each document itself, and a copy can be wrong by one character. What is verified:

- **On the way in.** Every inbox document and every index document holds `ck`, a checksum of
  its own contents. The script recomputes it: a waiting row that does not match is left
  where it is, and a seal will not build on an index document that does not match, or, when
  sealing from a chat, on one that holds no checksum at all.
- **On the way out.** Everything the seal's plan wrote is read back and compared with the
  plan by `rowkeep confirm`, document by document, and the inbox is read back to show that
  the rows it removed are gone. A sealed row stored wrongly no longer matches its
  fingerprint, and an index document stored wrongly no longer matches its checksum: `check`
  reports both, and the next seal that reads that index document stops. `confirm` compares
  what one write left; it does not replay the chain, and the skill tells the assistant to
  report the two as separate facts.
- **A write whose outcome is not known.** A timeout or a result cut short says nothing
  about whether the batch landed. The skill tells the assistant to read the tip back before
  doing anything else and never to send the same write again blind; a batch is pinned to
  the versions that were read, so one sent twice is refused the second time.
- **Not caught by a checksum:** an entry left out of the write. `confirm` reports a planned
  document that is not there and a waiting row that was to be removed and is still there,
  where the session runs it; a missing row or tally also shows in `check`. The skill tells
  the assistant to send the plan whole and to run `confirm`, which are instructions, not
  guarantees.
- **The vault copy.** Sealed rows hold no checksum, so on the way in they are verified by
  their fingerprints: the row at the vault's tip must be the row the vault holds, and each
  new row must carry the fingerprint of its own contents and chain on from the one before.
  A row miscopied in the read, or stored wrongly by the seal before it, stops the copy. The
  vault's tip is verified by its checksum. On the way out, both documents the copy wrote
  are read back and compared with the plan by `rowkeep confirm`: value for value, as
  canonical JSON, and each against its own checksum. (Not character for character: `1.0`
  read back where `1` was planned is the same value, and passes.)
  A difference found there is reported and not repaired: nothing in the vault is rewritten
  without a person deciding.
- **Which read a document came from.** `ingest` takes a read result in four exact layouts
  and refuses anything else, and leaves a manifest beside the documents: the collection,
  each document's id, its version and the SHA-256 of the file written for it, and whether
  the read was whole, which only a read from its first page to a page with an empty
  cursor is. Every command that reads such a folder first holds the folder to its
  manifest: a file added, removed, renamed or swapped for another, however true to its
  own `ck`, is refused by name. `seal --inline` takes only folders that hold one: the
  inbox from a read of exactly that account's inbox, the index from a whole read of
  collection `ix`, an empty one included, and each document's version from the manifest.
  `confirm` counts a removal as confirmed only by a whole read of exactly that document's
  collection, or a read of that one document that found none; otherwise it ends
  `not confirmed`, not `as planned`.

  What a read manifest proves is this much and no more: it ties a folder's files to one
  read's collection, ids, versions and contents, and says whether that read said it was
  whole. It cannot show that the read was current: a folder read an hour ago and left
  untouched passes, and it is the pinned write, refused by the database on a stale
  version, that catches that. It cannot show that a query was not narrowed by a filter,
  that a result given as the first page was the first page, or that the text saved was
  the text returned. And like `ck` it has no secret in it: it catches a mix-up of files,
  not someone who rewrites the manifest to match.
- **Setup.** The two first documents are written from a plan and read back the same way.
  Nothing checks that the two pages were published unchanged or that the access rules took
  effect, except that setup reads the rules back and stops if they differ from the ones it
  ships.

The checksum has no secret in it. It catches accidents of copying and proves nothing about
who wrote a document. Exactly what a matching `ck` shows: that the document's own contents
are, to within 48 bits (twelve hexadecimal digits of a SHA-256), the contents its writer
hashed. It shows nothing about the document's path, its version, whether it is still the
current document, or whether the read it came from was complete. An index document's name
is tied to it by the field `k`, which the checksum covers; its version is settled by the
database when the pinned write is sent; which read a file is of, and the completeness of
that read, by the manifest above.
A `ck` that is anything but twelve lowercase hexadecimal digits is a wrong one.

## What has been tried against it

An adversarial review before publication ran attacks against the program. Each of these was
confirmed and then fixed, and each fix has a test in `test/stream.mjs` (section 12) or in
`test/unit.py`:

- one enormous summed value blocking every later seal of that verb;
- `check` and `vault-check` passing on a log with rows deleted from the front;
- a junk row using up another person's once-only key, so their real row was dropped;
- oversized counted values and impossible dates crowding out the tallies;
- a credential check that missed most real tokens and blanked ordinary sentences;
- a row's text being echoed raw into the terminal of the session sealing it;
- a deeply nested document crashing the program, and one bad file failing a whole seal;
- changes the index check ignored: planted records and tallies, deleted markers, altered
  filterable copies;
- a record redirected to a made-up entity;
- a private copy of the program placed at a predictable path in a shared temp folder (the
  launcher no longer copies anything anywhere).

That review found no way to run code, no script or markup injection in the pages, no path
traversal, and no way for a contributor to write as another person.

## The two pages

The pages run JavaScript in the browser of whoever opens them, read the artifact's own
database, and load nothing from outside. They write nothing. Every value is set as text.

The log's page recomputes the fingerprints of the newest rows it holds (up to 400) and says
what it did: "Verified rows X to Y against the tip" only when the tip is a valid one and
the rows read reach it; otherwise "Not verified" with the reason, or "Broken" with the
row. It does not check earlier rows, does not recount the index, and does not look at the
vault: it is a convenience, and `rowkeep check` and `vault-check` are the checks. The
vault's page shows what the vault's tip says and compares nothing.

A review of the pages found, and `test/page.mjs` now covers: a clean verdict shown over a
tip with no rows under it; a reload that retried without limit and said "Reloaded" after
a read that failed; one malformed document blanking a panel while the status stayed good;
`NaN` and a stale date on the vault's page; a "due" count taken over the first 200
entries only; reads that stopped at 1,000 documents without saying so; and a prepared
draft shown against a step it was not written for.

A later review of the pages, before version 0.7.1, found more, each now covered in section
13 of `test/page.mjs`: "Verified rows X to Y against the tip" kept on screen after the
tip's fingerprint had changed at the same row number, because only the number was
compared (the page now compares the tip's row, count and fingerprint, and looks at the tip
again after the fingerprints are computed); a tip whose count was not its newest row's
number taken as a tip; a failed read of the stream left as "Checking", and a failed read
of the vault left over the count and tip read before it; a stream whose documents were
all malformed, with no tip, called "Nothing sealed yet"; a browser that could not compute
a fingerprint reported as a row "changed since it was sealed"; a brief with a malformed
date, no list of items or no tip shown all the same, and a brief that could not be read
passed over in silence; and tallies called "exact", where the page shows them as the
index stores them and recounts nothing.

A second review, of the script alone, before version 0.7.0, found the following. Each was
reproduced, fixed, and given a test in `test/unit.py` (and, where a whole command is
involved, in sections 14 and 16 to 22 of `test/stream.mjs`):

- clean verdicts that were not earned: an unknown option or one with no value ignored, so
  that `check --ixx` checked no index and said `chain intact`; a partial check that took a
  tip whose count had been changed; `rebuild` deriving an index from a row whose contents
  no longer matched its fingerprint, and moving the tip back when given too few rows;
  `vault-restore` taking one chunk of two for the vault; `vault-check` counting an altered
  row as "not yet copied"; a renamed vault chunk and a stray index document passing;
  `confirm` saying `as planned` of a removal it could not check;
- wrong numbers: an amount written as text counted and added to nothing; a field listed
  twice in a registry doubled; the value true and the text `true` in one bucket; a row
  blocked for ever by another vendor's total; a tally document grown past what the
  database takes; a full record losing both the detail a row cleared and the one it set;
  the 31st of February kept as a due date, and a date in the year 10000 cut short;
- documents copied by hand: a result that declared two documents and held one, an array
  missing its bracket and an object with two lists all taken; a version of five thousand
  digits ending in a traceback; an inbox file named `order a` sealed; a document with an
  unsupported number accepted on a `ck` of null;
- the JSON reader: `1`, `1.0000000000000001` and a key given twice sharing one fingerprint,
  `1e-999` read as 0, and a Unicode escape accepted with spaces in it;
- files: `first-docs` writing over a file; reads and writes through links and junctions;
  `personalize` packing whatever was in the folder; the launcher failing under `CDPATH`;
- records and sequences: `match` answering with a record nobody had given, or with any
  object that carried a name; names typed with combining accents keyed differently; a step
  sealed after a sequence had ended, setting it going again;
- text: a payload key holding a newline printed as two lines; a run whose reader had gone
  away ending with exit code 0; the credential filter blanking a word after `password:`
  and missing `Authorization: Basic`.

Not everything that review raised could be closed in the script. The manifest cannot prove
a read was unfiltered; nothing here defends against a process that changes a file between
the script's check of it and its read; and a contributor can still do everything listed
under "What a contributor can still do".

A second independent check, of version 0.7.0 as released, confirmed that those stayed
fixed and found twelve more. Each was reproduced, fixed in version 0.7.1, and given a test
in `test/unit.py` (`SecondCheckTests`) and, end to end, in section 23 of
`test/stream.mjs`:

1. a read manifest that was not tied to the files beside it: one file of an ingested read
   swapped for an older copy, true to its own checksum, was sealed on, pinned to the
   current version; `confirm` and `vault-plan --inline` took the same mix-up, and
   `vault-check` and `vault-restore` did not look;
2. an empty read with no collection, the last page of a read alone, or a read of another
   collection or another account's inbox certifying a removal or supplying versions;
3. `ingest` setting aside the dangling members of a truncated document as an app's notice;
4. `ingest --id` dropping a field beside `data`, choosing between names that disagreed
   (`id` and `path`, `version` and `ifVersion`), and writing the first copy of an id given
   twice;
5. `vault-check --partial` ending `vault intact` over a row that did not point at the
   vault's tip; `vault-plan` building on a tip that counted no rows; a partial vault check
   ending in the words of a full one;
6. `rebuild` passing a row `check` reported (an extra filterable copy);
7. a sound older log whose tally was over the size budget passing `check` and being
   refused by `rebuild`;
8. `rebuild` and `vault-restore` leaving batch and document files behind a refusal that
   said nothing was written;
9. a sequence step built from a shorter list of steps finishing an enrollment early; and
   the documents saying a repeated step stays in the inbox where the code drops it as a
   repeat (the documents were wrong);
10. `match` saying `new` of a folder that did not exist, and of candidates it could not
    use;
11. `confirm` finding `ix/A.json` for `ix/a` on a file system that does not tell capitals
    apart;
12. smaller gaps: options with no `--NAME-file` form; a file name holding a
    direction-changing mark printed raw; `pack` refusing a `SKILL.md` with CRLF line ends;
    an inbox of invalid documents ending `nothing to seal`, exit 0; `--leave` taking an id
    that was not there; an override or skipped rows named only above an ordinary last
    line; `confirm` saying `as planned` of a plan with no writes; `check` saying
    `chain intact` of two empty folders.

What that check could not close either: a read that is whole and untouched and no longer
current passes every check of the folder, and is caught only when its pinned write is
refused; and the script takes the caller's word that a result ingested without `--page`
was a read's first page.

## Reporting a problem

Open an issue on this repository. If the problem would put users at risk before a fix,
report it privately instead: open this repository's Security tab and choose "Report a
vulnerability". If the tab offers no way to report (private reporting is a setting of the
repository, and may not be on), open an issue that says only "security contact requested",
with no details, and you will be contacted.
