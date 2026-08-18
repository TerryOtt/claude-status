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

# The 12-hour pivot, named so the meridiem arithmetic reads as a rule.
NOON = 12

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
    """An ISO stamp as `2026-08-18 2:56pm`, or unchanged if it will not parse.

    **Terry's house format**, which he named in one line: ISO 8601 date, a space, then
    `HH:MM` with a lower-case meridiem closed up. The date sorts and never reads
    ambiguously; the time reads the way a person says it.

    **Built by hand rather than by `strftime`**, because `%p` emits `AM`/`PM` in the
    C locale and the platform decides in others -- so lowering it would be a
    locale-dependent guess. `%I` also zero-pads, which he does not want.

    **Unchanged rather than blank on failure.** A card migrated from the markdown log
    carries whatever its old date column said, and showing that beats showing nothing.
    """
    try:
        moment = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    # `% NOON or NOON` is what makes midnight read `12:05am` and noon `12:00pm`,
    # which a plain modulo gets wrong in both directions.
    hour = moment.hour % NOON or NOON
    meridiem = "am" if moment.hour < NOON else "pm"
    # **The hour is zero-padded, and that is what makes a COLUMN of these align.**
    # Terry: "in the name of all that is holy keep the datetimes cleanly aligned so
    # all dates line up and times line up." Inter carries `tnum`, so every digit is
    # the same width -- but `2:56pm` against `12:05pm` is one digit shorter and
    # shifts everything after it regardless.
    #
    # **It also matches his original spec**, which said `HH:MM(am/pm)`. `HH` is two
    # digits and the first implementation quietly dropped the pad.
    return f"{moment:%Y-%m-%d} {hour:02d}:{moment:%M}{meridiem}"


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
    --live: #14663F;
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
  /* **ONLY CALLS TO ACTION LIVE HERE.** Terry: "No other stats up there, just
     calls to action." Open counts and in-progress counts were noise -- the lane
     headers already carry them, and a number that never asks for anything trains
     the eye to skip the whole bar.

     **Zero is plain text; non-zero is a hazard pill.** So the bar is quiet when
     there is nothing to do and impossible to miss when there is, which is this
     project's loudness rule applied to a status line. */
  /* **The gap on the left separates the CTAs from the project name**, so they read
     as one group rather than as a continuation of the title. Terry: "CTA's should
     stand in a group." */
  #counts { display: flex; gap: 8px; align-items: center; margin-left: 22px; }
  /* A persistent toggle rather than a button that vanishes. See the note on
     `syncAlerts` for why the browser permission and this preference are two
     different things. */
  #alerts-wrap { font-size: 11px; font-weight: 600; color: var(--dim);
                 display: flex; gap: 4px; align-items: center; cursor: pointer;
                 user-select: none; white-space: nowrap; }
  #alerts-wrap.blocked { cursor: not-allowed; opacity: .55; }
  #alerts { margin: 0; cursor: inherit; }
  .cta { font-size: 11px; font-weight: 700; letter-spacing: .03em;
         color: var(--dim); padding: 3px 8px; border-radius: 4px; }
  /* **Warning-sign yellow, not highlighter yellow.** Terry asked for higher
     contrast, and the lever is SATURATION rather than contrast ratio -- black on
     the old #F5CD47 already measured about 11:1, well past AAA, so the pill was
     never hard to READ. It just did not read as a hazard. #FFD400 is the pure
     unmuted yellow of a road sign and takes black past 13:1. */
  .cta.hot { background: #FFD400; color: #000000; }
  /* **THE HEARTBEAT.** Terry, 2026-08-18, after being burned by a dashboard at
     work: *"'Written 13:52:53 Reload 1' ain't gonna do it for my brain due to
     emotional trauma."*

     **He is right, and it is the same defect this page exists to prevent.** A
     reload counter only moves when the FILE changes, so `Reload 1` after three
     quiet hours means "nothing happened" and "the poll died at 13:52" equally.
     **A stale view and a healthy view produced the identical pixel.**

     So the dot pulses on every successful poll and the bar counts the seconds
     since the last one. **Proof of life has to be something that MOVES**, because
     anything static is indistinguishable from a frozen page. */
  /* **A CONSTANT-SIZE DOT THAT BREATHES.** Terry: "have it be a constant size but
     do its heartbeat as a fade in/-out. Fade in linear over 1s, then fade out
     linear over 1s. very chillax." Then, having watched it: "go bigger on breather
     radius and slow down to 2s/2s." So 13px and a 4s cycle, linear both ways, no
     scaling -- nothing in the corner of his eye jumping about.

     **The animation runs ONLY while the poll is confirmed live**, and that is not
     decoration. A CSS animation left running unconditionally would keep breathing
     over a dead server, which is precisely the reassuring-but-false signal this
     whole bar exists to kill. `renderLive` owns the class.

     **A 4s cycle is FIFTY TIMES slower than the poll, and that gap is fine.** The
     dot says "alive"; it was never a per-request indicator. `POLL_MS` is the
     guarantee and this is the mood. */
  @keyframes breathe {
    0%   { opacity: .10; }
    50%  { opacity: 1; }
    100% { opacity: .10; }
  }
  #dot { width: 13px; height: 13px; border-radius: 50%; background: var(--live);
         flex: 0 0 auto; opacity: .10; transition: background .2s; }
  #dot.alive { animation: breathe 4s linear infinite; }
  /* Solid, not breathing. A stopped heart does not pulse. */
  #dot.stale { background: var(--p0); opacity: 1; animation: none; }
  .meta { color: var(--dim); font-variant-numeric: tabular-nums; }
  #live { color: var(--dim); font-variant-numeric: tabular-nums; }

  /* **A BADGE, not colored text.** Terry: "go high contrast green with bold white
     LIVE badge and we're good." White on #14663F is 7.4:1, past WCAG AA for
     small text -- which matters because this is the one element on the page whose
     job is to be believed at a glance. */
  #badge { font-size: 11px; font-weight: 700; letter-spacing: .06em;
           color: #FFFFFF; background: var(--live); border-radius: 4px;
           padding: 3px 8px; flex: 0 0 auto; transition: background .2s; }
  #badge.warn { background: var(--p1); }
  #badge.dead { background: var(--p0); }

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

  /* **+25% on the lane titles, 12px to 15px.** Terry asked for +50% first, looked
     at 18px and pulled it back: "that may have overshot for my yes." The count sits
     in a circle sized in `em` of this same rule, so the two track each other and a
     third adjustment needs one number changed rather than two. */
  .lane h2 { margin: 0; padding: 9px 12px 3px; font-size: 15px; font-weight: 700;
             text-transform: uppercase; letter-spacing: .02em;
             display: flex; gap: 8px; align-items: center; line-height: 1.15; }
  .lane h2 .n { margin-left: auto; background: #FFFFFF; color: var(--dim);
                font-size: .61em; font-weight: 600;
                height: 1.64em; min-width: 1.64em; border-radius: 50%;
                display: inline-flex; align-items: center; justify-content: center;
                padding: 0 .4em; box-sizing: border-box; }
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
  /* **A card that MOVED glides from where it was.** Terry asked to watch Claude's
     moves happen: "a motion animation would be fun af for me to watch in realtime."
     The transform is set by the FLIP pass in paint(); this rule only says how it
     travels. `will-change` keeps it on the compositor so a board full of cards does
     not judder. */
  .card.flip { transition: transform .55s cubic-bezier(.22, 1, .36, 1);
               will-change: transform; z-index: 5; position: relative; }
  /* A brief wash of the destination lane's meaning, so the eye lands on the card
     that actually changed rather than hunting for it. */
  @keyframes landed {
    0%   { box-shadow: 0 0 0 3px rgba(0, 82, 204, .55); }
    100% { box-shadow: 0 1px 1px rgba(9, 30, 66, .25); }
  }
  .card.landed { animation: landed 1.1s ease-out; }
  .card:hover { background: #FAFBFC; }
  .pri { font-size: 10px; font-weight: 700; color: #FFFFFF; border-radius: 3px;
         padding: 1px 5px; letter-spacing: .02em; flex: 0 0 auto; margin-top: 1px; }
  .pri.P0 { background: var(--p0); } .pri.P1 { background: var(--p1); }
  .pri.P2 { background: var(--p2); } .pri.P3 { background: var(--p3); }
  .pri.P4 { background: var(--p4); } .pri.P5 { background: var(--p5); }
  /* **Three rows: badge and ticket, subject, then the comment count.** Terry:
     "display card number on same line as P1/P2 with description below it", then
     "drop the # in front of ticket number, move ticket number to top right and
     make bold", then "move comment count to its own row at the very bottom on
     right and double height of glyph and count; it's too small for me to see."

     **The ticket and the count SWAP places**, which is why those two arrived as
     separate asks and were built in one pass -- doing either alone leaves the top
     right holding two things or nothing. */
  .card { flex-direction: column; gap: 5px; }
  .card .head { display: flex; gap: 7px; align-items: center; width: 100%; }
  .card .tix { margin-left: auto; color: var(--dim); font-size: 12px;
               font-weight: 700; font-variant-numeric: tabular-nums; }
  /* **+50% on the gap above the subject**, Terry's ask, 5px to 7.5px. It is a
     `margin-top` rather than a larger flex `gap` on purpose: raising the gap would
     also push the comment-count row down, and that is a different space he did not
     ask about. */
  .card .subject { font-size: 13px; font-weight: 500; margin-top: 2.5px; }
  /* **1.5x the 11px base, not 2x.** Terry corrected himself after seeing it: "2x is
     too damn much." **This is why the house rule says render and LOOK** -- 22px
     passed every check available and was still wrong, because "is this the right
     size" is not a question code can answer.

     It exists only when a card HAS comments; an empty third row on every card
     would cost real height in a lane that scrolls. */
  .card .marks { align-self: flex-end; color: var(--dim); font-size: 17px;
                 line-height: 1; display: flex; gap: 5px; align-items: baseline; }
  /* **Two digits of reserved width, and NO zero padding** -- his instruction, and
     the reason is alignment rather than tidiness. The row is right-aligned, so an
     unreserved number drags the bubble left and right as counts change and a
     column of cards ends up with bubbles at three different x positions.
     `tnum` makes "two digits" an exact width rather than an estimate. */
  .card .marks .n { min-width: 2ch; text-align: right;
                    font-variant-numeric: tabular-nums; }

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
  /* **THREE COLUMNS: datetime, actor, action.** Terry's order and his layout --
     "change audit trail to be datetime then user then action in three columns."
     A grid rather than three spans in a sentence, so every column starts at the
     same x whatever the content.

     **`tabular-nums` plus a zero-padded hour is what actually aligns the times.**
     The grid aligns the COLUMN; without the pad, `02:56pm` and `12:05pm` differ by
     a digit inside it. Between the two, no monospace face is needed -- which is
     why Roboto Mono and JetBrains Mono stayed on the shelf. */
  .trail { border-left: 2px solid var(--line); padding-left: 12px;
           display: grid; grid-template-columns: max-content max-content 1fr;
           gap: 5px 14px; font-size: 12px; }
  .trail .at { color: var(--dim); font-variant-numeric: tabular-nums;
               white-space: nowrap; }
  .trail .who { font-weight: 600; }
  .trail .who.terry { color: var(--terry); }
  .trail .who.claude { color: var(--claude); }
  .trail .what { color: var(--dim); }
  .trail .empty { grid-column: 1 / -1; }

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
    <span id="live">connecting…</span>
    <label id="alerts-wrap"><input type="checkbox" id="alerts"> Alerts</label>
    <span id="badge"
      title="Green means Python read and parsed the board file in the last 5s.">LIVE</span>
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
      <div class="trail" id="p-trail"></div>
    </div>
  </aside>
  <div id="toast"></div>

<script>
// **The repaint counter is GONE, deliberately.** It only moved when the file
// changed, so a healthy quiet board and a dead poll showed the same number --
// which is the exact confusion the LIVE badge replaced. Two signals telling the
// same story badly is worse than one telling it well.
let seen = null, openId = null;
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
    it.ticket + '  \\u00b7  ' + it.laneLabel + '  \\u00b7  ' + it.id;

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

  // Three cells per entry, appended straight into the grid -- no row wrapper, so
  // the columns line up across every entry rather than within each one.
  const tr = document.getElementById('p-trail');
  tr.replaceChildren();
  if (!it.history.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = 'No recorded history \\u2014 migrated before the trail existed.';
    tr.appendChild(e);
  }
  for (const h of it.history) {
    const at = document.createElement('div');
    at.className = 'at';
    at.textContent = h.when;

    const who = document.createElement('div');
    who.className = 'who ' + h.by;
    who.textContent = h.by === 'terry' ? 'Terry' : 'Claude';

    const what = document.createElement('div');
    what.className = 'what';
    what.textContent = h.from ? (h.fromLabel + ' \\u2192 ' + h.toLabel)
                              : ('created in ' + h.toLabel);

    tr.append(at, who, what);
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
  d.innerHTML = '<div class="head"><span class="pri"></span>'
    + '<span class="tix"></span></div>'
    + '<span class="subject"></span>'
    + (item.comments.length ? '<span class="marks"></span>' : '');
  // **No leading hash.** Display only -- find() still takes 0003, #0003, 3 or the
  // slug, because he may paste a hash from an older message or a commit.
  d.querySelector('.tix').textContent = item.ticket.replace('#', '');
  const pri = d.querySelector('.pri');
  pri.textContent = item.priority;
  pri.className = 'pri ' + item.priority;
  pri.title = item.priorityLabel;
  // Set as text, so a stray angle bracket in a subject cannot become markup.
  d.querySelector('.subject').textContent = item.subject;
  // A card with discussion on it should say so without being opened. At 11px on
  // the top row Terry could not see it -- "it's too small for me to see" -- so it
  // is 22px on its own bottom row now, and absent entirely when there is nothing
  // to say.
  // **Bubble first, then the count**, per Terry. The number sits in its own span so
  // CSS can reserve two digits and right-align it without padding the value.
  if (item.comments.length) {
    const marks = d.querySelector('.marks');
    marks.innerHTML = '<span class="b">\\u{1F4AC}</span><span class="n"></span>';
    marks.querySelector('.n').textContent = item.comments.length;
  }

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

// **FLIP: First, Last, Invert, Play.** Terry wanted to watch Claude's moves happen
// rather than see a card teleport: "a motion animation would be fun af for me to
// watch in realtime."
//
// **The board repaints wholesale, so the old elements are gone by the time the new
// ones exist.** FLIP works anyway because it compares POSITIONS rather than nodes:
// measure every card's rectangle before the rebuild, measure again after, then
// translate each survivor back to where it was and let CSS carry it home.
//
// **Cards are matched by `data-id`**, which is why stable ids mattered beyond
// bookkeeping. Under the old positional row numbers a signoff renumbered everything
// and every card would have appeared to move.
function measureCards() {
  const seen = new Map();
  for (const el of document.querySelectorAll('.card[data-id]')) {
    seen.set(el.dataset.id, el.getBoundingClientRect());
  }
  return seen;
}

function playFlip(before) {
  if (!before.size) return;   // First paint. Nothing moved; it all just arrived.
  for (const el of document.querySelectorAll('.card[data-id]')) {
    const was = before.get(el.dataset.id);
    if (!was) continue;       // A brand new card fades in rather than flying in.
    const now = el.getBoundingClientRect();
    const dx = was.left - now.left;
    const dy = was.top - now.top;
    // Sub-pixel drift from a reflow is not a move. Two pixels is the floor.
    if (Math.abs(dx) < 2 && Math.abs(dy) < 2) continue;

    el.style.transform = 'translate(' + dx + 'px, ' + dy + 'px)';
    // **Two frames, not one.** Setting the transform and removing it in the same
    // frame collapses to no animation at all, because the browser never renders
    // the inverted position.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      el.classList.add('flip');
      el.style.transform = '';
      el.addEventListener('transitionend', () => {
        el.classList.remove('flip');
        el.classList.add('landed');
      }, {once: true});
    }));
  }
}

// **A card can ask for Terry and he can be in another window.** His words: "with no
// email those could get missed." Three escalating tells, none of which needs a mail
// server:
//
//   1. **The tab title.** Free, always on, survives a backgrounded tab. `(2) FGA
//      board` is visible in the tab strip from any other window.
//   2. **A desktop notification**, if he grants it once. Fires only when a count
//      GOES UP, never on a repaint, so a board sitting at two does not nag.
//   3. **A push to his phone**, which is Claude's job rather than the page's --
//      Claude sends one when it moves a card to Needs Terry.
//
// **It fires on a RISE, not on a non-zero value.** A notification that repeats every
// 400ms while a count sits at one is the boy-who-cried-wolf failure this project
// keeps guarding against, and it would be worse than silence.
let lastCta = null;
const BASE_TITLE = document.title;

function announce(c) {
  const asking = (c.needs_terry_action || 0) + (c.blocked || 0);
  document.title = asking > 0 ? '(' + asking + ') ' + BASE_TITLE : BASE_TITLE;

  if (lastCta !== null && asking > lastCta && alertsOn()) {
    const n = new Notification('FGA board needs you', {
      body: (c.needs_terry_action || 0) + ' waiting for Terry, '
          + (c.blocked || 0) + ' blocked',
      tag: 'fga-cta',          // Replaces its predecessor rather than stacking.
    });
    n.onclick = () => { window.focus(); n.close(); };
  }
  lastCta = asking;
}

// **THE BROWSER PERMISSION AND THIS PREFERENCE ARE TWO DIFFERENT THINGS**, and
// conflating them is what made the old button vanish forever after one click.
//
// A permission is granted once and CANNOT be revoked from script. A preference is
// ours, lives in `localStorage` beside `fga-hide-landed`, and can be turned off.
// Terry asked for a checkbox precisely because the button gave him no way to see
// whether alerts were on, or to switch them off again.
//
// **Three states, rendered honestly:**
//   `default` -- unchecked; ticking it prompts.
//   `granted` -- checked or not, per the stored preference.
//   `denied`  -- unchecked and DISABLED. **A checkbox that silently does nothing
//                is worse than one that admits it cannot.**
const ALERTS_KEY = 'fga-alerts';
const alertsBox = document.getElementById('alerts');
const alertsWrap = document.getElementById('alerts-wrap');

function alertsOn() {
  return ('Notification' in window)
    && Notification.permission === 'granted'
    && localStorage.getItem(ALERTS_KEY) === '1';
}

function syncAlerts() {
  if (!('Notification' in window)) {
    alertsWrap.hidden = true;
    return;
  }
  const denied = Notification.permission === 'denied';
  alertsBox.disabled = denied;
  alertsWrap.classList.toggle('blocked', denied);
  alertsWrap.title = denied
    ? 'Your browser has blocked notifications for this site.'
    : 'Desktop notification when a card starts waiting on you.';
  alertsBox.checked = alertsOn();
}

alertsBox.addEventListener('change', async () => {
  if (!alertsBox.checked) {
    localStorage.setItem(ALERTS_KEY, '0');
    syncAlerts();
    return;
  }
  // **The prompt only appears behind this gesture.** Chrome refuses
  // `requestPermission` without one, and an auto-request on load is the pattern
  // people reflexively deny -- which would leave the page permanently unable to
  // ask again.
  if (Notification.permission === 'default') {
    await Notification.requestPermission();
  }
  // **Stored only if the browser actually said yes.** Otherwise the box unticks
  // itself, which is the honest answer to "I asked and was refused".
  localStorage.setItem(ALERTS_KEY,
    Notification.permission === 'granted' ? '1' : '0');
  syncAlerts();
});

syncAlerts();

function paint() {
  const board = document.getElementById('board');
  const before = measureCards();
  board.replaceChildren();
  for (const lane of data.lanes) board.appendChild(laneEl(lane));
  playFlip(before);

  const banner = document.getElementById('banner');
  banner.classList.toggle('show', !!data.error);
  if (data.error) {
    document.getElementById('banner-head').textContent = 'The board did not load.';
    document.getElementById('banner-body').textContent = data.error;
  }

  // **Three counters, and every one is something TERRY must do.** Nothing else
  // belongs here -- the lane headers already carry every other number, and a stat
  // that never asks for anything teaches the eye to skip past the ones that do.
  //
  // **Zero is plain text; non-zero is a hazard pill.** Quiet when there is nothing
  // to do, impossible to miss when there is.
  const c = data.counts || {};
  const counts = document.getElementById('counts');
  counts.replaceChildren();
  // **Ranked worst first, left to right.** Terry's order. Blocked means neither of
  // us can move it; waiting-for-Terry means Claude is stopped until he answers;
  // needs-signoff means the work is done and a tick is outstanding. **Reading order
  // is severity order**, so the leftmost pill that is lit is the worst thing on the
  // board.
  for (const pair of [
    ['BLOCKED', c.blocked || 0],
    ['WAITING FOR TERRY', c.needs_terry_action || 0],
    ['NEEDS SIGNOFF', c.ready_for_review || 0],
  ]) {
    const s = document.createElement('span');
    s.className = pair[1] > 0 ? 'cta hot' : 'cta';
    s.textContent = pair[0] + ': ' + pair[1];
    counts.appendChild(s);
  }

  announce(c);

  // Repaint the drawer too, so a comment posted a moment ago appears without the
  // card having to be reopened.
  if (openId) openCard(openId);
}

// ---- proof of life -------------------------------------------------------
//
// **Terry had a dashboard whose backend had crashed, and the page looked fine.**
// His words: "past date/time does not mean it's the latest." A timestamp answers
// "when did I last get data", which reads IDENTICALLY to "when did everything
// stop" -- and the second one is the case you needed to notice.
//
// **So liveness is measured by the CLIENT against its own clock**, and it counts
// UP. `lastOk` is the only thing this trusts: the moment a fetch actually
// returned. Nothing the server sends can fake it, because a server that sends
// nothing cannot move it.
let lastOk = 0;
// The board file's own mtime, in client-clock milliseconds. Both processes are on
// this machine, so the two clocks are the same clock.
let fileMs = 0;

// **`lastOk` moves ONLY when Python confirms it read and parsed the board.** The
// `/mtime` route performs a real `status.load()`, and its `ok` field is what this
// keys on -- not the HTTP status, because a socket answering proves the process is
// up and nothing about the file.
const LIVE_MS  = 5000;   // Terry's number: "read that file within last 5 seconds".
const WARN_MS  = 15000;  // ~37 polls missed at 400ms. Nothing benign lasts this long.

// **The dot's 2s breathing cycle is CSS, not JavaScript, and it is cosmetic.**
// **Do not "fix" the poll rate to match it.** The badge claims Python read the
// file within 5 seconds; polling at the animation's pace would leave two or three
// chances to notice a failure before the claim is already false. The animation is
// how it feels; POLL_MS is what it guarantees.

// **One absolute clock, everything else relative.** Terry's call: the two
// timestamps became "N seconds/minutes ago" and only `Currently` stays a wall
// clock. **The absolute one is the tick; the relative ones are the answer.** A
// past HH:MM:SS makes you subtract before you know whether to worry, and doing
// arithmetic under stress is how a stale dashboard gets believed.
function clockOf(ms) {
  // **`2:09:29pm`, lower case and closed up.** Terry's preference, stated as one:
  // "for AM/PM go all lower case and no space between second and am/pm."
  //
  // **Built by hand rather than by `toLocaleTimeString`, and that is the safer
  // choice here.** Chrome emits `2:09:29 PM` with U+202F NARROW NO-BREAK SPACE
  // before the meridiem, not an ordinary space -- so the obvious `.replace(' PM')`
  // silently does nothing on a machine with current ICU, and a regex for it needs
  // escapes that this Python template would have to double.
  const d = new Date(ms);
  const h24 = d.getHours();
  const h12 = ((h24 + 11) % 12) + 1;
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return h12 + ':' + mm + ':' + ss + (h24 < 12 ? 'am' : 'pm');
}

function agoOf(ms) {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return s + (s === 1 ? ' second ago' : ' seconds ago');
  const m = Math.round(s / 60);
  if (m < 60) return m + (m === 1 ? ' minute ago' : ' minutes ago');
  const h = Math.round(m / 60);
  return h + (h === 1 ? ' hour ago' : ' hours ago');
}

// **Terry specified this bar himself:** "last update: HH:MM:SS - Currently:
// HH:MM:SS [green circle]".
//
// **It is better than a computed age, and the reason is that it needs no
// interpretation.** `Currently` is a wall clock the browser ticks every half
// second. If it FREEZES, the page is dead. If the GAP between the two grows,
// the server is dead. Both failures are visible without anyone trusting a
// number this code calculated -- which is the whole complaint, since the
// dashboard that burned him showed a perfectly plausible timestamp.
function renderLive() {
  const el = document.getElementById('live');
  const badge = document.getElementById('badge');
  const dot = document.getElementById('dot');
  const nowMs = Date.now();
  const nowTxt = clockOf(nowMs);

  if (!lastOk) {
    el.textContent = 'Last update: never  ·  ' + nowTxt;
    badge.className = 'dead';
    badge.textContent = 'NO DATA';
    dot.classList.add('stale');
    dot.classList.remove('alive');
    return;
  }

  // **All three in ONE span, so every separator is the same separator.** Terry
  // asked for the dot between the first two to match the one already between the
  // last two; two spans plus a flex gap could never quite line up with a `·`
  // typed into the middle of a string.
  //
  // The file's age is a DIFFERENT fact from the poll's, and both belong here: a
  // board nobody has touched for two hours is normal, and a poll that has not
  // answered for two hours is not.
  // **The wall clock carries no label.** Terry: "drop 'Currently:', it's obvious
  // its current local time." The two ages are labelled because a bare duration
  // could be either; a running clock explains itself.
  const age = nowMs - lastOk;
  const parts = [];
  if (fileMs) parts.push('File written: ' + agoOf(fileMs));
  parts.push('Last update: ' + agoOf(lastOk));
  parts.push(nowTxt);
  el.textContent = parts.join('  ·  ');

  // **The badge flips at LIVE_MS, not at WARN_MS.** It carries a single claim --
  // "Python read that file within the last 5 seconds" -- and a badge that stays
  // green while quietly becoming false is the whole problem being fixed.
  if (age < LIVE_MS) {
    badge.className = '';
    badge.textContent = 'LIVE';
  } else if (age < WARN_MS) {
    badge.className = 'warn';
    badge.textContent = 'NO ANSWER ' + Math.round(age / 1000) + 's';
  } else {
    const s = Math.round(age / 1000);
    badge.className = 'dead';
    badge.textContent = 'STALE ' + (s < 90 ? s + 's' : Math.round(s / 60) + 'm');
  }
  // **The dot and the badge key off the SAME `lastOk` and the same threshold**, so
  // they cannot disagree. Breathing green and a red STALE badge side by side would
  // be worse than either alone.
  const live = age < LIVE_MS;
  dot.classList.toggle('stale', !live);
  dot.classList.toggle('alive', live);
}

// **POLLING IS THE DESIGN, not a fallback for missing file watching.** Terry:
// "I hope inotify or equivalent works and we get realtime update, but the timer
// also actively polling will keep blood pressure low."
//
// **A push socket would be worse here, and for his own reason.** A WebSocket or
// SSE stream that goes quiet is indistinguishable from one that is working and
// has nothing to say -- which is the crashed-dashboard failure again, wearing a
// different protocol. **A poll that must answer every 400ms cannot go quiet
// without the circle noticing.**
//
// 400ms is realtime as far as an eye is concerned, and the cost is one `stat`
// plus a 12 KB JSON parse against a local file.
async function tick() {
  try {
    const meta = await (await fetch('/mtime', {cache: 'no-store'})).json();
    // **`ok: false` does NOT refresh `lastOk`.** The server answered, but it
    // could not read the board -- and a reachable server serving an unreadable
    // file is exactly the state that must not look healthy.
    if (meta.ok === false) { renderLive(); return; }
    fileMs = meta.mtime * 1000;
    if (meta.mtime !== seen) {
      data = await (await fetch('/data', {cache: 'no-store'})).json();
      seen = meta.mtime;
      paint();
    }
    // Only a real answer moves this. It is the whole signal.
    lastOk = Date.now();
  } catch (e) {
    // Deliberately does NOT touch lastOk. The age keeps climbing, and that is
    // what turns the bar red on its own.
  }
  renderLive();
}
tick();
setInterval(tick, %POLL%);
// **A second timer, and it is not redundant.** If `tick` itself wedges -- a
// hung fetch, a thrown error in paint -- the age would freeze at whatever it
// last rendered and the page would look healthy forever. This one only reads
// the clock, so it keeps counting when everything else has stopped.
setInterval(renderLive, 500);
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
                "ticket": item.label,
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
                # **Newest first.** Terry: "needs to be newest at top (most
                # relevant) to oldest at bottom." The Completed lane already
                # reverses for the same reason -- the interesting end of a
                # permanent record is the recent end, and a long trail otherwise
                # buries the entry you opened the card to read.
                #
                # **Reversed HERE rather than in `status.py`.** The stored order is
                # chronological and `verify()` replays it forwards; flipping the
                # model to suit a drawer would break the audit.
                "history": [{"by": h.by, "when": when(h.at),
                             "from": h.frm,
                             "fromLabel": status.LANE_LABEL.get(h.frm or "", ""),
                             "toLabel": status.LANE_LABEL.get(h.to, h.to)}
                            for h in reversed(item.history)],
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
            # **THE GREEN CIRCLE MEANS "PYTHON READ THAT FILE JUST NOW", so this
            # route actually reads it.** Terry set the bar: *"green circle means
            # python has actively polled and read that file within last 5 seconds."*
            #
            # **A `stat()` would have been cheaper and would have lied.** It proves
            # the directory entry exists, not that the board is readable -- a
            # truncated or half-written file passes `stat` and fails `load`. And a
            # server answering HTTP proves only that the socket is up.
            #
            # **`ok` is the field the dot keys on.** Parsing 12 KB of JSON at 2.5 Hz
            # is nothing, and it is the difference between a signal that means
            # something and one that looks reassuring.
            try:
                mtime = BOARD_PATH.stat().st_mtime
                status.load(BOARD_PATH)
            except (OSError, status.BoardError, json.JSONDecodeError) as exc:
                self._json({"ok": False, "mtime": 0, "stamp": "unreadable",
                            "error": str(exc)})
                return
            stamp = (datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)
                     .astimezone().strftime("%H:%M:%S"))
            self._json({"ok": True, "mtime": mtime, "stamp": stamp})
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
            # **`status.edit` holds an exclusive lock across load, mutate and save.**
            # Both writers must use it or it protects nothing: Terry drags here while
            # Claude runs `status.py --move`, and each rewrites the WHOLE file. The
            # loser of that race does not get a corrupt board -- it gets a perfectly
            # valid one that silently forgot a move.
            #
            # **This server is threaded**, so it also races itself: two quick drags
            # land on two handler threads.
            with status.edit(BOARD_PATH) as board:
                if route == "/move":
                    result = board.move(str(body["id"]), str(body["to"]), "terry")
                else:
                    result = board.comment(str(body["id"]), str(body["text"]), "terry")
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
