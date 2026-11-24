#!/bin/sh
# sh tools/public-tree.sh DEST [--archive FILE]...
# sh tools/public-tree.sh --tracked | --scan DIR | --archive FILE
#
# Writes a copy of this repository that is fit to publish into DEST, which must not exist
# yet, and checks the copy for things that must never ship: links to particular artifacts,
# account and service ids, addresses, home-folder paths, credentials, files that hold one
# person's settings, and the like. With --archive it checks a built skill the same way,
# entry by entry. The work is done by tools/public-tree.py, which says what each option
# does and lists the few known fixtures that are allowed, each as one exact value in one
# named file.
#
# Exit 0: nothing found. 1: findings. 2: the check could not finish, which is a failure.
# 3: no Python 3.8 or later.
here=$(cd "$(dirname "$0")" && pwd)
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    PYTHONUTF8=1 exec "$c" -B "$here/public-tree.py" "$@"
  fi
done
echo "public-tree: needs Python 3.8 or later, as python3 or python" >&2
exit 3
