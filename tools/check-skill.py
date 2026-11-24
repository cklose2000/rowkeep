#!/usr/bin/env python3
"""python tools/check-skill.py --skill DIR [--archive FILE] [--head require|warn|skip]

Checks the skill folder, and with --archive the `.skill` file built from it.

The folder: SKILL.md begins with a block naming the skill `rowkeep` and describing it in at
most 1024 characters, which is what claude.ai accepts when a skill is uploaded.

The archive, which is the public one and names no log:
  - it is a zip of exactly the files listed below, in that order, inside one folder,
    `rowkeep/`, with nothing before, between or after them and no comment;
  - every entry carries the one fixed date, the expected permissions and no extra field;
  - every entry's bytes are the file's bytes in the skill folder, and (--head) in the last
    commit: `require` fails on a difference, `warn` (the default) says so and goes on;
  - SKILL.md still holds the three words that stand where an account's links go, and no
    entry holds a link to a particular artifact.

Standard library only. Exit 0: fine. Exit 1: problems. Exit 2: it could not be checked.
"""
import os
import re
import struct
import subprocess
import sys
import zlib

# What the public skill ships. The script that writes the archive has its own list
# (SKILL_FILES in rowkeep.py); this one is written out again on purpose, so that a file
# added to one and not the other stops the build.
SKILL_FILES = [
    "LICENSE", "SKILL.md", "ops/BRIEF.md", "ops/SEALER.md", "ops/VAULT.md", "registry/verbs.json", "scripts/rowkeep.py",
    "scripts/rowkeep.sh", "setup/SETUP.md", "setup/capabilities.json", "setup/log-page.html", "setup/vault-page.html",
]
RUNNABLE = ("scripts/rowkeep.py", "scripts/rowkeep.sh")
PLACEHOLDERS = ["ROWKEEP_LOG_LINK", "ROWKEEP_LOG_SLUG", "ROWKEEP_VAULT_LINK"]
DESCRIPTION_LIMIT = 1024
FIXED_TIME, FIXED_DATE = 0, 0x21  # 1980-01-01 00:00:00
ARTIFACT_LINK = re.compile(r"claude\.ai/(?:[a-z]+/)?artifacts?/([A-Za-z0-9_-]+)", re.I)
# The one made-up link the skill's text uses to show what a link looks like.
EXAMPLE_LINKS = {("SKILL.md", "abc123XYZ")}


class Unreadable(Exception):
    pass


def front_matter_problems(text):
    front = re.match(r"---\n(.*?)\n---\n", text, re.S)
    name = re.search(r"^name:[ \t]*(\S+)[ \t]*$", front.group(1), re.M) if front else None
    described = re.search(r"^description:[ \t]*(.*)$", front.group(1), re.M) if front else None
    if name is None or described is None or described.group(1).strip() == "":
        return ["SKILL.md must begin with a block between --- lines that gives a name and a description"]
    problems = []
    if name.group(1) != "rowkeep":
        problems.append("SKILL.md names the skill %r, not rowkeep" % name.group(1))
    value = described.group(1).strip()
    if not (len(value) >= 2 and value[0] == '"' and value[-1] == '"') and (": " in value or " #" in value or value[:1] in "'\"[]{}>|*&!%@`"):
        problems.append("SKILL.md's description holds \": \" or \" #\" or starts with a mark YAML reads, so it must be in double quotes: a strict YAML reader refuses it as it is")
    length = len(unquoted(value))
    if length > DESCRIPTION_LIMIT:
        problems.append("SKILL.md's description is %d characters; claude.ai accepts at most %d" % (length, DESCRIPTION_LIMIT))
    return problems


def unquoted(value):
    """A description as a reader of YAML takes it: without the double quotes round it."""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def description_length(text):
    described = re.search(r"^description:[ \t]*(.*)$", text, re.M)
    return len(unquoted(described.group(1).strip())) if described else 0


def read_archive(data):
    """The entries of a zip, read strictly: [(name, bytes, mode, time, date)], and the
    problems found on the way. Every byte of the file has to be accounted for."""
    problems, entries, at = [], [], 0
    while data[at : at + 4] == b"PK\x03\x04":
        if at + 30 > len(data):
            raise Unreadable("the archive is cut short inside an entry's header")
        version, flags, method, time, date, crc, packed, size, name_length, extra_length = struct.unpack("<HHHHHIIIHH", data[at + 4 : at + 30])
        name = data[at + 30 : at + 30 + name_length].decode("utf-8", "replace")
        start = at + 30 + name_length + extra_length
        if flags != 0x0800:
            problems.append("%s: its flags are %#06x, not the plain UTF-8 name flag alone" % (name, flags))
        if flags & 0x0008:
            raise Unreadable("%s: its sizes are written after its data, which this check does not read" % name)
        if extra_length:
            problems.append("%s: carries an extra field of %d bytes" % (name, extra_length))
        if method != 8:
            raise Unreadable("%s: is stored with method %d, not deflate" % (name, method))
        inflate = zlib.decompressobj(-15)
        try:
            body = inflate.decompress(data[start : start + packed]) + inflate.flush()
        except zlib.error as error:
            raise Unreadable("%s: does not unpack: %s" % (name, error))
        if not inflate.eof or inflate.unused_data:
            problems.append("%s: its packed data does not end where the archive says" % name)
        if len(body) != size or zlib.crc32(body) & 0xFFFFFFFF != crc:
            problems.append("%s: its size or checksum is not the one recorded" % name)
        entries.append({"name": name, "data": body, "at": at, "time": time, "date": date, "header": data[at + 4 : at + 30]})
        at = start + packed
    directory_at = at
    for entry in entries:
        if data[at : at + 4] != b"PK\x01\x02":
            raise Unreadable("the archive's list of entries is shorter than its entries, or something else follows them")
        made_by = struct.unpack("<H", data[at + 4 : at + 6])[0]
        name_length, extra_length, comment_length, disk, internal, external, offset = struct.unpack("<HHHHHII", data[at + 28 : at + 46])
        name = data[at + 46 : at + 46 + name_length].decode("utf-8", "replace")
        if data[at + 6 : at + 32] != entry["header"] or name != entry["name"] or offset != entry["at"]:
            problems.append("%s: the archive's list of entries describes it differently from the entry itself" % entry["name"])
        if extra_length or comment_length or disk or internal:
            problems.append("%s: carries an extra field, a comment or other attributes in the archive's list" % name)
        entry["mode"] = external >> 16
        entry["unix"] = made_by >> 8 == 3
        at += 46 + name_length + extra_length + comment_length
    if data[at : at + 4] != b"PK\x05\x06" or len(data) < at + 22:
        raise Unreadable("the archive holds something other than its entries and its list of them")
    disk, first_disk, here, total, directory_size, directory_offset, comment_length = struct.unpack("<HHHHIIH", data[at + 4 : at + 22])
    if (here, total, directory_size, directory_offset) != (len(entries), len(entries), at - directory_at, directory_at) or disk or first_disk:
        problems.append("the archive's closing record does not match its entries")
    if comment_length or len(data) != at + 22:
        problems.append("the archive has a comment, or %d bytes after its end" % (len(data) - at - 22))
    return entries, problems


def at_head(root, path):
    """The file's bytes in the last commit, or None where git does not have it."""
    done = subprocess.run(["git", "-C", root, "show", "HEAD:" + path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return done.stdout if done.returncode == 0 else None


def archive_problems(skill, archive, head):
    problems, notes = [], []
    with open(archive, "rb") as f:
        entries, found = read_archive(f.read())
    problems += found
    names = [entry["name"] for entry in entries]
    expected = ["rowkeep/" + rel for rel in SKILL_FILES]
    if names != expected:
        for name in names:
            if name not in expected:
                problems.append("the archive holds %r, which the skill does not ship" % name)
        for name in expected:
            if name not in names:
                problems.append("the archive lacks %s" % name)
        if sorted(names) == sorted(expected):
            problems.append("the archive's entries are not in the order the skill's files are listed")
        if len(names) != len(set(names)):
            problems.append("the archive holds a name twice")
    root = None
    if head != "skip":
        done = subprocess.run(["git", "-C", skill, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if done.returncode == 0:
            root = done.stdout.decode("utf-8").strip()
        elif head == "require":
            raise Unreadable("%s is not in a git checkout, so the archive cannot be compared with the last commit" % skill)
        else:
            notes.append("not a git checkout: the archive was compared with the folder only")
    uncommitted = []
    for entry in entries:
        name = entry["name"]
        rel = name[len("rowkeep/") :] if name.startswith("rowkeep/") else None
        if (entry["time"], entry["date"]) != (FIXED_TIME, FIXED_DATE):
            problems.append("%s: its date is not the fixed one (1980-01-01 00:00:00)" % name)
        mode = 0o100755 if rel in RUNNABLE else 0o100644
        if entry.get("mode") != mode or not entry.get("unix"):
            problems.append("%s: its permissions are %o, not %o" % (name, entry.get("mode", 0), mode))
        if rel not in SKILL_FILES:
            continue
        path = os.path.join(skill, *rel.split("/"))
        try:
            with open(path, "rb") as f:
                on_disk = f.read()
        except OSError as error:
            raise Unreadable("%s: %s" % (path, error))
        if entry["data"] != on_disk:
            problems.append("%s: its bytes are not those of %s" % (name, path.replace(os.sep, "/")))
        if root is not None:
            tracked = os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")
            committed = at_head(root, tracked)
            if committed is None:
                uncommitted.append("%s is not in the last commit" % tracked)
            elif committed != entry["data"]:
                uncommitted.append("%s differs from the last commit" % tracked)
        try:
            text = entry["data"].decode("utf-8")
        except UnicodeDecodeError:
            problems.append("%s: is not UTF-8 text" % name)
            continue
        for link in ARTIFACT_LINK.finditer(text):
            if (rel, link.group(1)) not in EXAMPLE_LINKS:
                problems.append("%s: holds a link to a particular artifact (line %d)" % (name, text.count("\n", 0, link.start()) + 1))
        if rel == "SKILL.md":
            for word in PLACEHOLDERS:
                if word not in text:
                    problems.append("%s: does not hold %s, the word that stands where an account's link goes. This is a copy made for an account, not the public skill" % (name, word))
    if uncommitted:
        if head == "require":
            problems += ["%s: the public archive is built from committed files only" % line for line in uncommitted]
        else:
            notes.append("NOT A RELEASE BUILD: " + "; ".join(uncommitted) + ". Commit, then build again, before handing this file out")
    return problems, notes, len(entries)


def main(argv):
    options, bad = {}, len(argv) % 2 == 1
    for name, value in zip(argv[0::2], argv[1::2]):
        if name not in ("--skill", "--archive", "--head") or name[2:] in options:
            bad = True
        options[name[2:]] = value
    options.setdefault("head", "warn")
    if bad or "skill" not in options or options["head"] not in ("require", "warn", "skip"):
        sys.stderr.write("usage: python tools/check-skill.py --skill DIR [--archive FILE] [--head require|warn|skip]\n")
        return 2
    skill = options["skill"]
    try:
        with open(os.path.join(skill, "SKILL.md"), "rb") as f:
            text = f.read().decode("utf-8")
        problems = front_matter_problems(text)
        notes, count = [], 0
        if "archive" in options:
            more, notes, count = archive_problems(skill, options["archive"], options["head"])
            problems += more
    except (OSError, UnicodeDecodeError, Unreadable, struct.error) as error:
        sys.stderr.write("check-skill: could not check: %s\n" % error)
        return 2
    for note in notes:
        print("check-skill: " + note)
    if problems:
        for problem in problems:
            print("PROBLEM: " + problem)
        print("check-skill: %d problem%s" % (len(problems), "" if len(problems) == 1 else "s"))
        return 1
    said = "SKILL.md's description is %d of %d characters" % (description_length(text), DESCRIPTION_LIMIT)
    if "archive" in options:
        said += "; %s holds the skill's %d files and nothing else, each as it is in the folder, with fixed dates, the three placeholders and no artifact link" % (options["archive"], count)
    print("check-skill: " + said)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
