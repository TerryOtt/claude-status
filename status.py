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
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Self

# **`dataclasses.asdict` was considered and REFUSED**, which is why every class here
# hand-writes `to_json`. `asdict` walks nested dataclasses blindly: it would emit `frm`
# instead of `from`, and it would write an empty `comments` list onto every card.
# **The file is JSON so a person can read the diff**, and that is worth two dozen lines.

SCHEMA = 1

# **A SECOND, INDEPENDENT NUMBER, and splitting it was the first thing card #0064 had to
# do.** One `SCHEMA` used to gate both `board.json` and `rules.json`, so flattening the
# rules would have bumped the number every board is checked against and **rejected every
# board on disk** -- a data outage caused by a change to a different file.
#
# **Two files with two shapes get two version numbers.** They change for unrelated
# reasons and neither should be able to invalidate the other.
RULES_SCHEMA = 2

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


def as_actor(value: str) -> Actor:
    """Turn a caller-supplied string into an `Actor`, or refuse it. Card #0013's
    side finding.

    **THE ONE PLACE A STRING BECOMES AN ACTOR**, and it exists because pyright found
    the same defect from two directions at once on 2026-08-19.

    `serve.py`'s `/assign` route passed `str(body["owner"])` straight into `assign()`,
    whose parameter is annotated `Actor`. **The annotation was a lie at that call
    site**, and the browser could name any owner it liked. Meanwhile pyright reported
    `assign()`'s own `if owner not in ACTORS` guard as UNREACHABLE -- because the
    annotation promised the check could never fire.

    **Those are one bug.** The runtime guard was the only thing keeping the route safe,
    and the type system had been told it was redundant.

    **It returns the literals explicitly rather than the argument.** Returning `value`
    after an `in` test relies on the checker's narrowing of a `str`, which varies by
    version; returning `"terry"` cannot be misread by any of them.
    """
    name = value.strip().lower()
    if name == "terry":
        return "terry"
    if name == "claude":
        return "claude"
    raise BoardError(f"unknown actor {value!r}; want one of {', '.join(ACTORS)}")


# ---------------------------------------------------------------------------
# RELATIONSHIPS BETWEEN CARDS. Card #0028.
#
# **Terry: *"update data model for status board to be able to note ticket relationships
# 'related, parent, child, referenced by, etc.'"*** He asked for the set to come from a
# survey rather than from taste, and the survey changed the shape of the answer.
#
# **Jira, Linear and GitHub all SPLIT hierarchy from relations**, and none of them models
# a parent as one more link type. GitHub caps a sub-issue at one parent and an open
# request to allow several is still unfulfilled. Terry approved the split: *"recommendation
# accepted and approved for work."*
#
# **So `parent` is a FIELD on the card and everything here is a symmetric pair.** A tree
# needs "one parent" and "no cycles", and neither is expressible in a symmetric table.
#
# `clones` is deliberately absent. It exists in Jira because Jira has a Clone button, and
# a relationship naming a feature this board does not have would never be written.
LINK_INVERSE: dict[str, str] = {
    "blocks": "blocked_by",
    "blocked_by": "blocks",
    "duplicates": "duplicated_by",
    "duplicated_by": "duplicates",
    "references": "referenced_by",
    "referenced_by": "references",
    # **Its own inverse, and that is not a special case to remove.** "A relates to B"
    # and "B relates to A" are the same claim, which is why all three products ship one
    # symmetric `related` rather than a pair.
    "relates_to": "relates_to",
}

# **One direction of each pair is STORED and the other is DERIVED.** `--link 5 blocked_by
# 28` is normalized to `28 blocks 5` at the door, so the file only ever holds one spelling.
LINK_CANONICAL: tuple[str, ...] = ("blocks", "duplicates", "references", "relates_to")


@dataclass(frozen=True)
class Link:
    """One relationship, stored ONCE. Card #0028.

    **TERRY'S HARDEST REQUIREMENT DISSOLVES HERE RATHER THAN BEING ENFORCED.** His words:
    *"the two halves RFC-MUST be done atomically while holding file lock. Both halves get
    relationship or neither get it. Inconsistent relationships where only one of the two
    get updated MUST NOT be allowed to be possible."*

    **He described writing a copy onto each card**, which needs a lock, an atomic write,
    and an API shaped so that "add one half" cannot be expressed. All three are real work
    and all three can be got wrong.

    **A single row has no halves.** The other direction is computed by `LINK_INVERSE` when
    something reads it, so a one-sided link is not merely forbidden -- there is nowhere to
    put one. **That is the same lesson `rules.json` is being flattened for on card #0064**:
    the bug class disappears when the fact stops being stored twice.
    """

    frm: str
    kind: str
    to: str

    def to_json(self) -> dict[str, str]:
        return {"from": self.frm, "kind": self.kind, "to": self.to}

    @classmethod
    def from_json(cls, raw: Any, where: str) -> Self:  # noqa: ANN401 -- untrusted input
        if not isinstance(raw, dict):
            raise BoardError(f"{where}: a link is {type(raw).__name__}, want object")
        missing = [k for k in ("from", "kind", "to") if not raw.get(k)]
        if missing:
            raise BoardError(f"{where}: link is missing {', '.join(missing)}")
        kind = str(raw["kind"])
        if kind not in LINK_CANONICAL:
            raise BoardError(
                f"{where}: link kind {kind!r} is not stored form; "
                f"want one of {', '.join(LINK_CANONICAL)}")
        return cls(frm=str(raw["from"]), kind=kind, to=str(raw["to"]))

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

def _index_edges(
    edges_raw: list[Any], known: set[str], path: pathlib.Path,
) -> tuple[dict[str, dict[str, set[str]]], dict[str, dict[str, set[str]]]]:
    """Validate the flat edge list and index it both ways. Card #0064.

    **`inbound` and `outbound` are still BUILT, and that is why nothing else changed.**
    The FILE stopped storing each edge twice; this index still answers both questions, so
    `serve.py`, `may_move` and `edges_for` never learned that the format moved.

    **Extracted from `_load_rules`**, which ruff correctly called too branchy once the
    edge validation landed in it -- the same call it made on `Board.from_json` an hour
    earlier, for the same reason.
    """
    inbound: dict[str, dict[str, set[str]]] = {lane: {} for lane in known}
    outbound: dict[str, dict[str, set[str]]] = {lane: {} for lane in known}
    seen: set[tuple[str, str]] = set()

    for index, edge in enumerate(edges_raw):
        spot = f"edges[{index}]"
        if not isinstance(edge, dict):
            raise BoardError(f"{path}: {spot} is not an object")
        # **All three are MANDATORY. Terry, 2026-08-19: *"making sure rules MUST have
        # actor + source lane + dest lane."*** A row missing one is not a weaker rule,
        # it is an unreadable one.
        missing = [k for k in ("actors", "from", "to") if not edge.get(k)]
        if missing:
            raise BoardError(f"{path}: {spot} is missing {', '.join(missing)}")
        frm, to = str(edge["from"]), str(edge["to"])
        for end in (frm, to):
            if end not in known:
                raise BoardError(f"{path}: {spot} names unknown lane {end!r}")
        if frm == to:
            raise BoardError(f"{path}: {spot} joins {frm!r} to itself")
        if (frm, to) in seen:
            raise BoardError(f"{path}: {spot} repeats the edge {frm} -> {to}")
        seen.add((frm, to))
        if not isinstance(edge["actors"], list):
            raise BoardError(f"{path}: {spot} 'actors' is not a list")
        bad = [a for a in edge["actors"] if a not in ACTORS]
        if bad:
            raise BoardError(f"{path}: {spot} names unknown actor(s) {bad}")
        outbound[frm][to] = set(edge["actors"])
        inbound[to][frm] = set(edge["actors"])
    return inbound, outbound


# **THE PERMISSION TABLE. Terry dictated it lane by lane on 2026-08-18**, in the shape
# he asked for: *"I like that the perms are (in/out, actor, source/dest)."* **The FILE was
# flattened on 2026-08-19 by card #0064** -- one row per edge instead of a copy under each
# lane -- and the shape he named survives as the in-memory index this builds.
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

    if doc.get("schema") != RULES_SCHEMA:
        raise BoardError(
            f"{path}: rules schema {doc.get('schema')!r}, want {RULES_SCHEMA}. "
            "Schema 1 nested every edge under both lanes; card #0064 flattened it.")

    lanes_raw = doc.get("lanes")
    if not isinstance(lanes_raw, list) or not lanes_raw:
        raise BoardError(f"{path}: 'lanes' is missing or empty")

    known = {lane["id"] for lane in lanes_raw}
    order = tuple((lane["id"], lane["label"]) for lane in lanes_raw)

    edges_raw = doc.get("edges")
    if not isinstance(edges_raw, list) or not edges_raw:
        raise BoardError(f"{path}: 'edges' is missing or empty")

    inbound, outbound = _index_edges(edges_raw, known, path)

    table: dict[str, LaneRules] = {}
    for lane in lanes_raw:
        lane_id = lane["id"]
        bad_create = [a for a in lane.get("create", []) if a not in ACTORS]
        if bad_create:
            raise BoardError(f"{path}: {lane_id}.create names {bad_create}")
        table[lane_id] = LaneRules(
            create=frozenset(lane.get("create", [])),
            inbound={src: frozenset(who) for src, who in inbound[lane_id].items()},
            outbound={dst: frozenset(who) for dst, who in outbound[lane_id].items()},
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
    """Every inconsistency in the derived permission index. Empty means consistent.

    **THIS FUNCTION LOST ITS ORIGINAL JOB ON 2026-08-19, and that is the win.** Card
    #0064. It was 32 lines that compared the two stored copies of every edge --
    `laneA.out.laneB` against `laneB.in.laneA` -- because `rules.json` held each fact
    twice and the two could disagree.

    **`rules.json` now stores each edge once**, so there is no second copy to contradict
    the first. The comparison it used to make cannot fail.

    **It is kept rather than deleted, with a narrower job**, because `inbound` and
    `outbound` are still BUILT as two dictionaries in `_load_rules`, and a future edit
    there could still fill one and not the other. **The check moved from guarding the
    FILE to guarding the derivation.**

    **Callers MUST surface a non-empty result.** A table that contradicts itself behaves
    as whichever half a given code path reads, and the two halves are read by different
    code.
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
                    f"{src} -> {state} was indexed inbound but not outbound")
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
                    f"{state} -> {dst} was indexed outbound but not inbound "
                    f"for {sorted(actors)}")
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

# **The mtime the table above was built from.** `_rules_mtime` is what makes a live
# reload possible without asking the filesystem to re-read an unchanged file.
_rules_mtime: float = RULES_PATH.stat().st_mtime if RULES_PATH.exists() else 0.0


def reload_rules_if_changed() -> str | None:
    """Re-read `rules.json` when it has changed on disk. Returns a message, or None.

    **Terry's standing order, 2026-08-19: a tool that can DETECT its own staleness
    MUST resolve it where it can, and alert only where it cannot.** `rules.json` is
    data, so this end of the problem is resolvable and gets resolved silently.

    **It bit twice in one afternoon before this existed.** The rules gained a
    `claude` actor and the board went on showing the old lane owners; Terry noticed
    before any instrument did, and his first guess was that the rule had never been
    written. **A server holding a table it loaded at import cannot see that it is
    wrong.**

    **EVERY DERIVED GLOBAL IS REBOUND TOGETHER, and that is the whole difficulty.**
    Seven names come out of `_load_rules` or are computed from it, and a table that
    is half-new contradicts itself -- which is exactly what `check_edges()` exists to
    catch, arriving from a new direction.

    **A BAD FILE KEEPS THE OLD TABLE.** A `rules.json` saved mid-edit is a real
    state, and half a permission model is worse than a stale one. So the new table is
    built and validated COMPLETELY before anything is rebound; on any failure this
    returns a message and changes nothing.

    **`_rules_mtime` advances even on a rejected file.** Otherwise a broken save
    would be re-read, re-parsed and re-rejected on every single poll -- twice a
    second, forever -- and the log would be the only thing that noticed.
    """
    # **Nine globals rebound, and `global` is correct here rather than a smell.** The
    # rest of this module reads these names directly, and every caller reaches them as
    # `status.RULES`, so **rebinding the module attribute IS the delivery mechanism.**
    #
    # **PLW0603 is suppressed for one function, deliberately.** The lint is right in
    # general: global rebinding is hard to reason about. The alternative here is a
    # mutable container -- `_state.rules` -- which means touching every reference in
    # two files to satisfy a rule about a single function that exists precisely to
    # rebind them. **The blast radius of the fix exceeds the blast radius of the
    # finding**, and the function is short, documented, and the only writer.
    # **The first line carries no `noqa` and the second does, which looks wrong and is
    # not.** PLW0603 does not fire on names bound only by TUPLE UNPACKING, and every
    # name on the first line is. Adding a matching directive there is an unused `noqa`,
    # which `RUF100` then reports -- so the asymmetry is ruff's, not a slip.
    global LANES, RULES, PRIORITIES, PRIORITY_LABEL, DEFAULT_PRIORITY
    global STATES, LANE_LABEL, TERRY_EDGES, CLAUDE_EDGES, _rules_mtime  # noqa: PLW0603

    try:
        now = RULES_PATH.stat().st_mtime
    except OSError:
        return None
    if now == _rules_mtime:
        return None
    _rules_mtime = now

    try:
        lanes, rules, priorities, labels, default = _load_rules(RULES_PATH)
    except (BoardError, OSError, ValueError) as exc:
        return f"rules.json changed and was REFUSED: {exc}. Keeping the loaded table."

    # **Validated against the NEW table before it is installed**, by swapping in,
    # checking, and swapping back on failure. `check_edges()` reads the globals, so
    # there is no way to ask it about a table that is not currently bound.
    keep = (LANES, RULES, PRIORITIES, PRIORITY_LABEL, DEFAULT_PRIORITY,
            STATES, LANE_LABEL, TERRY_EDGES, CLAUDE_EDGES)
    LANES, RULES, PRIORITIES, PRIORITY_LABEL, DEFAULT_PRIORITY = (
        lanes, rules, priorities, labels, default)
    STATES = tuple(state for state, _ in LANES)
    LANE_LABEL = dict(LANES)
    problems = check_edges()
    if problems:
        (LANES, RULES, PRIORITIES, PRIORITY_LABEL, DEFAULT_PRIORITY,
         STATES, LANE_LABEL, TERRY_EDGES, CLAUDE_EDGES) = keep
        return ("rules.json changed and was REFUSED: "
                + "; ".join(problems) + ". Keeping the loaded table.")

    TERRY_EDGES = edges_for("terry")
    CLAUDE_EDGES = edges_for("claude")
    return f"rules.json reloaded: {len(STATES)} lanes, {len(TERRY_EDGES)} terry edges"


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
    # **An OWNERSHIP change has no lane transition**, so `to` is empty on those and
    # these two carry the actors instead. Card #0053.
    #
    # **This keeps the history a log of CHANGES rather than turning it into a general
    # event log**, which is what Terry's clarification bought: *"initial ticket
    # ownership assignment is NOT to be in the audit log, that's clear by ticket
    # creation timestamp."* Only a REASSIGNMENT is an event, so these entries are rare
    # and no existing card needs a synthetic one.
    owner_frm: str | None = None
    owner_to: str | None = None

    @property
    def is_owner_change(self) -> bool:
        """True for an ownership entry, which moves no lane."""
        return self.owner_to is not None

    def to_json(self) -> dict[str, str]:
        out = {"at": self.at, "to": self.to, "by": self.by}
        if self.frm is not None:
            out["from"] = self.frm
        if self.owner_to is not None:
            out["ownerTo"] = self.owner_to
        if self.owner_frm is not None:
            out["ownerFrom"] = self.owner_frm
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any], where: str) -> Self:
        owner_to = raw.get("ownerTo")
        # **`to` is required on a LANE entry and absent on an OWNERSHIP one.** Checking
        # it unconditionally would refuse every board written after this change.
        required = ("at", "by") if isinstance(owner_to, str) else ("at", "to", "by")
        for key in required:
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise BoardError(f"{where}: history entry has no {key}")
        frm = raw.get("from")
        owner_frm = raw.get("ownerFrom")
        return cls(at=raw["at"], to=raw.get("to", ""), by=raw["by"],
                   frm=frm if isinstance(frm, str) else None,
                   owner_frm=owner_frm if isinstance(owner_frm, str) else None,
                   owner_to=owner_to if isinstance(owner_to, str) else None)


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
    # **EXACTLY ONE OWNER, and it is a LABEL rather than a permission.** Terry,
    # 2026-08-19: *"It's just a label, not permissions model."* Card #0053.
    #
    # **NO CODE PATH MAY CONSULT THIS TO ALLOW OR REFUSE ANYTHING.** Not `may_move`,
    # not `may_create`, not the drag handler, not `/create`. `rules.json` answers *who
    # may do what*; this answers *who is carrying it*. **Joining them would create a
    # second authorization mechanism that `check_edges()` cannot see**, contradicting
    # the one that is actually enforced.
    #
    # **Terry can move a card Claude owns, and Claude can move a card Terry owns.**
    # That is not a bug to close.
    #
    # **Defaults to `claude` on his standing order**: *"if in doubt, assign to claude
    # and it'll get fixed as ticket progresses."* An error that lands on Claude gets
    # corrected the moment the work starts; one that lands on Terry sits in his lane
    # until he notices.
    owner: str = "claude"
    # **HIERARCHY IS A FIELD, NOT A RELATIONSHIP. Card #0028.**
    #
    # Jira, Linear and GitHub all keep parent/child out of their relationship table, and
    # Terry approved following them. **A tree needs two rules a symmetric table cannot
    # state**: a card has at most ONE parent, and the chain must not loop. Both are
    # checked in `Board.from_json` and in `set_parent`.
    #
    # It holds the parent's `id`, and `None` means top level.
    parent: str | None = None
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

        **OWNERSHIP ENTRIES ARE SKIPPED, and forgetting that was a real defect.** Card
        #0053 gave `Change` an ownership form that carries no lane, so `to` is `""` on
        those. This read `history[-1].to` and returned `""` for any card whose most
        recent event was a reassignment -- **a wrong lane, silently, rather than an
        error.**

        **Caught 2026-08-19 on card #0003**, whose last entry was Terry handing it
        back. `verify()` was never affected because it filters the same entries before
        its own replay; **the filter was applied there and not here**, which is the
        instance being fixed rather than the class.
        """
        lanes = [c for c in self.history if not c.is_owner_change]
        return lanes[-1].to if lanes else None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "ticket": self.ticket,
            "priority": self.priority,
            "state": self.state,
            "subject": self.subject,
            "detail": self.detail,
            "owner": self.owner,
            "history": [c.to_json() for c in self.history],
        }
        # Omitted at top level, so 60-odd parentless cards stay readable.
        if self.parent:
            out["parent"] = self.parent
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
        # **"Exactly one owner" is ENFORCED here rather than assumed.** An unknown
        # actor is refused outright: a card owned by nobody, or by a name no lane
        # header can render, is a data error and `from_json` REFUSES rather than
        # repairs -- the same contract the rest of this class keeps.
        owner = raw.get("owner", "claude")
        if owner not in ("terry", "claude"):
            raise BoardError(f"{where}: unknown owner {owner!r}")
        return cls(
            id=raw["id"],
            subject=raw["subject"],
            state=raw["state"],
            ticket=ticket,
            priority=priority,
            detail=raw.get("detail", "") or "",
            # **A card with no `owner` reads as Claude's**, which is the migration for
            # the 51 cards written before this field existed. Terry's standing order:
            # *"if in doubt, assign to claude."* No synthetic history entry is written
            # for them, because initial ownership is not an audit event.
            owner=owner,
            # **Existence and cycles are checked in `Board.from_json`, not here.** An item
            # cannot see its siblings, so this only records what the file said.
            parent=str(raw["parent"]) if raw.get("parent") else None,
            history=[Change.from_json(h, where) for h in raw.get("history", [])],
            comments=[Comment.from_json(c, where) for c in raw.get("comments", [])],
        )


def _links_from_json(raw: Any, known: set[str], where: str) -> list[Link]:  # noqa: ANN401
    """Validate the board's link table. **Extracted from `Board.from_json`**, which ruff
    correctly called too branchy once this landed in it. Card #0028."""
    if not isinstance(raw, list):
        raise BoardError(f"{where}: 'links' is not a list")
    links: list[Link] = []
    pairs: set[tuple[str, str, str]] = set()
    for index, link_raw in enumerate(raw):
        link = Link.from_json(link_raw, f"{where}: links[{index}]")
        for end in (link.frm, link.to):
            if end not in known:
                raise BoardError(f"{where}: links[{index}] names unknown card {end!r}")
        if link.frm == link.to:
            raise BoardError(f"{where}: links[{index}] joins {link.frm!r} to itself")
        # **A duplicate is checked in BOTH directions, because `relates_to` is its own
        # inverse.** `a relates_to b` and `b relates_to a` are one claim written two
        # ways, and letting both in would show the relationship on each card twice.
        key = (link.frm, link.kind, link.to)
        mirror = (link.to, link.kind, link.frm)
        if key in pairs or (link.kind == "relates_to" and mirror in pairs):
            raise BoardError(f"{where}: links[{index}] repeats an existing link")
        pairs.add(key)
        links.append(link)
    return links


def _check_parents(board: "Board", known: set[str], where: str) -> None:
    """Refuse a parent that does not exist or that closes a loop. Card #0028.

    **It runs once the whole board exists**, because a parent is a sibling and no item
    can validate its own.
    """
    for item in board.items:
        if item.parent is None:
            continue
        if item.parent not in known:
            raise BoardError(f"{where}: {item.label} names unknown parent {item.parent!r}")
        if cycle := board.parent_cycle(item):
            raise BoardError(f"{where}: parent cycle {' -> '.join(cycle)}")


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

    # **Every relationship on the board, each stored ONCE.** See `Link` for why this is
    # here rather than a copy on each card. Card #0028.
    links: list[Link] = field(default_factory=list)

    # ---- serialization -------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema": SCHEMA,
            "project": self.project,
            "port": self.port,
            "nextTicket": self.next_ticket,
            "items": [item.to_json() for item in self.items],
        }
        # **Omitted while empty, which is also the migration.** Every board written before
        # card #0028 simply has no `links` key, and reads back as a board with no links.
        if self.links:
            out["links"] = [link.to_json() for link in self.links]
        return out

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

        board = cls(project=str(raw.get("project", "")), port=port, items=items,
                    next_ticket=next_ticket,
                    links=_links_from_json(raw.get("links", []), seen, where))
        _check_parents(board, seen, where)
        return board

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

            # **OWNERSHIP ENTRIES MOVE NO LANE, so they are removed before the replay.**
            # Card #0053. Replaying one as a transition would break the chain check on
            # every reassignment, and a permission model that refuses the board after an
            # ownership change is worse than no ownership field at all.
            #
            # **They are checked on their own terms instead**, below.
            lane_history = [c for c in item.history if not c.is_owner_change]
            problems.extend(
                f"{item.id}: history reassigns to {c.owner_to!r}, which is not an actor"
                for c in item.history
                if c.is_owner_change and c.owner_to not in ("terry", "claude"))
            if not lane_history:
                continue

            first = lane_history[0]
            if first.frm is None and not may_create(first.by, first.to):
                problems.append(
                    f"{item.id}: history says {first.by} created it in {first.to}, "
                    f"which {first.by} may not do")

            where: str | None = None
            for index, change in enumerate(lane_history):
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

    def set_priority(self, item_id: str, priority: str, by: Actor) -> str:
        """Change one card's priority. Card #0060.

        **No history entry, and no permission check.** Priority is a sorting key, not
        a state transition -- the same reasoning that keeps ownership out of the lane
        replay and a project rename out of the trail. **`verify()` replays LANES**, and
        an entry with neither a lane nor an owner would be a third shape for it to
        learn.

        **Either actor may set it.** Terry decides what matters; Claude files cards and
        gets the guess wrong sometimes. A permission here would only make a correction
        need a round trip.
        """
        if priority not in PRIORITIES:
            raise BoardError(
                f"unknown priority {priority!r}; want one of {', '.join(PRIORITIES)}")
        item = self.find(item_id)
        was = item.priority
        if was == priority:
            return f"{item.label} is already {priority}"
        item.priority = priority
        return f"{item.label} priority: {was} -> {priority} (by {by})"

    # ---- relationships, card #0028 -------------------------------------------

    def parent_cycle(self, item: Item) -> list[str] | None:
        """The loop this card's parent chain falls into, or None. Card #0028.

        **A tree is the one shape a symmetric relationship table cannot express**, and
        this is half the reason `parent` is a field rather than a link kind. The other
        half is "at most one parent", which the field gives for free.

        **It walks rather than recurses, and it stops on the first repeat**, so a board
        that somehow reaches disk with a loop reports it instead of hanging.
        """
        by_id = {i.id: i for i in self.items}
        seen: list[str] = [item.id]
        at = item.parent
        while at is not None:
            if at in seen:
                return [*seen, at]
            seen.append(at)
            nxt = by_id.get(at)
            if nxt is None:
                return None
            at = nxt.parent
        return None

    def set_parent(self, child_id: str, parent_ref: str | None, by: Actor) -> str:
        """Give a card a parent, or clear it with `None`. Card #0028.

        **No history entry**, for the reason `set_priority` gives: `verify()` replays
        LANES and OWNERS, and a third shape would have to be taught to it. Git is the
        trail.
        """
        child = self.find(child_id)
        if parent_ref is None:
            if child.parent is None:
                return f"{child.label} already has no parent"
            was = child.parent
            child.parent = None
            return f"{child.label} parent cleared (was {was}, by {by})"

        parent = self.find(parent_ref)
        if parent.id == child.id:
            raise BoardError(f"{child.label} cannot be its own parent")
        was_parent = child.parent
        child.parent = parent.id
        # **Set it, then check, then put it back.** Testing the loop before the write
        # would have to simulate the new edge anyway, and this way the check reads the
        # same board every other caller does.
        if cycle := self.parent_cycle(child):
            child.parent = was_parent
            raise BoardError(f"that would make a parent cycle: {' -> '.join(cycle)}")
        return f"{child.label} parent: {was_parent or 'none'} -> {parent.id} (by {by})"

    def link(self, a_ref: str, kind: str, b_ref: str, by: Actor) -> str:
        """Relate two cards. **There is no way to write one half.** Card #0028.

        **It takes BOTH cards, which is the structural answer to Terry's requirement**
        that an inconsistent relationship *"MUST NOT be allowed to be possible"*. A
        function taking one card and one direction could write a dangling half; this one
        cannot express that.

        **An inverse spelling is normalized to the stored one.** `--link 5 blocked_by 28`
        becomes `28 blocks 5`, so the file holds exactly one spelling of each fact.
        """
        if kind not in LINK_INVERSE:
            raise BoardError(
                f"unknown relationship {kind!r}; want one of "
                f"{', '.join(sorted(LINK_INVERSE))}")
        a, b = self.find(a_ref), self.find(b_ref)
        if a.id == b.id:
            raise BoardError(f"{a.label} cannot be related to itself")
        frm, to, stored = a, b, kind
        if kind not in LINK_CANONICAL:
            frm, to, stored = b, a, LINK_INVERSE[kind]
        if self.find_link(frm.id, stored, to.id) is not None:
            return f"{a.label} already {kind} {b.label}"
        self.links.append(Link(frm=frm.id, kind=stored, to=to.id))
        return f"{a.label} {kind} {b.label} (by {by})"

    def find_link(self, frm: str, kind: str, to: str) -> Link | None:
        """The stored row for this relationship, in either spelling, or None."""
        for link in self.links:
            if link.kind != kind:
                continue
            if (link.frm, link.to) == (frm, to):
                return link
            # `relates_to` is its own inverse, so the row may be written either way.
            if kind == "relates_to" and (link.frm, link.to) == (to, frm):
                return link
        return None

    def unlink(self, a_ref: str, kind: str, b_ref: str, by: Actor) -> str:
        """Remove a relationship. Removing one half removes the whole thing, because
        there is only one row. Card #0028."""
        if kind not in LINK_INVERSE:
            raise BoardError(f"unknown relationship {kind!r}")
        a, b = self.find(a_ref), self.find(b_ref)
        frm, to, stored = a, b, kind
        if kind not in LINK_CANONICAL:
            frm, to, stored = b, a, LINK_INVERSE[kind]
        found = self.find_link(frm.id, stored, to.id)
        if found is None:
            return f"{a.label} is not {kind} {b.label}"
        self.links.remove(found)
        return f"{a.label} no longer {kind} {b.label} (by {by})"

    def links_for(self, item_id: str) -> list[tuple[str, str]]:
        """`(relationship as this card sees it, other card's id)`, sorted. Card #0028.

        **This is where the second half comes from.** A row saying `28 blocks 5` is read
        by card 5 as `blocked_by 28`, computed through `LINK_INVERSE` rather than stored.
        """
        out: list[tuple[str, str]] = []
        for link in self.links:
            if link.frm == item_id:
                out.append((link.kind, link.to))
            elif link.to == item_id:
                out.append((LINK_INVERSE[link.kind], link.frm))
        return sorted(out)

    def set_detail(self, item_id: str, detail: str, by: Actor) -> str:
        """Replace one card's description. Card #0028's second lesson.

        **A description was WRITE-ONCE until 2026-08-19, and that was an accident.**
        Terry read card #0028 and answered *"wall of text ELI5, try again in human
        readable fashion"*. Nothing could try again. Claude could only apologize in a
        comment under the wall, which leaves the wall.

        **No history entry, for the reason `set_priority` gives.** `verify()` replays
        LANES and OWNERS, and prose is neither. **The audit trail for the text is git**
        -- the board file is committed after every write, so the old description is one
        `git diff` away and never rides in the JSON twice.

        **An empty description is REFUSED.** Blanking a card is far more likely to be a
        shell that ate the argument than a thing somebody meant, and the old text is
        gone either way.
        """
        text = detail.rstrip("\n")
        if not text.strip():
            raise BoardError("refusing to blank a description; pass real text")
        item = self.find(item_id)
        was = len(item.detail)
        if item.detail == text:
            return f"{item.label} description is already that text"
        item.detail = text
        return f"{item.label} description: {was} -> {len(text)} chars (by {by})"

    def assign(self, item_id: str, owner: Actor, by: Actor) -> str:
        """Reassign one card's owner, appending to its history. Card #0053.

        **EITHER ACTOR MAY REASSIGN EITHER WAY, and that is not an oversight.** Terry:
        *"Terry and Claude MUST be able to reassign ownership between the two"*, and
        *"It's just a label, not permissions model."* **So there is no `may_` check
        here and there MUST NOT be one** -- adding a permission to this path would be
        exactly the second authorization mechanism the field is defined not to be.

        **A no-op reassignment writes NOTHING.** An audit trail that records "Terry set
        the owner to Terry" is noise in the one place noise is expensive, and Terry
        signalling he is working a card can arrive repeatedly.

        **Write the log first, then the field**, for the same reason `move()` does: a
        state change that never reached the trail is indistinguishable from tampering.
        """
        if owner not in ("terry", "claude"):
            raise BoardError(f"unknown owner {owner!r}")
        item = self.find(item_id)
        was = item.owner
        if was == owner:
            return f"{item.label} is already owned by {owner}"
        item.history.append(Change(at=now(), to="", by=by,
                                   owner_frm=was, owner_to=owner))
        item.owner = owner
        return f"{item.label} ownership change: {was} -> {owner} (by {by})"

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
        made, missing = self._links_from_text(item, text, by)
        note = ""
        if made:
            note += f"; references {', '.join(made)}"
        # **A TYPO IS REPORTED, NOT SWALLOWED.** Card #0028 asks what happens when the
        # named ticket does not exist, and silence is the wrong answer: the comment
        # stands either way, so a quiet miss looks exactly like a link that worked.
        if missing:
            note += f"; NO SUCH TICKET: {', '.join(missing)}"
        return f"{item_id}: comment by {by}{note}"

    # **`#0028` and `#28` both count, and a bare `28` does not.** Requiring the hash keeps
    # ordinary prose -- "28 tests", "step 12" -- from silently wiring cards together.
    TICKET_MENTION = re.compile(r"#(\d{1,6})\b")

    def _links_from_text(self, item: Item, text: str,
                         by: Actor) -> tuple[list[str], list[str]]:
        """Add a `references` link for each `#nnnn` a comment names. Card #0028.

        **Terry's example, verbatim:** *"If I tag ticket 9876 in a comment with 'See
        #9876' that should add a 'references #9876' in source ticket auto add a
        'referenced by' relationship to ticket 9876."*

        **Only one row is written**, and card 9876 reads it as `referenced_by` through
        `LINK_INVERSE`. The second half needs no code.

        **A comment is append-only, so a link it created is never retracted here.** That
        answers the card's second open question by construction rather than by policy:
        there is no edit path that could trigger a retraction.
        """
        made: list[str] = []
        missing: list[str] = []
        for hit in self.TICKET_MENTION.finditer(text):
            ref = hit.group(1)
            try:
                other = self.find(ref)
            except BoardError:
                label = f"#{int(ref):04d}"
                if label not in missing:
                    missing.append(label)
                continue
            if other.id == item.id:
                continue
            if self.find_link(item.id, "references", other.id) is not None:
                continue
            self.links.append(Link(frm=item.id, kind="references", to=other.id))
            made.append(other.label)
        del by
        return made, missing


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
        _replace_with_retry(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# **MEASURED, not guessed: 7 failures in 400 saves, 1.75%.** Reproduced 2026-08-19 by
# hammering `save()` on the SMB share while a `serve.py` polled the same file.
#
# Roughly 47 saves a second there, so the window is small and real. `X:` is an SMB
# share and the server opens `board.json` every `POLL_MS = 400`; on Windows
# `os.replace` fails with `ERROR_ACCESS_DENIED` when the target is open in another
# process without `FILE_SHARE_DELETE`, which Python's `open()` does not request.
RENAME_TRIES = 6
RENAME_PAUSE = 0.04


def _replace_with_retry(tmp: pathlib.Path, target: pathlib.Path) -> None:
    """Rename the temp file over the target, retrying a transient WinError 5.

    **BOUNDED, and it RAISES at the end.** A swallowed failure would be a lost write
    reporting success, which is exactly the defect card #0032 exists to prevent.

    **It MUST NOT fall back to a non-atomic write.** A refused save is recoverable --
    the caller sees an exception and the old file is untouched. A half-written
    `board.json` is not, and it would take every card ever recorded with it.

    **Only `PermissionError` is retried.** A missing directory or a full disk will not
    fix itself in 40 ms, and retrying those would turn a clear failure into a slow one.
    """
    for attempt in range(RENAME_TRIES):
        try:
            tmp.replace(target)
        except PermissionError:
            if attempt == RENAME_TRIES - 1:
                raise
            time.sleep(RENAME_PAUSE)
        else:
            return


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
    # **Both actors are offered, unlike `--state` above.** Ownership is a LABEL rather
    # than a permission -- card #0053 -- so either actor may be assigned either way and
    # there is no lane-style restriction to encode here.
    ap.add_argument("--assign", nargs=2, metavar=("ID", "OWNER"),
                    help="reassign one card's owner between terry and claude")
    # **Board METADATA gets a flag rather than a hand edit.** Card #0050. The standing
    # order is that Claude writes to the board THROUGH THE LIBRARY, and the usual
    # reasons -- `may_create`, `nextTicket`, the creation history entry -- do not reach
    # a metadata field. **The rule still wins, for a different reason:** `--verify`
    # replays card histories and cannot catch a bad metadata edit at all, which is an
    # argument for keeping hands out of the file rather than a license to reach in.
    #
    # **`port` has the same shape and will want the same treatment.**
    ap.add_argument("--set-project", metavar="NAME",
                    help="rename the board's project field")
    # **Priority was set-once until 2026-08-19.** `--priority` above only decorates
    # `--create`, so a card filed at the wrong priority could not be corrected from the
    # CLI at all -- and the drawer has no control for it either. Terry asked to move a
    # card to P1 and there was no way to do it. Card #0060.
    ap.add_argument("--set-priority", nargs=2, metavar=("ID", "PRIORITY"),
                    help="change one card's priority")
    # **A card's description was WRITE-ONCE until 2026-08-19.** Terry read one and
    # said *"wall of text ELI5, try again in human readable fashion"* -- and there was
    # no way to try again. Only comments could be added, so a bad description could be
    # apologized for and never fixed.
    #
    # **Takes its text from `--detail` or `--detail-file`, the same pair `--create`
    # uses**, so the shell-quoting lesson is inherited rather than repeated.
    ap.add_argument("--set-detail", metavar="ID",
                    help="replace one card's description; use --detail or --detail-file")
    # **Card #0028. Both cards in one call, always.** The relationship is stored once and
    # the other direction is derived, so there is no call shape that writes half of one.
    ap.add_argument("--link", nargs=3, metavar=("ID", "KIND", "OTHER"),
                    help=f"relate two cards; KIND is one of {', '.join(sorted(LINK_INVERSE))}")
    ap.add_argument("--unlink", nargs=3, metavar=("ID", "KIND", "OTHER"),
                    help="remove a relationship between two cards")
    ap.add_argument("--set-parent", nargs=2, metavar=("CHILD", "PARENT"),
                    help="put one card under another; refuses a cycle")
    ap.add_argument("--clear-parent", metavar="CHILD",
                    help="move a card back to the top level")
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

    if any(getattr(args, name) for name in MUTATIONS):
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
    for name, handler in HANDLERS.items():
        if getattr(args, name):
            return handler(board, args)
    # **Unreachable via `main`'s guard, and it MUST stay a raise anyway.** Falling off
    # the end would return `None`, which prints as `None` and saves an unchanged
    # board -- a silent no-op of exactly the kind this table exists to prevent.
    raise BoardError(f"no mutation requested; one of {', '.join(MUTATIONS)} is needed")


def _do_create(board: Board, args: argparse.Namespace) -> str:
    return board.create(args.create[0], args.create[1], args.state, "claude",
                        priority=args.priority, detail=_detail_text(args))


def _do_assign(board: Board, args: argparse.Namespace) -> str:
    return board.assign(args.assign[0], as_actor(args.assign[1]), "claude")


def _do_set_project(board: Board, args: argparse.Namespace) -> str:
    """Rename the board. **No history entry, and that is consistent rather than lazy.**

    The trail belongs to CARDS -- `verify()` replays per-item histories -- and board
    metadata has no card to attach to. Same reasoning that keeps initial ownership out
    of the log: a property, not an event.
    """
    was = board.project
    name = args.set_project.strip()
    if not name:
        raise BoardError("a project needs a name")
    if name == was:
        return f"project is already {name!r}"
    board.project = name
    return f"project renamed: {was!r} -> {name!r}"


# **ONE TABLE, so a write flag cannot be added in one place and forgotten in another.**
#
# This replaced a literal `if args.move or args.comment or args.create:` in `main` plus
# a matching if-chain here. **`--assign` was added to argparse and to the chain and NOT
# to the condition on 2026-08-19**, so it fell through to the REPORT path: it printed
# the whole board and exited 0. A write that silently became a read, and reported
# success.
#
# **`MUTATIONS` is now DERIVED from these keys rather than maintained beside them**, so
# the two cannot disagree. The key is the argparse `dest`.
HANDLERS: dict[str, Callable[[Board, argparse.Namespace], str]] = {
    "create": _do_create,
    "move": lambda b, a: b.move(a.move[0], a.move[1], "claude"),
    "assign": _do_assign,
    "comment": lambda b, a: b.comment(a.comment[0], a.comment[1], "claude"),
    "set_project": _do_set_project,
    "set_priority": lambda b, a: b.set_priority(
        a.set_priority[0], a.set_priority[1].upper(), "claude"),
    "set_detail": lambda b, a: b.set_detail(a.set_detail, _detail_text(a), "claude"),
    "link": lambda b, a: b.link(a.link[0], a.link[1].lower(), a.link[2], "claude"),
    "unlink": lambda b, a: b.unlink(a.unlink[0], a.unlink[1].lower(), a.unlink[2],
                                    "claude"),
    "set_parent": lambda b, a: b.set_parent(a.set_parent[0], a.set_parent[1], "claude"),
    "clear_parent": lambda b, a: b.set_parent(a.clear_parent, None, "claude"),
}

MUTATIONS = tuple(HANDLERS)


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
