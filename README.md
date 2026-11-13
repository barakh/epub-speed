# epub-speed

A local, phone-friendly reader for getting through one EPUB faster.

Three views of the same book, plus a position that is remembered:

| view | what it does | good for |
| --- | --- | --- |
| **RSVP** | a column of words sliding upward, pivot letter pinned to the centre line, the next few words queued below | fiction, narrative nonfiction |
| **Pacer** | the whole page, read text dimmed, current word highlighted | dense text, arguments, numbers |
| **Plain** | ordinary page | checking that extraction did not mangle the book |

No dependencies, no build step, no Calibre. Python standard library only.

## Run

```sh
cd ~/Dev/epub-speed
python3 server.py                    # serves data/books/*.epub
python3 server.py ~/Books/foo.epub   # also serve specific files
```

It prints its URLs on startup:

```
epub-speed
  http://localhost:8756
  http://100.64.x.x:8756      <- reachable from your phone over Tailscale
  books: /home/burek/Dev/epub-speed/data/books
```

Add books either with the **+** button on the library screen (works from the
phone) or by dropping `.epub` files into `data/books/`.

State lives in `data/`:

- `data/books/` — the shelf (uploads land here)
- `data/progress/<book-id>.json` — chapter, word index, speed, last mode
- `data/sources.json` — extra paths given on the command line

## Controls

| key | action |
| --- | --- |
| space / tap the page | play / pause |
| ← → | previous / next sentence |
| ↑ ↓ | ±25 wpm |
| 1 2 3 | RSVP / Pacer / Plain |
| c | chapter list |
| esc | back |

On the phone: tap the page to start, tap again to pause. Press and **hold**
anywhere on the words to freeze the flow — it resumes where it stopped as
soon as you lift your finger. `⇤ ⇥` jump by sentence, `−/+` change speed,
`A−/A+` change type size.

In **Plain** mode, press and hold a word to hand that exact spot to the
Pacer: it switches mode, jumps there and dims everything before it.

## Timing model

At *n* wpm every word gets `60000 / n` ms, then:

- long words get up to +55 % (they take longer to recognise)
- a comma/colon adds 35 %, a full stop 90 %, an em dash 45 %
- the last word of a paragraph adds 40 %

The slide itself takes `min(110, 0.6 × delay)` ms, so faster speeds tighten the
motion instead of dropping frames. Jumps (sentence, chapter, mode change) are
instant.

The speed only changes when you change it. `−/+` (or ↑ ↓) move it by 25 wpm
and the value is saved per book, so tomorrow starts where today stopped.

## How extraction works

`epub_text.py` walks `META-INF/container.xml` → OPF → spine, and turns each
XHTML document into blocks of `[word, flags]` tokens (bold / italic /
superscript). The nav document or NCX supplies chapter titles. Documents
under 8 words, nav documents and non-XHTML spine entries are dropped, which
removes most front matter automatically.

Known limits, all deliberate:

- **CSS is ignored.** Text hidden with `display:none` will still be read.
  Fixed-layout EPUBs and comics are out of scope.
- **Images are dropped.** No figures, no captions.
- **Footnotes are inline**, small and dimmed. There is no jump-to-note.
- **No DRM**, obviously.
- Tables are flattened to a word stream; use Plain mode to see the damage.

## Files

```
epub_text.py      EPUB → chapters → tokens
server.py         stdlib HTTP server + JSON API + upload
static/           index.html, style.css, app.js
tools/make_sample.py   builds a three-chapter test book
data/             books, progress, sources
```

API, if you want to script it:

```
GET  /api/library
GET  /api/book/<id>
GET  /api/book/<id>/chapter/<n>
GET  /api/progress/<id>
POST /api/progress/<id>   {chapter, idx, wpm, mode}
POST /api/upload          raw bytes + X-Filename header
POST /api/add             {path: "/abs/path/book.epub"}
```
