#!/bin/sh
# Runs rowkeep.py, which sits beside this file, passing every argument through.
# The script needs Python 3.8 or later and nothing else: no packages, and it never opens a
# network connection. It finds the verb registry bundled with this skill on its own, unless
# the caller names another with --registry or ROWKEEP_REGISTRY.

# With CDPATH set, `cd` into a folder found through it prints that folder's path, and the
# path worked out below would then hold two lines. It is cleared, and `cd --` and `pwd -P`
# are told plainly what to do.
unset CDPATH
here=$(cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd -P)
if [ -z "$here" ] || [ ! -f "$here/rowkeep.py" ]; then
  echo "rowkeep: cannot find rowkeep.py beside this launcher ($0)" >&2
  exit 3
fi

# Each candidate is run, not just looked for: on Windows a `python3` can be on the PATH that
# only says where to install Python.
seen=""
for python in python3 python; do
  command -v "$python" >/dev/null 2>&1 || continue
  if "$python" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    exec "$python" "$here/rowkeep.py" "$@"
  fi
  seen="$seen $python"
done

if [ -n "$seen" ]; then
  echo "rowkeep: needs Python 3.8 or later; the only Python found ($(echo $seen | sed 's/ / and /')) is older or does not run" >&2
else
  echo "rowkeep: needs Python 3.8 or later, as python3 or python; neither was found" >&2
fi
exit 3
