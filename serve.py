"""A live, draggable swimlane board over one board JSON, served on loopback.

    python serve.py path/to/board.json
    then open the URL it prints

**RFC 2119 keywords, and the capitals are load-bearing.**

## The port comes from the BOARD, not from this file

**Terry, 2026-08-18:** *"I want per-project config JSON that includes TCP port num; I
want to be able to bookmark one board per project. reduces surprise if nothing is
listening at that port."*

**A shared default port is worse than a dead bookmark.** With every project on 8792, a
bookmark opens whichever board happens to be running -- so the failure mode is reading
the WRONG project's work and believing it, which is silent. **A port per project means
the bookmark either shows your board or shows nothing, and nothing is honest.**

`--port` still overrides, for the case where two boards must run at once during a
migration.

## IT WRITES, and that is safe here for a reason Trello could not offer

**Terry drags cards, and either of us can comment.** Both are write paths.

**The same afternoon, the official Trello MCP server was connected and removed within
the hour**, because its OAuth grant authenticates Claude AS Terry -- a card Claude moved
and a card Terry moved were the same event by the same member, so his signoff stopped
being provable.

**This server binds to loopback.** Whoever reaches it is at his machine, so a drag IS
Terry: no identity to forge, no token to leak, and `by` in the history can be trusted.

**Permission is re-checked in `status.Board.move`, not here.** The page carries the edge
list only so the cursor can answer without a round trip. **A guard that lives only in
the client is decoration** -- and one that lives only in the server leaves the library
Claude uses wide open, which is exactly what happened on the first version.

## Three staleness edges, and each looks like the server being broken

**A change to the BOARD FILE is live**, picked up within `POLL_MS`.

**A change to THIS FILE or to `status.py` needs a RESTART.**

**A change to the PAGE needs a BROWSER RELOAD on top of that.** The open tab still runs
the script it was served, which produces a genuinely confusing halfway state: cards
render correctly because their content comes from `/data`, while new CSS and new counts
do not. **Half the change appearing is more disorienting than none of it.**

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

# Far below the time a human takes to switch windows, and a stat() against a local file
# rather than anything on a network.
POLL_MS = 400

# **Inline markdown only, and that is a deliberate scope.** A detail or comment carries
# `code`, **bold** and *italic* and nothing else. A markdown library for one field would
# be a dependency for a job this size.
INLINE = (
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\*([^*]+)\*"), r"<em>\1</em>"),
)

# Set once at startup by `main`. One process serves one board.
BOARD_PATH: pathlib.Path = pathlib.Path("board.json")

# **Inter, bundled rather than linked, under the SIL Open Font License 1.1.**
# `vendor/typefaces/inter/README.md` carries the copyright, the license text and the
# three conditions that make redistributing it here compliant. Terry raised the
# question himself and proposed this exact layout.
#
# **It is NOT installed on his machine**, checked rather than assumed, so naming it in a
# CSS font stack alone would have fallen back to Segoe UI and looked almost right --
# the kind of failure nobody investigates. And a Google Fonts `<link>` would make a
# LOCAL tool reach the internet to render, breaking the board on a plane.
FONT_DIR = pathlib.Path(__file__).resolve().parent / "vendor" / "typefaces" / "inter"

FONTS = {
    "/fonts/Inter-latin.woff2": "Inter-latin.woff2",
    "/fonts/Inter-latin-ext.woff2": "Inter-latin-ext.woff2",
}


def inline(text: str) -> str:
    """Escape HTML, THEN apply the three inline spans.

    **That order is the whole safety of it.** Reversing it would escape the markup this
    function just produced and leave the content raw.
    """
    out = html.escape(text)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    return out


def when(stamp: str) -> str:
    """An ISO stamp as `Aug 18, 13:52`, or unchanged if it will not parse.

    **Unchanged rather than blank on failure.** A card migrated from the markdown log
    carries whatever its old date column said, and showing that beats showing nothing.
    """
    try:
        return datetime.datetime.fromisoformat(stamp).strftime("%b %d, %H:%M")
    except ValueError:
        return stamp


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>%TITLE%</title>
<style>
  /* Inter, served from this repository. See the FONTS note in serve.py. */
  @font-face {
    font-family: 'Inter'; font-style: normal; font-weight: 100 900;
    font-display: swap; src: url('/fonts/Inter-latin.woff2') format('woff2');
    unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6,
      U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122,
      U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
  }
  @font-face {
    font-family: 'Inter'; font-style: normal; font-weight: 100 900;
    font-display: swap; src: url('/fonts/Inter-latin-ext.woff2') format('woff2');
    unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7,
      U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F,
      U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F,
      U+A720-A7FF;
  }

  /* LIGHT, because Terry asked in exactly those words: "turn off dark mode I hate
     it." No media query and no toggle -- one look, chosen. */
  :root {
    --bg: #F4F5F7; --lane: #EBECF0; --card: #FFFFFF;
    --ink: #172B4D; --dim: #5E6C84; --line: #DFE1E6;
    --terry: #0052CC; --claude: #E2A100; --handoff: #1F845A; --done: #5E6C84;
    --p0: #C9372C; --p1: #E56910; --p2: #B77600;
    --p3: #5E6C84; --p4: #8993A4; --p5: #B3BAC5;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 14px/1.5 'Inter', 'Segoe UI', system-ui, sans-serif;
         font-feature-settings: 'cv05' 1, 'tnum' 1; }

  #bar { position: sticky; top: 0; z-index: 20; display: flex; gap: 14px;
         align-items: center; padding: 9px 14px; background: #FFFFFF;
         border-bottom: 1px solid var(--line); font-size: 12px; }
  #bar .grow { flex: 1; }
  #title { font-weight: 700; letter-spacing: -.01em; }
  #counts { color: var(--dim); }
  #dot { width: 8px; height: 8px; border-radius: 50%; background: var(--handoff); }
  #dot.stale { background: var(--p0); }
  .meta { color: var(--dim); }

  /* **SEVEN LANES SHARE THE WIDTH RATHER THAN OVERFLOWING IT.** Terry works with the
     terminal on the left and the browser on the right, so the viewport is about half a
     screen -- and at a fixed 268px the seventh lane was clipped off the edge with a
     horizontal scrollbar under it. **A board you have to scroll sideways to see is not
     a board**, because the whole point is taking it in at a glance.

     `flex: 1 1 0` divides whatever is there. **The floor is deliberately LOW**, because
     Terry ruled on the trade: *"I'm okay with cards being taller than they are wide;
     vertical scroll is easy and I don't think it'll get deep other than backlog and
     completed."* So a narrow lane wraps its titles rather than pushing a lane off the
     edge -- **the horizontal overflow is the failure, and card height is not.**

     118px keeps all seven visible down to about a 900px viewport. Below that the row
     scrolls, which is the honest behavior for a window too small for the board. */
  #board { display: flex; gap: 8px; padding: 10px; align-items: stretch;
           overflow-x: auto; height: calc(100vh - 44px); }
  .lane { background: var(--lane); border-radius: 8px;
          flex: 1 1 0; min-width: 118px; max-width: 340px;
          display: flex; flex-direction: column;
          border-top: 3px solid var(--dim); }
  .lane[data-css="terry"]   { border-top-color: var(--terry); }
  .lane[data-css="claude"]  { border-top-color: var(--claude); }
  .lane[data-css="handoff"] { border-top-color: var(--handoff); }
  .lane[data-css="done"]    { border-top-color: var(--done); }

  .lane h2 { margin: 0; padding: 9px 12px 3px; font-size: 12px; font-weight: 700;
             text-transform: uppercase; letter-spacing: .04em;
             display: flex; gap: 8px; align-items: center; }
  .lane h2 .n { margin-left: auto; background: #FFFFFF; border-radius: 10px;
                padding: 0 7px; font-size: 11px; color: var(--dim); }
  /* Ownership is stated in words under every lane title, not implied by a color.
     Terry: "Real clear ownership per lane." A legend elsewhere would make him
     remember which color meant what. */
  .owner { padding: 0 12px 8px; font-size: 10px; font-weight: 600;
           letter-spacing: .02em; color: var(--dim); }
  .lane[data-css="terry"]   .owner { color: var(--terry); }
  .lane[data-css="handoff"] .owner { color: var(--handoff); }

  .cards { padding: 0 8px 10px; overflow-y: auto; flex: 1; }
  .lane.over { outline: 2px solid var(--terry); outline-offset: -2px; }
  .lane.deny { outline: 2px dashed var(--p0); outline-offset: -2px; }

  /* **PRIORITY AND TITLE, NOTHING ELSE.** Terry: "for the cards, only show P1-P5 &
     title; I need to click in for description or comment history or audit trail."
     A card is a thing you scan; the panel is a thing you read. */
  .card { background: var(--card); border-radius: 6px; padding: 7px 9px;
          margin-bottom: 7px; box-shadow: 0 1px 1px rgba(9,30,66,.25);
          display: flex; gap: 8px; align-items: flex-start; cursor: pointer; }
  .card.dragging { opacity: .4; }
  .card:hover { background: #FAFBFC; }
  .pri { font-size: 10px; font-weight: 700; color: #FFFFFF; border-radius: 3px;
         padding: 1px 5px; letter-spacing: .02em; flex: 0 0 auto; margin-top: 1px; }
  .pri.P0 { background: var(--p0); } .pri.P1 { background: var(--p1); }
  .pri.P2 { background: var(--p2); } .pri.P3 { background: var(--p3); }
  .pri.P4 { background: var(--p4); } .pri.P5 { background: var(--p5); }
  .card .subject { font-size: 13px; font-weight: 500; }
  .card .marks { margin-left: auto; color: var(--dim); font-size: 11px;
                 flex: 0 0 auto; }

  /* The detail panel. A drawer rather than a modal, so the board stays visible and
     a card's lane is still legible while you read it. */
  #scrim { position: fixed; inset: 0; background: rgba(9,30,66,.45); display: none;
           z-index: 30; }
  #scrim.show { display: block; }
  #panel { position: fixed; top: 0; right: 0; bottom: 0; width: 560px;
           max-width: 92vw; background: #FFFFFF; z-index: 31; display: none;
           flex-direction: column; box-shadow: -4px 0 16px rgba(9,30,66,.2); }
  #panel.show { display: flex; }
  #panel header { padding: 16px 20px 12px; border-bottom: 1px solid var(--line); }
  #panel h1 { margin: 6px 0 0; font-size: 18px; letter-spacing: -.01em; }
  #panel .sub { color: var(--dim); font-size: 12px; margin-top: 6px; }
  #panel .body { overflow-y: auto; padding: 16px 20px 24px; flex: 1; }
  #panel h3 { font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
              color: var(--dim); margin: 22px 0 8px; }
  #panel h3:first-child { margin-top: 0; }
  #close { float: right; border: 0; background: transparent; font-size: 20px;
           cursor: pointer; color: var(--dim); line-height: 1; }
  .detail-text { font-size: 13px; }
  .detail-text code { font-family: 'Cascadia Mono', Consolas, monospace;
                      font-size: 11.5px; background: #F4F5F7; padding: 0 3px;
                      border-radius: 3px; }
  .empty { color: var(--dim); font-style: italic; font-size: 12.5px; }

  /* The audit trail and the comments are visually DIFFERENT on purpose. One is what
     the machine recorded and nobody typed; the other is what a person chose to say.
     Making them look alike would suggest the trail is editable. */
  .trail { border-left: 2px solid var(--line); padding-left: 12px; }
  .trail li { list-style: none; font-size: 12px; color: var(--dim);
              margin-bottom: 5px; font-variant-numeric: tabular-nums; }
  .trail .who { font-weight: 600; }
  .trail .who.terry { color: var(--terry); }
  .trail .who.claude { color: var(--claude); }
  .trail ul { margin: 0; padding: 0; }

  .comment { background: #F4F5F7; border-radius: 6px; padding: 8px 10px;
             margin-bottom: 8px; font-size: 13px; }
  .comment .head { font-size: 11px; color: var(--dim); margin-bottom: 3px; }
  .comment .head .who { font-weight: 700; }
  .comment .head .who.terry { color: var(--terry); }
  .comment .head .who.claude { color: var(--claude); }
  #say { width: 100%; min-height: 68px; font: inherit; font-size: 13px;
         padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px;
         resize: vertical; }
  #post { margin-top: 8px; background: var(--terry); color: #FFFFFF; border: 0;
          border-radius: 5px; padding: 7px 14px; font: inherit; font-weight: 600;
          font-size: 13px; cursor: pointer; }

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

  <div id="scrim"></div>
  <aside id="panel">
    <header>
      <button id="close" title="Close">&times;</button>
      <span class="pri" id="p-pri"></span>
      <h1 id="p-subject"></h1>
      <div class="sub" id="p-sub"></div>
    </header>
    <div class="body">
      <h3>Description</h3>
      <div class="detail-text" id="p-detail"></div>
      <h3>Comments</h3>
      <div id="p-comments"></div>
      <textarea id="say" placeholder="Leave a note on this card…"></textarea>
      <button id="post">Comment as Terry</button>
      <h3>Audit trail</h3>
      <div class="trail"><ul id="p-trail"></ul></div>
    </div>
  </aside>
  <div id="toast"></div>

<script>
let seen = null, reloads = 0, openId = null;
let data = {lanes: [], edges: [], counts: {}, error: null};

function toast(msg, bad) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('bad', !!bad);
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2800);
}

function allowed(from, to) {
  return data.edges.some(e => e[0] === from && e[1] === to);
}

function itemById(id) {
  for (const lane of data.lanes) {
    for (const it of lane.items) if (it.id === id) return it;
  }
  return null;
}

// ---- the detail panel ----------------------------------------------------

function openCard(id) {
  const it = itemById(id);
  if (!it) return;
  openId = id;
  const pri = document.getElementById('p-pri');
  pri.textContent = it.priority;
  pri.className = 'pri ' + it.priority;
  pri.title = it.priorityLabel;
  document.getElementById('p-subject').textContent = it.subject;
  document.getElementById('p-sub').textContent =
    it.laneLabel + '  \\u00b7  ' + it.id;

  const detail = document.getElementById('p-detail');
  if (it.detail) { detail.innerHTML = it.detail; detail.className = 'detail-text'; }
  else { detail.textContent = 'No description.'; detail.className = 'empty'; }

  const cs = document.getElementById('p-comments');
  cs.replaceChildren();
  if (!it.comments.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = 'No comments yet.';
    cs.appendChild(e);
  }
  for (const c of it.comments) {
    const d = document.createElement('div');
    d.className = 'comment';
    d.innerHTML = '<div class="head"><span class="who ' + c.by + '"></span>'
      + '<span class="at"></span></div><div class="text">' + c.text + '</div>';
    d.querySelector('.who').textContent = c.by === 'terry' ? 'Terry' : 'Claude';
    d.querySelector('.at').textContent = '  \\u00b7  ' + c.when;
    cs.appendChild(d);
  }

  const tr = document.getElementById('p-trail');
  tr.replaceChildren();
  if (!it.history.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'No recorded history \\u2014 migrated before the trail existed.';
    tr.appendChild(li);
  }
  for (const h of it.history) {
    const li = document.createElement('li');
    const move = h.from ? (h.fromLabel + ' \\u2192 ' + h.toLabel)
                        : ('created in ' + h.toLabel);
    li.innerHTML = '<span class="who ' + h.by + '"></span> <span class="m"></span>';
    li.querySelector('.who').textContent = h.by === 'terry' ? 'Terry' : 'Claude';
    li.querySelector('.m').textContent = move + '  \\u00b7  ' + h.when;
    tr.appendChild(li);
  }

  document.getElementById('scrim').classList.add('show');
  document.getElementById('panel').classList.add('show');
  document.getElementById('say').value = '';
}

function closeCard() {
  openId = null;
  document.getElementById('scrim').classList.remove('show');
  document.getElementById('panel').classList.remove('show');
}

document.getElementById('close').addEventListener('click', closeCard);
document.getElementById('scrim').addEventListener('click', closeCard);
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') closeCard();
});

document.getElementById('post').addEventListener('click', async () => {
  const text = document.getElementById('say').value.trim();
  if (!openId || !text) return;
  const res = await fetch('/comment', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: openId, text: text}),
  });
  const out = await res.json();
  if (!res.ok) { toast(out.error || 'Comment refused', true); return; }
  document.getElementById('say').value = '';
  toast(out.result);
  seen = null;
});

// ---- the board -----------------------------------------------------------

function card(item) {
  const d = document.createElement('div');
  d.className = 'card';
  d.draggable = !!item.draggable;
  d.dataset.id = item.id;
  d.dataset.state = item.state;
  d.innerHTML = '<span class="pri"></span><span class="subject"></span>'
    + '<span class="marks"></span>';
  const pri = d.querySelector('.pri');
  pri.textContent = item.priority;
  pri.className = 'pri ' + item.priority;
  pri.title = item.priorityLabel;
  // Set as text, so a stray angle bracket in a subject cannot become markup.
  d.querySelector('.subject').textContent = item.subject;
  // A tiny count, because a card with discussion on it should say so without
  // being opened. Nothing else earns space here.
  const marks = [];
  if (item.comments.length) marks.push(item.comments.length + '\\u{1F4AC}');
  d.querySelector('.marks').textContent = marks.join(' ');

  d.addEventListener('click', () => openCard(item.id));
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
  el.dataset.css = lane.css;
  el.innerHTML = '<h2><span class="nm"></span><span class="n"></span></h2>'
    + '<div class="owner"></div><div class="cards"></div>';
  el.querySelector('.nm').textContent = lane.label;
  el.querySelector('.n').textContent = lane.items.length;
  el.querySelector('.owner').textContent = lane.ownerLabel;
  const cards = el.querySelector('.cards');
  for (const item of lane.items) cards.appendChild(card(item));

  el.addEventListener('dragover', ev => {
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
      toast('Not yours to drop there: ' + payload.from + ' \\u2192 ' + lane.state, true);
      return;
    }
    const res = await fetch('/move', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: payload.id, to: lane.state}),
    });
    const out = await res.json();
    if (!res.ok) { toast(out.error || 'Move refused', true); return; }
    toast(out.result);
    seen = null;
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
    document.getElementById('banner-head').textContent = 'The board did not load.';
    document.getElementById('banner-body').textContent = data.error;
  }

  const c = data.counts || {};
  document.getElementById('counts').textContent =
    ((c.needs_terry_action ? c.needs_terry_action + ' NEEDS YOU \\u00b7 ' : '')
     + (c.ready_for_review ? c.ready_for_review + ' TO SIGN OFF \\u00b7 ' : '')
     + (c.open || 0) + ' open \\u00b7 ' + (c.in_progress || 0) + ' in progress \\u00b7 '
     + (c.completed || 0) + ' completed');

  // Repaint the drawer too, so a comment posted a moment ago appears without the
  // card having to be reopened.
  if (openId) openCard(openId);
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
        board = status.load(BOARD_PATH)
    except (status.BoardError, OSError, json.JSONDecodeError) as exc:
        return json.dumps({"lanes": [], "edges": [], "counts": {},
                           "error": str(exc)}).encode("utf-8")

    # **Drift is reported to the page, not swallowed.** `verify()` replays each item's
    # history; a mismatch means something changed a state without going through
    # `move()`, and that is exactly the news a board must not keep to itself.
    drift = board.verify()
    lanes = board.lanes()
    counts: dict[str, int] = {lane.state: len(lane.items) for lane in lanes}
    counts["open"] = sum(len(lane.items) for lane in lanes
                         if lane.state != "completed")

    return json.dumps({
        "project": board.project,
        "lanes": [{
            "state": lane.state,
            "label": lane.label,
            "css": lane.css,
            "ownerLabel": lane.owner_label,
            "items": [{
                "id": item.id,
                "state": item.state,
                "laneLabel": lane.label,
                "subject": item.subject,
                "priority": item.priority,
                "priorityLabel": status.PRIORITY_LABEL.get(item.priority, ""),
                "detail": inline(item.detail),
                # **Computed on the SERVER from the same table the server enforces**,
                # so the cursor and the answer cannot disagree.
                "draggable": any(a == item.state for a, _ in status.TERRY_EDGES),
                "comments": [{"by": c.by, "when": when(c.at),
                              "text": inline(c.text)} for c in item.comments],
                "history": [{"by": h.by, "when": when(h.at),
                             "from": h.frm,
                             "fromLabel": status.LANE_LABEL.get(h.frm or "", ""),
                             "toLabel": status.LANE_LABEL.get(h.to, h.to)}
                            for h in item.history],
            } for item in lane.items],
        } for lane in lanes],
        "edges": sorted(status.TERRY_EDGES),
        "counts": counts,
        "error": "; ".join(drift) if drift else None,
    }).encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    """The page, the board, a timestamp to poll, the fonts, and two write routes."""

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

    def _read_json(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return None
        return body if isinstance(body, dict) else None

    def do_GET(self) -> None:
        route = self.path.partition("?")[0]
        if route == "/mtime":
            try:
                mtime = BOARD_PATH.stat().st_mtime
            except OSError:
                self._json({"mtime": 0, "stamp": "board file missing"})
                return
            stamp = (datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)
                     .astimezone().strftime("%H:%M:%S"))
            self._json({"mtime": mtime, "stamp": stamp})
        elif route == "/data":
            self._send(payload(), "application/json")
        elif route in FONTS:
            # The font is immutable and 133 KB; letting the browser cache it is the
            # one thing on this server that SHOULD be cached.
            try:
                blob = (FONT_DIR / FONTS[route]).read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "font/woff2")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(blob)
        elif route in ("/", "/index.html"):
            # **A broken board still serves its page.** The title falls back and
            # `/data` carries the real error to the banner.
            title = "Work board"
            with contextlib.suppress(status.BoardError, OSError,
                                     json.JSONDecodeError):
                title = status.load(BOARD_PATH).project or title
            page = (PAGE.replace("%POLL%", str(POLL_MS))
                    .replace("%TITLE%", html.escape(title)))
            self._send(page.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        """`/move` and `/comment`. **Both act as `terry`, and that is a FACT here.**

        The server binds to loopback, so the request came from his machine. That is
        the property the Trello route could not offer at any price.
        """
        route = self.path.partition("?")[0]
        if route not in ("/move", "/comment"):
            self.send_error(404)
            return
        body = self._read_json()
        if body is None:
            self._json({"error": "bad request body"}, 400)
            return

        try:
            board = status.load(BOARD_PATH)
            if route == "/move":
                result = board.move(str(body["id"]), str(body["to"]), "terry")
            else:
                result = board.comment(str(body["id"]), str(body["text"]), "terry")
            status.save(board, BOARD_PATH)
        except KeyError as exc:
            self._json({"error": f"missing field {exc}"}, 400)
            return
        except (status.BoardError, OSError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 409)
            return

        print(f"  {result}", flush=True)
        self._json({"result": result})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
        # **The parameter names are the base class's and MUST NOT be renamed.** A
        # parameter name is part of an override's contract, because a caller may pass
        # it by keyword. Both lint codes here are forced by a signature this code does
        # not own.
        #
        # The poll runs twice a second, so logging every request would bury anything
        # worth reading. Only a real data fetch prints.
        #
        # **`args[0]` is NOT always a string.** `send_error` routes through `log_error`
        # with `("code %d, message %s", 404, ...)`, an int first, and an unguarded `in`
        # test raises inside the handler and closes the socket with no response.
        first = args[0] if args else ""
        if isinstance(first, str) and "/data" in first:
            print(f"  Served {first.split()[1] if ' ' in first else first}", flush=True)


def main() -> None:
    global BOARD_PATH  # noqa: PLW0603 -- one process serves one board, set at startup
    ap = argparse.ArgumentParser(description="Serve a claude-status board.")
    ap.add_argument("board", type=pathlib.Path, help="path to the board JSON")
    ap.add_argument("--port", type=int, default=None,
                    help="override the port in the board file")
    args = ap.parse_args()
    BOARD_PATH = args.board

    port = args.port or status.DEFAULT_PORT
    print(f"Serving {BOARD_PATH}")
    try:
        board = status.load(BOARD_PATH)
        if args.port is None:
            port = board.port
        print(f"  project   : {board.project or '(unnamed)'}")
        for lane in board.lanes():
            if lane.items:
                print(f"      {lane.label:<20} {len(lane.items):>2}  [{lane.owner_label}]")
        drift = board.verify()
        if drift:
            print(f"  DRIFT: {len(drift)} item(s) disagree with their own history:")
            for problem in drift:
                print(f"      {problem}")
    except (status.BoardError, OSError, json.JSONDecodeError) as exc:
        # **Loud, and it still serves.** The page renders the same message, so the
        # failure is visible in both places rather than as an empty board.
        print(f"  WARNING: {exc}")
        print("  The page will say so rather than look empty.")

    bad_edges = status.check_edges()
    if bad_edges:
        print(f"  PERMISSION TABLE INCONSISTENT, {len(bad_edges)} problem(s):")
        for problem in bad_edges:
            print(f"      {problem}")

    if not FONT_DIR.is_dir():
        # Inter is bundled. Without it the page silently falls back to Segoe UI and
        # looks almost right, which is the kind of failure nobody investigates.
        print(f"  WARNING: {FONT_DIR} is missing, so Inter will not load.")

    print(f"  view      : http://{HOST}:{port}/")
    print(f"  polling   : every {POLL_MS} ms, repaints only when the file changes")
    print("  Terry may drag:")
    for a, b in sorted(status.TERRY_EDGES):
        print(f"      {a} -> {b}")
    print(f"Listening on {HOST}:{port}. Press Ctrl+C to stop.", flush=True)
    http.server.ThreadingHTTPServer((HOST, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
