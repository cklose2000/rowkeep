# The drill: a week in fifteen minutes

Everything in the sequence machinery turns on dates: a step is due some days after the one
before, a brief runs each morning, a reply on Wednesday stops Thursday's step. Waiting real
days to test that is useless. The drill passes the date in instead, so a simulated day takes
only as long as the sessions that act in it: about two minutes live.

It runs at two speeds from one script, `tools/drill.mjs`, against one written-down week.

## The week

Five targets are enrolled in one three-step sequence (email, email two days later, call two
days after that) and take five different paths:

| Target | What happens | Ends |
|---|---|---|
| Aspen Example Corporation | every step on time, then silence | `no_reply` after step 3 |
| Hazel Example Industries | replies the day after step 2 | `replied` after step 2 |
| Maple Example LLC | the first email bounces | `bounced` after step 1 |
| Rowan Example Group | step 2 goes out a day late, so step 3 moves a day | `finished` |
| Willow Example | finishes, then replies the next day | `replied` after step 3 |

On day 2 Aspen Example's second step is logged twice at the same moment; one must be refused.

Each simulated day has a **morning**, when a brief session seals whatever was logged the day
before and says what is due, and a **day**, when that day's scripted actions are logged.

## Fast: the stand-in database

```
node tools/drill.mjs mock
```

The whole week in a few seconds: the script builds and seals every row, and each morning's
due list is read with the same query a session uses. CI runs it with the other tests.

## Live: the real store and real scheduled sessions

Run by a Claude Code session, because only a Claude session can write to an artifact's
database. Use the test log, never the real one. About fifteen minutes.

1. Pick a run id not used before on that log (a target can go through a given sequence only
   once, and the sequence is named `drill-<run>`) and a start date well away from other data.
   `node tools/drill.mjs expect --run 0002 --start 2027-02-01` prints what should happen.
2. Day 0: `node tools/drill.mjs day 0 --run ... --start ... --out DIR --inbox data/users/<id>/as/inbox`
   prints the writes; send them as one `batch`.
3. For each morning, day 1 to day 7: start a brief session ([ops/BRIEF.md](../plugin/skills/rowkeep/ops/BRIEF.md))
   with `Date:` set to that day and `Seal: own` (the drill's rows are in the operator's own
   inbox, and from version 0.7.1 a brief seals only what its prompt says), and wait for
   `brief/<date>` to exist. A cloud routine whose
   prompt is updated and then run does this without a schedule. Then build and send that
   day's rows the same way as day 0. Where the output lists `again`, send those writes in the
   same instant as the others, as a separate call, and expect that call to be refused.
4. After the day 7 morning, save all of `ix`, `stream` and `brief` with `out_dir` and grade:
   `node tools/drill.mjs grade --run ... --start ... --dir EXPORT`

## What is checked

The drill holds its own model of how a sequence behaves, written without reference to
`rowkeep.py`, and compares:

- each morning's brief: exactly the steps the model says are due, for the right targets,
  with the right due dates; a draft for each email step; notes and no draft for each call;
- each target's final state, and that nothing more is due for any of them;
- each target's rows: every step once, in order, each recording the simulated day;
- the tallies: how many reached each step, and which step drew each outcome.

## What it does not check

Whether the drafts are any good, which is a person's call. Anything about real mail. A second
person. And real elapsed days: a morning that never comes because a schedule failed to fire.

## Results (the author's runs, on private artifacts)

These are notes of runs nobody else can reproduce or inspect. They are here to say what was
tried, not as evidence.

The five targets' names are invented. They were changed in version 0.7.1, so the run
below, which was made before that, logged the same week under other sample names, and
with the brief as it then stood.

2026-11-22, run 0001 on the test log, start date 2027-01-04: passed, 32 of 32 checks. Seven
mornings ran as cloud sessions (one routine, its prompt updated and run each time) between
00:01:44 and 00:12:52 UTC, about eleven minutes, each taking 40 to 85 seconds. They sealed 28
rows between them (17 on the first morning, in the cloud, unattended), wrote seven briefs
with five email drafts and three sets of call notes, sent nothing and logged nothing. The
repeated step on day 2 was sent in the same instant as the original and was refused by the
store. Afterwards the log held 66 rows and `check` ended `chain intact`.

2026-11-22, restore drill on the same log: a fresh session following the skill's `ops/VAULT.md` copied all
66 rows into an empty vault (one batch of two writes), checked it, and restored into a new
empty artifact in four batches plus the registry. `tools/same-log.mjs` then compared the
original and the restored log: 66 of 66 rows and 79 of 79 index documents identical. A
viewer-level read of the vault returned nothing.

2026-11-22, vault inside the morning session: one new row was logged, and a cloud brief
session given `Vault:` sealed it as row 67 and copied it into the vault in about a minute.
The vault's tip and the log's tip then showed the same fingerprint for row 67.
