#!/bin/sh
# sh tools/build-personal.sh [--team]
#
# Makes the checkout owner's OWN copy of the skill, with the links of their log and vault
# written in, as dist/personal/rowkeep-personal.skill. THAT FILE IS PRIVATE: it is for the
# owner's own account and is never published, attached to an issue or sent to anyone.
# The file to hand out is dist/rowkeep.skill, which tools/build-skill.sh makes and which
# names no log.
#
# The links are read from local.json at the top of the checkout, which is never committed:
#   {"log": "<link>", "slug": "<id>", "vault": "<link or empty>"}
# With --team, a second file is made as well, dist/personal/rowkeep-team.skill, which names
# the log and not the vault: the one a teammate is given. It is private to the team.
#
# It refuses to run where CI is set: nothing private is ever built there.
set -e
if [ -n "$CI" ]; then
  echo "build-personal: refused: CI is set. A personal copy of the skill is never built in CI" >&2
  exit 2
fi
team=""
if [ "$1" = "--team" ] && [ $# -eq 1 ]; then
  team=1
elif [ $# -ne 0 ]; then
  echo "usage: sh tools/build-personal.sh [--team]" >&2
  exit 2
fi
root=$(cd "$(dirname "$0")/.." && pwd)
skill=plugin/skills/rowkeep
cd "$root"
if [ ! -f local.json ]; then
  echo "build-personal: there is no local.json here. It names your log: {\"log\": \"<link>\", \"slug\": \"<id>\", \"vault\": \"<link or empty>\"}" >&2
  exit 2
fi
py=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then py=$c; break; fi
done
if [ -z "$py" ]; then echo "build-personal: needs Python 3.8 or later, as python3 or python" >&2; exit 3; fi

get() { PYTHONUTF8=1 "$py" -c "import json,sys; print(json.load(open('local.json', encoding='utf-8')).get(sys.argv[1]) or '')" "$1"; }
log=$(get log)
slug=$(get slug)
vault=$(get vault)
if [ -z "$log" ]; then echo "build-personal: local.json gives no \"log\"" >&2; exit 2; fi

# The script that ships does the writing, as it does for the public archive. It writes over
# nothing, so the last build is removed first.
rm -rf dist/personal
mkdir -p dist
if [ -n "$vault" ]; then
  PYTHONUTF8=1 "$py" -B "$skill/scripts/rowkeep.py" personalize --skill "$skill" --out dist/personal --log "$log" --slug "$slug" --vault "$vault" >/dev/null
else
  PYTHONUTF8=1 "$py" -B "$skill/scripts/rowkeep.py" personalize --skill "$skill" --out dist/personal --log "$log" --slug "$slug" >/dev/null
fi
echo "PRIVATE: dist/personal/rowkeep-personal.skill NAMES YOUR LOG AND YOUR VAULT."
echo "PRIVATE: IT IS FOR YOUR OWN ACCOUNT. NEVER PUBLISH IT, ATTACH IT OR SEND IT TO ANYONE."
if [ -n "$team" ]; then
  PYTHONUTF8=1 "$py" -B "$skill/scripts/rowkeep.py" personalize --skill "$skill" --out dist/personal/team-work --log "$log" --slug "$slug" >/dev/null
  mv dist/personal/team-work/rowkeep-personal.skill dist/personal/rowkeep-team.skill
  rm -rf dist/personal/team-work
  echo "PRIVATE TO YOUR TEAM: dist/personal/rowkeep-team.skill NAMES YOUR LOG (NOT YOUR VAULT)."
  echo "PRIVATE TO YOUR TEAM: GIVE IT TO TEAMMATES ONLY. NEVER PUBLISH IT."
fi
echo "The file to hand out to anyone else is dist/rowkeep.skill (sh tools/build-skill.sh), which names no log."
