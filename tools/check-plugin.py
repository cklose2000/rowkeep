#!/usr/bin/env python3
"""python tools/check-plugin.py [--root DIR]

Checks the plugin folder as it is on disk (plugin/, or DIR), and fails on anything a plugin
directory would turn away or that should not be handed out:

  - a file over 256 KiB (each file's size is printed against the limit, and one within a
    tenth of the limit gets a warning, which does not fail the check);
  - a file that is not one of the kinds the plugin ships: .md, .json, .py, .sh, .html, a
    file named LICENSE, and the one picture, .claude-plugin/icon.png;
  - a text file that is not UTF-8, or holds a NUL byte;
  - an icon that is not a PNG, is not square, is under 512 or over 2048 pixels a side, is
    2 MB or more, holds a chunk other than the picture itself (text, a timestamp, camera
    data, a colour profile), or has bytes after its end;
  - a symbolic link, a junction, or anything else that is not a plain file or folder;
  - two names that differ only in capitals;
  - no icon at all;
  - a manifest, .claude-plugin/plugin.json, that is missing, is not valid JSON, or does not
    hold what the plugin ships with: the name `rowkeep`, a version of three numbers, a
    description, a licence, an author, and the five links a directory's listing shows
    (homepage, repository, documentation, support, privacy policy), each an https link.

Standard library only. Exit 0: fine. Exit 1: problems, each on its own line. Exit 2: the
folder could not be read.
"""
import json
import os
import re
import stat
import struct
import sys
import zlib

LIMIT = 256 * 1024
NEAR = LIMIT - LIMIT // 10  # within a tenth of the limit
TEXT_KINDS = (".md", ".json", ".py", ".sh", ".html")
TEXT_NAMES = ("LICENSE",)
ICON = ".claude-plugin/icon.png"
ICON_LIMIT = 2 * 1024 * 1024
ICON_SIDES = (512, 2048)
# The chunks a picture needs, and those that only say how to show it. Everything else
# (tEXt, zTXt, iTXt, tIME, eXIf, iCCP ...) can carry words or data about where the
# picture came from, and is refused.
PNG_CHUNKS = (b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"bKGD", b"pHYs")


MANIFEST = ".claude-plugin/plugin.json"
LINKS = ("homepage", "repository", "documentationUrl", "supportUrl", "privacyPolicyUrl")


def manifest_problems(root):
    """What is wrong with the plugin's manifest, read as JSON and not as text: a file that
    only looks like one, or has lost a field a listing needs, would otherwise ship."""
    try:
        with open(os.path.join(root, *MANIFEST.split("/")), "rb") as f:
            data = f.read()
    except OSError:
        return ["%s is missing: the plugin ships its manifest" % MANIFEST]
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        return ["%s is not valid JSON: %s" % (MANIFEST, error)]
    if not isinstance(manifest, dict):
        return ["%s is not an object of fields" % MANIFEST]

    def text(key, of=manifest):
        value = of.get(key) if isinstance(of, dict) else None
        return value if isinstance(value, str) and value.strip() else None

    problems = []
    if manifest.get("name") != "rowkeep":
        problems.append('"name" must be "rowkeep"')
    if text("version") is None or re.fullmatch(r"\d+\.\d+\.\d+", text("version")) is None:
        problems.append('"version" must be three numbers, such as 0.7.1')
    for key in ("description", "license"):
        if text(key) is None:
            problems.append('"%s" is missing or empty' % key)
    if text("name", manifest.get("author")) is None:
        problems.append('"author" must name an author')
    for key in LINKS:
        if text(key) is None or not text(key).startswith("https://"):
            problems.append('"%s" must be an https link' % key)
    return ["%s: %s" % (MANIFEST, problem) for problem in problems]


def is_plain(path, info):
    """A plain file or folder: not a symbolic link, and on Windows not a junction or any
    other reparse point, which `islink` does not report."""
    if stat.S_ISLNK(info.st_mode):
        return False
    if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return False
    return stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)


def icon_problems(data):
    problems = []
    if len(data) >= ICON_LIMIT:
        problems.append("is %d bytes; an icon must be under 2 MB" % len(data))
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return problems + ["is not a PNG: its first eight bytes are not a PNG's"]
    at, kinds, ended = 8, [], False
    while at < len(data):
        if ended:
            problems.append("has %d bytes after the picture's end" % (len(data) - at))
            break
        if at + 12 > len(data):
            problems.append("is cut short inside a chunk")
            break
        length, kind = struct.unpack(">I4s", data[at : at + 8])
        body = data[at + 8 : at + 8 + length]
        checksum = data[at + 8 + length : at + 12 + length]
        if len(body) != length or len(checksum) != 4:
            problems.append("is cut short inside a chunk")
            break
        if struct.unpack(">I", checksum)[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            problems.append("has a %s chunk whose checksum is wrong" % kind.decode("latin-1"))
        if kind not in PNG_CHUNKS:
            problems.append("holds a %s chunk: an icon may hold the picture and nothing else (no text, dates or data about its making)" % kind.decode("latin-1"))
        if not kinds and kind != b"IHDR":
            problems.append("does not start with its header")
        if kind == b"IHDR" and length == 13 and not kinds:
            width, height = struct.unpack(">II", body[:8])
            if width != height:
                problems.append("is %d by %d pixels; an icon must be square" % (width, height))
            elif not ICON_SIDES[0] <= width <= ICON_SIDES[1]:
                problems.append("is %d pixels a side; an icon must be between %d and %d" % (width, ICON_SIDES[0], ICON_SIDES[1]))
        kinds.append(kind)
        ended = kind == b"IEND"
        at += 12 + length
    if not ended:
        problems.append("has no end chunk")
    if b"IDAT" not in kinds:
        problems.append("holds no picture")
    return problems


def check(root):
    """(lines to print, problems) for the plugin folder at `root`."""
    lines, problems, seen = [], [], {}
    found_icon = False

    def walk(folder, rel):
        nonlocal found_icon
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            name_rel = rel + name
            folded = name_rel.lower()
            if folded in seen:
                problems.append("%s and %s differ only in capitals" % (seen[folded], name_rel))
            seen[folded] = name_rel
            info = os.lstat(path)
            if not is_plain(path, info):
                problems.append("%s is a link or a special file: the plugin holds plain files only" % name_rel)
                continue
            if stat.S_ISDIR(info.st_mode):
                walk(path, name_rel + "/")
                continue
            with open(path, "rb") as f:
                data = f.read()
            size = len(data)
            note = ""
            if name_rel == ICON:
                found_icon = True
                for problem in icon_problems(data):
                    problems.append("%s %s" % (name_rel, problem))
            elif name in TEXT_NAMES or name.lower().endswith(TEXT_KINDS):
                if b"\x00" in data:
                    problems.append("%s holds a NUL byte: it is not text" % name_rel)
                else:
                    try:
                        data.decode("utf-8")
                    except UnicodeDecodeError:
                        problems.append("%s is not UTF-8 text" % name_rel)
            else:
                problems.append("%s is not a kind of file the plugin ships (%s, %s, and %s)" % (name_rel, ", ".join(TEXT_KINDS), ", ".join(TEXT_NAMES), ICON))
            if size > LIMIT:
                problems.append("%s is %d bytes, over the limit of %d (256 KiB) for one file" % (name_rel, size, LIMIT))
                note = "   OVER THE LIMIT"
            elif size > NEAR:
                note = "   WARNING: within a tenth of the limit (%d bytes left)" % (LIMIT - size)
            lines.append("%8d of %d  %3d%%  %s%s" % (size, LIMIT, size * 100 // LIMIT, name_rel, note))

    walk(root, "")
    if not lines:
        problems.append("the folder holds no files")
    else:
        if not found_icon:
            problems.append("%s is missing: the plugin ships one icon" % ICON)
        problems.extend(manifest_problems(root))
    return lines, problems, found_icon


def main(argv):
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin")
    if argv[:1] == ["--root"] and len(argv) == 2:
        root = argv[1]
    elif argv:
        sys.stderr.write("usage: python tools/check-plugin.py [--root DIR]\n")
        return 2
    try:
        lines, problems, _ = check(root)
    except OSError as error:
        sys.stderr.write("check-plugin: could not read the folder: %s\n" % error)
        return 2
    for line in lines:
        print(line)
    warned = sum(1 for line in lines if "WARNING" in line)
    if problems:
        for problem in problems:
            print("PROBLEM: " + problem)
        print("check-plugin: %d problem%s in %s" % (len(problems), "" if len(problems) == 1 else "s", root))
        return 1
    print("check-plugin: %d files, none over %d bytes, all of the kinds the plugin ships%s" % (len(lines), LIMIT, "; %d near the limit" % warned if warned else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
