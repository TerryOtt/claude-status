# Inter — vendored, and here is why that is allowed

**RFC 2119 keywords, and the capitals are load-bearing.**

| | |
|---|---|
| Typeface | **Inter** |
| Copyright | **© 2016 The Inter Project Authors**, https://github.com/rsms/inter |
| License | **SIL Open Font License, Version 1.1** — full text in `LICENSE.txt` |
| Files | `Inter-latin.woff2` (48,256 bytes), `Inter-latin-ext.woff2` (85,068 bytes) |
| Obtained | 2026-08-18, from Google Fonts' `css2` API, which serves OFL Inter as
  per-subset woff2. Both verified to begin `wOF2` |
| Version | Google Fonts `v20` of Inter, variable weight 100–900 |

## Redistributing it here is COMPLIANT, and these are the three conditions

**The OFL permits redistribution outright**, including bundled inside another project,
and the conditions are all satisfiable by a directory like this one:

1. **The license text travels with the font.** That is `LICENSE.txt`, copied verbatim
   from `rsms/inter`.
2. **The copyright notice is preserved.** It is the first line of that file and the
   second row of the table above.
3. **The font is NOT sold on its own.** It is not sold at all here.

**A fourth condition exists and does not apply: the Reserved Font Name clause.** A
MODIFIED version may not be distributed under the name "Inter". **Nothing here is
modified** — these are unaltered subsets as Google Fonts publishes them. **If anyone
ever re-subsets, hints or otherwise alters these files, the result MUST be renamed.**

## Why the font is bundled rather than linked

**Terry asked for Inter**, 2026-08-18: *"if this project could use Inter as its typeface,
it'd be appreciated. That's my current go to. Was a Roboto kid for years."*

**It is NOT installed on his machine**, checked rather than assumed. So naming it in a
CSS font stack alone would have fallen back to Segoe UI and looked almost right, which
is the kind of failure nobody investigates.

**A Google Fonts `<link>` was the obvious alternative and it is refused.** It makes a
LOCAL tool reach the internet to render, so the board would look wrong on a plane —
which is the same *offline is not stale* argument this house applies everywhere else.

**133 KB buys a board that always looks right.** The server sends these with
`Cache-Control: public, max-age=31536000, immutable`, the only cached thing it serves.

## Why only two subsets

**`latin` and `latin-ext`.** The board renders English task subjects, ASCII
identifiers, and a handful of punctuation. Cyrillic, Greek and Vietnamese would have
tripled the payload for glyphs nothing here emits.

**A glyph outside both ranges still renders**, in the fallback face — browsers
substitute per glyph, not per element. Worth knowing before someone concludes a missing
subset broke the page.
