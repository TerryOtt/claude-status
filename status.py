"""The data model for a Claude/Terry swimlane board, stored as ONE JSON file.

**RFC 2119 keywords, and the capitals are load-bearing.** MUST and MUST NOT are
absolute. SHOULD is a strong default a good argument may overrule. MAY is optional.

## Why JSON, and why dataclasses over the JSON

**Terry, 2026-08-18: *"'database' needs to be a JSON file, not md."*** He is applying
his own standing order -- JSON by default for structured data -- and the markdown table
this replaced had already started paying for the exception.

**A table makes every reader re-derive the record from text.** The parser it needed grew
`OPEN_RE`, `LANDED_RE`, a suspect-line detector for each, a renumbering pass and a
migration the day a column was added. **Every one of those existed only because the
storage had no types**, and it lost a signoff silently on its first real use.

**Then `TypedDict` was tried and dropped the same afternoon.** Every optional key makes
every access unsafe, so pyright objected at each use site and two of them were papered
over with `.get`. **A type that makes the checker cry wolf trains you to ignore it.**
Terry: *"how do you feel about Python dataclasses?"* -- and then *"yeah I found I loved
them too."* A field with a default simply exists.

## The three things a dataclass bought that the dict did not

  * **Defaults that apply.** `priority: str = DEFAULT_PRIORITY` needs no `.get`.
  * **Validation at one boundary.** `from_json` is the only place a malformed board can
    enter, so everything past it is known-good.
  * **Methods where the data is.** `Item.current_state()` recomputes state from history,
    which is what makes `verify()` possible at all.

## HOW THE PERMISSION MODEL IS ENFORCED, and why there is no state machine library

**`move()` checks `RULES` and raises.** That covers every caller that uses the API.

**`verify()` covers the ones that do not.** Python cannot stop `item.state = "completed"`,
and a guard that only protects the path you thought of protects nothing -- proven here
the same day, when the check lived in the server's POST handler and the library that
Claude uses was wide open. So `verify()` REPLAYS each item's history and refuses a board
whose stored state disagrees with its own audit trail. **A hand edit to the JSON is
caught by the same mechanism**, because the trail is the authority.

**`python-statemachine` 3.2.1 was surveyed and refused**, and the survey is recorded in
the README. It is genuinely good -- zero runtime dependencies, pushed the day before --
but the transition table here is already declarative data, `explain_refusal` writes
better errors than a generic guard, and the library cannot stop a direct attribute write
either. **It would have added a dependency and moved the rules, not enforced them.**
"""

import argparse
import datetime
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Literal, Self

# **`dataclasses.asdict` was considered and REFUSED**, which is why every class here
# hand-writes `to_json`. `asdict` walks nested dataclasses blindly: it would emit `frm`
# instead of `from`, and it would write an empty `comments` list onto every card.
# **The file is JSON so a person can read the diff**, and that is worth two dozen lines.

SCHEMA = 1

# **The default port, and it is a DEFAULT rather than the port.** Terry, 2026-08-18:
# *"I want per-project config JSON that includes TCP port num; I want to be able to
# bookmark one board per project."*
#
# **A shared port is worse than a dead bookmark.** With every project on 8792, a
# bookmark opens whichever board happens to be running -- so the failure mode is
# reading the WRONG project's work and believing it. A port per project means the
# bookmark either shows your board or shows nothing, and nothing is honest.
DEFAULT_PORT = 8792

# The TCP port range, named so the validation reads as a rule rather than as two
# numbers somebody typed.
MIN_PORT, MAX_PORT = 1, 65535

# **P0 is on fire. P5 is only if there is nothing else.** Terry's scale, verbatim:
# *"P0 is [...] on fire emergency and P5 is 'only if you have nothing else to work'."*
# Six levels rather than three, because he wanted room to rank a long backlog without
# every item collapsing to "medium".
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

# **The lanes, left to right.** Terry named this order himself: *"backlog; ready for
# claude; in progress; needs terry action; blocked; ready for review; completed."*
#
# **It reads as a pipeline and the middle two are a detour.** Work flows left to right;
# `needs_terry_action` and `blocked` are where it stalls, and putting them in-line
# rather than off to one side is what makes a stalled card impossible to miss.
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
LANE_LABEL: dict[str, str] = dict(LANES)

Actor = Literal["terry", "claude"]
ACTORS: tuple[str, ...] = ("terry", "claude")

# **THREE STATES MEAN "NOT MOVING", AND TERRY DREW THE LINES HIMSELF.** They get
# confused constantly, and the whole value of the board is that a stalled card says WHO
# is holding it.
#
# | State | Who can move it | Terry, 2026-08-18 |
# |---|---|---|
# | `needs_terry_action` | **Terry** | *"that's 'need a judgement call'"* |
# | `blocked` | **Nobody** | *"neither of us can action it (eg 'awaiting license key')"* |
# | `ready_for_review` | **Terry** | Claude finished. Waiting on the signoff |
#
# **`blocked` MUST NOT be used for "waiting on a decision" and MUST NOT be used for
# "hard".** If Terry could unstick it by answering, it is `needs_terry_action`.


@dataclass(frozen=True)
class LaneRules:
    """Who may CREATE a card here, who may move one IN, and who may move one OUT.

    `inbound` and `outbound` map the OTHER lane to the actors allowed on that edge.
    **Naming the other lane is what an actor set alone could not do**: it is the
    difference between *"Terry may take cards out of Backlog"* and *"Terry may promote a
    Backlog card to Ready for Claude, and nowhere else."*

    **Every edge is declared TWICE, once from each end**, and `check_edges()` refuses to
    let the halves disagree. Terry reasons one lane at a time, so the table is written
    the way he thinks and the machine catches what that costs. **It fired on the second
    lane he specified, and again on the first draft of one of Claude's.**
    """

    create: frozenset[str]
    inbound: dict[str, frozenset[str]]
    outbound: dict[str, frozenset[str]]


TERRY = frozenset({"terry"})
CLAUDE = frozenset({"claude"})
NOBODY: frozenset[str] = frozenset()

# **THE PERMISSION TABLE. Terry dictated it lane by lane on 2026-08-18**, in the shape
# he asked for: *"I like that the perms are (in/out, actor, source/dest)."*
RULES: dict[str, LaneRules] = {
    # **Verbatim:** *"backlog: create=claude, in=terry: from ready for claude, from
    # ready for review. out=terry: to ready for claude."*
    #
    # **CREATE IS ALWAYS CLAUDE AND NEVER TERRY, and that is not an oversight.**
    # *"create is always claude only because I want this chat to be how I task you."*
    # The board TRACKS work; the conversation ASSIGNS it. A card he typed into a web
    # form would be a second inbox.
    "backlog": LaneRules(
        create=CLAUDE,
        inbound={"ready_for_claude": TERRY, "ready_for_review": TERRY},
        outbound={"ready_for_claude": TERRY},
    ),
    # **Verbatim:** *"ready for claude: create=none, in=terry, from backlog, from
    # blocked, from ready for review. out=claude, to (three lanes claude owns)."*
    #
    # **`create` was then widened to CLAUDE:** *"can start in either backlog or ready
    # for claude I guess."*
    #
    # **CREATING here is not PROMOTING here, and the difference is why it is safe.**
    # Filing a card transcribes something Terry just said; moving one in is a ranking
    # decision, and `backlog -> ready_for_claude` stays TERRY-only. Claude can never
    # advance its own backlog.
    #
    # **It also keeps the trail honest** -- his observation: *"so then there'd be audit
    # trail of ready for claude to in progress."* A card created here and picked up
    # records two events. One created straight into `in_progress` would materialize
    # mid-flight with no pickup to point at, which is why no working lane permits
    # `create`.
    "ready_for_claude": LaneRules(
        create=CLAUDE,
        inbound={"backlog": TERRY, "blocked": TERRY, "ready_for_review": TERRY},
        outbound={"in_progress": CLAUDE, "needs_terry_action": CLAUDE,
                  "blocked": CLAUDE, "backlog": TERRY},
    ),
    "in_progress": LaneRules(
        create=NOBODY,
        inbound={"ready_for_claude": CLAUDE, "needs_terry_action": CLAUDE,
                 "blocked": CLAUDE, "ready_for_review": CLAUDE},
        outbound={"needs_terry_action": CLAUDE, "blocked": CLAUDE,
                  "ready_for_review": CLAUDE},
    ),
    # **Terry answers in CHAT and Claude moves the card.** He does not drag this one:
    # the card is Claude's bookkeeping about what it is waiting for.
    "needs_terry_action": LaneRules(
        create=NOBODY,
        inbound={"in_progress": CLAUDE, "ready_for_claude": CLAUDE, "blocked": CLAUDE},
        outbound={"in_progress": CLAUDE, "blocked": CLAUDE,
                  "ready_for_review": CLAUDE},
    ),
    # **NOT a pure Claude lane, and the cross-check is what revealed that.** His
    # `ready_for_claude` spec lets a card arrive *from blocked*, which is Terry taking
    # it out of here -- so `terry -> ready_for_claude` is required, and an earlier
    # "read-only for you" label on this lane would have been a lie.
    "blocked": LaneRules(
        create=NOBODY,
        inbound={"in_progress": CLAUDE, "ready_for_claude": CLAUDE,
                 "needs_terry_action": CLAUDE},
        outbound={"in_progress": CLAUDE, "needs_terry_action": CLAUDE,
                  "ready_for_review": CLAUDE, "ready_for_claude": TERRY},
    ),
    # **Terry, 2026-08-18:** *"claude can pull BACK from ready for terry review but
    # Terry is only one that is ready to review out -> completed."*
    #
    # **`completed` is the single edge Claude has no actor on**, so "Claude MUST NOT
    # sign off its own work" is a missing table entry rather than a rule somebody
    # remembers. It exists because Claude marked its own work complete twice on
    # 2026-08-18 and was wrong both times.
    "ready_for_review": LaneRules(
        create=NOBODY,
        inbound={"in_progress": CLAUDE, "needs_terry_action": CLAUDE,
                 "blocked": CLAUDE},
        outbound={"completed": TERRY, "backlog": TERRY, "ready_for_claude": TERRY,
                  "in_progress": CLAUDE},
    ),
    # **Terminal, and it needs no flag saying so** -- an empty `outbound` IS
    # append-only, enforced by the same rule as everything else.
    "completed": LaneRules(create=NOBODY, inbound={"ready_for_review": TERRY},
                           outbound={}),
}


class BoardError(ValueError):
    """The board, or a request against it, is not something this version accepts."""


def check_edges() -> list[str]:
    """Every mismatch between the two declarations of an edge. Empty means consistent.

    **Callers MUST surface a non-empty result.** A table that contradicts itself
    behaves as whichever half a given code path reads, and the two halves are read by
    different code.
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
            elif state not in other.inbound:
                problems.append(
                    f"{state}.outbound has {state} -> {dst} for {sorted(actors)}, "
                    f"but {dst}.inbound does not mention {state}")
    return problems


def may_move(actor: str, from_state: str, to_state: str) -> bool:
    """Whether `actor` may move a card along this one edge.

    **One lookup, because the edge names both ends.** An earlier model asked two
    separate questions -- may this actor leave that lane, may this actor enter this one
    -- and answered yes to combinations nobody intended, `backlog -> completed` among
    them.
    """
    rules = RULES.get(from_state)
    return rules is not None and actor in rules.outbound.get(to_state, NOBODY)


def may_create(actor: str, state: str) -> bool:
    """Whether `actor` may create a NEW card in this lane."""
    rules = RULES.get(state)
    return rules is not None and actor in rules.create


def explain_refusal(actor: str, from_state: str, to_state: str) -> str:
    """Why this move is refused, naming the ACTUAL cause. Empty when it is allowed.

    **Terry wrote the target wording himself:** *"Terry does have out perms on ready for
    review but not where you tried to drop that card."*

    **A single "not allowed" flattens three situations**, and each needs a different
    next action: the lane is not yours at all, it is yours but not to that destination,
    or nobody may take that edge. **The middle one is the interesting case and the one a
    generic message hides.**
    """
    rules = RULES.get(from_state)
    if rules is None:
        return f"{from_state} is not a lane"
    if to_state not in RULES:
        return f"{to_state} is not a lane"
    if actor in rules.outbound.get(to_state, NOBODY):
        return ""

    allowed_here = sorted(dst for dst, actors in rules.outbound.items()
                          if actor in actors)
    if allowed_here:
        return (f"{actor.capitalize()} has out permission on {from_state}, but not to "
                f"{to_state}. From here {actor} may go to: {', '.join(allowed_here)}")
    others = sorted({a for actors in rules.outbound.values() for a in actors})
    if others:
        return (f"{from_state} is not {actor}'s to move out of. "
                f"That belongs to: {', '.join(others)}")
    return f"Nothing moves out of {from_state}"


def edges_for(actor: str) -> frozenset[tuple[str, str]]:
    """Every move `actor` may make, derived from `RULES` rather than listed."""
    return frozenset((a, b) for a in STATES for b in STATES
                     if a != b and may_move(actor, a, b))


TERRY_EDGES: frozenset[tuple[str, str]] = edges_for("terry")
CLAUDE_EDGES: frozenset[tuple[str, str]] = edges_for("claude")


def actors_in(state: str) -> frozenset[str]:
    """Every actor who may move a card INTO this lane, from anywhere.

    **A summary for display, never a permission check.** `may_move` asks about one
    edge; this collapses all of them.
    """
    rules = RULES.get(state)
    if rules is None or not rules.inbound:
        return NOBODY
    return frozenset().union(*rules.inbound.values())


def actors_out(state: str) -> frozenset[str]:
    """Every actor who may move a card OUT of this lane, to anywhere."""
    rules = RULES.get(state)
    if rules is None or not rules.outbound:
        return NOBODY
    return frozenset().union(*rules.outbound.values())


def lane_class(state: str) -> str:
    """A coarse class for styling: `terry`, `claude`, `handoff` or `done`.

    **Derived from the permission table, never stored.** A lane whose `in` and `out`
    name different actors IS a handoff -- that is the definition rather than a list, so
    both handoff lanes qualify without either being special-cased.
    """
    into, out = actors_in(state), actors_out(state)
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
        return " + ".join(n.capitalize() for n in sorted(names)) if names else "nobody"

    return f"IN: {who(actors_in(state))}  ·  OUT: {who(actors_out(state))}"


def now() -> str:
    """An ISO 8601 stamp in this machine's local zone, offset included.

    **Local rather than UTC, and the offset is what makes that safe.** Terry reads
    these; a UTC stamp would make him do arithmetic to answer *"did I sign that off
    before dinner"*. The offset keeps it unambiguous for anything that parses it.
    """
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Change:
    """One entry in an item's history. Appended, never edited, never removed.

    **`frm` is `None` on a creation entry**, which is how a reader tells creation from
    a move without a type field. It serializes as `"from"`, because that is what the
    JSON should read like -- `from` is a Python keyword and cannot be an attribute.
    """

    at: str
    to: str
    by: str
    frm: str | None = None

    def to_json(self) -> dict[str, str]:
        out = {"at": self.at, "to": self.to, "by": self.by}
        if self.frm is not None:
            out["from"] = self.frm
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any], where: str) -> Self:
        for key in ("at", "to", "by"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise BoardError(f"{where}: history entry has no {key}")
        frm = raw.get("from")
        return cls(at=raw["at"], to=raw["to"], by=raw["by"],
                   frm=frm if isinstance(frm, str) else None)


@dataclass
class Comment:
    """A note either of us leaves on a card.

    **Terry asked for these alongside the audit trail:** *"I need to click in for
    description or comment history (from either of us) or audit trail."*

    **They are SEPARATE from `history` on purpose.** History is what the machine
    recorded and nobody typed; comments are what a person chose to say. Mixing them
    would make the audit trail editable, which is the one thing it must not be.
    """

    at: str
    by: str
    text: str

    def to_json(self) -> dict[str, str]:
        return {"at": self.at, "by": self.by, "text": self.text}

    @classmethod
    def from_json(cls, raw: dict[str, Any], where: str) -> Self:
        for key in ("at", "by", "text"):
            if not isinstance(raw.get(key), str):
                raise BoardError(f"{where}: comment has no {key}")
        return cls(at=raw["at"], by=raw["by"], text=raw["text"])


@dataclass
class Item:
    """One card.

    **`id` is stable and MUST NOT be reused.** It is how the board, the harness panel
    and any future consumer agree on which card is which, and it survives reordering,
    renaming and signoff. The markdown version numbered rows positionally, so signing
    one off renumbered the rest while the panel matched by position.
    """

    id: str
    subject: str
    state: str
    priority: str = DEFAULT_PRIORITY
    detail: str = ""
    history: list[Change] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)

    def replayed_state(self) -> str | None:
        """The state this item's own history says it should be in.

        `None` when there is no history to replay -- migrated cards have none, and an
        absent trail is not evidence of a wrong state.
        """
        return self.history[-1].to if self.history else None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "priority": self.priority,
            "state": self.state,
            "subject": self.subject,
            "detail": self.detail,
            "history": [c.to_json() for c in self.history],
        }
        # Omitted when empty, so a board full of comment-less cards stays readable.
        if self.comments:
            out["comments"] = [c.to_json() for c in self.comments]
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any], where: str) -> Self:
        for key in ("id", "state", "subject"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise BoardError(f"{where} has no {key}")
        if raw["state"] not in STATES:
            raise BoardError(f"{where}: unknown state {raw['state']!r}")
        priority = raw.get("priority", DEFAULT_PRIORITY)
        if priority not in PRIORITIES:
            raise BoardError(f"{where}: unknown priority {priority!r}")
        return cls(
            id=raw["id"],
            subject=raw["subject"],
            state=raw["state"],
            priority=priority,
            detail=raw.get("detail", "") or "",
            history=[Change.from_json(h, where) for h in raw.get("history", [])],
            comments=[Comment.from_json(c, where) for c in raw.get("comments", [])],
        )


@dataclass
class Lane:
    state: str
    label: str
    css: str
    owner_label: str
    items: list[Item]


@dataclass
class Board:
    """A whole board: the project it belongs to, the port it is served on, its cards."""

    project: str = ""
    port: int = DEFAULT_PORT
    items: list[Item] = field(default_factory=list)

    # ---- serialization -------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "project": self.project,
            "port": self.port,
            "items": [item.to_json() for item in self.items],
        }

    @classmethod
    def from_json(cls, raw: Any, where: str) -> Self:  # noqa: ANN401 -- untrusted input
        """Validate and build. **The ONLY place a malformed board can enter.**

        **It refuses rather than repairs.** Silently normalizing an unknown state or a
        duplicate id would hide a bug in whatever wrote it, which is exactly how the
        markdown version lost a signoff.
        """
        if not isinstance(raw, dict):
            raise BoardError(f"{where}: top level is {type(raw).__name__}, want object")
        if raw.get("schema") != SCHEMA:
            raise BoardError(
                f"{where}: schema {raw.get('schema')!r}, this build reads {SCHEMA}")
        items_raw = raw.get("items")
        if not isinstance(items_raw, list):
            raise BoardError(f"{where}: 'items' is missing or not a list")

        port = raw.get("port", DEFAULT_PORT)
        if not isinstance(port, int) or not MIN_PORT <= port <= MAX_PORT:
            raise BoardError(f"{where}: port {port!r} is not a TCP port number")

        items: list[Item] = []
        seen: set[str] = set()
        for index, item_raw in enumerate(items_raw):
            spot = f"{where}: items[{index}]"
            if not isinstance(item_raw, dict):
                raise BoardError(f"{spot} is not an object")
            item = Item.from_json(item_raw, spot)
            if item.id in seen:
                raise BoardError(f"{spot}: duplicate id {item.id!r}")
            seen.add(item.id)
            items.append(item)

        return cls(project=str(raw.get("project", "")), port=port, items=items)

    # ---- reading -------------------------------------------------------------

    def find(self, item_id: str) -> Item:
        for item in self.items:
            if item.id == item_id:
                return item
        raise BoardError(f"no item with id {item_id!r}")

    def lanes(self) -> list[Lane]:
        """One `Lane` per column, each sorted by priority then by file order.

        **Priority orders WITHIN a lane and nothing else.** Terry: *"the cards will be
        priority order per swimlane, so claude knows to work top down."* So the top card
        of `Ready for Claude` is the next thing to pick up.

        **`sorted` is stable**, so equal priorities keep the order the file gives them.
        """
        def rank(item: Item) -> int:
            return (PRIORITIES.index(item.priority)
                    if item.priority in PRIORITIES else len(PRIORITIES))

        buckets: dict[str, list[Item]] = {state: [] for state in STATES}
        for item in self.items:
            buckets.setdefault(item.state, []).append(item)
        return [Lane(state, label, lane_class(state), lane_owner_label(state),
                     sorted(buckets.get(state, []), key=rank))
                for state, label in LANES]

    def verify(self) -> list[str]:
        """Replay every item's history and report anything the state machine forbids.

        **THIS IS THE ENFORCEMENT THAT SURVIVES A DIRECT WRITE.** `move()` guards every
        caller that uses the API; nothing can stop `item.state = "completed"` or a hand
        edit to the JSON. **The history is the authority**, so replaying it catches an
        out-of-band change on the next load, whoever made it and however.

        **Terry asked for exactly this emphasis:** *"I'd rather the state machine guard
        sanity."* So it checks four things rather than one, and only the first was
        here before:

        1. The stored `state` matches where the history ends.
        2. **Every recorded transition was LEGAL for the actor that claimed it.** A
           forged `by` on an edge that actor may not take is now caught.
        3. **The chain is unbroken** -- each entry leaves where the previous one
           arrived. A spliced or deleted entry shows up as a gap.
        4. The first entry is a creation, in a lane that permits creation by its actor.

        **What it still cannot catch, stated plainly: a LEGAL edge with a forged
        actor.** If a `ready_for_review -> completed` entry claims `terry`, the state
        machine agrees, because that is exactly what Terry is allowed to do. No amount
        of checking here fixes that; only signing would, and signing a local board is
        absurd. **The defense is that the CLI cannot emit `by: terry` and the server
        cannot emit `by: claude`**, so forging one takes a deliberate hand edit rather
        than a flag.

        **An item with no history is skipped rather than flagged.** The twelve cards
        migrated from the markdown log carry none, and inventing a trail for them would
        have been fabricating evidence.
        """
        problems: list[str] = []
        for item in self.items:
            if not item.history:
                continue

            first = item.history[0]
            if first.frm is None and not may_create(first.by, first.to):
                problems.append(
                    f"{item.id}: history says {first.by} created it in {first.to}, "
                    f"which {first.by} may not do")

            where: str | None = None
            for index, change in enumerate(item.history):
                if change.frm is None:
                    if index > 0:
                        problems.append(
                            f"{item.id}: history[{index}] has no 'from', so it reads "
                            f"as a second creation")
                elif where is not None and change.frm != where:
                    problems.append(
                        f"{item.id}: history[{index}] leaves {change.frm!r} but the "
                        f"previous entry arrived at {where!r} -- the chain is broken")
                elif not may_move(change.by, change.frm, change.to):
                    problems.append(
                        f"{item.id}: history[{index}] records {change.by} moving "
                        f"{change.frm} -> {change.to}, which the permission table "
                        f"forbids")
                where = change.to

            if where is not None and where != item.state:
                problems.append(
                    f"{item.id}: stored state is {item.state!r} but its history ends "
                    f"at {where!r} -- something changed it without going through "
                    f"move()")
        return problems

    # ---- writing -------------------------------------------------------------

    def create(  # noqa: PLR0913 -- see below
        self,
        item_id: str,
        subject: str,
        state: str,
        by: Actor,
        *,
        priority: str = DEFAULT_PRIORITY,
        detail: str = "",
    ) -> str:
        """Add a new card, with its first history entry already on it.

        **`PLR0913` is suppressed and the suggested fix would be worse.** Collapsing
        these into a dict would move the field names off the call site, where they are
        the only thing making the call readable. The last two are keyword-only, so the
        positional count is within the rule's real concern.

        **Creation is an EVENT, not an initial condition.** Without an entry here a
        card's earliest record would be the day somebody happened to touch it.
        """
        if not may_create(by, state):
            allowed = sorted(s for s in STATES if may_create(by, s))
            raise BoardError(
                f"{by} may not create in {state}. "
                + (f"{by} may create in: {', '.join(allowed)}" if allowed
                   else f"{by} may not create anywhere"))
        if any(item.id == item_id for item in self.items):
            raise BoardError(f"duplicate id {item_id!r}")
        if priority not in PRIORITIES:
            raise BoardError(f"unknown priority {priority!r}")
        self.items.append(Item(
            id=item_id, subject=subject, state=state, priority=priority,
            detail=detail, history=[Change(at=now(), to=state, by=by)]))
        return f"created {item_id} in {state} (by {by})"

    def move(self, item_id: str, to_state: str, by: Actor) -> str:
        """Move one card, appending to its history. Returns a one-line description.

        **THE PERMISSION CHECK LIVES HERE, not in the callers.** The first version had
        it only in the server's POST handler, so the browser was guarded and the library
        was not -- and the library is what Claude uses. A test written the same hour
        walked a card `ready_for_review -> completed` as `claude` and then
        `completed -> in_progress` as `terry`: the one edge Claude must never take, and
        a breach of append-only, both accepted in silence.

        **A guard that covers only the path you had in mind covers nothing.**
        """
        if to_state not in STATES:
            raise BoardError(f"unknown state {to_state!r}")
        item = self.find(item_id)
        was = item.state
        if was == to_state:
            return f"{item_id} is already {to_state}"
        if not may_move(by, was, to_state):
            raise BoardError(explain_refusal(by, was, to_state))
        item.state = to_state
        item.history.append(Change(at=now(), frm=was, to=to_state, by=by))
        return f"{item_id}: {was} → {to_state} (by {by})"

    def comment(self, item_id: str, text: str, by: Actor) -> str:
        """Leave a note on a card. **Either of us, any card, any state.**

        **Commenting is NOT permission-checked, and that is deliberate.** A comment
        changes nothing about who owns the work; refusing one would only stop the two of
        us talking on the card where the talking belongs.
        """
        if not text.strip():
            raise BoardError("a comment needs text")
        item = self.find(item_id)
        item.comments.append(Comment(at=now(), by=by, text=text.strip()))
        return f"{item_id}: comment by {by}"


def load(path: pathlib.Path) -> Board:
    """Read, parse and VALIDATE a board file."""
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return Board.from_json(raw, str(path))


def save(board: Board, path: pathlib.Path) -> None:
    """Write the board, formatted so a git diff shows one changed field per line.

    **`indent=2` and a trailing newline are not cosmetic.** A single-line JSON file
    turns every edit into one enormous diff, which throws away the reason the record
    lives in git at all.
    """
    text = json.dumps(board.to_json(), indent=2, ensure_ascii=False) + "\n"
    with pathlib.Path(path).open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main() -> None:
    """A small CLI, so a board can be inspected and moved without the browser."""
    ap = argparse.ArgumentParser(description="Inspect or update a claude-status board.")
    ap.add_argument("board", type=pathlib.Path, help="path to the board JSON")
    ap.add_argument("--json", action="store_true", help="dump the parsed board")
    ap.add_argument("--verify", action="store_true",
                    help="replay every history and refuse a state it disagrees with")
    ap.add_argument("--move", nargs=2, metavar=("ID", "STATE"), help="move one card")
    ap.add_argument("--comment", nargs=2, metavar=("ID", "TEXT"),
                    help="leave a comment on one card")
    args = ap.parse_args()

    board = load(args.board)

    if args.move or args.comment:
        # **THE CLI IS ALWAYS `claude`, and there is no flag to say otherwise.**
        #
        # Terry asked how Claude's changes get tagged, and the first answer exposed a
        # backwards asymmetry: the server HARD-CODES `terry` because loopback proves
        # it is him, while the CLI merely DEFAULTED to `claude` and accepted
        # `--by terry`. **So he could not impersonate Claude and Claude could
        # impersonate him** -- including on `ready_for_review -> completed`, the one
        # edge that exists to be his alone.
        #
        # **Two paths, two identities, neither able to claim the other.** The
        # library's `move(by=...)` still takes an actor because the tests need to
        # walk both sides of the permission table; the CLI, which is the thing
        # Claude actually runs, cannot.
        #
        # **This is a guard rail, not a proof.** Nothing stops a hand edit that
        # writes `"by": "terry"` into the JSON, and no amount of code here can fix
        # that short of signing, which would be absurd for a local board. What it
        # does is make the honest path the easy path and forgery a deliberate act.
        result = (board.move(args.move[0], args.move[1], "claude") if args.move
                  else board.comment(args.comment[0], args.comment[1], "claude"))
        save(board, args.board)
        print(f"  {result}")
        return

    if args.json:
        print(json.dumps(board.to_json(), indent=2, ensure_ascii=False))
        return

    bad_edges = check_edges()
    if bad_edges:
        print(f"  PERMISSION TABLE IS INCONSISTENT, {len(bad_edges)} problem(s):")
        for problem in bad_edges:
            print(f"      {problem}")

    drift = board.verify()
    if drift:
        print(f"  {len(drift)} item(s) DISAGREE WITH THEIR OWN HISTORY:")
        for problem in drift:
            print(f"      {problem}")
    elif args.verify:
        print("  Every item's state matches its audit trail.")

    print(f"\n{board.project}  ({len(board.items)} items, port {board.port})")
    for lane in board.lanes():
        if not lane.items:
            continue
        print(f"\n  {lane.label}  [{lane.owner_label}]  {len(lane.items)}")
        for item in lane.items:
            note = f"  ({len(item.comments)} comment(s))" if item.comments else ""
            print(f"    {item.priority}  {item.subject}{note}")

    if bad_edges or drift:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
