# Privacy

Rowkeep's authors receive no data from anyone who uses it. There is no server, no account
with us, no telemetry and no analytics in the plugin.

That is the part we can promise. Everything else on this page describes where your data
sits inside your own Claude account and on your own machine, which is governed by
Anthropic's terms, not ours.

## What is kept, and where

**In two artifacts in your Claude account,** created when you say "set up Rowkeep":

- **Your log** holds the rows you asked to have logged (or, if you turned automatic logging
  on for yourself, the actions Claude recorded as you worked); an index derived from them;
  a copy of the list of verbs; the member list, one entry per person who logs to it, each
  holding that person's own choice about automatic logging; and the morning briefs, with
  their drafts, if you scheduled them.
- **Your vault** holds a copy of the sealed rows.

A row holds: when; what happened; to what; a short label; a status; details; who did it,
by the name used for them; where it was written from; and the id your Claude account has on
that artifact. Rows about clients hold what you logged about them: company names, the names
of people there with their employer and role, the names used for owners of accounts and
deals, amounts, next steps, and free text. The skill tells Claude to keep email addresses,
phone numbers and home addresses out.

**In your conversations.** When a chat seals rows or copies them to the vault, the
documents pass through that conversation as text. When any session logs or answers, what
it read and wrote is in that conversation.

**In working files.** The script works on files: rows about to be stored, read results,
plans. They are written to a scratch folder in the session's sandbox, or on your own
machine when you run Claude Code there, and are not cleaned up by Rowkeep.

**In your personal skill file,** if setup made one from Claude Code
(`rowkeep-personal.skill`): the links of your log and your vault. A team file
(`rowkeep-team.skill`) holds the log's link only. The links give no access by themselves;
access is what you grant in each artifact's Share menu. Do not publish either file.

**In a scheduled session,** if you said yes to one: its prompt names the link of your log,
your time zone, what you agreed the session may do (whose waiting rows it seals, and
whether it copies to the vault), and the link of your vault if it copies to it.

## What is not kept

The skill instructs Claude not to log conversations, transcripts or summaries of chats, not
to read its memory of you or your past chats, and to keep email addresses, phone numbers and
home addresses out of rows. The script drops fields named like credentials and masks text
shaped like common tokens. These are instructions and a filter, not guarantees: what you ask
to have logged is logged.

## Who can see it

Only you, unless you share an artifact.

When you share your log, **you own it**: the artifact stays in your account. Everyone you
share it with can read every sealed row, the index, the member list (including whether each
member has automatic logging on), and the briefs with their drafts. A contributor can also
add rows of their own. Each person's waiting rows are readable by that person and by the
owner and editors. Reading your own waiting rows needs at least the Contributor role: with
Viewer or Commenter you can read what is sealed, and have no waiting rows of your own to
read or write. Only the owner and editors can seal, and they can also change or delete
anything in the log's database (from version 0.7.1, as the rules read, anything but
another person's member entry): the skill never does, but the access is theirs.

The vault is readable only by you and anyone you make an editor of it. Do not share it.

Sharing is yours to control, from each artifact's Share menu.

## How long it is kept, and how to remove it

Nothing here expires by itself. To remove:

| What | How |
|---|---|
| The log and everything in it | Delete the artifact titled `Rowkeep` |
| The copy of the rows | Delete the artifact titled `Rowkeep Vault` |
| Conversations in which rows were logged, sealed or read | Delete those conversations |
| Working files | Delete the scratch folder on your machine; a sandbox's files go when the sandbox does |
| The installed plugin or the uploaded skill | In Claude Code, uninstall the plugin from the `/plugin` menu; in claude.ai, remove the skill under Customize, then Skills |
| Your personal skill file, and a team skill file | Delete the files, wherever setup or you copied them; ask teammates to delete theirs |
| The scheduled session | Delete it from your scheduled sessions |

The workflow has no way to erase one row, and the skill never edits one: a correction is a
new row. So do not log anything that someone may have a right to have deleted. (The owner
and editors of the artifact do hold the database itself; removing a row by hand breaks the
fingerprint chain from that row on, which is what the chain is for.)

Deleting the two artifacts removes what Rowkeep stored in them. It does not remove the
conversations, the working files, the plugin or skill, a skill file or a scheduled
session: those are the other rows of the table above. Anthropic's own terms and retention apply to artifacts and
conversations, as to anything else in your account, including for how long something
deleted is kept.

## What leaves your account

Nothing goes to Rowkeep's authors or to any service of ours: there is none. The script
opens no network connection, and the two pages load no outside code, fonts or data.
Rowkeep sends no telemetry to its authors; what you put in a support or security report
goes to the channel you choose. An issue on the repository is public, so leave out your
log's link and anything from your rows.

The skill does use Claude's own tools, inside your account: to list your artifacts (to find
your log), to read and write the two artifacts' databases, and, during setup only, to
publish the two pages. The two pages run JavaScript in the browser of whoever opens them;
the log's page asks for the viewer's account id on that artifact, to show them their own
waiting rows, and for nothing else about them. If you schedule the morning session, it runs
in your account with the database tool and the shell, no connectors and no mail, and sends
nothing.

## Children

Rowkeep is not intended for anyone under 18.

## Questions

Open an issue on the repository.
