#!/usr/bin/env python3
"""python tools/public-tree.py [DEST] [--tracked] [--scan DIR] [--archive FILE]... [--reveal]

Looks for things that must never be published, and can write the copy to publish.

  DEST            Write a copy of this repository's tracked files into DEST, which must not
                  exist yet, and check the copy. Needs a clean working tree. Nothing ignored
                  (a local.json, dist/, build folders) can come along. It makes no commit and
                  contacts nothing: starting a new repository's history from the copy is a
                  separate, deliberate step.
  --tracked       Check the tracked files where they are, without copying.
  --scan DIR      Check every file under DIR, whatever it is.
  --archive FILE  Check a `.skill` or `.zip`: the file's own name, every entry's name and
                  contents, the archive's own bytes, and any archive inside it. May be
                  given more than once.
  --reveal        Print what was found in full. Without it the middle of each value is
                  masked, in the VALUE and wherever it is part of the FILE as well (a
                  private detail can be in a file's or an entry's name), so that a log of
                  this check does not publish what it caught.

Every file's path is checked as well as its contents, and so is the text a picture carries.
A finding is printed as  FILE:LINE  NAME-OF-THE-PATTERN  VALUE.

What it looks for, and what it does not:

  addresses    Email addresses, other than a no-reply sender and addresses at example,
               .test and .invalid domains. A person's home folder, as Windows, macOS and
               Linux write it, and root's. Shares on another machine. Private and
               link-local IPv4 addresses, and localhost with a port.
               NOT: IPv6 addresses other than [::1], public IP addresses, host names,
               street addresses, phone numbers, or a person's name.
  credentials  A value given to a name such as password, api_key, token or secret, where
               the value looks generated. Tokens with the prefixes GitHub, GitLab, Slack,
               Stripe and "sk-" keys use. AWS and Google key ids. Authorization headers
               and bearer tokens. Signed web tokens. Private key blocks. A password
               inside a link.
               NOT: a secret with no known prefix that no such name is given to, a
               password that is an ordinary word, or anything encoded or encrypted.
  identifiers  Links to an artifact, UUIDs, user ids, the ids of sessions and routines,
               and the project's former names.
  pictures     PNG: the text chunks (tEXt, zTXt, iTXt), eXIf and iCCP read as text, and
               bytes after the picture's end. GIF: comment, plain-text and application
               blocks, each with its pieces joined, and bytes after its end.
               NOT: what a picture shows (a screenshot of a private page passes), EXIF
               fields that are not plain text, or the metadata of any other kind of
               picture (JPEG, WebP), whose bytes are read only as plain letters.

A known harmless value is allowed only as that exact value, for that pattern, in that file:
see ALLOWED. Anything else on the same line is still reported.

Exit 0: nothing found. Exit 1: findings. Exit 2: something could not be read or checked;
that is a failure, never "nothing found". These checks look for what a project like this
one can hold by accident. They are not proof: read the copy before publishing it.

Standard library only; Python 3.8 or later.
"""
import fnmatch
import hashlib
import io
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ID = r"A-Za-z0-9_-"

# ---------- what is looked for ----------

# The project's former names are written in two parts here and nowhere whole, so that this
# file passes its own check.
FORMER_NAMES = ["pack" + "log", "row" + "stash"]


def email_is_placeholder(match):
    """The two forms of address a public tree may hold: a no-reply sender, and an address
    at a domain reserved for examples."""
    local, domain = match.group(0).lower().rsplit("@", 1)
    labels = domain.split(".")
    return local in ("noreply", "no-reply") or "noreply" in labels or "example" in labels[-2:] or labels[-1] in ("test", "invalid")


def made_up(value):
    """As the script's own credential filter decides it: a digit, a capital after the first
    letter, or one of the signs passwords are padded with."""
    return any(c.isdigit() or c in "!@#$%^*~+=" for c in value) or any("A" <= c <= "Z" for c in value[1:])


def named_secret(match):
    """`password: x`, `api_key=x` and the like, where the value looks like one and is not
    plainly code (a call, an index, a name with dots that goes on to a bracket)."""
    name, value = match.group(1).lower().replace("-", "_"), match.group(2)
    least = 4 if name in ("password", "passwd", "pwd", "passphrase") else 12
    if len(value.encode("utf-8")) < least or not (made_up(value) or len(value) >= 24):
        return True
    return False


def has_digit(match):
    return not any(c.isdigit() for c in match.group(0))


# (name, pattern, a test that says "this match is not one" or None). Patterns are matched
# against the whole text, so every occurrence on a line is found.
RULES = [
    ("artifact-link", re.compile(r"claude\.ai/(?:[a-z]+/)?artifacts?/[%s]+|(?<![A-Za-z0-9.])/artifacts?/(?=[%s]*[0-9])[%s]{16,}" % (ID, ID, ID), re.I), None),
    ("uuid", re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), None),
    ("user-id", re.compile(r"(?<![%s])u_[%s]{22}" % (ID, ID)), None),
    ("service-id", re.compile(r"(?<![A-Za-z0-9])(?:trig|env|cse|session|routine)_[A-Za-z0-9]{12,}", re.I), None),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}"), email_is_placeholder),
    ("home-path", re.compile(r"[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[^\\/\s\"'<>|*?:]+|(?<![A-Za-z0-9._-])/(?:Users|home)/[A-Za-z0-9._-]+|(?<![A-Za-z0-9._-])/root(?=/)", re.I), None),
    # A share on another machine (two backslashes, the machine, one backslash, the share),
    # as it is written, or as it is inside a quoted string of code, where every backslash
    # is doubled. A run of escapes in code (a doubled backslash, then u202e or x1b) is not.
    ("unc-path", re.compile(r"(?<!\\)\\\\(?!\\)[A-Za-z0-9][A-Za-z0-9.-]*\\(?!\\)[A-Za-z0-9$_.-]+|(?<!\\)\\\\\\\\(?!\\)[A-Za-z0-9][A-Za-z0-9.-]*\\\\(?!\\)[A-Za-z0-9$_.-]+"), lambda match: re.match(r"\\+(?:u[0-9a-fA-F]{2,}|x[0-9a-fA-F]{2})", match.group(0)) is not None),
    ("private-ip", re.compile(r"(?<![0-9.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168|169\.254|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\.\d{1,3}\.\d{1,3}(?![0-9]|\.[0-9])"), None),
    ("localhost-url", re.compile(r"(?<![A-Za-z0-9.-])(?:localhost|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|0\.0\.0\.0|\[::1\]):\d{2,5}", re.I), None),
    ("credential-assignment", re.compile(r"(?<![A-Za-z0-9_-])(password|passwd|pwd|passphrase|api[_-]?key|access[_-]?token|client[_-]?secret|secret|token)[ \"']*[=:][ \"']*([^\s&\"',;]+)", re.I), named_secret),
    ("token", re.compile(r"(?<![A-Za-z0-9_-])(?:sk-|sk_live_|sk_test_|rk_live_|pk_live_|gh[posur]_|github_pat_|xox[bpas]-|glpat-)[A-Za-z0-9+/_=.-]{10,}", re.I), has_digit),
    ("cloud-key", re.compile(r"(?<![A-Za-z0-9_-])(?:AKIA|ASIA)[A-Za-z0-9+/_=.-]{16,}|(?<![A-Za-z0-9_-])AIza[A-Za-z0-9+/_=.-]{30,}"), None),
    ("authorization-header", re.compile(r"(?<![A-Za-z0-9_])authorization[ \"']*[=:][ \"']*(?:[A-Za-z0-9_-]{1,20}[ \t]+)?([A-Za-z0-9+/_=.-]{8,})", re.I), lambda match: not made_up(match.group(1))),
    ("bearer-token", re.compile(r"(?<![A-Za-z0-9_-])bearer[ \t]+[A-Za-z0-9+/_=.-]{20,}", re.I), has_digit),
    ("signed-web-token", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_.-]{4,}"), None),
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY"), None),
    ("password-in-link", re.compile(r"://[^\s/@:]+:[^\s/@]+@"), None),
    ("former-name", re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(FORMER_NAMES), re.I), None),
]
RULE_NAMES = [name for name, _, _ in RULES] + ["private-file-name", "built-file", "link", "scanner-error"]

# File names that hold secrets or one person's own settings, whatever is in them.
PRIVATE_NAMES = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "local.json", ".npmrc", ".netrc"]
# Things that are built, not written: never part of a source tree that is published.
BUILT_NAMES = ["*.skill", "*.zip", "*.pyc", "*.pyo", "*.exe", "*.dll", "*.so", "*.dylib", "__pycache__"]

# ---------- what is allowed ----------

# A known fixture: (file, pattern's name, the exact matched value), and for a value that
# may stand on one known line only, the SHA-256 of that whole line as a fourth part, so
# that the allowance ends when the line is reworded. A file is named by its path; a path
# ending in `/` names every file under that folder; an entry inside an archive is
# ARCHIVE-NAME!ENTRY-NAME. Each value is written in two parts, so that this file does not
# hold it whole and passes its own check.
USER = "u_" + "abcdefghijklmnopqrstuv"  # the made-up account every test and fixture signs as
LINK = "claude.ai/" + "artifact/example"  # the made-up link the tests personalize a skill with
GITHUB_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
CLOUD_KEY = "AKIA" + "IOSFODNN7EXAMPLE"  # the access key id the cloud vendor's own documents use
BEARER = "Bearer " + "abcdefghij0123456789xyz"
OLD_TITLE = "Pack" + "log"  # the title of a log made before version 0.6.0
UNIT, STREAM = "test/unit.py", "test/stream.mjs"
SKILL_MD, SETUP_MD = "plugin/skills/rowkeep/SKILL.md", "plugin/skills/rowkeep/setup/SETUP.md"
# The one line that says what a log made under the project's first name is titled: the
# line of history in the format's description. The name is allowed on exactly that line, as
# it now reads, and nowhere in the skill.
HISTORY_LINE = "15dfe0945a7eecfb89c0b448f08e6cc2e9134893c5937b7400d0b829f069b3e9"
ALLOWED = [
    ("test/fixtures/", "user-id", USER),
    (UNIT, "user-id", USER),
    (STREAM, "user-id", USER),
    ("test/page.mjs", "user-id", USER),
    ("tools/drill.mjs", "user-id", USER),
    ("tools/make-older-log.py", "user-id", USER),
    (UNIT, "artifact-link", LINK),
    (STREAM, "artifact-link", LINK),
    ("test/page.mjs", "artifact-link", "claude.ai/" + "artifact/EXAMPLE"),
    # The link the skill's text uses to show what an artifact's link looks like.
    (SKILL_MD, "artifact-link", "claude.ai/" + "artifact/abc123XYZ"),
    ("docs/ACTIVITY-STREAM.md", "former-name", OLD_TITLE, HISTORY_LINE),
    # What the tests feed the script's credential filter: shapes it must catch, all made up.
    (UNIT, "token", GITHUB_TOKEN),
    (STREAM, "token", GITHUB_TOKEN),
    (UNIT, "token", "github_pat_" + "11ABCDEFG0abcdefghijklmn_opqrstuvwxyz0123456789"),
    (UNIT, "token", "sk-" + "proj-abcdefghijklmnopqrstuvwxyz0123456789"),
    (UNIT, "token", "sk_live_" + "51Habcdefghijklmnop"),
    (UNIT, "token", "xoxb-" + "123456789012-abcdefghijklmnop"),
    (UNIT, "cloud-key", CLOUD_KEY),
    (STREAM, "cloud-key", CLOUD_KEY),
    (UNIT, "bearer-token", BEARER),
    (UNIT, "authorization-header", "Authorization" + ": Basic dXNlcjpwYXNzd29yZA=="),
    (UNIT, "authorization-header", "authorization" + ": Digest dXNlcj0iTWFsbG9yeSI"),
    (UNIT, "authorization-header", "Authorization" + '": "Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=='),
    (UNIT, "authorization-header", "Authorization" + ": abcdefghij0123456789xyz"),
    (UNIT, "signed-web-token", "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    (UNIT, "private-key", "-----BEGIN " + "RSA PRIVATE KEY"),
    (UNIT, "private-key", "-----BEGIN " + "OPENSSH PRIVATE KEY"),
    (UNIT, "private-key", "-----BEGIN " + "PRIVATE KEY"),
    (UNIT, "password-in-link", "://admin" + ":hunter2@"),
    (UNIT, "credential-assignment", "password" + ": hunter2"),
    (UNIT, "credential-assignment", "password" + ": [Hidden]"),
    (UNIT, "credential-assignment", "password" + '": "hunter2'),
    (UNIT, "credential-assignment", "pwd" + "=Tr0ub4dor"),
    (UNIT, "credential-assignment", "pwd" + "=[Hidden]"),
    (UNIT, "credential-assignment", "client_secret" + ": aVeryLongSecretValueWithoutDigits"),
    (UNIT, "credential-assignment", "api_key" + "=abcdef0123456789"),
]
# The same lines of the skill, as they are inside the public archive.
ALLOWED += [("rowkeep.skill!rowkeep/" + entry[0][len("plugin/skills/rowkeep/") :],) + entry[1:] for entry in ALLOWED if entry[0] in (SKILL_MD, SETUP_MD)]


def digest(text):
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


class Allowances:
    def __init__(self, entries):
        self.entries = []
        for entry in entries:
            if entry[1] not in RULE_NAMES:
                raise ValueError("ALLOWED names a pattern that does not exist: %s" % entry[1])
            self.entries.append(tuple(entry))
        self.used = set()

    def takes(self, path, rule, value, line_text):
        for entry in self.entries:
            at = entry[0]
            if (at == path or at.endswith("/") and path.startswith(at)) and entry[1] == rule and entry[2] == value:
                if len(entry) > 3 and digest(line_text) != entry[3]:
                    continue
                self.used.add(entry)
                return True
        return False

    def unused(self):
        return [entry for entry in self.entries if entry not in self.used]


# ---------- scanning ----------


class Scan:
    def __init__(self, allowed):
        self.allowed = Allowances(allowed)
        self.findings = []  # (where, line, rule, value)
        self.errors = []
        self.files = 0
        self.let_through = 0

    def found(self, where, line, rule, value, line_text=""):
        if self.allowed.takes(where, rule, value, line_text):
            self.let_through += 1
            return
        self.findings.append((where, line, rule, value))

    def error(self, where, what):
        self.errors.append((where, what))

    def text(self, where, text, first_line=1):
        """Every rule over one text."""
        starts = None
        for rule, pattern, not_one in RULES:
            for match in pattern.finditer(text):
                if not_one is not None and not_one(match):
                    continue
                if starts is None:
                    starts = [0] + [m.end() for m in re.finditer("\n", text)]
                line = bisect(starts, match.start())
                end = starts[line] if line < len(starts) else len(text)
                self.found(where, line + first_line - 1 if first_line else 0, rule, match.group(0), text[starts[line - 1] : end].rstrip("\r\n"))

    def path(self, where, rel, built=True):
        """A file's own path: its name, and every rule over the path as text. `built`
        false is for an archive that was itself handed over to be checked: it is a built
        file, and that is not a finding about it."""
        for part in rel.split("/"):
            folded = part.lower()
            if any(fnmatch.fnmatchcase(folded, pattern) for pattern in PRIVATE_NAMES):
                self.found(where, 0, "private-file-name", part)
            if built and any(fnmatch.fnmatchcase(folded, pattern) for pattern in BUILT_NAMES):
                self.found(where, 0, "built-file", part)
        self.text(where, rel, first_line=0)

    def contents(self, where, data, depth=0):
        """A file's contents: as text, and by its kind (a picture's text chunks, an
        archive's entries)."""
        self.files += 1
        try:
            self.text(where, data.decode("utf-8"))
        except UnicodeDecodeError:
            # Not UTF-8: its bytes are read one to a character, which finds anything
            # written in plain letters, and as two-byte text where that is what it is.
            self.text(where, data.decode("latin-1"))
            if data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[1:2] == b"\x00" or data[:1] == b"\x00":
                self.text(where, data.decode("utf-16", "replace"))
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            self.png(where, data)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            self.gif(where, data)
        if data[:4] == b"PK\x03\x04" or where.lower().endswith((".zip", ".skill")):
            if depth >= 3:
                self.error(where, "an archive inside an archive inside an archive inside an archive: not opened")
            else:
                self.archive(where, data, depth + 1)

    def png(self, where, data):
        at = 8
        while at + 12 <= len(data):
            length, kind = struct.unpack(">I4s", data[at : at + 8])
            body = data[at + 8 : at + 8 + length]
            if len(body) != length:
                self.error(where, "a PNG cut short inside a chunk")
                return
            name = kind.decode("latin-1")
            try:
                if kind == b"tEXt":
                    self.text("%s [%s]" % (where, name), body.replace(b"\x00", b"\n").decode("latin-1"), first_line=0)
                elif kind == b"zTXt":
                    key, _, rest = body.partition(b"\x00")
                    self.text("%s [%s]" % (where, name), (key + b"\n" + zlib.decompress(rest[1:])).decode("latin-1"), first_line=0)
                elif kind == b"iTXt":
                    key, _, rest = body.partition(b"\x00")
                    packed, rest = rest[:1] == b"\x01", rest[2:]
                    language, _, rest = rest.partition(b"\x00")
                    translated, _, words = rest.partition(b"\x00")
                    if packed:
                        words = zlib.decompress(words)
                    self.text("%s [%s]" % (where, name), b"\n".join([key, language, translated, words]).decode("utf-8", "replace"), first_line=0)
                elif kind in (b"eXIf", b"iCCP"):
                    self.text("%s [%s]" % (where, name), body.decode("latin-1"), first_line=0)
            except zlib.error as error:
                self.error(where, "a %s chunk that does not unpack: %s" % (name, error))
            at += 12 + length
            if kind == b"IEND":
                break
        if at < len(data):
            self.text("%s [after the picture's end]" % where, data[at:].decode("latin-1"), first_line=0)

    def gif(self, where, data):
        """The words a GIF can carry beside its pictures: comment, plain-text and
        application blocks (a program's name, a loop count, XMP), and whatever follows
        its end. A block is stored in pieces of at most 255 bytes, each behind a byte that
        gives its length, so a value can be cut in two by a length byte and missed when
        the file's bytes are read as text: here the pieces are joined first."""
        names = {0xFE: "comment", 0x01: "plain text", 0xFF: "application"}

        def table(flags):
            return 3 * (2 << (flags & 7)) if flags & 0x80 else 0

        def pieces(at, keep):
            out = []
            while True:
                size = data[at]
                at += 1
                if size == 0:
                    return b"".join(out), at
                if at + size > len(data):
                    raise IndexError
                if keep:
                    out.append(data[at : at + size])
                at += size

        try:
            at = 13 + table(data[10])
            while True:
                kind = data[at]
                if kind == 0x3B:  # the end
                    at += 1
                    break
                if kind == 0x21:  # an extension
                    label = data[at + 1]
                    body, at = pieces(at + 2, label in names)
                    if label in names:
                        self.text("%s [%s block]" % (where, names[label]), body.decode("latin-1"), first_line=0)
                elif kind == 0x2C:  # a picture: its header, its colours, its data
                    at += 10 + table(data[at + 9]) + 1
                    _, at = pieces(at, False)
                else:
                    self.error(where, "a GIF with a block of a kind that is not a GIF's")
                    return
        except IndexError:
            self.error(where, "a GIF cut short inside a block")
            return
        if at < len(data):
            self.text("%s [after the picture's end]" % where, data[at:].decode("latin-1"), first_line=0)

    def archive(self, where, data, depth):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if z.comment:
                    self.text("%s [comment]" % where, z.comment.decode("latin-1"), first_line=0)
                infos = z.infolist()
                if not infos:
                    self.error(where, "an archive with no entries")
                for info in infos:
                    inside = "%s!%s" % (where, info.filename)
                    self.path(inside, info.filename)
                    if stat.S_ISLNK(info.external_attr >> 16):
                        self.found(inside, 0, "link", info.filename)
                    if info.is_dir():
                        continue
                    self.contents(inside, z.read(info), depth)
        except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, OSError, ValueError, EOFError) as error:
            self.error(where, "an archive that could not be read: %s" % error)


def bisect(starts, offset):
    """The 1-based number of the line that holds `offset`."""
    low, high = 0, len(starts)
    while low < high:
        middle = (low + high) // 2
        if starts[middle] <= offset:
            low = middle + 1
        else:
            high = middle
    return low


def mask(value, count=True):
    shown = " ".join(value.split())
    if len(shown) <= 4:
        return shown[:1] + "*" * (len(shown) - 1)
    keep = min(4, len(shown) // 4)
    return "%s%s%s%s" % (shown[:keep], "*" * min(len(shown) - 2 * keep, 12), shown[-keep:], " (%d characters)" % len(shown) if count else "")


def mask_where(where, values):
    """A place, with every value that was found masked where it is part of the place: a
    file named for a person, an entry named with an id. Longest first, so that a value
    inside another is not left half shown."""
    for value in sorted(set(values), key=len, reverse=True):
        if len(value) >= 3:
            where = where.replace(value, mask(value, count=False))
    return where


def is_plain_file(info):
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return False
    return stat.S_ISREG(info.st_mode)


def scan_files(scan, base, rels):
    """Check the files at base/rel, naming each by rel."""
    for rel in rels:
        scan.path(rel, rel)
        full = os.path.join(base, *rel.split("/"))
        try:
            info = os.lstat(full)
            if not is_plain_file(info):
                scan.found(rel, 0, "link", rel)
                continue
            with open(full, "rb") as f:
                data = f.read()
        except OSError as error:
            scan.error(rel, "could not be read: %s" % error)
            continue
        scan.contents(rel, data)


def walk(base):
    """Every file under base, as paths with `/`. Links and folders that are links are
    listed and not followed."""
    out = []

    def into(folder, rel):
        for name in sorted(os.listdir(folder)):
            full = os.path.join(folder, name)
            info = os.lstat(full)
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                into(full, rel + name + "/")
            else:
                out.append(rel + name)

    into(base, "")
    return out


def git(root, *args):
    done = subprocess.run(["git", "-C", root] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if done.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), done.stderr.decode("utf-8", "replace").strip()))
    return done.stdout


def tracked(root):
    names = [name for name in git(root, "ls-files", "-z").decode("utf-8").split("\x00") if name]
    if not names:
        raise RuntimeError("git lists no tracked files in %s" % root)
    return names


def main(argv):
    dest, scan_dirs, archives, in_place, reveal, root = None, [], [], False, False, ROOT
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg in ("--archive", "--scan", "--root") and args:
            value = args.pop(0)
            if arg == "--archive":
                archives.append(value)
            elif arg == "--scan":
                scan_dirs.append(value)
            else:
                root = value
        elif arg == "--tracked":
            in_place = True
        elif arg == "--reveal":
            reveal = True
        elif not arg.startswith("--") and dest is None:
            dest = arg
        else:
            dest, scan_dirs, archives, in_place = None, [], [], False
            break
    if dest is None and not scan_dirs and not archives and not in_place:
        sys.stderr.write("usage: sh tools/public-tree.sh [DEST] [--tracked] [--scan DIR] [--archive FILE]... [--reveal]\n       (DEST must not exist)\n")
        return 2

    scan = Scan(ALLOWED)
    said = []
    try:
        if dest is not None:
            if os.path.lexists(dest):
                sys.stderr.write("public-tree: %s is already there: DEST must not exist\n" % dest)
                return 2
            if git(root, "status", "--porcelain").strip():
                sys.stderr.write("public-tree: commit or stash your changes first: the copy is made from tracked files as they are on disk\n")
                return 2
            names = tracked(root)
            for rel in names:
                source = os.path.join(root, *rel.split("/"))
                if not is_plain_file(os.lstat(source)):
                    continue  # reported by the scan below, and not copied
                target = os.path.join(dest, *rel.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(source, target)
                shutil.copymode(source, target)
            scan_files(scan, dest, names)
            for rel in names:
                if not os.path.lexists(os.path.join(dest, *rel.split("/"))) and is_plain_file(os.lstat(os.path.join(root, *rel.split("/")))):
                    scan.error(rel, "was not copied")
            said.append("%d tracked files written to %s" % (len(names), dest))
            runnable = [line.split("\t", 1)[1] for line in git(root, "ls-files", "-s").decode("utf-8").splitlines() if line.startswith("100755")]
            if runnable:
                said.append("git records these as runnable, which a copy made on Windows does not carry: " + ", ".join(runnable))
        if in_place:
            names = tracked(root)
            scan_files(scan, root, names)
            said.append("%d tracked files checked in %s" % (len(names), root))
        for folder in scan_dirs:
            names = walk(folder)
            if not names:
                scan.error(folder, "holds no files")
            scan_files(scan, folder, names)
            said.append("%d files checked under %s" % (len(names), folder))
        for archive in archives:
            name = os.path.basename(archive)
            with open(archive, "rb") as f:
                data = f.read()
            before = scan.files
            scan.files += 1
            # The archive's own name is held to the rules for names, as an entry's is.
            scan.path(name, name, built=False)
            scan.text("%s [its bytes]" % name, data.decode("latin-1"), first_line=0)
            scan.archive(name, data, 1)
            said.append("%d entries checked in %s" % (scan.files - before - 1, archive))
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError) as error:
        scan.error("(the check itself)", str(error))
    if scan.files == 0 and not scan.errors:
        scan.error("(the check itself)", "no file was checked")

    # A value a pattern matched is masked wherever it is printed. A file reported for the
    # kind of name it has (a built file, a private file's name) is still named: where such
    # a name holds something private, a pattern has matched that part as well.
    values = [value for _, _, rule, value in scan.findings if rule not in ("private-file-name", "built-file", "link")]
    place = (lambda where: where) if reveal else (lambda where: mask_where(where, values))
    for where, line, rule, value in scan.findings:
        print("FOUND  %s:%s  %s  %s" % (place(where), line if line else "-", rule, value if reveal else mask(value)))
    for where, what in scan.errors:
        print("ERROR  %s  scanner-error  %s" % (place(where), place(what)))
    for line in said:
        print(place(line))
    if scan.errors:
        print("public-tree: THE CHECK DID NOT FINISH (%d error%s). That is a failure, not a clean result." % (len(scan.errors), "" if len(scan.errors) == 1 else "s"))
        return 2
    if scan.findings:
        print("public-tree: %d finding%s. NOT fit to publish. Fix the source and run this again%s." % (len(scan.findings), "" if len(scan.findings) == 1 else "s", "" if reveal else " (--reveal prints each value and each place in full)"))
        return 1
    print("public-tree: %d files checked, %d known fixtures let through, nothing else found by these checks." % (scan.files, scan.let_through))
    print("They look for what a project like this can hold by accident. They are not proof: read the copy before publishing.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
