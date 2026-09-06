# agent-leash

A fail-closed authorization hook for Claude Code. It decides `allow` / `deny` /
`ask` for every tool call by **parsing the command**, not by matching a prefix.

Zero dependencies. One file of Python, standard library only.

---

## Sixty seconds

```bash
git clone https://github.com/ibaifernandez/agent-leash
cd agent-leash

python3 tests/seal.py       # 51/51 green — both directions
python3 tests/mutants.py    # 5/5 mutants killed
```

The first command proves the rules hold. The second proves the first command is
worth anything: it reintroduces five real defects, one at a time, and requires
the battery to go **red** for each. A guard nobody has tried to break is a guard
nobody knows the strength of.

---

## Why prefix matching is not enough

Claude Code's own permission patterns match the **start** of the command string.
That covers what you anticipated. An agent running unattended always invents
something you did not.

Worse, string matching gets both directions wrong. Two measured incidents, both
from the same root cause — reasoning about raw text instead of parsed structure:

```bash
git commit -m "merge main…" && git push origin HEAD:agent/x
```
Blocked for "pushing to the main branch". The word was in the **commit message**.

```bash
grep -n -iE "database|psql|supabase" permission.py
```
Blocked for "touching the database". The pipe was **inside the quotes**, so what
followed looked like a new command.

Both were false positives on legitimate work. Fixing them naively opened three
false negatives — `+main`, `refs/heads/main`, `HEAD:refs/heads/main` all reached
the main branch. Nothing caught it, because at that point nothing tested it.

`agent-leash` splits the command with `shlex`, respects quoting, segments on
shell operators, and recursively unwraps `bash -c` payloads. Each segment is
judged on its own command name and its own arguments.

> The difference between **invoking** and **mentioning** is a parser, not a
> regex.

---

## What it decides

| | |
|---|---|
| **Vetoed commands** | Matched only in command position, so searching for the word is a search and running it is a run |
| **Push destinations** | Refspecs normalized before comparison: `+main`, `refs/heads/main`, `HEAD:+main` all resolve to `main` |
| **Force pushes** | `--force` denied; `--force-with-lease` allowed only on the reserved agent branch prefix |
| **Repository boundaries** | A session writes inside its own repository. It may read the others — verifying against a second source is half the job |
| **Destructive MCP tools** | Judged by **verb shape** (`delete_`, `purge_`, `drop_`…), not by an allowlist |
| **Secrets** | `.env` is closed to `Bash` and to `Read` alike — read-only is not harmless when reading dumps the file into the context |
| **Unparseable input** | Denied. If the guard does not understand, it closes |

### Why verb shape and not a list

The destructive-tool check used to be a list. It named five tools and it was
complete the day it was written. Then a sixth tool was added to the MCP server,
the list did not hear about it, and an agent deleted a board column.

Nobody left anything out. The server grew and the list did not.

It is the same bill this codebase had already written for shell permissions — a
list does not converge, a program that judges does — charged **inside the very
guard that replaced it**. So the check now judges by shape: any tool whose verb
destroys is vetoed, whether it exists today or is born tomorrow.

The battery asserts on a tool name that does not exist yet.

---

## The leash

Set `LEASH_ROLE=worker` for an unattended agent and two more rules apply:

**It cannot merge.** A supervised session eats its own cooking — it reviews what
its worker did and merges it if it holds up. The worker cannot merge what it
just wrote. Work with no second pair of eyes does not enter `main`, and the
worker cannot be its own eyes.

**It cannot write to any `.claude/` directory, including its own.** This one is a
path veto rather than a verb veto, and that is deliberate:

> Everything else this file forbids defends itself: to get around it you have to
> merge something, and somebody looks at that. This one does not. The hook is
> read from disk **on every call** — verified by mutating it live: the change
> took effect on the very next invocation — so a worker that edits it loosens
> its own leash for its own next turn. No pull request, no review, and nobody
> finds out.

The asymmetry that decides how wide to close it:

> If the veto is too narrow, somebody gets frustrated and says so. If it is too
> wide, a worker slips its leash and nobody says anything.

---

## Install

Copy `leash/permission.py` anywhere and register it as a `PreToolUse` hook:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "python3 /path/to/leash/permission.py" }
        ]
      }
    ]
  }
}
```

To revert, remove the `hooks` block. The file can stay; unregistered, it does
nothing.

## Configuration

All optional, all environment variables.

| Variable | Default | What it does |
|---|---|---|
| `LEASH_ROOT` | parent of the session's cwd | Directory holding your repositories, one level above each |
| `LEASH_ROLE` | unset | `worker` for an unattended agent |
| `LEASH_BRANCH` | `agent/` | Branch prefix reserved for unattended agents |
| `LEASH_MCP_GATES` | empty | Comma-separated MCP prefixes callable without asking |
| `LEASH_SHARED_DIR` | empty | One directory outside the repository any session may write to |

Note that temp directories (`/tmp`, `/private/tmp`, `/var/folders`) are treated
as belonging to nobody. Do not keep your repositories inside one, or every house
boundary evaporates.

---

## What this is not

It is **not a sandbox**. A determined process can bypass any hook that runs
in-process with it. This raises the cost of an accident, not the cost of an
attack. The threat model is an unattended agent doing something irreversible at
three in the morning — not an adversary.

It is **not audited**. It is one operator's guard, extracted from a fleet of ten
repositories where it has gated every tool call for months. Read it before you
trust it. That is what the comments are for.

It has **one measured coverage gap**, stated rather than hidden: when `shlex`
cannot parse a command — a heredoc, an unclosed quote — it falls back to
whole-string regex matching. That path is conservative, so it over-blocks rather
than under-blocks, but it is not the parser.

---

## Provenance

Extracted from a private multi-repository agent-governance layer, where the
kernel and its batteries gate every session across ten repositories. Every
incident cited in the comments is a real one, with a real bill.

The comments explaining *why* a rule exists are the point of this repository.
The code is 600 lines; anyone can write those. The reasons cost the failures.

MIT licensed.
