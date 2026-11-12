#!/usr/bin/env python3
"""Build a small test EPUB so the reader has something to chew on."""

from __future__ import annotations

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "data", "books", "sample-book.epub")

CHAPTERS = [
    ("The Anchor", """
      <h1>The Anchor</h1>
      <p>Every reader has felt it: the eye leaves the page, and by the time it
      returns you have re-read the same line twice. That backward jump is a
      <em>regression</em>, and it is the single largest tax on reading speed.</p>
      <p>Speed reading methods rarely fail because the words arrive too fast.
      They fail because the reader keeps reaching back for context that the
      format no longer offers.<sup>1</sup> The fix is not discipline. It is
      structure.</p>
      <p>A word shown at a fixed point on the screen removes the decision of
      where to look next. There is no deciding, so there is no reaching back.
      The reading rate becomes a number you set rather than a habit you
      negotiate with.</p>
      <blockquote><p>Comfort is a poor signal here. A pace that feels easy at
      first is often a pace that lets the old habit survive.</p></blockquote>
    """),
    ("What Gets Lost", """
      <h1>What Gets Lost</h1>
      <p>Nothing is free. When the page disappears, so does the map of it: no
      heading sliding past, no sense of how much paragraph remains, no easy
      return to the sentence before this one.</p>
      <p>Three things reliably survive the transition if you protect them:</p>
      <ul>
        <li><strong>Punctuation pauses.</strong> A comma is a breath; a period is
        a stop. Timed honestly, they do most of the work of comprehension.</li>
        <li><strong>Emphasis.</strong> The difference between <em>not</em> and
        not is the difference between an argument and its negation.</li>
        <li><strong>Paragraph breaks.</strong> A longer pause at the end of a
        paragraph gives the mind one beat to consolidate before the next idea
        arrives.</li>
      </ul>
      <p>Everything else, at six hundred words a minute, is decoration.</p>
    """),
    ("Calibration", """
      <h1>Calibration</h1>
      <p>Start slower than you expect. Two hundred and fifty words a minute
      feels insulting for about four minutes, which is exactly why it works:
      the comprehension has to catch up before the speed claim can be tested.</p>
      <p>Then raise the rate in small steps, ten words at a time, and notice
      where the meaning starts to smear. That boundary is not a wall. It is
      usually ten to fifteen percent further than it feels, and it moves.</p>
      <p>Dense material will always need a different tool. Numbers resist
      streaming. Arguments need their premises held in view. That is what the
      <em>paced</em> mode is for: the whole page stays, and only the drift
      stops.</p>
      <p>The target is not the fastest number you can survive. It is the
      fastest number you can forget you are reading at.</p>
    """),
]

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def chapter_xhtml(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title><link rel="stylesheet" href="style.css"/></head>
<body>{body}</body>
</html>
"""


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    items, refs, nav = [], [], []
    for i, (title, body) in enumerate(CHAPTERS, start=1):
        name = f"chapter{i}.xhtml"
        items.append(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>')
        refs.append(f'<itemref idref="c{i}"/>')
        nav.append(f'<li><a href="{name}">{title}</a></li>')

    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:epub-speed-sample</dc:identifier>
    <dc:title>Reading at the Speed of Thought</dc:title>
    <dc:creator>epub-speed</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    {''.join(items)}
  </manifest>
  <spine>{''.join(refs)}</spine>
</package>
"""

    nav_doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body><nav epub:type="toc"><h1>Contents</h1><ol>{''.join(nav)}</ol></nav></body>
</html>
"""

    css = "body{font-family:serif;}h1{font-size:1.4em;}blockquote{margin:1em 2em;}"

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav_doc)
        z.writestr("OEBPS/style.css", css)
        for i, (title, body) in enumerate(CHAPTERS, start=1):
            z.writestr(f"OEBPS/chapter{i}.xhtml", chapter_xhtml(title, body))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
