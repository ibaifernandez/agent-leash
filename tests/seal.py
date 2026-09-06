#!/usr/bin/env python3
"""Seal on the permission hook. It exercises BOTH DIRECTIONS.

    python3 tests/seal.py      → 0 if all green, 1 if anything fails

Every case declares what it expects: that the legitimate thing **passes**, or
that the forbidden thing **stays blocked**. Both, always.

**Why this exists.** An over-reach in the hook was fixed — the word `main`
inside a commit message was blocking a legitimate push — and the fix opened
three holes the previous code did close: `+main`, `refs/heads/main` and
`HEAD:refs/heads/main`. No test caught it because there were no tests. **A seal
that measures only one direction is exactly how you get there.**

And on the same day, during that rewrite, the file was left half-edited and
returned `NameError` to **every call from every session on the machine**. It
closed by default, which is correct — but this would have caught it before it
was ever saved.

**Rule when touching this: if a legitimate case fails, fix the code, not the
case.**

This file is executed, not read. And it is run by somebody who did not write it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = str(Path(__file__).resolve().parent.parent / "leash" / "permission.py")

# Two sibling repositories under one root. Nothing is created on disk: the hook
# only ever reasons about the strings.
#
# Deliberately NOT under /tmp. The hook treats temp directories as belonging to
# nobody, so a root inside one makes every house boundary evaporate — which is
# also the practical warning: do not keep your repositories under /tmp.
ROOT = "/repos/"
HOUSE = ROOT + "my-repo"
OTHER = ROOT + "their-repo"

GATES = "mcp__board__,mcp__atlas__"


def decide(raw_input: str, role=None) -> str:
    env = dict(os.environ)
    env["LEASH_ROOT"] = ROOT
    env["LEASH_BRANCH"] = "agent/"
    env["LEASH_MCP_GATES"] = GATES
    env.pop("LEASH_ROLE", None)
    if role is not None:
        env["LEASH_ROLE"] = role
    r = subprocess.run(
        [sys.executable, HOOK], input=raw_input, capture_output=True, text=True, env=env
    )
    s = r.stdout.strip()
    if not s:
        return "no-decision"
    try:
        return json.loads(s)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return "unreadable-output"


def bash(cmd, cwd=HOUSE):
    return {"cwd": cwd, "tool_name": "Bash", "tool_input": {"command": cmd}}


def tool(name):
    return {"cwd": HOUSE, "tool_name": name, "tool_input": {}}


# ── Where a push actually lands. The three marked ← slipped through once. ────
PUSH = [
    ("git push origin main", "deny"),
    ("git push origin master", "deny"),
    ("git push origin HEAD:main", "deny"),
    ("git push --force origin main", "deny"),
    ("git push origin +main", "deny"),                    # ← slipped through
    ("git push origin refs/heads/main", "deny"),          # ← slipped through
    ("git push origin HEAD:refs/heads/main", "deny"),     # ← slipped through
    ("git push origin +refs/heads/master", "deny"),
    ("git push origin HEAD:agent/x", "allow"),
    ('git commit -m "merge main + docs" && git push origin HEAD:agent/x', "allow"),
    ("git push origin --delete agent/old", "deny"),
    ("git push --force origin agent/x", "deny"),
    ("git push origin +agent/x", "deny"),                 # the `+` is forcing
    ("git push --force-with-lease origin agent/x", "allow"),
    # These two exist because the mutant found them: without them, two real
    # defects could be reintroduced and the seal stayed green. Do not delete.
    #
    # With `--force-with-lease` the forcing rule does not fire, so the only
    # thing stopping a push to the main branch is normalizing the `+`. Without
    # this case, removing that normalization went unnoticed.
    ("git push --force-with-lease origin +main", "deny"),
    # THE `+` AFTER THE COLON. This is the form that really discriminates: with
    # the `+` in front, the forcing rule catches it, so removing the
    # normalization did not show. Here nothing else catches it — without the
    # `lstrip`, `HEAD:+main` pushes to the main branch, green.
    ("git push origin HEAD:+main", "deny"),
    ("git push -u origin main:+main", "deny"),
    # The push's scope has to be ONLY its own segment. If it goes back to
    # reading the whole command, a flag written in the commit message blocks a
    # legitimate push. Without this case, that regression went unnoticed.
    ('git commit -m "drop the -d flag and the --force" && git push origin HEAD:agent/x', "allow"),
    # A push inside a string is NOT a push.
    ("""echo '{"tool_input":{"command":"git push origin +main"}}' | python3 x.py""", "allow"),
    ('echo "remember: git push origin main is forbidden" >> /tmp/note.txt', "allow"),
]

# ── Quotes and pipes: what is searched for is not what is run ────────────────
SCOPE = [
    ('grep -n -iE "database|psql|supabase|DATABASE" permission.py', "allow"),
    ('grep -rn "netlify|vercel|rm -rf" docs/', "allow"),
    ('gh pr create --title x --body "check netlify.toml first"', "allow"),
    ("git status --short", "allow"),
]

# ── The veto, which is not up for negotiation ───────────────────────────────
VETO = [
    ("supabase db push", "deny"),
    ("echo x | supabase db reset", "deny"),
    ("psql $DATABASE_URL -c 'select 1'", "deny"),
    ("ls; rm -rf dist", "deny"),
    ('bash -c "rm -rf dist"', "deny"),
    ("netlify deploy --prod", "deny"),
    ("sudo systemctl restart nginx", "deny"),
    ("git reset --hard HEAD~3", "deny"),
    ("gh repo delete someone/x", "deny"),
    ("cat .env", "deny"),
]

# ── MCP tools: judged by the shape of the verb, not by a list ───────────────
MCP = [
    ("mcp__board__list_cards", "allow"),
    ("mcp__board__delete_card", "deny"),
    ("mcp__board__delete_column", "deny"),    # born after the list was written
    ("mcp__atlas__purge_snapshot", "deny"),   # does not exist yet
    ("mcp__board__undelete_card", "allow"),   # only looks destructive
]

# ── House boundaries ────────────────────────────────────────────────────────
HOUSES = [
    ({"cwd": HOUSE, "tool_name": "Write", "tool_input": {"file_path": HOUSE + "/docs/x.md"}}, "allow"),
    ({"cwd": HOUSE, "tool_name": "Write", "tool_input": {"file_path": OTHER + "/x.md"}}, "deny"),
    ({"cwd": HOUSE, "tool_name": "Read", "tool_input": {"file_path": "/tmp/x.md"}}, "allow"),
]

# ── Nobody merges their own work ────────────────────────────────────────────
#
# This block did not exist, and it was the ONLY one of four sabotages that no
# battery caught. Measured: with the worker veto returning `None`, a worker
# could merge its own pull request and every CI check stayed green.
#
# The invariant it protects admits no nuance: nobody merges their own work, not
# even if asked to. And its shape is the dangerous kind: the veto depends on the
# ROLE, so a failure here breaks nothing visible — it simply lets through the
# one who should not have passed.
#
# Both directions, like everything in this file: the worker cannot, and the role
# that can is not blocked either.
MERGE = [
    ("gh pr merge 1 --squash", "worker",     "deny"),
    ("gh pr merge 1",          "worker",     "deny"),
    ("git merge agent/x",      "worker",     "deny"),
    ("gh pr merge 1 --squash", "supervisor", "allow"),
    ("git merge agent/x",      "supervisor", "allow"),
    # With no role declared we cannot assert the caller is NOT the worker.
    # What the hook decides here is its own judgement; what this seal fixes is
    # that the case EXISTS and somebody looks if it changes.
    ("gh pr merge 1 --squash", None,         "allow"),
]

# ── If it does not understand the request, it CLOSES ────────────────────────
RAW = [
    ("this is not json", "deny"),
    ("", "deny"),
    ("[1,2,3]", "deny"),
]


def main() -> int:
    failures = []

    def check(label, payload, expected, role=None):
        got = decide(payload, role)
        ok = got == expected
        if not ok:
            failures.append((label, expected, got))
        print(f"  {'OK ' if ok else 'BAD'} [{expected:5}] {label[:60]:60} -> {got}")

    print("── where a push lands ──")
    for cmd, exp in PUSH:
        check(cmd, json.dumps(bash(cmd, OTHER)), exp)

    print("── scope: quotes and pipes ──")
    for cmd, exp in SCOPE:
        check(cmd, json.dumps(bash(cmd)), exp)

    print("── the veto ──")
    for cmd, exp in VETO:
        check(cmd, json.dumps(bash(cmd)), exp)

    print("── MCP tools ──")
    for name, exp in MCP:
        check(name.rsplit("__", 1)[-1], json.dumps(tool(name)), exp)

    print("── house boundaries ──")
    for payload, exp in HOUSES:
        label = payload["tool_name"] + " " + payload["tool_input"]["file_path"][:44]
        check(label, json.dumps(payload), exp)

    print("── nobody merges their own work ──")
    for cmd, role, exp in MERGE:
        check(f"[{role or 'no role'}] {cmd}", json.dumps(bash(cmd)), exp, role)

    print("── input it cannot understand ──")
    for payload, exp in RAW:
        check(repr(payload)[:40], payload, exp)

    total = (len(PUSH) + len(SCOPE) + len(VETO) + len(MCP)
             + len(HOUSES) + len(MERGE) + len(RAW))
    print()
    if failures:
        print(f"{len(failures)} FAILURES out of {total}:")
        for label, exp, got in failures:
            print(f"  · {label}  expected {exp}, got {got}")
        return 1
    print(f"{total}/{total} green — both directions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
