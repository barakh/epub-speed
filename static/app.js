/* epub-speed — RSVP / pacer / plain reader. */
'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const S = {
  book: null,
  chapter: null,
  chapters: [],
  words: [],
  idx: 0,
  wpm: 300,
  mode: 'rsvp',
  chunk: 1,
  playing: false,
  timer: null,
  rampOn: true,
  rampCount: 0,
  zoom: 1,
  textBuiltFor: -1,
  hi: -1,
  saveTimer: null,
  starting: true,
};

/* ── helpers ───────────────────────────────────────────── */

async function api(url, body) {
  const opts = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : undefined;
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error('request failed: ' + url);
  return res.json();
}

function fmt(n) {
  return n >= 10000 ? (n / 1000).toFixed(1) + 'k' : String(n);
}

function minutes(words, wpm) {
  const m = Math.round(words / Math.max(120, wpm));
  return m < 1 ? '<1 min' : m + ' min';
}

function toast(msg) {
  const el = $('#endnote');
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 3200);
}

/* ── library ───────────────────────────────────────────── */

async function showLibrary() {
  pause();
  if (location.hash) history.replaceState(null, '', location.pathname);
  setScreen('library');
  const { books } = await api('/api/library');
  const list = $('#books');
  list.innerHTML = '';
  const real = books.filter((b) => !b.errors);
  const broken = books.find((b) => b.errors);

  if (!real.length) $('#empty').classList.remove('hidden');
  else $('#empty').classList.add('hidden');

  for (const book of real) {
    const card = document.createElement('button');
    card.className = 'card';
    const progress = book.progress;
    let resume = '';
    if (progress && progress.chapter != null) {
      const ch = book.chapters[progress.chapter];
      if (ch) {
        const pct = Math.round((progress.idx / Math.max(1, ch.words)) * 100);
        const where = pct > 0 ? `${pct}% · ch ${progress.chapter + 1}` : `ch ${progress.chapter + 1}`;
        resume = `<span class="resume">${where}</span>`;
      }
    }
    card.innerHTML = `
      <span class="spine"></span>
      <span class="main">
        <span class="t"></span>
        <span class="a"></span>
        <span class="m">${book.chapters.length} chapters · ${fmt(book.words)} words · ${minutes(book.words, 300)} at 300 wpm</span>
      </span>
      ${resume}`;
    card.querySelector('.t').textContent = book.title;
    card.querySelector('.a').textContent = book.author || '';

    card.addEventListener('click', () => openBook(book).catch((e) => toast(e.message)));
    list.appendChild(card);
  }

  if (broken) {
    const note = document.createElement('p');
    note.className = 'empty';
    note.style.paddingTop = '18px';
    note.textContent = broken.errors.map((e) => `${e.file}: ${e.error}`).join(' · ');
    list.appendChild(note);
  }
}

/* ── opening a book ────────────────────────────────────── */

async function openBook(summary) {
  S.book = await api('/api/book/' + summary.id);
  S.chapters = S.book.chapters;
  $('#book-title').textContent = S.book.title;
  history.replaceState(null, '', '#b/' + S.book.id);
  renderToc();

  const progress = S.book.progress || {};
  const chapter = Number.isInteger(progress.chapter) ? progress.chapter : 0;
  const idx = Number.isInteger(progress.idx) ? progress.idx : 0;
  if (progress.wpm) S.wpm = progress.wpm;
  if (progress.mode) S.mode = progress.mode;
  updateWpm();
  $$('.mode').forEach((b) => b.classList.toggle('on', b.dataset.mode === S.mode));

  await loadChapter(chapter, idx);
  setScreen('reader');
  applyMode();
  S.starting = true;
  $('#tap-hint').classList.remove('hidden');
}

async function loadChapter(number, idx = 0, autoplay = false) {
  if (number < 0 || number >= S.chapters.length) return;
  pause();
  const chapter = await api(`/api/book/${S.book.id}/chapter/${number}`);
  S.chapter = chapter;
  S.words = buildWords(chapter);
  S.idx = Math.min(Math.max(0, idx), S.words.length);
  S.textBuiltFor = -1;
  S.hi = -1;
  $('#chapter-title').textContent = chapter.title;
  $('#endnote').classList.add('hidden');
  markToc();

  if (S.mode === 'rsvp') renderRsvp();
  else {
    buildText();
    if (S.mode === 'pacer') { S.hi = -1; paint(S.idx); dimBefore(S.idx); }
  }
  updateMeta();
  saveProgress();
  if (autoplay) { S.playing = true; setPlayIcon(true); $('#tap-hint').classList.add('hidden'); schedule(); }
}

function buildWords(chapter) {
  const words = [];
  chapter.blocks.forEach((block, bi) => {
    const tokens = block.tokens;
    tokens.forEach((tok, ti) => {
      words.push({
        t: tok[0],
        f: tok[1],
        kind: block.kind,
        last: ti === tokens.length - 1,
        paraEnd: ti === tokens.length - 1,
        end: false,
      });
    });
    words[words.length - 1].paraEnd = true;
  });
  words.forEach((w) => {
    if (/[.!?…]["'”’)\]]*$/.test(w.t)) w.end = true;
    else if (w.last && /^h[1-6]$/.test(w.kind)) w.end = true;
  });
  return words;
}

/* ── RSVP rendering ────────────────────────────────────── */

function orp(text) {
  const n = text.length;
  if (n <= 1) return 0;
  if (n <= 5) return 1;
  if (n <= 9) return 2;
  if (n <= 13) return 3;
  return 4;
}

function chunkText(i) {
  const w = S.words[i];
  if (!w) return '';
  if (S.chunk === 2 && S.words[i + 1] && !w.end && !w.paraEnd) {
    return w.t + ' ' + S.words[i + 1].t;
  }
  return w.t;
}

function measureWidth(text) {
  const m = $('#measure');
  m.textContent = text;
  return m.getBoundingClientRect().width;
}

function renderRsvp() {
  const word = S.words[S.idx];
  const line = $('#rsvp-line');
  if (!word) { line.style.opacity = '0'; return; }
  line.style.opacity = '1';

  const text = chunkText(S.idx);
  let p = orp(text);
  if (p >= text.length) p = Math.max(0, text.length - 1);

  const left = text.slice(0, p);
  const pivot = text[p] || '';
  const right = text.slice(p + 1);

  line.children[0].textContent = left;
  line.children[1].textContent = pivot;
  line.children[2].textContent = right;
  line.style.fontWeight = word.f & 1 ? '700' : '400';
  line.style.fontStyle = word.f & 2 ? 'italic' : 'normal';
  line.style.opacity = word.f & 4 ? '.55' : '1';

  const m = $('#measure');
  m.style.fontWeight = line.style.fontWeight;
  m.style.fontStyle = line.style.fontStyle;
  const pivotLeft = measureWidth(left);
  const pivotRight = measureWidth(left + pivot);
  const shift = (pivotLeft + pivotRight) / 2;
  line.style.transform = `translate(${-shift}px, -50%)`;
}

/* ── paced / plain page ────────────────────────────────── */

function buildText() {
  const view = $('#textview');
  view.innerHTML = '';
  let i = 0;
  for (const block of S.chapter.blocks) {
    const tag = /^h[1-6]$/.test(block.kind) ? 'h2' : block.kind === 'blockquote' ? 'blockquote' : 'p';
    const el = document.createElement(tag);
    if (block.kind === 'li') el.className = 'li';
    for (const tok of block.tokens) {
      const span = document.createElement('span');
      span.className = 'w';
      span.dataset.i = String(i);
      span.textContent = tok[0];
      if (tok[1] & 1) span.style.fontWeight = '700';
      if (tok[1] & 2) span.style.fontStyle = 'italic';
      if (tok[1] & 4) span.classList.add('sup');
      el.appendChild(span);
      el.appendChild(document.createTextNode(' '));
      i++;
    }
    view.appendChild(el);
  }
  S.textBuiltFor = S.chapter.index;
  view.classList.toggle('placeless', S.mode === 'plain');
}

function paint(i) {
  if (S.mode !== 'pacer') return;
  const view = $('#textview');
  const old = S.hi >= 0 ? view.querySelector(`.w[data-i="${S.hi}"]`) : null;
  if (old) old.classList.remove('cur');
  if (i < S.hi) view.querySelectorAll('.w.done').forEach((n) => n.classList.remove('done'));
  if (old && i > S.hi) old.classList.add('done');

  const cur = view.querySelector(`.w[data-i="${i}"]`);
  if (cur) cur.classList.add('cur');
  S.hi = i;
  ensureVisible(cur);
}

function dimBefore(i) {
  const view = $('#textview');
  for (const node of view.querySelectorAll('.w')) {
    const k = Number(node.dataset.i);
    node.classList.toggle('done', k < i);
  }
}

function ensureVisible(node) {
  if (!node) return;
  const view = $('#textview');
  const r = node.getBoundingClientRect();
  const vr = view.getBoundingClientRect();
  if (r.top < vr.top + vr.height * 0.18 || r.bottom > vr.bottom - vr.height * 0.3) {
    const top = view.scrollTop + (r.top - vr.top) - vr.height * 0.35;
    view.scrollTo({ top, behavior: S.playing ? 'auto' : 'smooth' });
  }
}

/* ── transport ─────────────────────────────────────────── */

function delayFor(i) {
  const text = chunkText(i);
  const word = S.words[i];
  let d = 60000 / S.wpm;
  const len = text.length;
  if (len > 7) d *= 1 + Math.min(0.55, (len - 7) * 0.028);
  if (/[,;:]\s*$/.test(text)) d *= 1.35;
  else if (/[.!?…]["'”’)\]]*$/.test(text)) d *= 1.9;
  else if (/[—–-]$/.test(text)) d *= 1.45;
  if (word.paraEnd) d *= 1.4;
  if (text.includes(' ')) d *= 1.5;
  return Math.max(30, d);
}

function step() {
  const w = S.words[S.idx];
  if (!w) return 1;
  return S.chunk === 2 && S.words[S.idx + 1] && !w.end && !w.paraEnd ? 2 : 1;
}

function schedule() {
  clearTimeout(S.timer);
  if (!S.playing) return;
  const wait = delayFor(S.idx);
  S.timer = setTimeout(tick, wait);
}

function tick() {
  if (!S.playing) return;
  if (S.rampOn) {
    S.rampCount += step();
    if (S.rampCount >= 400 && S.wpm < 900) {
      S.rampCount -= 400;
      S.wpm += 10;
      updateWpm();
    }
  }
  S.idx += step();
  if (S.idx >= S.words.length) {
    S.idx = S.words.length - 1;
    finishChapter();
    return;
  }
  if (S.mode === 'rsvp') renderRsvp();
  else { paint(S.idx); if (S.mode === 'pacer') dimBefore(S.idx); }
  updateMeta();
  saveProgress();
  schedule();
}

async function finishChapter() {
  pause();
  const next = S.chapter.index + 1;
  if (next < S.chapters.length) {
    await loadChapter(next, 0, true);
  } else {
    toast('End of book · ☰ for chapters');
    renderRsvp();
    updateMeta();
  }
}

function setPlayIcon(on) {
  $('#btn-play').classList.toggle('playing', on);
  $('#btn-play').setAttribute('aria-label', on ? 'Pause' : 'Play');
}

function play() {
  if (S.playing) return;
  if (S.starting) {
    S.starting = false;
    $('#tap-hint').classList.add('hidden');
  }
  S.playing = true;
  S.rampCount = 0;
  setPlayIcon(true);
  schedule();
}

function pause() {
  S.playing = false;
  clearTimeout(S.timer);
  setPlayIcon(false);
}

function toggle() {
  if (S.playing) { pause(); saveProgress(); } else play();
}

function jump(dir) {
  let i = S.idx;
  if (dir > 0) {
    i += 1;
    while (i < S.words.length - 1 && !S.words[i - 1].end) i++;
  } else {
    i -= 1;
    while (i > 0 && !S.words[i - 1].end) i--;
  }
  setPos(i);
}

function setPos(i) {
  S.idx = Math.min(Math.max(0, i), S.words.length - 1);
  S.hi = -1;
  $('#textview').querySelectorAll('.w.done,.w.cur').forEach((n) => n.classList.remove('done', 'cur'));
  if (S.mode === 'rsvp') renderRsvp();
  else { paint(S.idx); if (S.mode === 'pacer') dimBefore(S.idx); }
  updateMeta();
  saveProgress();
  if (S.playing) schedule();
}

/* ── chrome ────────────────────────────────────────────── */

function setScreen(name) {
  $('#library').classList.toggle('hidden', name !== 'library');
  $('#reader').classList.toggle('hidden', name !== 'reader');
  if (name === 'library') { S.book = null; S.chapter = null; }
}

function applyMode() {
  $$('.mode').forEach((b) => b.classList.toggle('on', b.dataset.mode === S.mode));
  $('#rsvp').classList.toggle('hidden', S.mode !== 'rsvp');
  $('#textview').classList.toggle('hidden', S.mode === 'rsvp');
  $('#btn-chunk').classList.toggle('hidden', S.mode !== 'rsvp');
  if (S.mode === 'plain' && S.playing) pause();
  if (!S.chapter) return;
  if (S.mode !== 'rsvp') {
    if (S.textBuiltFor !== S.chapter.index) buildText();
    $('#textview').classList.toggle('placeless', S.mode === 'plain');
    S.hi = -1;
    paint(S.idx);
    if (S.mode === 'pacer') dimBefore(S.idx);
    if (S.mode === 'plain') ensureVisible($('#textview').querySelector('.w.cur'));
  } else {
    renderRsvp();
  }
}

function updateWpm() {
  $('#wpm-val').textContent = String(S.wpm);
}

function updateMeta() {
  const total = S.words.length || 1;
  const pct = Math.min(100, (S.idx / total) * 100);
  $('#progress-fill').style.width = pct.toFixed(1) + '%';
  const left = Math.max(0, total - S.idx);
  $('#pos').textContent = `${Math.round(pct)}% · ch ${S.chapter.index + 1}/${S.chapters.length}`;
  $('#stats').textContent = `${fmt(left)} words left · ${minutes(left, S.wpm)}`;
}

function renderToc() {
  const list = $('#toc-list');
  list.innerHTML = '';
  S.chapters.forEach((ch, i) => {
    const li = document.createElement('li');
    li.dataset.i = String(i);
    li.innerHTML = `<span class="n"></span><span class="w"></span><span class="c"></span>`;
    li.querySelector('.n').textContent = String(i + 1);
    li.querySelector('.w').textContent = ch.title;
    li.querySelector('.c').textContent = fmt(ch.words);
    li.addEventListener('click', () => {
      $('#toc').classList.add('hidden');
      loadChapter(i, 0).catch((e) => toast(e.message));
    });
    list.appendChild(li);
  });
}

function markToc() {
  $$('#toc-list li').forEach((li) => {
    li.classList.toggle('cur', S.chapter && Number(li.dataset.i) === S.chapter.index);
  });
}

/* ── progress ──────────────────────────────────────────── */

function saveProgress() {
  if (!S.book || !S.chapter) return;
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(sendProgress, 600);
}

function sendProgress(beacon = false) {
  if (!S.book || !S.chapter) return;
  const body = JSON.stringify({
    chapter: S.chapter.index,
    idx: S.idx,
    wpm: S.wpm,
    mode: S.mode,
    at: Date.now(),
  });
  if (beacon && navigator.sendBeacon) {
    navigator.sendBeacon(`/api/progress/${S.book.id}`, new Blob([body], { type: 'application/json' }));
    return;
  }
  fetch(`/api/progress/${S.book.id}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {});
}

/* ── wiring ────────────────────────────────────────────── */

function setZoom(delta) {
  S.zoom = Math.min(1.9, Math.max(0.7, Math.round((S.zoom + delta) * 100) / 100));
  document.documentElement.style.setProperty('--rsvp-zoom', S.zoom);
  $('#textview').style.fontSize = (1.12 * S.zoom).toFixed(3) + 'rem';
  try { localStorage.setItem('epub-speed-zoom', String(S.zoom)); } catch (e) {}
  if (S.mode === 'rsvp') renderRsvp();
}

function wire() {
  $('#upload').addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    toast('adding ' + file.name + ' …');
    try {
      const res = await fetch('/api/upload', {
        method: 'POST',
        headers: { 'X-Filename': encodeURIComponent(file.name) },
        body: file,
      });
      if (!res.ok) throw new Error('upload rejected');
      await showLibrary();
      toast('added ' + file.name);
    } catch (err) {
      toast('could not add that file');
    }
    e.target.value = '';
  });

  $('#btn-back').addEventListener('click', () => { sendProgress(true); showLibrary(); });
  $('#btn-toc').addEventListener('click', () => { $('#toc').classList.remove('hidden'); markToc(); });
  $('#toc-close').addEventListener('click', () => $('#toc').classList.add('hidden'));
  $('#help-close').addEventListener('click', () => $('#help').classList.add('hidden'));

  $('#btn-play').addEventListener('click', toggle);
  $('#btn-prev').addEventListener('click', () => jump(-1));
  $('#btn-next').addEventListener('click', () => jump(1));

  $('#wpm-up').addEventListener('click', () => { S.wpm = Math.min(900, S.wpm + 25); S.rampCount = 0; updateWpm(); updateMeta(); saveProgress(); });
  $('#wpm-dn').addEventListener('click', () => { S.wpm = Math.max(150, S.wpm - 25); S.rampCount = 0; updateWpm(); updateMeta(); saveProgress(); });

  $('#btn-chunk').addEventListener('click', () => {
    S.chunk = S.chunk === 2 ? 1 : 2;
    $('#btn-chunk').classList.toggle('on', S.chunk === 2);
    if (S.mode === 'rsvp') renderRsvp();
  });

  $('#btn-font-up').addEventListener('click', () => setZoom(0.12));
  $('#btn-font-dn').addEventListener('click', () => setZoom(-0.12));

  $$('.mode').forEach((b) => b.addEventListener('click', () => {
    if (S.mode === b.dataset.mode) return;
    S.mode = b.dataset.mode;
    applyMode();
    updateMeta();
    saveProgress();
  }));

  $('#stage').addEventListener('click', (e) => {
    const word = e.target.closest('.w');
    if (word && S.mode === 'pacer') {
      setPos(Number(word.dataset.i));
      return;
    }
    if (S.mode === 'plain') return;
    toggle();
  });

  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if ($('#reader').classList.contains('hidden')) return;
    switch (e.key) {
      case ' ': case 'k': e.preventDefault(); toggle(); break;
      case 'ArrowLeft': e.preventDefault(); jump(-1); break;
      case 'ArrowRight': e.preventDefault(); jump(1); break;
      case 'ArrowUp': e.preventDefault(); S.wpm = Math.min(900, S.wpm + 25); S.rampCount = 0; updateWpm(); break;
      case 'ArrowDown': e.preventDefault(); S.wpm = Math.max(150, S.wpm - 25); S.rampCount = 0; updateWpm(); break;
      case '1': case '2': case '3': {
        S.mode = ['rsvp', 'pacer', 'plain'][Number(e.key) - 1];
        applyMode(); break;
      }
      case 'c': $('#toc').classList.toggle('hidden'); markToc(); break;
      case 'Escape': $('#toc').classList.add('hidden'); $('#help').classList.add('hidden'); break;
      case '?': $('#help').classList.toggle('hidden'); break;
      default: return;
    }
    updateMeta();
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) sendProgress(true);
  });
  window.addEventListener('pagehide', () => sendProgress(true));

  try {
    const z = parseFloat(localStorage.getItem('epub-speed-zoom'));
    if (z) { S.zoom = z; setZoom(0); }
  } catch (e) {}
}

/* ── boot ──────────────────────────────────────────────── */

async function boot() {
  const deep = (location.hash.match(/^#b\/([0-9a-f]{6,})/) || [])[1];
  await showLibrary();
  if (!deep) return;
  const { books } = await api('/api/library');
  const book = books.find((b) => b.id === deep);
  if (book) await openBook(book);
}

wire();
boot().catch((e) => toast(e.message));
setInterval(() => { if (!document.hidden && S.book && !S.playing) sendProgress(); }, 15000);
