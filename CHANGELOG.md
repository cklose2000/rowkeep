# Changelog

## 0.7.3

- Two wordings in the script changed, and nothing it does. A scan by Anthropic's plugin
  directory read a web address in a comment (the source of the date arithmetic) as a
  place the script could send data, and the words "pass --partial" in a message as a use
  of the `pass` password store. The script imports nothing that can open a connection or
  start another program. The comment now names the source by its title, and the message
  says "add --partial".

## 0.7.2

- The skill's description is in double quotes. It holds colons, and a strict YAML reader
  refused the unquoted line ("mapping values are not allowed here"); claude.ai's upload
  and Claude Code's plugin check both took it as it was. Nothing else in the skill
  changed. `tools/check-skill.py` now fails a description that needs quotes and has none.

## 0.7.1

Fixes for what a second independent check of 0.7.0 found, in the script and then in the
skill's instructions, the pages and the release tools. Stored rows, index documents and
vaults are read as before; what differs is listed under "Versions" in
[docs/ACTIVITY-STREAM.md](docs/ACTIVITY-STREAM.md), and the script's twelve findings in
[SECURITY.md](SECURITY.md).

The skill's instructions and the access rules:

- A member document can be written only by its own member. A new log's rules give the
  write of the member list to a level nobody holds, so that an owner or editor cannot
  switch on another person's automatic logging. This is the platform's rule; it was seen
  once, on a one-person test log, and not yet with two accounts. A log made earlier keeps
  the older rule until its owner asks for the rules to be brought up to date. A log first
  published before 0.7.0 also stores a `profile` scope the page no longer uses, and
  Claude Code refuses the update until the declaration drops it (`setup/SETUP.md`).
- Setup asks about the morning session's parts one by one (the brief, your own waiting
  rows, your teammates', the vault copy), writes each answer into the session's prompt,
  and the session does only those. A prompt written by an earlier version seals the
  user's own rows only and copies nothing to the vault until it is edited.
- A title no longer chooses a vault or, on a shared account, a log for a write.
  `vault-plan` refuses a vault whose tip names another log (`--moved` after a restore to a
  new link), and the assistant names a log found by title, and asks, before its first
  write there when the log is shared or one of several.
- Nothing read from the database, the user or a prompt goes on a command line: ids,
  entities, versions, links and a time zone go in by file. `today --zone-file` replaces
  `TZ=<zone> date`.
- A brief's draft uses a step's wording only when the user's own account defined it;
  nothing from another account's rows, and no address, price or commitment, goes into a
  draft, a note or the brief. A day without a clean check stores what is due by name and
  date only. The brief's and the sealer's result lines give each fact its own part.
- The assistant makes no database call before a request that is plainly Rowkeep's; adding
  an alias, clearing a step on another record and ending a sequence each wait for a yes; a
  seal whose reply never arrived is "outcome unknown" until rows and removals are compared.
- The seven steps of sealing from a chat are now in `ops/SEALER.md`, under "The rounds,
  from a chat". Setup can make an empty log to restore into.

The pages:

- The log's page compares the whole tip, not its row number alone, looks at it again
  after computing fingerprints, and says "Not verified", with the reason, when a read or
  the hashing fails. Only a valid brief is shown. The vault's page clears what it showed
  when a read fails. The tallies are described as the index stores them.

The release tools:

- The pre-publish check masks where a finding is as well as what it is, applies its
  file-name rules to the archive it is given, and reads the text a GIF carries; the plugin
  check parses the manifest; CI's steps fail on the first error and run on a named image.

The documents: [SECURITY.md](SECURITY.md) says which refusals have been observed on a
shared log and which are the rules as they read; the walk-through in
[docs/REVIEW.md](docs/REVIEW.md) uses a test log; the sample company names in the tests and
the drill are invented ones; the README opens with an animated walk-through.

The script:

- `ingest` records the collection and the SHA-256 of every file it writes, and every
  command that reads such a folder holds the folder to that record. A read's collection
  must be known (`--collection` where the result gives no paths, an empty result always);
  a read that came back in pages is ingested into one folder with `--page 2`, `--page 3`;
  a read of one document that found none is `{"exists": false}` with `--id`. A folder
  ingested by 0.7.0 is refused: ingest the read again.
- `seal --inline` takes only folders `ingest` filled: the inbox from a read of that
  account's inbox, the index from a whole read of collection `ix`, an empty one included.
- `ingest --id` drops no field of a bare document, and an entry whose names for its id or
  its version disagree is refused. Lines above a result are set aside only if they cannot
  be part of a document.
- A vault's tip is held to one rule by every command. `vault-check --partial` anchors the
  rows it is given to the vault and has its own last line.
- `rebuild` refuses every row `check` reports, leaves an index document that is right
  alone whatever its size, and, like `vault-restore`, writes nothing when it is refused.
- A sequence step must agree with the number of steps the target was enrolled with.
- `match` says `new` only of candidates it could read.
- Documents are found by their exact names, on every file system.
- Every option that takes a value has a `--NAME-file` form. `pack` takes a `SKILL.md`
  with CRLF line ends.
- `vault-plan` compares `--source` with the vault's tip; `today` is new.
- The last line of every command states the outcome. `seal` exits 1 when documents were
  waiting and none could be sealed; `check` given no rows reports a problem; `confirm`
  refuses a plan with no writes.

## 0.7.0

The first public release.

- A skill and one Python script (Python 3.8 or later, standard library only, no network)
  that keep an append-only activity log in the database of an artifact in your own Claude
  account. There is no server and no outside service.
- Rows are checked against a registry of verbs. Sealing numbers them, chains them by
  fingerprint and updates the tallies in one all-or-nothing batch; `check` and `rebuild`
  go over the whole stream.
- Current records (companies, contacts, deals), follow-ups with due dates, and outreach
  sequences, all kept from the stream. Sequences record steps; they never send anything.
- A vault: a second artifact that holds a copy of every sealed row, with commands to plan
  a copy, check it against the log and restore from it.
- Setup from Claude Code or from a chat that can publish artifacts. It says what it will
  make and waits for a yes.
- The skill is released as `rowkeep.skill`, which names no log. It is built from a fixed
  list of files with fixed dates. The build makes the archive twice and fails if the two
  differ: what is tested is that two builds with the same tools give the same bytes.

What each check proves, and what it does not, is in [SECURITY.md](SECURITY.md). What is
kept, and where, is in [PRIVACY.md](PRIVACY.md).
