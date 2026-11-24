#!/bin/sh
# sh tools/build-skill.sh [--release]
#
# Builds what the plugin folder ships that is not written by hand, then packages the skill:
#   plugin/skills/rowkeep/setup/*.html   the two pages, built from src/ (committed, because
#                                        the plugin is installed straight from the repository)
#   dist/rowkeep.skill                   the skill as one file: it names no log; setup makes one
# and checks the archive it made: the listed files and nothing else, each byte for byte the
# file in the folder, fixed dates, no link to anyone's artifact, and the same bytes when it
# is built a second time. With --release, or wherever CI is set, every file in the archive
# must also be exactly as it is in the last commit; otherwise a difference is only reported.
#
# This build reads nothing about any account. A copy of the skill that names the owner's own
# log is made by a separate script, tools/build-personal.sh, into another folder under
# another name.
# Run from anywhere. Needs node, and Python 3.8 or later. Nothing is compiled.
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
skill=plugin/skills/rowkeep
head=warn
if [ "$1" = "--release" ] || [ -n "$CI" ]; then head=require; fi

# The first python3 or python that runs and is 3.8 or later, which is what the launcher
# (scripts/rowkeep.sh) takes too. Each candidate is run, not just looked for: on Windows a
# `python3` can be on the PATH that only says where to install Python.
py=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then py=$c; break; fi
done
if [ -z "$py" ]; then echo "build-skill: needs Python 3.8 or later, as python3 or python" >&2; exit 3; fi
echo "build-skill: using $(command -v "$py") ($("$py" -c 'import sys; print("Python " + sys.version.split()[0])'))"

cd "$root"
mkdir -p dist "$skill/setup"
node build.mjs >/dev/null
cp dist/rowkeep.html "$skill/setup/log-page.html"
cp dist/rowkeep-vault.html "$skill/setup/vault-page.html"

# What claude.ai checks when a skill is uploaded: a name, and a description of at most
# 1024 characters.
PYTHONUTF8=1 "$py" -B tools/check-skill.py --skill "$skill"

# The script that ships writes the archive, from its own fixed list of the skill's files, so
# that the public rowkeep.skill and an account's own copy come from one writer. It writes
# over nothing, so the last build's file is removed first. It is written twice, and the two
# must be the same bytes: a release file whose checksum cannot be reproduced proves nothing.
rm -rf dist/rowkeep.skill dist/again
mkdir dist/again
PYTHONUTF8=1 "$py" -B "$skill/scripts/rowkeep.py" pack --skill "$skill" --out dist/rowkeep.skill
PYTHONUTF8=1 "$py" -B "$skill/scripts/rowkeep.py" pack --skill "$skill" --out dist/again/rowkeep.skill >/dev/null
if ! cmp -s dist/rowkeep.skill dist/again/rowkeep.skill; then
  echo "build-skill: two builds of the same files gave different archives (dist/rowkeep.skill, dist/again/rowkeep.skill)" >&2
  exit 1
fi
rm -rf dist/again

if ! PYTHONUTF8=1 "$py" -B tools/check-skill.py --skill "$skill" --archive dist/rowkeep.skill --head "$head"; then
  # A file that failed its check must not be left where a release is picked up from.
  mv dist/rowkeep.skill dist/rowkeep.skill.failed
  echo "build-skill: the archive failed its check and was set aside as dist/rowkeep.skill.failed" >&2
  exit 1
fi
echo "build-skill: built twice, the same bytes both times"
