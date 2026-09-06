#!/usr/bin/env python3
"""Is the seal worth anything? Find out by breaking what it claims to watch.

    python3 tests/mutants.py

Reintroduces, one at a time, the defects the seal says it catches, and
**requires it to go red**. If a mutation passes green, the seal does not watch
that: it watches a toy version of that.

It copies the file before touching anything and always restores, even if
something blows up. It runs no `push`, no deploy and no command from the hook
itself: it edits one file, runs the seal, and undoes.
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "leash" / "permission.py"
SEAL = HERE / "seal.py"
BACKUP = HERE.parent / "leash" / "permission.py.before-mutating"

# (name, text to find, what to replace it with, what stops being watched)
MUTANTS = [
    (
        "remove the destination normalization",
        'd = refspec.split(":")[-1].lstrip("+")',
        'd = refspec.split(":")[-1]',
        "+main, refs/heads/main and HEAD:refs/heads/main slip through again",
    ),
    (
        "remove the refs/heads/ trim",
        'for prefix in ("refs/heads/", "refs/remotes/", "heads/"):',
        "for prefix in ():",
        "refs/heads/main slips through again",
    ),
    (
        "stop counting `+` as forcing",
        'if any(r.startswith("+") for r in refspecs) and not re.search(',
        'if False and any(r.startswith("+") for r in refspecs) and not re.search(',
        "+agent/x force-pushes blind",
    ),
    (
        "go back to reading the whole command on a push",
        'scope = " ".join(pieces) if pieces is not None else command',
        "scope = command",
        "the word in a commit message blocks a legitimate push again",
    ),
    (
        "do not split: read the raw string",
        "    segs = segments(command)\n    if segs is None:",
        "    segs = None\n    if segs is None:",
        "a pipe inside quotes looks like a command again",
    ),
]


def seal_passes() -> bool:
    """True if the seal passes in full."""
    r = subprocess.run([sys.executable, str(SEAL)], capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    if not seal_passes():
        print("THE SEAL IS ALREADY RED BEFORE MUTATING. Fix that first.")
        return 1
    print("Seal green to begin with. Now we break it, one at a time.\n")

    shutil.copy2(HOOK, BACKUP)
    survivors = []
    try:
        for name, find, put, what_stops in MUTANTS:
            original = BACKUP.read_text()
            if find not in original:
                print(f"  ??? {name}: could not find where to mutate — NOT applied")
                survivors.append((name, "not applied"))
                continue
            HOOK.write_text(original.replace(find, put, 1))
            red = not seal_passes()
            print(f"  {'OK ' if red else 'BAD'} {name}")
            print(f"      {'the seal goes red' if red else 'THE SEAL STAYS GREEN'} · {what_stops}")
            if not red:
                survivors.append((name, what_stops))
    finally:
        shutil.copy2(BACKUP, HOOK)
        BACKUP.unlink(missing_ok=True)

    print()
    if not seal_passes():
        print("CAREFUL! After restoring, the seal is NOT green. Check the file.")
        return 1

    if survivors:
        print(f"{len(survivors)} mutants SURVIVE — the seal does not catch them:")
        for name, what in survivors:
            print(f"  · {name}: {what}")
        return 1

    print(f"{len(MUTANTS)}/{len(MUTANTS)} mutants killed. Restored and green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
