// Cortex Memory Board — Kanban by consolidation stage with flow header
// Shows consolidated vs dropped memories in a tree-like view.
//
// Data source: /api/memories (paged endpoint). Lazy-loads pages on
// scroll; never holds the full memory set in memory. Sort = recent so
// the freshest activity lands first.
(function() {
  var container = null;
  var visible = false;
  var currentData = null;
  var selectedId = null;
  var _emitting = false;

  // Paged-fetch state. ONE page on open, all subsequent pages gated
  // on actual user scroll.
  var BOARD_PAGE_LIMIT = 100;
  var boardAccum = [];
  var boardSeenIds = Object.create(null);
  var boardCursor = null;
  var boardLoading = false;
  var boardDone = false;
  var boardPagesFetched = 0;
  var boardFetchToken = 0;
  var boardScrolledSinceFetch = false;
  var _boardObserver = null;  // track the active IntersectionObserver so we can disconnect before recreating

  // Server-known facets + active filters (mirror Knowledge tab).
  var boardFacets = null;
  var boardFilterDomain = 'all';     // 'all' | 'global' | <domain name>
  var boardFilterStage = null;       // null = all stages
  var boardFilterEmotion = null;
  var boardFilterMinHeat = null;
  var boardFilterProtected = false;
  var boardSearchQuery = '';
  var boardSort = 'recent';           // 'recent' | 'heat'

  var STAGES = ['labile', 'early_ltp', 'late_ltp', 'consolidated', 'reconsolidating'];
  var STAGE_LABELS = JUG.CONSOLIDATION_LABELS || {};

  // Stage identity inks, as CSS custom-property references (NOT baked hexes):
  // DOM consumers keep re-inking per surface for free — deep variants on
  // paper, bright on the legacy ink instrument.
  // source: DS tokens/colors.css + tokens/surfaces.css --stage-* ;
  //         mapping per ui/shared/palette.js STAGE_TOKENS.
  var STAGE_INK = {
    labile:          'var(--stage-labile)',
    early_ltp:       'var(--stage-early)',
    late_ltp:        'var(--stage-late)',
    consolidated:    'var(--stage-cons)',
    reconsolidating: 'var(--stage-recon)',
  };
  // Unknown stage → neutral chrome ink (honest fallback, no invented colour).
  var STAGE_INK_FALLBACK = 'var(--text-muted)';
  // Measurement meter inks. DD-01: meters carry the amber fill — --warn-ink
  // IS the DS amber (oklch 78% .13 75 = --amber on ink) and re-inks to
  // --warn-deep on paper. Interference reads in the danger ink; heat in the
  // heat family. source: DS README §Data display DD-01; tokens/surfaces.css.
  var METER_INK = 'var(--warn-ink)';
  var METER_INK_HEAT = 'var(--heat-hot)';
  // Emotion facet inks. source: DS tokens --emo-* ; mapping per
  // ui/shared/palette.js EMO_TOKENS (positive→satisfaction, negative→frustration).
  var EMO_INK = {
    urgent:   'var(--emo-urgent)',
    positive: 'var(--emo-satisf)',
    negative: 'var(--emo-frustr)',
  };

  // Stage physics + Advance rules, verbatim from the exhibit.
  // source: DS cards/data-board.html (Spec DD-02 \u00b7 The stage board)
  var STAGE_BIO = {
    labile:          { decay: '2.0\u00d7', vuln: '90%', plast: '100%', advance: 'replays \u2265 1 or imp > 0.3' },
    early_ltp:       { decay: '1.2\u00d7', vuln: '50%', plast: '70%',  advance: 'replays \u2265 3 (z \u2265 1 if schema \u2265 0.5)' },
    late_ltp:        { decay: '0.8\u00d7', vuln: '20%', plast: '30%',  advance: 'stable' },
    consolidated:    { decay: '0.5\u00d7', vuln: '5%',  plast: '10%',  advance: '\u2014' },
    reconsolidating: { decay: '1.5\u00d7', vuln: '80%', plast: '90%',  advance: 're-stabilises' },
  };

  function init() {
    container = document.getElementById('timeline-container');
    if (!container) return;

    JUG.on('state:activeView', function(ev) {
      if (ev.value === 'timeline') show(); else hide();
    });
    JUG.on('state:lastData', function(ev) {
      if (visible && ev.value) { currentData = ev.value; rebuild(); }
    });
    JUG.on('state:activeFilter', rebuildIfVisible);
    JUG.on('state:domainFilter', rebuildIfVisible);
    JUG.on('state:emotionFilter', rebuildIfVisible);
    JUG.on('state:stageFilter', rebuildIfVisible);
    JUG.on('graph:selectNode', function(node) {
      if (_emitting || !visible || !node || node.type !== 'memory') return;
      if (selectedId === node.id) return;
      highlightMemory(node.id);
    });
    JUG.on('graph:deselectNode', function() {
      if (_emitting || !visible) return;
      clearHighlight();
    });
  }

  function show() {
    if (!container) return;
    container.style.display = 'flex';
    visible = true;
    if (!boardFacets) {
      _boardFetchFacets().then(function() { _boardResetAndFetch(); });
    } else {
      _boardResetAndFetch();
    }
    if (JUG.state.selectedId) highlightMemory(JUG.state.selectedId);

    // 60s poll for live updates: refetch the first page (heat may have
    // shifted, new memories landed). Cheap because the page is paged.
    if (!window._boardPollInterval) {
      window._boardPollInterval = setInterval(function() {
        if (visible && document.querySelector('.kb-board')) _boardResetAndFetch();
      }, 60000);
    }
  }

  function _boardFetchFacets() {
    return fetch('/api/memories/facets', { cache: 'no-store' })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) { if (d) boardFacets = d; })
      .catch(function(err) { console.warn('[board] facets fetch failed:', err); });
  }
  function hide() {
    visible = false;
    if (container) container.style.display = 'none';
  }
  function rebuildIfVisible() { if (visible) rebuild(); }

  // ── Lazy-load paged memories from /api/memories ──
  function _boardResetAndFetch() {
    boardAccum = [];
    boardSeenIds = Object.create(null);
    boardCursor = null;
    boardDone = false;
    boardLoading = false;
    boardPagesFetched = 0;
    boardFetchToken++;
    boardScrolledSinceFetch = true;  // allow the very first page
    currentData = { nodes: [], edges: [], links: [] };
    rebuild();
    _boardFetchPage();
  }

  function _boardFetchPage() {
    if (boardDone || boardLoading) return;
    // Gate: every page after the first must be triggered by a genuine
    // user scroll. Without this the sentinel inside the initial
    // viewport keeps firing the IntersectionObserver in a loop.
    if (!boardScrolledSinceFetch) return;
    boardScrolledSinceFetch = false;
    boardLoading = true;
    var token = boardFetchToken;
    var qs = '?limit=' + BOARD_PAGE_LIMIT + '&sort=' + encodeURIComponent(boardSort);
    if (boardCursor) qs += '&cursor=' + encodeURIComponent(boardCursor);
    if (boardFilterDomain === 'global') qs += '&global=1';
    else if (boardFilterDomain !== 'all') qs += '&domain=' + encodeURIComponent(boardFilterDomain);
    if (boardFilterStage) qs += '&stage=' + encodeURIComponent(boardFilterStage);
    if (boardFilterEmotion) qs += '&emotion=' + encodeURIComponent(boardFilterEmotion);
    if (boardFilterMinHeat != null) qs += '&min_heat=' + encodeURIComponent(boardFilterMinHeat);
    if (boardFilterProtected) qs += '&protected=1';
    if (boardSearchQuery) qs += '&search=' + encodeURIComponent(boardSearchQuery);
    fetch('/api/memories' + qs, { cache: 'no-store' })
      .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(data) {
        if (token !== boardFetchToken) return;
        var items = data.items || [];
        for (var i = 0; i < items.length; i++) {
          var m = items[i];
          if (boardSeenIds[m.id]) continue;
          boardSeenIds[m.id] = true;
          m._ts = parseTs(m.createdAt) || 0;
          boardAccum.push(m);
        }
        boardCursor = data.next_cursor || null;
        boardDone = !boardCursor;
        boardPagesFetched++;
        boardLoading = false;
        currentData = { nodes: boardAccum, edges: [], links: [] };
        rebuild();
        // No auto-prefetch — every subsequent page is gated on user
        // scroll via boardScrolledSinceFetch. The first page is enough
        // to populate the columns; user scrolls to load more.
      })
      .catch(function(err) {
        console.warn('[board] /api/memories fetch failed:', err);
        boardLoading = false;
      });
  }

  function extractMemories(data) {
    var nodes = (data && data.nodes) ? data.nodes : [];
    if (JUG._applyExtraFilters) nodes = JUG._applyExtraFilters(nodes);
    return nodes;
  }

  // DS Chip (paper doctrine): chrome-neutral pill; pressed = accent-soft
  // tint; the count renders as a mono figure; stage/emotion/heat identity
  // is a small data dot, never a coloured chip. Styling lives in
  // timeline.css (.kb-chip*) — no baked colours here.
  function _boardChip(label, count, active, onClick, dotInk) {
    var b = el('button', 'kb-chip' + (active ? ' kb-chip--active' : ''));
    if (dotInk) {
      var dot = el('span', 'kb-chip__dot');
      dot.style.setProperty('--chip-dot', dotInk); // data ink token reference
      b.appendChild(dot);
    }
    b.appendChild(document.createTextNode(label));
    if (count !== '' && count != null) {
      var c = el('span', 'kb-chip__count');
      c.textContent = String(count);
      b.appendChild(c);
      // An empty facet filters to nothing — disable unless it is the
      // active chip (which must stay clickable to be unselected).
      if (count === 0 && !active) b.disabled = true;
    }
    b.addEventListener('click', onClick);
    return b;
  }

  // All chrome styling lives in timeline.css against the DS semantic
  // aliases — this builder only assembles structure (no inline colours).
  function _boardFilterBar() {
    var wrap = el('div', 'kb-filter-wrap');

    // Row 1: search + sort + clear-all.
    var row1 = el('div', 'kb-filter-row');
    var searchInput = el('input', 'kb-search');
    searchInput.type = 'text'; searchInput.placeholder = 'Search…';
    searchInput.value = boardSearchQuery;
    var sDeb = null;
    searchInput.addEventListener('input', function() {
      clearTimeout(sDeb);
      sDeb = setTimeout(function() {
        boardSearchQuery = searchInput.value;
        _boardResetAndFetch();
      }, 250);
    });
    row1.appendChild(searchInput);

    [['recent', 'Recent'], ['heat', 'Heat']].forEach(function(s) {
      row1.appendChild(_boardChip(s[1], null, boardSort === s[0],
        function() { boardSort = s[0]; _boardResetAndFetch(); }));
    });

    var anyActive = boardFilterDomain !== 'all' || boardFilterStage || boardFilterEmotion
      || boardFilterMinHeat != null || boardFilterProtected || boardSearchQuery;
    if (anyActive) {
      var clr = el('button', 'kb-clear'); clr.textContent = 'Clear all';
      clr.addEventListener('click', function() {
        boardFilterDomain = 'all'; boardFilterStage = null; boardFilterEmotion = null;
        boardFilterMinHeat = null; boardFilterProtected = false; boardSearchQuery = '';
        _boardResetAndFetch();
      });
      row1.appendChild(clr);
    }
    wrap.appendChild(row1);

    // Row 2: domain chips (server-known facets).
    var row2 = el('div', 'kb-filter-row');
    var domLabel = el('span', 'kb-filter-label'); domLabel.textContent = 'Domain';
    row2.appendChild(domLabel);
    row2.appendChild(_boardChip('All', boardFacets ? boardFacets.total : null,
      boardFilterDomain === 'all',
      function() { boardFilterDomain = 'all'; _boardResetAndFetch(); }));
    if (boardFacets && boardFacets.global > 0) {
      row2.appendChild(_boardChip('Global', boardFacets.global,
        boardFilterDomain === 'global',
        function() { boardFilterDomain = 'global'; _boardResetAndFetch(); }));
    }
    var domains = boardFacets && boardFacets.domains ? boardFacets.domains : [];
    domains.slice(0, 30).forEach(function(d) {
      row2.appendChild(_boardChip(_boardShortDomain(d.name), d.count,
        boardFilterDomain === d.name,
        function() { boardFilterDomain = d.name; _boardResetAndFetch(); }));
    });
    wrap.appendChild(row2);

    // Row 3: stage + emotion + heat + protected.
    var row3 = el('div', 'kb-filter-row');
    var fLabel = el('span', 'kb-filter-label'); fLabel.textContent = 'Filter';
    row3.appendChild(fLabel);
    var stageOpts = [
      { v: null,              t: 'Any stage' },
      { v: 'labile',          t: 'New' },
      { v: 'early_ltp',       t: 'Growing' },
      { v: 'late_ltp',        t: 'Strong' },
      { v: 'consolidated',    t: 'Stable' },
      { v: 'reconsolidating', t: 'Updating' },
    ];
    stageOpts.forEach(function(opt) {
      var c = boardFacets && opt.v ? boardFacets.stages[opt.v]
            : (opt.v == null && boardFacets ? boardFacets.total : '');
      row3.appendChild(_boardChip(opt.t, c === '' ? null : c,
        boardFilterStage === opt.v,
        function() { boardFilterStage = opt.v; _boardResetAndFetch(); },
        opt.v ? STAGE_INK[opt.v] : null));
    });
    var sep = el('span', 'kb-filter-dot'); sep.textContent = '·';
    row3.appendChild(sep);
    var emoOpts = [
      { v: null,       t: 'Any feel' },
      { v: 'urgent',   t: 'Urgent' },
      { v: 'positive', t: 'Positive' },
      { v: 'negative', t: 'Negative' },
      { v: 'neutral',  t: 'Neutral' },
    ];
    emoOpts.forEach(function(opt) {
      var c = boardFacets && opt.v ? boardFacets.emotions[opt.v] : '';
      row3.appendChild(_boardChip(opt.t, c === '' ? null : c,
        boardFilterEmotion === opt.v,
        function() { boardFilterEmotion = opt.v; _boardResetAndFetch(); },
        opt.v ? EMO_INK[opt.v] : null));
    });
    var sep2 = el('span', 'kb-filter-dot'); sep2.textContent = '·';
    row3.appendChild(sep2);
    row3.appendChild(_boardChip('Hot', boardFacets ? boardFacets.hot : null,
      boardFilterMinHeat != null,
      function() { boardFilterMinHeat = boardFilterMinHeat != null ? null : 0.5; _boardResetAndFetch(); },
      METER_INK_HEAT));
    row3.appendChild(_boardChip('Protected', boardFacets ? boardFacets.protected : null,
      boardFilterProtected,
      function() { boardFilterProtected = !boardFilterProtected; _boardResetAndFetch(); },
      METER_INK));
    wrap.appendChild(row3);

    return wrap;
  }

  function _boardShortDomain(d) {
    if (!d) return 'unknown';
    var parts = d.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts.length > 0 ? parts[parts.length - 1] : d;
  }

  function parseTs(val) {
    if (!val) return null;
    var d = new Date(val);
    return isNaN(d.getTime()) ? null : d.getTime();
  }

  function rebuild() {
    var memories = extractMemories(currentData);
    container.innerHTML = '';

    // Group by stage
    var groups = {};
    STAGES.forEach(function(s) { groups[s] = []; });
    memories.forEach(function(m) {
      // Accept all shapes the backend has emitted over time:
      //   m.stage                (current workflow_graph.v1 field)
      //   m.consolidationStage   (legacy camelCase)
      //   m.consolidation_stage  (snake_case from memory record)
      var s = m.stage || m.consolidationStage || m.consolidation_stage || 'labile';
      if (!groups[s]) s = 'labile';
      groups[s].push(m);
    });
    STAGES.forEach(function(s) {
      groups[s].sort(function(a, b) {
        return (b.heat || 0) - (a.heat || 0) || (b._ts || 0) - (a._ts || 0);
      });
    });

    // ── Flow header — DD-02 anatomy: count, stage name, the three physics
    // ledger rows and the factual Advance rule. Nothing else: no share bar,
    // no live meters, no arrows, no badges (directive user 2026-07-04).
    // source: DS cards/data-board.html (Spec DD-02 · The stage board)
    var flowStrip = el('div', 'kb-flow-strip');
    STAGES.forEach(function(stage) {
      // G10: the count is the server-side truth for the whole stage
      // (same source as the facet chip), never the length of the
      // currently-loaded page. Falls back to the page-local count only
      // while facets have not yet arrived (boardFacets == null).
      var count = (boardFacets && boardFacets.stages && boardFacets.stages[stage] != null)
        ? boardFacets.stages[stage]
        : groups[stage].length;
      var bio = STAGE_BIO[stage];

      var card = el('div', 'kb-flow-node');
      card.innerHTML =
        '<div class="kb-flow-count">' + count + '</div>' +
        '<div class="kb-flow-label">' + (STAGE_LABELS[stage] || stage) + '</div>';

      var bioEl = el('div', 'kb-flow-bio-section');
      bioEl.innerHTML =
        '<div class="kb-flow-bio-row"><span>Decay</span><span>' + bio.decay + '</span></div>' +
        '<div class="kb-flow-bio-row"><span>Vulnerability</span><span>' + bio.vuln + '</span></div>' +
        '<div class="kb-flow-bio-row"><span>Plasticity</span><span>' + bio.plast + '</span></div>';
      card.appendChild(bioEl);

      // Advance condition: the factual rule, accent-ink label (DD-02).
      var advEl = el('div', 'kb-flow-advance');
      advEl.innerHTML = '<span class="kb-flow-advance-label">Advance:</span> ' + bio.advance;
      card.appendChild(advEl);

      flowStrip.appendChild(card);
    });
    container.appendChild(flowStrip);

    // Filter chips — same shape as Knowledge tab.
    container.appendChild(_boardFilterBar());

    // ── Board columns ──
    var board = el('div', 'kb-board');

    STAGES.forEach(function(stage) {
      var sc = STAGE_INK[stage] || STAGE_INK_FALLBACK;
      var mems = groups[stage];

      var col = el('div', 'kb-col');
      // --sc: stage data ink — CSS renders it as the header's identity dot.
      col.style.setProperty('--sc', sc);

      // Header
      var header = el('div', 'kb-col-header');
      var title = el('span', 'kb-col-title');
      title.textContent = (STAGE_LABELS[stage] || stage).toUpperCase();
      header.appendChild(title);
      // G10: server-truth stage total (same source as the facet chip),
      // not the length of the currently-loaded page.
      var colCount = (boardFacets && boardFacets.stages && boardFacets.stages[stage] != null)
        ? boardFacets.stages[stage]
        : mems.length;
      var count = el('span', 'kb-col-count');
      count.textContent = colCount;
      header.appendChild(count);
      col.appendChild(header);

      // Cards
      var cards = el('div', 'kb-col-cards');

      mems.forEach(function(mem) {
        var card = buildCard(mem, sc);
        cards.appendChild(card);
      });

      if (mems.length === 0) {
        var empty = el('div', 'kb-empty');
        empty.textContent = 'No memories';
        cards.appendChild(empty);
      }

      col.appendChild(cards);
      board.appendChild(col);
    });

    container.appendChild(board);

    // Sentinel + manual "Load more" button — same triple-trigger
    // pattern as Knowledge: IntersectionObserver, scroll/wheel
    // backstop, and an explicit click button. Without this the user
    // sees the first ~500 memories and the Board feels capped.
    var sentinel = el('div', 'kb-load-sentinel');
    sentinel.id = 'kb-load-sentinel';
    var sText = el('span'); sText.id = 'kb-load-text';
    sText.textContent = boardDone
      ? '— end of board — ' + boardAccum.length + ' memories loaded'
      : (boardLoading
          ? 'Loading more memories… (' + boardAccum.length + ' so far)'
          : 'Scroll for more · ' + boardAccum.length + ' loaded');
    sentinel.appendChild(sText);
    if (!boardDone) {
      // DS secondary button — styled in timeline.css (.kb-load-more).
      var sBtn = el('button', 'kb-load-more');
      sBtn.id = 'kb-load-more';
      sBtn.textContent = 'Load more';
      sBtn.addEventListener('click', function(){
        boardScrolledSinceFetch = true;  // explicit user intent
        _boardFetchPage();
      });
      sentinel.appendChild(sBtn);
    }
    container.appendChild(sentinel);
    _boardAttachIntersectionObserver(sentinel);
    _boardAttachScrollBackstop(sentinel);
  }

  function _boardAttachIntersectionObserver(sentinel) {
    if (!('IntersectionObserver' in window)) return;
    if (_boardObserver) { _boardObserver.disconnect(); _boardObserver = null; }
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) { if (e.isIntersecting) _boardFetchPage(); });
    }, { root: null, rootMargin: '400px' });
    io.observe(sentinel);
    _boardObserver = io;
  }

  function _boardAttachScrollBackstop(sentinel) {
    if (window._boardScrollBackstopAttached) return;
    window._boardScrollBackstopAttached = true;
    var maybeFetch = function() {
      if (!visible || boardLoading || boardDone) return;
      // Mark that the user has actually scrolled — only NOW does the
      // gate in _boardFetchPage allow another fetch.
      boardScrolledSinceFetch = true;
      var s = document.getElementById('kb-load-sentinel');
      if (!s) return;
      var rect = s.getBoundingClientRect();
      var vh = window.innerHeight || document.documentElement.clientHeight;
      if (rect.top < vh + 400) _boardFetchPage();
    };
    container.addEventListener('scroll', maybeFetch, { passive: true });
    window.addEventListener('scroll', maybeFetch, { passive: true });
    container.addEventListener('wheel', maybeFetch, { passive: true });
  }

  function buildCard(mem, stageColor) {
    var card = el('div', 'kb-card');
    card.dataset.memId = mem.id;
    // Keyboard operability (WCAG 2.1.1): the card carries a click handler
    // (select + open inspector) with no native interactive semantics —
    // make it a focusable, announced button-equivalent.
    card.tabIndex = 0;
    card.setAttribute('role', 'button');

    var heat = Math.max(0, Math.min(1, mem.heat || 0));
    var stability = mem.stability;
    var interference = mem.interferenceScore || 0;
    var plasticity = Math.max(0, Math.min(1, mem.plasticity || 0));
    var replayCount = mem.accessCount || 0;

    // No heat tint / opacity fade on the sheet — DD-01 cards are plain
    // cream; heat lives in the HeatBar footer (stageColor stays used by
    // the column header dot only).

    // Integrity class (null-safe stability check) — rendered by CSS as a
    // small status dot in the sheet's corner (the DS forbids
    // coloured-left-border cards).
    var integrityClass;
    if (stability == null || stability === undefined) {
      integrityClass = 'kb-card--neutral';
    } else if (stability > 0.7 && interference < 0.3) {
      integrityClass = 'kb-card--healthy';
    } else if (stability > 0.3) {
      integrityClass = 'kb-card--consolidating';
    } else if (interference > 0.5) {
      integrityClass = 'kb-card--at-risk';
    } else {
      integrityClass = 'kb-card--fading';
    }
    card.classList.add(integrityClass);

    // Fading indicator for very low heat
    if (heat < 0.1) {
      card.classList.add('kb-card-fading');
    }

    var body = el('div', 'kb-card-body');

    // Content — minimal card (user directive 2026-07-04): one clamped
    // passage; commands/code speak mono (T-04). Full detail lives in the
    // inspector on click.
    var rawLabel = (mem.label || mem.content || '').replace(/\*\*/g, '').trim();
    var labelIsCode = /(^|\n)\s*(Tool:|Command:|\$ |#!\/|cd |grep |python3? |node |npm |git )/.test(rawLabel)
      || rawLabel.indexOf('&&') !== -1 || rawLabel.indexOf('```') !== -1;
    var label = el('div', 'kb-card-label' + (labelIsCode ? ' kb-card-label--code' : ''));
    var labelText = rawLabel.replace(/\s+/g, ' ').slice(0, 100);
    label.textContent = labelText;
    body.appendChild(label);
    // Accessible name (WCAG 4.1.2): announce the card's content in place
    // of the visual label since role="button" has no text-node fallback.
    card.setAttribute('aria-label', (labelText || 'Memory') + (mem.domain ? ', ' + mem.domain : ''));

    // Meta row: domain pill + store type badge + relative age
    var meta = el('div', 'kb-card-meta');

    var domain = el('span', 'kb-card-domain');
    domain.textContent = (mem.domain || '').slice(0, 18);
    meta.appendChild(domain);

    var storeType = mem.storeType || (mem.consolidationStage === 'consolidated' ? 'semantic' : 'episodic');
    var storeBadge = el('span', 'kb-card__store-badge');
    if (storeType === 'semantic') {
      storeBadge.classList.add('kb-card__store-badge--semantic');
      storeBadge.textContent = 'SEM';
    } else {
      storeBadge.classList.add('kb-card__store-badge--episodic');
      storeBadge.textContent = 'EP';
    }
    meta.appendChild(storeBadge);

    if (mem._ts) {
      var time = el('span', 'kb-card-time');
      time.textContent = formatTime(mem._ts);
      meta.appendChild(time);
    }
    body.appendChild(meta);

    // Minimal card stops here — meaning, meters, tags and symbol chips all
    // live in the full inspector (JUG._kvOpenMemory) opened on click.

    // Protected badge \u2014 micro-caps word, never a pictograph (DS: no emoji).
    if (mem.isProtected) {
      var shield = el('span', 'kb-badge kb-badge-protected');
      shield.textContent = 'PROTECTED';
      body.appendChild(shield);
    }

    // Heat footer \u2014 the one meter the minimal card keeps (DD-01: a zero
    // value shows an empty track, never hidden). Value verbatim.
    var heatFoot = el('div', 'aia-heat kb-card__heat');
    var heatMeta = el('div', 'aia-heat__meta');
    var hk = el('span'); hk.textContent = 'Heat';
    var hv = el('span', 'aia-heat__val'); hv.textContent = heat.toFixed(3);
    heatMeta.appendChild(hk); heatMeta.appendChild(hv);
    var heatTrack = el('div', 'aia-heat__track');
    var heatFill = el('div', 'aia-heat__fill');
    heatFill.style.setProperty('--heat-scale', heat || 0.001);
    heatFill.style.width = (heat * 100) + '%';
    heatTrack.appendChild(heatFill);
    heatFoot.appendChild(heatMeta); heatFoot.appendChild(heatTrack);
    body.appendChild(heatFoot);
    card.appendChild(body);

    // Tooltip on hover
    card.addEventListener('mouseenter', function() { JUG._tooltip.show(mem); });
    card.addEventListener('mouseleave', function() { JUG._tooltip.hide(); });

    // Activate: highlight + open the FULL inspector (same panel as the
    // Knowledge view — minimal card, complete detail on click). Shared by
    // the click and keyboard (Enter/Space) paths so both stay in sync.
    function activate() {
      _emitting = true;
      if (selectedId === mem.id) {
        clearHighlight();
        JUG.emit('graph:deselectNode');
      } else {
        highlightMemory(mem.id);
        if (window.JUG && typeof JUG._kvOpenMemory === 'function') {
          JUG._kvOpenMemory(mem, []);
        } else {
          JUG.emit('graph:selectNode', mem);
        }
      }
      _emitting = false;
    }
    card.addEventListener('click', function(e) { e.stopPropagation(); activate(); });
    card.addEventListener('keydown', function(e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();  // Space must not scroll the column
      e.stopPropagation();
      activate();
    });

    return card;
  }

  function highlightMemory(id) {
    selectedId = id;
    if (!container) return;
    var cards = container.querySelectorAll('.kb-card');
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].dataset.memId === id) {
        cards[i].classList.add('kb-card-selected');
        cards[i].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } else {
        cards[i].classList.add('kb-card-dimmed');
      }
    }
  }

  function clearHighlight() {
    selectedId = null;
    if (!container) return;
    var cards = container.querySelectorAll('.kb-card');
    for (var i = 0; i < cards.length; i++) {
      cards[i].classList.remove('kb-card-selected', 'kb-card-dimmed');
    }
  }

  function formatTime(ts) {
    var d = new Date(ts);
    var now = new Date();
    var diff = now - d;
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    requestAnimationFrame(init);
  }

  JUG.timelineView = { show: show, hide: hide };
})();
