"""Drop container privileges, then replace PID 1 with the worker."""
from __future__ import annotations

import os
import sys


def _identity(name: str, minimum: int) -> int:
    value = os.environ.get(name, "")
    if not value.isascii() or not value.isdecimal() or int(value) < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def main() -> int:
    try:
        uid = _identity("PUID", 1)
        gid = _identity("PGID", 0)
        if os.geteuid() == 0:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
        elif os.geteuid() != uid or os.getegid() != gid:
            raise ValueError("Container user must match PUID/PGID, or start as root to drop privileges")
        os.execv(sys.executable, [sys.executable, "-m", "scanmail", *sys.argv[1:]])
    except (ValueError, OSError, OverflowError) as error:
        print(f"Cannot start container: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
