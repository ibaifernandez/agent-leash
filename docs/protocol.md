# The work protocol

`agent-leash` enforces one rule in code — a worker cannot merge its own work —
and that rule only makes sense inside a protocol that says where a worker comes
from and who does merge.

This is that protocol. It is prose, not code: nothing here is executable, and
the kernel does not read it. It is here because `LEASH_ROLE=worker` is
otherwise a loose end.

It has run a four-role crew across ten repositories. Take what transfers.

---

## The idea, in one sentence

**Every layer touches the work.** Nobody judges by reading the report of the
layer below: a report is the list of things to go and check.

That is what stops the infinite regress of reviewers. The chain does not end by
adding another reviewer above — **it ends when somebody measures**.

---

## The four roles

Each role reads its own brief and the shared rules. **None of them needs to know
what the others do**; they are named only as destinations.

| Role | Owns | Never does |
|---|---|---|
| **Worker** | building; the card it claimed | merge, create cards, certify its own work |
| **Reviewer** | measuring one exact commit, then closing delivery | build the thing it measures |
| **Planner** | turning findings into cards; the only role that creates them | build, merge, certify finished work |
| **Auditor** | measuring work that is already live, one domain at a time | build; it audits, then routes |

Plus **the Operator** — the human. Who executes what no automated role can, and
decides what no automated role may.

### Two things fixed by convention

- **The worker is a service account.** Anything assigned to it is worked by an
  automated agent. Anything assigned to a human is touched by nobody automatic.
- **Agent branches carry a reserved prefix.** No human branch uses it, so
  force-pushing over that prefix cannot land on anyone's work. `agent-leash`
  reads this prefix from `LEASH_BRANCH`.

---

## The board columns are the protocol

Each column is **a state with an owner**. Reading the board tells you who holds
what right now, without asking anyone.

| Column | Who holds it | What it means |
|---|---|---|
| **Backlog** | nobody | claimable. Nothing here is in anyone's hands |
| **In progress** | the worker | this card is theirs — first time or returned |
| **To triage** | the planner | their inbox: a premise, a finding or an outcome that has to become a work decision |
| **Ready for review** | nobody | built, not yet measured |
| **In review** | the reviewer | measuring one exact commit |
| **Approved** | the reviewer | that exact commit passed. Delivery still has to close, without measuring again while it stays the same commit |
| **Operator decides** | the human | paused for something only they can give, with the exact command, how to verify it and where it resumes written inside |
| **Done** | nobody | merged **and live**, pending post-production audit |
| **Audited** | nobody | the auditor measured the live work, every finding from that pass was resolved or externalized, **and whatever could not be measured is written inside with its literal error** |

**Priority is a field on the card, not a column.**

---

## Three transitions that admit no interpretation

### 1 · Claiming work is an exclusive acquisition

`Backlog → In progress`. Seeing the card free is not enough: the worker starts
only once it has *won* the transition and sees the card under its own name.

If two runs arrive at once, **only one can win**. The one that lost builds
nothing — it reads the board again.

Without this, two agents build the same card and one of them wastes a full turn
discovering the other already did it.

### 2 · An approval belongs to a commit, not to a card

When the reviewer finishes a positive review it writes down **the exact SHA it
measured**.

Approved does not yet mean done. It means the measurement is finished and does
not have to be repeated **while the commit stays exactly the same**.

Before closing delivery the reviewer checks, in this order:

1. the pull request's head is still the approved SHA;
2. the checks the repository requires for that PR ran **on that commit** and are
   green;
3. it merges;
4. **the commit that landed on the main branch has those same checks green.** A
   squash merge creates a new object: the branch's checks do not travel with it;
5. the work is actually live.

If the SHA changed, or a required check stopped holding green, **the approval
expires** and the card goes back to review.

> **Done requires live, not merged.** A green pipeline is not a running system.

### 3 · A human pause carries its exit, written before it enters

`Operator decides` is not a closing condition. It is a pause, because there is an
action no automated role can execute or a decision none may take.

Nothing enters without its exit written:

- **An operator action** → the exact command, how to verify it afterwards, and
  **the exact column it returns to**. The operator executes and hands it back;
  **they do not certify their own result**.
- **An irreversible decision** → what is proposed, what evidence justifies it,
  and **what happens both if the human accepts and if they refuse**.

It never means "the human works out what to do". The decision arrives prepared.

---

## Invariants

These are the lines the rest of the protocol exists to protect.

- **Zero inference. Measure against the source.** No summary counts — not a pull
  request's, not another role's.
- **Nobody merges their own work**, not even if asked to. *This one is enforced
  in code by `agent-leash`, because it is the only invariant a worker can break
  silently and alone.*
- **No merge unless the checks the repository requires for that PR ran on the
  commit being merged and are all green.**
- **An approval is valid for one exact SHA.** If the commit changes, it expires.
  If it does not change, passing through another inbox does not by itself
  require measuring again.
- **One card, one branch, one pull request**, dead on merge.
- **One closing condition per card, also after it is born.** The bar was met at
  creation and broken afterwards: each review round added conditions to the same
  card instead of expelling the finding into a new one. A card with three things
  inside can never close.
- **Only the planner creates cards**, and only after checking against the code or
  the authoritative source. Everyone else updates the one in their hand.
- **A side finding does not erase an approval or stop a merge.** If the reviewer
  measured the original work correctly and finds something else in passing, it
  routes it out and merges. Stopping correct work over a defect that already
  existed leaves the bigger thing live in production. It stops only if the
  finding shows *this* work introduces a new falsehood worse than the one it
  removes — and then it is not a side finding, it is the unmet condition.
- **Live work does not pretend its pull request is still open.** Post-production
  findings become new work, including when they belong to a different card than
  the one where they were measured.

---

## Messages between roles

A message is **a wake-up**: it exists so somebody looks at something that is
theirs and does not yet know it is there. **It is not correspondence.**

**The message does not carry the work inside — it says what to look at and
where.** Losing a message costs latency, not work: the work lives on the board.

**Board state does not travel inside either.** How many cards there are, at what
priority, in which column: that is consulted, not quoted. A count written into a
message **is already stale when it is read**, however fresh it was when written.

**The test, and it comes before the list:** does this message change what the
other role does next? If yes, it is a wake-up. If no, it is a status report, and
there are no status reports.

Three things that are not reasons, however much they look like it:

- **That somebody wrote to you.** A received message **creates no duty to
  reply**. If nothing is left in anyone's inbox, **silence is the correct
  answer** — what you did is already written on the card.
- **Telling people what you did.** That is a status report.
- **Warning somebody not to continue.** Writing "don't go on" to the role that
  holds the clock **is pushing it**. What should not start is left alone.

---

## How the crew wakes up, and how it stops

**Only the worker has a clock.** If all four had one, all four would wake every
quarter of an hour to discover they have nothing to do.

The worker starts the chain: if it has nothing, it calls the reviewer; if the
reviewer has nothing, it calls the planner. If the planner has nothing either,
**everything shuts down** until the next tick.

**It stops on what it measures, not on how many times it has looked.** When
nothing is claimable **and** a full pass of the chain comes back empty, the
worker stops it **and says so**. Without that last part, "stopped because there
was nothing" and "dead" look identical from outside.

There is a hard cap on passes as a fuse, not as the rule. If the fuse blows,
something is wrong — and that is said too.

---

## What this protocol does not solve

It assumes **one operator** and a fleet of repositories that person owns. Roles
are model instances, not people, so "a second pair of eyes" means a second
context, not a second human. That is weaker than human review and it should be
said out loud rather than implied.

It has no answer for two humans disagreeing, for on-call, or for anything
touching a customer in real time.

And the honest limit: the invariants above are prose. Exactly one of them —
*nobody merges their own work* — is enforced by a program. The rest hold because
the roles read them.

That asymmetry is the reason `agent-leash` exists, and the reason this document
sits next to it rather than instead of it.
