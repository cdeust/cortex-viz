// Cortex Brain View — node search box (STEP 2 of tasks/todo.md).
//
// Builds a combobox in #chrome-top-right, feeds a Web Worker
// (search_worker.js) the full node index once the graph has streamed, and
// renders its trigram-similarity results in a dropdown. Selecting a result
// reuses the existing galaxy fly-to (JUG.selectNodeById) — this module owns
// UI only, never positions or colours a node itself.
//
// Contract with search_worker.js (frozen, see tasks/todo.md):
//   main->worker {type:'index', nodes:[{id,label,path,kind}]}
//   main->worker {type:'query', q, seq, limit}
//   worker->main {type:'ready', count, elapsed_ms}
//   worker->main {type:'results', seq, total, elapsed_ms, items:[{id,label,kind,path,score}]}

window.BRAIN = window.BRAIN || {};

(function () {
  var DEBOUNCE_MS = 200;
  var RESULT_LIMIT = 12;

  var worker = null;
  var ready = false;
  var seqCounter = 0;
  var debounceTimer = null;
  var items = [];          // last rendered result rows
  var activeIndex = -1;    // -1 = no row highlighted
  var initialized = false;

  var els = {};

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // Last path segment only — the dropdown shows a dimmed hint, not the full
  // path (matches the detail panel's own truncation convention).
  function pathTail(p) {
    if (!p) return '';
    var parts = String(p).split('/');
    return parts[parts.length - 1] || p;
  }

  function buildDom() {
    var host = document.getElementById('chrome-top-right');
    if (!host) return false;

    var wrap = document.createElement('div');
    wrap.className = 'aia-inputwrap';
    wrap.id = 'brain-search-wrap';

    var icon = document.createElement('span');
    icon.className = 'aia-input__icon';
    icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';

    var input = document.createElement('input');
    input.type = 'text';
    input.id = 'brain-search-input';
    input.className = 'aia-field';
    input.placeholder = 'Loading graph…';
    input.disabled = true;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', 'brain-search-listbox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('autocomplete', 'off');
    // Accessible name — the placeholder alone is not a valid name source: it
    // is a documented WCAG 4.1.2 failure pattern, and here it also mutates
    // ('Loading graph…' -> 'Search nodes…' -> 'Search unavailable'), so a
    // static aria-label is required (placeholder stays as a visual hint).
    input.setAttribute('aria-label', 'Search nodes');

    // Outer positioned/scrollable container. role=listbox's required-owned-
    // elements rule (ARIA 1.2) only allows option/group children, so the
    // actual listbox is an INNER element; the match-count / empty-state text
    // lives in a sibling role=status node instead of being injected directly
    // into the listbox.
    var dropdown = document.createElement('div');
    dropdown.className = 'brain-search-dropdown';
    dropdown.hidden = true;

    var listbox = document.createElement('div');
    listbox.id = 'brain-search-listbox';
    listbox.className = 'bsr-listbox';
    listbox.setAttribute('role', 'listbox');
    listbox.setAttribute('aria-label', 'Search results');

    var status = document.createElement('div');
    status.setAttribute('role', 'status');

    dropdown.appendChild(listbox);
    dropdown.appendChild(status);

    wrap.appendChild(icon);
    wrap.appendChild(input);
    wrap.appendChild(dropdown);
    // First child: search reads left-to-right before the toggle/reset pair,
    // and keeps their click handlers (wired separately in boot.js) untouched.
    host.insertBefore(wrap, host.firstChild);

    els.host = host;
    els.wrap = wrap;
    els.input = input;
    els.dropdown = dropdown;
    els.listbox = listbox;
    els.status = status;
    return true;
  }

  function closeDropdown() {
    // Invalidate any in-flight query: cancel the pending debounce (Escape or
    // Enter-selection pressed inside the 200ms debounce window must not let
    // the captured closure fire sendQuery afterwards) and bump seqCounter so
    // a worker reply already in flight can never match onWorkerMessage's
    // "msg.seq === seqCounter" guard and reopen the dropdown after the user
    // dismissed it. Every user-initiated close (Escape, selectItem, empty
    // query, outside click) routes through this one function.
    clearTimeout(debounceTimer);
    seqCounter++;
    els.dropdown.hidden = true;
    els.listbox.innerHTML = '';
    els.status.textContent = '';
    els.status.className = '';
    els.input.setAttribute('aria-expanded', 'false');
    els.input.removeAttribute('aria-activedescendant');
    if (els.host) els.host.classList.remove('search-open');
    items = [];
    activeIndex = -1;
  }

  function optionId(i) { return 'brain-search-opt-' + i; }

  function renderResults(total, list) {
    items = list;
    activeIndex = list.length ? 0 : -1;
    var q = els.input.value.trim();

    if (!list.length) {
      els.listbox.innerHTML = '';
      // textContent, not innerHTML — escapes q with no manual esc() needed
      // and cannot inject markup into the role=status node.
      els.status.textContent = 'no node matches "' + q + '"';
      els.status.className = 'bsr-empty';
    } else {
      var html = '';
      for (var i = 0; i < list.length; i++) {
        var it = list[i];
        html += '<div class="bsr-item" role="option" id="' + optionId(i) + '" ' +
          'aria-selected="' + (i === activeIndex ? 'true' : 'false') + '" data-idx="' + i + '">' +
          '<span class="bsr-label">' + esc(it.label) + '</span>' +
          '<span class="bsr-kind">' + esc(it.kind || '') + '</span>' +
          (it.path ? '<span class="bsr-path">' + esc(pathTail(it.path)) + '</span>' : '') +
          '</div>';
      }
      els.listbox.innerHTML = html;
      els.status.textContent = total.toLocaleString('en-US') + (total === 1 ? ' match' : ' matches');
      els.status.className = 'bsr-count';
    }

    els.dropdown.hidden = false;
    els.input.setAttribute('aria-expanded', 'true');
    // Raise the chrome cluster's stacking context above #detail-panel
    // (z-index:200) and #flow-panel (z-index:199) only while the dropdown is
    // open — see the "search-open" rule in brain-viz.html. #chrome-top-right
    // stays at its base z-index:30 otherwise so it does not float over the
    // #loading splash (z-index:50) during startup.
    if (els.host) els.host.classList.add('search-open');
    updateActiveDescendant();
  }

  function updateActiveDescendant() {
    var rows = els.listbox.querySelectorAll('.bsr-item');
    for (var i = 0; i < rows.length; i++) {
      rows[i].classList.toggle('bsr-item--active', i === activeIndex);
      rows[i].setAttribute('aria-selected', i === activeIndex ? 'true' : 'false');
    }
    if (activeIndex >= 0 && rows[activeIndex]) {
      els.input.setAttribute('aria-activedescendant', optionId(activeIndex));
      // RESULT_LIMIT (12) rows can exceed the dropdown's 340px scroll fold;
      // without this, ArrowDown can highlight (and Enter can select) a row
      // the user cannot see.
      rows[activeIndex].scrollIntoView({ block: 'nearest' });
    } else {
      els.input.removeAttribute('aria-activedescendant');
    }
  }

  function moveActive(delta) {
    if (!items.length) return;
    activeIndex = Math.max(0, Math.min(items.length - 1, activeIndex + delta));
    updateActiveDescendant();
  }

  // If the legend's per-kind isolate filter (boot.js toggleFilterKind) is
  // hiding the picked node's kind, clear it first — otherwise the fly-to
  // would land on a node the screen is still rendering invisible, an
  // honesty violation (screen state must match the selection). Reuses the
  // EXISTING toggle mechanism (exposed as BRAIN.toggleFilterKind) rather
  // than re-deriving filter state here.
  function clearBlockingFilter(kind) {
    if (BRAIN.filterKind && BRAIN.filterKind !== kind && BRAIN.toggleFilterKind) {
      BRAIN.toggleFilterKind(BRAIN.filterKind);
    }
  }

  function selectItem(i) {
    var it = items[i];
    if (!it) return;
    clearBlockingFilter(it.kind);
    closeDropdown();
    if (window.JUG && JUG.selectNodeById) JUG.selectNodeById(it.id);
    // Query text is intentionally preserved (spec: "keep the query text").
  }

  function sendQuery(q) {
    var seq = ++seqCounter;
    if (!q) {
      closeDropdown();
      return;
    }
    if (!worker || !ready) return;
    worker.postMessage({ type: 'query', q: q, seq: seq, limit: RESULT_LIMIT });
  }

  function onInput() {
    clearTimeout(debounceTimer);
    var q = els.input.value.trim();
    debounceTimer = setTimeout(function () { sendQuery(q); }, DEBOUNCE_MS);
  }

  function onKeydown(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); return; }
    if (e.key === 'Enter') {
      if (activeIndex >= 0) { e.preventDefault(); selectItem(activeIndex); }
      return;
    }
    if (e.key === 'Escape') {
      // Only consume Escape when the search widget actually has something to
      // dismiss (dropdown open or text typed). Otherwise let it bubble, so a
      // second Escape can still close the impact flow panel / conversation
      // modal (both listen on window keydown — see impact.js, discussion_
      // detail.js) instead of this handler eating every Escape on the page.
      var consumed = !els.dropdown.hidden || els.input.value !== '';
      if (!consumed) return;
      e.preventDefault();
      e.stopPropagation();
      els.input.value = '';
      closeDropdown();
      els.input.blur();
    }
  }

  // '/' focuses the search box from anywhere on the page, matching the
  // existing repo-wide keyboard guard (controls.js:147 — never fire while
  // the user is already typing into a form control).
  function wireSlashFocus() {
    window.addEventListener('keydown', function (e) {
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' ||
        e.target.tagName === 'TEXTAREA')) return;
      if (e.key === '/') {
        e.preventDefault();
        els.input.focus();
      }
    });
  }

  function onWorkerMessage(ev) {
    var msg = ev.data || {};
    if (msg.type === 'ready') {
      ready = true;
      els.input.disabled = false;
      els.input.placeholder = 'Search nodes…';
      console.log('[brain] search index ready:', msg.count, 'nodes in', msg.elapsed_ms, 'ms');
      return;
    }
    if (msg.type === 'results') {
      // Worker replies are strictly newer than the query that produced them,
      // so a reply is only current if it matches the LATEST issued seq.
      // closeDropdown() bumps seqCounter on every user-initiated dismissal
      // (Escape, selectItem, empty query, outside click), which invalidates
      // any reply still in flight for the dismissed query — the previous
      // guard ("msg.seq < lastAppliedSeq") could never catch this because
      // lastAppliedSeq only advanced on an APPLIED reply, not on a dismissal.
      if (msg.seq !== seqCounter) return;
      renderResults(msg.total, msg.items || []);
      return;
    }
  }

  function wireDropdownClicks() {
    els.dropdown.addEventListener('mousedown', function (e) {
      // mousedown (not click) so the row fires before the input's blur
      // handler would otherwise close the dropdown first.
      var row = e.target.closest ? e.target.closest('.bsr-item') : null;
      if (!row) return;
      e.preventDefault();
      selectItem(Number(row.getAttribute('data-idx')));
    });
  }

  // The dropdown otherwise never closes on its own: no blur/outside-click
  // handler previously existed (only Escape or emptying the input closed
  // it), so clicking the 3D canvas or a legend row left it open indefinitely,
  // intercepting pointer events in its footprint. pointerdown (not blur) so
  // clicking a non-focusable element inside the widget (e.g. the search
  // icon) cannot spuriously close it.
  function wireOutsideClose() {
    document.addEventListener('pointerdown', function (e) {
      if (!els.dropdown.hidden && !els.wrap.contains(e.target)) closeDropdown();
    });
  }

  BRAIN.searchInit = function (nodes) {
    if (initialized) return;
    initialized = true;
    if (!buildDom()) return;

    els.input.addEventListener('input', onInput);
    els.input.addEventListener('keydown', onKeydown);
    wireDropdownClicks();
    wireOutsideClose();
    wireSlashFocus();

    try {
      worker = new Worker('/brain/js/search_worker.js');
    } catch (e) {
      console.error('[brain] search worker failed to start', e);
      els.input.placeholder = 'Search unavailable';
      return;
    }
    worker.onmessage = onWorkerMessage;
    worker.onerror = function (e) {
      console.error('[brain] search worker error', e.message || e);
      els.input.placeholder = 'Search unavailable';
    };

    var indexed = (nodes || []).map(function (n) {
      return {
        id: n.id,
        label: n.label || n.id,
        path: n.path || null,
        kind: n.kind || n.type || 'node',
      };
    });
    worker.postMessage({ type: 'index', nodes: indexed });
  };
})();
