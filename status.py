"""The data model for a Claude/Terry swimlane board, stored as ONE JSON file.

**RFC 2119 keywords, and the capitals are load-bearing.** MUST and MUST NOT are
absolute. SHOULD is a strong default a good argument may overrule. MAY is optional.

## Why JSON and not the markdown table this replaced

**Terry, 2026-08-18: *"'database' needs to be a JSON file, not md."*** He is applying
his own standing order -- JSON by default for structured data -- and the markdown
version had already started paying for the exception.

**A table makes every reader re-derive the record from text.** The parser it needed
grew `OPEN_RE`, `LANDED_RE`, a suspect-line detector for each, a renumbering pass, and
a migration the day a column was added. **Every one of those exists only because the
storage format had no types.** The whole family is deleted here.

**Two defects the format itself caused, both on its first day:**

  * A Landed row was written `2026-08-18 12:40 ET` while the regex demanded a bare
    date, so **the file recorded a signoff and every reader reported zero.** Silent.
  * Row numbers were positional, so signing one off renumbered the rest, and the
    harness panel matched by position.

**Stable ids fix the second and JSON fixes the first.**

## The shape

```json
{
  "schema": 1,
  "project": "FlickrGroupAddr backend-api",
  "items": [
    {
      "id": "argparse-gate",
      "priority": "P2",
      "state": "ready_for_review",
      "subject": "Convert Python sys.argv handling to argparse",
      "detail": "Free prose. Inline markdown only.",
      "history": [
        {"at": "2026-08-18T09:14:00-04:00", "to": "in_progress", "by": "claude"}
      ]
    }
  ]
}
```

**`id` is stable and MUST NOT be reused.** It is how the board, the harness panel and
any future consumer agree on which card is which, and it survives reordering, renaming
and signoff. **A renumber is no longer a thing that can happen.**

**`history` is what makes "append-only" REAL rather than a convention.** The markdown
version promised a permanent record and enforced it by asking Claude nicely. Here every
state change appends an entry naming the time and the actor, and nothing removes one.

**`by` is the attribution Trello could not give us.** Its OAuth grant authenticates
Claude AS Terry, so a card he moved and a card Claude moved were the same event by the
same member -- which is why that route was abandoned the same afternoon. **This server
binds to loopback, so a drag IS Terry**, and the field can be trusted.
"""

import argparse
import datetime
import json
import pathlib
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict

SCHEMA = 1

# **P0 is on fire. P5 is only if there is nothing else.** Terry's scale, verbatim:
# *"P0 is [...] on fire emergency and P5 is 'only if you have nothing else to
# work'."* Six levels rather than three, because he wanted room to rank a long
# backlog without every item collapsing to "medium".
PRIORITIES: tuple[str, ...] = ("P0", "P1", "P2", "P3", "P4", "P5")

PRIORITY_LABEL: dict[str, str] = {
    "P0": "On fire",
    "P1": "Urgent",
    "P2": "High",
    "P3": "Normal",
    "P4": "Low",
    "P5": "Only if idle",
}

DEFAULT_PRIORITY = "P3"

# **The lanes, left to right.** Terry named this order himself on 2026-08-18:
# *"backlog; ready for claude; in progress; needs terry action; blocked; ready for
# review; completed."*
#
# **It reads as a pipeline and the middle two are a detour.** Work flows left to
# right; `needs_terry_action` and `blocked` are where it stalls, and putting them
# in-line rather than off to one side is what makes a stalled card impossible to miss.
LANES: tuple[tuple[str, str], ...] = (
    ("backlog", "Backlog"),
    ("ready_for_claude", "Ready for Claude"),
    ("in_progress", "In progress"),
    ("needs_terry_action", "Needs Terry"),
    ("blocked", "Blocked"),
    ("ready_for_review", "Ready for review"),
    ("completed", "Completed"),
)

STATES: tuple[str, ...] = tuple(state for state, _ in LANES)

# **THREE STATES MEAN "NOT MOVING", AND TERRY DREW THE LINES HIMSELF.** They get
# confused constantly, and the whole value of the board is that a stalled card says
# WHO is holding it.
#
# | State | Who can move it | Terry, 2026-08-18 |
# |---|---|---|
# | `needs_terry_action` | **Terry** | *"that's 'need a judgement call'"* |
# | `blocked` | **Nobody** | *"neither of us can action it (eg 'awaiting license key')"* |
# | `ready_for_review` | **Terry** | Claude finished. Waiting on the signoff |
#
# **`blocked` MUST NOT be used for "waiting on a decision" and MUST NOT be used for
# "hard".** If Terry could unstick it by answering, it is `needs_terry_action`.

Actor = Literal["terry", "claude"]

# **PERMISSION IS PER LANE PER DIRECTION: who may move a card IN, and who may move it
# OUT.** Terry proposed this on 2026-08-18: *"Should we set a 'move in' and 'move
# out'? eg 'Ready for claude' is 'terry move in from Backlog' and 'claude move out'."*
#
# **It replaced a single owner per lane, and the boundary lanes are why.** His words:
# *"it makes it cleaner on the boundary lanes like review."* One owner cannot describe
# `ready_for_review` -- Claude fills it and Terry empties it -- so the first model
# needed a third pseudo-owner called `handoff` and a special case in the edge
# derivation. **A model needing a special case for its most important lane is the
# wrong model.**
#
# **Three things fall out rather than being written down:**
#
#   * **A handoff lane is simply one whose `in` and `out` differ.** There are two, in
#     opposite directions, and the earlier model only expressed one of them.
#   * **`completed` needs no terminal flag.** Its `out` is empty, so nothing leaves.
#     Append-only becomes a consequence instead of a rule somebody must honor.
#   * **Withdrawing a card is possible.** With a single actor per direction Terry
#     could queue work and never take it back; `out` is a SET for exactly that case.
class LaneRules(NamedTuple):
    """Who may CREATE a card here, who may move one IN, and who may move one OUT.

    `inbound` and `outbound` map the OTHER lane to the actors allowed on that edge.
    **Naming the other lane is what an actor set alone could not do**: it is the
    difference between *"Terry may take cards out of Backlog"* and *"Terry may
    promote a Backlog card to Ready for Claude, and nowhere else."*

    **Every edge is therefore declared TWICE, once from each end**, and
    `check_edges()` refuses to let the two halves disagree. That redundancy is
    deliberate: Terry reasons one lane at a time, so the table is written the way he
    thinks, and the machine catches what that costs. **It fired on the second lane he
    specified**, which is the argument for keeping it.
    """

    create: frozenset[str]
    inbound: dict[str, frozenset[str]]
    outbound: dict[str, frozenset[str]]


TERRY = frozenset({"terry"})
CLAUDE = frozenset({"claude"})
NOBODY: frozenset[str] = frozenset()

# **THE PERMISSION TABLE. Terry dictated it lane by lane on 2026-08-18.**
#
# **A cell marked DRAFTED is Claude's proposal, not his instruction.** He specified
# `backlog` and `ready_for_claude` and said *"I figured I'd miss some state machine
# edges"*; the rest are derived from constraints those two impose, and he corrects
# whatever is wrong.
RULES: dict[str, LaneRules] = {
    # **Terry, verbatim:** *"backlog: create=claude, in=terry: from ready for claude,
    # from ready for review. out=terry: to ready for claude."*
    #
    # **`create` is a third verb and it matters.** A new card is not a move, so it
    # needs its own permission.
    #
    # **CREATE IS ALWAYS CLAUDE AND NEVER TERRY, and that is not an oversight.**
    # Terry, 2026-08-18: *"create is always claude only because I want this chat to be
    # how I task you."* The board is where work is TRACKED; the conversation is where
    # work is ASSIGNED. A card he typed into a web form would be a second inbox.
    "backlog": LaneRules(
        create=CLAUDE,
        inbound={"ready_for_claude": TERRY, "ready_for_review": TERRY},
        outbound={"ready_for_claude": TERRY},
    ),
    # **Terry, verbatim:** *"ready for claude: create=none, in=terry, from backlog,
    # from blocked, from ready for review. out=claude, to (three lanes claude owns)."*
    #
    # **`create` was then widened to CLAUDE**, same day: *"can start in either backlog
    # or ready for claude I guess."* So a card he tasks in chat is filed straight into
    # the queue rather than parked in the backlog and immediately promoted.
    #
    # **CREATING here is not PROMOTING here, and the difference is the whole reason
    # this is safe.** Claude filing a card transcribes something Terry just said;
    # moving one in is a ranking decision, and `backlog -> ready_for_claude` stays
    # TERRY-only. Claude can never advance its own backlog.
    #
    # **It also keeps the audit trail honest** -- his observation: *"so then there'd
    # be audit trail of ready for claude to in progress."* A card created here and
    # then picked up records two events with times. A card created straight into
    # `in_progress` would materialize mid-flight with no pickup to point at, which is
    # why no working lane permits `create`.
    #
    # **The `terry -> backlog` edge is his Backlog spec's other half.** He declared it
    # from the Backlog end and omitted it here; the cross-check caught the mismatch
    # and he confirmed the withdraw should exist.
    "ready_for_claude": LaneRules(
        create=CLAUDE,
        inbound={"backlog": TERRY, "blocked": TERRY, "ready_for_review": TERRY},
        outbound={"in_progress": CLAUDE, "needs_terry_action": CLAUDE,
                  "blocked": CLAUDE, "backlog": TERRY},
    ),
    # DRAFTED. Claude's working lane. Nothing returns to the backlog from here --
    # abandoning work means saying so, not silently re-filing it.
    "in_progress": LaneRules(
        create=NOBODY,
        inbound={"ready_for_claude": CLAUDE, "needs_terry_action": CLAUDE,
                 "blocked": CLAUDE, "ready_for_review": CLAUDE},
        outbound={"needs_terry_action": CLAUDE, "blocked": CLAUDE,
                  "ready_for_review": CLAUDE},
    ),
    # DRAFTED. **Terry answers in CHAT and Claude moves the card.** He does not drag
    # this one: the card is Claude's bookkeeping about what it is waiting for.
    "needs_terry_action": LaneRules(
        create=NOBODY,
        inbound={"in_progress": CLAUDE, "ready_for_claude": CLAUDE,
                 "blocked": CLAUDE},
        outbound={"in_progress": CLAUDE, "blocked": CLAUDE,
                  "ready_for_review": CLAUDE},
    ),
    # DRAFTED, and **it is NOT a pure Claude lane after all.** Terry's
    # `ready_for_claude` spec says a card may arrive there *from blocked*, which is
    # Terry taking it out of here -- so `terry -> ready_for_claude` is required, and
    # the lane's "read-only for you" label would have been a lie.
    "blocked": LaneRules(
        create=NOBODY,
        inbound={"in_progress": CLAUDE, "ready_for_claude": CLAUDE,
                 "needs_terry_action": CLAUDE},
        outbound={"in_progress": CLAUDE, "needs_terry_action": CLAUDE,
                  "ready_for_review": CLAUDE, "ready_for_claude": TERRY},
    ),
    # **The signoff lane. Terry, 2026-08-18:** *"claude can pull BACK from ready for
    # terry review but Terry is only one that is ready to review out -> completed."*
    #
    # **So the lane has two kinds of exit and only one of them is his.** Claude may
    # retract work it decides is not actually finished; Terry signs it off, sends it
    # back to the queue, or drops it to the backlog.
    #
    # **`completed` is the single edge Claude MUST NOT take**, and that prohibition is
    # now expressed by one missing actor rather than by a rule somebody remembers.
    # It exists because Claude marked its own work complete twice on 2026-08-18 and
    # was wrong both times. **A worker who signs off their own work has no reviewer.**
    "ready_for_review": LaneRules(
        create=NOBODY,
        inbound={"in_progress": CLAUDE, "needs_terry_action": CLAUDE,
                 "blocked": CLAUDE},
        outbound={"completed": TERRY, "backlog": TERRY, "ready_for_claude": TERRY,
                  "in_progress": CLAUDE},
    ),
    # DRAFTED. **Terminal, and it needs no flag saying so** -- an empty `outbound` IS
    # append-only, enforced by the same rule as everything else.
    "completed": LaneRules(
        create=NOBODY,
        inbound={"ready_for_review": TERRY},
        outbound={},
    ),
}


def check_edges() -> list[str]:
    """Every mismatch between the two declarations of an edge. Empty means consistent.

    **Callers MUST surface a non-empty result.** A table that contradicts itself will
    otherwise behave as whichever half a given code path happens to read, and the two
    halves are read by different code.
    """
    problems: list[str] = []
    for state, rules in RULES.items():
        for src, actors in rules.inbound.items():
            other = RULES.get(src)
            if other is None:
                problems.append(f"{state}.inbound names unknown lane {src!r}")
                continue
            mirror = other.outbound.get(state)
            if mirror is None:
                problems.append(
                    f"{state}.inbound has {src} -> {state} for {sorted(actors)}, "
                    f"but {src}.outbound does not mention {state}")
            elif mirror != actors:
                problems.append(
                    f"{src} -> {state}: inbound says {sorted(actors)}, "
                    f"outbound says {sorted(mirror)}")
        for dst, actors in rules.outbound.items():
            other = RULES.get(dst)
            if other is None:
                problems.append(f"{state}.outbound names unknown lane {dst!r}")
                continue
            if state not in other.inbound:
                problems.append(
                    f"{state}.outbound has {state} -> {dst} for {sorted(actors)}, "
                    f"but {dst}.inbound does not mention {state}")
    return problems


def may_move(actor: Actor, from_state: str, to_state: str) -> bool:
    """Whether `actor` may move a card along this one edge.

    **One lookup, because the edge names both ends.** The earlier model asked two
    questions -- may this actor leave that lane, may this actor enter this one --
    and answered yes to combinations nobody intended, `backlog -> completed` among
    them.
    """
    rules = RULES.get(from_state)
    return rules is not None and actor in rules.outbound.get(to_state, NOBODY)


def explain_refusal(actor: Actor, from_state: str, to_state: str) -> str:
    """Why this move is refused, naming the ACTUAL cause. Empty when it is allowed.

    **Terry wrote the target wording himself, 2026-08-18:** *"Terry does have out
    perms on ready for review but not where you tried to drop that card."*

    **A single "not allowed" flattens three different situations**, and the reader
    needs a different next action for each:

    | Cause | What the person should do |
    |---|---|
    | No `out` on this lane at all | Stop trying. The lane is not yours |
    | `out` yes, this destination no | Aim somewhere else, and here is where |
    | Nobody may take this edge | The edge does not exist; this is a modeling gap |

    **The middle one is the interesting case** and the one a generic message hides.
    """
    rules = RULES.get(from_state)
    if rules is None:
        return f"{from_state} is not a lane"
    if to_state not in RULES:
        return f"{to_state} is not a lane"
    if actor in rules.outbound.get(to_state, NOBODY):
        return ""

    who = actor.capitalize()
    allowed_here = sorted(dst for dst, actors in rules.outbound.items()
                          if actor in actors)
    if allowed_here:
        return (f"{who} has out permission on {from_state}, but not to {to_state}. "
                f"From here {actor} may go to: {', '.join(allowed_here)}")

    others = sorted({a for actors in rules.outbound.values() for a in actors})
    if others:
        return (f"{from_state} is not {actor}'s to move out of. "
                f"That belongs to: {', '.join(others)}")
    return f"Nothing moves out of {from_state}"


def may_create(actor: Actor, state: str) -> bool:
    """Whether `actor` may create a NEW card in this lane."""
    rules = RULES.get(state)
    return rules is not None and actor in rules.create


def edges_for(actor: Actor) -> frozenset[tuple[str, str]]:
    """Every move `actor` may make, derived from the two tables above.

    **Derived rather than listed.** A hand-written edge list would let it disagree
    with the permission tables, which is the exact class of bug this file exists to
    prevent.
    """
    return frozenset((a, b) for a in STATES for b in STATES
                     if a != b and may_move(actor, a, b))


# **What the BROWSER may do**, re-checked server-side on every request. It is a guard
# rail rather than a security boundary: the server binds to loopback, so whoever
# reaches it is already Terry. The point is that a mis-drop cannot silently rewrite a
# status Claude is responsible for.
TERRY_EDGES: frozenset[tuple[str, str]] = edges_for("terry")
CLAUDE_EDGES: frozenset[tuple[str, str]] = edges_for("claude")


def actors_in(state: str) -> frozenset[str]:
    """Every actor who may move a card INTO this lane, from anywhere.

    **A summary for display, never a permission check.** `may_move` asks about one
    edge; this collapses all of them and would happily say "Terry" about a lane he
    can only reach from one specific place.
    """
    rules = RULES.get(state)
    if rules is None:
        return NOBODY
    return frozenset().union(*rules.inbound.values()) if rules.inbound else NOBODY


def actors_out(state: str) -> frozenset[str]:
    """Every actor who may move a card OUT of this lane, to anywhere."""
    rules = RULES.get(state)
    if rules is None:
        return NOBODY
    return frozenset().union(*rules.outbound.values()) if rules.outbound else NOBODY


def lane_class(state: str) -> str:
    """A coarse class for styling: `terry`, `claude`, `handoff` or `done`.

    **Derived from the permission tables, never stored.** It is a rendering detail,
    and the earlier model's mistake was treating this label as the SOURCE of the
    rules rather than a summary of them.

    A lane whose `in` and `out` name different sole actors IS a handoff -- that is
    the definition rather than a list, so `ready_for_claude` and `ready_for_review`
    both qualify without either being special-cased.
    """
    rules = RULES.get(state)
    if rules is None:
        return "done"
    into = actors_in(state)
    out = actors_out(state)
    if not out:
        return "done"
    if into != out:
        return "handoff"
    return "claude" if into == {"claude"} else "terry"


def lane_owner_label(state: str) -> str:
    """`IN: x · OUT: y`, which is what a lane header shows.

    **Two halves rather than one word.** Terry asked for *"real clear ownership per
    lane"*, and a single label is exactly what fails on a boundary lane -- calling
    `ready_for_review` "Claude's" or "Terry's" is wrong either way.
    """
    def who(names: frozenset[str]) -> str:
        if not names:
            return "nobody"
        return " + ".join(n.capitalize() for n in sorted(names))

    # `IN` is the interesting half for Terry, so it leads.
    return f"IN: {who(actors_in(state))}  ·  OUT: {who(actors_out(state))}"


# **One entry in an item's history. Appended, never edited, never removed.**
#
# **Declared functionally because `from` is a Python keyword** and cannot be a class
# attribute. The JSON key is worth the awkwardness: `{"from": "in_progress", "to":
# "ready_for_review"}` reads as an event, where `from_state` would read as a variable
# somebody chose.
#
# **`from` is ABSENT on a creation entry**, which is how a reader tells the two apart
# without a type field.
Change = TypedDict("Change", {
    "at": str,
    "to": str,
    "by": str,
    "from": NotRequired[str],
})


class ItemCore(TypedDict):
    """The three fields `load` GUARANTEES, so the type says what the validator does.

    **Splitting required from optional is not pedantry.** With everything optional,
    `item["state"]` is a possible `KeyError` at every use site and the typechecker
    says so at every one of them -- which trains a reader to ignore it. These three
    are refused at load time, so they are required here.
    """

    id: str
    state: str
    subject: str


class Item(ItemCore, total=False):
    priority: str
    detail: str
    history: list[Change]


class Board(TypedDict):
    schema: int
    project: str
    items: list[Item]


class Lane(NamedTuple):
    state: str
    label: str
    owner: str
    owner_label: str
    items: list[Item]


class BoardError(ValueError):
    """The file is not a board this version understands."""


def now() -> str:
    """An ISO 8601 stamp in this machine's local zone, offset included.

    **Local rather than UTC, and the offset is what makes that safe.** Terry reads
    these; a UTC stamp would make him do arithmetic to answer *"did I sign that off
    before dinner"*. The offset keeps it unambiguous for anything that parses it.
    """
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load(path: pathlib.Path) -> Board:
    """Read and VALIDATE a board file.

    **It refuses rather than repairs.** A board with an unknown state or a duplicate
    id is a bug in whatever wrote it, and silently normalizing would hide that --
    which is precisely how the markdown version lost a signoff.
    """
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        raw: Any = json.load(fh)

    if not isinstance(raw, dict):
        raise BoardError(f"{path}: top level is {type(raw).__name__}, expected an object")
    if raw.get("schema") != SCHEMA:
        raise BoardError(f"{path}: schema {raw.get('schema')!r}, this build reads {SCHEMA}")
    items = raw.get("items")
    if not isinstance(items, list):
        raise BoardError(f"{path}: 'items' is missing or not a list")

    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"{path}: items[{index}]"
        if not isinstance(item, dict):
            raise BoardError(f"{where} is not an object")
        for field in ("id", "state", "subject"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise BoardError(f"{where} has no {field}")
        if item["id"] in seen:
            raise BoardError(f"{where}: duplicate id {item['id']!r}")
        seen.add(item["id"])
        if item["state"] not in STATES:
            raise BoardError(f"{where}: unknown state {item['state']!r}")
        pri = item.get("priority", DEFAULT_PRIORITY)
        if pri not in PRIORITIES:
            raise BoardError(f"{where}: unknown priority {pri!r}")

    return {
        "schema": SCHEMA,
        "project": str(raw.get("project", "")),
        "items": items,
    }


def save(board: Board, path: pathlib.Path) -> None:
    """Write the board, formatted so a git diff shows one changed field per line.

    **`indent=2` and a trailing newline are not cosmetic.** A single-line JSON file
    turns every edit into one enormous diff, which throws away the reason the record
    lives in git at all.
    """
    text = json.dumps(board, indent=2, ensure_ascii=False) + "\n"
    with pathlib.Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def rank(item: Item) -> int:
    """Sort key within a lane. An unknown priority sorts last rather than raising."""
    pri = item.get("priority", DEFAULT_PRIORITY)
    return PRIORITIES.index(pri) if pri in PRIORITIES else len(PRIORITIES)


def lanes(board: Board) -> list[Lane]:
    """One `Lane` per column, each sorted by priority then by file order.

    **Priority orders WITHIN a lane and nothing else.** Terry: *"the cards will be
    priority order per swimlane, so claude knows to work top down."* So the top card
    of `Ready for Claude` is the next thing to pick up.

    **`sorted` is stable**, so equal priorities keep the order the file gives them,
    and the file itself is the tiebreak.
    """
    buckets: dict[str, list[Item]] = {state: [] for state in STATES}
    for item in board["items"]:
        buckets.setdefault(item["state"], []).append(item)
    return [Lane(state, label, lane_class(state), lane_owner_label(state),
                 sorted(buckets.get(state, []), key=rank))
            for state, label in LANES]


def find(board: Board, item_id: str) -> Item:
    for item in board["items"]:
        if item["id"] == item_id:
            return item
    raise BoardError(f"no item with id {item_id!r}")


def create(  # noqa: PLR0913 -- see below
    board: Board,
    item_id: str,
    subject: str,
    state: str,
    by: Actor,
    *,
    priority: str = DEFAULT_PRIORITY,
    detail: str = "",
) -> str:
    """Add a new card, with the first history entry already on it.

    **`PLR0913` is suppressed on purpose, and the suggested fix would be worse.**
    Seven parameters is over ruff's threshold of five; collapsing them into a dict
    or a builder object would move the field names off the call site, where they are
    the only thing making `create(board, "obe-lane", "Decide...", "backlog",
    "claude")` readable. **The last two are keyword-only**, so the positional count
    is five and the rule's real concern -- a long unlabeled argument list -- does not
    apply.

    **Creation is an EVENT, not an initial condition.** Without an entry here the
    trail starts one move late, and a card's earliest record would be the day
    somebody happened to touch it rather than the day it was raised.

    **`from` is absent on this entry**, which is exactly how a reader tells creation
    from a move. Every later entry names where the card came from.
    """
    if not may_create(by, state):
        raise BoardError(f"{by} may not create in {state!r}")
    if item_id in {item["id"] for item in board["items"]}:
        raise BoardError(f"duplicate id {item_id!r}")
    if priority not in PRIORITIES:
        raise BoardError(f"unknown priority {priority!r}")
    board["items"].append({
        "id": item_id,
        "priority": priority,
        "state": state,
        "subject": subject,
        "detail": detail,
        "history": [{"at": now(), "to": state, "by": by}],
    })
    return f"created {item_id} in {state} (by {by})"


def move(board: Board, item_id: str, to_state: str, by: Actor) -> str:
    """Move one item, appending to its history. Returns a one-line description.

    **Raises rather than returning a falsy value.** A drag that appears to work and
    changes nothing is the worst outcome available here.
    """
    if to_state not in STATES:
        raise BoardError(f"unknown state {to_state!r}")
    item = find(board, item_id)
    was = item["state"]
    if was == to_state:
        return f"{item_id} is already {to_state}"
    # **THE PERMISSION CHECK LIVES HERE, not in the callers.** The first version had
    # it only in the server's POST handler, so the browser was guarded and the
    # library was not -- and the library is what CLAUDE uses. A test written the same
    # hour walked a card `ready_for_review -> completed` as `claude` and then
    # `completed -> in_progress` as `terry`: the one edge Claude must never take, and
    # a breach of append-only, both accepted in silence.
    #
    # **A guard that protects only the path you were thinking about protects nothing.**
    if not may_move(by, was, to_state):
        raise BoardError(explain_refusal(by, was, to_state))
    item["state"] = to_state
    entry: Change = {"at": now(), "from": was, "to": to_state, "by": by}
    item.setdefault("history", []).append(entry)
    return f"{item_id}: {was} → {to_state} (by {by})"


def main() -> None:
    """A small CLI, mostly so the file can be sanity-checked without the server."""
    ap = argparse.ArgumentParser(description="Inspect a claude-status board file.")
    ap.add_argument("board", type=pathlib.Path, help="path to the board JSON")
    ap.add_argument("--json", action="store_true", help="dump the parsed board")
    args = ap.parse_args()

    board = load(args.board)
    if args.json:
        print(json.dumps(board, indent=2, ensure_ascii=False))
        return

    print(f"{board['project']}  ({len(board['items'])} items)")
    for lane in lanes(board):
        if not lane.items:
            continue
        print(f"\n  {lane.label}  [{lane.owner_label}]  {len(lane.items)}")
        for item in lane.items:
            pri = item.get("priority", DEFAULT_PRIORITY)
            print(f"    {pri}  {item['subject']}")


if __name__ == "__main__":
    main()
