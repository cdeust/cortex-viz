// Cortex — Knowledge View
// Readable memory cards organized by domain with search, sort, and filtering.
//
// Data source: /api/memories (keyset-paged endpoint backed by Postgres).
// Lazy-loads pages on scroll; never holds the full memory set in memory,
// so it works at any N. Filter / sort / search are server-side params.
(function() {
  var container = null;
  var visible = false;
  var currentSort = 'heat';
  var currentDomain = 'all';
  var searchQuery = '';
  var expandedCardId = null;

  // Paged-fetch state. Reset on every show()/filter change. Each page
  // after the first is gated on a genuine user scroll.
  var PAGE_LIMIT = 50;
  var memoriesAccum = [];          // accumulated rows across pages
  var seenIds = Object.create(null);
  var nextCursor = null;
  var pageLoading = false;
  var pageDone = false;
  var lastFetchToken = 0;
  var scrolledSinceFetch = false;
  var _pageObserver = null;  // track the active IntersectionObserver so we can disconnect before recreating

  // Server-known filter facets. Loaded once on first show() so chips
  // can show ALL options up-front (not only what's been paged).
  var facets = null;

  // Active filters (server-side params).
  var filterStage = null;         // 'labile' | 'early_ltp' | 'late_ltp' | 'consolidated' | 'reconsolidating'
  var filterEmotion = null;       // 'urgent' | 'positive' | 'negative' | 'neutral'
  var filterMinHeat = null;       // 0.5 when "Hot" toggle on
  var filterProtected = false;    // true when "Protected" toggle on

  var STAGE_MAP = {
    labile:          { label: 'New',      cls: 'kv-badge-new' },
    early_ltp:       { label: 'Growing',  cls: 'kv-badge-growing' },
    late_ltp:        { label: 'Strong',   cls: 'kv-badge-strong' },
    consolidated:    { label: 'Stable',   cls: 'kv-badge-stable' },
    reconsolidating: { label: 'Updating', cls: 'kv-badge-updating' },
  };

  // Emotion inks — DS data tokens (tokens/colors.css --emo-*); the paper
  // surface remaps them to deep re-inked variants via tokens/surfaces.css.
  var EMO_COLORS = {
    urgency: 'var(--emo-urgent)', frustration: 'var(--emo-frustr)',
    satisfaction: 'var(--emo-satisf)', discovery: 'var(--emo-discov)',
    confusion: 'var(--emo-conflct)',
  };

  // ── Title extraction ──
  // Pulls a meaningful title from raw memory content
  function extractTitle(content) {
    if (!content) return 'Untitled Memory';
    var text = content.trim();

    // If content starts with a markdown heading, use it
    var headingMatch = text.match(/^#{1,3}\s+(.+)/);
    if (headingMatch) return headingMatch[1].trim();

    // Use the first sentence (up to first period, question mark, or newline)
    var firstLine = text.split('\n')[0].trim();
    var sentenceMatch = firstLine.match(/^(.+?[.?!])\s/);
    if (sentenceMatch && sentenceMatch[1].length >= 12) {
      return sentenceMatch[1];
    }

    // Fall back to first line, capped at reasonable length
    if (firstLine.length <= 120) return firstLine;
    // Truncate at last word boundary before 120 chars
    var truncated = firstLine.substring(0, 120);
    var lastSpace = truncated.lastIndexOf(' ');
    if (lastSpace > 60) truncated = truncated.substring(0, lastSpace);
    return truncated;
  }

  // Extract body preview (content after the title)
  function extractPreview(content, title) {
    if (!content) return '';
    var text = content.trim();

    // Remove the heading line if title came from a heading
    if (text.match(/^#{1,3}\s+/)) {
      text = text.replace(/^#{1,3}\s+.+\n?/, '').trim();
    } else {
      // Remove the title portion from the beginning
      var idx = text.indexOf(title);
      if (idx === 0) {
        text = text.substring(title.length).trim();
      }
    }

    // Strip markdown artifacts for cleaner preview
    text = text.replace(/^[-*]\s+/gm, '').replace(/\*\*/g, '').replace(/`/g, '');

    // Return first ~200 chars at a word boundary
    if (text.length <= 200) return text;
    var cut = text.substring(0, 200);
    var sp = cut.lastIndexOf(' ');
    if (sp > 100) cut = cut.substring(0, sp);
    return cut;
  }

  function init() {
    container = document.getElementById('knowledge-container');
    if (!container) return;

    JUG.on('state:activeView', function(ev) {
      if (ev.value === 'knowledge') show(); else hide();
    });
    // Re-render counters if the legacy lastData arrives, but we no
    // longer depend on it for the actual memory list — that comes
    // from the paged /api/memories endpoint.
    JUG.on('state:lastData', function() { /* no-op */ });
  }

  function show() {
    if (!container) return;
    container.style.display = 'flex';
    visible = true;
    if (!facets) {
      _fetchFacets().then(function() { _resetAndFetch(); });
    } else {
      _resetAndFetch();
    }
  }

  function _fetchFacets() {
    return fetch('/api/memories/facets', { cache: 'no-store' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) { if (d) facets = d; })
      .catch(function(err) { console.warn('[knowledge] facets fetch failed:', err); });
  }

  function hide() {
    visible = false;
    if (container) container.style.display = 'none';
    closeExpanded();
  }

  // ── Lazy-load paged memories from /api/memories ──
  function _serverSort() {
    if (currentSort === 'recency') return 'recent';
    if (currentSort === 'importance') return 'heat'; // server has no importance index; fall back to heat (client-side reorder is per-page)
    return 'heat';
  }

  function _resetAndFetch() {
    memoriesAccum = [];
    seenIds = Object.create(null);
    nextCursor = null;
    pageDone = false;
    pageLoading = false;
    domainsSeen = Object.create(null);
    globalCount = 0;
    hotCount = 0;
    lastFetchToken++;
    scrolledSinceFetch = true;  // allow the very first page on open/filter
    rebuild();
    _fetchPage();
  }

  function _fetchPage() {
    if (pageDone || pageLoading) return;
    // Gate: subsequent pages require a real user scroll since the
    // last fetch. Without it, the IntersectionObserver on a sentinel
    // that's already in-view loops forever.
    if (!scrolledSinceFetch) return;
    scrolledSinceFetch = false;
    pageLoading = true;
    var token = lastFetchToken;
    var qs = '?limit=' + PAGE_LIMIT + '&sort=' + encodeURIComponent(_serverSort());
    if (nextCursor) qs += '&cursor=' + encodeURIComponent(nextCursor);
    if (currentDomain === 'global') {
      qs += '&global=1';
    } else if (currentDomain !== 'all') {
      qs += '&domain=' + encodeURIComponent(currentDomain);
    }
    if (searchQuery) qs += '&search=' + encodeURIComponent(searchQuery);
    if (filterStage) qs += '&stage=' + encodeURIComponent(filterStage);
    if (filterEmotion) qs += '&emotion=' + encodeURIComponent(filterEmotion);
    if (filterMinHeat != null) qs += '&min_heat=' + encodeURIComponent(filterMinHeat);
    if (filterProtected) qs += '&protected=1';
    fetch('/api/memories' + qs, { cache: 'no-store' })
      .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(data) {
        if (token !== lastFetchToken) return; // superseded by newer fetch
        var items = data.items || [];
        for (var i = 0; i < items.length; i++) {
          var m = items[i];
          if (seenIds[m.id]) continue;
          seenIds[m.id] = true;
          if (currentDomain === 'global' && !m.isGlobal) continue;
          memoriesAccum.push(m);
          if (m.domain) domainsSeen[m.domain] = (domainsSeen[m.domain] || 0) + 1;
          if (m.isGlobal) globalCount++;
          if ((m.heat || 0) >= 0.5) hotCount++;
        }
        nextCursor = data.next_cursor || null;
        pageDone = !nextCursor;
        pageLoading = false;
        _renderPagedGrid();
      })
      .catch(function(err) {
        console.warn('[knowledge] /api/memories fetch failed:', err);
        pageLoading = false;
        if (memoriesAccum.length === 0) {
          var grid = document.getElementById('kv-grid');
          if (grid) {
            grid.innerHTML = '';
            var empty = el('div', 'kv-empty');
            var t = el('div', 'kv-empty-title'); t.textContent = 'Failed to load memories';
            empty.appendChild(t);
            var s = el('div', 'kv-empty-sub'); s.textContent = String(err.message || err);
            empty.appendChild(s);
            grid.appendChild(empty);
          }
        }
      });
  }

  function getDomains() { return Object.keys(domainsSeen).sort(); }

  // ── Build the view (chrome only — grid populated by _renderPagedGrid) ──
  function rebuild() {
    if (!container) return;
    container.innerHTML = '';

    // Domain pills (rebuilt on demand from domainsSeen as pages arrive).
    var domainBar = el('div', 'kv-domain-bar');
    domainBar.id = 'kv-domain-bar';
    container.appendChild(domainBar);
    _refreshDomainBar();

    // Search + sort row
    var searchRow = el('div', 'kv-search-row');
    var searchInput = el('input', 'kv-search');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search memories...';
    searchInput.value = searchQuery;
    var debounce = null;
    searchInput.addEventListener('input', function() {
      clearTimeout(debounce);
      debounce = setTimeout(function() {
        searchQuery = searchInput.value;
        _resetAndFetch();
      }, 250);
    });
    searchRow.appendChild(searchInput);

    var sortGroup = el('div', 'kv-sort-group');
    var sortLabel = el('span', 'kv-sort-label');
    sortLabel.textContent = 'Sort:';
    sortGroup.appendChild(sortLabel);
    var sortLabels = { heat: 'Activity', recency: 'Recency', importance: 'Importance' };
    ['heat', 'recency', 'importance'].forEach(function(s) {
      var btn = el('button', 'kv-sort-btn');
      btn.textContent = sortLabels[s];
      if (s === currentSort) btn.classList.add('active');
      btn.addEventListener('click', function() {
        currentSort = s;
        _resetAndFetch();
      });
      sortGroup.appendChild(btn);
    });
    searchRow.appendChild(sortGroup);
    container.appendChild(searchRow);

    // Filter chip row (server-side facets). Rebuilt once facets load.
    // Layout + colour live in knowledge.css (.kv-filter-row) — paper chips.
    var filterRow = el('div', 'kv-filter-row');
    filterRow.id = 'kv-filter-row';
    container.appendChild(filterRow);
    _refreshFilterRow();

    // Stats bar (live: counters update as pages stream in)
    var statsBar = el('div', 'kv-stats-bar');
    statsBar.id = 'kv-stats-bar';
    container.appendChild(statsBar);
    _refreshStatsBar();

    // Grid container — items appended page-by-page, sentinel at bottom
    // triggers next-page fetch via IntersectionObserver.
    var grid = el('div', 'kv-grid');
    grid.id = 'kv-grid';
    container.appendChild(grid);

    // Styling lives in knowledge.css (.kv-load-sentinel / .kv-load-more)
    // — mono micro-caps status + DS secondary button on paper.
    var sentinel = el('div', 'kv-load-sentinel');
    sentinel.id = 'kv-load-sentinel';
    var sentinelText = el('span'); sentinelText.id = 'kv-load-text'; sentinelText.textContent = 'Loading more memories…';
    sentinel.appendChild(sentinelText);
    var loadMoreBtn = el('button', 'kv-load-more');
    loadMoreBtn.id = 'kv-load-more';
    loadMoreBtn.textContent = 'Load more';
    loadMoreBtn.addEventListener('click', function(){
      scrolledSinceFetch = true;   // explicit user intent
      _fetchPage();
    });
    sentinel.appendChild(loadMoreBtn);
    container.appendChild(sentinel);
    _attachIntersectionObserver(sentinel);
    _attachScrollBackstop(sentinel);
  }

  function _attachScrollBackstop(sentinel) {
    var maybeFetch = function() {
      if (!visible || pageLoading || pageDone) return;
      // Arm the gate — the user has now genuinely scrolled.
      scrolledSinceFetch = true;
      var rect = sentinel.getBoundingClientRect();
      var vh = window.innerHeight || document.documentElement.clientHeight;
      if (rect.top < vh + 400) _fetchPage();
    };
    container.addEventListener('scroll', maybeFetch, { passive: true });
    window.addEventListener('scroll', maybeFetch, { passive: true });
    container.addEventListener('wheel', maybeFetch, { passive: true });
  }

  function _refreshDomainBar() {
    var bar = document.getElementById('kv-domain-bar');
    if (!bar) return;
    bar.innerHTML = '';
    bar.appendChild(domainPill('All', 'all', false, facets ? facets.total : null));
    if (!facets || facets.global > 0) {
      bar.appendChild(domainPill('Global', 'global', true, facets ? facets.global : null));
    }
    // Server-known domains beat the accumulated set — even if you've
    // only paged through 50 of 179k memories, every domain pill is
    // available with its full count.
    var serverDomains = facets && facets.domains
      ? facets.domains.map(function(d) { return d; })
      : Object.keys(domainsSeen).sort().map(function(name) {
          return { name: name, count: domainsSeen[name] };
        });
    serverDomains.forEach(function(d) {
      bar.appendChild(domainPill(shortDomain(d.name), d.name, false, d.count));
    });
  }

  // Build the filter chip row: stage, emotion, hot, protected.
  function _refreshFilterRow() {
    var row = document.getElementById('kv-filter-row');
    if (!row) return;
    row.innerHTML = '';

    var label = el('span', 'kv-filter-label');
    label.textContent = 'Filter';
    row.appendChild(label);

    // Stage chips (consolidation pipeline).
    var stageOpts = [
      { v: null,              t: 'Any stage' },
      { v: 'labile',          t: 'New' },
      { v: 'early_ltp',       t: 'Growing' },
      { v: 'late_ltp',        t: 'Strong' },
      { v: 'consolidated',    t: 'Stable' },
      { v: 'reconsolidating', t: 'Updating' },
    ];
    stageOpts.forEach(function(opt) {
      var c = facets && opt.v ? facets.stages[opt.v] : (opt.v == null ? (facets ? facets.total : '') : '');
      row.appendChild(_chip(opt.t,
        filterStage === opt.v,
        function() { filterStage = opt.v; _resetAndFetch(); }, null, c));
    });

    var sep1 = el('span', 'kv-filter-sep'); sep1.textContent = '·';
    row.appendChild(sep1);

    // Emotion chips — ink comes from the DS data tokens (--emo-*),
    // deep re-inked on paper via tokens/surfaces.css.
    var emoOpts = [
      { v: null,       t: 'Any feel' },
      { v: 'urgent',   t: 'Urgent', color: 'var(--emo-urgent)' },
      { v: 'positive', t: 'Positive', color: 'var(--emo-satisf)' },
      { v: 'negative', t: 'Negative', color: 'var(--emo-frustr)' },
      { v: 'neutral',  t: 'Neutral' },
    ];
    emoOpts.forEach(function(opt) {
      var c = facets && opt.v ? facets.emotions[opt.v] : '';
      row.appendChild(_chip(opt.t,
        filterEmotion === opt.v,
        function() { filterEmotion = opt.v; _resetAndFetch(); }, opt.color, c));
    });

    var sep2 = el('span', 'kv-filter-sep'); sep2.textContent = '·';
    row.appendChild(sep2);

    // Boolean toggles — heat / protection inks from the DS data tokens.
    row.appendChild(_chip(
      'Hot',
      filterMinHeat != null,
      function() { filterMinHeat = filterMinHeat != null ? null : 0.5; _resetAndFetch(); },
      'var(--heat-hot)', facets ? facets.hot : ''));
    row.appendChild(_chip(
      'Protected',
      filterProtected,
      function() { filterProtected = !filterProtected; _resetAndFetch(); },
      'var(--warn-ink)', facets ? facets.protected : ''));

    // Reset button if anything is active. Styled by .kv-clear-btn
    // (DS secondary button on paper).
    if (filterStage || filterEmotion || filterMinHeat != null || filterProtected
        || currentDomain !== 'all' || searchQuery) {
      var clr = el('button', 'kv-clear-btn'); clr.textContent = 'Clear all';
      clr.addEventListener('click', function() {
        filterStage = null; filterEmotion = null; filterMinHeat = null;
        filterProtected = false; currentDomain = 'all'; searchQuery = '';
        _resetAndFetch();
      });
      row.appendChild(clr);
    }
  }

  // Filter chip — DS Chip spec via .kv-chip in knowledge.css. `accent`
  // is a DS data-token string (e.g. 'var(--emo-urgent)') exposed to CSS
  // as --chip-ink; `count` renders in mono per the DS chip spec.
  function _chip(label, active, onClick, accent, count) {
    var b = el('button', 'kv-chip' + (active ? ' active' : ''));
    if (accent) {
      b.style.setProperty('--chip-ink', accent);
      b.appendChild(el('span', 'kv-chip-dot'));
    }
    var t = el('span'); t.textContent = label;
    b.appendChild(t);
    if (count !== '' && count != null) {
      var n = el('span', 'kv-chip-count'); n.textContent = String(count);
      b.appendChild(n);
    }
    b.addEventListener('click', onClick);
    return b;
  }

  function _refreshStatsBar() {
    var bar = document.getElementById('kv-stats-bar');
    if (!bar) return;
    bar.innerHTML = '';
    bar.appendChild(statEl(memoriesAccum.length + (pageDone ? '' : '+'), 'loaded'));
    bar.appendChild(statEl(getDomains().length, 'domains'));
    if (globalCount > 0) bar.appendChild(statEl(globalCount, 'global rules'));
    if (hotCount > 0) bar.appendChild(statEl(hotCount, 'hot'));
  }

  function _attachIntersectionObserver(sentinel) {
    if (!('IntersectionObserver' in window)) return;  // scroll-backstop covers it
    // Use the viewport (root: null) — the kv-grid scrolls inside the
    // body in the unified-viz layout, NOT inside container. Anchoring
    // root to container made the sentinel always count as "inside the
    // root" so isIntersecting was permanently true (or permanently
    // false depending on overflow), and pagination stalled.
    if (_pageObserver) { _pageObserver.disconnect(); _pageObserver = null; }
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) { if (e.isIntersecting) _fetchPage(); });
    }, { root: null, rootMargin: '400px' });
    io.observe(sentinel);
    _pageObserver = io;
  }

  function _renderPagedGrid() {
    var grid = document.getElementById('kv-grid');
    var sentinel = document.getElementById('kv-load-sentinel');
    if (!grid) return;

    // Group into globals + by-domain on the cumulative accumulated set.
    var globals = [];
    var byDomain = {};
    for (var i = 0; i < memoriesAccum.length; i++) {
      var m = memoriesAccum[i];
      if (m.isGlobal) { globals.push(m); continue; }
      if (currentDomain !== 'all' && currentDomain !== 'global'
          && m.domain !== currentDomain) continue;
      var d = m.domain || 'unknown';
      if (!byDomain[d]) byDomain[d] = [];
      byDomain[d].push(m);
    }

    grid.innerHTML = '';
    // Flat 2-column exhibit grid — Réf A shows no domain group headers or
    // banners; each card's exhibit header already carries its category slot.
    var flat;
    if (currentDomain === 'all' || currentDomain === 'global') {
      flat = globals.slice();
      Object.keys(byDomain).sort().forEach(function(d) {
        flat = flat.concat(byDomain[d]);
      });
    } else {
      flat = byDomain[currentDomain] || [];
    }
    flat.forEach(function(m) { grid.appendChild(buildCard(m, memoriesAccum)); });

    if (memoriesAccum.length === 0 && pageDone) {
      var empty = el('div', 'kv-empty');
      var t = el('div', 'kv-empty-title'); t.textContent = 'No memories found';
      empty.appendChild(t);
      var s = el('div', 'kv-empty-sub');
      s.textContent = searchQuery ? 'No memories match "' + searchQuery + '"' : 'No memories yet';
      empty.appendChild(s);
      grid.appendChild(empty);
    }

    var sText = document.getElementById('kv-load-text');
    var sBtn = document.getElementById('kv-load-more');
    if (sText) {
      sText.textContent = pageDone
        ? '— end of memories — ' + memoriesAccum.length + ' loaded'
        : (pageLoading
            ? 'Loading more memories… (' + memoriesAccum.length + ' so far)'
            : 'Scroll for more · ' + memoriesAccum.length + ' loaded');
    }
    if (sBtn) sBtn.style.display = pageDone ? 'none' : 'inline-block';

    _refreshDomainBar();
    _refreshStatsBar();
  }

  // ── Symbol ↔ memory impact resolution ──
  // A memory is "impacted by" a code symbol when (a) a file touched by
  // the memory (path / file_refs / file_path) is the symbol's parent
  // file, or (b) the symbol's label appears verbatim in the memory's
  // body or tags. We resolve this purely from the already-loaded graph
  // data so no extra server round-trip is needed.
  var _symIndexCache = null;
  var _symIndexKey = 0;
  function _buildSymbolIndex() {
    var data = window.JUG && JUG.state && JUG.state.lastData;
    if (!data || !Array.isArray(data.nodes)) return null;
    // Cache by data-identity so repeated card renders reuse the index.
    var key = data.nodes.length + ':' + (data.edges ? data.edges.length : 0);
    if (_symIndexCache && _symIndexKey === key) return _symIndexCache;
    // Object.create(null) — no prototype, so a key like "constructor"
    // or "toString" doesn't short-circuit to the builtin function.
    var byPath = Object.create(null);
    var byLabel = Object.create(null);
    data.nodes.forEach(function (n) {
      if (n.kind !== 'symbol' && n.type !== 'symbol') return;
      var p = n.path || '';
      if (p) {
        if (!byPath[p]) byPath[p] = [];
        byPath[p].push(n);
        var base = p.split('/').pop();
        if (base && base !== p) {
          if (!byPath[base]) byPath[base] = [];
          byPath[base].push(n);
        }
      }
      // Case-sensitive key — function names in memories are usually
      // written with their original casing (`appendGraphDelta`), and
      // case-sensitive matching avoids "do" matching every "Do" verb.
      var lbl = (n.label || '').trim();
      if (lbl && lbl.length >= 4) {
        if (!byLabel[lbl]) byLabel[lbl] = [];
        byLabel[lbl].push(n);
      }
    });
    _symIndexCache = { byPath: byPath, byLabel: byLabel };
    _symIndexKey = key;
    return _symIndexCache;
  }
  function _isWordChar(ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
           (ch >= '0' && ch <= '9') || ch === '_';
  }
  function _hasWordMatch(hay, needle) {
    // Case-sensitive indexOf + manual word-boundary check — avoids
    // the 4000-per-card RegExp churn that was freezing the tab.
    if (!needle) return false;
    var idx = 0;
    while (true) {
      var pos = hay.indexOf(needle, idx);
      if (pos === -1) return false;
      var before = pos === 0 ? '' : hay.charAt(pos - 1);
      var after = pos + needle.length >= hay.length ? '' : hay.charAt(pos + needle.length);
      if (!_isWordChar(before) && !_isWordChar(after)) return true;
      idx = pos + 1;
    }
  }

  function resolveMemorySymbols(mem, maxN) {
    var idx = _buildSymbolIndex();
    if (!idx) return [];
    var refs = [];
    var seen = Object.create(null);
    // File-based matches (cheap, exact).
    var fileRefs = [];
    if (mem.path) fileRefs.push(mem.path);
    if (Array.isArray(mem.file_refs)) fileRefs = fileRefs.concat(mem.file_refs);
    if (Array.isArray(mem.fileRefs)) fileRefs = fileRefs.concat(mem.fileRefs);
    for (var f = 0; f < fileRefs.length && refs.length < (maxN || 12); f++) {
      var fp = fileRefs[f];
      if (!fp) continue;
      var hits = idx.byPath[fp] || [];
      var base = fp.split('/').pop();
      if (base && base !== fp && idx.byPath[base]) hits = hits.concat(idx.byPath[base]);
      for (var h = 0; h < hits.length && refs.length < (maxN || 12); h++) {
        var s = hits[h];
        if (seen[s.id]) continue;
        seen[s.id] = 1;
        refs.push({ node: s, via: 'file' });
      }
    }
    if (refs.length >= (maxN || 12)) return refs.slice(0, maxN || 12);

    // Label-based matches — iterate labels, not characters. Cap at
    // 1500 labels and stop as soon as we've filled maxN to keep the
    // per-card cost bounded on 10k-symbol graphs.
    var hay = (mem.content || mem.body || '') + ' ' +
              ((mem.tags || []).join(' '));
    if (hay.length > 4) {
      var labelKeys = Object.keys(idx.byLabel);
      var cap = Math.min(labelKeys.length, 1500);
      for (var i = 0; i < cap && refs.length < (maxN || 12); i++) {
        var k = labelKeys[i];
        if (hay.indexOf(k) === -1) continue;   // cheap pre-filter
        if (!_hasWordMatch(hay, k)) continue;  // word-boundary check
        var syms = idx.byLabel[k];
        for (var j = 0; j < syms.length && refs.length < (maxN || 12); j++) {
          var sym = syms[j];
          if (seen[sym.id]) continue;
          seen[sym.id] = 1;
          refs.push({ node: sym, via: 'label' });
        }
      }
    }
    return refs.slice(0, maxN || 12);
  }

  // Shared export so timeline.js (Board) reuses the same resolver.
  window.JUG = window.JUG || {};
  window.JUG._kvResolve = resolveMemorySymbols;

  // ── Build a memory card — Spec DD-01 anatomy, zone by zone ──
  // source: DS cards/data-memory-card.html ("Memory card — anatomy").
  // Title (the source, serif) · Feeling (emotion word + signed valence and
  // arousal, mono, never colour-only) · excerpt · MEANING (verbatim excerpt
  // in italic mono quotes) · badges (stage · domain · Hot) + relative time ·
  // meters (fixed order Heat · Importance · Valence · Arousal, amber fill,
  // zero = empty track, never hidden) · provenance rows · capture tags.

  // Verbatim meaning excerpt: the record's first inline-code span joined
  // with its most specific capture tag — mirrors the DD-01 example
  // (“ cargo test --lib graph_store · test-result ”). Nothing invented:
  // both halves are verbatim from the record; absent halves are omitted.
  function meaningExcerpt(mem) {
    var raw = mem.content || mem.label || '';
    var m = raw.match(/`([^`\n]{3,90})`/);
    var frag = m ? m[1] : '';
    var tags = (mem.tags || []).filter(function(t) { return t !== 'auto-captured'; });
    var tag = tags.length ? tags[tags.length - 1] : '';
    if (frag && tag) return frag + ' · ' + tag;
    return frag || tag;
  }

  function buildCard(mem, allMems) {
    var heat = mem.heat || 0;
    var storeType = (mem.storeType || mem.store_type) === 'semantic' ? 'semantic' : 'episodic';
    var emotion = mem.emotion || mem.dominant_emotion || 'neutral';
    var valence = typeof mem.valence === 'number' ? mem.valence
      : (typeof mem.emotional_valence === 'number' ? mem.emotional_valence : null);
    var arousal = typeof mem.arousal === 'number' ? mem.arousal : null;
    var importance = typeof mem.importance === 'number' ? mem.importance : null;
    var stage = mem.consolidationStage || mem.stage || '';

    var card = el('div', 'kv-card aia-card');
    if (mem.isGlobal) card.classList.add('kv-card-global');
    if (heat >= 0.5) card.classList.add('kv-card-hot');

    // Title — the source, serif.
    var title = extractTitle(mem.content || mem.label || '');
    var titleEl = el('h3', 'kv-mc-title');
    titleEl.textContent = title;
    card.appendChild(titleEl);

    // Feeling — dot + emotion word + signed valence (the arrow carries the
    // sign, magnitude verbatim) + arousal; mono, never colour-only.
    var feel = el('div', 'kv-mc-feel');
    feel.appendChild(el('span', 'kv-mc-feel-dot'));
    var feelText = emotion.charAt(0).toUpperCase() + emotion.slice(1);
    if (valence !== null) feelText += ' · ' + (valence < 0 ? '↓' : '↑') + ' ' + Math.abs(valence).toFixed(2);
    if (arousal !== null) feelText += ' · ↑ ' + arousal.toFixed(2);
    feel.appendChild(document.createTextNode(feelText));
    card.appendChild(feel);

    // Excerpt — the record's first passage after the title, sans voice.
    var prev = extractPreview(mem.content || mem.label || '', title).replace(/\s+/g, ' ').trim();
    if (prev) {
      var cmd = el('div', 'kv-mc-cmd');
      if (prev.length > 140) {
        var cut = prev.slice(0, 140);
        var sp = cut.lastIndexOf(' ');
        if (sp > 70) cut = cut.slice(0, sp);
        prev = cut + '…';
      }
      cmd.textContent = prev;
      card.appendChild(cmd);
    }

    // MEANING — micro-label + verbatim excerpt in italic mono quotes.
    var quote = meaningExcerpt(mem);
    if (quote) {
      var ml = el('div', 'kv-mc-ml');
      ml.textContent = 'Meaning';
      card.appendChild(ml);
      var q = el('div', 'kv-mc-quote');
      q.textContent = '“ ' + quote + ' ”';
      card.appendChild(q);
    }

    // Badges — stage · domain · Hot in amber; relative time right-aligned.
    var brow = el('div', 'kv-mc-brow');
    if (stage) {
      var stBadge = el('span', 'aia-badge aia-badge--info');
      stBadge.textContent = stage.charAt(0).toUpperCase() + stage.slice(1);
      brow.appendChild(stBadge);
    }
    if (mem.domain) {
      var domBadge = el('span', 'aia-badge');
      domBadge.textContent = shortDomain(mem.domain);
      brow.appendChild(domBadge);
    }
    if (heat >= 0.5) {
      var hotBadge = el('span', 'aia-badge aia-badge--warn');
      hotBadge.textContent = 'Hot';
      brow.appendChild(hotBadge);
    }
    var when = el('span', 'kv-mc-when');
    when.textContent = formatDate(mem.lastAccessed || mem.last_accessed || mem.createdAt || mem.created_at);
    brow.appendChild(when);
    card.appendChild(brow);

    // Meters — fixed order Heat · Importance · Valence · Arousal; amber
    // fill; a zero or absent value shows an empty track — never hidden.
    // Valence is signed: the track carries its magnitude, the Feeling
    // line carries its sign.
    [
      ['Heat', heat],
      ['Importance', importance],
      ['Valence', valence === null ? null : Math.abs(valence)],
      ['Arousal', arousal]
    ].forEach(function(f) {
      var v = (typeof f[1] === 'number' && isFinite(f[1])) ? Math.max(0, Math.min(1, f[1])) : 0;
      var row = el('div', 'kv-mc-meter');
      var k = el('span', 'kv-mc-meter-k');
      k.textContent = f[0];
      var track = el('span', 'kv-mc-meter-track');
      var fill = el('span', 'kv-mc-meter-fill');
      fill.style.width = (v * 100) + '%';
      track.appendChild(fill);
      row.appendChild(k);
      row.appendChild(track);
      card.appendChild(row);
    });

    // Provenance — Emotion→Store, Accessed, Created ledger rows.
    [
      ['Emotion', emotion + ' · Store'],
      [storeType, 'Accessed · ' + formatDate(mem.lastAccessed || mem.last_accessed)],
      ['Created', formatDate(mem.createdAt || mem.created_at)]
    ].forEach(function(pair) {
      var kv = el('div', 'kv-mc-kv');
      var k = el('span');
      k.textContent = pair[0];
      var v = el('b');
      v.textContent = pair[1];
      kv.appendChild(k);
      kv.appendChild(v);
      card.appendChild(kv);
    });

    // Capture tags — footer, only when the record carries any.
    var ftagList = (mem.tags || []).slice(0, 4);
    if (ftagList.length > 0) {
      var ftags = el('div', 'kv-mc-ftags');
      ftagList.forEach(function(t) {
        var s = el('span');
        s.textContent = t;
        ftags.appendChild(s);
      });
      card.appendChild(ftags);
    }

    // Click → the complete inspector (contract unchanged).
    card.addEventListener('click', function() {
      openExpanded(mem, allMems);
    });

    return card;
  }

  // ── Detail inspector — right-docked 380px panel, kit-detail anatomy:
  // header chip+close, title, domain label, CONTENT (serif+citation),
  // PROPERTIES (ledger), CLASSIFIERS (tags), PROXIMAL LINKS (entities/
  // symbols/related memories), SATURATION (HeatBar).
  // source: da-user-addendum.md Ref A "Panneau détail droit (inspector)".
  function sectionLabel(text) {
    var l = el('div', 'kv-detail__label');
    l.textContent = text;
    return l;
  }

  function openExpanded(mem, allMems) {
    closeExpanded();
    expandedCardId = mem.id;

    var heat = mem.heat || 0;
    var storeType = mem.storeType === 'semantic' ? 'semantic' : 'episodic';

    var panel = el('aside', 'kv-expanded open');
    panel.id = 'kv-expanded';

    var closeBtn = el('button', 'kv-expanded-close');
    closeBtn.innerHTML = '&#x2715;';
    closeBtn.setAttribute('aria-label', 'Close');
    closeBtn.addEventListener('click', closeExpanded);
    panel.appendChild(closeBtn);

    // Header chip — dot + type, data-coloured (episodic/semantic).
    var chip = el('span', 'aia-badge ' + (storeType === 'semantic' ? 'aia-badge--accent' : 'aia-badge--ok'));
    chip.appendChild(el('span', 'aia-badge__dot'));
    chip.appendChild(document.createTextNode(storeType.toUpperCase()));
    panel.appendChild(chip);

    // Title (serif) + domain label directly under it.
    var title = extractTitle(mem.content || mem.label || '');
    var titleEl = el('h2', 'kv-expanded-title');
    titleEl.textContent = title;
    panel.appendChild(titleEl);
    var domainLabel = el('div', 'kv-expanded-domain');
    domainLabel.textContent = mem.domain ? shortDomain(mem.domain) : 'uncategorized';
    panel.appendChild(domainLabel);

    // CONTENT — full text, serif, markdown-rendered.
    var contentSec = el('div', 'kv-section');
    contentSec.appendChild(sectionLabel('Content'));
    var contentBlock = el('div', 'kv-expanded-content');
    contentBlock.innerHTML = renderMemoryContent(mem.content || mem.label || '');
    contentSec.appendChild(contentBlock);
    panel.appendChild(contentSec);

    // PROPERTIES — ledger grid (key micro-caps / value mono), matching
    // the kit's kit-props dl exactly (Heat/Type/Domain/Source/Created/Links).
    var links = findRelatedEntities(mem, allMems).length
      + resolveMemorySymbols(mem, 999).length
      + findRelatedMemories(mem, allMems).length;
    var propsSec = el('div', 'kv-section');
    propsSec.appendChild(sectionLabel('Properties'));
    var dl = el('dl', 'kv-props');
    [
      ['Heat', heat.toFixed(3)],
      ['Type', storeType.charAt(0).toUpperCase() + storeType.slice(1)],
      ['Stage', mem.consolidationStage || '--'],
      ['Domain', mem.domain ? shortDomain(mem.domain) : '--'],
      ['Emotion', mem.emotion || '--'],
      ['Source', mem.isGlobal ? 'global' : (mem.isProtected ? 'protected' : 'user')],
      ['Created', formatDate(mem.createdAt || mem.lastAccessed)],
      ['Accessed', formatDate(mem.lastAccessed) + (mem.accessCount ? ' · ×' + mem.accessCount : '')],
      ['Links', String(links)],
    ].forEach(function(pair) {
      var row = el('div', 'kv-prop');
      var dt = el('dt'); dt.textContent = pair[0];
      var dd = el('dd'); dd.textContent = pair[1];
      row.appendChild(dt); row.appendChild(dd);
      dl.appendChild(row);
    });
    propsSec.appendChild(dl);
    panel.appendChild(propsSec);

    // MEASUREMENTS — every continuous instrumented field actually present
    // on the memory, fixed DD-01 order (Heat · Importance · Valence ·
    // Arousal · Plasticity · Stability); a zero value shows an empty
    // track, never hidden. Values verbatim, never rounded flatteringly.
    var meterFields = [
      ['Importance', mem.importance],
      ['Valence', mem.valence],
      ['Arousal', mem.arousal],
      ['Plasticity', mem.plasticity],
      ['Stability', mem.stability],
    ].filter(function(f) { return typeof f[1] === 'number' && isFinite(f[1]); });
    if (meterFields.length > 0) {
      var mSec = el('div', 'kv-section');
      mSec.appendChild(sectionLabel('Measurements'));
      meterFields.forEach(function(f) {
        var v = Math.max(0, Math.min(1, f[1]));
        var wrap = el('div', 'aia-heat');
        var meta = el('div', 'aia-heat__meta');
        var k = el('span'); k.textContent = f[0];
        var val = el('span', 'aia-heat__val'); val.textContent = f[1].toFixed(3);
        meta.appendChild(k); meta.appendChild(val);
        var track = el('div', 'aia-heat__track');
        var fill = el('div', 'aia-heat__fill');
        fill.style.setProperty('--heat-scale', v || 0.001);
        fill.style.width = (v * 100) + '%';
        track.appendChild(fill);
        wrap.appendChild(meta); wrap.appendChild(track);
        mSec.appendChild(wrap);
      });
      panel.appendChild(mSec);
    }

    // CLASSIFIERS — tags as outline chips (mem.emotion folded in as a
    // classifier, matching the card's affective signal).
    var allTags = (mem.tags || []).slice();
    if (mem.emotion && mem.emotion !== 'neutral') allTags.unshift(mem.emotion);
    if (allTags.length > 0) {
      var classSec = el('div', 'kv-section');
      classSec.appendChild(sectionLabel('Classifiers'));
      var tagsRow = el('div', 'kv-tags');
      allTags.forEach(function(t) {
        var tag = el('span', 'aia-badge');
        tag.textContent = t;
        tagsRow.appendChild(tag);
      });
      classSec.appendChild(tagsRow);
      panel.appendChild(classSec);
    }

    // PROXIMAL LINKS — entities, code symbols, and related memories in
    // one dot+label+metric list (honest: only rendered when non-empty).
    var proximal = [];
    findRelatedEntities(mem, allMems).forEach(function(e) {
      proximal.push({ label: e.label || e.id, metric: 'entity', dotVar: '--kind-entity', onClick: function() {
        if (window.JUG && JUG.emit) JUG.emit('graph:selectNode', e);
        if (JUG.state) JUG.state.activeView = 'graph';
      }});
    });
    resolveMemorySymbols(mem, 12).forEach(function(ref) {
      proximal.push({ label: (ref.via === 'file' ? 'in ' : '') + (ref.node.label || ref.node.id), metric: 'symbol', dotVar: '--accent-ink', onClick: function() {
        if (window.JUG && JUG.emit) JUG.emit('graph:selectNode', ref.node);
        if (JUG.state) JUG.state.activeView = 'graph';
      }});
    });
    findRelatedMemories(mem, allMems).forEach(function(r) {
      proximal.push({ label: extractTitle(r.content || r.label || ''), metric: (r.heat || 0).toFixed(2), dotVar: r.storeType === 'semantic' ? '--accent-ink' : '--ok-ink', onClick: function() {
        openExpanded(r, allMems);
      }});
    });
    if (proximal.length > 0) {
      var linkSec = el('div', 'kv-section');
      linkSec.appendChild(sectionLabel('Proximal links'));
      proximal.forEach(function(p) {
        var row = el('div', 'kv-link');
        var dot = el('span', 'kv-link__dot');
        dot.style.background = 'var(' + p.dotVar + ')';
        var name = el('span', 'kv-link__name');
        name.textContent = p.label;
        var w = el('span', 'kv-link__w');
        w.textContent = p.metric;
        row.appendChild(dot); row.appendChild(name); row.appendChild(w);
        row.addEventListener('click', function(e) { e.stopPropagation(); p.onClick(); });
        linkSec.appendChild(row);
      });
      panel.appendChild(linkSec);
    }

    // SATURATION — HeatBar footer, the shipped aia-heat primitive.
    var satSec = el('div', 'kv-section');
    satSec.appendChild(sectionLabel('Saturation'));
    var heatBlock = el('div', 'aia-heat');
    var heatMeta = el('div', 'aia-heat__meta');
    var hl = el('span'); hl.textContent = 'Heat';
    var hv = el('span', 'aia-heat__val'); hv.textContent = heat.toFixed(3);
    heatMeta.appendChild(hl); heatMeta.appendChild(hv);
    var heatTrack = el('div', 'aia-heat__track');
    var heatFill = el('div', 'aia-heat__fill');
    heatFill.style.setProperty('--heat-scale', heat || 0.001);
    heatFill.style.width = (heat * 100) + '%';
    heatTrack.appendChild(heatFill);
    heatBlock.appendChild(heatMeta); heatBlock.appendChild(heatTrack);
    satSec.appendChild(heatBlock);
    panel.appendChild(satSec);

    document.body.appendChild(panel);

    panel._escHandler = function(e) {
      if (e.key === 'Escape') closeExpanded();
    };
    window.addEventListener('keydown', panel._escHandler);
  }

  function closeExpanded() {
    var panel = document.getElementById('kv-expanded');
    if (panel) {
      if (panel._escHandler) window.removeEventListener('keydown', panel._escHandler);
      panel.remove();
    }
    expandedCardId = null;
  }

  // The inspector docks to document.body, so any view can open it — the
  // Board reuses it as its click target (minimal card, complete inspector).
  if (window.JUG) JUG._kvOpenMemory = openExpanded;

  // ── Helpers ──
  function findRelatedEntities(mem, allMems) {
    var data = JUG.state.lastData;
    if (!data || !data.edges) return [];
    var entities = [];
    var nodeMap = {};
    (data.nodes || []).forEach(function(n) { nodeMap[n.id] = n; });

    data.edges.forEach(function(e) {
      var sid = typeof e.source === 'object' ? e.source.id : e.source;
      var tid = typeof e.target === 'object' ? e.target.id : e.target;
      if (sid === mem.id && nodeMap[tid] && nodeMap[tid].type === 'entity') {
        entities.push(nodeMap[tid]);
      } else if (tid === mem.id && nodeMap[sid] && nodeMap[sid].type === 'entity') {
        entities.push(nodeMap[sid]);
      }
    });
    return entities;
  }

  function isToolCapture(m) {
    var c = (m.content || m.label || '').trim();
    if (!c) return false;
    // Tool captures start with "# Tool:" or "Tool:" markers
    if (/^#?\s*Tool:\s*/i.test(c)) return true;
    // Command/Output skeleton with no narrative
    if (/\*\*Command:\*\*/.test(c) && /\*\*Output:\*\*/.test(c)) return true;
    return false;
  }

  function findRelatedMemories(mem, allMems) {
    var memTags = new Set(mem.tags || []);
    if (memTags.size === 0) return [];
    return allMems.filter(function(m) {
      if (m.id === mem.id) return false;
      if (m.domain !== mem.domain) return false;
      if (isToolCapture(m)) return false;
      var overlap = (m.tags || []).filter(function(t) { return memTags.has(t); });
      return overlap.length >= 1;
    }).sort(function(a, b) {
      var oa = (a.tags || []).filter(function(t) { return memTags.has(t); }).length;
      var ob = (b.tags || []).filter(function(t) { return memTags.has(t); }).length;
      return ob - oa;
    }).slice(0, 5);
  }

  function shortDomain(d) {
    if (!d) return 'unknown';
    var parts = d.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts.length > 0 ? parts[parts.length - 1] : d;
  }

  function formatDate(iso) {
    if (!iso) return '--';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var now = new Date();
    var diff = now - d;
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
    return d.toISOString().slice(0, 10);
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function statEl(val, label) {
    var s = el('div', 'kv-stat');
    var v = el('span', 'kv-stat-val');
    v.textContent = val;
    var l = el('span', 'kv-stat-label');
    l.textContent = label;
    s.appendChild(v);
    s.appendChild(l);
    return s;
  }

  function esc(s) {
    return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function renderMemoryContent(raw) {
    if (!raw) return '';
    var text = raw;

    // Unescape literal \n to real newlines (from JSON serialization)
    text = text.replace(/\\n/g, '\n');

    var lines = text.split('\n');
    var html = [];
    var inCode = false;
    var codeLines = [];
    var codeLang = '';

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];

      // Fenced code blocks
      var fence = line.match(/^```(\w*)/);
      if (fence) {
        if (inCode) {
          html.push('<pre class="kv-code"><code>' + codeLines.join('\n') + '</code></pre>');
          codeLines = [];
          inCode = false;
        } else {
          codeLang = fence[1] || '';
          inCode = true;
        }
        continue;
      }
      if (inCode) { codeLines.push(esc(line)); continue; }

      // Detect JSON blocks — accumulate, then parse and pretty-print
      if (/^\s*[\{\[]/.test(line) && !inCode) {
        var jsonLines = [line];
        var depth = 0;
        for (var c = 0; c < line.length; c++) {
          if (line[c] === '{' || line[c] === '[') depth++;
          if (line[c] === '}' || line[c] === ']') depth--;
        }
        while (depth > 0 && i + 1 < lines.length) {
          i++;
          jsonLines.push(lines[i]);
          for (var c2 = 0; c2 < lines[i].length; c2++) {
            if (lines[i][c2] === '{' || lines[i][c2] === '[') depth++;
            if (lines[i][c2] === '}' || lines[i][c2] === ']') depth--;
          }
        }
        var jsonRaw = jsonLines.join('\n');
        var rendered = null;
        try {
          var parsed = JSON.parse(jsonRaw);
          // Tool capture shape: { stdout, stderr, ... } — render plain text, not JSON
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) &&
              ('stdout' in parsed || 'stderr' in parsed)) {
            var parts = [];
            if (parsed.stdout) parts.push(String(parsed.stdout));
            if (parsed.stderr) parts.push('--- stderr ---\n' + String(parsed.stderr));
            rendered = '<pre class="kv-code"><code>' + esc(parts.join('\n')) + '</code></pre>';
          } else {
            rendered = '<pre class="kv-code"><code>' + esc(JSON.stringify(parsed, null, 2)) + '</code></pre>';
          }
        } catch (e) {
          rendered = '<pre class="kv-code"><code>' + esc(jsonRaw) + '</code></pre>';
        }
        html.push(rendered);
        continue;
      }

      // Blank lines
      if (!line.trim()) { continue; }

      // Headings
      var hm = line.match(/^(#{1,4})\s+(.+)/);
      if (hm) {
        var level = hm[1].length;
        html.push('<h' + (level + 1) + '>' + esc(hm[2]) + '</h' + (level + 1) + '>');
        continue;
      }

      // Bold
      var formatted = esc(line);
      formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');

      // List items
      if (/^\s*[-*]\s+/.test(line)) {
        html.push('<li>' + formatted.replace(/^\s*[-*]\s+/, '') + '</li>');
        continue;
      }

      html.push('<p>' + formatted + '</p>');
    }

    // Close unclosed code
    if (inCode && codeLines.length) {
      html.push('<pre class="kv-code"><code>' + codeLines.join('\n') + '</code></pre>');
    }

    return html.join('');
  }

  // Domain tab — DS Chip spec via .kv-domain-pill in knowledge.css;
  // the count renders in mono (.kv-chip-count) per the DS chip spec.
  function domainPill(label, value, isGlobal, count) {
    var pill = el('button', 'kv-domain-pill');
    if (isGlobal) pill.classList.add('kv-pill-global');
    if (value === currentDomain) pill.classList.add('active');
    var t = el('span'); t.textContent = label;
    pill.appendChild(t);
    if (count != null && count !== '') {
      var n = el('span', 'kv-chip-count'); n.textContent = String(count);
      pill.appendChild(n);
    }
    pill.addEventListener('click', function() {
      currentDomain = value;
      _resetAndFetch();
    });
    return pill;
  }

  // ── Initialize ──
  document.addEventListener('DOMContentLoaded', init);
})();
