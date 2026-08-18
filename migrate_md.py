"""One-shot: turn a `docs/WORK-LOG.md` Open/Landed pair into a board JSON file.

**This exists once, to carry 2026-08-18's work across, and it MUST NOT become a
supported input format.** The markdown tables are what the JSON file replaces; keeping
a live importer would keep the parser this project deleted.

**Ids are slugged from the subject and checked for collisions**, because an id is
permanent and a duplicate would silently merge two cards. It refuses rather than
disambiguating -- a generated `-2` suffix is a decision nobody made.

Run it, read the output, then commit the JSON. Nothing writes without `--apply`.
"""

import argparse
import pathlib
import re
import sys

import status

OPEN_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(P[0-5])\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*$"
)
LANDED_RE = re.compile(
    r"^\|\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2})?"
    r"(?:\s*[A-Za-z]{1,5})?)?)\s*\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*$"
)
END_HEADINGS = ("## Landed", "## Not an item yet", "## Open")

# The markdown used `not_started`; the board calls that lane `backlog`. Every other
# state kept its name, which is why only one entry is here.
RENAME = {"not_started": "backlog"}


def region(text: str, heading: str) -> list[str]:
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == heading:
            inside = True
            continue
        if inside and stripped in END_HEADINGS:
            break
        if inside:
            out.append(line)
    return out


def slug(subject: str) -> str:
    """A short, stable, readable id.

    **Readable matters more than short.** These end up in commit messages and in
    conversation, and `argparse-gate` is something a person can say out loud where a
    hash is not.
    """
    words = re.findall(r"[a-z0-9]+", subject.lower())
    skip = {"the", "a", "an", "to", "for", "of", "on", "in", "and", "can", "is", "it"}
    kept = [w for w in words if w not in skip] or words
    return "-".join(kept[:4])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("markdown", type=pathlib.Path, help="the WORK-LOG.md to read")
    ap.add_argument("out", type=pathlib.Path, help="the board JSON to write")
    ap.add_argument("--project", default="", help="project name for the board header")
    ap.add_argument("--apply", action="store_true", help="write the file")
    args = ap.parse_args()

    text = args.markdown.read_text(encoding="utf-8")
    items: list[status.Item] = []
    ids: dict[str, str] = {}
    clashes: list[str] = []

    for line in region(text, "## Open"):
        m = OPEN_RE.match(line)
        if m is None:
            continue
        subject = m.group(4)
        key = slug(subject)
        if key in ids:
            clashes.append(f"{key}: {ids[key]!r} and {subject!r}")
        ids[key] = subject
        state = RENAME.get(m.group(3), m.group(3))
        items.append({
            "id": key,
            "priority": m.group(2),
            "state": state,
            "subject": subject,
            "detail": m.group(5),
            # **No history is invented.** These rows predate the history field, and a
            # fabricated transition would be worse than an empty list -- it would look
            # like evidence. The first real move appends the first entry.
            "history": [],
        })

    for line in region(text, "## Landed"):
        m = LANDED_RE.match(line)
        if m is None:
            continue
        subject = m.group(2)
        key = slug(subject)
        if key in ids:
            clashes.append(f"{key}: {ids[key]!r} and {subject!r}")
        ids[key] = subject
        items.append({
            "id": key,
            "priority": status.DEFAULT_PRIORITY,
            "state": "completed",
            "subject": subject,
            "detail": m.group(3),
            # **The one history entry that IS real.** The Landed table's date column is
            # the signoff time, and Terry is the only actor who can produce one.
            "history": [{"at": m.group(1), "to": "completed", "by": "terry"}],
        })

    board: status.Board = {
        "schema": status.SCHEMA,
        "project": args.project,
        "items": items,
    }

    print(f"  {len(items)} item(s) from {args.markdown}")
    for lane in status.lanes(board):
        if lane.items:
            print(f"    {lane.label:<20} {len(lane.items)}")
    print()
    for item in items:
        # `.get` rather than `[...]`: `priority` is OPTIONAL on the `Item` TypedDict,
        # and pyright is right to insist. Every card built above sets one, but the
        # type does not promise it. **This is the friction that argues for
        # dataclasses** -- a field with a default would simply be there.
        pri = item.get("priority", status.DEFAULT_PRIORITY)
        print(f"    {pri}  {item['state']:<19} "
              f"{item['id']:<28} {item['subject'][:44]}")

    if clashes:
        print(f"\n  REFUSED: {len(clashes)} id collision(s). Rename a subject or widen slug():")
        for c in clashes:
            print(f"      {c}")
        sys.exit(1)

    if not args.apply:
        print("\n  DRY RUN. Pass --apply to write.")
        return

    status.save(board, args.out)
    # Reading it back proves the validator accepts what the writer produced, which a
    # successful write does not.
    back = status.load(args.out)
    print(f"\n  WROTE {args.out} -- reloaded and validated, {len(back['items'])} items.")
    print(f"  {args.out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
