# Rowkeep

A small database that lives in an artifact in your own Claude account. It keeps an activity
log and records for your work. Ask Claude to log a decision, an approval, a purchase or a
call with a client, and it is written to a log you own. Ask what changed, what is due or
where a deal stands, and Claude answers from that log. There is no outside service and
nothing to administer.

Rowkeep is an independent open-source project. It is not made, endorsed or supported by
Anthropic.

## Install

In Claude Code 2.1.275 or later:

```
/plugin install rowkeep --marketplace cklose2000/rowkeep
```

Then say "set up Rowkeep".

For claude.ai, get `rowkeep.skill` from the repository's releases: in claude.ai open
Customize, then Skills, choose Add skill, then Upload skill (older layouts have Skills
under Settings); code execution must be on.

## What you can ask

Start with "Set up Rowkeep." Then, for example (the company names are invented):

- "Log a first call with Alder Example Co. They want a pilot. Next step: send a proposal by Friday." And later: "What follow-ups are open, and what's the latest on Alder Example Co?"
- "Log that I approved $40,000 with Birch Example Media for the Q1 campaign." And later: "How much have I approved, by vendor?"
- "Log that Sam reviewed slide 7 of version 3 of the Q1 board deck and signed off." And later: "Give me the history of the Q1 board deck: versions, decisions, and who signed off."

A fifteen-minute walk-through with sample data:
https://github.com/cklose2000/rowkeep/blob/main/docs/REVIEW.md

## What it keeps

- **A log** of what was done, to what and by whom. Rows are numbered and chained by
  fingerprint, so a changed or missing row shows up when the log is checked.
- **Records**: companies, contacts and deals, each with its current details, next step and
  due date. Names are matched, so "ALDER EXAMPLE Corp." finds the record made for "Alder
  Example Co".
- **Sequences**: steps in order, a few days apart, each done once per target. Claude drafts
  a step; you send it.
- **A vault**: a second copy of the sealed rows, as of its last completed copy, from
  which a log can be put back up to that row.

## Everything this plugin runs, stores, sends and fetches

- **Runs:** one Python script, `skills/rowkeep/scripts/rowkeep.py`, through a short shell
  launcher, `skills/rowkeep/scripts/rowkeep.sh`, which finds a Python 3.8 or later and
  starts the script with it. The script uses only the standard library. It checks rows,
  computes fingerprints and totals, and matches names. It reads and writes only the files
  and folders it is given, in a working folder Claude chooses. It opens no network
  connection.
- **Uses Claude's own tools,** in your account: to list your artifacts, so as to find your
  log; to read and write the databases of your log and your vault; and, during setup only,
  to publish the two pages.
- **Stores:** rows, an index and the member list (with each person's own choice about
  automatic logging) in the database of an artifact in your own account, your log; a copy
  of the rows in a second artifact, your vault; and the morning briefs in the log, if you
  schedule them. Working files stay in the session's sandbox or on your machine, and what a
  session read and wrote is in its conversation.
  [PRIVACY.md](https://github.com/cklose2000/rowkeep/blob/main/PRIVACY.md) lists each
  place and how to remove it.
- **Sends:** nothing to the authors of this plugin or to any other service. There is no
  telemetry.
- **Fetches:** nothing from outside. The script and the two pages load no outside code,
  fonts or data. The two pages do run JavaScript, in the browser of whoever opens them, to
  read the artifact's own database and draw it.
- **Schedules:** nothing unless you say yes. Setup offers a weekday morning session that
  prepares a brief of what is due, and asks separately whether it may also seal your
  waiting rows, seal your teammates', and copy to the vault. It is created only on your
  yes, does only what you said yes to, sends nothing, and you can remove it at any time.

## What gets logged, and when

By default Claude logs only what you ask it to log, and tells you in one line each time. At
setup, or later by asking, you can turn on automatic logging for yourself, in which case
Claude also records a closed list of things as they happen: a version published, a file
changed in a way that matters, a decision, an approval, a contact with a client. On a
shared log that choice is each person's own. Either way a row records an action, a decision
or a fact about your work. The skill tells Claude never to log the conversation itself and
never to read its memory of you or your past chats to fill the log.

## Before you use it

- **There is no way to erase one row.** The workflow has none, and the skill never edits a
  row: a correction is a new row. The owner and editors of the artifact do hold the
  database itself. So do not use Rowkeep for data someone may have a right to have deleted.
- **What is kept out, and what is not.** The skill tells Claude to keep email addresses,
  phone numbers and home addresses out of rows. It allows names, employers and roles, which
  are personal data too. Nothing enforces either: what you ask to have logged is logged.
- **The credential check is a net, not a guarantee.** The script drops fields named like
  credentials and masks text shaped like common tokens. Do not paste secrets and rely on it.
- **Setup needs a session that can publish artifacts:** Claude Code, or a claude.ai chat
  that has the tools to publish one. After setup, logging needs a chat that has the
  artifact database tools and access to the log.
- **Status: experimental.** See the repository for what has and has not been tested. A
  shared log has been run by the author with two people, and not with more.

## Support and privacy

Questions and problems: open an issue at https://github.com/cklose2000/rowkeep/issues.
Privacy: https://github.com/cklose2000/rowkeep/blob/main/PRIVACY.md. In short, the authors
of this plugin receive no data of any kind.

Licence: Apache-2.0.
