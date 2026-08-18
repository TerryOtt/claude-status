"""A live, draggable swimlane board over one `board.json`, served on loopback.

    python serve.py path/to/board.json
    then open http://127.0.0.1:8792/

**RFC 2119 keywords, and the capitals are load-bearing.**

## What it is for

**Terry and Claude need ONE artifact instead of two views held equal by a contract.**
The earlier arrangement had a markdown log plus the harness task panel, and the whole
sync problem existed only because there were two copies. **A browser tab reading the
same file Claude writes cannot diverge from it.**

**It also lifts the panel's size limit.** The harness panel gives Terry about five
lines, so the log had to be trimmed to fit a window it does not control.

## IT WRITES, and that is safe here for a reason Trello could not offer

**Terry drags cards.** That is a write path by definition, and it was added
deliberately on 2026-08-18.

**The same afternoon, the official Trello MCP server was connected and removed within
the hour**, because its OAuth grant authenticates Claude AS Terry -- a card Claude
moved and a card Terry moved were the same event by the same member, so his signoff
stopped being provable.

**This server binds to loopback.** Whoever reaches it is sitting at his machine, so a
drag IS Terry: no identity to forge, no token to leak, and the `by` field in an item's
history can be trusted.

**`status.TERRY_EDGES` is the guard rail**, re-checked on the server for every request.
The browser carries the same list only so the cursor can answer without a round trip.
**A guard that lives only in the client is decoration.**

## Three staleness edges, and each looks like the server being broken

**A change to the BOARD FILE is live**, picked up within `POLL_MS`.

**A change to THIS FILE or to `status.py` needs a RESTART**, because the server
imports the parser once at startup.

**A change to the PAGE needs a BROWSER RELOAD on top of that.** The open tab is still
running the script it was served, which produces a genuinely confusing halfway state:
rows render correctly because their content comes from `/data`, while new CSS and new
counts do not, because those live in the page. **Half the change appearing is more
disorienting than none of it.**

## The browser rules this had to satisfy

  * **Loopback is exempt from mixed-content blocking, and `127.0.0.1` is the address
    that gets the exemption.** A LAN address is a plain insecure origin and Chrome
    blocks it.
  * **Chrome may gate loopback behind a Local Network Access prompt** on a fresh
    profile. The symptom looks exactly like a server that is not running.
"""

import argparse
import contextlib
import datetime
import html
import http.server
import json
import pathlib
import re

import status

HOST = "127.0.0.1"
DEFAULT_PORT = 8792

# Far below the time a human takes to switch windows, and a stat() against a local
# file rather than anything on a network.
POLL_MS = 400

# **Inline markdown only, and that is a deliberate scope.** A detail field carries
# `code`, **bold** and *italic* and nothing else. A markdown library for one field
# would be a dependency for a job this size.
INLINE = (
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\*([^*]+)\*"), r"<em>\1</em>"),
)

BOARD: pathlib.Path = pathlib.Path("board.json")


def inline(text: str) -> str:
    """Escape HTML, THEN apply the three inline spans.

    **That order is the whole safety of it.** Detail fields are written by Claude and
    read by Terry, so this is about a stray `<` in a path rendering as text rather
    than about an attacker -- but reversing the order would escape the markup this
    function just produced and leave the content raw.
    """
    out = html.escape(text)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    return out


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>%TITLE%</title>
<style>
  /* LIGHT, because Terry asked for it in exactly those words: "turn off dark mode
     I hate it." No media query and no toggle -- one look, chosen. */
  :root {
    --bg: #F4F5F7; --lane: #EBECF0; --card: #FFFFFF;
    --ink: #172B4D; --dim: #5E6C84; --line: #DFE1E6;
    --terry: #0052CC; --claude: #E2A100; --handoff: #1F845A; --done: #5E6C84;
    --p0: #C9372C; --p1: #E56910; --p2: #E2A100;
    --p3: #5E6C84; --p4: #8993A4; --p5: #B3BAC5;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 14px/1.5 "Segoe UI", system-ui, sans-serif; }

  #bar { position: sticky; top: 0; z-index: 20; display: flex; gap: 14px;
         align-items: center; padding: 8px 14px; background: #FFFFFF;
         border-bottom: 1px solid var(--line); font-size: 12px; }
  #bar .grow { flex: 1; }
  #title { font-weight: 700; }
  #counts { color: var(--dim); }
  #dot { width: 8px; height: 8px; border-radius: 50%; background: var(--handoff); }
  #dot.stale { background: var(--p0); }
  .meta { color: var(--dim); }

  /* One row of lanes, scrolled sideways. Seven columns do not fit a laptop at a
     readable card width, and shrinking them to fit is what makes a board useless. */
  #board { display: flex; gap: 10px; padding: 12px; align-items: flex-start;
           overflow-x: auto; min-height: calc(100vh - 46px); }
  .lane { background: var(--lane); border-radius: 8px; width: 296px;
          flex: 0 0 296px; display: flex; flex-direction: column;
          max-height: calc(100vh - 70px); border-top: 3px solid var(--dim); }
  .lane[data-owner="terry"]   { border-top-color: var(--terry); }
  .lane[data-owner="claude"]  { border-top-color: var(--claude); }
  .lane[data-owner="handoff"] { border-top-color: var(--handoff); }
  .lane[data-owner="done"]    { border-top-color: var(--done); }

  .lane h2 { margin: 0; padding: 9px 12px 4px; font-size: 12px; font-weight: 700;
             text-transform: uppercase; letter-spacing: .5px;
             display: flex; gap: 8px; align-items: center; }
  .lane h2 .n { margin-left: auto; background: #FFFFFF; border-radius: 10px;
                padding: 0 7px; font-size: 11px; color: var(--dim);
                font-weight: 600; }
  /* Ownership is stated in words under every lane title, not implied by a color.
     Terry: "Real clear ownership per lane." A legend somewhere else would make him
     remember which color meant what. */
  .owner { padding: 0 12px 8px; font-size: 10px; font-weight: 700;
           letter-spacing: .8px; color: var(--dim); }
  .lane[data-owner="terry"]   .owner { color: var(--terry); }
  .lane[data-owner="claude"]  .owner { color: var(--claude); }
  .lane[data-owner="handoff"] .owner { color: var(--handoff); }
  .owner .ro { font-weight: 600; letter-spacing: 0; text-transform: none;
               color: var(--dim); }

  .cards { padding: 0 8px 10px; overflow-y: auto; flex: 1; }
  /* The drop target is the whole lane, so a card can be released anywhere in the
     column rather than onto a precise gap. */
  .lane.over { outline: 2px solid var(--terry); outline-offset: -2px; }
  .lane.deny { outline: 2px dashed var(--p0); outline-offset: -2px; }

  .card { background: var(--card); border-radius: 6px; padding: 8px 10px 9px;
          margin-bottom: 8px; box-shadow: 0 1px 1px rgba(9,30,66,.25); }
  .card.draggable { cursor: grab; }
  .card.dragging { opacity: .45; }
  .card .top { display: flex; gap: 7px; align-items: center; }
  .pri { font-size: 10px; font-weight: 700; color: #FFFFFF; border-radius: 3px;
         padding: 1px 5px; letter-spacing: .3px; }
  .pri.P0 { background: var(--p0); } .pri.P1 { background: var(--p1); }
  .pri.P2 { background: var(--p2); } .pri.P3 { background: var(--p3); }
  .pri.P4 { background: var(--p4); } .pri.P5 { background: var(--p5); }
  .card .subject { font-weight: 600; font-size: 13px; }
  .card .detail { color: var(--dim); font-size: 12px; margin-top: 5px;
                  overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3;
                  line-clamp: 3; -webkit-box-orient: vertical; }
  .card.open .detail { -webkit-line-clamp: unset; line-clamp: unset; }
  .card .more { color: var(--terry); font-size: 11px; cursor: pointer;
                margin-top: 4px; user-select: none; }
  .card code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 11px;
               background: #F4F5F7; padding: 0 3px; border-radius: 3px; }

  #banner { display: none; margin: 12px; padding: 14px 16px; background: #FFEBE6;
            border: 1px solid var(--p0); border-radius: 6px; font-size: 13px; }
  #banner.show { display: block; }
  .hint { color: var(--dim); font-size: 12px; margin-top: 4px; }

  /* A move writes a file. Saying so, briefly, is what tells Terry the drag took --
     a card that snapped back and a card that saved look identical. */
  #toast { position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
           background: var(--ink); color: #FFFFFF; padding: 9px 15px;
           border-radius: 6px; font-size: 13px; opacity: 0; transition: opacity .18s;
           pointer-events: none; z-index: 40; max-width: 70vw; }
  #toast.show { opacity: 1; }
  #toast.bad { background: var(--p0); }
</style>
</head>
<body>
  <div id="bar">
    <span id="dot"></span>
    <span id="title">%TITLE%</span>
    <span id="counts"></span>
    <span class="grow"></span>
    <span class="meta" id="stamp"></span>
    <span class="meta" id="reloads"></span>
  </div>
  <div id="banner">
    <strong id="banner-head"></strong>
    <div class="hint" id="banner-body"></div>
  </div>
  <div id="board"></div>
  <div id="toast"></div>

<script>
let seen = null, reloads = 0;
let data = {lanes: [], edges: [], counts: {}, error: null};

function toast(msg, bad) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('bad', !!bad);
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2800);
}

// Terry may drag only the edges the server will accept. Asking the server first
// would make the highlight lag the cursor, so the page carries the same list and
// the server stays the one that decides.
function allowed(from, to) {
  return data.edges.some(e => e[0] === from && e[1] === to);
}

function card(item) {
  const d = document.createElement('div');
  d.className = 'card' + (item.draggable ? ' draggable' : '');
  d.draggable = !!item.draggable;
  d.dataset.id = item.id;
  d.dataset.state = item.state;
  d.innerHTML = '<div class="top"><span class="pri"></span>'
    + '<span class="subject"></span></div>'
    + '<div class="detail">' + item.detail + '</div>'
    + (item.detail.length > 110 ? '<div class="more">more</div>' : '');
  const pri = d.querySelector('.pri');
  pri.textContent = item.priority;
  pri.className = 'pri ' + item.priority;
  pri.title = item.priorityLabel;
  // Set as text, so a stray angle bracket in a subject cannot become markup.
  d.querySelector('.subject').textContent = item.subject;
  const more = d.querySelector('.more');
  if (more) {
    more.addEventListener('click', ev => {
      ev.stopPropagation();
      more.textContent = d.classList.toggle('open') ? 'less' : 'more';
    });
  }
  d.addEventListener('dragstart', ev => {
    ev.dataTransfer.setData('text/plain',
      JSON.stringify({id: item.id, from: item.state}));
    ev.dataTransfer.effectAllowed = 'move';
    d.classList.add('dragging');
  });
  d.addEventListener('dragend', () => d.classList.remove('dragging'));
  return d;
}

function laneEl(lane) {
  const el = document.createElement('section');
  el.className = 'lane';
  el.dataset.lane = lane.state;
  el.dataset.owner = lane.owner;
  el.innerHTML = '<h2><span class="nm"></span><span class="n"></span></h2>'
    + '<div class="owner"></div><div class="cards"></div>';
  el.querySelector('.nm').textContent = lane.label;
  el.querySelector('.n').textContent = lane.items.length;
  const owner = el.querySelector('.owner');
  owner.textContent = lane.ownerLabel;
  if (lane.owner === 'claude') {
    const ro = document.createElement('span');
    ro.className = 'ro';
    ro.textContent = '  \\u00b7 read-only for you';
    owner.appendChild(ro);
  }
  const cards = el.querySelector('.cards');
  for (const item of lane.items) cards.appendChild(card(item));

  el.addEventListener('dragover', ev => {
    // Chrome hides the payload during dragover, so the source lane is read from
    // the card currently marked .dragging instead.
    const src = document.querySelector('.card.dragging');
    if (!src || src.dataset.state === lane.state) return;
    if (allowed(src.dataset.state, lane.state)) {
      ev.preventDefault();
      el.classList.add('over');
    } else {
      el.classList.add('deny');
    }
  });
  el.addEventListener('dragleave', () => {
    el.classList.remove('over'); el.classList.remove('deny');
  });
  el.addEventListener('drop', async ev => {
    ev.preventDefault();
    el.classList.remove('over'); el.classList.remove('deny');
    let payload;
    try { payload = JSON.parse(ev.dataTransfer.getData('text/plain')); }
    catch (e) { return; }
    if (!allowed(payload.from, lane.state)) {
      toast('That lane is Claude\\u2019s \\u2014 ' + payload.from + ' \\u2192 '
            + lane.state + ' is not yours to drag', true);
      return;
    }
    const res = await fetch('/move', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: payload.id, to: lane.state}),
    });
    const out = await res.json();
    if (!res.ok) { toast(out.error || 'Move refused', true); return; }
    toast(out.result);
    seen = null;   // force the next tick to refetch
  });
  return el;
}

function paint() {
  const board = document.getElementById('board');
  board.replaceChildren();
  for (const lane of data.lanes) board.appendChild(laneEl(lane));

  const banner = document.getElementById('banner');
  banner.classList.toggle('show', !!data.error);
  if (data.error) {
    document.getElementById('banner-head').textContent =
      'The board file did not load.';
    document.getElementById('banner-body').textContent = data.error;
  }

  const c = data.counts || {};
  // The two counts that ask Terry for something lead, because they are the only
  // numbers on this page that are about him.
  document.getElementById('counts').textContent =
    ((c.needs_terry_action ? c.needs_terry_action + ' NEEDS YOU \\u00b7 ' : '')
     + (c.ready_for_review ? c.ready_for_review + ' TO SIGN OFF \\u00b7 ' : '')
     + (c.open || 0) + ' open \\u00b7 ' + (c.in_progress || 0) + ' in progress \\u00b7 '
     + (c.completed || 0) + ' completed');
}

async function tick() {
  try {
    const meta = await (await fetch('/mtime', {cache: 'no-store'})).json();
    if (meta.mtime !== seen) {
      data = await (await fetch('/data', {cache: 'no-store'})).json();
      seen = meta.mtime;
      reloads++;
      document.getElementById('stamp').textContent = 'Written ' + meta.stamp;
      document.getElementById('reloads').textContent = 'Reload ' + reloads;
      paint();
    }
    document.getElementById('dot').classList.remove('stale');
  } catch (e) {
    // Say the server is gone rather than leaving a stale board looking current.
    document.getElementById('dot').classList.add('stale');
    document.getElementById('stamp').textContent = 'Server unreachable';
  }
}
tick();
setInterval(tick, %POLL%);
</script>
</body>
</html>
"""


def payload() -> bytes:
    """The board as JSON for the page, or an `error` the page renders as a banner.

    **A load failure is DATA, not a 500.** The page stays up and says what is wrong,
    because a blank tab and a broken parser look identical and one of them is a lie.
    """
    try:
        board = status.load(BOARD)
    except (status.BoardError, OSError, json.JSONDecodeError) as exc:
        return json.dumps({"lanes": [], "edges": [], "counts": {},
                           "error": str(exc)}).encode("utf-8")

    lanes = status.lanes(board)
    counts = {lane.state: len(lane.items) for lane in lanes}
    counts["open"] = sum(len(lane.items) for lane in lanes
                         if lane.state != "completed")

    return json.dumps({
        "project": board["project"],
        "lanes": [{
            "state": lane.state,
            "label": lane.label,
            "owner": lane.owner,
            "ownerLabel": lane.owner_label,
            "items": [{
                "id": item["id"],
                "state": item["state"],
                "subject": item["subject"],
                "priority": item.get("priority", status.DEFAULT_PRIORITY),
                "priorityLabel": status.PRIORITY_LABEL.get(
                    item.get("priority", status.DEFAULT_PRIORITY), ""),
                "detail": inline(item.get("detail", "")),
                # **Computed on the SERVER from the same table the server enforces**,
                # so the cursor and the answer cannot disagree.
                "draggable": any(a == item["state"] for a, _ in status.TERRY_EDGES),
            } for item in lane.items],
        } for lane in lanes],
        "edges": sorted(status.TERRY_EDGES),
        "counts": counts,
        "error": None,
    }).encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    """Four routes: the page, the board, one timestamp to poll, and the move."""

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A cached copy of the previous write looks exactly like an edit that did not
        # happen, which is the one thing a live view must never show.
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict[str, object], code: int = 200) -> None:
        self._send(json.dumps(obj).encode("utf-8"), "application/json", code)

    def do_GET(self) -> None:
        route = self.path.partition("?")[0]
        if route == "/mtime":
            try:
                mtime = BOARD.stat().st_mtime
            except OSError:
                self._json({"mtime": 0, "stamp": "board file missing"})
                return
            # **Terry's LOCAL wall clock**, which is the point of the bar.
            stamp = (datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)
                     .astimezone().strftime("%H:%M:%S"))
            self._json({"mtime": mtime, "stamp": stamp})
        elif route == "/data":
            self._send(payload(), "application/json")
        elif route in ("/", "/index.html"):
            # **A broken board still serves its page.** The title falls back and
            # `/data` carries the real error to the banner, because a blank tab and
            # a parse failure look identical from the outside.
            title = "Work board"
            with contextlib.suppress(status.BoardError, OSError, json.JSONDecodeError):
                title = status.load(BOARD)["project"] or title
            page = (PAGE.replace("%POLL%", str(POLL_MS))
                    .replace("%TITLE%", html.escape(title)))
            self._send(page.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        """`POST /move` -- the one write path, and it refuses more than it accepts.

        **The server re-checks the edge rather than trusting the page.** The page
        carries the same list only so the cursor can answer without a round trip.
        **A guard that lives only in the client is decoration.**
        """
        if self.path.partition("?")[0] != "/move":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            item_id, to = str(body["id"]), str(body["to"])
        except (ValueError, KeyError, TypeError):
            self._json({"error": "bad request body"}, 400)
            return

        try:
            board = status.load(BOARD)
            was = status.find(board, item_id)["state"]
        except (status.BoardError, OSError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 404)
            return

        if (was, to) not in status.TERRY_EDGES:
            self._json({"error": f"{was} -> {to} is not an edge Terry drags"}, 409)
            return

        try:
            # **`by="terry"` is a FACT here, not an assumption.** The server binds to
            # loopback, so this request came from his machine.
            result = status.move(board, item_id, to, "terry")
            status.save(board, BOARD)
        except (status.BoardError, OSError) as exc:
            self._json({"error": str(exc)}, 409)
            return
        print(f"  MOVED {result}", flush=True)
        self._json({"result": result})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
        # **The parameter names are the base class's and MUST NOT be renamed.**
        # `_fmt` satisfies ruff and then pyright refuses the override outright -- a
        # parameter name is part of an override's contract because a caller may pass
        # it by keyword. Both lint codes here are forced by a signature this code
        # does not own.
        #
        # The poll runs twice a second, so logging every request would bury anything
        # worth reading. Only a real data fetch prints.
        #
        # **`args[0]` is NOT always a string.** `send_error` routes through
        # `log_error` with `("code %d, message %s", 404, ...)`, an int first, and an
        # unguarded `in` test raises inside the handler and closes the socket with no
        # response.
        first = args[0] if args else ""
        if isinstance(first, str) and "/data" in first:
            print(f"  Served {first.split()[1] if ' ' in first else first}", flush=True)


def main() -> None:
    global BOARD  # noqa: PLW0603 -- one process serves one board, set once at startup
    ap = argparse.ArgumentParser(description="Serve a claude-status board.")
    ap.add_argument("board", type=pathlib.Path, help="path to the board JSON")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"TCP port on {HOST} (default {DEFAULT_PORT})")
    args = ap.parse_args()
    BOARD = args.board

    print(f"Serving {BOARD}")
    try:
        board = status.load(BOARD)
        print(f"  project   : {board['project'] or '(unnamed)'}")
        for lane in status.lanes(board):
            if lane.items:
                print(f"      {lane.label:<20} {len(lane.items):>2}  [{lane.owner_label}]")
    except (status.BoardError, OSError, json.JSONDecodeError) as exc:
        # **Loud, and it still serves.** The page renders the same message, so the
        # failure is visible in both places rather than as an empty board.
        print(f"  WARNING: {exc}")
        print("  The page will say so rather than look empty.")

    print(f"  view      : http://{HOST}:{args.port}/")
    print(f"  polling   : every {POLL_MS} ms, repaints only when the file changes")
    print("  Terry may drag:")
    for a, b in sorted(status.TERRY_EDGES):
        print(f"      {a} -> {b}")
    print(f"Listening on {HOST}:{args.port}. Press Ctrl+C to stop.", flush=True)
    http.server.ThreadingHTTPServer((HOST, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
