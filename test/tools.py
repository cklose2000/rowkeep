"""python test/tools.py

Tests of the checks the build and CI run, in tools/: the check of the built skill
(check-skill.py), the check of the plugin folder (check-plugin.py), the check of what may
be published (public-tree.py), and the refusal of the personal build to run in CI.
Standard library only. Each check is run as the build runs it, as a command.
"""
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
SKILL = os.path.join(ROOT, "plugin", "skills", "rowkeep")
SCRIPT = os.path.join(SKILL, "scripts", "rowkeep.py")

spec = importlib.util.spec_from_file_location("rowkeep", SCRIPT)
rowkeep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rowkeep)


def run(tool, *args, **env):
    """(exit code, everything it printed) of one of the tools."""
    command = [sys.executable, "-B", os.path.join(TOOLS, tool)] + list(args)
    done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUTF8="1", **env))
    return done.returncode, done.stdout.decode("utf-8", "replace")


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rowkeep-tools-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def write(self, rel, data):
        full = self.path(*rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return full


class CheckSkill(Temp):
    """The archive that is handed out holds the skill's files and nothing else."""

    def setUp(self):
        super().setUp()
        # A copy of the skill's files, outside any checkout.
        self.files = rowkeep.skill_files(SKILL)
        for rel, data in self.files:
            self.write("skill/" + rel, data)

    def archive(self, entries=None, data=None):
        if data is None:
            data = rowkeep.zip_of(entries if entries is not None else [("rowkeep/" + rel, body, rel.startswith("scripts/")) for rel, body in self.files])
        return self.write("out/rowkeep.skill", data)

    def check(self, archive, head="skip"):
        return run("check-skill.py", "--skill", self.path("skill"), "--archive", archive, "--head", head)

    def entries(self):
        return [("rowkeep/" + rel, body, rel.startswith("scripts/")) for rel, body in self.files]

    def test_the_archive_the_script_packs_passes(self):
        code, said = self.check(self.archive())
        self.assertEqual(code, 0, said)
        self.assertIn("12 files and nothing else", said)
        self.assertEqual([rel for rel, _ in self.files][0], "LICENSE", "the licence ships in the skill")

    def test_the_real_skill_packs_the_same_bytes_twice_and_passes(self):
        first, second = self.path("a.skill"), self.path("b.skill")
        for out in (first, second):
            done = subprocess.run([sys.executable, "-B", SCRIPT, "pack", "--skill", SKILL, "--out", out], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(done.returncode, 0, done.stderr)
        with open(first, "rb") as a, open(second, "rb") as b:
            self.assertEqual(a.read(), b.read(), "two builds of the same files differ")
        code, said = run("check-skill.py", "--skill", SKILL, "--archive", first, "--head", "warn")
        self.assertEqual(code, 0, said)

    def test_an_entry_the_skill_does_not_ship_fails(self):
        for extra in ("rowkeep/local.json", "rowkeep/scripts/__pycache__/rowkeep.cpython-311.pyc", "rowkeep/SKILL.md~", "local.json"):
            code, said = self.check(self.archive(self.entries() + [(extra, b"{}", False)]))
            self.assertEqual(code, 1, said)
            self.assertIn("which the skill does not ship", said)

    def test_a_missing_or_reordered_entry_fails(self):
        code, said = self.check(self.archive(self.entries()[1:]))
        self.assertEqual(code, 1, said)
        self.assertIn("lacks rowkeep/LICENSE", said)
        code, said = self.check(self.archive(list(reversed(self.entries()))))
        self.assertEqual(code, 1, said)
        self.assertIn("not in the order", said)

    def test_an_entry_that_is_not_the_file_fails(self):
        entries = [(name, body + b"\n# one more line\n" if name.endswith("rowkeep.py") else body, runnable) for name, body, runnable in self.entries()]
        code, said = self.check(self.archive(entries))
        self.assertEqual(code, 1, said)
        self.assertIn("its bytes are not those of", said)

    def test_a_build_time_date_or_other_permissions_fail(self):
        out = self.path("dated.skill")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name, body, _ in self.entries():
                z.writestr(name, body)
        code, said = self.check(out)
        self.assertNotEqual(code, 0, said)
        self.assertTrue("its date is not the fixed one" in said or "could not check" in said, said)
        code, said = self.check(self.archive([(name, body, False) for name, body, _ in self.entries()]))
        self.assertEqual(code, 1, said)
        self.assertIn("its permissions are", said)

    def test_bytes_around_the_entries_fail(self):
        good = rowkeep.zip_of(self.entries())
        code, said = self.check(self.archive(data=good + b"https://claude.ai/"))
        self.assertNotEqual(code, 0, said)
        code, said = self.check(self.archive(data=b"something first" + good))
        self.assertNotEqual(code, 0, said)

    def test_a_copy_made_for_an_account_fails(self):
        link = "https://claude.ai/" + "artifact/" + "Zz9" * 7 + "z"
        mine = []
        for rel, body in self.files:
            if rel == "SKILL.md":
                text = body.decode("utf-8")
                for word in rowkeep.PLACEHOLDERS:
                    text = text.replace(word, link)
                body = text.encode("utf-8")
                self.write("skill/" + rel, body)
            mine.append(("rowkeep/" + rel, body, rel.startswith("scripts/")))
        code, said = self.check(self.archive(mine))
        self.assertEqual(code, 1, said)
        self.assertIn("holds a link to a particular artifact", said)
        for word in rowkeep.PLACEHOLDERS:
            self.assertIn("does not hold " + word, said)

    def test_a_description_over_1024_characters_fails(self):
        text = dict(self.files)["SKILL.md"].decode("utf-8")
        described = [line for line in text.split("\n") if line.startswith("description:")][0]
        self.write("skill/SKILL.md", text.replace(described, "description: " + "x" * 1025))
        code, said = run("check-skill.py", "--skill", self.path("skill"))
        self.assertEqual(code, 1, said)
        self.assertIn("1025 characters", said)
        self.write("skill/SKILL.md", text.replace(described, "description: " + "x" * 1024))
        self.assertEqual(run("check-skill.py", "--skill", self.path("skill"))[0], 0)

    def test_a_file_that_is_not_an_archive_is_an_error_not_a_pass(self):
        code, said = self.check(self.write("out/not.skill", b"not a zip at all"))
        self.assertEqual(code, 2, said)


def png(width=1024, height=1024, extra=(), after=b""):
    """A small valid PNG of one grey, with any extra chunks before its end."""

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    rows = (b"\x00" + b"\x80" * width) * height
    chunks = [chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))] + [chunk(kind, body) for kind, body in extra]
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"") + after


def gif(blocks=(), after=b""):
    """A small valid GIF: one picture of one colour, with extension blocks before it. A
    block is (its label, its pieces), each piece stored behind its length."""
    out = b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x80, 0, 0) + b"\x00\x00\x00\xff\xff\xff"
    for label, pieces in blocks:
        out += bytes([0x21, label]) + b"".join(bytes([len(piece)]) + piece for piece in pieces) + b"\x00"
    out += b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00"
    return out + b"\x3b" + after


class CheckPlugin(Temp):
    """What a plugin folder may hold: text of known kinds, one icon, nothing large."""

    def setUp(self):
        super().setUp()
        for rel in ("README.md", "LICENSE", "skills/rowkeep/SKILL.md", "skills/rowkeep/scripts/rowkeep.py", "skills/rowkeep/scripts/rowkeep.sh", "skills/rowkeep/setup/log-page.html"):
            self.write("plugin/" + rel, "text\n")
        self.manifest()
        self.write("plugin/.claude-plugin/icon.png", png())

    MANIFEST = {
        "name": "rowkeep", "version": "0.7.1", "description": "A small database.", "license": "Apache-2.0",
        "author": {"name": "Rowkeep authors"}, "homepage": "https://example.com/", "repository": "https://example.com/r",
        "documentationUrl": "https://example.com/d", "supportUrl": "https://example.com/s", "privacyPolicyUrl": "https://example.com/p",
    }

    def manifest(self, text=None, **changed):
        """Write the stand-in plugin's manifest: as given, or the good one with fields
        changed (a value of None leaves the field out)."""
        if text is None:
            fields = dict(self.MANIFEST, **changed)
            text = json.dumps({k: v for k, v in fields.items() if v is not None})
        return self.write("plugin/.claude-plugin/plugin.json", text)

    def check(self):
        return run("check-plugin.py", "--root", self.path("plugin"))

    def fails_with(self, words):
        code, said = self.check()
        self.assertEqual(code, 1, said)
        self.assertIn(words, said)

    def test_a_folder_of_the_kinds_the_plugin_ships_passes(self):
        code, said = self.check()
        self.assertEqual(code, 0, said)
        self.assertIn("8 files", said)

    def test_the_real_plugin_folder_passes_and_shows_every_size(self):
        code, said = run("check-plugin.py")
        self.assertEqual(code, 0, said)
        self.assertIn("of 262144", said)
        self.assertIn("skills/rowkeep/scripts/rowkeep.py", said)

    def test_the_manifest_is_read_as_json_and_must_hold_what_the_plugin_ships_with(self):
        # The manifest was checked as text of a known kind and nothing more: a file that
        # was not JSON, or had lost its name or a listing link, passed.
        for text, words in (
            ("text\n", ".claude-plugin/plugin.json is not valid JSON"),
            ('{"name": "rowkeep",}', "is not valid JSON"),
            ("[]", "is not an object of fields"),
            (b"\xff\xfe{}", "is not valid JSON"),
        ):
            self.manifest(text)
            self.fails_with(words)
        for changed, words in (
            ({"name": "rowkeep-fork"}, '"name" must be "rowkeep"'),
            ({"name": None}, '"name" must be "rowkeep"'),
            ({"version": "0.7"}, '"version" must be three numbers, such as 0.7.1'),
            ({"version": 1}, '"version" must be three numbers'),
            ({"description": "  "}, '"description" is missing or empty'),
            ({"license": None}, '"license" is missing or empty'),
            ({"author": "someone"}, '"author" must name an author'),
            ({"supportUrl": None}, '"supportUrl" must be an https link'),
            ({"privacyPolicyUrl": "http://example.com/p"}, '"privacyPolicyUrl" must be an https link'),
            ({"homepage": 7}, '"homepage" must be an https link'),
        ):
            self.manifest(**changed)
            self.fails_with(".claude-plugin/plugin.json: " + words)
        os.remove(self.manifest())
        self.fails_with(".claude-plugin/plugin.json is missing: the plugin ships its manifest")
        self.manifest()
        self.assertEqual(self.check()[0], 0)

    def test_a_plugin_with_no_icon_fails(self):
        os.remove(self.path("plugin", ".claude-plugin", "icon.png"))
        self.fails_with(".claude-plugin/icon.png is missing: the plugin ships one icon")

    def test_the_real_manifest_names_this_version(self):
        with open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            self.assertEqual((json.load(f)["version"], run("check-plugin.py")[0]), (rowkeep.VERSION, 0))

    def test_a_file_over_256_kib_fails(self):
        self.write("plugin/skills/rowkeep/ops/BIG.md", b"a" * (257 * 1024))
        self.fails_with("over the limit of 262144")

    def test_a_file_at_the_limit_passes_with_a_warning(self):
        self.write("plugin/skills/rowkeep/ops/BIG.md", b"a" * (256 * 1024))
        code, said = self.check()
        self.assertEqual(code, 0, said)
        self.assertIn("WARNING: within a tenth of the limit (0 bytes left)", said)
        self.write("plugin/skills/rowkeep/ops/BIG.md", b"a" * (256 * 1024 * 9 // 10))
        code, said = self.check()
        self.assertEqual(code, 0, said)
        self.assertNotIn("WARNING", said)

    def test_a_kind_of_file_the_plugin_does_not_ship_fails(self):
        for rel in ("skills/rowkeep/scripts/__pycache__/rowkeep.cpython-311.pyc", "tool.exe", "lib.so", "rowkeep.skill", "notes", "skills/rowkeep/picture.png", "icon.png"):
            full = self.write("plugin/" + rel, png() if rel.endswith(".png") else b"x")
            self.fails_with("is not a kind of file the plugin ships")
            os.remove(full)
        self.assertEqual(self.check()[0], 0)

    def test_text_that_is_not_text_fails(self):
        self.write("plugin/skills/rowkeep/ops/A.md", b"caf\xe9\n")
        self.fails_with("is not UTF-8 text")
        self.write("plugin/skills/rowkeep/ops/A.md", b"a\x00b\n")
        self.fails_with("holds a NUL byte")

    def test_an_icon_that_is_more_than_a_picture_fails(self):
        icon = "plugin/.claude-plugin/icon.png"
        for data, words in [
            (png(extra=[(b"tEXt", b"Author\x00someone")]), "holds a tEXt chunk"),
            (png(extra=[(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x/>")]), "holds a iTXt chunk"),
            (png(extra=[(b"tIME", b"\x07\xea\x0b\x17\x00\x00\x00")]), "holds a tIME chunk"),
            (png(extra=[(b"eXIf", b"MM\x00*")]), "holds a eXIf chunk"),
            (png(256, 256), "is 256 pixels a side"),
            (png(4096, 4096), "is 4096 pixels a side"),
            (png(1024, 512), "must be square"),
            (png(after=b"more"), "bytes after the picture's end"),
            (b"GIF89a" + b"\x00" * 100, "is not a PNG"),
            (png()[:-20], "is cut short"),
            (png(extra=[(b"prVt", b"\x01" * (2 * 1024 * 1024))]), "must be under 2 MB"),
        ]:
            self.write(icon, data)
            self.fails_with(words)

    def test_a_link_fails(self):
        target = self.write("elsewhere/secret.md", "not the plugin's\n")
        link = self.path("plugin", "skills", "rowkeep", "LINK.md")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("this account cannot make symbolic links here")
        self.fails_with("is a link or a special file")

    def test_a_junction_fails(self):
        if os.name != "nt":
            self.skipTest("junctions are a Windows thing")
        os.makedirs(self.path("elsewhere"))
        made = subprocess.run(["cmd", "/c", "mklink", "/J", self.path("plugin", "skills", "joined"), self.path("elsewhere")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if made.returncode != 0:
            self.skipTest("could not make a junction")
        self.addCleanup(os.rmdir, self.path("plugin", "skills", "joined"))
        self.fails_with("is a link or a special file")

    def test_names_that_differ_only_in_capitals_fail(self):
        self.write("plugin/skills/rowkeep/ops/Notes.md", "a\n")
        self.write("plugin/skills/rowkeep/ops/notes.md", "b\n")
        if len(os.listdir(self.path("plugin", "skills", "rowkeep", "ops"))) < 2:
            self.skipTest("this file system does not tell capitals apart")
        self.fails_with("differ only in capitals")

    def test_a_folder_that_is_not_there_is_an_error_not_a_pass(self):
        code, said = run("check-plugin.py", "--root", self.path("nothing-here"))
        self.assertEqual(code, 2, said)


def found(said):
    """The findings a run of public-tree.py printed: [(file, line, pattern's name, value)]."""
    out = []
    for line in said.split("\n"):
        if line.startswith("FOUND  "):
            where, rule, value = line[len("FOUND  ") :].split("  ", 2)
            file, _, number = where.rpartition(":")
            out.append((file, number, rule, value))
    return out


# One example of each thing that must never be published. Every value is put together
# here from parts, so that this file holds none of them whole and passes the check itself.
ID22 = "Zk3" * 7 + "q"
UUID = "-".join(["0A1B2C3D", "4E5F", "6A7B", "8C9D", "0E1F2A3B4C5D"])
SEEDS = [
    ("artifact-link", "https://claude.ai/" + "artifact/" + ID22),
    ("artifact-link", "https://CLAUDE.AI/" + "Artifacts/" + ID22),
    ("artifact-link", "see /" + "artifacts/" + ID22 + " for it"),
    ("uuid", UUID),
    ("uuid", UUID.lower()),
    ("user-id", "u_" + ID22),
    ("service-id", "trig_" + "01AbCdEfGhIjKlMn"),
    ("service-id", "env_" + "01AbCdEfGhIjKlMn"),
    ("service-id", "cse_" + "01AbCdEfGhIjKlMn"),
    ("service-id", "session_" + "01AbCdEfGhIjKlMn"),
    ("service-id", "ROUTINE_" + "01AbCdEfGhIjKlMn"),
    ("email", "someone" + "@" + "gmail.com"),
    ("email", "first.last" + "@" + "mail.some-company.co.uk"),
    ("home-path", "C:" + "\\Users\\" + "somebody\\notes"),
    ("home-path", "c:" + "/users/" + "somebody"),
    ("home-path", "/" + "Users/somebody/notes"),
    ("home-path", "/" + "home/somebody/.ssh"),
    ("unc-path", "\\\\" + "fileserver" + "\\" + "share\\x"),
    ("private-ip", "10." + "0.0.12"),
    ("private-ip", "172." + "16.4.1"),
    ("private-ip", "192." + "168.1.20"),
    ("localhost-url", "http://local" + "host:8787/mcp"),
    ("localhost-url", "http://127." + "0.0.1:3000"),
    ("credential-assignment", "api_key" + "=abcdef0123456789abcd"),
    ("credential-assignment", "token" + ": abc123def456ghi789"),
    ("credential-assignment", "SECRET" + "=s3cr3tValue12345"),
    ("credential-assignment", "password" + ": hunter2"),
    ("token", "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"),
    ("token", "sk-" + "ant-api03-abcdefghijklmnop0123456789"),
    ("token", "xoxb-" + "123456789012-abcdefghijklmnop"),
    ("cloud-key", "AKIA" + "IOSFODNN7EXAMPLE"),
    ("authorization-header", "Authorization" + ": Basic dXNlcjpwYXNzd29yZA=="),
    ("bearer-token", "Bearer " + "abcdefghij0123456789xyz"),
    ("signed-web-token", "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"),
    ("private-key", "-----BEGIN " + "RSA PRIVATE KEY-----"),
    ("password-in-link", "postgres://admin" + ":hunter2" + "@" + "db.internal/app"),
    ("former-name", "the " + "pack" + "log" + " days"),
    ("former-name", "Row" + "stash"),
    ("former-name", "PACK" + "LOG_HOME"),
]
PRIVATE_FILES = [".env", ".env.local", "server.pem", "site.key", "id_rsa", "id_rsa.pub", "id_ed25519", "local.json"]
# Things that look a little like the above and are not.
HARMLESS = [
    "noreply" + "@" + "anthropic.com", "12345+someone" + "@" + "users.noreply.github.com", "someone" + "@" + "example.com", "a" + "@" + "mail.example.org",
    "actions/checkout" + "@" + "v4.2.2", "menu_" + "abcdefghijklmnopqrstuvwx", "8.8.8.8", "version 10.2", "1.2.3.4",
    "tokens_used: 5", "the secret is out", "password: reset", "localhost", "session_" + "started", "environment",
    "a\\\\u202e\\\\u2066b", "claude.ai/artifact/ and then nothing", "unpack" + "logs", "rowstashed",
]


class PublicTree(Temp):
    """What may be published: nothing private in a file, a path, an archive or a picture."""

    def scan(self, *more):
        return run("public-tree.py", "--scan", self.path("tree"), *more)

    def test_one_of_each_thing_is_caught_by_name_and_masked(self):
        for i, (rule, value) in enumerate(SEEDS):
            self.write("tree/notes/%02d.md" % i, "first line\nsome words " + value + " and more\n")
        code, said = self.scan()
        self.assertEqual(code, 1, said)
        by_file = {}
        for file, line, rule, value in found(said):
            by_file.setdefault(file, []).append((line, rule, value))
        for i, (rule, value) in enumerate(SEEDS):
            got = by_file.get("notes/%02d.md" % i, [])
            self.assertIn(rule, [r for _, r, _ in got], "missed %s number %d; it said %r" % (rule, i, got))
            self.assertTrue(all(line == "2" for line, _, _ in got), got)
        for rule, value in SEEDS:
            if len(value) > 12:
                self.assertNotIn(value, said, "a value was printed whole")
        code, said = self.scan("--reveal")
        self.assertEqual(code, 1)
        self.assertIn("u_" + ID22, said)

    def test_private_file_names_are_caught_whatever_they_hold(self):
        for name in PRIVATE_FILES:
            self.write("tree/config/" + name, "nothing in particular\n")
        self.write("tree/out/rowkeep.skill", rowkeep.zip_of([("rowkeep/SKILL.md", b"plain words\n", False)]))
        code, said = self.scan("--reveal")
        self.assertEqual(code, 1, said)
        got = found(said)
        for name in PRIVATE_FILES:
            self.assertIn(("config/" + name, "-", "private-file-name"), [g[:3] for g in got], said)
        self.assertIn(("out/rowkeep.skill", "-", "built-file"), [g[:3] for g in got], said)

    def test_a_path_is_checked_as_well_as_contents(self):
        self.write("tree/people/u_" + ID22 + "/notes.md", "nothing\n")
        self.write("tree/" + "pack" + "log" + "/README.md", "nothing\n")
        code, said = self.scan()
        self.assertEqual(code, 1, said)
        self.assertEqual(sorted(g[2] for g in found(said)), ["former-name", "user-id"], said)

    def test_a_place_is_masked_as_well_as_a_value(self):
        # A private detail can be in a file's or an entry's name. The value was masked and
        # the place beside it was printed whole, so the log of the check published it.
        user, address = "u_" + ID22, "dana.private" + "@" + "gmail.com"
        self.write("tree/people/" + user + "/notes.md", "nothing\n")
        self.write("tree/mail/" + address + ".md", "a token: " + "ghp_" + "Zk3Zk3Zk3Zk3Zk3Zk3Zk3Zk3Zk3Zk3Zk3Zk3" + "\n")
        inner = rowkeep.zip_of([("rowkeep/" + user + ".md", b"x\n", False)])
        self.write("tree/built/copy.zip", inner)
        code, said = self.scan()
        self.assertEqual(code, 1, said)
        self.assertNotIn(user, said, "the user id in a path was printed whole")
        self.assertNotIn(address, said, "the address in a file's name was printed whole")
        # Every line still says which file, with the value's middle hidden: also the line
        # about the token, which is in a file named for someone.
        places = [g[0] for g in found(said)]
        self.assertTrue(any(p.startswith("people/u_Zk") and p.endswith("/notes.md") and "*" in p for p in places), said)
        self.assertTrue(any(p.startswith("mail/dana") and p.endswith(".md") and "*" in p for p in places if True), said)
        self.assertEqual(sum(1 for g in found(said) if g[2] == "token" and "*" in g[0]), 1, said)
        self.assertTrue(any(p.startswith("built/copy.zip!rowkeep/u_Zk") and "*" in p for p in places), said)
        self.assertIn("--reveal prints each value and each place in full", said)
        code, said = self.scan("--reveal")
        self.assertEqual(code, 1, said)
        self.assertIn("people/" + user + "/notes.md", [g[0] for g in found(said)], said)
        self.assertIn("mail/" + address + ".md", [g[0] for g in found(said)], said)

    def test_the_archive_given_is_held_to_the_rules_for_names(self):
        # --archive checked every entry's name and not the name of the file it was given.
        good = rowkeep.zip_of([("rowkeep/SKILL.md", b"plain words\n", False)])
        code, said = run("public-tree.py", "--reveal", "--archive", self.write("out/u_" + ID22 + ".skill", good))
        self.assertEqual((code, [g[:3] for g in found(said)]), (1, [("u_" + ID22 + ".skill", "-", "user-id")]), said)
        code, said = run("public-tree.py", "--archive", self.write("out/u_" + ID22 + ".skill", good))
        self.assertTrue(code == 1 and ("u_" + ID22) not in said, said)
        code, said = run("public-tree.py", "--reveal", "--archive", self.write("out/id_rsa.zip", good))
        self.assertEqual((code, [g[:3] for g in found(said)]), (1, [("id_rsa.zip", "-", "private-file-name")]), said)
        # A built skill is what this option is for: being one is not a finding about it.
        code, said = run("public-tree.py", "--archive", self.write("out/rowkeep.skill", good))
        self.assertEqual(code, 0, said)

    def test_what_only_looks_like_a_problem_passes(self):
        self.write("tree/notes.md", "\n".join(HARMLESS) + "\n")
        code, said = self.scan()
        self.assertEqual(code, 0, said)
        self.assertIn("nothing else found", said)

    def test_a_known_fixture_is_allowed_as_that_value_in_that_file_only(self):
        user, link = "u_" + "abcdefghijklmnopqrstuv", "https://claude.ai/" + "artifact/example"
        self.write("tree/test/unit.py", 'USER = "%s"\nLINK = "%s"\n' % (user, link))
        self.write("tree/test/fixtures/v060/rows/1.json", '{"user": "%s"}\n' % user)
        code, said = self.scan()
        self.assertEqual(code, 0, said)
        self.assertIn("3 known fixtures let through", said)
        # The same value in a file that is not named is a finding.
        self.write("tree/src/page.html", user + "\n")
        code, said = self.scan()
        self.assertEqual((code, [g[:3] for g in found(said)]), (1, [("src/page.html", "1", "user-id")]), said)
        os.remove(self.path("tree", "src", "page.html"))
        # A second thing on the line of an allowed one is still found, and so is a value
        # that only starts like the allowed one.
        self.write("tree/test/unit.py", 'USER = "%s"  # ask %s\nLINK = "%sXYZ"\n' % (user, "someone" + "@" + "gmail.com", link))
        code, said = self.scan()
        self.assertEqual((code, sorted(g[:3] for g in found(said))), (1, [("test/unit.py", "1", "email"), ("test/unit.py", "2", "artifact-link")]), said)

    def test_a_former_name_is_allowed_on_its_one_line_only(self):
        with open(os.path.join(ROOT, "docs", "ACTIVITY-STREAM.md"), encoding="utf-8") as f:
            history = [line for line in f.read().split("\n") if ("pack" + "log") in line.lower()]
        self.assertEqual(len(history), 1, "the format's description names the former name on one line")
        self.write("tree/docs/ACTIVITY-STREAM.md", "# The format\n\n" + history[0] + "\n")
        self.assertEqual(self.scan()[0], 0)
        self.write("tree/docs/ACTIVITY-STREAM.md", "# The format\n\n" + history[0] + "\n\nAnother line about " + "Pack" + "log" + ".\n")
        code, said = self.scan()
        self.assertEqual((code, [g[:3] for g in found(said)]), (1, [("docs/ACTIVITY-STREAM.md", "5", "former-name")]), said)
        self.write("tree/README.md", history[0] + "\n")
        self.assertEqual(self.scan()[0], 1, "the same line in another file is a finding")

    def archive_with(self, name, body):
        return self.write("out/x.skill", rowkeep.zip_of([("rowkeep/SKILL.md", b"plain words\n", False), (name, body, False)]))

    def test_an_archive_is_checked_entry_by_entry(self):
        secret = ("someone" + "@" + "gmail.com").encode()
        code, said = run("public-tree.py", "--archive", self.archive_with("rowkeep/ops/A.md", b"one\ntwo " + secret + b"\n"))
        self.assertEqual((code, [g[:3] for g in found(said)]), (1, [("x.skill!rowkeep/ops/A.md", "2", "email")]), said)
        # An entry's name (which is also in the archive's own bytes), and a private file's name.
        code, said = run("public-tree.py", "--reveal", "--archive", self.archive_with("rowkeep/u_" + ID22 + ".md", b"x\n"))
        self.assertEqual((code, set(g[2] for g in found(said))), (1, {"user-id"}), said)
        self.assertIn("x.skill!rowkeep/u_" + ID22 + ".md", [g[0] for g in found(said)], said)
        code, said = run("public-tree.py", "--archive", self.archive_with("rowkeep/local.json", b"{}\n"))
        self.assertEqual((code, [g[2] for g in found(said)]), (1, ["private-file-name"]), said)
        # An archive inside the archive.
        inner = rowkeep.zip_of([("deep/notes.md", secret, False)])
        code, said = run("public-tree.py", "--archive", self.archive_with("rowkeep/more.zip", inner))
        self.assertEqual(code, 1, said)
        self.assertIn(("x.skill!rowkeep/more.zip!deep/notes.md", "1", "email"), [g[:3] for g in found(said)], said)
        # Something that is in the file and in no entry: after the end, and in a comment.
        good = rowkeep.zip_of([("rowkeep/SKILL.md", b"plain words\n", False)])
        code, said = run("public-tree.py", "--archive", self.write("out/y.skill", good + b" " + secret))
        self.assertEqual(code, 1, said)
        self.assertIn("y.skill [its bytes]", said)
        # A clean one passes.
        code, said = run("public-tree.py", "--archive", self.write("out/z.skill", good))
        self.assertEqual(code, 0, said)

    def test_a_pictures_text_is_checked(self):
        address = ("someone" + "@" + "gmail.com").encode()
        self.write("tree/a.png", png(512, 512, extra=[(b"tEXt", b"Author\x00" + address)]))
        self.write("tree/b.png", png(512, 512, extra=[(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"made by " + address))]))
        self.write("tree/c.png", png(512, 512, extra=[(b"iTXt", b"XML:com.adobe.xmp\x00\x01\x00\x00\x00" + zlib.compress(b"<creator>" + address + b"</creator>"))]))
        self.write("tree/d.png", png(512, 512))
        code, said = self.scan()
        self.assertEqual(code, 1, said)
        self.assertEqual(sorted(set(g[0].split(" ")[0] for g in found(said) if g[2] == "email")), ["a.png", "b.png", "c.png"], said)

    def test_a_gifs_text_is_checked(self):
        # A GIF was read only as bytes. Its comment and application blocks are stored in
        # pieces, each behind a length byte, so a value that crosses two pieces was missed:
        # each address here is cut inside its domain, and is found only with the pieces joined.
        address = ("someone" + "@" + "gmail.com").encode()
        self.write("tree/a.gif", gif(blocks=[(0xFE, [b"made by someone" + b"@" + b"gma", b"il.com"])]))
        self.write("tree/b.gif", gif(blocks=[(0xFF, [b"XMP DataXMP", b"<creator>someone" + b"@" + b"gma", b"il.com</creator>"])]))
        self.write("tree/c.gif", gif(blocks=[(0x01, [b"\x00" * 12, b"someone" + b"@" + b"gma", b"il.com"])]))
        self.write("tree/d.gif", gif(after=b"\x00" + address))
        self.write("tree/e.gif", gif(blocks=[(0xFF, [b"NETSCAPE2.0", b"\x01\x00\x00"]), (0xF9, [b"\x04\x00\x00\x00"]), (0xFE, [b"Lavf58.76.100"])]))
        code, said = self.scan()
        self.assertEqual(code, 1, said)
        got = sorted(set(g[0] for g in found(said) if g[2] == "email"))
        self.assertEqual(got, ["a.gif [comment block]", "b.gif [application block]", "c.gif [plain text block]", "d.gif", "d.gif [after the picture's end]"], said)
        # One that carries only what a program writes (a loop count, the encoder's name) passes.
        for name in "abcd":
            os.remove(self.path("tree", name + ".gif"))
        code, said = self.scan()
        self.assertEqual(code, 0, said)
        # A GIF that stops inside a block was not read whole, and that is not a pass.
        self.write("tree/f.gif", gif(blocks=[(0xFE, [b"a comment"])])[:-8])
        code, said = self.scan()
        self.assertEqual(code, 2, said)
        self.assertIn("f.gif  scanner-error  a GIF cut short inside a block", said)

    def test_a_check_that_could_not_finish_is_a_failure(self):
        for args in (["--scan", self.path("nothing-here")], ["--archive", self.path("nothing.skill")], ["--archive", self.write("out/bad.skill", b"PK\x03\x04 and then rubbish")], ["--archive", self.write("out/empty.skill", b"")]):
            code, said = run("public-tree.py", *args)
            self.assertEqual(code, 2, said)
            self.assertIn("THE CHECK DID NOT FINISH", said)
            self.assertNotIn("nothing else found", said)
        os.makedirs(self.path("empty"))
        code, said = run("public-tree.py", "--scan", self.path("empty"))
        self.assertEqual(code, 2, said)
        self.assertEqual(run("public-tree.py")[0], 2)
        self.assertEqual(run("public-tree.py", "--no-such-option")[0], 2)

    def git(self, *args):
        done = subprocess.run(["git", "-C", self.path("repo"), "-c", "user.name=Test", "-c", "user.email=test" + "@" + "example.com", "-c", "core.autocrlf=false"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_copy_holds_tracked_files_only_and_is_checked(self):
        if shutil.which("git") is None:
            self.skipTest("no git on the PATH")
        self.write("repo/README.md", "plain words\n")
        self.write("repo/tools/run.sh", "#!/bin/sh\n")
        self.write("repo/.gitignore", "local.json\ndist/\n")
        self.write("repo/local.json", '{"log": "private"}\n')
        self.write("repo/dist/rowkeep.skill", b"built")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        dest = self.path("copy")
        code, said = run("public-tree.py", "--root", self.path("repo"), dest)
        self.assertEqual(code, 0, said)
        self.assertEqual(sorted(walk_names(dest)), [".gitignore", "README.md", "tools/run.sh"])
        # DEST must not exist, and the tree must be clean.
        self.assertEqual(run("public-tree.py", "--root", self.path("repo"), dest)[0], 2)
        self.write("repo/README.md", "plain words, changed\n")
        self.assertEqual(run("public-tree.py", "--root", self.path("repo"), self.path("copy2"))[0], 2)
        # Something private that was committed is found in the copy.
        self.write("repo/README.md", "write to " + "someone" + "@" + "gmail.com\n")
        self.git("commit", "-q", "-am", "second")
        code, said = run("public-tree.py", "--root", self.path("repo"), self.path("copy3"))
        self.assertEqual((code, [g[:3] for g in found(said)]), (1, [("README.md", "1", "email")]), said)
        self.assertIn("NOT fit to publish", said)

    def test_the_real_tree_passes(self):
        if shutil.which("git") is None or not os.path.exists(os.path.join(ROOT, ".git")):
            self.skipTest("not a git checkout")
        code, said = run("public-tree.py", "--tracked")
        self.assertEqual(code, 0, said)

    def test_the_shell_entry_point_gives_the_same_answers(self):
        sh = shutil.which("sh")
        if sh is None:
            self.skipTest("no sh on the PATH")
        self.write("tree/notes.md", "plain words\n")
        done = subprocess.run([sh, os.path.join(TOOLS, "public-tree.sh"), "--scan", self.path("tree")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.write("tree/notes.md", "u_" + ID22 + "\n")
        done = subprocess.run([sh, os.path.join(TOOLS, "public-tree.sh"), "--scan", self.path("tree")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(done.returncode, 1, done.stdout)


def walk_names(base):
    out = []
    for folder, _, names in os.walk(base):
        for name in names:
            out.append(os.path.relpath(os.path.join(folder, name), base).replace(os.sep, "/"))
    return out


class PersonalBuild(unittest.TestCase):
    def test_it_refuses_to_run_in_ci(self):
        sh = shutil.which("sh")
        if sh is None:
            self.skipTest("no sh on the PATH")
        done = subprocess.run([sh, os.path.join(TOOLS, "build-personal.sh")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(os.environ, CI="true"))
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn(b"refused: CI is set", done.stdout)

    def test_the_public_build_reads_nothing_about_an_account(self):
        with open(os.path.join(TOOLS, "build-skill.sh"), encoding="utf-8") as f:
            code = [line for line in f.read().split("\n") if not line.lstrip().startswith("#")]
        self.assertFalse([line for line in code if "local.json" in line or "personalize" in line or "dist/personal" in line])


if __name__ == "__main__":
    unittest.main()
