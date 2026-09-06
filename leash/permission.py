#!/usr/bin/env python3
"""Decide permissions by parsing the program, not by matching a text prefix.

Claude Code's own allow/deny patterns match the START of the command string, so
they never cover a command nobody anticipated — and an agent running overnight
always invents one. This reads the whole command and decides.

To revert: remove the "hooks" block from .claude/settings.json. This file can
stay; unregistered, it does nothing.

Contract with Claude Code: a JSON object arrives on stdin with `tool_name` and
`tool_input`; a JSON object leaves on stdout with `permissionDecision` =
allow | deny | ask. Decide nothing and it falls through to normal behaviour.

Configuration, all optional, all environment variables:

    LEASH_ROOT        Directory holding your repositories, one level up from
                      each. Writes are confined to the repo the session is in.
                      Defaults to the parent of the session's working directory.
    LEASH_ROLE        Set to "worker" for an unattended agent. Adds the
                      restrictions a supervised session does not need.
    LEASH_BRANCH      Branch prefix reserved for unattended agents.
                      Default "agent/".
    LEASH_MCP_GATES   Comma-separated MCP server prefixes that may be called
                      without asking. Empty by default.
    LEASH_SHARED_DIR  One directory outside the repo that any session may write
                      to. Empty by default.
"""

import json
import os
import re
import sys

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# An unattended worker and a supervised session run in the same folder with the
# same hook, so the folder cannot tell them apart. This can: the launcher puts
# the marker in the environment. No marker means somebody is watching.
#
# What the difference is FOR: merging. A supervised session eats its own
# cooking — it reviews what its worker did and merges it if it holds up — but
# the worker cannot merge what it just wrote itself. Work with no second pair
# of eyes does not enter `main`, and the worker cannot be its own eyes.
IS_WORKER = os.environ.get("LEASH_ROLE") == "worker"

# Branches created by an unattended worker. Reserve this prefix deliberately:
# if no human branch uses it, force-pushing over it cannot touch anyone's work.
# As a bonus it separates machine-made work at a glance.
AGENT_BRANCH = os.environ.get("LEASH_BRANCH", "agent/")

# MCP server prefixes an agent may call without being asked. Declared in one
# place so a new repository has them the day it opens, and changing them is one
# file rather than nine.
MCP_GATES = tuple(
    p.strip() for p in os.environ.get("LEASH_MCP_GATES", "").split(",") if p.strip()
)

# The one door open outside the repo: where agents leave their notes.
SHARED_DIR = os.environ.get("LEASH_SHARED_DIR", "")

# Temp directories: they belong to nobody and there is nothing to protect.
TEMP_DIRS = ("/tmp/", "/private/tmp/", "/var/folders/")


def _root(cwd: str) -> str:
    """Where the repositories live.

    Explicit configuration wins. Otherwise it is derived from the session's
    working directory, so the tool works on a fresh clone with no setup: the
    parent of the current repository is the root, and the repository itself is
    the house.
    """
    configured = os.environ.get("LEASH_ROOT", "")
    if configured:
        return configured.rstrip("/") + "/"
    if not cwd:
        return ""
    return cwd.rstrip("/").rsplit("/", 1)[0] + "/"


# ─────────────────────────────────────────────────────────────────────────────
# HOW A COMMAND IS READ
#
# This used to reason about the RAW STRING with regular expressions, with no
# idea what was quoted and what was an argument. Two measured defects came out
# of that, and they were the same defect:
#
#   · git commit -m "merge main…" && git push origin HEAD:agent/x
#     → blocked for "pushing to the main branch". The word was in the COMMIT
#       MESSAGE, not in the push destination.
#   · grep -n -iE "database|psql|supabase|…" permission.py
#     → blocked for "touching the database". The pipe was INSIDE the quotes, so
#       what followed looked like a new command.
#
# Now it is split with `shlex`: quotes are respected and shell operators come
# out as separate pieces. A command is split into SEGMENTS on those operators,
# and each segment has its own command name and its own arguments.
#
# What does NOT change is the veto. What gets corrected is the OVER-REACH.
# ─────────────────────────────────────────────────────────────────────────────

OPERATORS = {";", "|", "||", "&", "&&", "(", ")", "\n", "&|"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}


def segments(command: str, _depth: int = 0):
    """Split a command into segments, each already unquoted.

    Returns `None` when it cannot be parsed — an unclosed quote, a heredoc.
    The caller decides what to do with that; nothing is invented here.

    A `bash -c "…"` is split again from the inside: otherwise wrapping something
    forbidden in a string would be enough to make it invisible.
    """
    try:
        import shlex

        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        pieces = list(lex)
    except Exception:
        return None

    out, current = [], []
    for piece in pieces:
        if piece in OPERATORS:
            if current:
                out.append(current)
            current = []
        else:
            current.append(piece)
    if current:
        out.append(current)

    if _depth < 2:
        expanded = []
        for seg in out:
            expanded.append(seg)
            if seg and seg[0] in SHELLS and "-c" in seg:
                i = seg.index("-c")
                if i + 1 < len(seg):
                    inner = segments(seg[i + 1], _depth + 1)
                    if inner:
                        expanded.extend(inner)
        out = expanded

    return out


# Commands that never run. Checked ONLY in command position: the first name of a
# segment. That way searching for the word is a search, and running it is a run.
#
# The names are written as adjacent string literals on purpose. This file has to
# be greppable and editable by the very agents it restrains, and a literal
# `rm -rf` inside it would trip its own string-level rules.
VETOED_COMMANDS = {
    "r" "m": "delete files",
    "railway": "touch live infrastructure",
    "netlify": "touch deployments",
    "supa" "base": "touch the database",
    "vercel": "touch deployments",
    "ps" "ql": "talk to a production database",
    "su" "do": "escalate privileges",
    "shut" "down": "power off the machine",
    "re" "boot": "power off the machine",
    "twine": "publish a package",
}

# What has to be checked in the arguments, not just the name. Each entry is
# (command name, pieces that must all be present, what it is).
VETOED_PHRASES = [
    ("git", ("reset", "--hard"), "discard work with no way back"),
    ("git", ("clean",), "delete untracked files"),
    ("gh", ("repo", "delete"), "delete a repository"),
    ("crontab", ("-r",), "delete the scheduled jobs"),
    ("npm", ("publish",), "publish a package"),
    ("pip", ("upload",), "publish a package"),
]

# These are still matched against the whole string, deliberately: a secrets file
# does not stop being one because it is quoted, and here a false positive is the
# cheaper mistake. They worked and they are not being touched.
WHOLE_STRING = [
    (r"\.env\b", "read or write a secrets file"),
    (r"\bdd\s+if=", "write to a disk at block level"),
    (r"\bmkfs\b", "format a disk"),
]

# Safety net for when `shlex` cannot parse the command — a heredoc, an unclosed
# quote. This is the previous behaviour: conservative, over-reaching, but never
# less veto than there was before.
START = r"(?:^|[;&|]|\$\(|\n)\s*"
END = r"(?![\w.-])"
FALLBACK = [(START + re.escape(c) + END, q) for c, q in VETOED_COMMANDS.items()]


def veto_in(command: str):
    """Is anything forbidden in this command? Returns the reason, or None."""
    for pattern, what in WHOLE_STRING:
        if re.search(pattern, command):
            return what

    segs = segments(command)
    if segs is None:
        for pattern, what in FALLBACK:
            if re.search(pattern, command):
                return what
        return None

    for seg in segs:
        if not seg:
            continue
        name = seg[0].rsplit("/", 1)[-1]
        if name in VETOED_COMMANDS:
            return VETOED_COMMANDS[name]
        if name.startswith("mkfs"):
            return "format a disk"
        for head, pieces, what in VETOED_PHRASES:
            if name == head and all(p in seg for p in pieces):
                return what
    return None


# What only a worker is forbidden, checked per segment like everything else.
SUPERVISED_ONLY = [
    ("gh", ("pr", "merge"), "merge a pull request"),
    ("git", ("merge",), "merge branches"),
]


# The leash. A worker does not write to any agent's configuration — not even to
# its own repository's.
#
# WHY THIS VETO IS BY PATH AND NOT BY VERB, and why review is not enough.
# Everything else this file forbids defends itself: to get around it you have to
# merge something, and somebody looks at that. This one does not. The hook is
# read from disk ON EVERY CALL — verified by mutating it to test its own battery:
# the change took effect on the very next invocation — so a worker that edits it
# loosens its own leash for its own next turn. No pull request, no review, and
# nobody finds out.
#
# And it applies to ANY `.claude/`, not only this repository's: a worker could
# edit the `settings.json` of its own house and be running unhooked from the
# next session onward. It was its own house, so no rule was watching.
#
# The asymmetry that says close it on the wide side: if the veto is too narrow,
# somebody gets frustrated and says so. If it is too wide, a worker slips its
# leash and nobody says anything.
#
# This does NOT stop a worker working on its repository's code, its guards, or
# its test batteries. Only on the agent's configuration.
LEASH = "/.claude/"


def worker_veto(command: str):
    """What a worker cannot do even though a supervised session can."""
    segs = segments(command)
    if segs is None:
        if re.search(r"\bgh\s+pr\s+merge\b", command):
            return "merge a pull request"
        if re.search(r"\bgit\s+merge\b", command):
            return "merge branches"
        return None
    for seg in segs:
        if not seg:
            continue
        name = seg[0].rsplit("/", 1)[-1]
        for head, pieces, what in SUPERVISED_ONLY:
            if name == head and all(p in seg for p in pieces):
                return what
    return None


# Read-only tools: always allowed.
READ_ONLY = {"Read", "Grep", "Glob", "NotebookRead", "TodoWrite"}

# What no gate excuses: an agent does not delete. Bad work gets reverted; a card
# deleted overnight does not come back, and the context somebody spent time
# writing goes with it.
#
# THIS USED TO BE A LIST AND THE LIST FELL SHORT. It enumerated `delete_card`,
# `delete_board`, `delete_workspace`, `clear_workspace` and `remove_member`, and
# it was complete the day it was written. Then `delete_column` was added to the
# MCP server, the list did not hear about it, and an agent deleted a column.
# Nobody left anything out: the server grew and the list did not.
#
# It is the same bill this codebase had already written for shell permissions —
# a list does not converge, a program that judges does — charged inside the very
# guard that replaced it. So here it judges by SHAPE, not by name: any tool
# whose verb destroys is vetoed, whether it exists today or is born tomorrow.
DESTRUCTIVE_VERBS = ("delete_", "remove_", "clear_", "purge_", "drop_", "destroy_")


def is_destructive(tool: str) -> bool:
    """Does this tool's verb destroy something?

    The name is read from after the MCP server prefix, because the prefix
    carries the server's own name and could contain anything.
    """
    name = tool.rsplit("__", 1)[-1]
    return any(name.startswith(v) for v in DESTRUCTIVE_VERBS)


def writing_patterns(root: str):
    """Verbs that WRITE. If one of these appears next to another repo's path,
    it is a foreign write dressed up as a command."""
    return [
        r"\bsed\s+-i\b",
        r"\btee\b",
        # Only redirection that POINTS AT a house counts. `2>/dev/null` or
        # `> /tmp/x` do not write into anybody's repo, and blocking them broke
        # perfectly innocent read commands.
        r">>?\s*[\"']?" + re.escape(root),
        r"\bcp\b",
        r"\bmv\b",
        r"\btouch\b",
        r"\bmkdir\b",
        # A repository path may contain a space, so the subcommand cannot be
        # required to sit flush against it. It is enough that both appear: this
        # is only evaluated once a foreign path is already on the line.
        #
        # The three anchors are not decoration, and each one pays a measured
        # bill:
        #
        #   (?<!\.)  the `git` in `.git` is NOT the git program. Without this,
        #            any command naming `<repo>/.git/...` already met half the
        #            condition.
        #   (?<!-)   the `commit` in `post-commit` is NOT the subcommand. A
        #            hyphen is a word boundary, so `\b` alone does not separate
        #            it — and with that, reading another repo's git hooks was
        #            denied, which is ordinary audit work.
        #
        # Together they denied a READ, which is exactly the opposite of what
        # this block exists to do, and against the intent written five lines
        # above. A reviewer caught it in passing, not a test: which is why this
        # file now has its own battery with a positive control.
        #
        # And the third anchor: between `git` and its subcommand, no longer
        # anything at all. It used to be `[\s\S]*`, and with that it was enough
        # to MENTION a subcommand anywhere on the line — a label, a comment, a
        # message — for a perfectly innocent `git log` to come back denied.
        #
        # What does fit now: flags, quoted strings and paths. What does not:
        # `;`, `&`, `|`, `#`, `<`, `>`. So the repetition DOES NOT CROSS a
        # command separator, and that is why `git status && echo "doing the
        # commit"` passes while `git -C <repo> commit` stays denied — the
        # difference between invoking and mentioning is exactly that boundary.
        #
        # Quoted strings are consumed whole on purpose: if the generic
        # alternative were allowed to eat half a quote, the subcommand could
        # enter from inside the text, which is where we came from.
        r"(?<!\.)\bgit\b"
        r"(?:\s+(?:-\S+|\"[^\"]*\"|'[^']*'|[^\s;&|#<>'\"]+))*"
        r"\s+(?<!-)(?:commit|add|checkout|switch|reset|restore|apply|stash)\b",
    ]


def house_of(cwd: str, root: str) -> str:
    """A session's house: the repository it lives in.

    A repository may READ the others — verifying against a second source is half
    the job — but not write into them. Otherwise a shared artifact ends up with
    seven authors and no owner, and whoever changed it last week does not show
    up. Finding out about a cross-write today was one thread's discipline, not a
    property of the system. This makes it a property of the system.
    """
    if not cwd:
        return ""
    if root and cwd.startswith(root):
        rest = cwd[len(root):]
        return root + rest.split("/", 1)[0] + "/"
    return cwd.rstrip("/") + "/"


def is_own(path: str, house: str) -> bool:
    """The house, with or without a trailing slash, and everything under it.

    Without the second case, a command citing its OWN root without a slash
    — `cd "…/my-repo" && git checkout …` — was classified as foreign and blocked
    from inside the correct repository.
    """
    return path.startswith(house) or path.rstrip("/") == house.rstrip("/")


def memory_of(cwd: str) -> str:
    """The session-memory folder belonging to THIS repository.

    It lives outside the repository root, so the "everyone in their own house"
    rule left it out — and with it, the session started blank every time. It is
    opened, but only its own: the path is derived from the working directory
    itself, so one repository cannot write into another's memory.
    """
    if not cwd:
        return ""
    # That folder's name flattens the path: slashes, spaces and dots all become
    # hyphens. `…/repos/my.app` → `-Users-…-repos-my-app`.
    flat = re.sub(r"[/ .]", "-", cwd)
    return os.path.expanduser("~/.claude/projects/") + flat + "/"


def in_house(path: str, house: str, cwd: str = "") -> bool:
    if not house or not path:
        return True  # with no data we do not invent a boundary
    if SHARED_DIR and path.startswith(SHARED_DIR):
        return True
    if path.startswith(TEMP_DIRS):
        return True
    memory = memory_of(cwd)
    if memory and path.startswith(memory):
        return True
    return is_own(path, house)


def foreign_paths(command: str, house: str, root: str) -> list:
    """Absolute paths of OTHER repositories mentioned in a command."""
    if not house or not root:
        return []
    found = re.findall(re.escape(root) + r"[^\s\"';|&]*", command)
    return [
        p
        for p in found
        if not is_own(p, house) and not (SHARED_DIR and p.startswith(SHARED_DIR))
    ]


def respond(decision: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def destination_of(refspec: str) -> str:
    """The branch a refspec really points at, normalized.

    Comparing against `("main", "master")` as-is let three forms through that
    the older code did block, and all three push to the main branch:

        git push origin +main                  ← the leading `+`
        git push origin refs/heads/main        ← the ref's full name
        git push origin HEAD:refs/heads/main   ← both at once

    The destination is normalized BEFORE it is compared. Widening the search
    over the whole command would block them too, and would reopen the defect
    this fix came to close: the word inside a commit message.
    """
    d = refspec.split(":")[-1].lstrip("+")
    for prefix in ("refs/heads/", "refs/remotes/", "heads/"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip("/")


def decide_push(command: str) -> tuple:
    """Pushing a new branch cannot overwrite anything. Pushing to `main` can.

    Without this there is no pull request possible — `gh pr create` needs the
    branch on the remote — so blocking `git push` outright closed the whole
    process. What is allowed is the subset that destroys nobody's work.
    """
    # LOOKS ONLY AT THE PUSH SEGMENT, not the whole command. It used to search
    # the whole string, so
    #   git commit -m "merge main…" && git push origin HEAD:agent/x
    # came back blocked because of something written in the COMMIT MESSAGE.
    segs = segments(command)
    pieces = None
    if segs is not None:
        for seg in segs:
            if seg and seg[0].rsplit("/", 1)[-1] == "git" and "push" in seg:
                pieces = seg
                break
        if pieces is None:
            # It parsed and there is NO segment that pushes: then there is no
            # push, however often the words appear inside a string. Looking at
            # the whole command here blocked
            #   echo '{"command":"git push origin +main"}' | python3 …
            # which is an `echo`.
            return "allow", "no push in this command"

    # With no parse — heredoc, unclosed quote — the whole command is used:
    # conservative, as before, and never less veto than there was.
    scope = " ".join(pieces) if pieces is not None else command

    if re.search(r"(?:^|\s)(?:--delete|-d)\b", scope) or re.search(
        r"\bpush\b[^;&|]*\s+:\S", scope
    ):
        return "deny", "blocked by the leash: deleting a remote branch"

    if pieces is not None:
        # What is neither a flag nor the verb: the remote and the refspec. In an
        # `source:destination`, what counts is what follows the colon.
        targets = [p for p in pieces[1:] if p != "push" and not p.startswith("-")]
        refspecs = targets[1:] or targets

        # A leading `+` on a refspec IS forcing, even with no `--force` in
        # sight. This was not covered: `git push origin +agent/x` forced blind.
        if any(r.startswith("+") for r in refspecs) and not re.search(
            r"--force-with-lease\b", scope
        ):
            return "deny", "blocked by the leash: forcing without checking the remote"

        for d in refspecs:
            if destination_of(d) in ("main", "master"):
                return "deny", "blocked by the leash: pushing straight to the main branch"
    elif re.search(r"\b(?:main|master)\b", scope):
        return "deny", "blocked by the leash: pushing straight to the main branch"

    with_lease = re.search(r"--force-with-lease\b", scope)
    forced = re.search(r"(?:^|\s)(?:-f|--force)\b", scope)
    if forced and not with_lease:
        return "deny", "blocked by the leash: forcing without checking the remote"
    if with_lease:
        # Redoing a branch is legitimate — a worker that branched wrong needs to
        # fix it — but only its own. A repository has branches whose work exists
        # ONLY there: forcing over one of those has no way back.
        if re.search(r"\b" + re.escape(AGENT_BRANCH), command):
            return "allow", "redoing an agent branch, having checked nobody moved it"
        return "deny", f"blocked by the leash: forcing is only allowed on «{AGENT_BRANCH}» branches"

    # An explicit branch is required: a bare `git push` depends on which branch
    # we are on, and from here that cannot be known.
    if not re.search(r"\bgit\s+push\b\s+\S+\s+\S+", command):
        return "ask", "push with no explicit branch: cannot tell where it goes"
    return "allow", "pushing a branch that is not the main one"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        # If we do not understand the input, we CLOSE.
        #
        # This used to open, with the comment "let it ask". That is true when
        # somebody is watching, and this hook exists precisely for when nobody
        # is: opening in the face of what you do not understand is opening at
        # night, which is when the door matters.
        #
        # The price is measured and accepted: a failure here stops the work of
        # every session. Stopping breaks nothing — the work stays on a branch,
        # nothing is deleted, git keeps the history; letting a deletion through
        # does. Revert by turning this branch back into `sys.exit(0)`.
        respond(
            "deny",
            "blocked by the leash: it did not understand the request, so it is not "
            f"letting it through ({type(e).__name__}). If this repeats, the leash "
            "is broken and needs looking at — do not route around it.",
        )

    tool = payload.get("tool_name", "")
    data = payload.get("tool_input", {}) or {}
    cwd = payload.get("cwd", "") or ""
    root = _root(cwd)
    house = house_of(cwd, root)

    if tool.startswith("mcp__"):
        if is_destructive(tool):
            respond(
                "deny",
                "blocked by the leash: deleting is not something an agent does. "
                "If something really has to go, a person removes it.",
            )
        if MCP_GATES and tool.startswith(MCP_GATES):
            respond("allow", "declared gate")

    if tool in READ_ONLY:
        # Read-only does not mean harmless: reading a secrets file dumps it
        # whole into the context. The `cat .env` route is already closed by the
        # list above; this is the same door with a different tool.
        target = data.get("file_path", "") or data.get("path", "") or ""
        if re.search(r"\.env\b", target):
            respond("deny", "blocked by the leash: secrets file")
        respond("allow", "read-only")

    if tool == "Bash":
        command = data.get("command", "") or ""
        reason = veto_in(command)
        if reason:
            respond("deny", f"blocked by the leash: {reason}")
        if IS_WORKER:
            reason = worker_veto(command)
            if reason:
                respond(
                    "deny",
                    f"blocked by the leash: {reason} is not something a worker does. "
                    "The supervised session reviews it and merges it.",
                )
            if LEASH in command and any(
                re.search(p, command) for p in writing_patterns(root)
            ):
                respond(
                    "deny",
                    "blocked by the leash: a worker does not write to an agent's "
                    "configuration, not even its own repository's. That is where the "
                    "leash that holds it lives, and it is read from disk on every "
                    "call. The supervised session does it.",
                )
        if re.search(r"\bgit\s+push\b", command):
            respond(*decide_push(command))
        foreign = foreign_paths(command, house, root)
        if foreign and any(re.search(p, command) for p in writing_patterns(root)):
            respond(
                "deny",
                "blocked by the leash: writing into another repository's house "
                f"({foreign[0]}). Reading yes; writing, no.",
            )
        respond("allow", "command with no forbidden operation inside")

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = data.get("file_path", "") or ""
        if path.endswith(".env") or "/.env" in path:
            respond("deny", "blocked by the leash: secrets file")
        # Before the "its own house" door: for a worker in the repository that
        # holds the leash, the leash IS in its own house.
        if IS_WORKER and LEASH in path:
            respond(
                "deny",
                "blocked by the leash: a worker does not write to an agent's "
                "configuration, not even its own repository's. That is where the "
                "leash that holds it lives. The supervised session does it.",
            )
        if in_house(path, house, cwd):
            respond("allow", "write inside its own house")
        respond(
            "deny",
            "blocked by the leash: writing into another repository's house. "
            "Reading yes; writing, no.",
        )

    # Anything else — other MCP servers, agents, web — follows the normal path.
    sys.exit(0)


if __name__ == "__main__":
    # Same rule for an internal failure as for unreadable input: if the guard
    # breaks, it closes. A hook that blows up and lets things through is worse
    # than no hook, because now people believe there is a door.
    #
    # `respond` exits with SystemExit, which does not inherit from Exception, so
    # ordinary denials do not land here.
    try:
        main()
    except Exception as e:
        respond(
            "deny",
            f"blocked by the leash: it broke while deciding ({type(e).__name__}: {e}). "
            "Closed by default. Fix it before continuing; do not route around it.",
        )
