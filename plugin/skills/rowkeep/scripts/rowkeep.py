#!/usr/bin/env python3
"""rowkeep: the command-line half of the Rowkeep skill.

The assistant talks to the database; this script never does. It reads JSON documents the
assistant saved to files, checks and computes, and writes the documents and the list of
writes for the assistant to send. Everything it reads may have been written by someone
else, so it is treated as untrusted. It uses the standard library only and never opens a
network connection.

The activity stream: rows, the verb registry, the fingerprint chain and the tallies. The
format is specified in docs/ACTIVITY-STREAM.md, in Rowkeep's repository. `rowkeep help`
describes each command, with its options, the exit codes and the rules about files.

Three rules run through all of it. A stored row never changes, and a log is replayed as
it was written: what a new row must pass is checked at the door (`row`, the sequence
commands, `seal`), never against history. A command says only what it verified, and where
it could not verify something it was asked to, it does not end cleanly. And nothing that
came from a document is used as a path, or printed, as it stands.

This script replaced a Rust program, which is no longer shipped, and existing logs hold
fingerprints that program computed. Where something here looks more particular than it needs to be (numbers
kept as floats, lengths counted in UTF-8 bytes, a JSON reader written out by hand), it is
because the result must be the same, byte for byte, as what that program wrote.
"""

import errno
import hashlib
import math
import os
import re
import stat
import struct
import sys
import time
import traceback
import unicodedata
import zlib
from bisect import bisect_right
from collections import namedtuple
from decimal import Decimal

VERSION = "0.7.1"

USAGE = """\
rowkeep: an append-only activity stream kept in an artifact's database.
The format is described in docs/ACTIVITY-STREAM.md, in Rowkeep's repository. Every command
reads the verb registry from --registry FILE, or from the ROWKEEP_REGISTRY environment
variable, or else from the copy bundled with the skill, beside this script.

OPTIONS. A command takes the options listed for it and no others: an option it does not
have, an option whose value is missing, and an option given twice are each refused, and
nothing is done. A value cannot start with "--".
Every option that takes a value can be given as --NAME-file PATH instead: the value is
read from the file, less one newline at its end. Text that came out of a row, or from
anywhere else you did not type yourself, is passed this way and never placed in a command
line.

EXIT CODES. 0: done. 1: a check found problems, a confirm could not confirm, or a seal
could seal none of what was waiting. 2: an error, or something this script refuses to do;
it says why on one line starting "rowkeep:". 3 (from the launcher): no Python 3.8 or
later that runs. 4: whoever was reading the output stopped reading before the command had
finished, so its verdict was not delivered.

OUTPUT. The last line a command prints states the outcome, with any override in force
(REWIND, PARTIAL RESTORE) and any rows skipped, left or remaining; a plan holds the same
under "override", "skipped", "left" and "remaining".

FILES. No command writes over a file: give --out a new, empty folder each time. Nothing is
read or written through a link inside a folder you give. A document is found by its exact
name: a folder holding two names that differ only in capitals is refused.

rowkeep verbs
    List every registered verb: name, default status, required payload fields, description.

rowkeep row --out DIR --verb V --entity TYPE:ID --source chat|code|page|system|import --actor NAME
            [--subject TEXT] [--payload JSON] [--status ok|fail|pending|info]
            [--key TEXT] [--ts 2026-11-22T19:05:01+00:00]
rowkeep row --out DIR --from FILE
    Check one row against the registry and write it as an inbox document.
    Prints {"doc_id": ..., "file": ..., "data": {...}}: store "data" under "doc_id".
    --key makes a repeat of the same event harmless. --ts records an event that happened
    earlier than now (UTC, exactly that form); without it the current time is used.
    --from FILE gives the whole row as one JSON object, with the fields the options name:
    {"verb", "entity", "subject", "status", "source", "actor", "key", "ts", "payload"}.
    A field the verb adds up must be a whole number; a field it counts must be text, or
    true or false; a field the registry lists as a date must be a real date, 2026-11-27.

rowkeep match --name TEXT [--type TYPE] [--ix DIR | --candidates JSON] [--min 85]
    Say which existing record a name refers to. Alone, it prints the name's matching key,
    the query that finds records carrying it, and an id to use if the record is new.
    Given that type's entity documents (a folder saved from collection ix, which must
    exist, or a JSON array of them, each needing only "entity" and "names"), it adds a
    verdict: "use" (exactly one record has this name), "ask" (near matches, or several, or
    something a person has to settle, given under "why"), or "new". A candidate may also
    be a row still waiting in an inbox, whole and as it is. A candidate that is neither,
    or a row seal would not take, is listed under "ignored", and the verdict is then "ask".

SEQUENCES: ordered steps a target is taken through (an outreach cadence, a renewal run-up).
Each prints what `row` prints, plus "entity" and, where the row can only happen once,
"marker": the document in collection ix that exists once that row has been sealed.
All take --out DIR --sequence ID --source ... --actor NAME [--subject TEXT] [--what TEXT]
[--on 2026-11-27] (the day the thing happened; today in UTC if left out).

rowkeep seq-define --steps JSON
    Define or revise a sequence. Steps: [{"channel": "email", "wait_days": 0, "what": "Intro"}, ...]
    where wait_days is whole days after the step before it (for step 1, after enrolling).
    --steps accepts the list, or the whole sequence_defined row as read from the stream.
rowkeep seq-enroll --target TYPE:ID --steps ... [--owner NAME]
    Start one target on the sequence; works out the first step and its due date.
rowkeep seq-step --target TYPE:ID --steps ... --step N
    Record that step N was done; works out the next step and its due date, or finishes.
    Give the steps the target was enrolled with: a step built from a list of another
    length is not sealed.
rowkeep seq-end --target TYPE:ID --outcome replied|booked|stopped|bounced|unsubscribed|no_reply --step N
    Stop the sequence for that target. N is how many steps had been done.
    A sequence row is sealed only as a valid next move for its enrollment: a step after
    the sequence has ended, or out of order, is left in the inbox with the reason. A row
    whose once-only key is sealed already is a repeat, and is dropped like any other.

SEALING: giving waiting rows their place in the chain.

rowkeep seal-keys --inbox DIR [--max 20] [--inline] [--leave DOC_ID,...]
    Say which documents of collection ix the next seal must read (query: k in [...]).
    Give --inline and --leave here whenever the seal that follows will be given them.

rowkeep seal --inbox DIR --ix DIR --out DIR --account USER_ID
             [--versions head=7,v.file_edited=3,...] [--inbox-versions DOC_ID=2,...] [--max 20]
             [--inline] [--leave DOC_ID,...] [--inbox-collection PATH]
    Give inbox rows their ids and fingerprints and update the tallies. Writes the documents
    and plan.json under --out; send plan.json's "batch" as one batch. --versions gives the
    version of every ix document that was read. --inbox-versions is needed only for inbox
    documents whose version is not 1.
    Rows are taken oldest first: by ts, then by the millisecond they were written (at),
    then, for rows written in the same millisecond, in the order of their document ids.
    A row that is not valid is skipped and stays in the inbox; so is one that would take a
    total out of range or make an index document larger than 200,000 bytes (one an older
    log holds over that is rewritten only at its size or below, with a note). If documents
    were waiting and none can be sealed, it prints NO PROGRESS, plans nothing and exits 1;
    --leave names inbox documents to pass over, so that the rows behind them are sealed.
    --inline is for a session whose database tool cannot pass files. It also writes
    plan-inline.json, the same writes with each document inside its entry: send its "ops"
    as one write. It prints what ix/head must hold afterwards, to be read back and compared.
    Because such a session carried every document here by hand, --inline takes only
    documents that hold a checksum (an inbox row without one is skipped, and an ix document
    without one stops the seal), and only folders `ingest` filled: --inbox from a read of
    this account's inbox, --ix from a whole read of collection ix, an empty result
    included. The versions are taken from what ingest recorded; given as well, they must
    agree.

rowkeep ingest --from FILE --out DIR [--collection PATH] [--id DOC_ID] [--page N]
    For a session whose database reads come back as text. FILE holds one read result, saved
    exactly as it was returned, in one of four layouts and no other: the tool's own object
    and then one document on each line inside a tag; one object holding the documents as a
    list under "docs", with its cursor; a JSON array of documents; or one document. With
    --id it is that one document: the tool's wrapper around it, or its bare contents,
    whole, or {"exists": false} where the read found none. A count the result gives must
    be the number of documents in it; an entry that names its id or its version twice and
    differently is refused, and so is every entry of an id given twice. Writes each
    document's contents to DIR/<document id>.json, as reads that save files do, and prints
    {"documents": ..., "refused": [...], "damaged": [...], "unchecked": ..., "versions":
    "head=7,...", "complete": true|false, "collection": ..., "page": ...}. "versions" is
    ready to pass to seal as --versions or --inbox-versions. "refused" documents were not
    written; "damaged" ones hold a checksum that does not match their contents, and
    whatever reads them next will say so. DIR must be new or empty.
    The collection comes from the documents' paths, or else from --collection, which an
    empty result always needs. A result that ends with a cursor is one page: read the next
    and ingest it into the same DIR with --page 2, and so on.
    It also writes DIR/_read.manifest (one for each page): the collection, each document's
    id, version and the SHA-256 of its file, and whether the read was complete, which only
    a read from page 1 to a page with an empty cursor is. Every command that reads DIR
    holds its files to it. It shows which read the files are of and whether that read was
    whole, never that it was current.

rowkeep page --from FILE --out FILE
    Make a bundled page a whole HTML document. The pages in setup/ are the part inside
    one, which is what Claude Code's artifact tool takes; a tool that publishes a file as
    it stands needs the document type and character set on top. Writes --out, which must
    not exist yet, and changes nothing else.

rowkeep confirm --plan FILE --read DIR
    For a session that sent a plan's "ops" by hand. FILE is the plan-inline.json that was
    sent; one with no ops is refused. DIR holds what the database holds now: each
    collection the plan wrote to, read back and passed through ingest into
    DIR/<collection>, so that a document sits at DIR/<its path>.json. Every document the
    plan set must be there, identical to the plan and true to its own checksum. A document
    the plan removed counts as gone only where ingest recorded a whole read of its
    collection, or a read of that one document (--id) that found none. Ends "as planned"
    (exit 0); or lists each PROBLEM (exit 1); or, when nothing is wrong and a removal could
    not be checked, lists each as NOT CONFIRMED and ends "not confirmed" (exit 1).

rowkeep check --stream DIR [--ix DIR] [--partial]
    Replay rows: each in the file its id names, ids from 1 without gaps, every 'previous',
    every fingerprint, the filterable copies, the tip, and a recount of every index
    document; any other document in the ix folder is a problem, and so is being given no
    rows. Ends "chain intact" or lists each problem. Give every row. Without --ix the index
    is not checked, and it ends "rows intact, index not checked".
    --partial says you gave only the newest part on purpose. It checks the rows given
    against each other, and the checksum of each index document given; it does not recount
    the index and cannot speak for earlier rows, and it says so: it ends "This was not a
    full check; no problems in what was given", never "chain intact".

rowkeep rebuild --stream DIR --ix DIR --out DIR [--versions head=7,...] [--checksums] [--rewind]
    Recompute every index document from the rows. The rows are verified first, exactly as
    check verifies them, and rows check would report are refused: rebuild writes only the
    index and cannot put a row right. Plans a write of the index documents that differ and
    the removal of any other document in the ix folder; one that is right is left alone
    whatever its size. --checksums also rewrites index documents that hold no checksum
    yet, which is how a log written before version 0.4.0 gains them. If the index given
    puts the tip past the last row given, rows are missing and it refuses; --rewind says
    the log really was cut back, and the tip is to move back.

THE VAULT: a second artifact holding a copy of every sealed row (see ops/VAULT.md in the skill).
A tip (vault/head) names itself head, names a row from 1 on by its fingerprint and counts
as many rows as that row's id; anything else given as one is refused or reported.

rowkeep vault-plan --stream DIR --out DIR --source LOG_LINK
                   [--vault-head FILE (--head-version N | --versions head=N)] [--inline] [--max N]
                   [--moved]
    Plan a copy of the log's new rows into the vault. DIR holds the log's rows from the
    vault's tip onward. A vault whose tip names another log as its source is refused;
    --moved says this log was restored to a new link, and the tip is to name it from now on. The row at the vault's tip must be the row the vault holds, and
    every new row must carry the fingerprint of its own contents and chain on from it:
    otherwise it refuses, with ALARM, and plans nothing. It says what it verified: that
    anchor row and the new rows. It does not compare earlier rows with the vault
    (vault-check does), though any that are given must hold together. Writes the documents
    and plan.json under --out; send plan.json's "batch" as one batch, to the vault.
    --inline is for a session whose database tool cannot pass files. It plans one small
    document of rows (at most 10 rows and 6,000 bytes) and also writes plan-inline.json:
    send its "ops" as one write, to the vault, then read both documents back and run
    confirm. More rows than that take another round. Where the tip was passed through
    ingest (from a read of collection vault), its version is taken from what ingest recorded.
rowkeep vault-check --rows DIR --vault-head FILE [--stream DIR] [--partial]
    Check every row the vault holds, each chunk's name and checksum, and the tip against
    them; with --stream, check the log's rows too, every one, and compare them with the
    vault: every row in the vault must be in the log, unchanged. Ends "vault intact" or
    lists each problem. --partial says the stream folder holds only part of the log: the
    first row given must be one the vault holds or follow one it holds, and point at it;
    it ends "partial check: ...", saying which rows were compared, never "vault intact".
rowkeep vault-restore --rows DIR --vault-head FILE --out DIR [--partial]
    Turn the vault's rows back into the documents of collection stream, in batches of 50.
    The tip says how many rows the vault holds; rows that stop short of it are refused.
    --partial restores the rows given all the same and ends PARTIAL RESTORE, the range,
    and that the vault held more. Follow with rebuild to derive the index.

SETUP (see setup/SETUP.md in the skill).

rowkeep first-docs --out DIR --account USER_ID [--auto-log yes|no] [--announce yes|no] [--inline]
    Write the two documents a new log starts with: state/verbs (the verb registry) and the
    user's own member document, members/USER_ID, which holds {"member": true, "auto_log":
    ..., "announce": ...} (auto_log no unless given; announce yes unless given). Each
    person's choices are in their own member document; one that holds only {"member":
    true} means those defaults. Writes them and plan.json under --out; send plan.json's
    "batch" as one batch. --inline also writes plan-inline.json: send its "ops" as one
    write, then read the documents back and run confirm.
rowkeep personalize --skill DIR --out DIR --log LINK [--slug ID] [--vault LINK]
    Make an account's own copy of the skill, with its log named, as a folder and as
    rowkeep-personal.skill ready to upload. DIR is the skill folder as shipped. Only the
    files a skill ships are taken. See setup/SETUP.md.
rowkeep pack --skill DIR --out FILE
    Write the skill as shipped, naming no log, as one .skill file: the files a skill ships
    and no others, each file's bytes as they are (line ends included), the same bytes for
    the same files.

rowkeep today --zone NAME
    Print today's date, as 2026-11-27, in a time zone named like America/New_York. A name
    of another shape, or one this Python has no data for, is refused: it never answers in
    UTC for a zone it does not know.

rowkeep version
rowkeep help"""


STATUSES = ["ok", "fail", "pending", "info"]
SOURCES = ["chat", "code", "page", "system", "import"]
MAX_INT = 9007199254740991
# One seal is one database batch: at most 50 writes, and its read is one `in` query of at
# most 30 values.
MAX_WRITES = 50
MAX_KEYS = 30
MAX_VALUES = 200
HIDDEN = "[Hidden]"
MAX_PAYLOAD_BYTES = 16000
MAX_COUNTED_CHARS = 120
# The most one row may add to a summed field. In cents this is ten billion dollars.
MAX_SUMMED = 1000000000000
MAX_NESTING = 64
MAX_FILE_BYTES = 4 << 20
# The database takes a document of up to 256 KiB. No plan this script writes holds a
# document larger than this, which leaves room for however the database counts.
MAX_DOC_BYTES = 200000

ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ASCII_DIGITS = "0123456789"
ASCII_ALNUM = ASCII_LOWER + ASCII_UPPER + ASCII_DIGITS
# str.lower() would also change letters outside ASCII, some into more than one character.
TO_ASCII_LOWER = str.maketrans(ASCII_UPPER, ASCII_LOWER)
# What Unicode calls White_Space. Python's own idea of whitespace (str.split, str.strip) also
# takes in the control characters U+001C to U+001F, which the Rust program did not.
WHITE_SPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004"
    "\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
WHITE_SPACE_RUN = re.compile("[" + WHITE_SPACE + "]+")


class Refused(Exception):
    """Something this tool will not do. Printed as `rowkeep: <why>`, with exit code 2."""


class OutputClosed(Exception):
    """Whoever was reading this script's output has stopped, as `| head` does."""


LINE_BREAKERS = re.compile("[\x00-\x08\x0a-\x1f\x7f]")


def one_line(line):
    """A message is one line. Text from a document is quoted with `show` wherever it is
    printed; this is the net under that, so that nothing printed can start a line of its
    own or drive a terminal. (A tab cannot, and `verbs` separates its columns with tabs.)"""
    return LINE_BREAKERS.sub(lambda found: "\\u%04x" % ord(found.group(0)), line)


def say(line):
    """Print one line as UTF-8 with a plain newline, whatever the system's defaults are."""
    try:
        sys.stdout.buffer.write((one_line(line) + "\n").encode("utf-8", "replace"))
        sys.stdout.buffer.flush()
    except OSError as error:
        # A closed pipe is EPIPE on Linux and EINVAL on Windows.
        if isinstance(error, BrokenPipeError) or error.errno in (errno.EPIPE, errno.EINVAL):
            raise OutputClosed()
        raise


def warn(line):
    try:
        sys.stderr.buffer.write((one_line(line) + "\n").encode("utf-8", "replace"))
        sys.stderr.buffer.flush()
    except OSError:
        pass  # nowhere left to complain to


def utf8_len(text):
    return len(text.encode("utf-8", "surrogatepass"))


def split_whitespace(text):
    return [word for word in WHITE_SPACE_RUN.split(text) if word]


def one_spaced(text):
    """The text with each run of whitespace as one space, and none at either end."""
    return " ".join(split_whitespace(text))


def trim(text):
    return text.strip(WHITE_SPACE)


def is_ascii_digit(c):
    return "0" <= c <= "9"


def parse_unsigned(text):
    """A whole number from 0 to 2^64 - 1 written in ASCII digits, or None. int() alone would
    also take spaces, underscores and digits of other scripts."""
    # The length is looked at before the digits are turned into a number: Python refuses to
    # convert a few thousand of them, and nothing here needs more than twenty.
    if text is None or len(text) > 21 or not re.fullmatch(r"\+?[0-9]+", text):
        return None
    value = int(text)
    return value if value < 2 ** 64 else None



# ---------- SHA-256 ----------


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def crc32(data):
    """CRC-32 as zip files use it."""
    return zlib.crc32(data) & 0xFFFFFFFF


def new_id(ms, random):
    """Sortable document id: 9 base36 digits of milliseconds, a dash, 6 random base36 digits."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    time_part = ""
    for _ in range(9):
        time_part = digits[ms % 36] + time_part
        ms //= 36
    random_part = ""
    for _ in range(6):
        random_part += digits[random % 36]
        random //= 36
    return time_part + "-" + random_part


# ---------- numbers ----------
#
# Every JSON number is held as a float, as the Rust program held it: `5.0` and `5` are then
# the same number, and both are written `5`.


def num(x):
    return float(x)


def is_number(v):
    return isinstance(v, float)


def is_whole(x):
    return math.isfinite(x) and x == math.floor(x)


def to_i64(x):
    """A number as a 64-bit integer: cut toward zero, held at the ends of the range, and 0
    for not-a-number. Totals read back from an index document go through this."""
    if x != x:
        return 0
    if x >= 9223372036854775808.0:
        return 9223372036854775807
    if x <= -9223372036854775808.0:
        return -9223372036854775808
    return int(x)


def float_text(x):
    """A number as it appears in an error message: its shortest exact form, never with an
    exponent, and without `.0` on a whole one."""
    if x != x:
        return "NaN"
    if x in (math.inf, -math.inf):
        return "inf" if x > 0 else "-inf"
    text = format(Decimal(repr(x)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# ---------- canonical JSON ----------

JSON_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}
JSON_MUST_ESCAPE = re.compile('["\\\\\x00-\x1f]')


def json_escape(found):
    c = found.group(0)
    return JSON_ESCAPES.get(c) or "\\u%04x" % ord(c)


def json_text(text):
    """A string literal exactly as Python's `json.dumps(..., ensure_ascii=False)` writes it."""
    return '"' + JSON_MUST_ESCAPE.sub(json_escape, text) + '"'


def canonical(v):
    """Sorted keys, no spaces, whole numbers only: the bytes a fingerprint is taken over."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        x = float(v)
        if not is_whole(x) or abs(x) > MAX_INT:
            raise Refused(f"{float_text(x)} is not a whole number in range; write decimals as text")
        return str(int(x))
    if isinstance(v, str):
        return json_text(v)
    if isinstance(v, list):
        return "[" + ",".join(canonical(item) for item in v) + "]"
    return "{" + ",".join(json_text(k) + ":" + canonical(v[k]) for k in sorted(v)) + "}"


def canonical_or_none(v):
    try:
        return canonical(v)
    except Refused:
        return None


def fingerprint(v):
    return sha256_hex(canonical(v).encode("utf-8"))


# ---------- checksums ----------
#
# Some sessions have no way to pass a file to or from the database: every document they read
# or write goes through the conversation, copied by the assistant. So that one miscopied
# character cannot go unnoticed, every document a later session has to read back (an inbox
# document, and every document of the index) holds a checksum of the rest of itself in the
# field `ck`. Sealed rows hold none: they have their fingerprints, and their bytes are the
# format.

CHECKSUM = "ck"
CHECKSUM_FORM = re.compile("[0-9a-f]{12}")
CHECKSUM_WRONG ="its checksum (ck) does not match its contents: it was changed or miscopied after it was written"


def without_checksum(doc):
    return {k: v for k, v in doc.items() if k != CHECKSUM}


def checksum(doc):
    """The first 12 hexadecimal digits of the SHA-256 of a document's canonical JSON, taken
    without the checksum field itself."""
    return fingerprint(without_checksum(doc))[:12]


def with_checksum(doc):
    out = without_checksum(doc)
    out[CHECKSUM] = checksum(doc)
    return out


def checksum_problem(doc):
    """Why a document cannot be trusted, or None. A document with no checksum is one written
    before they existed, and is taken as it is; one with a wrong checksum never is."""
    if not isinstance(doc, dict) or CHECKSUM not in doc:
        return None
    # A checksum is twelve lowercase hexadecimal digits and nothing else. Whatever else is
    # under that name (null, a number, other text) is a wrong checksum, not a missing one.
    if not isinstance(doc[CHECKSUM], str) or not CHECKSUM_FORM.fullmatch(doc[CHECKSUM]):
        return CHECKSUM_WRONG
    try:
        right = checksum(doc)
    except Refused:
        # It holds a number no document this script wrote could hold, so no checksum can be
        # worked out for it, and none it carries can be right.
        return CHECKSUM_WRONG
    return None if doc[CHECKSUM] == right else CHECKSUM_WRONG


# ---------- reading JSON ----------
#
# Python's json module is not used for reading. It accepts things the Rust program's reader
# refused (NaN, Infinity, a lone half of a surrogate pair), keeps `5.0` apart from `5`, and
# words its errors differently, and those errors are printed and stored. This reader makes
# the same decisions and gives the same messages, down to the line and column.


class JsonSyntaxError(Exception):
    pass


# No number a document of this log holds is written with more than seventeen digits and an
# exponent. A text longer than this is refused before it is turned into a number.
MAX_NUMBER_CHARS = 64


def exact_number(literal):
    """The float that a number's text names, or None if no float is exactly that value.
    `literal` is the text without its sign. Every number is still held as a float (see
    "numbers" above), but only where that changes nothing: 1.0000000000000001 is not 1,
    1e-999 is not 0 and 9007199254740993 is not 9007199254740992, and a reader that took
    them for those would give two different documents one fingerprint. A value that a
    float does hold exactly and that is not a whole number in range (1.5, 2^53) is read,
    and refused wherever a whole number is required, as it always was."""
    if len(literal) <= 15 and literal.isdigit():
        return float(literal)  # every whole number of fifteen digits is exact
    try:
        value = float(literal)
        if value in (math.inf, -math.inf) or Decimal(value) != Decimal(literal):
            return None
    except (ArithmeticError, ValueError):
        return None
    return value


STRING_RUN = re.compile('[^"\\\\\x00-\x1f]*')
SIMPLE_ESCAPES = {"\\": "\\", "/": "/", '"': '"', "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
JSON_SPACE = " \n\r\t"
CHAR_ESCAPES = {"\0": "\\0", "\t": "\\t", "\r": "\\r", "\n": "\\n", "\\": "\\\\", '"': '\\"'}


def char_in_message(c, quote_too=True):
    """A character quoted in an error message: escaped if printing it could mislead.
    The Rust program decided this from Unicode 18 tables; this goes by the tables of the
    Python that runs it, so a very new or very rare character may print differently."""
    if c in CHAR_ESCAPES:
        return CHAR_ESCAPES[c]
    if c == "'" and quote_too:
        return "\\'"
    if unicodedata.category(c) in ("Mn", "Me") or not c.isprintable():
        return "\\u{%x}" % ord(c)
    return c


def value_in_message(v):
    """A JSON value named in an error message, in the form the Rust program printed."""
    if v is None:
        return "Null"
    if isinstance(v, bool):
        return "Boolean(true)" if v else "Boolean(false)"
    if isinstance(v, float):
        text = repr(v)
        if "e" in text:
            mantissa, exponent = text.split("e")
            text = mantissa + "e" + str(int(exponent))
        return "Number(" + ("NaN" if text == "nan" else text) + ")"
    if isinstance(v, str):
        return "String(" + text_in_message(v) + ")"
    if isinstance(v, list):
        return "Array([" + ", ".join(value_in_message(item) for item in v) + "])"
    return "Object({" + ", ".join(text_in_message(k) + ": " + value_in_message(v[k]) for k in v) + "})"


def text_in_message(text):
    return '"' + "".join(char_in_message(c, False) for c in text) + '"'


class JsonReader:
    def __init__(self, text):
        self.text = text
        self.pos = 0  # how many characters have been taken

    def fail(self, message):
        taken = self.text[: self.pos]
        line = taken.count("\n") + 1
        col = self.pos - (taken.rfind("\n") + 1)
        raise JsonSyntaxError(f"Parse error at line:{line}, col:{col}: {message}")

    def here(self):
        """The next character without taking it, or "" at the end."""
        return self.text[self.pos : self.pos + 1]

    def skip_space(self):
        while self.here() != "" and self.here() in JSON_SPACE:
            self.pos += 1

    def peek(self):
        self.skip_space()
        if self.here() == "":
            self.fail("Unexpected EOF")
        return self.here()

    def take(self):
        c = self.here()
        if c == "":
            self.fail("Unexpected EOF")
        self.pos += 1
        return c

    def take_after_space(self):
        self.skip_space()
        return self.take()

    def take_digits(self):
        start = self.pos
        while is_ascii_digit(self.here()):
            self.pos += 1
        return self.text[start : self.pos]

    def read_document(self):
        value = self.read_any()
        self.skip_space()
        if self.here() != "":
            self.fail(f"Expected EOF but got character '{char_in_message(self.take())}'")
        return value

    def read_any(self):
        c = self.peek()
        if c == "-" or is_ascii_digit(c):
            return self.read_number()
        if c == '"':
            return self.read_string()
        if c == "[":
            return self.read_array()
        if c == "{":
            return self.read_object()
        if c == "t":
            return self.read_word("true", True)
        if c == "f":
            return self.read_word("false", False)
        if c == "n":
            return self.read_word("null", None)
        return self.fail("Invalid character: " + char_in_message(c))

    def read_word(self, word, value):
        for expected in word:
            if self.take() != expected:
                self.fail(f"Unexpected character '{expected}' while parsing '{word}'")
        return value

    def read_number(self):
        start = self.pos
        negative = self.here() == "-"
        if negative:
            self.pos += 1
        literal = self.take_digits()
        if literal == "":
            self.fail("Integer part must not be empty in number literal")
        if literal[0] == "0" and len(literal) > 1:
            self.fail("Integer part of number must not start with 0 except for '0'")
        if self.here() == ".":
            self.pos += 1
            fraction = self.take_digits()
            if fraction == "":
                self.fail("Fraction part of number must not be empty")
            literal += "." + fraction
        if self.here() in ("e", "E"):
            self.pos += 1
            literal += "e"
            if self.here() in ("+", "-"):
                literal += self.take()
            exponent = self.take_digits()
            if exponent == "":
                self.fail("Exponent part must not be empty in number literal")
            literal += exponent
        # The text is measured before it is turned into anything.
        if self.pos - start > MAX_NUMBER_CHARS:
            self.fail(f"A number is written with more than {MAX_NUMBER_CHARS} characters")
        value = exact_number(literal)
        if value is None:
            shown = ("-" if negative else "") + literal
            self.fail(f"The number {shown} cannot be held exactly: write a whole number, or write it as text")
        return -value if negative else value

    def read_string(self):
        self.take()  # the opening quote
        parts = []
        units = []  # \uXXXX escapes not yet turned into characters
        while True:
            c = self.take()
            if c == "\\":
                escape = self.take()
                if escape == "u":
                    unit = 0
                    for _ in range(4):
                        # Four hexadecimal digits, one after another. (The Rust reader let
                        # whitespace stand between them; no JSON writer puts any there.)
                        digit = self.take()
                        if digit not in "0123456789abcdefABCDEF":
                            self.fail(f"Unicode character must be \\uXXXX (X is hex character) format but found character '{char_in_message(digit)}'")
                        unit = unit * 16 + int(digit, 16)
                    # Another \uXXXX may follow: the two halves of a surrogate pair have to
                    # be joined before either means anything.
                    units.append(unit)
                    continue
                if escape not in SIMPLE_ESCAPES:
                    self.fail(f"'\\{char_in_message(escape)}' is invalid escaped character")
                c = SIMPLE_ESCAPES[escape]
            elif c == '"':
                self.join_units(parts, units)
                return "".join(parts)
            elif c < " ":
                self.fail("String cannot contain control character " + char_in_message(c))
            self.join_units(parts, units)
            parts.append(c)
            # The ordinary characters that follow, taken in one piece.
            run = STRING_RUN.match(self.text, self.pos)
            parts.append(run.group(0))
            self.pos = run.end()

    def join_units(self, parts, units):
        if not units:
            return
        try:
            parts.append(b"".join(struct.pack("<H", unit) for unit in units).decode("utf-16-le"))
        except UnicodeDecodeError:
            self.fail(f"Invalid UTF-16 sequence {units}: invalid utf-16: lone surrogate found")
        del units[:]

    def read_array(self):
        self.take_after_space()  # the opening bracket
        if self.peek() == "]":
            self.take()
            return []
        items = [self.read_any()]
        while True:
            c = self.take_after_space()
            if c == "]":
                return items
            if c != ",":
                self.fail(f"',' or ']' is expected for array but actually found '{char_in_message(c)}'")
            items.append(self.read_any())

    def read_object(self):
        self.take_after_space()  # the opening brace
        if self.peek() == "}":
            self.take()
            return {}
        members = {}
        while True:
            key = self.read_any()
            if not isinstance(key, str):
                self.fail("Key of object must be string but found " + value_in_message(key))
            c = self.take_after_space()
            if c != ":":
                self.fail(f"':' is expected after key of object but actually found '{char_in_message(c)}'")
            # The same key twice is two documents in one text: which one a reader sees
            # depends on the reader, so neither is taken.
            if key in members:
                self.fail("Duplicate key " + text_in_message(key[:80]) + " in object")
            members[key] = self.read_any()
            c = self.take_after_space()
            if c == "}":
                return members
            if c != ",":
                self.fail("',' or '}' is expected for object but actually found '" + char_in_message(c) + "'")


def deepest_nesting(text):
    # Brackets inside strings do not count, so a text with no more brackets than the limit
    # cannot be nested past it; only a text with many is walked character by character.
    if text.count("[") + text.count("{") <= MAX_NESTING:
        return 0
    depth = deepest = 0
    in_string = escaped = False
    for c in text:
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif c in "]}":
            depth = max(depth - 1, 0)
    return deepest


def parse(text):
    if utf8_len(text) > MAX_FILE_BYTES:
        raise Refused("larger than any database document can be")
    # The JSON reader recurses, so deep nesting is refused before it runs.
    deepest = deepest_nesting(text)
    if deepest > MAX_NESTING:
        raise Refused(f"nested {deepest} levels deep; the limit is {MAX_NESTING}")
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        return JsonReader(text).read_document()
    except JsonSyntaxError as why:
        raise Refused(f"not JSON: {why}")


def parse_flag(text, flag):
    """Parse JSON given for a flag, naming the flag in any complaint."""
    try:
        return parse(text)
    except Refused as why:
        raise Refused(f"{flag}: {why}")


# ---------- small JSON helpers ----------


def misleading(c):
    """Whether a character is escaped when text that came from somewhere else is printed:
    the controls, the Unicode line and paragraph separators (a terminal or a log viewer may
    start a new line at them), the marks that change the direction text is shown in (they
    can make a line read as something it is not), and a lone half of a surrogate pair."""
    code = ord(c)
    return (
        code < 0x20 or 0x7F <= code <= 0x9F or code in (0x2028, 0x2029, 0x061C, 0x200E, 0x200F, 0xFEFF)
        or 0x202A <= code <= 0x202E or 0x2066 <= code <= 0x2069 or 0xD800 <= code <= 0xDFFF
    )


def show(x, limit=80):
    """Untrusted text, made safe to print: quoted, cut at 80 characters (or `limit`), with
    anything that could break or reorder the line escaped. Whoever reads this program's
    output may be another program acting on it. Every value that came from a document, a
    file name or an argument goes through this before it is printed."""
    if not isinstance(x, str):
        x = "(not text)"
    out = '"'
    for c in x[:limit]:
        if c == '"':
            out += '\\"'
        elif c == "\\":
            out += "\\\\"
        elif misleading(c):
            out += "\\u%04x" % ord(c)
        else:
            out += c
    out += '"'
    if len(x) > limit:
        out += "..."
    return out


def show_path(path):
    """A path the caller gave, or one built from it, as a message prints it. A path is
    printed to be used, so an ordinary one is printed as it is; one that holds a quote or
    anything `show` would escape goes through `show`."""
    path = str(path)
    if len(path) <= 600 and not any(misleading(c) or c == '"' for c in path):
        return path
    return show(path, 600)


def display_json(v):
    """A value as JSON for a line this script prints that is not a stored document: the same
    value as `canonical` gives, with the characters `show` escapes written as \\uXXXX, which
    any JSON reader turns back into the same text."""
    if isinstance(v, str):
        return "".join("\\u%04x" % ord(c) if misleading(c) and ord(c) >= 0x7F else c for c in json_text(v))
    if isinstance(v, list):
        return "[" + ",".join(display_json(item) for item in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(display_json(k) + ":" + display_json(v[k]) for k in sorted(v)) + "}"
    return canonical(v)


def unwrap_data(v):
    """A database read may wrap a document as `{id, data, version}`."""
    if isinstance(v, dict) and isinstance(v.get("data"), dict) and isinstance(v.get("id"), str):
        return v["data"]
    return v


def as_obj(v):
    if not isinstance(v, dict):
        raise Refused("expected a JSON object")
    return v


def text_of(m, key):
    x = m.get(key)
    return x if isinstance(x, str) else None


def num_of(m, key):
    x = m.get(key)
    if is_number(x) and 0 <= x <= MAX_INT and is_whole(x):
        return int(x)
    return 0


def list_of(m, key):
    x = m.get(key)
    return [item for item in x if isinstance(item, str)] if isinstance(x, list) else []


def counts_of(m, key):
    """`{name: count}` maps, used where the names come from a fixed, safe vocabulary."""
    x = m.get(key)
    return {name: num_of(x, name) for name in x} if isinstance(x, dict) else {}


def counts_json(counts):
    return {name: num(count) for name, count in counts.items()}


def values_of(v):
    """`[{v, n}]` lists, used where the names are free text (a payload value, an actor).
    Each comes back as a `[value, count]` pair."""
    if not isinstance(v, list):
        return []
    return [[text_of(x, "v"), num_of(x, "n")] for x in v if isinstance(x, dict) and text_of(x, "v") is not None]


def values_json(pairs_list):
    return [{"v": value, "n": num(count)} for value, count in pairs_list]


def bump(pairs_list, value):
    for pair in pairs_list:
        if pair[0] == value:
            pair[1] += 1
            break
    else:
        if len(pairs_list) < MAX_VALUES:
            pairs_list.append([value, 1])
        else:
            for pair in pairs_list:
                if pair[0] == "(other)":
                    pair[1] += 1
                    break
            else:
                pairs_list.append(["(other)", 1])
    pairs_list.sort(key=lambda pair: (-pair[1], pair[0]))


# ---------- names ----------


def is_key(x):
    return x != "" and x[0] in ASCII_LOWER and len(x) <= 41 and all(c in ASCII_LOWER + ASCII_DIGITS + "_" for c in x[1:])


def is_verb(x):
    return len(x) >= 2 and is_key(x)


def is_entity(x):
    kind, colon, ident = x.partition(":")
    if colon == "":
        return is_key(x) and len(x) <= 32
    return is_key(kind) and len(kind) <= 32 and 1 <= len(ident) <= 160 and all(c in ASCII_ALNUM + "_.+/@-" for c in ident)


def kind_of(entity):
    """The part of an entity before the colon, or all of it when there is none."""
    return entity.partition(":")[0]


def is_actor(x):
    return 2 <= len(x) <= 40 and all(c in ASCII_ALNUM + "_.-" for c in x)


def is_ts(x):
    return ts_secs(x) is not None


def ts_secs(x):
    """Seconds since 1970 for a `2026-11-22T19:05:01+00:00` time; None unless it has exactly
    that form and names a real moment."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00", x):
        return None
    days = parse_date(x[:10])
    if days is None:
        return None
    h, m, s = int(x[11:13]), int(x[14:16]), int(x[17:19])
    if h < 24 and m < 60 and s < 60:
        return days * 86400 + h * 3600 + m * 60 + s
    return None


def too_far_ahead(ts):
    """A row dated more than a day from now has a wrong clock or is an attempt to crowd out
    the per-day counts, which keep only the newest days."""
    t = ts_secs(ts)
    return t is not None and t > now_secs() + 86400


def now_ms():
    """The clock, in milliseconds since 1970. ROWKEEP_TEST_NOW_MS fixes it, for tests only,
    so that two implementations of this tool can be compared byte for byte."""
    fixed = parse_unsigned(os.environ.get("ROWKEEP_TEST_NOW_MS"))
    if fixed is not None:
        return fixed
    return time.time_ns() // 1000000


def now_secs():
    return now_ms() // 1000


def iso_utc(secs):
    """`2026-11-22T19:05:01+00:00`, as Python's `isoformat(timespec="seconds")` gives for
    UTC. The day arithmetic here and in `days_from_civil` follows Howard Hinnant's published
    algorithms for civil dates (https://howardhinnant.github.io/date_algorithms.html).
    datetime is not used because it stops at the year 9999 and the Rust program did not."""
    days, rem = secs // 86400, secs % 86400
    z = days + 719468
    era, doe = z // 146097, z % 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = yoe + era * 400 + (1 if m <= 2 else 0)
    return "%04d-%02d-%02dT%02d:%02d:%02d+00:00" % (y, m, d, rem // 3600, rem % 3600 // 60, rem % 60)


# ---------- credentials ----------

# Payload keys that name a credential: one of these words, or ending in `_` plus one. A key
# that merely contains such a word (`tokens_used`, `secret_santa`) is left alone.
SECRET_KEYS = [
    "password", "passwd", "pwd", "passphrase", "secret", "token", "api_key", "apikey", "access_key", "secret_key", "private_key",
    "authorization", "cookie", "credential", "credentials", "session_id",
]
# Known token prefixes, and the least length of the random-looking run after each.
TOKEN_PREFIXES = [
    ("sk-", 20), ("sk_live_", 10), ("sk_test_", 10), ("rk_live_", 10), ("pk_live_", 10), ("ghp_", 20), ("gho_", 20), ("ghs_", 20),
    ("ghu_", 20), ("ghr_", 20), ("github_pat_", 20), ("xoxb-", 10), ("xoxp-", 10), ("xoxa-", 10), ("xoxs-", 10), ("glpat-", 15),
]
CLOUD_KEY_PREFIXES = [("AKIA", 16), ("ASIA", 16), ("AIza", 30)]
# `password: x`, `api_key=x` and the like, and the least length of a value worth hiding.
NAMED_SECRETS = [
    ("password", 4), ("passwd", 4), ("pwd", 4), ("passphrase", 4), ("api_key", 12), ("apikey", 12), ("access_token", 12),
    ("client_secret", 12), ("secret", 12), ("token", 12),
]


def secret_name(key):
    k = key.translate(TO_ASCII_LOWER)
    return any(k == word or k.endswith("_" + word) for word in SECRET_KEYS)


def found_at(text, needle):
    """Where `needle` occurs in `text`, left to right, never overlapping."""
    places = []
    at = text.find(needle)
    while at >= 0:
        places.append(at)
        at = text.find(needle, at + len(needle))
    return places


def run_of(text, start, allowed):
    """How many characters from `start` on pass the test `allowed`."""
    end = start
    while end < len(text) and allowed(text[end]):
        end += 1
    return end - start


def is_token_char(c):
    return c in ASCII_ALNUM + "+/_=-."


def is_gap_char(c):
    return c in " \"'"


def is_value_char(c):
    return c not in WHITE_SPACE and c not in "&\"',;"


def looks_made_up(value):
    """Whether a word looks like something generated and not like a word of a sentence: it
    holds a digit, or a capital somewhere after its first letter, or one of the signs
    passwords are padded with. "hunter2" and "dXNlcjpwYXNz" do; "reset" and "Training" do
    not. It decides only what follows a word such as `password:`, where an ordinary word is
    as likely as a secret."""
    return any(is_ascii_digit(c) or c in "!@#$%^*~+=" for c in value) or any(c in ASCII_UPPER for c in value[1:])


def secret_spans(text):
    """The parts of a text that have the shape of a credential, as (start, end) ranges. This
    goes by shape (a known prefix and a long random-looking run, a header, a key block), not
    by English words, and it is a net, not a guarantee: the skill tells the writer to keep
    secrets out in the first place."""
    lower = text.translate(TO_ASCII_LOWER)  # same positions as `text`
    spans = []

    def starts_word(i):
        return i == 0 or text[i - 1] not in ASCII_ALNUM + "_-"

    def random_looking(start, end):
        return any(is_ascii_digit(c) for c in text[start:end])

    # Known token prefixes, then a long run that holds at least one digit.
    for prefix, least in TOKEN_PREFIXES:
        for i in found_at(lower, prefix):
            start = i + len(prefix)
            length = run_of(text, start, is_token_char)
            if starts_word(i) and length >= least and random_looking(start, start + length):
                spans.append((i, start + length))
    # Cloud keys, which are case-sensitive.
    for prefix, least in CLOUD_KEY_PREFIXES:
        for i in found_at(text, prefix):
            length = run_of(text, i + 4, is_token_char)
            if starts_word(i) and length >= least:
                spans.append((i, i + 4 + length))
    # A bearer token, wherever it is quoted.
    for i in found_at(lower, "bearer"):
        gap = run_of(text, i + 6, lambda c: c in " \t")
        start = i + 6 + gap
        length = run_of(text, start, is_token_char)
        if starts_word(i) and gap > 0 and length >= 20 and random_looking(start, start + length):
            spans.append((start, start + length))
    # An authorization header, by its structure: the word, a colon or an equals sign, a
    # scheme (Basic, Digest, Token, ...), and then the credentials, which are hidden. The
    # word in a sentence ("authorization training") has no such structure, and credentials
    # that read as an ordinary word are left alone.
    for i in found_at(lower, "authorization"):
        if i > 0 and text[i - 1] in ASCII_ALNUM + "_":
            continue
        sign = i + 13 + run_of(text, i + 13, is_gap_char)
        if text[sign : sign + 1] not in ("=", ":"):
            continue
        scheme = sign + 1 + run_of(text, sign + 1, is_gap_char)
        scheme_length = run_of(text, scheme, lambda c: c in ASCII_ALNUM + "_-")
        gap = run_of(text, scheme + scheme_length, lambda c: c in " \t")
        start = scheme + scheme_length + gap
        length = run_of(text, start, is_token_char)
        if 0 < scheme_length <= 20 and gap > 0 and length >= 8 and looks_made_up(text[start : start + length]):
            spans.append((start, start + length))
        elif gap == 0 and scheme_length >= 20 and random_looking(scheme, scheme + scheme_length):
            spans.append((scheme, scheme + run_of(text, scheme, is_token_char)))
    # A signed web token: three base64 parts joined by dots.
    for i in found_at(text, "eyJ"):
        length = run_of(text, i, lambda c: c in ASCII_ALNUM + "_-.")
        if starts_word(i) and length >= 30 and text[i : i + length].count(".") >= 2:
            spans.append((i, i + length))
    # A private key block, from its BEGIN line to the end of the END line that matches it,
    # and no further. Without one, everything after BEGIN is hidden.
    for i in found_at(text, "-----BEGIN "):
        closes = text.find("-----", i + 11)
        label = text[i + 11 : closes] if closes >= 0 else ""
        if "PRIVATE KEY" in label and len(label) <= 48:
            ending = "-----END " + label + "-----"
            end = text.find(ending, closes)
            spans.append((i, len(text) if end < 0 else end + len(ending)))
        elif closes < 0 and "PRIVATE KEY" in text[i : i + 60]:
            spans.append((i, len(text)))
    # A user name and password inside a link.
    for i in found_at(text, "://"):
        length = run_of(text, i + 3, lambda c: c not in WHITE_SPACE and c not in "/@")
        if text[i + 3 + length : i + 4 + length] == "@" and ":" in text[i + 3 : i + 3 + length]:
            spans.append((i + 3, i + 3 + length))
    # `password: x`, `api_key=x` and the like: the value, where it looks like one.
    for name, least in NAMED_SECRETS:
        for i in found_at(lower, name):
            sign = i + len(name) + run_of(text, i + len(name), is_gap_char)
            if text[sign : sign + 1] not in ("=", ":"):
                continue
            start = sign + 1 + run_of(text, sign + 1, is_gap_char)
            length = run_of(text, start, is_value_char)
            # The least length is in UTF-8 bytes, as the Rust program counted it.
            # A long run with no gap in it is not a word of a sentence either.
            value = text[start : start + length]
            if starts_word(i) and utf8_len(value) >= least and (looks_made_up(value) or len(value) >= 24):
                spans.append((start, start + length))
    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def secret_text(text):
    return len(secret_spans(text)) > 0


def mask(text):
    """The text with each credential-shaped part replaced by `[Hidden]`."""
    out, at = "", 0
    for start, end in secret_spans(text):
        out += text[at:start] + HIDDEN
        at = end
    return out + text[at:]


def scrub(v):
    """Drop fields named like credentials and mask the parts of values that look like one."""
    if isinstance(v, dict):
        return {k: scrub(val) for k, val in v.items() if not secret_name(k)}
    if isinstance(v, list):
        return [scrub(item) for item in v]
    if isinstance(v, str):
        return mask(v)
    return v


def dropped_names(v, out):
    """The names of fields `scrub` drops, at any depth, for telling the writer what happened."""
    if isinstance(v, dict):
        for k, val in v.items():
            if secret_name(k):
                out.append(k)
            else:
                dropped_names(val, out)
    elif isinstance(v, list):
        for item in v:
            dropped_names(item, out)


def check_payload(v, depth):
    if depth > 6:
        raise Refused("payload is nested too deeply")
    if isinstance(v, dict):
        for k, val in v.items():
            if not is_key(k):
                raise Refused(f"payload key {show(k)} must be lowercase letters, digits and _")
            check_payload(val, depth + 1)
    elif isinstance(v, list):
        for item in v:
            check_payload(item, depth + 1)
    elif is_number(v) and (not is_whole(v) or abs(v) > MAX_INT):
        raise Refused(f"{float_text(v)} is not a whole number; write decimals as text, money as whole cents")
    elif isinstance(v, str) and len(v) > 2000:
        raise Refused("a payload text is longer than 2000 characters")


# ---------- the registry ----------

# `current`: payload fields kept on the entity as its current details (`*`: every plain one).
# `names`: payload fields that hold another name for the entity.
# `dates`: payload fields that hold a calendar date, written `2026-11-27`.
Verb = namedtuple("Verb", "description required status group tally sum index current names dates")
FIELD_LISTS = ["required", "tally", "sum", "index", "current", "names", "dates"]


def field_list(spec, key, verb):
    """One of a verb's lists of payload fields. A list that names a field twice is refused:
    a field listed twice under `sum` would be added twice to every total."""
    listed = spec.get(key)
    if listed is None:
        return []
    if not isinstance(listed, list) or not all(isinstance(f, str) for f in listed):
        raise Refused(f'verb {show(verb)}: "{key}" must be a list of field names')
    for f in listed:
        if not (is_key(f) or (key == "current" and f == "*")):
            raise Refused(f"verb {show(verb)} names a field {show(f)} that is not lowercase letters, digits and _")
        if listed.count(f) > 1:
            raise Refused(f'verb {show(verb)} lists the field {show(f)} more than once under "{key}"')
    return listed


def registry_from_json(text):
    """The verbs a registry file lists, as {name: Verb}. A registry that carries a checksum
    (the copy setup stores as state/verbs does) must match it."""
    top = as_obj(unwrap_data(parse(text)))
    if CHECKSUM in top:
        if checksum_problem(top) is not None:
            raise Refused(f"the registry: {CHECKSUM_WRONG}")
        top = without_checksum(top)
    table = as_obj(top["verbs"]) if "verbs" in top else top
    verbs = {}
    for name, spec in table.items():
        if not is_verb(name):
            raise Refused(f"verb {show(name)} must be lowercase letters, digits and _")
        if not isinstance(spec, dict):
            raise Refused(f"verb {show(name)} is not an object")
        status = text_of(spec, "status")
        lists = {key: field_list(spec, key, name) for key in FIELD_LISTS}
        v = Verb(
            description=text_of(spec, "description") or "",
            status="ok" if status is None else status,
            group=text_of(spec, "group") or "",
            **lists
        )
        if v.description == "":
            raise Refused(f"verb {show(name)} needs a description")
        if v.status not in STATUSES:
            raise Refused(f"verb {show(name)} has an unknown status {show(v.status)}")
        verbs[name] = v
    if not verbs:
        raise Refused("the registry lists no verbs")
    return verbs


# ---------- rows ----------

# A row before it is sealed: everything the writer supplies. `key` is None when there is none.
Draft = namedtuple("Draft", "ts verb entity subject status payload key source actor")


def filled(v):
    """Whether a required payload field holds something."""
    if isinstance(v, str):
        return trim(v) != ""
    if isinstance(v, (bool, float)):
        return True
    return isinstance(v, list) and len(v) > 0


def check_draft(m, reg, door=True):
    """Check a draft against the registry. The payload is scrubbed of credentials first.
    `door` says the row is on its way in (`row`, the sequence commands, `seal`); without it
    the row is one that was sealed already and is being replayed, and it is held only to
    what rows have always been held to, so that an older log is recounted as it was
    counted."""
    # Every value quoted in an error below goes through `show`: it came from someone else.
    verb = text_of(m, "verb")
    if verb is None:
        raise Refused("a row needs a verb")
    spec = reg.get(verb)
    if spec is None:
        raise Refused(f"{show(verb)} is not a registered verb")
    entity = text_of(m, "entity")
    if entity is None:
        raise Refused("a row needs an entity")
    if not is_entity(entity):
        raise Refused(f"entity {show(entity)} must be type:id (or one lowercase word)")
    subject = one_spaced(text_of(m, "subject") or "")
    if len(subject) > 300:
        raise Refused("subject is longer than 300 characters")
    status = text_of(m, "status")
    if status is None:
        status = spec.status
    if status not in STATUSES:
        raise Refused(f"status {show(status)} must be one of ok, fail, pending, info")
    source = text_of(m, "source")
    if source is None:
        raise Refused("a row needs a source")
    if source not in SOURCES:
        raise Refused(f"source {show(source)} must be one of chat, code, page, system, import")
    actor = text_of(m, "actor")
    if actor is None:
        raise Refused("a row needs an actor")
    if not is_actor(actor):
        raise Refused(f"actor {show(actor)} must be 2 to 40 letters, digits, _ . or -")
    if door and (secret_text(entity) or secret_text(actor)):
        raise Refused("the entity or the actor holds what looks like a credential")
    ts = text_of(m, "ts")
    if ts is None:
        raise Refused("a row needs a ts")
    if not is_ts(ts):
        raise Refused(f"ts {show(ts)} must be a real time written like 2026-11-22T19:05:01+00:00")
    # The credential filter is for rows on their way in. A sealed row is replayed from
    # exactly what it holds: its index was derived from that, under whatever the filter was
    # when it was sealed, and running today's filter over it would recount something else.
    payload = m["payload"] if "payload" in m else {}
    if door:
        payload = scrub(payload)
    if not isinstance(payload, dict):
        raise Refused("payload must be a JSON object")
    check_payload(payload, 0)
    if utf8_len(canonical(payload)) > MAX_PAYLOAD_BYTES:
        raise Refused(f"payload is larger than {MAX_PAYLOAD_BYTES} bytes")
    for f in spec.required:
        if not filled(payload.get(f)):
            raise Refused(f"'{verb}' needs payload field '{f}', and it cannot be empty")
    # Fields that are counted, filtered on or kept as current details end up in index
    # documents every seal rewrites, so they must stay small.
    for f in spec.tally + spec.index:
        if isinstance(payload.get(f), str) and len(payload[f]) > MAX_COUNTED_CHARS:
            raise Refused(f"payload field '{f}' is counted, so it can be at most {MAX_COUNTED_CHARS} characters")
    for f in spec.sum:
        if is_number(payload.get(f)) and abs(payload[f]) > MAX_SUMMED:
            raise Refused(f"payload field '{f}' is summed, so it can be at most {MAX_SUMMED} either way")
    if door:
        # What a new row may hold in the fields its verb adds up, counts or dates. Rows
        # sealed before version 0.7.0 were not held to these, and are recounted as they
        # were counted: there, an amount written as text counts the row and adds nothing.
        for f in spec.sum:
            if payload.get(f) is not None and not (is_number(payload[f]) and is_whole(payload[f])):
                raise Refused(f"payload field '{f}' is summed, so it must be a whole number (an amount in whole cents), not text or anything else")
        for f in spec.tally:
            x = payload.get(f)
            if x is None or isinstance(x, bool):
                continue
            if not isinstance(x, str):
                raise Refused(f"payload field '{f}' is counted, so it must be text, or true or false: write a number as text, like \"3\"")
            # One bucket, one kind of value: the bucket named `true` is for the value true,
            # and the bucket named (other) is for everything past the two hundredth.
            if x in ("true", "false"):
                raise Refused(f"payload field '{f}' is counted: the text \"{x}\" would be counted together with the value {x}. Write {x} without quotes, or another word")
            if x == OTHER:
                raise Refused(f"payload field '{f}' is counted, and {OTHER} is the name the tally keeps for everything past its {MAX_VALUES}th value: use another word")
        for f in spec.dates:
            if payload.get(f) is not None and parse_date(payload[f]) is None:
                raise Refused(f"payload field '{f}' holds a date, so it must be a real one written like 2026-11-27, from 1970 to 9999")
    # A record can be marked as standing for another only of its own kind.
    if "merged_into" in payload:
        target = payload["merged_into"]
        if not (isinstance(target, str) and is_entity(target) and target != entity and kind_of(target) == kind_of(entity)):
            raise Refused("merged_into must be the entity id of another record of the same type")
    key = m.get("idempotency_key")
    if key is not None:
        # The limit is 200 UTF-8 bytes, not characters.
        if not (isinstance(key, str) and key != "" and utf8_len(key) <= 200 and not (door and secret_text(key))):
            raise Refused("idempotency_key must be a short text that holds no credential")
    if door and verb in SEQUENCE_VERBS:
        # The four sequence verbs are made by the seq-* commands, which work out what the
        # row holds. A row of one of them is held to what those commands would have made.
        problem = sequence_row_problem(verb, entity, payload, key)
        if problem is not None:
            raise Refused(f"a {verb} row is made by the seq commands, and this is not one they would make: {problem}")
    return Draft(ts, verb, entity, mask(subject) if door else subject, status, payload, key, source, actor)


def draft_json(d):
    return {
        "ts": d.ts, "verb": d.verb, "entity": d.entity, "subject": d.subject, "status": d.status, "payload": d.payload,
        "idempotency_key": d.key, "source": d.source, "actor": d.actor,
    }


def seal_row(d, row_id, account, previous):
    """The sealed row: the twelve fingerprinted fields, then the fingerprint itself.
    Returns (row, fingerprint)."""
    row = draft_json(d)
    row["id"] = num(row_id)
    row["account"] = account
    row["previous"] = previous
    fp = fingerprint(row)
    row["fingerprint"] = fp
    return row, fp


def entity_doc(entity):
    """Document ids in the index and the inbox that are derived from free text."""
    return "e." + sha256_hex(entity.encode("utf-8"))[:20]


def key_hash(verb, entity, key):
    """An idempotency key belongs to one verb on one entity. Without that, anyone could use
    up another person's key with an unrelated row and have their real one dropped as a
    repeat."""
    return sha256_hex(f"{verb}\n{entity}\n{key}".encode("utf-8"))[:24]


def key_doc(verb, entity, key):
    return "i." + key_hash(verb, entity, key)


def inbox_doc(verb, entity, key):
    return "k" + key_hash(verb, entity, key)


def stream_doc(row_id):
    return "%010d" % row_id


# ---------- tallies ----------
#
# A verb's tally is a plain dict:
#   n, last_id        counts
#   status, source, day   {name: count}
#   actor             [[name, count]], most frequent first
#   field             {payload field: [field value]}
#   sum               {payload field: total}
#   last_ts           text
# One field value is {"v": the value, "n": how many rows carry it, "s": {summed field: its
# total over those rows}}. That is what answers "how much per vendor".


def new_tally():
    return {"n": 0, "status": {}, "source": {}, "day": {}, "actor": [], "field": {}, "sum": {}, "last_id": 0, "last_ts": ""}


def field_vals_of(v):
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if not isinstance(x, dict) or text_of(x, "v") is None:
            continue
        sums = {}
        if isinstance(x.get("s"), dict):
            for name, total in x["s"].items():
                if is_number(total):
                    sums[name] = to_i64(total)
        out.append({"v": text_of(x, "v"), "n": num_of(x, "n"), "s": sums})
    return out


def field_vals_json(vals):
    return [{"v": f["v"], "n": num(f["n"]), "s": {name: num(total) for name, total in f["s"].items()}} for f in vals]


# The bucket that takes every value past the two hundredth of a counted field.
OTHER = "(other)"


def counted_as(x):
    """The text a counted payload value is counted under, or None if it is not counted.
    Text is counted as it is, true and false as those words, a number as its digits. Rows
    sealed before version 0.7.0 are recounted by this same rule; what a new row may hold in
    a counted field is narrower, and is decided at the door (see check_draft)."""
    if isinstance(x, str):
        return x
    if isinstance(x, bool):
        return "true" if x else "false"
    if is_number(x):
        return str(to_i64(x))
    return None


def bucket_of(vals, value):
    """(the bucket a value is counted in, its name): its own if it has one, or if there is
    room for one more; otherwise the bucket for everything else. The bucket is None when it
    does not exist yet."""
    slot = next((f for f in vals if f["v"] == value), None)
    if slot is None and len(vals) >= MAX_VALUES:
        value = OTHER
        slot = next((f for f in vals if f["v"] == value), None)
    return slot, value


def bump_field(vals, value, sums):
    slot, value = bucket_of(vals, value)
    if slot is None:
        slot = {"v": value, "n": 0, "s": {}}
        vals.append(slot)
    slot["n"] += 1
    for name, amount in sums:
        slot["s"][name] = slot["s"].get(name, 0) + amount
    vals.sort(key=lambda f: (-f["n"], f["v"]))


def tally_from_json(v):
    t = new_tally()
    if not isinstance(v, dict):
        return t
    t["n"] = num_of(v, "n")
    t["status"] = counts_of(v, "status")
    t["source"] = counts_of(v, "source")
    t["day"] = counts_of(v, "day")
    t["actor"] = values_of(v.get("actor"))
    t["last_id"] = num_of(v, "last_id")
    t["last_ts"] = text_of(v, "last_ts") or ""
    if isinstance(v.get("field"), dict):
        for name, vals in v["field"].items():
            t["field"][name] = field_vals_of(vals)
    if isinstance(v.get("sum"), dict):
        for name, total in v["sum"].items():
            if is_number(total):
                t["sum"][name] = to_i64(total)
    return t


def tally_copy(t):
    """A tally that can be added to without changing the one it was copied from."""
    return {
        "n": t["n"], "status": dict(t["status"]), "source": dict(t["source"]), "day": dict(t["day"]),
        "actor": [list(pair) for pair in t["actor"]],
        "field": {name: [{"v": f["v"], "n": f["n"], "s": dict(f["s"])} for f in vals] for name, vals in t["field"].items()},
        "sum": dict(t["sum"]), "last_id": t["last_id"], "last_ts": t["last_ts"],
    }


def tally_fits(t, d, spec):
    """Whether this row's summed fields can be added and every total they are added to stay
    a whole number that JSON keeps exact. Those totals are the verb's own, and, for each
    counted field the row carries, the one bucket the row is counted in. No other bucket is
    looked at: one vendor's total standing at the limit does not stop a row for another."""
    for f in spec.sum:
        x = d.payload.get(f)
        if not is_number(x):
            continue
        totals = [t["sum"].get(f, 0)]
        for name in spec.tally:
            value = counted_as(d.payload.get(name))
            if value is not None:
                slot, _ = bucket_of(t["field"].get(name, []), value)
                totals.append(slot["s"].get(f, 0) if slot is not None else 0)
        if any(abs(total + to_i64(x)) > MAX_INT for total in totals):
            return False
    return True


def tally_add(t, row_id, d, spec):
    t["n"] += 1
    t["status"][d.status] = t["status"].get(d.status, 0) + 1
    t["source"][d.source] = t["source"].get(d.source, 0) + 1
    t["day"][d.ts[:10]] = t["day"].get(d.ts[:10], 0) + 1
    while len(t["day"]) > 400:
        del t["day"][min(t["day"])]
    bump(t["actor"], d.actor)
    sums = []
    for f in spec.sum:
        x = d.payload.get(f)
        # A value that is not a number adds nothing, and the row is still counted: that is
        # what versions before 0.7.0 did with one, and their rows are recounted the same way.
        # A new row cannot hold one (see check_draft).
        if is_number(x):
            t["sum"][f] = t["sum"].get(f, 0) + to_i64(x)
            sums.append((f, to_i64(x)))
    for f in spec.tally:
        value = counted_as(d.payload.get(f))
        if value is not None:
            bump_field(t["field"].setdefault(f, []), value, sums)
    t["last_id"] = row_id
    t["last_ts"] = d.ts


def tally_json(t, verb):
    return {
        "k": "v." + verb,
        "verb": verb,
        "n": num(t["n"]),
        "status": counts_json(t["status"]),
        "source": counts_json(t["source"]),
        "day": counts_json(t["day"]),
        "actor": values_json(t["actor"]),
        "field": {name: field_vals_json(vals) for name, vals in t["field"].items()},
        "sum": {name: num(total) for name, total in t["sum"].items()},
        "last_id": num(t["last_id"]),
        "last_ts": t["last_ts"],
    }


# ---------- what a thing is called ----------

MAX_NAMES = 12
MAX_CURRENT = 60
# Legal forms, which say nothing about which company is meant.
SUFFIXES = [
    "inc", "incorporated", "corp", "corporation", "co", "company", "llc", "llp", "lp", "ltd", "limited", "plc", "gmbh", "ag", "sa",
    "sas", "srl", "bv", "nv", "pty", "pc", "pllc",
]
# Accented letters and what they are matched as. Each of these is one character.
FOLDED = {"ß": "ss", "æ": "ae", "œ": "oe", "ł": "l", "&": " and "}
# The sigma Greek writes at the end of a word is the same letter as the one inside it. A
# capital is lowered one character at a time (see `lowercase`), which never gives the final
# form, so a name typed in capitals and the same name typed small had different keys.
FOLDED["ς"] = "σ"
# Combining marks, as blocks of code points that are fixed here for the same reason the
# letters are: the same name must give the same key under every Python.
COMBINING_MARKS = [(0x0300, 0x036F), (0x1AB0, 0x1AFF), (0x1DC0, 0x1DFF), (0x20D0, 0x20FF), (0xFE20, 0xFE2F)]


def is_combining_mark(c):
    return any(first <= ord(c) <= last for first, last in COMBINING_MARKS)
for _plain, _accented in [
    ("a", "àáâãäåā"), ("c", "çćč"), ("e", "èéêëēě"), ("i", "ìíîïī"), ("n", "ñń"), ("o", "òóôõöøō"), ("u", "ùúûüū"), ("y", "ýÿ"),
    ("s", "šś"), ("z", "žźż"),
]:
    for _c in _accented:
        FOLDED[_c] = _plain

# A name's matching key is stored on every entity (`nks`) and compared whenever a log is
# checked, so the same name must give the same key on every machine. Python's own character
# tables change from one Python version to the next, and str.isalnum() never counted the
# vowel signs of Thai, Hindi, Arabic and many other scripts as letters. So the set of
# characters that count as a letter or a digit is fixed here: it is the set the Rust program
# used (`char::is_alphanumeric`, Unicode 18.0), as ranges of code points in hexadecimal.
# test/differential.mjs confirms it against that program for every character there is.
ALPHANUMERIC = """
30-39 41-5A 61-7A AA B2-B3 B5 B9-BA BC-BE C0-D6 D8-F6 F8-2C1 2C6-2D1 2E0-2E4 2EC 2EE 345 363-374
376-377 37A-37D 37F 386 388-38A 38C 38E-3A1 3A3-3F5 3F7-481 48A-52F 531-556 558-559 560-588 58B-58C
5B0-5BD 5BF 5C1-5C2 5C4-5C5 5C7-5C9 5D0-5EA 5EF-5F2 610-61A 620-657 659-669 66E-6D3 6D5-6DC 6E1-6E8
6ED-6FC 6FF 710-73F 74D-7B1 7C0-7EA 7F4-7F5 7FA 800-817 81A-82C 840-858 860-86A 870-887 889-88F 897
8A0-8C9 8D4-8DF 8E3-8E9 8F0-93B 93D-94C 94E-950 955-963 966-96F 971-983 985-98C 98F-990 993-9A8
9AA-9B0 9B2 9B6-9B9 9BD-9C4 9C7-9C8 9CB-9CC 9CE 9D7 9DC-9DD 9DF-9E3 9E6-9F1 9F4-9F9 9FC A01-A03
A05-A0A A0F-A10 A13-A28 A2A-A30 A32-A33 A35-A36 A38-A39 A3E-A42 A47-A48 A4B-A4C A51 A59-A5C A5E
A66-A75 A81-A83 A85-A8D A8F-A91 A93-AA8 AAA-AB0 AB2-AB3 AB5-AB9 ABD-AC5 AC7-AC9 ACB-ACC AD0 AE0-AE3
AE6-AEF AF9-AFC B01-B03 B05-B0C B0F-B10 B13-B28 B2A-B30 B32-B33 B35-B39 B3D-B44 B47-B48 B4B-B4C
B56-B57 B5C-B5D B5F-B63 B66-B6F B71-B77 B82-B83 B85-B8A B8E-B90 B92-B95 B99-B9A B9C B9E-B9F BA3-BA4
BA8-BAA BAE-BB9 BBE-BC2 BC6-BC8 BCA-BCC BD0 BD7 BE6-BF2 C00-C0C C0E-C10 C12-C28 C2A-C39 C3D-C44
C46-C48 C4A-C4C C55-C56 C58-C5A C5C-C5D C60-C63 C66-C6F C78-C7E C80-C83 C85-C8C C8E-C90 C92-CA8
CAA-CB3 CB5-CB9 CBD-CC4 CC6-CC8 CCA-CCC CD5-CD6 CDC-CDE CE0-CE3 CE6-CEF CF1-CF3 D00-D0C D0E-D10
D12-D3A D3D-D44 D46-D48 D4A-D4C D4E D54-D63 D66-D78 D7A-D7F D81-D83 D85-D96 D9A-DB1 DB3-DBB DBD
DC0-DC6 DCF-DD4 DD6 DD8-DDF DE6-DEF DF2-DF3 E01-E3A E40-E46 E4D E50-E59 E81-E82 E84 E86-E8A E8C-EA3
EA5 EA7-EB9 EBB-EBD EC0-EC4 EC6 ECD ED0-ED9 EDC-EDF F00 F20-F33 F40-F47 F49-F6C F71-F83 F88-F97
F99-FBC 1000-1036 1038 103B-1049 1050-109D 10A0-10C5 10C7 10CD 10D0-10FA 10FC-1248 124A-124D
1250-1256 1258 125A-125D 1260-1288 128A-128D 1290-12B0 12B2-12B5 12B8-12BE 12C0 12C2-12C5 12C8-12D6
12D8-1310 1312-1315 1318-135A 1369-137C 1380-138F 13A0-13F5 13F8-13FD 1401-166C 166F-167F 1681-169A
16A0-16EA 16EE-16F8 1700-1713 171F-1733 1740-1753 1760-176C 176E-1770 1772-1773 1780-17B3 17B6-17C8
17D7 17DC 17E0-17E9 17F0-17F9 1810-1819 1820-1878 1880-18AA 18B0-18F5 1900-191E 1920-192B 1930-1938
1946-196D 1970-1974 1980-19AB 19B0-19C9 19D0-19DA 1A00-1A1B 1A20-1A5E 1A61-1A74 1A80-1A89 1A90-1A99
1AA7 1ABF-1AC0 1ACC-1ACE 1B00-1B33 1B35-1B43 1B45-1B4C 1B50-1B59 1B80-1BA9 1BAC-1BE5 1BE7-1BF1
1C00-1C36 1C40-1C49 1C4D-1C7D 1C80-1C8A 1C90-1CBA 1CBD-1CBF 1CE9-1CEC 1CEE-1CF3 1CF5-1CF6 1CFA
1D00-1DBF 1DD3-1DF4 1E00-1F15 1F18-1F1D 1F20-1F45 1F48-1F4D 1F50-1F57 1F59 1F5B 1F5D 1F5F-1F7D
1F80-1FB4 1FB6-1FBC 1FBE 1FC2-1FC4 1FC6-1FCC 1FD0-1FD3 1FD6-1FDB 1FE0-1FEC 1FF2-1FF4 1FF6-1FFC
2070-2071 2074-2079 207F-2089 208F-209F 2102 2107 210A-2113 2115 2119-211D 2124 2126 2128 212A-212D
212F-2139 213C-213F 2145-2149 214E 2150-2189 2460-249B 24B6-24FF 2776-2793 2C00-2CE4 2CEB-2CEE
2CF2-2CF3 2CFD 2D00-2D25 2D27 2D2D 2D30-2D67 2D6F 2D80-2D96 2DA0-2DA6 2DA8-2DAE 2DB0-2DB6 2DB8-2DBE
2DC0-2DC6 2DC8-2DCE 2DD0-2DD6 2DD8-2DDE 2DE0-2DFF 2E2F 3005-3007 3021-3029 3031-3035 3038-303C
3041-3096 309D-309F 30A1-30FA 30FC-30FF 3105-312F 3131-318E 3192-3195 31A0-31BF 31F0-31FF 3220-3229
3248-324F 3251-325F 3280-3289 32B1-32BF 3400-4DBF 4E00-A48C A4D0-A4FD A500-A60C A610-A62B A640-A66E
A674-A67B A67F-A6EF A717-A71F A722-A788 A78B-A7DD A7E2 A7F1-A805 A807-A827 A830-A835 A840-A873
A880-A8C3 A8C5 A8D0-A8D9 A8F2-A8F7 A8FB A8FD-A92A A930-A952 A960-A97C A980-A9B2 A9B4-A9BF A9CF-A9D9
A9E0-A9FE AA00-AA36 AA40-AA4D AA50-AA59 AA60-AA76 AA7A-AABE AAC0 AAC2 AADB-AADD AAE0-AAEF AAF2-AAF5
AB01-AB06 AB09-AB0E AB11-AB16 AB20-AB26 AB28-AB2E AB30-AB5A AB5C-AB69 AB6C-AB6D AB70-ABEA ABF0-ABF9
AC00-D7A3 D7B0-D7C6 D7CB-D7FB F900-FA6D FA70-FAD9 FB00-FB06 FB13-FB17 FB1D-FB28 FB2A-FB36 FB38-FB3C
FB3E FB40-FB41 FB43-FB44 FB46-FBB1 FBD3-FD3D FD50-FD8F FD92-FDC7 FDF0-FDFB FE70-FE74 FE76-FEFC
FF10-FF19 FF21-FF3A FF41-FF5A FF66-FFBE FFC2-FFC7 FFCA-FFCF FFD2-FFD7 FFDA-FFDC 10000-1000B
1000D-10026 10028-1003A 1003C-1003D 1003F-1004D 10050-1005D 10080-100FA 10107-10133 10140-10178
1018A-1018B 10280-1029C 102A0-102D0 102E1-102FB 10300-10323 1032D-1034A 10350-1037A 10380-1039D
103A0-103C3 103C8-103CF 103D1-103D5 10400-1049D 104A0-104A9 104B0-104D3 104D8-104FB 10500-10527
10530-10563 10570-1057A 1057C-1058A 1058C-10592 10594-10595 10597-105A1 105A3-105B1 105B3-105B9
105BB-105BC 105C0-105F3 10600-10736 10740-10755 10760-10767 10780-10785 10787-107B0 107B2-107BF
10800-10805 10808 1080A-10835 10837-10838 1083C 1083F-10855 10858-10876 10879-1089E 108A7-108AF
108E0-108F2 108F4-108F5 108FB-1091B 10920-10939 10940-10959 10980-109B7 109BC-109CF 109D2-10A03
10A05-10A06 10A0C-10A13 10A15-10A17 10A19-10A35 10A40-10A48 10A60-10A7E 10A80-10A9F 10AC0-10AC7
10AC9-10AE4 10AEB-10AEF 10B00-10B35 10B40-10B55 10B58-10B72 10B78-10B91 10BA9-10BAF 10C00-10C48
10C80-10CB2 10CC0-10CF2 10CFA-10D27 10D30-10D39 10D40-10D65 10D69 10D6F-10D85 10E60-10E7E
10E80-10EA9 10EAB-10EAC 10EB0-10EB1 10EC2-10EC7 10ECB-10ECD 10ED9-10EEE 10EF3 10EF5 10EF7-10EF8
10EFA-10EFC 10F00-10F27 10F30-10F45 10F51-10F54 10F70-10F81 10FB0-10FCB 10FE0-10FF6 11000-11045
11052-1106F 11071-11075 11080-110B8 110C2 110D0-110E8 110F0-110F9 11100-11132 11136-1113F
11144-11147 11150-11172 11176 11180-111BF 111C1-111C4 111CE-111DA 111DC 111E1-111F4 11200-11211
11213-11234 11237 1123E-11241 11280-11286 11288 1128A-1128D 1128F-1129D 1129F-112A8 112B0-112E8
112F0-112F9 11300-11303 11305-1130C 1130F-11310 11313-11328 1132A-11330 11332-11333 11335-11339
1133D-11344 11347-11348 1134B-1134C 11350 11357 1135D-11363 11380-11389 1138B 1138E 11390-113B5
113B7-113C0 113C2 113C5 113C7-113CA 113CC-113CD 113D1 113D3 11400-11441 11443-11445 11447-1144A
11450-11459 1145F-11461 11480-114C1 114C4-114C5 114C7 114D0-114D9 11580-115B5 115B8-115BE
115D8-115DD 11600-1163E 11640 11644 11650-11659 11680-116B5 116B8 116C0-116C9 116D0-116E3
11700-1171A 1171D-1172A 11730-1173B 11740-11746 11800-11838 118A0-118F2 118FF-11906 11909
1190C-11913 11915-11916 11918-11935 11937-11938 1193B-1193C 1193F-11942 11950-11959 119A0-119A7
119AA-119D7 119DA-119DF 119E1 119E3-119E4 11A00-11A32 11A35-11A3E 11A50-11A97 11A9D 11AB0-11AF8
11B0A 11B60-11B67 11BC0-11BE0 11BF0-11BF9 11C00-11C08 11C0A-11C36 11C38-11C3E 11C40 11C50-11C6C
11C72-11C8F 11C92-11CA7 11CA9-11CB6 11D00-11D06 11D08-11D09 11D0B-11D36 11D3A 11D3C-11D3D
11D3F-11D41 11D43 11D46-11D47 11D50-11D59 11D60-11D65 11D67-11D68 11D6A-11D8E 11D90-11D91
11D93-11D96 11D98 11DA0-11DA9 11DB0-11DDB 11DE0-11DE9 11DF0-11DF1 11EE0-11EF6 11F00-11F10
11F12-11F3A 11F3E-11F40 11F50-11F59 11FB0 11FC0-11FD4 12000-12399 12400-1246F 12475-12543
12550-12686 12F90-12FF0 13000-1342F 13441-13446 13460-143FA 14400-14646 16100-1612E 16130-16139
16800-16A38 16A40-16A5E 16A60-16A69 16A70-16ABE 16AC0-16AC9 16AD0-16AED 16B00-16B2F 16B40-16B43
16B50-16B59 16B5B-16B61 16B63-16B77 16B7D-16B8F 16D40-16D6C 16D70-16D79 16E40-16E96 16EA0-16EB8
16EBB-16ED3 16F00-16F4A 16F4F-16F87 16F8F-16F9F 16FE0-16FE1 16FE3 16FF0-16FF6 17000-18CDA
18CFF-18D20 18D80-18DF2 18E00-19191 191A0-191D2 1AFF0-1AFF3 1AFF5-1AFFB 1AFFD-1AFFE 1B000-1B128
1B132 1B150-1B152 1B155 1B164-1B168 1B170-1B2FB 1BC00-1BC6A 1BC70-1BC7C 1BC80-1BC88 1BC90-1BC99
1BC9E 1CCF0-1CCF9 1D2C0-1D2D3 1D2E0-1D2F3 1D360-1D378 1D400-1D454 1D456-1D49C 1D49E-1D49F 1D4A2
1D4A5-1D4A6 1D4A9-1D4AC 1D4AE-1D4B9 1D4BB 1D4BD-1D4C3 1D4C5-1D505 1D507-1D50A 1D50D-1D514
1D516-1D51C 1D51E-1D539 1D53B-1D53E 1D540-1D544 1D546 1D54A-1D550 1D552-1D6A6 1D6A8-1D6C0
1D6C2-1D6DA 1D6DC-1D6FA 1D6FC-1D714 1D716-1D734 1D736-1D74E 1D750-1D76E 1D770-1D788 1D78A-1D7A8
1D7AA-1D7C2 1D7C4-1D7CB 1D7CE-1D7FF 1DF00-1DF81 1DF90-1DF96 1DFCD-1E006 1E008-1E018 1E01B-1E021
1E023-1E024 1E026-1E02A 1E030-1E06D 1E08F 1E100-1E12C 1E137-1E13D 1E140-1E149 1E14E 1E290-1E2AD
1E2C0-1E2EB 1E2F0-1E2F9 1E4D0-1E4EB 1E4F0-1E4F9 1E5D0-1E5ED 1E5F0-1E5FA 1E6C0-1E6DE 1E6E0-1E6F5
1E6FE-1E6FF 1E7E0-1E7E6 1E7E8-1E7EB 1E7ED-1E7EE 1E7F0-1E7FE 1E800-1E8C4 1E8C7-1E8CF 1E900-1E943
1E947 1E94B 1E950-1E959 1EC71-1ECAB 1ECAD-1ECAF 1ECB1-1ECB4 1ED01-1ED2D 1ED2F-1ED3D 1EE00-1EE03
1EE05-1EE1F 1EE21-1EE22 1EE24 1EE27 1EE29-1EE32 1EE34-1EE37 1EE39 1EE3B 1EE42 1EE47 1EE49 1EE4B
1EE4D-1EE4F 1EE51-1EE52 1EE54 1EE57 1EE59 1EE5B 1EE5D 1EE5F 1EE61-1EE62 1EE64 1EE67-1EE6A
1EE6C-1EE72 1EE74-1EE77 1EE79-1EE7C 1EE7E 1EE80-1EE89 1EE8B-1EE9B 1EEA1-1EEA3 1EEA5-1EEA9
1EEAB-1EEBB 1F100-1F10C 1F130-1F149 1F150-1F169 1F170-1F189 1FBF0-1FBF9 20000-2A6DF 2A700-2B81E
2B820-2CEAD 2CEB0-2EBE0 2EBF0-2EE5D 2F800-2FA1D 30000-3134A 31350-33479 3D000-3FC3F
"""
# Lowercase forms that Unicode gained after version 12.1, which is all Python 3.8 knows:
# "first-last:offset", each letter from first to last becoming the one `offset` further on.
NEWER_LOWERCASE = """
1C89:+1 2C2F:+48 A7C0:+1 A7C7:+1 A7C9:+1 A7CB:-42343 A7CC:+1 A7CE:+1 A7D0:+1 A7D2:+1 A7D4:+1 A7D6:+1
A7D8:+1 A7DA:+1 A7DC:-42561 A7DD:-42342 A7E2:-42342 A7F5:+1 AB6C-AB6D:-33 10570-1057A:+39
1057C-1058A:+39 1058C-10592:+39 10594-10595:+39 10D50-10D65:+32 16EA0-16EB8:+27 1DF40:+1 1DF48:+1
1DF4A:+1 1DF4D:+1 1DF51:+1 1DF68:+1 1DF6A:+1 1DF6C:+1 1DF6E:+1 1DF72:+1 1DF74:+1 1DF76:+1 1DF78:+1
1DF7A:+1 1DF7C:+1 1DF7E:+1
"""


def read_ranges(table):
    starts, ends = [], []
    for item in table.split():
        first, _, last = item.partition("-")
        starts.append(int(first, 16))
        ends.append(int(last or first, 16))
    return starts, ends


def read_lowercase(table):
    pairs_found = {}
    for item in table.split():
        span, _, offset = item.partition(":")
        first, _, last = span.partition("-")
        for code in range(int(first, 16), int(last or first, 16) + 1):
            pairs_found[chr(code)] = chr(code + int(offset))
    return pairs_found


ALNUM_STARTS, ALNUM_ENDS = read_ranges(ALPHANUMERIC)
LOWERCASE_ADDED = read_lowercase(NEWER_LOWERCASE)


def is_alphanumeric(c):
    at = bisect_right(ALNUM_STARTS, ord(c)) - 1
    return at >= 0 and ord(c) <= ALNUM_ENDS[at]


def lowercase(c):
    """One character in lowercase, which can be more than one character. Taken one at a
    time on purpose: lowering a whole string would treat a final Greek sigma specially."""
    return LOWERCASE_ADDED.get(c) or c.lower()


def name_key(text):
    """The form of a name that matching is done on: lowercase, accents and punctuation gone,
    "&" as "and", no leading "the", no trailing legal form. "ALDER Corp." and "The Alder
    Company" both give "alder". Stored on every entity, so changing this means a rebuild."""
    flat = []
    # Composed first, so that an accent typed as a letter and a combining mark is the one
    # character it is when typed whole, and is folded like it. (Before version 0.7.0 the
    # mark became a space: "E" + accent + "clair" gave "e clair", and "\u00c9clair" "eclair".)
    for original in unicodedata.normalize("NFC", text):
        for c in lowercase(original):
            if c in FOLDED:
                flat.append(FOLDED[c])
            elif c in "'\u2019.":
                pass
            elif is_alphanumeric(c):
                flat.append(c)
            elif is_combining_mark(c):
                pass  # an accent with no letter of its own to become: dropped, not a gap
            else:
                flat.append(" ")
    tokens = [t for t in "".join(flat).split(" ") if t]
    if len(tokens) > 1 and tokens[0] == "the":
        del tokens[0]
    while len(tokens) > 1 and tokens[-1] in SUFFIXES:
        del tokens[-1]
    return " ".join(tokens)[:80]


def jaro(a, b):
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    a_matched, b_matched = [False] * len(a), [False] * len(b)
    matches = 0
    for i in range(len(a)):
        for j in range(max(i - window, 0), min(i + window + 1, len(b))):
            if not b_matched[j] and a[i] == b[j]:
                a_matched[i] = b_matched[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    swaps = j = 0
    for i in range(len(a)):
        if a_matched[i]:
            while not b_matched[j]:
                j += 1
            if a[i] != b[j]:
                swaps += 1
            j += 1
    m = float(matches)
    return (m / len(a) + m / len(b) + (m - swaps // 2) / m) / 3.0


def jaro_winkler(a, b):
    j = jaro(a, b)
    prefix = 0
    for x, y in list(zip(a, b))[:4]:
        if x != y:
            break
        prefix += 1
    return j + prefix * 0.1 * (1.0 - j)


def name_score(q, c):
    """How alike two name keys are, from 0 to 100, and in what way. 100 is the same key;
    anything lower is a question for a person, never an answer."""
    if q == c:
        return (100, "same name")
    q_tokens, c_tokens = set(q.split(" ")), set(c.split(" "))
    small, big = (q_tokens, c_tokens) if len(q_tokens) <= len(c_tokens) else (c_tokens, q_tokens)
    if small <= big and any(len(t) >= 3 for t in small):
        return (90, "one name contains the other")

    def initials(x):
        return "".join(t[0] for t in x.split(" ") if t not in ("and", "of", ""))

    def short_for(x, y):
        return " " not in x and len(x) >= 3 and " " in y and x == initials(y)

    if short_for(q, c) or short_for(c, q):
        return (88, "initials of the other")

    def in_order(x):
        return " ".join(sorted(x.split(" ")))

    jw = max(jaro_winkler(q, c), jaro_winkler(in_order(q), in_order(c)))
    return (min(int(math.floor(jw * 100.0)), 99), "similar spelling")


def add_name(names, text):
    name = one_spaced(text)
    key = name_key(name)
    if key == "" or len(name) > 120 or len(names) >= MAX_NAMES or any(name_key(x) == key for x in names):
        return
    names.append(name)


def plain(v):
    """A payload value that can sit at the top of a document and be filtered on, or None."""
    if isinstance(v, str):
        return v[:300]
    if isinstance(v, (bool, float)):
        return v
    return None


def entity_json(prev, row_id, d, spec, lost=None):
    """The state of one entity after one more row. Besides the latest row's facts it keeps:
    `type`; `names` (its first label, then any alias) with their matching keys in `nks`; and
    `f_<field>` for each payload field its verbs keep as current, the newest value winning.
    A record keeps at most MAX_CURRENT such fields; the names of any this row carried that
    found no room are added to `lost`, if a list is given for them."""
    old = prev if isinstance(prev, dict) else None
    count = num_of(old, "n") if old is not None else 0
    first = num_of(old, "first_id") if old is not None else row_id
    before = (text_of(old, "subject") or "") if old is not None else ""
    subject = d.subject if d.subject != "" else before

    names = list_of(old, "names") if old is not None else []
    if not names:
        add_name(names, before)
    if not names:
        add_name(names, d.subject)
    for f in spec.names:
        if isinstance(d.payload.get(f), str):
            add_name(names, d.payload[f])

    current = {}
    if old is not None:
        for k, v in old.items():
            if k.startswith("f_") and plain(v) is not None:
                current[k] = plain(v)
    # `*` keeps every plain field except the one that redirects a record: only a verb that
    # lists `merged_into` by name may set it.
    every = "*" in spec.current
    # First what needs no room or frees some: a field cleared, and a new value for a field
    # the record has. Then the fields it did not have, in name order, while there is room.
    # (Before version 0.7.0 all of it went in name order, so that on a full record a row
    # which cleared one field and set a new one could lose the new one: a log in which that
    # happened recounts differently now, and `rebuild` brings its index into line.)
    new = []
    for f in sorted(f for f in d.payload if f in spec.current or (every and f != "merged_into")):
        key, value = "f_" + f, d.payload[f]
        if value is None:
            current.pop(key, None)
        elif plain(value) is None:
            continue
        elif key in current:
            current[key] = plain(value)
        else:
            new.append((key, plain(value)))
    for key, value in new:
        if len(current) < MAX_CURRENT:
            current[key] = value
        elif lost is not None:
            lost.append(key[2:])

    doc = {
        "k": entity_doc(d.entity),
        "entity": d.entity,
        "type": kind_of(d.entity),
        "subject": subject,
        "names": names,
        "nks": [name_key(x) for x in names],
        "n": num(count + 1),
        "first_id": num(first),
        "last_id": num(row_id),
        "last_ts": d.ts,
        "last_verb": d.verb,
        "status": d.status,
    }
    doc.update(current)
    return doc


# ---------- files ----------


def os_error_text(error, path):
    """An operating system's complaint in the Rust program's wording: the system's own
    message, then `(os error N)` with the system's own number."""
    if os.name == "nt":
        if error.winerror is None:
            # Python's open() reports a C error number on Windows, where the Rust program
            # reported the Windows one. os.stat() reports the Windows one, so ask it.
            try:
                os.stat(path)
                if error.errno == 13:
                    return "Access is denied. (os error 5)"
            except OSError as again:
                error = again
        if error.winerror is not None:
            return f"{error.strerror}. (os error {error.winerror})"
    return f"{error.strerror} (os error {error.errno})"


def is_link(path):
    """Whether a path is itself a symbolic link, a Windows junction or any other reparse
    point: something that looks like a file or a folder here and is really somewhere else."""
    try:
        found = os.lstat(path)
    except OSError:
        return False
    # FILE_ATTRIBUTE_REPARSE_POINT covers junctions and mount points, which are not symlinks.
    return stat.S_ISLNK(found.st_mode) or bool(getattr(found, "st_file_attributes", 0) & 0x400)


def read_bytes(path, inside=False, most=MAX_FILE_BYTES):
    """A file's bytes. Its size is looked at before any of it is read, and it must be a
    regular file. `inside` says the file was found in a folder the script was given, or is
    named by a document: then it must not be a link either, because whoever filled the
    folder is not the caller. A file the caller names outright may be reached through a
    link, which is the caller's own business."""
    try:
        found = os.lstat(path) if inside else os.stat(path)
        if inside and is_link(path):
            raise Refused(f"{show_path(path)}: is a link, and this script does not read through links")
        if not stat.S_ISREG(found.st_mode):
            raise Refused(f"{show_path(path)}: is not a regular file")
        if found.st_size > most:
            raise Refused(f"{show_path(path)}: is {found.st_size} bytes; the most this script reads from one file is {most}")
        with open(path, "rb") as f:
            data = f.read(most + 1)
    except OSError as error:
        raise Refused(f"{show_path(path)}: {os_error_text(error, path)}")
    if len(data) > most:
        raise Refused(f"{show_path(path)}: is more than {most} bytes, the most this script reads from one file")
    return data


def read_text(path, inside=False):
    try:
        return read_bytes(path, inside).decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{show_path(path)}: stream did not contain valid UTF-8")


def make_folders(path):
    """Create a folder and whichever folders above it are missing. Written out, where
    os.makedirs would do, so that when a file stands in the way the complaint is the one
    the Rust program gave."""
    if path == "":
        return
    try:
        os.mkdir(path)
        return
    except FileNotFoundError:
        pass  # a folder above it is missing: make that first
    except OSError:
        if os.path.isdir(path):
            return
        raise
    make_folders(os.path.dirname(path))
    try:
        os.mkdir(path)
    except OSError:
        if not os.path.isdir(path):
            raise


def out_path(out, *parts):
    """Where a document this script writes goes under --out: folders, then the document's
    id, which gets `.json`. Each part must be a name `is_file_name` passes, so that no part
    of a path under --out is ever taken from a document as it stands."""
    for part in parts:
        if not is_file_name(part):
            raise Refused(f"{show(part)} cannot be used as a file name")
    return os.path.join(out, *parts) + ".json"


def folders_below(root, path):
    """Each folder from `root` down to the one holding `path`, `root` first. `path` must be
    under `root`: it was built from it."""
    rest = os.path.relpath(os.path.dirname(os.path.abspath(path)), os.path.abspath(root))
    if rest == os.pardir or rest.startswith(os.pardir + os.sep) or os.path.isabs(rest):
        raise Refused(f"{show_path(path)}: is not inside {show_path(root)}")
    found, at = [root], root
    for part in [] if rest == os.curdir else rest.split(os.sep):
        at = os.path.join(at, part)
        found.append(at)
    return found


def new_files(root, files):
    """Create every one of `files`, [(path, text or bytes)], under `root`, and write over
    nothing: if any of them is there already, none is written. A folder on the way that is
    a link is refused, so nothing is written through one to somewhere else. `root` itself
    is the caller's own choice of folder and is taken as it is."""
    for path, _ in files:
        for at in folders_below(root, path)[1:]:
            if is_link(at) or (os.path.lexists(at) and not os.path.isdir(at)):
                raise Refused(f"{show_path(at)}: is a link or a file where a folder is needed; give --out a new, empty folder")
        if os.path.lexists(path):
            raise Refused(
                f"{show_path(path)}: is already there, and this script writes over nothing. Give --out a new, empty folder:"
                " what is in this one may be an earlier plan, or someone else's file. Nothing was written"
            )
    for written, (path, data) in enumerate(files):
        try:
            make_folders(os.path.dirname(path))
            # "x" creates the file and fails if anything is there, a link included.
            with open(path, "xb") as f:
                f.write(data.encode("utf-8") if isinstance(data, str) else data)
        except OSError as error:
            why = "is already there, and this script writes over nothing" if isinstance(error, FileExistsError) else os_error_text(error, path)
            # Every path was looked at before the first file was made, so this is rare; but
            # then some of the files are there, and saying "nothing was written" would be untrue.
            left = f"{written} of this command's {len(files)} files had been written under {show_path(root)} and are left there: use none of them, and give --out a new, empty folder"
            raise Refused(f"{show_path(path)}: {why}. " + (left if written else "Nothing was written"))


def new_file(root, path, data):
    new_files(root, [(path, data)])


# ---------- plans ----------
#
# What a command plans is written as files: each document, then `plan.json`, whose "batch"
# names those files for a session whose database tool takes files. A session that carries
# documents by hand gets `plan-inline.json` as well: the same writes as "ops", each
# document inside its entry ({op, path, data, ifVersion}). Every path is looked at before
# any file is made, and the plans are written last: a refused command leaves nothing.


class Plan:
    def __init__(self, out):
        self.out, self.batch, self.ops, self.files, self.batches = out, [], [], [], 1

    def set(self, collection, doc_id, doc, version=None):
        """Plan a write of one document, pinned to `version` if one is given. Returns its
        text as it will be stored."""
        text = canonical(doc)
        path = out_path(self.out, collection, doc_id)
        self.files.append((path, text))
        where = (json_text(collection), json_text(doc_id), json_text(absolute(path)), "" if version is None else ',"if_version":%d' % version)
        self.batch.append('{"op":"set","collection":%s,"doc_id":%s,"file_path":%s%s}' % where)
        self.ops.append('{"op":"set","path":%s,"data":%s%s}' % (json_text(collection + "/" + doc_id), text, "" if version is None else ',"ifVersion":%d' % version))
        return text

    def delete(self, collection, doc_id, version):
        self.batch.append('{"op":"delete","collection":%s,"doc_id":%s,"if_version":%d}' % (json_text(collection), json_text(doc_id), version))
        self.ops.append('{"op":"delete","path":%s,"ifVersion":%d}' % (json_text(collection + "/" + doc_id), version))

    def write(self, summary, after=None, split=False):
        """Write the documents and then the plans; returns the plans' paths. `summary` is
        the JSON members a plan starts with. `after`, given for a session that carries
        documents by hand, is what must be read back once the ops are applied. `split`
        says the writes may be more than one batch takes: they are also written as files
        of at most 50, `batch-01.json` onward, and listed under "batches"; "batch" is
        then present only when they fit one batch."""
        plan_path, inline_path = out_path(self.out, "plan"), out_path(self.out, "plan-inline")
        files = list(self.files)
        body = '"batch":[\n%s\n]' % ",\n".join(self.batch)
        if split:
            lists = ["[\n" + ",\n".join(self.batch[at : at + MAX_WRITES]) + "\n]" for at in range(0, len(self.batch), MAX_WRITES)]
            files += [(out_path(self.out, "batch-%02d" % (at + 1)), one + "\n") for at, one in enumerate(lists)]
            body = (body + "," if len(lists) <= 1 else "") + '"batches":[%s]' % ",".join(lists)
            self.batches = len(lists)
        files.append((plan_path, "{%s,%s}\n" % (summary, body)))
        if after is not None:
            files.append((inline_path, '{%s,"after":%s,"ops":[\n%s\n]}\n' % (summary, after, ",\n".join(self.ops))))
        new_files(self.out, files)
        return show_path(absolute(plan_path)), show_path(absolute(inline_path)) if after is not None else None

    def say_how(self, plan_path, inline_path=None, to=""):
        """Say how the plan is sent: as files, and, for a session that cannot pass files,
        as the entries of "ops". `to` names the artifact where it is not the log."""
        if self.batches > 1:
            say("send batch-01.json to batch-%02d.json in %s, each as one batch, in order%s" % (self.batches, show_path(absolute(self.out)), to))
        else:
            say(f"nothing is stored yet: apply the {len(self.batch)} writes in {plan_path} as one batch{to}")
        if inline_path is not None:
            say(f"without file_path: send the {len(self.ops)} entries of \"ops\" in {inline_path} as one write{to}, each exactly as written")
            say(f"the write's result must list {len(self.ops)} results, one for each entry: fewer means an entry was left out")


def absolute(path):
    """A path made absolute without looking at the disk, as the Rust program printed paths.
    os.path.abspath differs from it in two ways that are put right here: it drops a final
    separator, and away from Windows it works out `..`, which is wrong across a link."""
    if os.name == "nt":
        full = os.path.abspath(path)
        if path[-1:] in ("/", "\\") and full[-1:] != "\\":
            full += "\\"
        return full
    if path.startswith("/"):
        full = "//" if path.startswith("//") and not path.startswith("///") else "/"
    else:
        full = os.getcwd()
    for part in path.split("/"):
        if part not in ("", "."):
            full += part if full.endswith("/") else "/" + part
    if path.endswith("/") and not full.endswith("/"):
        full += "/"
    return full


def stem_and_extension(file_name):
    """`head.json` is ("head", "json"). A name that only starts with a dot has no extension."""
    dot = file_name.rfind(".")
    if file_name == ".." or dot <= 0:
        return file_name, None
    return file_name[:dot], file_name[dot + 1 :]


def docs_each(folder_path):
    """(document id, parsed document, why it could not be read) for every JSON file in a
    folder, in name order; one of the last two is None. A folder that is not there holds
    nothing."""
    out = []
    if not os.path.isdir(folder_path):
        return out
    # Where `ingest` filled the folder, its files must still be the ones that read gave.
    read_manifest(folder_path)
    # In the order of the names' bytes, which is the order the Rust program read them in.
    for name in sorted(names_in(folder_path), key=os.fsencode):
        doc_id, extension = stem_and_extension(name)
        if extension != "json":
            continue
        try:
            # Whoever filled this folder is not the caller: a link in it is not followed.
            out.append((doc_id, unwrap_data(parse(read_text(os.path.join(folder_path, name), inside=True))), None))
        except Refused as why:
            out.append((doc_id, None, str(why)))
    return out


def docs_in(folder_path):
    """The same as (document id, document) pairs, where one unreadable file is an error for
    the whole folder."""
    out = []
    for doc_id, doc, why in docs_each(folder_path):
        if why is not None:
            raise Refused(f"{show_path(os.path.join(folder_path, doc_id + '.json'))}: {why}")
        out.append((doc_id, doc))
    return out


def flag(flags, name):
    """A flag's value, or None when it was not given or was given empty."""
    return flags.get(name) or None


def need(flags, name, cmd):
    value = flag(flags, name)
    if value is None:
        raise Refused(f"{cmd} needs --{name}")
    return value


def folder(flags, name, cmd):
    """A folder the caller named must exist. A mistyped path would otherwise read as
    "nothing there", and a seal or a copy would start again from row 1."""
    path = need(flags, name, cmd)
    if not os.path.isdir(path):
        raise Refused(f"--{name}: no such folder as {show_path(path)}. If the read it came from returned nothing, create the empty folder")
    return path


def random_number():
    """The random part of a generated document id. ROWKEEP_TEST_RANDOM fixes it, for tests
    only."""
    fixed = parse_unsigned(os.environ.get("ROWKEEP_TEST_RANDOM"))
    if fixed is not None:
        return fixed
    return int.from_bytes(os.urandom(8), "big")


def registry_text(flags, cmd):
    """The text of the registry named by --registry, or else by the ROWKEEP_REGISTRY
    environment variable, or else of the copy bundled with the skill, which sits beside
    this script's folder."""
    path = flag(flags, "registry") or os.environ.get("ROWKEEP_REGISTRY") or None
    if path is None:
        bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "registry", "verbs.json")
        if not os.path.isfile(bundled):
            raise Refused(f"{cmd} needs --registry FILE (or the ROWKEEP_REGISTRY environment variable)")
        path = bundled
    return read_text(path)


def load_registry(flags, cmd):
    return registry_from_json(registry_text(flags, cmd))


def cli_verbs(flags):
    reg = load_registry(flags, "verbs")
    for name in sorted(reg):
        v = reg[name]
        needs = "needs " + ", ".join(v.required) if v.required else "nothing required"
        extra = []
        if v.tally:
            extra.append("counted: " + ", ".join(v.tally))
        if v.sum:
            extra.append("summed: " + ", ".join(v.sum))
        if v.index:
            extra.append("filterable: " + ", ".join("p_" + f for f in v.index))
        if "*" in v.current:
            extra.append("kept as current: every plain payload field")
        elif v.current:
            extra.append("kept as current: " + ", ".join(v.current))
        if v.names:
            extra.append("adds a name: " + ", ".join(v.names))
        # The description is whatever the registry file holds, so it is printed as any other
        # text from a document is: quoted, on one line, and cut if it runs on.
        say(f"{name}\t{v.status}\t{needs}\t{show(v.description, 300)}\t{'; '.join(extra)}")
    return True


# ---------- row ----------

# What `row --from FILE` may hold: the names of the options that give a row's fields, and
# the key under either of its names.
ROW_FIELDS = ["verb", "entity", "subject", "status", "source", "actor", "key", "idempotency_key", "ts", "payload"]


def cli_row(flags):
    reg = load_registry(flags, "row")
    out = need(flags, "out", "row")
    m = {}
    if "from" in flags:
        # --from FILE: the whole row as one JSON object in a file, so that none of its text
        # is ever part of a command line. Its fields are the options' names.
        given = [name for name in ROW_TEXTS.split() if name in flags]
        if given:
            raise Refused(f"--from gives the whole row: leave off --{given[0]}, and every other option that gives one of a row's fields")
        try:
            row = parse(read_text(flags["from"]))
        except Refused as why:
            raise Refused(f"--from: {why}")
        if not isinstance(row, dict):
            raise Refused("--from must hold one JSON object: a row's fields")
        for name, value in row.items():
            if name not in ROW_FIELDS:
                raise Refused(f"--from: the object holds {show(name)}, which is not a field of a row. A row's fields are: {', '.join(ROW_FIELDS)}")
            if name != "payload" and value is not None and not isinstance(value, str):
                raise Refused(f"--from: {name} must be text")
        if "key" in row and "idempotency_key" in row:
            raise Refused("--from: the object holds both key and idempotency_key, which are one field: give it once")
        for name in ["verb", "entity", "subject", "status", "source", "actor", "ts"]:
            if row.get(name) is not None:
                m[name] = row[name]
        key = row.get("key", row.get("idempotency_key"))
        if key is not None:
            m["idempotency_key"] = key
        payload = row["payload"] if row.get("payload") is not None else {}
        if "ts" not in m:
            m["ts"] = iso_utc(now_secs())
    else:
        payload = parse_flag(flags["payload"], "--payload") if flag(flags, "payload") is not None else {}
        for name in ["verb", "entity", "subject", "status", "source", "actor"]:
            if flag(flags, name) is not None:
                m[name] = flags[name]
        if flag(flags, "key") is not None:
            m["idempotency_key"] = flags["key"]
        # An empty --ts is not "now": it is refused below as a time that is not one.
        m["ts"] = flags["ts"] if "ts" in flags else iso_utc(now_secs())
    m["payload"] = payload
    dropped = []
    dropped_names(payload, dropped)
    draft = check_draft(m, reg)
    if too_far_ahead(draft.ts):
        raise Refused("--ts is more than a day in the future")
    # Say what the credential check removed, so a field that vanished is not a mystery.
    for name in dropped:
        warn(f"note: payload field {show(name)} was left out: its name says it holds a credential")
    if HIDDEN in canonical(draft.payload) or HIDDEN in draft.subject:
        warn(f"note: part of a value looked like a credential and was stored as {HIDDEN}")
    emit(out, draft, "")
    return True


def emit(out, draft, extra):
    """Write a checked row as an inbox document and print where it is. The document itself
    is printed too, so a caller that cannot pass files need not read one. `extra` is more
    JSON members for the printed line, each starting with a comma."""
    now = now_ms()
    if draft.key is not None:
        doc_id = inbox_doc(draft.verb, draft.entity, draft.key)
    else:
        doc_id = new_id(now, random_number())
    # `at` is when the row was written, to the millisecond. It is not part of the sealed
    # row; it only settles the order of rows that share a `ts`, which is kept to the second.
    doc = draft_json(draft)
    doc["at"] = num(now)
    # `ck` is not part of the sealed row either: it lets whoever reads this document back
    # know it arrived whole.
    doc = with_checksum(doc)
    path = out_path(out, doc_id)
    # A file is never written over. With a key the document's id is the same every time, so
    # a second row with the same key made in the same folder stops here: the first is still
    # there, and whether it was stored is for the caller to find out before making another.
    new_file(out, path, canonical(doc))
    say('{"doc_id":' + json_text(doc_id) + ',"file":' + display_json(absolute(path)) + ',"data":' + canonical(doc) + extra + "}")


# ---------- sequences ----------
#
# A sequence is an ordered list of steps, each some days after the one before. A target is
# enrolled in it, and the enrollment is an entity whose current details say which step is
# done, what comes next and when. These commands build those rows, so the step numbering,
# the due dates and the once-only keys are computed, never typed.

Step = namedtuple("Step", "channel wait_days what")


def days_from_civil(y, m, d):
    if m <= 2:
        y -= 1
    era, yoe = y // 400, y % 400
    doy = (153 * (m - 3 if m > 2 else m + 9) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


# Dates run from 1970-01-01 to 9999-12-31: a year of four digits, so that dates compare
# correctly as text, which is how the database filters on them.
LAST_DAY = days_from_civil(9999, 12, 31)


def date_of(days):
    """`2026-11-27` for a count of days since 1970. A day outside the range has no such
    form (the year would not have four digits), and is refused, never cut to fit."""
    if not 0 <= days <= LAST_DAY:
        raise Refused("that date falls outside 1970-01-01 to 9999-12-31, the dates this log can hold")
    return iso_utc(days * 86400)[:10]


def parse_date(x):
    """Days since 1970 for a `2026-11-27` date; None for anything else, a 31st of February
    included."""
    if not isinstance(x, str) or not re.fullmatch("[0-9]{4}-[0-9]{2}-[0-9]{2}", x):
        return None
    y, m, d = int(x[:4]), int(x[5:7]), int(x[8:])
    if y < 1970 or not 1 <= m <= 12 or not 1 <= d <= 31:
        return None
    days = days_from_civil(y, m, d)
    return days if 0 <= days <= LAST_DAY and date_of(days) == x else None


def is_slug(x):
    return 1 <= len(x) <= 40 and not x.startswith("-") and all(c in ASCII_LOWER + ASCII_DIGITS + "-" for c in x)


def steps_in(v):
    """The steps, from a bare list, from `{steps: [...]}`, or from a whole
    `sequence_defined` row. None when there are none to be found."""
    v = unwrap_data(v)
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        if isinstance(v.get("steps"), list):
            return v["steps"]
        if "payload" in v:
            return steps_in(v["payload"])
    return None


def checked_steps(items):
    """A sequence's steps, checked: 1 to 20 of {channel, wait_days, what}."""
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise Refused("a sequence has 1 to 20 steps")
    steps = []
    for at, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise Refused(f"step {at} is not an object")
        channel = text_of(item, "channel")
        if channel is None or not is_key(channel):
            raise Refused(f"step {at} needs a channel: one lowercase word such as email, call, message")
        what = one_spaced(text_of(item, "what") or "")
        if what == "" or len(what) > 200:
            raise Refused(f"step {at} needs a what: up to 200 characters on what this step is")
        wait = item.get("wait_days")
        if not (is_number(wait) and is_whole(wait) and 0 <= wait <= 365):
            raise Refused(f"step {at} needs wait_days: whole days after the step before it, 0 to 365")
        steps.append(Step(channel, int(wait), what))
    return steps


def read_steps(flags, cmd):
    if flag(flags, "steps") is not None:
        text = flags["steps"]
    else:
        raise Refused(f"{cmd} needs --steps JSON or --steps-file FILE: the sequence's steps, as its sequence_defined row holds them")
    given = parse_flag(text, "--steps")
    # The steps may be given as a whole row that is still waiting in an inbox.
    if checksum_problem(unwrap_data(given)) is not None:
        raise Refused(f"--steps: the document given cannot be used: {CHECKSUM_WRONG}")
    items = steps_in(given)
    if items is None:
        raise Refused('--steps must be a list of steps, or a document holding one under "steps"')
    return checked_steps(items)


def steps_json(steps):
    return [{"channel": x.channel, "wait_days": num(x.wait_days), "what": x.what} for x in steps]


def next_after(steps, done, on):
    """What comes after `done` steps, counted from `on`: (text, date), or None when all are
    done."""
    if done >= len(steps):
        return None
    following = steps[done]
    if on + following.wait_days > LAST_DAY:
        raise Refused(f"step {done + 1} would be due after 9999-12-31, the last date this log can hold: give --on a nearer day")
    return (f"Step {done + 1} of {len(steps)}, {following.channel}: {following.what}", date_of(on + following.wait_days))


# How a sequence can end for a target.
OUTCOMES = ["replied", "booked", "stopped", "bounced", "unsubscribed", "no_reply"]
SEQUENCE_VERBS = ["sequence_defined", "sequence_enrolled", "sequence_step_done", "sequence_ended"]


def sequence_row_problem(verb, entity, payload, key):
    """Why a row of one of the four sequence verbs is not a row the seq-* commands make, or
    None. Those commands work out the entity, the step, the once-only key and the tally
    labels from the sequence and the target; a row typed by hand can get any of them wrong,
    and a row without its key can be sealed twice. So what the commands compute is computed
    again here, and must be what the row holds."""
    if verb == "sequence_defined":
        if not (entity.startswith("sequence:") and is_slug(entity[len("sequence:") :])):
            return "its entity must be sequence:<id>, as seq-define writes it"
        try:
            steps = checked_steps(payload.get("steps"))
        except Refused as why:
            return str(why)
        return None if payload.get("step_count") == len(steps) else "its step_count is not the number of its steps"
    slug, target, step = payload.get("sequence"), payload.get("target"), payload.get("step")
    if not (isinstance(slug, str) and is_slug(slug) and isinstance(target, str) and is_entity(target) and ":" in target):
        return "it must name its sequence and its target, as the seq commands do"
    if entity != f"enrollment:{slug}/{target.replace(':', '.')}":
        return "its entity is not the enrollment of that target in that sequence"
    if not (is_number(step) and is_whole(step) and 0 <= step <= 20):
        return "its step must be a whole number from 0 to 20"
    step, base, state = int(step), f"seq:{slug}:{target}", payload.get("state")
    if verb == "sequence_enrolled":
        want = base
        if step != 0 or state != "active":
            return "an enrollment starts at step 0, active"
    elif verb == "sequence_step_done":
        want = f"{base}:step:{step}"
        if step < 1 or payload.get("seq_step") != f"{slug}/{step}":
            return "its step and its seq_step do not agree with its sequence"
        if state not in ("active", "finished") or (state == "active") != isinstance(payload.get("next_due"), str):
            return "after a step an enrollment is active with the next step due, or finished with nothing due"
    else:
        want = f"{base}:end"
        outcome = payload.get("outcome")
        if outcome not in OUTCOMES:
            return "its outcome must be one of " + ", ".join(OUTCOMES)
        if payload.get("seq_outcome") != f"{slug}/{outcome}" or payload.get("seq_step_outcome") != f"{slug}/{step}/{outcome}" or state != outcome:
            return "its seq_outcome, seq_step_outcome and state do not agree with its outcome"
        if payload.get("next_due") is not None or payload.get("next_step") is not None:
            return "a sequence that has ended has nothing due"
    if key != want:
        return f"it does not carry the once-only key the seq commands give this row ({show(want)}): build it with the seq command, not with row"
    return None


def sequence_move_problem(known, d):
    """Why a sequence row is not a valid next move from where its enrollment stands, or
    None. `known` gives an index document by its id as it stands when the row would be
    sealed: what the index holds, with the rows sealed before it in the same round. This
    cannot be known when the row is made, so it is decided at `seal`: a step that arrives
    after the sequence ended would otherwise set the enrollment going again, and a step
    built from another list of steps would finish it early or run on past its end."""
    if d.verb not in SEQUENCE_VERBS or d.verb == "sequence_defined":
        return None

    def held(entity):
        doc = known(entity_doc(entity))
        return doc if isinstance(doc, dict) else {}

    state = held(d.entity)
    stands = text_of(state, "f_state")
    done = num_of(state, "f_step")
    step = int(d.payload["step"])
    if d.verb == "sequence_enrolled":
        return None if stands is None else f"the target is enrolled in this sequence already (its enrollment stands at {show(stands)})"
    if stands is None:
        return "the target is not enrolled in this sequence: no sealed row enrolls it"
    if d.verb == "sequence_step_done":
        if stands != "active":
            return f"the enrollment is not active (it stands at {show(stands)}): a step now would set an ended sequence going again"
        if step != done + 1:
            return f"step {step} is not the next step: {done} are done"
        # How many steps the sequence had when the target was enrolled is on the enrollment
        # (or, for one made before that was kept, on the definition, if it was read). The
        # row must have been built from that many: it finishes after the last and only
        # then, and what it names as next is the step after it of that many.
        count = num_of(state, "f_step_count") or num_of(held("sequence:" + d.payload["sequence"]), "f_step_count")
        ends = d.payload["state"] == "finished"
        if not count or (ends == (step == count) and (ends or str(d.payload.get("next_step")).startswith(f"Step {step + 1} of {count}, "))):
            return None
        wrong = f"finishes the sequence after step {step}" if ends else f"does not name step {step + 1} of {count} as the next" if step < count else f"goes on after step {step}"
        return f"it does not agree with the sequence this enrollment was made from, which has {count} steps: it {wrong}. Build it again with seq-step, giving the steps of that sequence"
    if stands not in ("active", "finished"):
        return f"the sequence has ended for this target already (it stands at {show(stands)})"
    return None if step == done else f"it says the sequence ended after step {step}, and {done} steps are done"


def cli_seq(cmd, flags):
    reg = load_registry(flags, cmd)
    out = need(flags, "out", cmd)
    slug = need(flags, "sequence", cmd)
    if not is_slug(slug):
        raise Refused(f"--sequence {show(slug)} must be a short id in lowercase letters, digits and hyphens, like outbound-intro")
    if flag(flags, "on") is not None:
        on = parse_date(flags["on"])
        if on is None:
            raise Refused(f"--on {show(flags['on'])} must be a date like 2026-11-27, from 1970 to 9999")
    else:
        on = now_secs() // 86400
    what = flag(flags, "what")
    payload = {}
    if what is not None:
        payload["what"] = what

    def step_flag(most):
        done = parse_unsigned(need(flags, "step", cmd))
        if done is None or done > most:
            raise Refused(f"--step must be a whole number from 0 to {most}")
        return done

    # The definition is its own entity; everything else is about one target's enrollment.
    if cmd == "seq-define":
        steps = read_steps(flags, cmd)
        payload["steps"] = steps_json(steps)
        payload["step_count"] = num(len(steps))
        verb, entity, key = "sequence_defined", "sequence:" + slug, None
    else:
        target = need(flags, "target", cmd)
        if not is_entity(target) or ":" not in target:
            raise Refused(f"--target {show(target)} must be the entity being taken through the sequence, like client:alder-example")
        entity = f"enrollment:{slug}/{target.replace(':', '.')}"
        if not is_entity(entity):
            raise Refused("the sequence id and target together are too long")
        base = f"seq:{slug}:{target}"
        payload["sequence"] = slug
        payload["target"] = target
        # The day it happened. The row's own `ts` is always the time of writing, because rows
        # are sealed in `ts` order and an enrollment's rows must stay in the order written.
        payload["on"] = date_of(on)
        if cmd == "seq-enroll":
            steps = read_steps(flags, cmd)
            text, due = next_after(steps, 0, on)
            payload.update({"step": num(0), "step_count": num(len(steps)), "next_step": text, "next_due": due, "state": "active"})
            if flag(flags, "owner") is not None:
                payload["owner"] = flags["owner"]
            verb, key = "sequence_enrolled", base
        elif cmd == "seq-step":
            steps = read_steps(flags, cmd)
            done = step_flag(len(steps))
            if done == 0:
                raise Refused("--step is the number of the step just done, from 1")
            this = steps[done - 1]
            payload.update({"step": num(done), "seq_step": f"{slug}/{done}", "channel": this.channel})
            if what is None:
                payload["what"] = this.what
            following = next_after(steps, done, on)
            if following is not None:
                payload.update({"next_step": following[0], "next_due": following[1], "state": "active"})
            else:
                payload.update({"next_step": None, "next_due": None, "state": "finished"})
            verb, key = "sequence_step_done", f"{base}:step:{done}"
        else:
            outcome = need(flags, "outcome", cmd)
            if outcome not in OUTCOMES:
                raise Refused(f"--outcome {show(outcome)} must be one of: {', '.join(OUTCOMES)}")
            done = step_flag(20)
            payload.update({
                "outcome": outcome,
                "step": num(done),
                "seq_outcome": f"{slug}/{outcome}",
                "seq_step_outcome": f"{slug}/{done}/{outcome}",
                "state": outcome,
                "next_step": None,
                "next_due": None,
            })
            verb, key = "sequence_ended", f"{base}:end"

    m = {"verb": verb, "entity": entity}
    for name in ["subject", "source", "actor"]:
        if flag(flags, name) is not None:
            m[name] = flags[name]
    if key is not None:
        m["idempotency_key"] = key
    m["ts"] = iso_utc(now_secs())
    m["payload"] = payload
    draft = check_draft(m, reg)
    # `marker` is the index document that exists once a row with this key has been sealed.
    marker = ',"marker":' + json_text(key_doc(verb, entity, key)) if key is not None else ""
    emit(out, draft, ',"entity":' + json_text(entity) + marker)
    return True


# ---------- seal ----------


def at_of(doc):
    """When a waiting row was written, in milliseconds, or None if what it holds under `at`
    is not a time. A document with no `at` was written before rows carried one, and is
    taken as 0."""
    if "at" not in doc:
        return 0
    at = doc["at"]
    return int(at) if is_number(at) and is_whole(at) and 0 <= at <= MAX_INT else None


def pick(inbox, reg, cap, carried=False, leave=()):
    """The inbox rows one seal takes: oldest first, as many as fit one batch and one read.
    Returns (rows, skipped, remaining, left): rows as (inbox document id, checked draft) in
    the order they will be sealed, skipped as (inbox document id, why it was left where it
    is), and how many valid rows were named in `leave` and passed over. `carried` says the
    documents reached this folder by hand, so one that holds no checksum is left too.

    The order is: the row's `ts`; then `at`, the millisecond it was written; then, for two
    rows written in the same millisecond, the order of their document ids. The last is
    arbitrary, and it is the same every time."""
    good, skipped = [], []
    left = 0
    there = set()
    # A document that cannot be read or does not pass the checks is left where it is; it
    # never stops the rest from being sealed.
    for doc_id, doc, why in docs_each(inbox):
        there.add(doc_id)
        try:
            # The name is the document's id, and the plan will name a removal by it.
            if not is_file_name(doc_id):
                raise Refused("its file's name is not a document id (letters, digits, _ . and -), so it is not a document of the inbox")
            if why is not None:
                raise Refused(why)
            problem = checksum_problem(as_obj(doc))
            if problem is not None:
                raise Refused(problem)
            if carried and CHECKSUM not in doc:
                raise Refused("it holds no checksum (ck), so a miscopy of it could not be caught: seal it from a session whose database tool passes files")
            at = at_of(doc)
            if at is None:
                raise Refused("its at (when it was written) is not a whole number of milliseconds")
            d = check_draft(doc, reg)
            if too_far_ahead(d.ts):
                raise Refused("its ts is more than a day in the future")
            if doc_id in leave:
                left += 1
                continue
            good.append((doc_id, d, at))
        except Refused as why_not:
            skipped.append((doc_id, str(why_not)))
    for doc_id in sorted(set(leave) - there):
        raise Refused(f"--leave: {show(doc_id)} is not a document in the inbox folder given. Nothing was done")
    good.sort(key=lambda x: (x[1].ts, x[2], x[0]))
    keys = {"head"}
    rows = []
    for doc_id, d, _ in good:
        wider = keys | keys_of(d)
        # Each row is one stream write and one inbox delete; each key is at most one write.
        if len(rows) >= cap or len(wider) > MAX_KEYS or (len(rows) + 1) * 2 + len(wider) > MAX_WRITES:
            break
        keys = wider
        rows.append((doc_id, d))
    return rows, skipped, len(good) - len(rows), left


def keys_of(d):
    """The index documents sealing one row touches, besides the tip."""
    keys = {"v." + d.verb, entity_doc(d.entity)}
    if d.key is not None:
        keys.add(key_doc(d.verb, d.entity, d.key))
    return keys


def keys_for(rows):
    keys = {"head"}
    for _, d in rows:
        keys |= keys_of(d)
    return sorted(keys)


def pairs(flags, name):
    """`name=number` pairs from a flag such as --versions."""
    out = {}
    for pair in flags.get(name, "").split(","):
        if trim(pair) == "":
            continue
        key, equals, value = pair.rpartition("=")
        number = parse_unsigned(trim(value))
        if equals == "" or number is None:
            raise Refused(f"--{name}: {show(pair)} is not name=number")
        out[trim(key)] = number
    return out


def count_flag(flags, name, otherwise, least=0):
    """A whole number given for an option such as --max, or `otherwise` when it was not
    given. Anything else is refused: a limit that is quietly ignored is no limit."""
    if name not in flags:
        return otherwise
    number = parse_unsigned(trim(flags[name]))
    if number is None or number < least or number > MAX_INT:
        raise Refused(f"--{name}: {show(flags[name])} is not a whole number from {least}")
    return number


def cap_of(flags, otherwise):
    return count_flag(flags, "max", otherwise, 1)


def leave_of(flags):
    """--leave ID[,ID...]: inbox documents this round passes over. A row that cannot be
    sealed for a reason that will not go away (it would take a total out of range) would
    otherwise be first in line every round."""
    ids = [trim(x) for x in flags.get("leave", "").split(",") if trim(x) != ""]
    for doc_id in ids:
        if not is_file_name(doc_id):
            raise Refused(f"--leave: {show(doc_id)} is not a document id")
    return set(ids)


def versions_read(folder_path, typed, name, collection, whole=False):
    """The version each document of a folder was read at, for a seal whose documents were
    carried by hand. The folder must have been filled by `ingest` from a read of exactly
    `collection`, an empty read included; `whole` says the seal has to know that the read
    left nothing out. The versions are the ones the manifest holds; any the caller typed
    must agree with them."""
    manifest = read_manifest(folder_path)
    if manifest is None or manifest["collection"] != collection:
        got = "no read manifest" if manifest is None else f"a read of collection {show(manifest['collection'])}"
        raise Refused(
            f"{show_path(folder_path)} holds {got}, and this seal needs a read of {show(collection)} there. Nothing was planned."
            " With --inline, every read is saved as text and passed through ingest, an empty result included (with --collection)"
        )
    if whole and not manifest["complete"]:
        raise Refused(
            f"the read of collection {collection} that was ingested into {show_path(folder_path)} was not complete: {manifest['why']}."
            " A document this seal builds on may be missing from it. Nothing was planned. Read it again, whole, into a new folder"
        )
    out = dict(typed)
    for entry in manifest["documents"]:
        if entry["version"] is None:
            continue
        if entry["id"] in typed and typed[entry["id"]] != entry["version"]:
            raise Refused(
                f"--{name} says {show(entry['id'])} is at version {typed[entry['id']]}, and the read that was ingested says {entry['version']}."
                " Nothing was planned. Pass the versions as ingest printed them, or leave the option off"
            )
        out[entry["id"]] = entry["version"]
    return out


def cli_seal_keys(flags):
    reg = load_registry(flags, "seal-keys")
    cap = cap_of(flags, 20)
    # --inline here, as on `seal`, so that both choose the same rows.
    rows, skipped, remaining, left = pick(folder(flags, "inbox", "seal-keys"), reg, cap, "inline" in flags, leave_of(flags))
    keys = ",".join(json_text(k) for k in keys_for(rows))
    say('{"rows":%d,"remaining":%d,"skipped":%d,"left":%d,"keys":[%s]}' % (len(rows), remaining, len(skipped), left, keys))
    return True


def doc_bytes(doc):
    """How large a document is as it will be stored, or None if it cannot be written."""
    text = canonical_or_none(with_checksum(doc)) if canonical_or_none(doc) is not None else None
    return None if text is None else utf8_len(text)


def cli_seal(flags):
    reg = load_registry(flags, "seal")
    inbox_dir = folder(flags, "inbox", "seal")
    ix_dir = folder(flags, "ix", "seal")
    out = need(flags, "out", "seal")
    account = trim(need(flags, "account", "seal"))
    if not is_actor(account):
        raise Refused("--account must be the writer's user id")
    inbox_collection = flags.get("inbox-collection", f"data/users/{account}/as/inbox")
    if not all(is_file_name(part) for part in inbox_collection.split("/")):
        raise Refused("--inbox-collection must be the path of a collection")
    cap = cap_of(flags, 20)
    versions = pairs(flags, "versions")
    inbox_versions = pairs(flags, "inbox-versions")
    # --inline: the session cannot pass files, so every document here was carried by hand.
    inline = "inline" in flags
    if inline:
        # Carried by hand, the documents came through `ingest`, which recorded what each
        # read was: of which collection, each document's version, so that none is typed
        # against the wrong document, and whether the read of the index was whole (a tally
        # lost on the way would otherwise be taken for one that does not exist yet). An
        # inbox read of another account, or a folder nothing was ingested into, shows nothing.
        inbox_versions = versions_read(inbox_dir, inbox_versions, "inbox-versions", inbox_collection)
        versions = versions_read(ix_dir, versions, "versions", "ix", True)

    rows, skipped, remaining, left = pick(inbox_dir, reg, cap, inline, leave_of(flags))
    kept = f"{len(skipped)} skipped" + (", %d left as --leave asked" % left if left else "")
    if not rows:
        for doc_id, why in skipped:
            warn(f"skipped {show(doc_id)}: {why}")
        if skipped:
            # Documents were waiting and none of them is a row that can be sealed: that is
            # not an inbox with nothing in it, and it does not end like one.
            say(f"NO PROGRESS: {len(skipped)} waiting documents were given and none of them can be sealed, for the reasons given ({kept}). Nothing was planned and nothing was written")
            return False
        say(f"nothing to seal ({kept})")
        return True

    # What the index held when it was read. A missing file is a document that does not exist.
    had = dict(docs_in(ix_dir))
    # A seal builds on these documents, so one that did not arrive whole stops it. Nothing
    # has been written at this point.
    for key in sorted(had):
        doc = had[key]
        if not is_file_name(key) or not isinstance(doc, dict):
            raise Refused(f"{index_doc_name(key)} is not a document of the index. Nothing was planned")
        if checksum_problem(doc) is not None:
            raise Refused(f"ix/{key}: {CHECKSUM_WRONG}. Nothing was planned. Read that document again and start this round over; never edit a document to make it pass")
        if CHECKSUM not in doc:
            if inline:
                raise Refused(
                    f"ix/{key} holds no checksum (ck), so a miscopy of it could not be caught. Nothing was planned."
                    " Seal from a session whose database tool passes files, or have one run rebuild --checksums first"
                )
        elif text_of(doc, "k") != key:
            # Every index document names itself in `k`, which its checksum covers.
            raise Refused(f"ix/{key}: the document under this name says it is {show(text_of(doc, 'k') or '')}. Nothing was planned. Read it again and start this round over")
    for key in keys_for(rows):
        if key in had and key not in versions:
            raise Refused(f"ix/{key} exists: pass its version, e.g. --versions {key}=3")
    head = had.get("head") if isinstance(had.get("head"), dict) else None
    tip_id = num_of(head, "id") if head is not None else 0
    tip_fp = (text_of(head, "fingerprint") or "") if head is not None else ""
    tip_ts = ""

    tallies = {}
    entities = {}
    used = {}
    stream = []
    duplicates = []
    not_kept = []
    # Inbox documents this seal takes out: the rows it seals and the repeats it drops.
    taken = []
    # Rows that passed every check a row can be given on its own and cannot be sealed onto
    # the index as it stands. They stay in the inbox.
    stuck = []

    for doc_id, d in rows:
        spec = reg[d.verb]
        if d.verb not in tallies:
            tallies[d.verb] = tally_from_json(had.get("v." + d.verb))
        tally = tallies[d.verb]
        marker = key_doc(d.verb, d.entity, d.key) if d.key is not None else None
        # A key is stored once: a row whose key is sealed already is a repeat, whatever
        # its verb, and is dropped from the inbox.
        if marker is not None and (marker in had or marker in used):
            duplicates.append(doc_id)
            taken.append(doc_id)
            continue
        # A total may not leave the range every reader keeps exact. The row stays in the inbox.
        if not tally_fits(tally, d, spec):
            stuck.append((doc_id, "it would take a running total past what can be stored exactly"))
            continue
        row_id = tip_id + 1
        ek = entity_doc(d.entity)
        before = entities.get(ek, had.get(ek))
        # A sequence row must be a move its enrollment can make from where it stands now.
        wrong_move = sequence_move_problem(lambda key: entities.get(key, had.get(key)), d)
        if wrong_move is not None:
            stuck.append((doc_id, wrong_move))
            continue
        # No document may be made larger than the database takes. The two that grow are the
        # verb's tally and the entity's state, so both are built and measured before the
        # row is given its place. A document that is over already, as an older log's may
        # be, and is not made larger by this row, does not hold the row back.
        counted = tally_copy(tally)
        tally_add(counted, row_id, d, spec)
        lost = []
        state = entity_json(before, row_id, d, spec, lost)
        grown = [("the tally for '%s'" % d.verb, tally_json(tally, d.verb), tally_json(counted, d.verb)), ("the state of its entity", before, state)]
        too_large = None
        for what, old, new in grown:
            size, was = doc_bytes(new), doc_bytes(old) if isinstance(old, dict) else 0
            if size is None:
                too_large = f"{what} could not be written with it"
            elif size > MAX_DOC_BYTES and size > (was or 0):
                too_large = f"it would make {what} {size} bytes, more than the {MAX_DOC_BYTES} this script will plan for one document"
            if too_large is not None:
                break
        if too_large is not None:
            stuck.append((doc_id, too_large))
            continue
        if marker is not None:
            used[marker] = row_id
        row, fp = seal_row(d, row_id, account, tip_fp)
        promote(row, spec)
        tallies[d.verb] = counted
        entities[ek] = state
        if lost:
            not_kept.append((row_id, doc_id, lost))
        taken.append(doc_id)
        stream.append((row_id, row))
        tip_id, tip_fp, tip_ts = row_id, fp, d.ts

    skipped += stuck
    kept = f"{len(skipped)} skipped, {left} left as --leave asked, {remaining} remaining"
    for doc_id, why in skipped:
        warn(f"skipped {show(doc_id)}: {why}")
    if not taken:
        # Rows were taken up and none could be sealed. That is not a round that sealed
        # nothing because nothing was waiting, and it does not end like one: the same rows
        # would be first in line next time.
        say(
            f"NO PROGRESS: {len(rows)} waiting rows were taken up and none could be sealed, for the reasons given ({kept}). Nothing was planned and nothing was written."
            f" They stay in the inbox. To seal the rows behind them, run seal-keys and seal again with --leave {','.join(doc_id for doc_id, _ in stuck)}"
        )
        return False

    plan = Plan(out)

    def put(doc_id, doc, pinned):
        # Every index document gets a checksum, because a later seal has to read it back.
        text = plan.set("ix", doc_id, with_checksum(doc), versions[doc_id] if pinned and doc_id in had else None)
        if utf8_len(text) > MAX_DOC_BYTES:
            warn(f"note: ix/{doc_id} is {utf8_len(text)} bytes, over the {MAX_DOC_BYTES} this script lets a document grow to. It was that large already, and this plan does not make it larger")

    # Sealed rows are stored exactly as fingerprinted.
    for row_id, row in stream:
        plan.set("stream", stream_doc(row_id), row)
    if stream:
        count = (num_of(head, "n") if head is not None else 0) + len(stream)
        put("head", {"k": "head", "id": num(tip_id), "fingerprint": tip_fp, "ts": tip_ts, "n": num(count)}, True)
    sealed_verbs = {row["verb"] for _, row in stream}
    for verb in sorted(tallies):
        if verb in sealed_verbs:
            put("v." + verb, tally_json(tallies[verb], verb), True)
    for key in sorted(entities):
        put(key, entities[key], True)
    for key in sorted(used):
        put(key, {"k": key, "id": num(used[key])}, False)
    # An inbox document is at version 1 unless someone wrote it twice; the caller says which.
    for doc_id in taken:
        plan.delete(inbox_collection, doc_id, inbox_versions.get(doc_id, 1))

    # A record keeps at most sixty current details. What a row carried beyond that is in the
    # row, and not on the record: said here, so that it is not found out later.
    for row_id, doc_id, lost in not_kept:
        warn(f"note: row {row_id} ({show(doc_id)}): its record holds {MAX_CURRENT} current details already, so these were not kept as current: {', '.join(lost)}. The row itself holds them")
    first_id = stream[0][0] if stream else 0
    summary = '"sealed":%d,"first_id":%d,"last_id":%d,"duplicates":[%s],"skipped":[%s],"left":%d,"remaining":%d' % (
        len(stream), first_id, tip_id, quoted(duplicates), quoted([doc_id for doc_id, _ in skipped]), left, remaining,
    )
    if not_kept:
        summary += ',"not_kept":[%s]' % ",".join('{"id":%d,"fields":[%s]}' % (row_id, quoted(lost)) for row_id, _, lost in not_kept)
    # `after` is what the tip must hold once the ops are applied. The session reads ix/head
    # back and compares, which shows whether its write was the one that landed.
    after = '{"path":"ix/head","id":%d,"fingerprint":%s}' % (tip_id, json_text(tip_fp))
    plan.say_how(*plan.write(summary, after if inline else None))
    if inline:
        say(f"then read ix/head back: it must hold id {tip_id} and fingerprint {tip_fp}" if stream else "no row is sealed by this write, so ix/head stays as it was")
    say(f"ready to seal {len(stream)} rows as ids {first_id} to {tip_id} ({len(duplicates)} duplicates dropped, {kept}); planned, not stored")
    return True


def quoted(texts):
    return ",".join(json_text(x) for x in texts)


def promote(row, spec):
    """Copy a row's filterable payload fields to its top level as `p_<field>`. They are not
    part of the fingerprint; they exist because the database filters only on top-level
    fields."""
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return
    for f in spec.index:
        if isinstance(payload.get(f), (str, bool, float)):
            row["p_" + f] = payload[f]


# ---------- ingest ----------
#
# Where the database tool cannot save a read to files, the result arrives as text and the
# assistant saves that text to a file. This turns such a file into what a read that saves
# files would have left: one file per document. The text was copied by hand, so the
# layouts it may have are few and exact, and anything else is refused: a guess at what a
# damaged paste meant is how a document goes missing without anyone noticing.
#
#   1. The chat tools' own layout: one JSON object of the tool's (a notice, `count`, a
#      cursor), then a tag, one document on each line inside it, and the closing tag.
#   2. One JSON object holding the documents as a list under `docs` or `documents`, with a
#      cursor and perhaps a `count` beside it: `{"docs": [...], "nextCursor": ""}`.
#   3. A JSON array of documents.
#   4. One document on its own; with --id, its bare contents.

# Names a document's id, and its version, may be under.
ID_NAMES = ["id", "doc_id", "path"]
VERSION_NAMES = ["version", "ifVersion", "if_version"]
# What a read of one document may say of it around its contents ("data").
WRAPPER_NAMES = ID_NAMES + VERSION_NAMES + ["data", "exists", "updatedAt", "createdAt"]
# The names layout 2 takes its list under, and the names other tools use, which are known
# only so that a result laid out that way is refused by name.
LIST_NAMES = ["docs", "documents"]
OTHER_LIST_NAMES = ["results", "result", "items", "data"]
CURSOR_NAMES = ["nextCursor", "next_cursor", "cursor"]
# What a read tool says of its own beside the documents.
ENVELOPE_NAMES = ["notice", "count"] + CURSOR_NAMES
# File names Windows keeps for devices, whatever extension follows.
DEVICE_NAMES = ["con", "prn", "aux", "nul"] + ["com%d" % n for n in range(1, 10)] + ["lpt%d" % n for n in range(1, 10)]
OPENING_TAG = re.compile("<([A-Za-z][A-Za-z0-9_-]{0,80})>")
SHAPES = (
    "It must be a read result exactly as the tool returned it: the tool's own object and then one document on each line inside a tag;"
    ' or one object holding the documents as a list under "docs"; or a JSON array of documents; or one document.'
    ' Each document is {"id" or "path": ..., "data": {...}, "version": ...}'
)


def is_file_name(x):
    """Whether a document's id may be used as a file name: up to 100 letters, digits, `_`,
    `.` and `-`, starting with a letter or a digit, not ending in a dot (Windows drops it,
    so two ids would be one file), and not a name Windows keeps for a device. That leaves
    no room for a path, a hidden file or a drive. Every id this script gives a document
    passes."""
    if not (isinstance(x, str) and 1 <= len(x) <= 100 and x[0] in ASCII_ALNUM and all(c in ASCII_ALNUM + "_.-" for c in x)):
        return False
    return not x.endswith(".") and x.partition(".")[0].translate(TO_ASCII_LOWER) not in DEVICE_NAMES


def first_object(text):
    """Where the JSON object that a text starts with ends: the index after its closing
    brace, or None if the text does not start with an object that closes."""
    if not text.startswith("{"):
        return None
    depth, in_string, escaped = 0, False, False
    for at, c in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return at + 1
    return None


def is_envelope(v):
    """Whether an object is the tool's own and not a document: every field it has is one a
    read tool says of itself."""
    return isinstance(v, dict) and len(v) > 0 and all(name in ENVELOPE_NAMES for name in v)


def envelope_says(v):
    """What a tool's own object says about the result: (how many documents it declares, or
    None; whether more documents follow: True, False, or None when it does not say)."""
    declared = None
    if "count" in v:
        given = v["count"]
        if not (is_number(given) and is_whole(given) and 0 <= given <= MAX_INT):
            raise Refused('the read result\'s "count" is not a whole number')
        declared = int(given)
    more = None
    for name in CURSOR_NAMES:
        if name in v:
            if v[name] is None or v[name] == "":
                more = bool(more)
            elif isinstance(v[name], str):
                more = True
            else:
                raise Refused(f'the read result\'s "{name}" is not text')
    return declared, more


def tagged_result(text):
    """Layout 1, or None if the text is not laid out that way at all. Once it starts that
    way it must be that way throughout: the tool's object, the tag, one whole document on
    each line, the closing tag at the start of a line, and after it only the tool's words."""
    end = first_object(text)
    if end is None:
        return None
    tag = OPENING_TAG.match(text, end + len(text[end:]) - len(text[end:].lstrip(JSON_SPACE)))
    if tag is None:
        return None
    try:
        envelope = parse(text[:end])
    except Refused as why:
        raise Refused(f"the tool's own object before the tag: {why}")
    if not is_envelope(envelope):
        raise Refused("the object before the tag is not the read tool's own: it holds fields a read tool does not say of itself")
    declared, more = envelope_says(envelope)
    if declared is None:
        raise Refused('the tool\'s own object before the tag does not say how many documents follow ("count")')
    closing = "\n</" + tag.group(1) + ">"
    close_at = text.find(closing, tag.end())
    if close_at < 0:
        raise Refused(f"the closing {show(closing[1:])} is missing, or is not at the start of a line: the result was cut short")
    after = text[close_at + len(closing) :]
    if any(c in after for c in "{}[]<>"):
        raise Refused("there is more after the closing tag than the tool's own words: two results may have been saved into one file")
    found = []
    lines = text[tag.end() : close_at].split("\n")
    # The opening tag ends its own line, so the rest of that line is not a document.
    for at, line in enumerate(lines[1:] if trim(lines[0]) == "" else lines, 1):
        line = trim(line)
        if line == "":
            continue
        # Each line is one whole document. A stray bracket, half a document or two on a
        # line is a paste that went wrong, and none of it is used.
        try:
            one = parse(line)
        except Refused as why:
            raise Refused(f"line {at} inside the tag is not one whole document: {why}")
        if not isinstance(one, dict):
            raise Refused(f"line {at} inside the tag is not a document: each line there is one JSON object")
        found.append(one)
    return found, declared, more


def without_app_notice(text):
    """A read result without the lines an app may put above a tool's output (a notice that
    other people wrote some of the content, for one). Only whole lines before the first
    bracket are set aside, at most four of them, and only if none of them could be part of
    a document: no closing bracket, no `"name":`, no comma at its end. A result that
    starts partway through a document is not one of these, and is refused."""
    starts = [at for at in (text.find("{"), text.find("[")) if at >= 0]
    if not starts or min(starts) == 0:
        return text
    lines = text[: min(starts)].split("\n")
    if lines[-1] != "" or len(lines) > 5 or any(re.search('"\\s*:|[}\\]]|,\\s*$', line) for line in lines):
        return text
    return text[min(starts) :]


def read_result(text, bare=False):
    """The documents a saved read result holds, in one of the four layouts above:
    (documents, how many the result declares or None, whether more follow or None when it
    does not say). `bare` says the caller named the one document with --id: then an object
    that is not the tool's wrapper around a document is that document's contents, whole,
    and `{"exists": false}` is a read that found no such document: (None, None, None)."""
    if text.startswith("﻿"):
        text = text[1:]
    text = without_app_notice(trim(text))
    try:
        v = parse(text)
    except Refused as why:
        if not str(why).startswith("not JSON"):
            raise
        tagged = tagged_result(text)
        if tagged is None:
            raise Refused(f"{why}. {SHAPES}")
        return tagged
    if isinstance(v, list):
        return v, None, None
    if not isinstance(v, dict):
        raise Refused("no documents found. " + SHAPES)
    lists = [name for name in LIST_NAMES + OTHER_LIST_NAMES if isinstance(v.get(name), list)]
    if len(lists) > 1:
        raise Refused(f"it holds more than one list ({', '.join(lists)}), and nothing says which one is the documents. Nothing was written")
    if lists:
        if lists[0] not in LIST_NAMES:
            raise Refused(f'it holds a list under "{lists[0]}", which is not a layout this script knows. ' + SHAPES)
        extra = sorted(name for name in v if name != lists[0] and name not in ENVELOPE_NAMES)
        if extra:
            raise Refused(f"beside its documents it holds {show(extra[0])}, which is not something a read tool says of itself. Nothing was written. " + SHAPES)
        declared, more = envelope_says(v)
        return v[lists[0]], declared, more
    if bare:
        if v == {"exists": False}:
            return None, None, None
        # The tool's wrapper is "data" beside nothing but what a tool says of a document.
        # Anything else is the document itself, and none of its fields is dropped.
        wrapped = isinstance(v.get("data"), dict) and all(name in WRAPPER_NAMES for name in v)
        return [v if wrapped else {"data": v}], None, None
    if isinstance(v.get("data"), dict) or any(isinstance(v.get(name), str) for name in ID_NAMES):
        return [v], None, None
    if is_envelope(v):
        # The tool's own object and nothing else: a result that holds no documents.
        declared, more = envelope_says(v)
        if declared is not None:
            return [], declared, more
    raise Refused("no documents found. " + SHAPES)


def document_parts(entry):
    """One document of a read result as (collection, id, contents, version, why it cannot
    be used). The id is under `id` or `doc_id`, or is the last part of `path`; any of them
    may be the document's full path. Where it is named, or its version given, more than
    once, the names must agree: none is chosen over another. The collection is None when
    the result does not say."""
    if not isinstance(entry, dict):
        return None, None, None, None, "it is not an object"
    named = [entry[name].rpartition("/") for name in ID_NAMES if isinstance(entry.get(name), str)]
    doc_id = (named[0][2] or None) if named else None
    collections = {collection for collection, _, _ in named if collection != ""}
    if len({last for _, _, last in named}) > 1 or len(collections) > 1:
        return None, doc_id, None, None, "its id, doc_id and path do not name the same document"
    contents = entry.get("data")
    if not isinstance(contents, dict):
        return None, doc_id, None, None, 'it has no contents under "data"'
    versions = set()
    for name in VERSION_NAMES:
        given = entry.get(name)
        if is_number(given) and is_whole(given) and 1 <= given <= MAX_INT:
            versions.add(int(given))
        elif isinstance(given, str) and parse_unsigned(given) and parse_unsigned(given) <= MAX_INT:
            versions.add(parse_unsigned(given))
        elif given is not None:
            return None, doc_id, None, None, f'its "{name}" is not a whole number from 1'
    if len(versions) > 1:
        return None, doc_id, None, None, "it gives its version more than once, and not the same one: " + ", ".join(str(x) for x in sorted(versions))
    return (collections.pop() if collections else None), doc_id, contents, (versions.pop() if versions else None), None


# Beside the documents, `ingest` leaves one more file, which is not a document: what the
# read was. A folder of files cannot say by itself which collection it is a read of,
# whether it holds all of it, which version each file was read at, or whether its files
# are still the ones that read gave, and the commands that need to know have only the
# folder. So the manifest holds the collection, each document's id, version and the
# SHA-256 of the file written for it, and whether the read was whole. A longer read has
# one manifest for each page, and is whole only when page 1 is there, the pages follow
# one another and the last ends without a cursor. It cannot show that the read was
# current, or that a query was not narrowed by a filter.
MANIFEST = "_read.manifest"
REINGEST = "Read the documents again and ingest them into a new folder; never add, remove or edit a file in a folder ingest wrote"


def manifest_name(page):
    return MANIFEST if page == 1 else "_read.%d.manifest" % page


def names_in(folder_path):
    """The names a folder holds, as its listing gives them. A document is looked up among
    these and compared exactly, never by asking the file system for a path: some would
    answer for `A.json` when asked for `a.json`. Two names that differ only in capitals
    are refused: on such a system they would be one file."""
    try:
        names = os.listdir(folder_path)
    except OSError as error:
        raise Refused(f"{show_path(folder_path)}: {os_error_text(error, folder_path)}")
    lowered = {}
    for name in names:
        if lowered.setdefault(name.lower(), name) != name:
            raise Refused(f"{show_path(folder_path)}: holds {show(lowered[name.lower()])} and {show(name)}, names that differ only in capitals. Nothing was done: read the documents again into a new folder")
    return names


def child(folder_path, name):
    """The path of `name` in a folder, or None unless the folder's listing holds exactly
    that name."""
    return os.path.join(folder_path, name) if folder_path is not None and os.path.isdir(folder_path) and name in names_in(folder_path) else None


def read_manifest(folder_path):
    """What `ingest` recorded about the read that filled a folder, or None if the folder
    holds no manifest: {collection, declared, complete, why, absent, more, pages,
    documents: [{id, version}]}. The folder must still hold exactly the files `ingest`
    wrote there: a manifest that was changed, or a file added, removed or swapped for
    another, is refused by name, as evidence of a read that did not happen. So is a
    manifest written by version 0.7.0, which holds no digests."""
    names = names_in(folder_path)
    if MANIFEST not in names:
        return None
    where = show_path(folder_path)
    pages = []
    while manifest_name(len(pages) + 1) in names:
        path = os.path.join(folder_path, manifest_name(len(pages) + 1))
        try:
            doc = parse(read_text(path, inside=True))
        except Refused as why:
            raise Refused(f"{show_path(path)}: {why}")
        if isinstance(doc, dict) and doc.get("rowkeep_read") == 1:
            raise Refused(f"{where}: its read manifest was written by version 0.7.0 and holds no digest of the documents, so nothing ties these files to that read. Ingest the read again, into a new folder")
        entries = doc.get("documents") if isinstance(doc, dict) else None
        good = (
            isinstance(entries, list) and CHECKSUM in doc and checksum_problem(doc) is None and doc.get("rowkeep_read") == 2
            and isinstance(doc.get("complete"), bool) and isinstance(doc.get("more"), bool) and isinstance(doc.get("why"), str)
            and isinstance(doc.get("collection"), str) and doc.get("page") == len(pages) + 1
            and (doc.get("absent") is None or is_file_name(doc.get("absent")))
            and all(
                isinstance(e, dict) and is_file_name(e.get("id")) and (e.get("version") is None or num_of(e, "version") >= 1)
                and FINGERPRINT.fullmatch(text_of(e, "sha256") or "") for e in entries
            )
        )
        if not good:
            raise Refused(f"{show_path(path)}: is not a read manifest as ingest wrote it. {REINGEST}")
        pages.append(doc)
    listed = {e["id"]: e for doc in pages for e in doc["documents"]}
    stray = len([name for name in names if name.endswith(".manifest")]) != len(pages)
    if stray or any(not doc["more"] or doc["collection"] != pages[0]["collection"] for doc in pages[:-1]) or len(listed) != sum(len(doc["documents"]) for doc in pages):
        raise Refused(f"{where}: the read manifests there are not the pages of one read as ingest wrote them. {REINGEST}")
    there = [stem_and_extension(name)[0] for name in names if stem_and_extension(name)[1] == "json"]
    for doc_id in sorted(set(there) | set(listed)):
        if doc_id not in listed:
            raise Refused(f"{where}: holds {show(doc_id + '.json')}, which ingest did not write there. {REINGEST}")
        if doc_id not in there:
            raise Refused(f"{where}: the document {show(doc_id)}, which ingest wrote there, is gone. {REINGEST}")
        if sha256_hex(read_bytes(os.path.join(folder_path, doc_id + ".json"), inside=True)) != listed[doc_id]["sha256"]:
            raise Refused(f"{where}: {show(doc_id + '.json')} is not the file ingest wrote from that read: it was changed, or another was put in its place. {REINGEST}")
    last = pages[-1]
    counts = [doc.get("declared") for doc in pages]
    return {
        "collection": last["collection"],
        "declared": None if None in counts else sum(int(n) for n in counts),
        "complete": last["complete"],
        "why": last["why"],
        "absent": last.get("absent"),
        "more": last["more"],
        "pages": len(pages),
        "documents": [{"id": doc_id, "version": None if e.get("version") is None else num_of(e, "version")} for doc_id, e in listed.items()],
    }


def cli_ingest(flags):
    source = need(flags, "from", "ingest")
    out = need(flags, "out", "ingest")
    named = flag(flags, "id")
    if named is not None and not is_file_name(named):
        raise Refused("--id must be a document id: letters, digits, _ . and -")
    said = flag(flags, "collection")
    if said is not None and not all(is_file_name(part) for part in said.split("/")):
        raise Refused("--collection must be the path of a collection, like ix or data/users/<user id>/as/inbox")
    # A read that came back in pages is ingested a page at a time into one folder: the
    # first as usual, each later one with --page saying which it is.
    page = count_flag(flags, "page", 1, 1)
    if page > 1 and named is not None:
        raise Refused("--id names one document, and --page a page of a longer read: give one or the other")
    try:
        found, declared, more = read_result(read_text(source), named is not None)
    except Refused as why:
        raise Refused(f"--from {show_path(source)}: {why}")
    # `{"exists": false}`: a read of one document that found none.
    absent = named if found is None else None
    found = found or []
    # Where the result says how many documents it holds, that many must be here: a document
    # lost in copying must stop the read.
    if declared is not None and len(found) != declared:
        raise Refused(
            f"--from {show_path(source)}: the read result says it holds {declared} documents and {len(found)} are here."
            " Save the whole result again, exactly as it was returned, and start this round over"
        )
    if named is not None and absent is None and len(found) != 1:
        raise Refused(f"--id names one document, and this read result holds {len(found)}")

    parts = []
    # The same id twice, or twice but for capitals (one file, on some systems): neither is
    # used. Every entry that names itself is counted, whatever else is wrong with it.
    times = {}
    for entry in found:
        collection, doc_id, contents, version, why = document_parts(entry)
        if named is not None and why is None:
            if doc_id is not None and doc_id != named:
                why = f"--id says {named}, and the document says it is something else"
            doc_id = named
        if why is None and doc_id is None:
            why = "it does not say which document it is: no id, doc_id or path"
        if why is None and not is_file_name(doc_id):
            why = "its id cannot be used as a file name: only letters, digits, _ . and - can"
        if why is None and collection is not None and not all(is_file_name(part) for part in collection.split("/")):
            why = "the path it gives is not the path of a document"
        if doc_id is not None:
            times[doc_id.lower()] = times.get(doc_id.lower(), 0) + 1
        parts.append([collection, doc_id, contents, version, why])
    # One folder holds one collection. Documents of two collections in one read result mean
    # two results were pasted together, and nothing of it is written.
    collections = sorted({part[0] for part in parts if part[0] is not None and part[4] is None})
    if len(collections) > 1:
        raise Refused("this read result holds documents of more than one collection (%s): save each read to its own file, and ingest each into its own folder" % ", ".join(show(c) for c in collections))
    if said is not None and collections and collections[0] != said:
        raise Refused(f"--collection says {show(said)}, and the documents say they are of {show(collections[0])}. Nothing was written")
    collection = said if said is not None else (collections[0] if collections else None)
    before = None
    if page > 1:
        # A later page goes where the pages before it went, and nowhere else.
        before = read_manifest(out) if os.path.isdir(out) and not is_link(out) else None
        if before is None or before["pages"] != page - 1 or before["collection"] != (collection or before["collection"]) or not before["more"]:
            raise Refused(
                f"--page {page}: {show_path(out)} does not hold pages 1 to {page - 1} of a read of this collection, the last of them ending with a cursor."
                " Ingest the pages of one read in order, the first without --page. Nothing was written"
            )
        collection = before["collection"]
    # A folder that does not say which collection it is a read of is evidence of nothing:
    # an empty result of one collection would pass for an empty result of any other.
    if collection is None:
        raise Refused("ingest needs --collection PATH: nothing in this read result says which collection it is a read of (an empty result never does). Nothing was written")

    earlier = [entry["id"].lower() for entry in before["documents"]] if before is not None else []
    try:
        if page == 1:
            # One read, one folder: a folder that holds anything already is not written
            # into, so that no file from an earlier read is taken for part of this one,
            # whatever its name and however this system compares capitals.
            if os.path.lexists(out) and (is_link(out) or not os.path.isdir(out) or os.listdir(out)):
                raise Refused(f"--out {show_path(out)}: is not a new, empty folder. Give each read a folder of its own; nothing was written")
            make_folders(out)
    except OSError as error:
        raise Refused(f"{show_path(out)}: {os_error_text(error, out)}")
    written = unchecked = 0
    refused, damaged, versions, files, entries = [], [], [], [], []
    for at, (_, doc_id, contents, version, why) in enumerate(parts, 1):
        if why is None and doc_id.lower() in earlier:
            raise Refused(f"--page {page}: this page holds {show(doc_id)}, which an earlier page of the read holds already: these are not the pages of one read. Nothing was written. Read it again from the first page, into a new folder")
        if doc_id is not None and times[doc_id.lower()] > 1:
            why = "this read result holds its id more than once"
        if why is None:
            try:
                text = canonical(contents)
            except Refused as why_not:
                why = str(why_not)
        if why is not None:
            refused.append('{"at":%d,"id":%s,"why":%s}' % (at, display_json((doc_id or "")[:80]), display_json(why)))
            continue
        files.append((out_path(out, doc_id), text))
        entries.append({"id": doc_id, "version": None if version is None else num(version), "sha256": sha256_hex(text.encode("utf-8"))})
        written += 1
        if version is not None:
            versions.append(f"{doc_id}={version}")
        # A document whose checksum is wrong is still written, so that the command that
        # reads it next names it and refuses it; here it is reported early.
        if CHECKSUM not in contents:
            unchecked += 1
        elif checksum_problem(contents) is not None:
            damaged.append('{"id":%s,"why":%s}' % (json_text(doc_id), json_text(CHECKSUM_WRONG)))
    # Whether this folder now holds all there was to read. Only a result that says so
    # itself counts: an array, or one document, says nothing about what else there is. And
    # only a read taken from its start: this page is the first, or follows the pages
    # before it in the same folder.
    if refused:
        complete, why = False, f"{len(refused)} of its documents were refused"
    elif named is not None:
        complete, why = False, "one document was read, not a collection"
    elif more is None:
        complete, why = False, "the result does not say whether more documents follow it (it carries no cursor)"
    elif more:
        complete, why = False, "the result ends with a cursor: more documents follow, and were not read into this folder"
    else:
        complete, why = True, ""
    manifest = {
        "rowkeep_read": num(2), "collection": collection, "page": num(page), "declared": None if declared is None else num(declared),
        "complete": complete, "more": more is True and not refused, "why": why, "absent": absent, "documents": entries,
    }
    new_files(out, files + [(os.path.join(out, manifest_name(page)), canonical(with_checksum(manifest)) + "\n")])
    say(
        '{"documents":%d,"refused":[%s],"damaged":[%s],"unchecked":%d,"versions":%s,"complete":%s,"collection":%s,"page":%d,"folder":%s}'
        % (written, ",".join(refused), ",".join(damaged), unchecked, json_text(",".join(versions)), canonical(complete), json_text(collection), page, display_json(absolute(out)))
    )
    return not refused and not damaged


# ---------- the vault ----------
#
# A second artifact that holds a copy of every sealed row, so the log can be put back if its
# own artifact is lost or damaged. Rows never change once sealed, so the vault only grows:
# each copy adds new chunk documents (`rows/<first id>`, up to 200 rows each) and moves one
# tip document (`vault/head`). The index is not copied; `rebuild` derives it from the rows.
#
# Both kinds of document hold a checksum (`ck`) from version 0.5.0 on. Documents written
# before that hold none and are taken as they are: the rows inside a chunk are checked by
# their fingerprints either way.

VAULT_CHUNK = 200
# The rows of one chunk, in bytes. With the few hundred bytes around them the document stays
# inside MAX_DOC_BYTES, and so well inside what the database takes.
VAULT_CHUNK_BYTES = 180000
# A session that cannot pass files carries every row of a copy three times: in, out inside
# the write, and in again to confirm. One round of such a copy is therefore one small
# chunk. The limit is set by the write, the one leg that is stored as typed: about 6,000
# characters in one write is what has been done in a real chat, and ten ordinary rows are
# about 4,500. A single row longer than that still goes, alone.
VAULT_INLINE_ROWS = 10
VAULT_INLINE_BYTES = 6000
REREAD = "Read it again, into a new folder, and start this round over; never edit a document to make it pass"


def core_row(m):
    """The row as it was fingerprinted, plus the fingerprint: what the vault keeps."""
    return {k: v for k, v in m.items() if not k.startswith("p_")}


def row_fp(m):
    """The fingerprint a row should carry, or None if its contents cannot have one."""
    try:
        return fingerprint({k: v for k, v in m.items() if k != "fingerprint" and not k.startswith("p_")})
    except Refused:
        return None


def chain_problems(rows, after, after_fp):
    """Check a run of rows: ids in step from `after + 1`, each pointing at the one before,
    each fingerprint true to its contents. Returns the problems found."""
    problems = []
    expect, previous = after + 1, after_fp
    for m in rows:
        row_id = num_of(m, "id")
        if row_id != expect:
            problems.append(f"expected row {expect}, found row {row_id}")
        if (text_of(m, "previous") or "") != previous:
            problems.append(f"row {row_id} does not point at the row before it")
        stored = text_of(m, "fingerprint") or ""
        if row_fp(m) != stored:
            problems.append(f"row {row_id}: fingerprint does not match its contents")
        expect = row_id + 1
        previous = stored
    return problems


def vault_rows(folder_path):
    """Every row the vault holds, in order, from its chunk documents, and what is wrong with
    the documents themselves: (rows, faults), each fault as (document, what is wrong). A
    chunk must be an object, sit under the name of its first row, name itself in `k`, and
    match its checksum if it holds one. The rows are checked by the caller, as a chain."""
    chunks = []
    for doc_id, doc in docs_in(folder_path):
        if not isinstance(doc, dict):
            raise Refused(f"vault document {show('rows/' + doc_id)} is not an object. Nothing was planned")
        chunks.append((doc_id, doc))
    chunks.sort(key=lambda chunk: num_of(chunk[1], "first"))
    rows, faults = [], []
    for doc_id, c in chunks:
        name = show("rows/" + doc_id)
        if checksum_problem(c) is not None:
            faults.append((name, CHECKSUM_WRONG))
        if doc_id != stream_doc(num_of(c, "first")):
            faults.append((name, f"it is named for another row than the one it says it starts at ({num_of(c, 'first')})"))
        elif "k" in c and text_of(c, "k") != "c." + doc_id:
            faults.append((name, f"the document under this name says it is {show(text_of(c, 'k') or '')}"))
        if not isinstance(c.get("rows"), list):
            raise Refused(f"vault chunk starting at row {num_of(c, 'first')} holds no rows")
        got = [r for r in c["rows"] if isinstance(r, dict)]
        as_labelled = got and num_of(got[0], "id") == num_of(c, "first") and num_of(got[-1], "id") == num_of(c, "last")
        if len(got) != len(c["rows"]) or len(got) != num_of(c, "n") or not as_labelled:
            raise Refused(f"vault chunk starting at row {num_of(c, 'first')} does not hold the rows it says it does")
        rows.extend(got)
    return rows, faults


def chunk_end(rows, at, most_rows, most_bytes):
    """Where the chunk that starts at rows[at] ends: it takes rows while it holds fewer than
    `most_rows` and the next one keeps it within `most_bytes`, and always at least one."""
    end, size = at, 0
    while end < len(rows) and end - at < most_rows:
        more = utf8_len(canonical(rows[end]))
        if end > at and size + more > most_bytes:
            break
        size += more
        end += 1
    return end


def head_of(flags, name):
    path = flag(flags, name)
    if path is None:
        return None
    if not os.path.isfile(path):
        # A path that was given and is not there is a mistake, not an empty vault.
        raise Refused(f"--{name}: no such file as {show_path(path)}. Leave the flag off only when the vault holds nothing")
    # Where the tip sits in a folder `ingest` wrote, it must still be the file of that read.
    read_manifest(os.path.dirname(path) or os.curdir)
    return as_obj(unwrap_data(parse(read_text(path))))


def tip_problem(head):
    """Why a document is not a vault's tip, or None. Whatever the command, and whether or
    not the document holds a checksum (which anyone can recompute), a tip names itself
    `head`, names a row from 1 on by a whole fingerprint, and counts exactly as many rows
    as that row's id: a vault holds every row from 1."""
    tip = head.get("id")
    whole = is_number(tip) and is_whole(tip) and 1 <= tip <= MAX_INT and head.get("n") == tip
    if whole and text_of(head, "k") == "head" and FINGERPRINT.fullmatch(text_of(head, "fingerprint") or ""):
        return None
    return "the document given as the vault's tip (vault/head) is not one: a tip names itself head (k), names a row from 1 on (id) by its 64-character fingerprint, and counts as many rows as that id (n)"


def head_faults(head, rows):
    """What is wrong with the vault's tip, given every row the vault holds: its checksum,
    what a tip must be, and that it names the last row, with that row's fingerprint, and
    counts exactly the rows there are."""
    faults = []
    last = rows[-1] if rows else None
    last_id = num_of(last, "id") if last is not None else 0
    if checksum_problem(head) is not None:
        faults.append(f"the vault's tip (vault/head): {CHECKSUM_WRONG}")
    if tip_problem(head) is not None:
        faults.append(tip_problem(head))
    same_tip = last is not None and num_of(head, "id") == last_id and text_of(head, "fingerprint") == text_of(last, "fingerprint")
    if not same_tip or num_of(head, "n") != len(rows):
        faults.append(f"the vault's tip says row {num_of(head, 'id')} of {num_of(head, 'n')} but its last row is {last_id}")
    return faults


def cli_vault_plan(flags):
    """Plan a copy of the log's new rows into the vault. Refuses if the log no longer agrees
    with what the vault already holds: that is a changed row, not something to copy over."""
    out = need(flags, "out", "vault-plan")
    source = need(flags, "source", "vault-plan")
    # --inline: the session cannot pass files, so the vault's tip and every row here were
    # carried by hand, and what is planned will be carried out by hand as well.
    inline = "inline" in flags
    stream_dir = folder(flags, "stream", "vault-plan")
    docs, bad = read_stream(stream_dir)
    rows = [doc for _, doc in docs]
    head = head_of(flags, "vault-head")
    tip, tip_fp, held, version = 0, "", 0, None
    if head is not None:
        why = f"vault/head: {CHECKSUM_WRONG}" if checksum_problem(head) is not None else tip_problem(head)
        if why is not None:
            raise Refused(f"{why}. Nothing was planned. {REREAD}")
        # A vault copies one log. A title or a mistaken link must not send rows to another's.
        was = text_of(head, "source")
        if was and was.rstrip("/") != source.rstrip("/") and "moved" not in flags:
            raise Refused(f"this vault's tip says it copies the log {show(was)}, and --source names another. Nothing was planned. Use this log's own vault; only if this log was restored to a new link, add --moved")
        tip, tip_fp, held = num_of(head, "id"), text_of(head, "fingerprint"), num_of(head, "n")
        version = head_version(flags, "vault-plan")
        if version is None:
            raise Refused("the vault has a tip already: pass its version as --head-version N, or as --versions head=N")
    # With --inline a refusal may be a slip in copying and not a change to the log.
    carried = (
        " Either the log has been changed, or a document was miscopied on its way here, or the read did not return the rows"
        " in order from the vault's tip: read vault/head and the log's rows again, into a new folder, and plan again."
        " If the same ALARM comes back, the log has been changed."
    )
    if tip > 0:
        at_tip = next((m for m in rows if num_of(m, "id") == tip), None)
        if at_tip is None:
            raise Refused(f"the rows given do not include row {tip}, the vault's tip: read the log's rows with id >= {tip}")
        if (text_of(at_tip, "fingerprint") or "") != tip_fp or row_fp(at_tip) != tip_fp:
            raise Refused(f"ALARM: the log's row {tip} is not the row the vault holds." + (" Nothing was planned." + carried if inline else " The log has been changed. Nothing was planned."))
    waiting = [core_row(m) for m in rows if num_of(m, "id") > tip]
    cap = cap_of(flags, VAULT_INLINE_ROWS if inline else 9000)
    fresh = waiting[:cap]
    problems = chain_problems(fresh, tip, tip_fp)
    if problems:
        raise Refused(f"ALARM: the log's new rows do not form a chain from the vault's tip ({'; '.join(problems)}). Nothing was planned." + (carried if inline else ""))
    # Every row that was given has now been read the strict way, the ones before the tip and
    # the ones this round does not take included. A row among them that does not hold
    # together is the same alarm.
    if bad:
        raise Refused(f"ALARM: the log's rows, as given, do not hold together ({some_of(bad)}). Nothing was planned." + (carried if inline else " The log has been changed."))
    if inline:
        # One round is one chunk.
        fresh = fresh[: chunk_end(fresh, 0, min(cap, VAULT_CHUNK), VAULT_INLINE_BYTES)]
    remaining = len(waiting) - len(fresh)
    # What was established about the rows, which matters most where they came by hand: the
    # anchor (the log's row at the vault's tip) and the new rows. Nothing else was compared.
    checked = f"row {tip} of the log is the row the vault holds" if tip > 0 else ""
    if fresh:
        first_id, last_id = num_of(fresh[0], "id"), num_of(fresh[-1], "id")
        span = f"row {first_id} carries" if len(fresh) == 1 else f"rows {first_id} to {last_id} each carry"
        checked += (", and " if checked else "") + f"{span} the fingerprint of {'its' if len(fresh) == 1 else 'their'} own contents and"
        checked += " chain on from it" if tip > 0 else " chain from the start of the stream"
    if checked:
        earlier = f". Rows before row {tip} were not compared with the vault: vault-check does that" if tip > 1 else ""
        say(f"checked by recomputing fingerprints: {checked}{earlier}")
    if head is not None and inline and CHECKSUM not in head:
        say(f"vault/head holds no checksum (it was written before version 0.5.0): its id and fingerprint were checked against the log's row {tip}, and its count against its id")
    plan = Plan(out)
    if not fresh:
        plan.write('"copied":0,"last_id":%d,"remaining":0' % tip, "null" if inline else None)
        # Said as what it is: the anchor row agrees and nothing follows it. That is not a
        # statement about any earlier row.
        say(f"nothing to copy: no row after row {tip} was given, and row {tip} of the log is the row at the vault's tip ({held} rows)" if tip > 0 else "nothing to copy: no rows were given and the vault holds none")
        return True

    def put(collection, doc_id, doc, pinned):
        doc = with_checksum(doc)
        # The plan is never one the database must refuse.
        size = utf8_len(canonical(doc))
        if size > MAX_DOC_BYTES:
            raise Refused(f"{collection}/{doc_id} would be {size} bytes, more than the {MAX_DOC_BYTES} this script will plan for one document. Nothing was planned: pass a smaller --max")
        plan.set(collection, doc_id, doc, pinned)
        return doc

    at = 0
    while at < len(fresh):
        end = chunk_end(fresh, at, VAULT_CHUNK, VAULT_INLINE_BYTES if inline else VAULT_CHUNK_BYTES)
        first, last = num_of(fresh[at], "id"), num_of(fresh[end - 1], "id")
        doc_id = stream_doc(first)
        put("rows", doc_id, {"k": "c." + doc_id, "first": num(first), "last": num(last), "n": num(end - at), "rows": fresh[at:end]}, None)
        at = end
    if len(plan.batch) > MAX_WRITES - 1:
        raise Refused("too many rows for one copy: pass a smaller --max and run it again afterwards")
    last = fresh[-1]
    last_id, last_fp, count = num_of(last, "id"), text_of(last, "fingerprint") or "", held + len(fresh)
    new_head = put(
        "vault", "head",
        {"k": "head", "id": num(last_id), "fingerprint": last_fp, "n": num(count), "ts": text_of(last, "ts") or "", "source": source, "copied_at": iso_utc(now_secs())},
        version,
    )
    summary = '"copied":%d,"first_id":%d,"last_id":%d,"remaining":%d' % (len(fresh), tip + 1, last_id, remaining)
    # `after` is what the vault's tip must hold once the ops are applied. Its checksum
    # covers every field, so the one value says whether the whole document landed.
    after = '{"path":"vault/head","id":%d,"fingerprint":%s,"n":%d,"ck":%s}' % (last_id, json_text(last_fp), count, json_text(new_head[CHECKSUM]))
    plan.say_how(*plan.write(summary, after if inline else None), to=", TO THE VAULT, not to the log")
    if inline:
        say(f"then read vault/head back from the vault: it must hold id {last_id}, fingerprint {last_fp}, n {count} and ck {new_head[CHECKSUM]}")
        say("and read back the rows document it wrote: confirm compares both with this plan")
    more = f"; {remaining} more rows were given and NOT planned: copy them in another round" if remaining else ""
    say(f"copy rows {tip + 1} to {last_id} into the vault ({len(fresh)} rows, {len(plan.batch) - 1} documents); planned, not stored{more}")
    return True


def head_version(flags, cmd):
    """The version of vault/head: as --head-version N, as --versions head=N, or, where the
    tip was read as text and passed through `ingest`, as the manifest beside it recorded
    it, which must be of a read of collection vault. Where the caller typed one and the
    manifest holds one, they must agree: the version pins the write, and a version typed
    against the wrong read would pin it to nothing."""
    typed = count_flag(flags, "head-version", None, 1) if "head-version" in flags else pairs(flags, "versions").get("head")
    manifest = read_manifest(os.path.dirname(flags["vault-head"]) or os.curdir) if "inline" in flags else None
    if manifest is not None and manifest["collection"] != "vault":
        raise Refused(f"the vault's tip was given from a folder that holds a read of collection {show(manifest['collection'])}, not of vault. Nothing was planned. {REREAD}")
    name = stem_and_extension(os.path.basename(flags["vault-head"]))[0]
    read_at = next((e["version"] for e in manifest["documents"] if e["id"] == name == "head"), None) if manifest is not None else None
    if typed is not None and read_at is not None and typed != read_at:
        raise Refused(f"the version given for vault/head is {typed}, and the read that was ingested says {read_at}. Nothing was planned. Pass the version as ingest printed it, or leave the option off")
    return typed if typed is not None else read_at


def cli_vault_check(flags):
    """Check everything the vault holds, and optionally compare it with the log's rows."""
    rows, faults = vault_rows(folder(flags, "rows", "vault-check"))
    # --partial is about the log's rows: without --stream there is nothing it changes.
    partial = "partial" in flags and "stream" in flags
    # Without the tip there is no telling all of the vault's rows from the first part of
    # them, and "vault intact" would be said of whatever was given.
    head = head_of(flags, "vault-head")
    if head is None:
        raise Refused("vault-check needs --vault-head FILE: the vault's tip (vault/head), which says how many rows the vault holds. Without it, nothing shows whether the rows given are all of them")
    problems = [f"vault document {name}: {why}" for name, why in faults]
    problems += chain_problems(rows, 0, "")
    last_id = num_of(rows[-1], "id") if rows else 0
    problems += head_faults(head, rows)
    compared = ""
    if "stream" in flags:
        # The log's rows are read the strict way, every one of them: a row the vault does
        # not hold yet is checked like any other, and is not passed over as "not yet copied".
        docs, bad = read_stream(folder(flags, "stream", "vault-check"))
        problems += [f"the log: {p}" for p in bad]
        live = [doc for _, doc in docs]
        held = {num_of(m, "id"): text_of(m, "fingerprint") or "" for m in rows}
        same = ahead = 0
        for m in live:
            row_id = num_of(m, "id")
            if row_id not in held:
                ahead += 1
            elif text_of(m, "fingerprint") == held[row_id] and row_fp(m) == held[row_id]:
                same += 1
            else:
                problems.append(f"row {row_id} in the log differs from the vault's copy")
        # Every row the vault holds must still be in the log. Only when the caller says the
        # folder is part of the log is the comparison kept to the ids that folder spans.
        seen = {num_of(m, "id") for m in live}
        lo, hi = (min(seen), max(seen)) if seen else (1, 0)
        if partial:
            gone = sorted(row_id for row_id in held if lo <= row_id <= hi and row_id not in seen)
            if not seen:
                problems.append("no rows of the log were given, so the log was not compared with the vault")
        else:
            gone = sorted(row_id for row_id in held if row_id not in seen)
        # The first row given has no row before it among those given. If the vault holds
        # the row before it, that is the row it must point at; if the vault holds neither,
        # nothing ties the rows given to the vault, and nothing is said of them.
        if lo > 1 and lo not in held:
            if lo - 1 not in held:
                problems.append(f"the log's rows were given from row {lo}, and the vault holds neither that row nor the one before it: they could not be anchored to the vault, so the log was not compared with it")
            elif (text_of(live[0], "previous") or "") != held[lo - 1]:
                problems.append(f"row {lo} in the log does not point at row {lo - 1} as the vault holds it: its 'previous' is not that row's fingerprint")
        for row_id in gone[:20]:
            problems.append(f"row {row_id} is in the vault but missing from the log")
        if len(gone) > 20:
            problems.append(f"and {len(gone) - 20} more rows are in the vault but missing from the log")
        compared = f"; {same} log rows match the vault, {ahead} not yet copied"
        if ahead:
            compared += " (each of those was checked too: its id, its 'previous' and its fingerprint)"
    for p in problems:
        say(f"PROBLEM {p}")
    if problems:
        verdict = f"{len(problems)} problems"
    elif partial:
        # Its own last words, never those of a full check.
        verdict = f"partial check: the vault's rows and tip hold together, and rows {lo} to {hi} of the log agree with them; no other row of the log was compared"
    else:
        verdict = "vault intact"
    say(f"{len(rows)} rows in the vault, ids {num_of(rows[0], 'id') if rows else 0} to {last_id}{compared}; {verdict}")
    return not problems


def cli_vault_restore(flags):
    """Turn the vault's rows back into the documents of collection `stream`, in batches.
    The vault's tip says how many rows there are, so it is asked for: one chunk of a
    two-chunk vault is a chain from row 1 with nothing wrong in it, and only the tip shows
    that it is not the whole vault."""
    reg = load_registry(flags, "vault-restore")
    out = need(flags, "out", "vault-restore")
    rows, faults = vault_rows(folder(flags, "rows", "vault-restore"))
    partial = "partial" in flags
    head = head_of(flags, "vault-head")
    if head is None and not partial:
        raise Refused(
            "vault-restore needs --vault-head FILE: the vault's tip (vault/head), which says how many rows the vault holds."
            " Without it, the first part of a vault cannot be told from all of it. Nothing was planned."
            " (--partial restores the rows that were given and says that it did)"
        )
    damaged = [f"{name} does not match its checksum (ck)" if why == CHECKSUM_WRONG else f"{name}: {why}" for name, why in faults]
    problems = chain_problems(rows, 0, "")
    if problems:
        raise Refused(f"the vault's own chain is broken, so nothing was planned: {'; '.join(damaged + problems)}")
    if damaged:
        raise Refused(f"a document of the vault was changed after it was written, so nothing was planned: {'; '.join(damaged)}")
    last_id = len(rows)
    held = None
    if head is not None:
        if checksum_problem(head) is not None:
            raise Refused(f"a document of the vault was changed after it was written, so nothing was planned: vault/head does not match its checksum (ck)")
        if tip_problem(head):
            raise Refused(tip_problem(head) + ". Nothing was planned")
        held = num_of(head, "id")
        if held < last_id:
            raise Refused(f"the vault's tip says it holds {held} rows, and {last_id} were given: the tip is older than the rows, or belongs to another vault. Nothing was planned. Read vault/head again")
        whole = held == last_id and text_of(head, "fingerprint") == text_of(rows[-1], "fingerprint")
        if held == last_id and not whole:
            raise Refused(f"the vault's tip names row {held} with another fingerprint than the row {held} given. Nothing was planned")
        if not whole and not partial:
            raise Refused(
                f"the vault's tip says it holds {held} rows, and the rows given end at row {last_id}: rows {last_id + 1} to {held} are missing."
                " Nothing was planned. Read every document of the vault's collection rows and run this again."
                f" (--partial restores rows 1 to {last_id} alone and says that it did)"
            )
    if not rows:
        raise Refused("no rows were given, so there is nothing to restore. Nothing was planned")
    plan = Plan(out)
    for m in rows:
        row = dict(m)
        spec = reg.get(text_of(m, "verb"))
        if spec is not None:
            promote(row, spec)
        plan.set("stream", stream_doc(num_of(m, "id")), row)
    short = held is None or held > last_id
    plan.write('"rows":%d,"override":%s' % (len(rows), '"partial"' if short else "null"), split=True)
    stream_dir = show_path(absolute(os.path.join(out, "stream")))
    say(f"{len(rows)} rows written under {stream_dir}")
    say("send batch-01.json to batch-%02d.json in %s, each as one batch, in order, to the artifact being restored" % (plan.batches, show_path(absolute(out))))
    say(f"then: rebuild --stream {stream_dir} --ix <an empty folder> --out <another folder>, and send its batch files the same way")
    if held is None:
        say(f"PARTIAL RESTORE: rows 1 to {last_id}, as --partial asked. The vault's tip was not given, so how many rows the vault holds is not known")
    elif short:
        say(f"PARTIAL RESTORE: rows 1 to {last_id}, as --partial asked. The vault's tip says it holds {held} rows: rows {last_id + 1} to {held} are NOT in this restore")
    else:
        say(f"whole restore planned, not stored: the vault's tip agrees: {held} rows, ending at the row the tip names")
    return True


# ---------- confirm ----------
#
# A session that cannot pass files puts every planned document into its write by hand, and
# a slip there is stored as it was typed. So after such a write the session reads back what
# it wrote, through `ingest`, and this compares that with the plan, document by document.


def cli_confirm(flags):
    plan_path = need(flags, "plan", "confirm")
    read = folder(flags, "read", "confirm")
    try:
        plan = parse(read_text(plan_path))
    except Refused as why:
        raise Refused(f"--plan {show_path(plan_path)}: {why}")
    ops = plan.get("ops") if isinstance(plan, dict) else None
    # A plan with no writes in it was not sent, and there is nothing to call "as planned".
    if not isinstance(ops, list) or not ops:
        raise Refused(f'--plan {show_path(plan_path)}: it holds no "ops", so there is nothing to confirm. Give the plan-inline.json that was sent')
    problems = []
    same = gone = 0
    unconfirmed = []
    for op in ops:
        kind = text_of(op, "op") if isinstance(op, dict) else None
        parts = (text_of(op, "path") or "").split("/") if isinstance(op, dict) else []
        # The plan is read like any other file: a path in it becomes a file name only if
        # each part of it could be one.
        if kind not in ("set", "delete") or len(parts) < 2 or len(parts) % 2 != 0 or not all(is_file_name(part) for part in parts):
            raise Refused(f"--plan {show_path(plan_path)}: an entry of \"ops\" is not a set or a delete of a document's path")
        path, collection = "/".join(parts), "/".join(parts[:-1])
        # The way down from --read is taken from each folder's own listing, one exact name
        # at a time, and nothing is read through a link on the way.
        at = [read]
        for name in parts[:-1] + [parts[-1] + ".json"]:
            at.append(child(at[-1], name))
            if at[-1] is not None and is_link(at[-1]):
                raise Refused(f"{show_path(at[-1])}: is a link, and this script does not read through links")
        collection_dir, file_path = at[-2], at[-1]
        # Where `ingest` filled the folder, its manifest says which read the files are of,
        # and is checked against them; a read of another collection shows nothing of this one.
        manifest = read_manifest(collection_dir) if collection_dir is not None else None
        other = f"the folder holds a read of collection {show(manifest['collection'])}, not of {show(collection)}" if manifest is not None and manifest["collection"] != collection else None
        if kind == "delete":
            # That a file is not in a folder shows nothing unless the folder is known to
            # hold all of the collection, or a read of that one document that found none.
            # `ingest` leaves a manifest that says so; without one, the removal is not
            # confirmed, which is not the same as confirmed.
            if collection_dir is None:
                why_not = "its collection was not read back"
            elif manifest is None:
                why_not = "the folder its collection was read into holds no read manifest, so nothing shows the collection was read whole. Read the collection back and pass the result through ingest"
            elif other is None and manifest["absent"] != parts[-1] and not manifest["complete"]:
                why_not = "the read of its collection was not complete: " + manifest["why"]
            else:
                why_not = other
            if file_path is not None and other is None:
                problems.append(f"{path} was to be removed and is still there")
            elif why_not is not None:
                unconfirmed.append(f"{path}: {why_not}")
            else:
                gone += 1
            continue
        planned = op.get("data")
        if not isinstance(planned, dict):
            raise Refused(f"--plan {show_path(plan_path)}: the entry that sets {path} holds no document")
        # Two copies that agree prove little if neither is what the script wrote: a planned
        # document that holds a checksum must match it, and so must the one read back.
        if checksum_problem(planned) is not None:
            problems.append(f"{path}: the plan's own copy of it does not match its checksum (ck): this plan was changed after the script wrote it, or the script did not write it")
            continue
        if other is not None or file_path is None or not os.path.isfile(file_path):
            where = show_path(absolute(os.path.join(read, *parts[:-1])))
            problems.append(f"{path} was not read back{'' if other is None else ' (' + other + ')'}: read it from the database, ingest it into {where}, and run this again")
            continue
        try:
            stored = unwrap_data(parse(read_text(file_path, inside=True)))
        except Refused as why:
            problems.append(f"{path}: {why}")
            continue
        if checksum_problem(stored) is not None:
            problems.append(
                f"{path}: what was read back does not match its own checksum (ck), so it was miscopied, in the write or in reading"
                " it back. Read it back once more, into a new folder: if it is still so, the database holds a damaged document"
            )
        elif canonical_or_none(planned) is not None and canonical_or_none(stored) == canonical_or_none(planned):
            same += 1
        else:
            problems.append(f"{path}: what was read back is not what was planned: the write did not land as it was planned, another write has landed since, or this is not the plan that was sent")
    for p in problems:
        say(f"PROBLEM {p}")
    for p in unconfirmed:
        say(f"NOT CONFIRMED {p}")
    counted = f"{same} documents read back and identical to the plan"
    if gone:
        counted += f", {gone} removed"
    if problems:
        verdict = f"{len(problems)} problems"
    elif unconfirmed:
        verdict = f"not confirmed: {len(unconfirmed)} removals could not be checked"
    else:
        verdict = "as planned"
    say(counted + "; " + verdict)
    return not problems and not unconfirmed


# ---------- the first documents ----------
#
# A new log holds two documents before its first row: a copy of the verb registry
# (`state/verbs`), which holds a checksum, and the user's own entry in the member list
# (`members/<user id>`). This writes both and the one batch that stores them, so that a
# session which cannot pass files copies them from a plan and types neither.
#
# Whether rows are logged without asking, and whether each one is announced, is each
# person's own choice, kept in their own member document: the one document of a shared
# log that only they can write. (Before version 0.7.0 it was kept once for the whole log,
# in `state/settings`.)


def yes_or_no(flags, name, otherwise):
    given = flag(flags, name)
    if given is None:
        return otherwise
    word = trim(given).translate(TO_ASCII_LOWER)
    if word in ("yes", "true"):
        return True
    if word in ("no", "false"):
        return False
    raise Refused(f"--{name} must be yes or no")


def cli_first_docs(flags):
    out = need(flags, "out", "first-docs")
    account = trim(need(flags, "account", "first-docs"))
    if not is_actor(account):
        raise Refused("--account must be the user's id")
    text = registry_text(flags, "first-docs")
    # The registry is stored as the file holds it, once it is known to be one.
    verbs = registry_from_json(text)
    auto_log = yes_or_no(flags, "auto-log", False)
    announce = yes_or_no(flags, "announce", True)
    registry = with_checksum(as_obj(unwrap_data(parse(text))))
    plan = Plan(out)
    plan.set("state", "verbs", registry)
    plan.set("members", account, {"member": True, "auto_log": auto_log, "announce": announce})
    summary = '"documents":2,"auto_log":%s,"announce":%s' % (canonical(auto_log), canonical(announce))
    inline = "inline" in flags
    # `after` lists the checksum of each document that holds one.
    after = '[{"path":"state/verbs","ck":%s}]' % json_text(registry[CHECKSUM])
    plan.say_how(*plan.write(summary, after if inline else None))
    if inline:
        say("then read collections state and members back: confirm compares them with this plan")
    say(f"first documents: state/verbs ({len(verbs)} verbs), members/{account} (auto_log {canonical(auto_log)}, announce {canonical(announce)}); planned, not stored")
    return True


# ---------- recounting the index ----------
#
# What the index must hold for a run of rows starting at 1: each verb's tally, each entity's
# state, each idempotency marker and the tip. Sealing builds these a few rows at a time;
# `check` and `rebuild` build them again from every row, and must arrive at the same
# documents.


def new_recount():
    return {"tallies": {}, "docs": {}, "tip": (0, "", ""), "rows": 0}


def recount_add(count, row_id, d, fp, reg):
    spec = reg[d.verb]
    tally_add(count["tallies"].setdefault(d.verb, new_tally()), row_id, d, spec)
    ek = entity_doc(d.entity)
    count["docs"][ek] = entity_json(count["docs"].get(ek), row_id, d, spec)
    if d.key is not None:
        kd = key_doc(d.verb, d.entity, d.key)
        count["docs"].setdefault(kd, {"k": kd, "id": num(row_id)})
    count["tip"] = (row_id, fp, d.ts)
    count["rows"] += 1


def recount_finish(count):
    """Every index document the rows call for, as {document id: document}."""
    docs = count["docs"]
    if count["rows"] > 0:
        row_id, fp, ts = count["tip"]
        docs["head"] = {"k": "head", "id": num(row_id), "fingerprint": fp, "ts": ts, "n": num(count["rows"])}
    for verb, tally in count["tallies"].items():
        docs["v." + verb] = tally_json(tally, verb)
    return docs


def index_doc_name(key):
    """What a message calls a document of collection ix. The id may be a file's name, which
    nobody checked, so one that is not a plain document id is quoted."""
    letter, dot, rest = key.partition(".")
    if key == "head":
        return "the tip"
    if not is_file_name(key) or not dot or letter not in ("v", "e", "i"):
        return f"document {show(key)} in the index folder"
    if letter == "v":
        return f"tally for '{rest}'"
    if letter == "e":
        return f"entity state {key}"
    return f"idempotency marker {key}"


def by_row_id(pair):
    doc = pair[1]
    return num_of(doc, "id") if isinstance(doc, dict) else 0


def same_but_for_checksum(stored, recounted):
    """Whether a stored index document holds what a recount gives, leaving its checksum
    aside. The recount carries none."""
    if not isinstance(stored, dict):
        return False
    return canonical_or_none(without_checksum(stored)) == canonical_or_none(recounted)


# ---------- reading an exported stream ----------
#
# `check`, `rebuild` and the three vault commands are each given a folder of sealed rows,
# one file per row, and each builds on what those rows say. So all of them read the folder
# the same strict way, and none takes a row's word for what it is.

FINGERPRINT = re.compile("[0-9a-f]{64}")


def read_stream(folder_path):
    """The rows in a folder exported from collection `stream`, in id order, and everything
    that is wrong with them as a run of rows: (rows, problems), rows as (the file's document
    id, the row). Each must be an object, sit in the file its id names, follow the row
    before it by one, point at that row's fingerprint in `previous`, and carry the
    fingerprint its own contents give. Whether the run starts at row 1 is for the caller,
    who knows whether it was given the whole stream."""
    rows, problems = [], []
    for doc_id, doc in docs_in(folder_path):
        if not isinstance(doc, dict):
            problems.append(f"{show(doc_id)} is not an object, so it is not a row")
        elif num_of(doc, "id") < 1:
            problems.append(f"{show(doc_id)} holds no row id")
        else:
            rows.append((doc_id, doc))
    rows.sort(key=by_row_id)
    expect = previous = None
    for doc_id, doc in rows:
        row_id = num_of(doc, "id")
        if doc_id != stream_doc(row_id):
            problems.append(f"row {row_id} is in a file named {show(doc_id)}, where a row's file is named for its id ({stream_doc(row_id)})")
        if expect is not None and row_id != expect:
            problems.append(f"row {show(doc_id)}: expected id {expect}, found {row_id}")
        stored = text_of(doc, "fingerprint") or ""
        before = text_of(doc, "previous") or ""
        if previous is not None:
            if before != previous:
                problems.append(f"row {row_id}: 'previous' does not match the row before it")
        elif row_id == 1 and before != "":
            problems.append("row 1: 'previous' should be empty")
        # The fingerprint covers the row's own fields, not the copied-up p_ fields.
        if not FINGERPRINT.fullmatch(stored) or row_fp(doc) != stored:
            problems.append(f"row {row_id}: fingerprint does not match its contents")
        expect, previous = row_id + 1, stored
    return rows, problems


def some_of(problems, most=5):
    """The first few of a list of problems, as one text, saying how many were left out."""
    more = f"; and {len(problems) - most} more" if len(problems) > most else ""
    return "; ".join(problems[:most]) + more


# ---------- check ----------


def replay(rows, reg):
    """Replay sealed rows, for `check` and for `rebuild`, which must not disagree about a
    row: (the recount, what is wrong with the rows, the verbs seen, the entities seen).
    Rows are replayed as they were sealed: the rules a new row must pass at the door are
    not applied to history."""
    count, problems, verbs, entities = new_recount(), [], set(), set()
    for _, doc in rows:
        row_id = num_of(doc, "id")
        try:
            d = check_draft(doc, reg, door=False)
            # The p_ fields are outside the fingerprint, so they are checked on their own:
            # the stored document must be the row plus exactly the copies its verb calls for.
            expected = core_row(doc)
            promote(expected, reg[d.verb])
            if canonical_or_none(expected) != canonical_or_none(doc):
                problems.append(f"row {row_id}: its filterable copies (p_ fields) do not match its payload")
            verbs.add(d.verb)
            entities.add(d.entity)
            recount_add(count, row_id, d, text_of(doc, "fingerprint") or "", reg)
        except Refused as why:
            problems.append(f"row {row_id}: {why}")
    return count, problems, verbs, entities


def cli_check(flags):
    reg = load_registry(flags, "check")
    rows, problems = read_stream(folder(flags, "stream", "check"))
    ix = dict(docs_in(folder(flags, "ix", "check"))) if "ix" in flags else None
    partial = "partial" in flags
    first = by_row_id(rows[0]) if rows else 0
    # Rows can be deleted from the front as easily as changed, so a stream that does not
    # start at 1 is a problem unless the caller says it gave only part.
    if not partial and rows and first != 1:
        problems.append(f"the rows given start at {first}, not 1: earlier rows are missing (pass --partial if you gave only the newest part on purpose)")
    # A check that was given nothing has verified nothing, and must not say otherwise.
    if not rows:
        problems.append("no rows were given, so nothing was verified")
    count, wrong, verbs, entities = replay(rows, reg)
    problems += wrong
    last_id = by_row_id(rows[-1]) if rows else 0
    last_fp = (text_of(rows[-1][1], "fingerprint") or "") if rows else ""

    whole = not partial and (not rows or first == 1)
    want = recount_finish(count)
    compared = whole_checksums = 0
    if ix is not None:
        head = ix.get("head")
        if isinstance(head, dict):
            if num_of(head, "id") != last_id or (text_of(head, "fingerprint") or "") != last_fp:
                problems.append(f"the tip says row {num_of(head, 'id')} but the last row given is {last_id}")
            if whole and num_of(head, "n") != len(rows):
                problems.append(f"the tip counts {num_of(head, 'n')} rows but {len(rows)} were given")
        if whole:
            # Every index document must be exactly what the rows give, and there must be
            # none the rows do not account for: a planted record or tally is a change too.
            # A document may hold no checksum (it was written before they existed) and still
            # be right; one that holds a wrong checksum is not.
            for key in sorted(want):
                if key not in ix:
                    problems.append(f"{index_doc_name(key)} is missing")
                elif not same_but_for_checksum(ix[key], want[key]):
                    problems.append(f"{index_doc_name(key)} differs from a recount")
                elif checksum_problem(ix[key]) is not None:
                    problems.append(f"{index_doc_name(key)} matches a recount, but its checksum (ck) does not match its contents")
                else:
                    compared += 1
            # Whatever else is in the folder, under any name: collection ix holds the index
            # and nothing else.
            extra = sorted(k for k in ix if k not in want)
            for key in extra[:20]:
                problems.append(f"{index_doc_name(key)} is not accounted for by the rows")
            if len(extra) > 20:
                problems.append(f"and {len(extra) - 20} more index documents are not accounted for by the rows")
        else:
            # Part of the stream cannot be recounted against the index. What can still be
            # verified of each index document that was given is that it arrived whole, that
            # it sits under its own name, and, of the tip, that it counts as many rows as
            # its id, which every log's tip does.
            if not isinstance(head, dict):
                problems.append("the index given holds no tip (head), so the last row could not be compared with it")
            elif num_of(head, "n") != num_of(head, "id"):
                problems.append(f"the tip counts {num_of(head, 'n')} rows at row {num_of(head, 'id')}, where a log counts as many rows as its tip's id")
            for key in sorted(ix):
                doc = ix[key]
                if not isinstance(doc, dict):
                    problems.append(f"{index_doc_name(key)} is not an object")
                elif checksum_problem(doc) is not None:
                    problems.append(f"{index_doc_name(key)}: {CHECKSUM_WRONG}")
                elif (text_of(doc, "k") or key) != key:
                    problems.append(f"{index_doc_name(key)}: the document under this name says it is {show(text_of(doc, 'k') or '')}")
                elif CHECKSUM in doc:
                    whole_checksums += 1

    for p in problems:
        say(f"PROBLEM {p}")
    counted = f"{len(rows)} rows, ids {first} to {last_id}, {len(verbs)} verbs, {len(entities)} entities"
    found = f"{len(problems)} problems"
    if not whole:
        # A partial check has its own verdict. It never says "chain intact": that is what a
        # full check says, and this was not one.
        about_ix = ""
        if ix is not None:
            about_ix = f" Of the {len(ix)} index documents given, {whole_checksums} hold a checksum that matches; none was recounted."
        say(
            f"partial check: {counted}. Verified among the rows given: ids in step, each 'previous', each fingerprint, each row's filterable copies."
            f" Not verified: any row before {first}, and the index against a recount.{about_ix}"
            f" This was not a full check; {'no problems in what was given' if not problems else found}"
        )
    elif ix is None:
        say(f"{counted}; no index was given (--ix), so no index document was compared; {'rows intact, index not checked' if not problems else found}")
    else:
        say(
            f"{counted}; {compared} index documents recounted and compared"
            " (the tip, each verb tally, each entity state, each idempotency marker); "
            + ("chain intact" if not problems else found)
        )
    return not problems


# ---------- rebuild ----------


def cli_rebuild(flags):
    """Recompute every index document from the rows themselves. Plans a write of the ones
    that differ from what the store holds and the removal of any the rows do not account
    for: the repair for an index that has gone wrong, and the second half of putting a log
    back from the vault. The rows are verified first, exactly as `check` verifies them: a
    rebuild from rows that were changed, or from only some of them, would write the damage
    into every count. It writes only the index, so there is nothing wrong with a row that
    it could put right, and it refuses them all."""
    reg = load_registry(flags, "rebuild")
    rows, bad = read_stream(folder(flags, "stream", "rebuild"))
    had = dict(docs_in(folder(flags, "ix", "rebuild")))
    out = need(flags, "out", "rebuild")
    versions = pairs(flags, "versions")
    # --checksums: a right document that holds no checksum is rewritten too, to give it one.
    add_checksums = "checksums" in flags
    count, wrong, _, _ = replay(rows, reg)
    if not bad and rows and by_row_id(rows[0]) != 1:
        raise Refused("rebuild needs every row from 1 with no gaps; row 1 is missing")
    if bad or wrong:
        raise Refused(
            f"the rows given do not hold together, so nothing was planned: {some_of(bad + wrong)}."
            " rebuild will not derive an index from rows it cannot vouch for; it writes only the index, and cannot put a row right."
            " Run check on the same folders and tell the user what it says"
        )
    want = recount_finish(count)

    # The index that was given says where the log's tip is. Rows that stop short of it are a
    # read that lost its end, and a plan made from them would move the tip back and drop
    # those rows from every count.
    told = num_of(had["head"], "id") if isinstance(had.get("head"), dict) else 0
    rewound = told > len(rows)
    if rewound and "rewind" not in flags:
        raise Refused(
            f"the index given says the log's tip is row {told}, and the rows given end at row {len(rows)}:"
            f" rows {len(rows) + 1} to {told} are missing from --stream. A rebuild from these rows would move the tip back and leave"
            " them out of every count. Nothing was planned. Read all of collection stream and run this again."
            f" If the log really was cut back to row {len(rows)} on purpose, say so with --rewind"
        )

    plan = Plan(out)
    changed, removed, unpinned, notes = [], [], [], []
    tip = None
    for key in sorted(want):
        stored = had.get(key)
        # A stored document is left alone when its contents are right and its checksum, if
        # it holds one, is right too. Its size is then no business of this plan's.
        if same_but_for_checksum(stored, want[key]) and checksum_problem(stored) is None and (CHECKSUM in stored or not add_checksums):
            continue
        # A document that cannot be written is an error, never a silent omission.
        try:
            doc = with_checksum(want[key])
            size = utf8_len(canonical(doc))
        except Refused as why:
            raise Refused(f"{index_doc_name(key)}: {why}")
        # No plan makes a document larger than the database takes. One that is over
        # already, as an older log's may be, can be rewritten at its size or below it.
        if size > MAX_DOC_BYTES:
            was = utf8_len(canonical_or_none(stored) or "") if isinstance(stored, dict) else 0
            if size > was:
                raise Refused(f"{index_doc_name(key)} would be {size} bytes, more than the {MAX_DOC_BYTES} this script will plan for one document. Nothing was planned")
            notes.append(f"note: ix/{key} will be {size} bytes, over the {MAX_DOC_BYTES} this script lets a document grow to. It is {was} bytes already, and this plan does not make it larger")
        if key in had and key not in versions:
            unpinned.append(key)
            continue
        changed.append(key)
        if key == "head":
            tip = doc
        else:
            plan.set("ix", key, doc, versions.get(key) if key in had else None)
    # Whatever else the folder holds is removed, under any name: collection ix holds the
    # index and nothing else.
    for key in sorted(k for k in had if k not in want):
        if not is_file_name(key):
            raise Refused(f"{index_doc_name(key)} is not accounted for by the rows, and its name is not one a document of this log can have. Nothing was planned: tell the user")
        if key in versions:
            plan.delete("ix", key, versions[key])
            removed.append(key)
        else:
            unpinned.append(key)
    if unpinned:
        raise Refused(
            "these index documents differ from a recount and exist already; pass their versions: --versions "
            + ",".join(k + "=N" for k in unpinned)
        )
    # The tip goes last, so a log being put back shows its rows as counted only once the
    # rest of the index is there.
    if tip is not None:
        plan.set("ix", "head", tip, versions.get("head") if "head" in had else None)
    # `batch` is present only when everything fits one batch; otherwise use `batches`.
    summary = '"rows":%d,"changed":[%s],"removed":[%s],"override":%s' % (len(rows), quoted(changed), quoted(removed), '"rewind"' if rewound else "null")
    plan_path = plan.write(summary, split=True)[0]
    for note in notes:
        warn(note)
    if not plan.batch:
        say(f"{len(rows)} rows verified and replayed; every index document already matches")
        return True
    plan.say_how(plan_path)
    said = f"{len(changed)} index documents {'differ or hold no checksum' if add_checksums else 'differ'}: {', '.join(changed)}"
    if removed:
        said += f"; {len(removed)} index documents are not accounted for by the rows and will be removed: {', '.join(removed)}"
    if rewound:
        said += f"; REWIND: the index given put the tip at row {told}; this plan moves it back to row {len(rows)}, as --rewind asked"
    say(f"{len(rows)} rows verified and replayed; {said}; planned, not stored")
    return True


# ---------- match ----------

# Fields only a row has. A candidate that carries any of them is a row still waiting in an
# inbox, and is held to what a row is held to.
ROW_ONLY = ["verb", "payload", "ts", "source", "actor", "idempotency_key", "at"]


def cli_match(flags):
    """Find which existing entity a name refers to. With no candidates it only says what to
    look up; given the entity documents of that type, it scores them and gives a verdict:
    `use` (exactly one carries this name), `ask` (a near match, or several, or something
    about the candidates that a person has to settle), `new` (nothing)."""
    name = one_spaced(need(flags, "name", "match"))
    key = name_key(name)
    if key == "":
        raise Refused("--name has no letters or digits to match on")
    kind = flag(flags, "type")
    if kind is not None and (not is_key(kind) or len(kind) > 32):
        raise Refused(f"--type {show(kind)} must be the part of an entity before the colon, like client")
    least = count_flag(flags, "min", 85)

    docs = []
    given = False
    if flag(flags, "ix") is not None:
        given = True
        docs.extend(doc for _, doc in docs_in(folder(flags, "ix", "match")))
    pasted = flag(flags, "candidates")
    if pasted is not None:
        given = True
        listed = parse_flag(pasted, "--candidates")
        if not isinstance(listed, list):
            raise Refused("--candidates must be a JSON array of entity documents")
        docs.extend(unwrap_data(doc) for doc in listed)

    # Candidates are entity states, or rows still waiting in an inbox. Several documents may
    # describe one entity (its state and its waiting rows): their names are pooled.
    reg = None
    cands = []  # each: {"entity", "subject", "names": [(name, key)], "merged"}
    ignored = []  # (entity, why it was not taken as a candidate)
    for doc in docs:
        entity = text_of(doc, "entity") if isinstance(doc, dict) else None
        if entity is None:
            # A folder saved from collection ix also holds the tip, tallies and markers,
            # which name themselves in `k` and are about no entity. Anything else that
            # names none was meant as a candidate and cannot be one: "new" said over it
            # could start a second record for something that has one.
            if not (isinstance(doc, dict) and re.match("head$|[vi][.]", text_of(doc, "k") or "")):
                ignored.append(("", "it is not an entity's state or a waiting row: it names no entity"))
            continue
        # A miscopied name could send a row to the wrong record, or start a second one.
        if checksum_problem(doc) is not None:
            raise Refused(f"the document given for {show(entity)} cannot be used: {CHECKSUM_WRONG}. Read it again")
        if not is_entity(entity):
            ignored.append((entity, "its entity is not an entity id"))
            continue
        if kind is not None and kind != kind_of(entity):
            continue
        if any(field in doc for field in ROW_ONLY):
            # A waiting row counts for what sealing it would make of it, so it must be a row
            # `seal` would take: a registered verb, its required fields, everything. An
            # object that only looks like one says nothing about any record.
            if reg is None:
                reg = load_registry(flags, "match")
            try:
                if at_of(doc) is None:
                    raise Refused("its at (when it was written) is not a whole number of milliseconds")
                d = check_draft(doc, reg)
                if too_far_ahead(d.ts):
                    raise Refused("its ts is more than a day in the future")
            except Refused as why:
                ignored.append((entity, f"it is not a waiting row that seal would take: {why}"))
                continue
            spec = reg[d.verb]
            subject = d.subject
            names = [subject] if subject != "" else []
            names += [d.payload[f] for f in spec.names if isinstance(d.payload.get(f), str)]
            keyed = [(x, name_key(x)) for x in names]
            # Only a verb that keeps `merged_into` by name redirects a record, as in sealing.
            merged = d.payload.get("merged_into") if "merged_into" in spec.current and isinstance(d.payload.get("merged_into"), str) else None
        else:
            # An entity's state, as collection ix holds it, or the few fields of one.
            if "k" in doc and text_of(doc, "k") != entity_doc(entity):
                ignored.append((entity, "it is filed under another entity's name (its k is not this entity's)"))
                continue
            subject = text_of(doc, "subject") or ""
            names = list_of(doc, "names")
            if "nks" in doc:
                # The stored keys are what the documented query (`nks array-contains`) finds
                # a record by, so they are what a record is matched by here: the two must
                # give the same answer.
                stored = list_of(doc, "nks")
                keyed = list(zip(names, stored)) if len(stored) == len(names) else [(x, x) for x in stored]
            else:
                keyed = [(x, name_key(x)) for x in names]
                if subject != "" and not any(k == name_key(subject) for _, k in keyed):
                    keyed.append((subject, name_key(subject)))
            merged = text_of(doc, "f_merged_into")
        known = next((c for c in cands if c["entity"] == entity), None)
        if known is None:
            cands.append({"entity": entity, "subject": subject, "names": keyed, "merged": merged})
        else:
            if known["subject"] == "":
                known["subject"] = subject
            known["names"].extend(keyed)
            if merged is not None:
                known["merged"] = merged
    for c in cands:
        if c["subject"] == "" and c["names"]:
            c["subject"] = c["names"][0][0]
    by_entity = {c["entity"]: c for c in cands}

    def target(start):
        """A record marked as a duplicate stands for the record it was merged into: (that
        record, None). The record it leads to must be here, of the same type, and reached
        without coming round again; otherwise (the record itself, why not)."""
        at, seen = start, {start["entity"]}
        while at["merged"] is not None:
            onto = at["merged"]
            if not is_entity(onto) or kind_of(onto) != kind_of(start["entity"]):
                return start, f"it is marked as merged into {show(onto)}, which is not a record of its type"
            if onto in seen:
                return start, "it is marked as merged into a record that is merged back into it"
            if onto not in by_entity:
                return start, f"it is marked as merged into {show(onto)}, which is not among the records given: read that record too, and ask again"
            seen.add(onto)
            at = by_entity[onto]
        return at, None

    # target entity -> (score, how, the name that matched, label, the duplicate it came through, what is wrong)
    best = {}
    for c in cands:
        top = None
        for x, x_key in c["names"]:
            score, how = name_score(key, x_key)
            # Of several names that score the same, the last one is reported.
            if top is None or score >= top[0]:
                top = (score, how, x)
        if top is None or top[0] < least:
            continue
        there, why = target(c)
        entity = there["entity"]
        via = "" if entity == c["entity"] else c["entity"]
        if entity not in best or best[entity][0] < top[0] or (why is not None and best[entity][5] is None and best[entity][0] == top[0]):
            best[entity] = (top[0], top[1], top[2], there["subject"], via, why)
    hits = sorted(best.items(), key=lambda hit: (-hit[1][0], hit[0]))

    out = {
        "name": name,
        "key": key,
        "query": {"collection": "ix", "where": [["nks", "array-contains", key]]},
    }
    if kind is not None:
        out["type"] = kind
        slug = "".join("-" if c == " " else c for c in key if c == " " or c in ASCII_ALNUM)[:60].strip("-")
        if slug != "":
            suggested = f"{kind}:{slug}"
            again = 2
            while any(c["entity"] == suggested for c in cands) and again < 100:
                suggested = f"{kind}:{slug}-{again}"
                again += 1
            out["suggest"] = suggested
    if given:
        exact = [entity for entity, hit in hits if hit[0] == 100]
        verdict = "use" if len(exact) == 1 else "new" if not hits else "ask"
        # What a person has to settle before any answer can be trusted: a merge that leads
        # nowhere, or a candidate that could not be used at all. Neither "use" nor "new"
        # is said over it.
        reasons = [f"{entity}: {hit[5]}" for entity, hit in hits if hit[5] is not None]
        reasons += [f"{len(ignored)} of the documents given could not be used as candidates"] if ignored else []
        if reasons:
            verdict = "ask"
            out["why"] = reasons
        out["checked"] = num(len(cands))
        out["verdict"] = verdict
        if verdict == "use":
            out["use"] = exact[0]
            out["subject"] = best[exact[0]][3]
        matches = []
        for entity, hit in hits[:5]:
            one = {"entity": entity, "subject": hit[3], "matched": hit[2], "score": num(hit[0]), "how": hit[1]}
            if hit[4] != "":
                one["via"] = hit[4]
            if hit[5] is not None:
                one["problem"] = hit[5]
            matches.append(one)
        out["matches"] = matches
        if ignored:
            out["ignored"] = [{"entity": entity[:200], "why": why} for entity, why in ignored[:20]]
    say(display_json(out))
    return True


# ---------- pack and personalize ----------

# The skill as shipped names no log. Setup makes one for the account, and `personalize`
# writes that account's own copy of the skill, with its links in place of these three
# words, as a folder and as a `.skill` file ready to upload. `pack` writes the skill as
# shipped, the public one, with the same writer.
PLACEHOLDERS = ["ROWKEEP_LOG_LINK", "ROWKEEP_LOG_SLUG", "ROWKEEP_VAULT_LINK"]
# What a skill ships: these files and no others, in this order. A list, and not whatever
# the folder happens to hold: a checkout owner's local.json, an editor's backup or a
# __pycache__ beside the script must never ride along into an archive that is handed out.
SKILL_FILES = [
    "LICENSE", "SKILL.md", "ops/BRIEF.md", "ops/SEALER.md", "ops/VAULT.md", "registry/verbs.json", "scripts/rowkeep.py",
    "scripts/rowkeep.sh", "setup/SETUP.md", "setup/capabilities.json", "setup/log-page.html", "setup/vault-page.html",
]
# The licence text is put into the skill folder when a release is made; a checkout may be
# without it.
OPTIONAL_SKILL_FILES = ["LICENSE"]
PERSONAL_ARCHIVE = "rowkeep-personal.skill"


def skill_files(skill):
    """(name inside the skill with `/` between its parts, contents) for each file a skill
    ships, read from a skill folder. Nothing is read through a link, each must be a regular
    file, and a name that is there twice but for capitals is refused: on a system that does
    not tell capitals apart the two would be one file, and nobody could say which."""
    if not os.path.isfile(os.path.join(skill, "SKILL.md")):
        raise Refused(f"{show_path(skill)} is not a skill folder: it has no SKILL.md")
    found = []
    for rel in SKILL_FILES:
        at = skill
        parts = rel.split("/")
        missing = False
        for depth, part in enumerate(parts):
            try:
                names = os.listdir(at)
            except OSError as error:
                raise Refused(f"{show_path(at)}: {os_error_text(error, at)}")
            alike = [name for name in names if name.lower() == part.lower()]
            if len(alike) > 1 or (alike and alike[0] != part):
                raise Refused(f"{show_path(at)}: holds {' and '.join(show(name) for name in sorted(alike))} where the skill ships {show(part)}: names that differ only in capitals cannot be packed")
            if not alike:
                missing = True
                break
            at = os.path.join(at, part)
            if is_link(at):
                raise Refused(f"{show_path(at)}: is a link, and nothing is packed through a link")
            if depth < len(parts) - 1 and not os.path.isdir(at):
                raise Refused(f"{show_path(at)}: is not a folder")
        if missing:
            if rel in OPTIONAL_SKILL_FILES:
                continue
            raise Refused(f"{show_path(skill)}: the skill's file {rel} is missing")
        found.append((rel, read_bytes(at, inside=True)))
    return found


def skill_front_matter(text):
    """Check what claude.ai checks of a skill's SKILL.md when it is uploaded: a block at
    the top naming the skill `rowkeep` and describing it in at most 1024 characters."""
    # Line ends may be CRLF: the archive holds the file's bytes as they are.
    front = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    name = re.search(r"^name:[ \t]*(\S+)[ \t]*\r?$", front.group(1), re.M) if front else None
    described = re.search(r"^description:[ \t]*(.*)$", front.group(1), re.M) if front else None
    if name is None or described is None or trim(described.group(1)) == "":
        raise Refused("SKILL.md must begin with a block between --- lines that gives a name and a description")
    if name.group(1) != "rowkeep":
        raise Refused("SKILL.md names the skill something other than rowkeep")
    if len(trim(described.group(1))) > 1024:
        raise Refused(f"SKILL.md's description is {len(trim(described.group(1)))} characters; claude.ai accepts at most 1024")


def zip_of(entries):
    """A zip archive of (name, contents, runnable): deflated, UTF-8 names, Unix permissions,
    and one fixed date, so the same files always give the same archive."""
    out, central = b"", b""
    for name, data, runnable in entries:
        name_bytes = name.encode("utf-8")
        deflate = zlib.compressobj(6, zlib.DEFLATED, -15)
        packed = deflate.compress(data) + deflate.flush()
        # version needed, flags (UTF-8 names), method (deflate), time, date (1980-01-01),
        # checksum, packed size, size, name length, extra length
        fixed = struct.pack("<HHHHHIIIHH", 20, 0x0800, 8, 0, 0x21, crc32(data), len(packed), len(data), len(name_bytes), 0)
        offset = len(out)
        out += struct.pack("<I", 0x04034B50) + fixed + name_bytes + packed
        mode = 0o100755 if runnable else 0o100644
        # made on Unix; then comment length, disk and internal attributes (all zero), the
        # permissions, and where the file's own header is
        central += struct.pack("<IH", 0x02014B50, (3 << 8) | 20) + fixed + bytes(6) + struct.pack("<II", mode << 16, offset) + name_bytes
    end = struct.pack("<IIHHIIH", 0x06054B50, 0, len(entries), len(entries), len(central), len(out), 0)
    return out + central + end


def skill_archive(files):
    """The `.skill` file for a skill's files: one folder, `rowkeep`, holding them in the
    order of SKILL_FILES, the two scripts runnable. The same files give the same bytes."""
    return zip_of([("rowkeep/" + rel, data, rel.startswith("scripts/")) for rel, data in files])


def skill_text(files, rel):
    data = dict(files)[rel]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{rel} is not UTF-8 text")


def cli_pack(flags):
    """Write the skill as shipped, naming no log, as one `.skill` file."""
    skill = need(flags, "skill", "pack")
    archive = need(flags, "out", "pack")
    files = skill_files(skill)
    text = skill_text(files, "SKILL.md")
    skill_front_matter(text)
    if PLACEHOLDERS[0] not in text:
        raise Refused("this skill folder names a log: pack writes the skill as shipped, which names none. Pack the folder in the repository, not a copy made for an account")
    data = skill_archive(files)
    new_file(os.path.dirname(archive) or os.curdir, archive, data)
    say('{"file":%s,"files":%d,"bytes":%d,"sha256":%s}' % (display_json(absolute(archive)), len(files), len(data), json_text(sha256_hex(data))))
    return True


def cli_personalize(flags):
    skill = need(flags, "skill", "personalize")
    out = need(flags, "out", "personalize")
    log = trim(need(flags, "log", "personalize"))
    if not log.startswith("https://claude.ai/") or any(misleading(c) or c in WHITE_SPACE for c in log):
        raise Refused("--log must be the log artifact's claude.ai link")
    slug = flag(flags, "slug") or log
    vault = flag(flags, "vault") or "none yet"
    if any(misleading(c) for c in slug + vault):
        raise Refused("--slug and --vault must be plain text on one line")
    files = []
    filled_in = 0
    for rel, data in skill_files(skill):
        if rel.endswith(".md"):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise Refused(f"{rel} is not UTF-8 text")
            new = text.replace(PLACEHOLDERS[0], log).replace(PLACEHOLDERS[1], slug).replace(PLACEHOLDERS[2], vault)
            if new != text:
                filled_in += 1
            data = new.encode("utf-8")
        files.append((rel, data))
    if filled_in == 0:
        raise Refused("this skill folder names a log already: personalize the copy as shipped, not one made for an account")
    skill_front_matter(skill_text(files, "SKILL.md"))
    # Its name says whose it is, so that it is never taken for the public rowkeep.skill.
    archive = os.path.join(out, PERSONAL_ARCHIVE)
    new_files(out, [(os.path.join(out, "rowkeep", *rel.split("/")), data) for rel, data in files] + [(archive, skill_archive(files))])
    say('{"folder":%s,"file":%s,"files":%d}' % (display_json(absolute(os.path.join(out, "rowkeep"))), display_json(absolute(archive)), len(files)))
    return True


def cli_today(flags):
    """Today's date in a named time zone. A zone named in a prompt goes in by file, never
    into `TZ=<zone> date`; and one that is not known is refused, where `date` would
    answer in UTC without saying so."""
    zone = need(flags, "zone", "today")
    if not re.fullmatch(r"[A-Za-z0-9_+-]{1,40}(/[A-Za-z0-9_+-]{1,40}){0,2}", zone):
        raise Refused(f"--zone {show(zone)} is not a time zone name such as America/New_York")
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        day = datetime.fromtimestamp(now_secs(), ZoneInfo(zone)).strftime("%Y-%m-%d")
    except Exception:
        raise Refused(f"this Python has no data for the time zone {show(zone)} (it takes Python 3.9 or later and the system's time zone files), so it gives no date")
    say(day)
    return True


def cli_fingerprint(flags):
    say(fingerprint(parse(read_text(need(flags, "file", "fingerprint")))))
    return True


# ---------- page ----------

# What a whole HTML document has above the part the bundled pages hold.
PAGE_TOP = '<!DOCTYPE html>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'


def cli_page(flags):
    source = need(flags, "from", "page")
    out = need(flags, "out", "page")
    text = read_text(source)
    if os.path.lexists(out):
        raise Refused(f"{show_path(out)}: is already there; give the name of a new file")
    # A page that is already a whole document is copied as it is.
    whole = text if text.lstrip()[:9].lower() == "<!doctype" else PAGE_TOP + text
    new_file(os.path.dirname(out) or os.curdir, out, whole)
    say(f"wrote {show_path(absolute(out))}: a whole HTML document of {utf8_len(whole)} bytes. Publish this file as it is")
    return True


def options(values="", switches=""):
    """What one command takes: options that take a value (each of those can also be given
    as --NAME-file PATH), and switches, which are given on their own."""
    return (values.split(), switches.split())


ROW_TEXTS = "verb entity subject status source actor key ts payload"
SEQ = "out registry sequence source actor subject what on "
# Every option of every command. An option that is not listed for a command is refused: a
# mistyped --ix that is quietly ignored turns a check of the index into a check of nothing.
OPTIONS = {
    "version": options(),
    "help": options(),
    "verbs": options("registry"),
    "row": options("out registry from " + ROW_TEXTS),
    "match": options("registry ix min name type candidates"),
    "seq-define": options(SEQ + "steps"),
    "seq-enroll": options(SEQ + "target steps owner"),
    "seq-step": options(SEQ + "target steps step"),
    "seq-end": options(SEQ + "target outcome step"),
    "seal-keys": options("inbox registry max leave", "inline"),
    "seal": options("inbox ix out account registry versions inbox-versions inbox-collection max leave", "inline"),
    "ingest": options("from out id collection page"),
    "page": options("from out"),
    "confirm": options("plan read"),
    "check": options("stream ix registry", "partial"),
    "rebuild": options("stream ix out registry versions", "checksums rewind"),
    "vault-plan": options("stream out vault-head head-version versions max source", "inline moved"),
    "vault-check": options("rows vault-head stream", "partial"),
    "vault-restore": options("rows vault-head out registry", "partial"),
    "first-docs": options("out account registry auto-log announce", "inline"),
    "personalize": options("skill out log slug vault"),
    "pack": options("skill out"),
    "fingerprint": options("file"),
    "today": options("zone"),
}


def text_from_file(path, name):
    """The value of --NAME-file PATH: the file's text, less exactly one newline at its end.
    Text that was read from a row is given this way, so that it is never part of a shell
    command."""
    try:
        text = read_text(path)
    except Refused as why:
        raise Refused(f"--{name}-file: {why}")
    if text.endswith("\r\n"):
        return text[:-2]
    return text[:-1] if text.endswith("\n") else text


def split_args(cmd, args):
    """The options given to one command, as {name: value}. Every argument must be an option
    the command has; an option that takes a value must be followed by one; none may be given
    twice. A switch takes no value and is held as "". A value cannot itself start with `--`:
    that is how a forgotten value is told from a given one."""
    values, switches = OPTIONS[cmd]
    flags = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg.startswith("--") or len(arg) < 3:
            raise Refused(f"{cmd}: {show(arg)} is not an option; options are written --name value. Nothing was done")
        given = name = arg[2:]
        from_file = name not in values + switches and name.endswith("-file") and name[:-5] in values
        if from_file:
            name = name[:-5]
        if name not in values + switches:
            raise Refused(f"{cmd} has no option {show('--' + given)}. Nothing was done. Run: rowkeep help")
        if name in flags:
            raise Refused(f"{cmd}: --{name} was given more than once. Nothing was done")
        if name in switches:
            flags[name] = ""
            i += 1
            continue
        if i + 1 >= len(args) or args[i + 1].startswith("--"):
            hint = "" if from_file else f" (text that starts with -- can be given as --{name}-file PATH)"
            raise Refused(f"{cmd}: --{given} needs a value{hint}. Nothing was done")
        flags[name] = text_from_file(args[i + 1], name) if from_file else args[i + 1]
        i += 2
    return flags


COMMANDS = {
    "row": cli_row,
    "match": cli_match,
    "verbs": cli_verbs,
    "seal-keys": cli_seal_keys,
    "seal": cli_seal,
    "ingest": cli_ingest,
    "check": cli_check,
    "confirm": cli_confirm,
    "page": cli_page,
    "first-docs": cli_first_docs,
    "rebuild": cli_rebuild,
    "personalize": cli_personalize,
    "pack": cli_pack,
    "vault-plan": cli_vault_plan,
    "vault-check": cli_vault_check,
    "vault-restore": cli_vault_restore,
    "fingerprint": cli_fingerprint,
    "today": cli_today,
}


def run(args):
    """Run one command. True: done. False: a check found problems."""
    cmd = args[0] if args else "help"
    if cmd in ("--help", "-h"):
        cmd = "help"
    if cmd not in OPTIONS:
        raise Refused(f"unknown command {show(cmd)}. Run: rowkeep help")
    flags = split_args(cmd, args[1:])
    if cmd == "version":
        say("rowkeep %s (Python %d.%d.%d)" % ((VERSION,) + tuple(sys.version_info[:3])))
        return True
    if cmd == "help":
        for line in USAGE.split("\n"):
            say(line)
        return True
    if cmd in ("seq-define", "seq-enroll", "seq-step", "seq-end"):
        return cli_seq(cmd, flags)
    return COMMANDS[cmd](flags)


EXIT_OUTPUT_CLOSED = 4


def main(args):
    """Exit codes: 0 for success, 1 when a check finds problems or could not confirm what it
    was asked to, 2 for an error or a refusal, 4 when whoever was reading the output stopped
    reading before the command had finished saying what it found. (3 is the launcher's: no
    Python that runs.)"""
    try:
        return 0 if run(args) else 1
    except Refused as why:
        warn(f"rowkeep: {why}")
        return 2
    except OutputClosed:
        # Nothing more can be printed. Whatever verdict was coming did not arrive, so this
        # is not success: a caller that reads only the exit code must not take a run that
        # was cut off for a clean one. Point the output somewhere harmless so that closing
        # it on the way out does not complain as well.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return EXIT_OUTPUT_CLOSED
    except Exception:
        # A fault in this script must not look like "a check found problems", which is 1.
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
