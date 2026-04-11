// Entry point: wires the chat, drawer, page viewer, and bridge.

import MarkdownIt from 'markdown-it';
import createDOMPurify from 'dompurify';

import * as api from './api.js';
import { bridge } from './bridge.js';

const md = new MarkdownIt({ breaks: true, linkify: true });
const DOMPurify = createDOMPurify(window);
const renderMarkdown = (text) => DOMPurify.sanitize(md.render(text || ''));

// ─── State ──────────────────────────────────────────────────

const state = {
  sessionId: null,
  streaming: false,
  pages: [],          // cached page list
  pagesLoaded: false,
  openPageId: null,
};

// ─── DOM refs ───────────────────────────────────────────────

const $ = (id) => document.getElementById(id);
const chatScroll = $('chat-scroll');
const welcome = $('welcome');
const composerInput = $('composer-input');
const sendBtn = $('send-btn');
const micBtn = $('mic-btn');
const menuBtn = $('menu-btn');
const newBtn = $('new-btn');
const drawer = $('pages-drawer');
const drawerBackdrop = $('drawer-backdrop');
const drawerClose = $('drawer-close');
const drawerList = $('drawer-list');
const drawerSearch = $('drawer-search');
const sheet = $('page-sheet');
const sheetBack = $('sheet-back');
const sheetTitle = $('sheet-title');
const sheetMeta = $('sheet-meta');
const sheetFrame = $('sheet-frame');
const sheetEdit = $('sheet-edit');
const topbarTitle = $('topbar-title');
const miniOutput = $('mini-output');
const miniOutputBody = $('mini-output-body');
const miniOutputClose = $('mini-output-close');

// ─── Chat rendering ─────────────────────────────────────────

function addMsg(role, initialContent = '') {
  if (welcome) welcome.classList.add('hidden');
  const row = document.createElement('div');
  row.className = `msg ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = role === 'assistant'
    ? renderMarkdown(initialContent) + '<span class="cursor"></span>'
    : escapeText(initialContent);
  row.appendChild(bubble);
  chatScroll.appendChild(row);
  chatScroll.scrollTop = chatScroll.scrollHeight;
  return { row, bubble };
}

function escapeText(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML.replace(/\n/g, '<br>');
}

function addPageCard(page) {
  const row = document.createElement('div');
  row.className = 'msg assistant';
  const card = document.createElement('div');
  card.className = 'page-card';
  card.innerHTML = `
    <div class="page-card-icon">
      <svg viewBox="0 0 24 24" fill="none" width="16" height="16">
        <path d="M6 3h9l4 4v14H6V3zM14 3v5h5" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
      </svg>
    </div>
    <div class="page-card-meta">
      <div class="page-card-title"></div>
      <div class="page-card-sub"></div>
    </div>`;
  card.querySelector('.page-card-title').textContent = page.title || page.id;
  card.querySelector('.page-card-sub').textContent = page.snippet || page.tags?.join(' · ') || '';
  card.addEventListener('click', () => openPage(page.id));
  row.appendChild(card);
  chatScroll.appendChild(row);
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

function maybeAutoLinkPages(textBuffer, lastBubble) {
  // Very light: detect `[title](page:<id>)` links and render page cards after
  // the bubble. Also detect bare references like `→ page: <id>`.
  const matches = [...textBuffer.matchAll(/\(page:([a-z0-9-]+)\)/g)];
  if (!matches.length) return;
  for (const m of matches) {
    const id = m[1];
    if (lastBubble.dataset.linkedPages?.includes(id)) continue;
    const cached = state.pages.find(p => p.id === id);
    if (cached) addPageCard(cached);
    lastBubble.dataset.linkedPages = (lastBubble.dataset.linkedPages || '') + ',' + id;
  }
}

// ─── Mini output strip ──────────────────────────────────────
// Always-available streaming view above the composer. Shown when the
// composer is focused or a message is in flight; stays visible after
// the stream finishes until dismissed or the next focus.

function showMiniOutput() {
  miniOutput.classList.add('show');
}

function hideMiniOutput() {
  miniOutput.classList.remove('show');
}

function setMiniOutputContent(html, { cursor = false, empty = false } = {}) {
  if (empty) {
    miniOutputBody.classList.add('empty');
    miniOutputBody.innerHTML = '';
    return;
  }
  miniOutputBody.classList.remove('empty');
  miniOutputBody.innerHTML = html + (cursor ? '<span class="mini-cursor"></span>' : '');
  // Pin to bottom so streaming text stays visible
  miniOutputBody.scrollTop = miniOutputBody.scrollHeight;
}

function isSheetOpen() {
  return sheet.getAttribute('aria-hidden') === 'false';
}

// ─── Send / stream ──────────────────────────────────────────

async function send(prompt) {
  if (!prompt.trim() || state.streaming) return;
  state.streaming = true;
  sendBtn.disabled = true;

  addMsg('user', prompt);
  const { bubble } = addMsg('assistant', '');
  let text = '';

  // Show the mini output as soon as a send starts — it's the primary
  // feedback surface when a page is open over the chat.
  showMiniOutput();
  setMiniOutputContent('', { cursor: true });

  try {
    const stream = api.streamChat({ prompt, sessionId: state.sessionId });
    for await (const ev of stream) {
      if (ev.type === 'delta') {
        text += ev.text;
        const rendered = renderMarkdown(text);
        bubble.innerHTML = rendered + '<span class="cursor"></span>';
        chatScroll.scrollTop = chatScroll.scrollHeight;
        setMiniOutputContent(rendered, { cursor: true });
      } else if (ev.type === 'done') {
        if (ev.sessionId) {
          state.sessionId = ev.sessionId;
          bridge.connect(state.sessionId);
        }
        const rendered = renderMarkdown(text);
        bubble.innerHTML = rendered;
        maybeAutoLinkPages(text, bubble);
        setMiniOutputContent(rendered, { cursor: false });
      }
    }
  } catch (err) {
    const msg = `<em style="color:var(--danger)">Error: ${err.message}</em>`;
    bubble.innerHTML = msg;
    setMiniOutputContent(msg);
  } finally {
    state.streaming = false;
    sendBtn.disabled = !composerInput.value.trim();
    // Refresh page list in background (edits may have happened)
    loadPages();
  }
}

// ─── Pages list / drawer ────────────────────────────────────

async function loadPages() {
  try {
    state.pages = await api.listPages();
    state.pagesLoaded = true;
    renderDrawerList();
  } catch (err) {
    console.warn('loadPages failed', err);
  }
}

function renderDrawerList() {
  const q = drawerSearch.value.trim().toLowerCase();
  const filtered = q
    ? state.pages.filter(p => p.title?.toLowerCase().includes(q))
    : state.pages;
  if (!filtered.length) {
    drawerList.innerHTML = '<div class="drawer-empty">No pages yet. Say hi in the chat to create one.</div>';
    return;
  }
  drawerList.innerHTML = '';
  for (const p of filtered) {
    const row = document.createElement('div');
    row.className = 'drawer-item';
    row.innerHTML = `
      <div class="drawer-item-icon">
        <svg viewBox="0 0 24 24" fill="none" width="16" height="16">
          <path d="M6 3h9l4 4v14H6V3zM14 3v5h5" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="drawer-item-meta">
        <div class="drawer-item-title"></div>
        <div class="drawer-item-sub"></div>
      </div>`;
    row.querySelector('.drawer-item-title').textContent = p.title || p.id;
    row.querySelector('.drawer-item-sub').textContent = p.tags?.join(' · ') || '';
    row.addEventListener('click', () => {
      openPage(p.id);
      closeDrawer();
    });
    drawerList.appendChild(row);
  }
}

function openDrawer() {
  drawer.setAttribute('aria-hidden', 'false');
  if (!state.pagesLoaded) loadPages();
}
function closeDrawer() { drawer.setAttribute('aria-hidden', 'true'); }

// ─── Page viewer sheet ──────────────────────────────────────

async function openPage(pageId) {
  state.openPageId = pageId;
  const cached = state.pages.find(p => p.id === pageId);
  sheetTitle.textContent = cached?.title || pageId;
  sheetMeta.textContent = cached?.tags?.length ? cached.tags.join(' · ') : '';
  sheetFrame.src = api.pageRawUrl(pageId);
  sheet.setAttribute('aria-hidden', 'false');
  // Topbar is hidden via CSS :has(.sheet[aria-hidden="false"]) when the
  // sheet is open — no JS title mutation needed.
}

function closePage() {
  sheet.setAttribute('aria-hidden', 'true');
  state.openPageId = null;
}

// ─── Voice input ────────────────────────────────────────────

let recognition = null;
function setupVoice() {
  const R = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!R) { micBtn.style.display = 'none'; return; }
  recognition = new R();
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.lang = 'en-US';

  recognition.addEventListener('result', (e) => {
    const last = e.results[e.results.length - 1];
    composerInput.value = last[0].transcript;
    autoGrow();
    sendBtn.disabled = !composerInput.value.trim();
  });
  recognition.addEventListener('end', () => {
    micBtn.classList.remove('active');
  });
}

function toggleVoice() {
  if (!recognition) return;
  if (micBtn.classList.contains('active')) {
    recognition.stop();
    micBtn.classList.remove('active');
  } else {
    try {
      recognition.start();
      micBtn.classList.add('active');
    } catch (err) { console.warn(err); }
  }
}

// ─── Input autogrow & send binding ──────────────────────────

function autoGrow() {
  composerInput.style.height = 'auto';
  composerInput.style.height = Math.min(composerInput.scrollHeight, 140) + 'px';
}

composerInput.addEventListener('input', () => {
  sendBtn.disabled = !composerInput.value.trim() || state.streaming;
  autoGrow();
});

composerInput.addEventListener('keydown', (e) => {
  // Plain Enter inserts a newline (default browser behavior).
  // Only Shift+Enter (or tapping the send button) submits.
  if (e.key === 'Enter' && e.shiftKey) {
    e.preventDefault();
    submit();
  }
});

composerInput.addEventListener('focus', () => {
  // On focus, reveal the mini output as a reserved streaming area.
  // If empty, show placeholder text; if it has content from the last
  // stream, keep showing it.
  if (!miniOutputBody.innerHTML.trim()) {
    setMiniOutputContent('', { empty: true });
  }
  showMiniOutput();
});

// Hide the mini output if the user blurs away AND there's no ongoing
// stream AND no past content to show. This keeps the UI quiet when
// the composer isn't in use.
composerInput.addEventListener('blur', () => {
  setTimeout(() => {
    if (state.streaming) return;
    if (document.activeElement === composerInput) return;
    if (miniOutputBody.classList.contains('empty')) hideMiniOutput();
  }, 150);
});

miniOutputClose.addEventListener('click', () => {
  hideMiniOutput();
  setMiniOutputContent('', { empty: true });
});

sendBtn.addEventListener('click', submit);

function submit() {
  const text = composerInput.value.trim();
  if (!text) return;
  composerInput.value = '';
  autoGrow();
  send(text);
}

// ─── Chips ──────────────────────────────────────────────────

document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    composerInput.value = chip.dataset.prompt || chip.textContent;
    sendBtn.disabled = false;
    composerInput.focus();
  });
});

// ─── Wire buttons ───────────────────────────────────────────

menuBtn.addEventListener('click', openDrawer);
drawerBackdrop.addEventListener('click', closeDrawer);
drawerClose.addEventListener('click', closeDrawer);
drawerSearch.addEventListener('input', renderDrawerList);

sheetBack.addEventListener('click', closePage);
sheetEdit.addEventListener('click', () => {
  if (!state.openPageId) return;
  composerInput.value = `Edit the "${sheetTitle.textContent}" page: `;
  composerInput.focus();
  closePage();
});

newBtn.addEventListener('click', () => {
  composerInput.value = 'Create a new page about ';
  sendBtn.disabled = false;
  composerInput.focus();
});

micBtn.addEventListener('click', toggleVoice);

// ─── Init ───────────────────────────────────────────────────

(async function init() {
  setupVoice();
  try {
    const latest = await api.latestSession();
    if (latest.session_id) {
      state.sessionId = latest.session_id;
      bridge.connect(state.sessionId);
      for (const m of latest.messages || []) {
        addMsg(m.role, m.content || '');
        const last = chatScroll.lastElementChild?.querySelector('.bubble');
        if (last && m.role === 'assistant') {
          last.innerHTML = renderMarkdown(m.content || '');
        }
      }
    }
  } catch (err) {
    console.warn('latest session failed', err);
  }
  loadPages();
  console.info('notes app ready');
})();
