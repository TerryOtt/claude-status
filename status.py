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
import contextlib
import datetime
import json
import os
import pathlib
import time
from collections.abc import Iterator
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

# **`LANES`, `STATES`, `PRIORITIES`, `PRIORITY_LABEL` and `DEFAULT_PRIORITY` are all
# LOADED FROM `rules.json`**, further down, once `LaneRules` exists to hold them.
#
# **They used to be literals here and both copies briefly survived the move.** The
# loader ran after them so the right table won, and the file quietly lied to anyone
# reading the top of it -- a dead literal that looks authoritative is worse than no
# literal at all.
#
# **P0 is on fire. P5 is only if there is nothing else.** Terry's scale, verbatim:
# *"P0 is [...] on fire emergency and P5 is 'only if you have nothing else to work'."*
# Six levels rather than three, because he wanted room to rank a long backlog without
# every item collapsing to "medium". **The list itself is in `rules.json`.**

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
    Backlog card to Ready For Work, and nowhere else."*

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

# **THE RULES LIVE IN `rules.json`, NOT HERE.** Terry, 2026-08-18: *"can we move
# rules outside code? I'd like that to be like a JSON so it acts more like a rules
# engine. I hate to recompile code when rules for rules engine change."*
#
# **The stronger argument is the one he gave second:** *"also get version history
# isolated to JUST perms changes."* Today's history proves it -- permission edits are
# tangled inside commits about heartbeat CSS and lane title sizes. Split out,
# `git log rules.json` is only ever the rules.
#
# **JSON has no comments, and the reasoning is the most valuable part of that table**,
# so every lane and every edge carries an optional `note`. He offered `.jsonc` as the
# alternative and it is REFUSED: `jq` cannot read JSONC, and it fails dishonestly --
# `jq -e '.name' wrangler.jsonc` reports `Invalid numeric literal at line 6, column 4`
# where line 6 is the first `//` comment. **The error names the wrong cause**, and he
# specifically wants jq on these files.
#
# **The notes are IN the data rather than in a companion document.** Two copies of one
# fact is the drift this project keeps paying for.
RULES_PATH = pathlib.Path(__file__).resolve().parent / "rules.json"

# **THE PERMISSION TABLE. Terry dictated it lane by lane on 2026-08-18**, in the shape
# he asked for: *"I like that the perms are (in/out, actor, source/dest)."*
def _load_rules(path: pathlib.Path) -> tuple[
    tuple[tuple[str, str], ...],
    dict[str, LaneRules],
    tuple[str, ...],
    dict[str, str],
    str,
]:
    """Read `rules.json` into the shapes the rest of this module already uses.

    **It REFUSES rather than repairs**, exactly like `Board.from_json`. A rules file
    naming an unknown actor or a lane that does not exist is a bug in whoever edited
    it, and defaulting past that would hide the edit that broke the board.

    **`note` fields are ignored here on purpose.** They exist for the person reading
    the diff; nothing in the permission logic consults them, so a wrong note cannot
    change behavior and a missing one cannot break a build.
    """
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)

    if doc.get("schema") != SCHEMA:
        raise BoardError(f"{path}: rules schema {doc.get('schema')!r}, want {SCHEMA}")

    lanes_raw = doc.get("lanes")
    if not isinstance(lanes_raw, list) or not lanes_raw:
        raise BoardError(f"{path}: 'lanes' is missing or empty")

    known = {lane["id"] for lane in lanes_raw}
    order = tuple((lane["id"], lane["label"]) for lane in lanes_raw)

    def actors(spec: object, where: str) -> frozenset[str]:
        if not isinstance(spec, dict) or not isinstance(spec.get("actors"), list):
            raise BoardError(f"{path}: {where} has no 'actors' list")
        bad = [a for a in spec["actors"] if a not in ACTORS]
        if bad:
            raise BoardError(f"{path}: {where} names unknown actor(s) {bad}")
        return frozenset(spec["actors"])

    table: dict[str, LaneRules] = {}
    for lane in lanes_raw:
        lane_id = lane["id"]
        for direction in ("in", "out"):
            for other in lane.get(direction, {}):
                if other not in known:
                    raise BoardError(
                        f"{path}: {lane_id}.{direction} names unknown lane {other!r}")
        bad_create = [a for a in lane.get("create", []) if a not in ACTORS]
        if bad_create:
            raise BoardError(f"{path}: {lane_id}.create names {bad_create}")
        table[lane_id] = LaneRules(
            create=frozenset(lane.get("create", [])),
            inbound={src: actors(spec, f"{lane_id}.in.{src}")
                     for src, spec in lane.get("in", {}).items()},
            outbound={dst: actors(spec, f"{lane_id}.out.{dst}")
                      for dst, spec in lane.get("out", {}).items()},
        )

    priorities = tuple(p["id"] for p in doc["priorities"])
    labels = {p["id"]: p["label"] for p in doc["priorities"]}
    default = doc.get("defaultPriority", priorities[len(priorities) // 2])
    if default not in priorities:
        raise BoardError(f"{path}: defaultPriority {default!r} is not in the list")
    return order, table, priorities, labels, default


LANES, RULES, PRIORITIES, PRIORITY_LABEL, DEFAULT_PRIORITY = _load_rules(RULES_PATH)

STATES: tuple[str, ...] = tuple(state for state, _ in LANES)
LANE_LABEL: dict[str, str] = dict(LANES)



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

    **THIS FUNCTION IS NOT THE WHOLE RULE FOR ONE EDGE, AND THAT IS DELIBERATE.**
    `backlog -> ready_for_claude` carries a `claude` actor, so this returns True for it.
    **Claude MUST NOT use it without Terry's explicit per-ticket instruction.** Terry,
    2026-08-19: *"claude MUST NOT move out of backlog until/unless Terry gives explicit
    guidance for one specific ticket."*

    **The table cannot express that constraint** -- the grant is PER TICKET and verbal,
    and a permission model grants an edge or it does not. The edge exists so Terry can
    say *"promote #0027"* and it happens without him reaching for the mouse.

    **So a caller that trusts this function alone will get that one edge wrong.** The
    restraint lives in `rules.json`'s note on both ends, in FlickrGroupAddr's
    `CLAUDE.md`, and in its `docs/ORIENTATION.md`. **Every other edge here IS the whole
    rule.**
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

    **`ticket` is the handle a PERSON uses**, and it is not decoration on top of the
    slug. Terry named the use case exactly: *"Human brains will want to do shit like
    'wtf is up with ticket 137, Claude? you high today?'"* -- and then the other half,
    *"we don't like ticket summary"*, because quoting a long subject out loud is
    miserable.

    **So the two identifiers have different jobs.** The slug is the machine's; the
    number is the conversation's. Both are permanent, and `find()` accepts either.
    """

    id: str
    subject: str
    state: str
    ticket: int = 0
    priority: str = DEFAULT_PRIORITY
    detail: str = ""
    history: list[Change] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)

    @property
    def label(self) -> str:
        """`#0016`. **Four digits, zero-padded**, per Terry's standing order.

        *"Zero-pad anything that will ever sort."* Unpadded numbers sort `1, 10, 2`,
        and a reference cited in a commit message cannot be cheaply changed later.
        Above 9999 it simply grows rather than truncating.
        """
        return f"#{self.ticket:04d}"

    def replayed_state(self) -> str | None:
        """The state this item's own history says it should be in.

        `None` when there is no history to replay -- migrated cards have none, and an
        absent trail is not evidence of a wrong state.
        """
        return self.history[-1].to if self.history else None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "ticket": self.ticket,
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
        ticket = raw.get("ticket", 0)
        if not isinstance(ticket, int) or ticket < 0:
            raise BoardError(f"{where}: ticket {ticket!r} is not a positive integer")
        return cls(
            id=raw["id"],
            subject=raw["subject"],
            state=raw["state"],
            ticket=ticket,
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

    # **The next ticket to hand out. It only ever goes UP.**
    #
    # **A number MUST NOT be reused, even after a card is archived or deleted.** The
    # whole point is that Terry can say "ticket 137" and mean one thing forever; two
    # pieces of work sharing a reference in git history would destroy that.
    #
    # **Stored rather than derived.** `len(items) + 1` and `max(ticket) + 1` both
    # look correct and both collide the moment anything is removed -- the first
    # immediately, the second as soon as the highest-numbered card goes.
    next_ticket: int = 1

    # ---- serialization -------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "project": self.project,
            "port": self.port,
            "nextTicket": self.next_ticket,
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
        tickets: set[int] = set()
        for index, item_raw in enumerate(items_raw):
            spot = f"{where}: items[{index}]"
            if not isinstance(item_raw, dict):
                raise BoardError(f"{spot} is not an object")
            item = Item.from_json(item_raw, spot)
            if item.id in seen:
                raise BoardError(f"{spot}: duplicate id {item.id!r}")
            seen.add(item.id)
            # **A duplicate ticket is refused at the door.** The number's whole value
            # is that "ticket 137" means one thing forever, and two cards sharing one
            # would break every reference in git and in conversation at once.
            if item.ticket and item.ticket in tickets:
                raise BoardError(f"{spot}: duplicate ticket {item.label}")
            tickets.add(item.ticket)
            items.append(item)

        next_ticket = raw.get("nextTicket", max(tickets, default=0) + 1)
        if not isinstance(next_ticket, int) or next_ticket < 1:
            raise BoardError(f"{where}: nextTicket {next_ticket!r} is not positive")
        # **The counter MUST be ahead of every ticket in the file.** A hand edit that
        # rewinds it would hand out a number already in use -- caught here rather
        # than discovered when two cards collide.
        if tickets and next_ticket <= max(tickets):
            raise BoardError(
                f"{where}: nextTicket is {next_ticket} but ticket "
                f"#{max(tickets):04d} already exists -- the counter went backwards")

        return cls(project=str(raw.get("project", "")), port=port, items=items,
                   next_ticket=next_ticket)

    # ---- reading -------------------------------------------------------------

    def find(self, ref: str) -> Item:
        """A card, by slug OR by ticket number. **If you can say it, you can type it.**

        Terry's use case is spoken -- *"wtf is up with ticket 137"* -- so the CLI
        accepts `137`, `#137` and `0137` as readily as `implement-lrc-plug-as`.
        Making him look up a slug to act on a number he just said out loud would
        waste the handle the number exists to be.

        **The slug is tried first.** A slug is unambiguous; a bare number could in
        principle be one, and the explicit identifier should win.
        """
        for item in self.items:
            if item.id == ref:
                return item

        digits = ref.lstrip("#").lstrip("0") or "0"
        if digits.isdigit():
            wanted = int(digits)
            for item in self.items:
                if item.ticket == wanted:
                    return item
            raise BoardError(f"no card with ticket #{wanted:04d}")

        raise BoardError(f"no item with id {ref!r}")

    def lanes(self) -> list[Lane]:
        """One `Lane` per column, each sorted by priority then by file order.

        **Priority orders WITHIN a lane and nothing else.** Terry: *"the cards will be
        priority order per swimlane, so claude knows to work top down."* So the top card
        of `Ready For Work` is the next thing to pick up.

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
        # **Taken from the counter and the counter advances**, never derived from the
        # items. See `next_ticket` for why both obvious derivations collide.
        item = Item(
            id=item_id, subject=subject, state=state, ticket=self.next_ticket,
            priority=priority, detail=detail,
            history=[Change(at=now(), to=state, by=by)])
        self.next_ticket += 1
        self.items.append(item)
        return f"created {item.label} {item_id} in {state} (by {by})"

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

        # **WRITE THE LOG FIRST, THEN THE STATE.** Terry: *"I'd rather log fail and
        # then we abort vs write file succeed and succeed THEN log fails. Leaves you
        # in a bad spot."*
        #
        # **The bad spot is a card whose state nothing explains.** `verify()` treats
        # the history as the authority, so a state change that never made it into the
        # trail does not read as a missing log entry -- it reads as tampering, and it
        # is indistinguishable from the real thing.
        #
        # **So the entry goes on first and is rolled back if anything downstream
        # refuses.** In memory the two lines are adjacent and nothing can fail
        # between them, which is exactly why the ordering costs nothing and is worth
        # having anyway: the next person to add a step between them inherits the safe
        # order rather than discovering it.
        entry = Change(at=now(), frm=was, to=to_state, by=by)
        item.history.append(entry)
        item.state = to_state

        # **Abort rather than half-apply.** If the result would not survive its own
        # audit, undo both and raise -- a board that fails `verify()` on the next load
        # is worse than a move that plainly did not happen.
        broken = [p for p in self.verify() if p.startswith(f"{item_id}:")]
        if broken:
            item.history.pop()
            item.state = was
            raise BoardError("; ".join(broken))
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


# **How long to wait for another writer, and when to call a lock abandoned.**
# A real edit is a file read, a dict mutation and a 13 KB write -- microseconds. A
# second of patience covers any honest contention; ten means the holder is dead.
LOCK_WAIT_S = 1.0
LOCK_STALE_S = 10.0


@contextlib.contextmanager
def locked(path: pathlib.Path) -> Iterator[None]:
    """Hold an exclusive lock on a board for the whole read-modify-write.

    **Terry asked whether this was needed:** *"are we (do we need to?) file locking
    as it's our 'Database'? ... if it's cheap seems like peace of mind to get atomic
    test and set."* **It is needed, and the race is real rather than theoretical.**

    **Two writers exist and both rewrite the WHOLE file.** Terry's drag goes through
    the server's `do_POST`; Claude's edits go through `status.py --move`. Each loads
    the entire board, mutates it and saves it. Overlap them and the second save
    silently discards everything the first one did -- a lost update, and the atomic
    rename in `save()` makes that outcome CLEANER rather than safer, because the
    file left behind is perfectly valid and simply missing a card's move.

    **`O_CREAT | O_EXCL` is the primitive**, because it is the one atomic
    test-and-set every filesystem agrees on, including the SMB share `X:` lives on.
    No dependency, and nothing to configure.

    **A stale lock is stolen rather than waited on forever.** A process killed
    mid-edit would otherwise wedge the board permanently, and a board that cannot be
    written is a worse failure than the race this prevents. The holder's pid is
    written into the file so an abandoned one can be identified.
    """
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # **Steal only a lock that is provably old.** Age is read from the lock
            # file itself, so a live holder refreshing nothing still keeps it for
            # LOCK_STALE_S -- far longer than any real edit takes.
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue  # It vanished between the two calls. Try again.
            if age > LOCK_STALE_S:
                lock.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise BoardError(
                    f"{path.name} is locked by another writer "
                    f"(held {age:.1f}s). Nothing was changed.") from None
            time.sleep(0.02)
            continue
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextlib.contextmanager
def edit(path: pathlib.Path) -> Iterator[Board]:
    """Load, hand over the board, then save -- all under one lock.

    **This is the ONLY correct way to change a board**, and both writers use it. A
    bare `load` / mutate / `save` is the lost-update race with extra steps.

    **The board is loaded INSIDE the lock**, which is the whole point: reading before
    acquiring would hand out a snapshot that another writer can invalidate before the
    save lands.
    """
    with locked(path):
        board = load(path)
        yield board
        save(board, path)


def save(board: Board, path: pathlib.Path) -> None:
    """Write the board ATOMICALLY. The reader sees the old file or the new one.

    **`indent=2` and a trailing newline are not cosmetic.** A single-line JSON file
    turns every edit into one enormous diff, which throws away the reason the record
    lives in git at all.

    **The write goes to a temp file and is then RENAMED over the target**, because
    the obvious version destroys the board on a bad day. `open(path, "w")` truncates
    first and writes second, so a crash, a full disk or a killed process between
    those two leaves a half file -- and the loser is not the one change in flight, it
    is every card ever recorded.

    **`Path.replace` is atomic on Windows and POSIX alike**, which is why it is used
    rather than `shutil.move` or an unlink-then-rename.

    **`fsync` before the rename is the part people skip.** Without it the rename can
    reach the disk before the bytes do, and a power loss leaves a correctly named
    empty file -- the worst of both outcomes. The board is 13 KB and this happens on
    a human's drag, so the cost is irrelevant.

    **The temp file is in the SAME DIRECTORY on purpose.** A rename across
    filesystems is not atomic, and `X:` is an SMB share while the temp directory is
    not.
    """
    text = json.dumps(board.to_json(), indent=2, ensure_ascii=False) + "\n"
    target = pathlib.Path(path)
    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


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
    ap.add_argument("--create", nargs=2, metavar=("ID", "SUBJECT"),
                    help="add one card; needs --state")
    # **`choices` is the lanes Claude may CREATE in, not every lane.** argparse then
    # refuses an illegal lane by name before the board is opened, which is a better
    # error than `BoardError` raised under the lock -- and it makes `--help` state
    # the permission rather than hide it behind a failed run.
    ap.add_argument("--state", choices=[s for s in STATES if may_create("claude", s)],
                    help="the lane to --create in")
    ap.add_argument("--priority", choices=list(PRIORITIES), default=DEFAULT_PRIORITY,
                    help="priority for --create")
    detail = ap.add_mutually_exclusive_group()
    detail.add_argument("--detail", default="", help="description for --create")
    detail.add_argument("--detail-file", type=pathlib.Path,
                        help="read the description from a file instead")
    args = ap.parse_args()

    if args.create and not args.state:
        ap.error("--create needs --state")

    if args.move or args.comment or args.create:
        # **The lock is held across load, mutate and save.** Reading first and
        # locking second would hand out a snapshot another writer can invalidate.
        #
        # **THE PRINT IS OUTSIDE THE BLOCK, and that ordering is the whole point.**
        # `edit()` saves on the way out, so printing inside it reports a result the
        # disk has not accepted yet. On 2026-08-18 `--move 31 ready_for_review`
        # printed its success line and the card did not move: `save()` then raised
        # `PermissionError: [WinError 5]` from `tmp.replace(target)`, because the
        # board lives on a NAS share and `os.replace` over SMB can return
        # access-denied while another handle is briefly open.
        #
        # **This is the library's own rule, one layer up.** `move()` records Terry's
        # instruction verbatim -- *"I'd rather log fail and then we abort vs write
        # file succeed and succeed THEN log fails. Leaves you in a bad spot."* A
        # failed save now raises before anything reaches the screen.
        with edit(args.board) as board:
            result = _apply(board, args)
        print(f"  {result}")
        return

    _report(load(args.board), args)


def _apply(board: Board, args: argparse.Namespace) -> str:
    """Perform the one requested change and RETURN its description.

    Called INSIDE `edit`, so it must not save -- and it must not PRINT either. The
    caller prints after the save lands, which is what makes the line trustworthy.
    See the comment at the call site for the lost write that established this.

    **THE CLI IS ALWAYS `claude`, and there is no flag to say otherwise.**

    Terry asked how Claude's changes get tagged, and the first answer exposed a
    backwards asymmetry: the server HARD-CODES `terry` because loopback proves it is
    him, while the CLI merely DEFAULTED to `claude` and accepted `--by terry`. **So
    he could not impersonate Claude and Claude could impersonate him** -- including
    on `ready_for_review -> completed`, the one edge that exists to be his alone.

    **Two paths, two identities, neither able to claim the other.** `Board.move`
    still takes an actor because the tests must walk both sides of the permission
    table; the CLI, which is the thing Claude actually runs, cannot.

    **This is a guard rail, not a proof.** Nothing stops a hand edit that writes
    `"by": "terry"` into the JSON, and no code here fixes that short of signing,
    which would be absurd for a local board. What it does is make the honest path
    the easy path and forgery a deliberate act.
    """
    if args.create:
        return board.create(args.create[0], args.create[1], args.state, "claude",
                            priority=args.priority, detail=_detail_text(args))
    if args.move:
        return board.move(args.move[0], args.move[1], "claude")
    return board.comment(args.comment[0], args.comment[1], "claude")


def _detail_text(args: argparse.Namespace) -> str:
    """A new card's description, from `--detail` or from `--detail-file`.

    **The file form exists because the SHELL eats punctuation and the board keeps the
    damage.** A detail passed inline through bash on 2026-08-18 lost the apostrophe in
    `I'd` to a literal `%27`, and it reached the board that way -- a quoting artifact
    is indistinguishable from something Terry typed once it is in the record.

    **A file has no quoting layer to survive**, which is why it is offered rather than
    left as the caller's problem.
    """
    if args.detail_file:
        return args.detail_file.read_text(encoding="utf-8").rstrip("\n")
    return str(args.detail)


def _report(board: Board, args: argparse.Namespace) -> None:
    """Print the board, and exit non-zero if anything fails its own audit."""
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
        print(f"  {len(drift)} item(s) FAIL THEIR OWN AUDIT TRAIL:")
        for problem in drift:
            print(f"      {problem}")
    elif args.verify:
        print("  Every item's history replays cleanly and matches its state.")

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
