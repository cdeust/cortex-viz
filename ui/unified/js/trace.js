// Cortex — Execution-Trace view (domain-split, collapsible, live).
//
// Navigation: domain -expand> session -expand> chain-of-work -expand> file.
// Each level is fetched live on expand (no snapshot):
//   /api/trace/domains              -> collapsed domain hubs
//   /api/trace/sessions?domain=<id> -> sessions + has_session edges
//   /api/trace/chain?session=<sid>  -> ordered prompt/action/file chain
//   /api/trace/file?path=<p>        -> file drill (rendered into detail panel)
//
// Emits workflow_graph.v1-shaped nodes/edges so the existing D3 force
// renderer (workflow_graph.js) + detail panels apply unchanged.
(function () {
  'use strict';

  // Per-tool action colors (override the generic 'action' KIND_COLOR).
  // G9 (design gate): tool colour comes from the --tool-* family tokens
  // (ui/shared/tokens/surfaces.css), resolved LIVE against the current
  // surface — never a static hex table. The previous static hex table baked
  // paper-only, low-contrast values straight into n.color (Edit 1.44:1,
  // Write 1.66:1, Read 1.85:1, Web 2.35:1, Bash 2.39:1 on paper), and because
  // nodeColor() prefers n.color over KIND_COLOR, the client tokens could
  // never override them. TOOL_COLOR now maps each tool to a token NAME;
  // _colorize (below) still bakes the resolved value into n.color at fetch
  // time — that contract is unchanged, only the colour SOURCE moved from a
  // literal to a live token read.
  var TOOL_COLOR_TOKEN = {
    Read: '--tool-read', NotebookRead: '--tool-read',
    Grep: '--tool-search', Glob: '--tool-search',
    Edit: '--tool-edit', MultiEdit: '--tool-edit', NotebookEdit: '--tool-edit',
    Write: '--tool-write', Bash: '--tool-exec',
    Task: '--tool-agent', Agent: '--tool-agent',
    WebFetch: '--tool-web', WebSearch: '--tool-web',
  };
  function _resolveToolColor(tool) {
    var token = TOOL_COLOR_TOKEN[tool];
    if (!token) return null;
    if (window.CortexPalette) return window.CortexPalette.hex(token);
    // Defensive fallback (palette.js failed to load) — same getComputedStyle
    // read every other renderer in this app uses when CortexPalette is absent.
    var v = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
    return v || null;
  }

  var _expanded = Object.create(null);
  var _mounted = false;
  var _booted = false;

  // ── Live tail ──────────────────────────────────────────────────────────
  // Live host activity arrives through /api/activity/stream and is projected
  // only into a session the user has expanded in this view. JSONL polling is
  // retained as a fallback for transcript-backed sessions: build_chain's
  // ``since`` cursor ships only the new tail — O(new events), not the whole
  // chain. Both paths end at appendGraphDelta, the graph's id-deduping merge.
  var _liveSince = Object.create(null);   // session node id -> next_since cursor
  var _liveDomains = Object.create(null); // domain id -> known session count
  var _liveTimer = null;
  var _liveOn = true;
  var LIVE_MS = 4000;

  // ── Historical reveal ─────────────────────────────────────────────────
  // A clicked session can carry hundreds of already-finished events. Reveal
  // one causal/hierarchy unit per animation frame instead of asking the
  // topology-aware layout to place the entire history in one blocking burst.
  // The 25 ms ceiling is the same budget used by graph_stream_loader.js; the
  // causal boundary is an additional visual yield, so a fast machine cannot
  // collapse the whole history back into one frame.
  var REVEAL_FRAME_BUDGET_MS = 25;
  var _revealJobs = Object.create(null); // clicked node id -> scheduled reveal
  var _traceEpoch = 0;

  function _container() { return document.getElementById('graph-container'); }

  function _scheduleFrame(fn) {
    if (typeof window.requestAnimationFrame === 'function') {
      return { id: window.requestAnimationFrame(fn), raf: true };
    }
    return { id: setTimeout(fn, 16), raf: false };
  }

  function _cancelFrame(frame) {
    if (!frame) return;
    if (frame.raf && typeof window.cancelAnimationFrame === 'function') {
      window.cancelAnimationFrame(frame.id);
    } else {
      clearTimeout(frame.id);
    }
  }

  function _cancelReveal(ownerId, retryable) {
    var job = _revealJobs[ownerId];
    if (!job) return;
    _cancelFrame(job.frame);
    delete _revealJobs[ownerId];
    if (retryable) {
      _expanded[ownerId] = false;
      delete _liveSince[ownerId];
      delete _liveDomains[ownerId];
    }
  }

  function _cancelAllReveals(retryable) {
    Object.keys(_revealJobs).forEach(function (ownerId) {
      _cancelReveal(ownerId, retryable);
    });
  }

  function _clearGraph() {
    _cancelAllReveals(false);
    _stopLiveTimer();
    _traceEpoch++;
    // Reset dedup sets BEFORE seeding the renderer so the first
    // appendGraphDelta is treated as fresh. setGraphData normalizes to
    // {nodes, links}; pass exactly that shape (force-graph's onChange
    // calls .filter on links, so it must be an array).
    JUG._existingIdSet = new Set();
    JUG._existingEdgeSet = new Set();
    _expanded = Object.create(null);
    _liveSince = Object.create(null);
    _liveDomains = Object.create(null);
    // Seed lastData with the TRACE schema so the workflow-graph bridge
    // hands trace data back to the force-graph renderer (tree-branching)
    // instead of overlaying its radial-galaxy canvas. appendGraphDelta
    // only seeds meta when lastData is null, so set it here first.
    JUG.state.lastData = {
      nodes: [], edges: [], links: [],
      meta: { schema: 'trace.v1', source: 'trace' },
    };
    if (typeof JUG.setGraphData === 'function') {
      // renderer.setGraphData(nodes, links) — two ARRAY args, not an object.
      JUG.setGraphData([], []);
    }
  }

  function _colorize(nodes) {
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if ((n.kind === 'action' || n.type === 'action') && n.tool) {
        var c = _resolveToolColor(n.tool);
        if (c) n.color = c;
      }
    }
    return nodes;
  }

  function _fetchJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function _apply(payload) {
    if (!payload) return;
    var nodes = _colorize(payload.nodes || []);
    JUG.appendGraphDelta(nodes, payload.edges || []);
  }

  function _historicalBatches(ownerId, payload) {
    var nodes = (payload && payload.nodes) || [];
    var edges = (payload && payload.edges) || [];
    var nodeById = Object.create(null);
    var nodeIndex = Object.create(null);
    nodes.forEach(function (n, i) {
      if (!n || !n.id) return;
      nodeById[n.id] = n;
      nodeIndex[n.id] = i;
    });

    // L2 chain nodes carry the authoritative causal sequence. L1 session
    // children do not; for those, preserve the server's newest-first node
    // order and use the explicit owner -> child relation as the boundary.
    var primary = nodes.filter(function (n) {
      return n && n.id && typeof n.seq === 'number' && isFinite(n.seq);
    }).sort(function (a, b) {
      return a.seq - b.seq || nodeIndex[a.id] - nodeIndex[b.id];
    });
    if (!primary.length) {
      var direct = Object.create(null);
      edges.forEach(function (e) {
        if (_edgeId(e && e.source) === ownerId) direct[_edgeId(e.target)] = true;
      });
      primary = nodes.filter(function (n) { return n && direct[n.id]; });
    }

    var primaryIds = Object.create(null);
    primary.forEach(function (n) { primaryIds[n.id] = true; });
    var usedNodes = Object.create(null);
    var usedEdges = Object.create(null);
    var batches = [];

    primary.forEach(function (root) {
      var batchNodes = [];
      var batchEdges = [];
      if (!usedNodes[root.id]) {
        usedNodes[root.id] = true;
        batchNodes.push(root);
      }
      edges.forEach(function (e, edgeIndex) {
        if (usedEdges[edgeIndex]) return;
        var source = _edgeId(e && e.source);
        var target = _edgeId(e && e.target);
        // Delay spine/branch edges until their target causal step appears;
        // reveal file/tool targets with the action that observed them.
        var belongs = target === root.id
          || (source === root.id && !primaryIds[target]);
        if (!belongs) return;
        usedEdges[edgeIndex] = true;
        batchEdges.push(e);
        var targetNode = nodeById[target];
        if (targetNode && !usedNodes[target]) {
          usedNodes[target] = true;
          batchNodes.push(targetNode);
        }
      });
      batches.push({ nodes: batchNodes, edges: batchEdges, boundary: true });
    });

    var tailNodes = nodes.filter(function (n) { return n && !usedNodes[n.id]; });
    var tailEdges = edges.filter(function (_e, i) { return !usedEdges[i]; });
    if (tailNodes.length || tailEdges.length) {
      batches.push({ nodes: tailNodes, edges: tailEdges, boundary: false });
    }
    return batches;
  }

  function _startHistoricalReveal(ownerId, payload, onDone) {
    _cancelReveal(ownerId, false);
    var batches = _historicalBatches(ownerId, payload);
    if (!batches.length) {
      if (onDone) onDone();
      return;
    }
    var job = { batches: batches, index: 0, frame: null, epoch: _traceEpoch };
    _revealJobs[ownerId] = job;

    function pump() {
      job.frame = null;
      if (_revealJobs[ownerId] !== job || job.epoch !== _traceEpoch || !_mounted) return;
      var started = (window.performance && typeof window.performance.now === 'function')
        ? window.performance.now() : Date.now();
      var crossedBoundary = false;
      while (job.index < job.batches.length) {
        var batch = job.batches[job.index];
        if (crossedBoundary && batch.boundary) break;
        job.index++;
        _apply(batch);
        crossedBoundary = crossedBoundary || batch.boundary;
        var now = (window.performance && typeof window.performance.now === 'function')
          ? window.performance.now() : Date.now();
        if (now - started >= REVEAL_FRAME_BUDGET_MS) break;
      }
      if (job.index < job.batches.length) {
        job.frame = _scheduleFrame(pump);
        return;
      }
      delete _revealJobs[ownerId];
      if (onDone) onDone();
    }

    job.frame = _scheduleFrame(pump);
  }

  function _setStatus(text) {
    var el = document.getElementById('status-text');
    if (el) el.textContent = text;
  }

  function _boot() {
    if (_booted) return;
    _booted = true;
    _clearGraph();
    _setStatus('Loading domains...');
    _fetchJSON('/api/trace/domains')
      .then(function (d) {
        _apply(d);
        _setStatus((d.nodes || []).length + ' domains - click to expand');
      })
      .catch(function (err) {
        _setStatus('Trace load failed: ' + err.message);
        _booted = false;
      });
  }

  function _expand(node) {
    if (!node || !node.id) return;
    var kind = node.kind || node.type;
    if (_expanded[node.id] && kind !== 'file') return;

    if (kind === 'domain') {
      _expanded[node.id] = true;
      var domainEpoch = _traceEpoch;
      _setStatus('Loading sessions...');
      _fetchJSON('/api/trace/sessions?domain=' + encodeURIComponent(node.id))
        .then(function (d) {
          if (domainEpoch !== _traceEpoch || !_expanded[node.id]) return;
          if (!_mounted) { _expanded[node.id] = false; return; }
          // Live: remember how many sessions this domain has, so the
          // poller can surface NEW sessions started after expand.
          _liveDomains[node.id] = (d.nodes || []).length;
          _ensureLiveTimer();
          _setStatus('Revealing ' + (d.nodes || []).length + ' sessions...');
          _startHistoricalReveal(node.id, d, function () {
            _setStatus((d.nodes || []).length + ' sessions');
          });
        })
        .catch(function (e) {
          if (domainEpoch !== _traceEpoch) return;
          _expanded[node.id] = false;
          _setStatus('Sessions failed: ' + e.message);
        });
    } else if (kind === 'session') {
      // Chain renders ON the canvas as the session's grouped sub-cluster
      // (computeSlots gives each session an exclusive sector). Detail goes
      // in the single detail panel (detail_panel.js). No node-list panel.
      _expanded[node.id] = true;
      var sid = node.session_id || String(node.id).replace(/^session:/, '');
      var sessionEpoch = _traceEpoch;
      _setStatus('Loading chain...');
      _fetchJSON('/api/trace/chain?session=' + encodeURIComponent(sid))
        .then(function (d) {
          if (sessionEpoch !== _traceEpoch || !_expanded[node.id]) return;
          if (!_mounted) { _expanded[node.id] = false; return; }
          var m = d.meta || {};
          // Arm the transcript-tail fallback from the exact cursor returned
          // by the initial expansion. Previously this map was never seeded,
          // so _liveTick had no expanded sessions to poll.
          if (typeof d.next_since === 'number') _liveSince[node.id] = d.next_since;
          _ensureLiveTimer();
          _setStatus('Revealing chain · ' + (m.event_count || 0) + ' steps...');
          _startHistoricalReveal(node.id, d, function () {
            _setStatus('chain - ' + (m.event_count || 0) + ' steps');
          });
        })
        .catch(function (e) {
          if (sessionEpoch !== _traceEpoch) return;
          _expanded[node.id] = false;
          _setStatus('Chain failed: ' + e.message);
        });
    }
    // file click: the impact diagram + detail are handled by the
    // detail-panel "Impact" section (detail_panel.js), not here.
  }

  // ── Impact / dependency DIAGRAM (flow panel) ─────────────────────────
  // A developer's blast-radius view for a file: what it imports/calls
  // (downstream) and what calls/imports it (upstream), grouped, with the
  // file in the center. Built from /api/trace/impact (Cortex code-graph).
  // Exposed as window.TraceView.showImpact(path) so the detail-panel
  // "Impact" section can open it.
  var EDGE_KIND_LABEL = {
    imports: 'imports', calls: 'calls', member_of: 'member', uses: 'uses',
  };

  function _renderImpact(path) {
    var panel = document.getElementById('flow-panel');
    var content = document.getElementById('flow-content');
    var title = document.getElementById('flow-title');
    if (!panel || !content) return;
    if (title) title.textContent = 'Impact · ' + _short(path.split('/').pop(), 32);
    content.innerHTML = '<div class="impact-loading">analyzing dependencies…</div>';
    panel.classList.add('open');
    var detail = document.getElementById('detail-panel');
    panel.classList.toggle('with-detail', !!(detail && detail.classList.contains('open')));

    _fetchJSON('/api/trace/impact?path=' + encodeURIComponent(path))
      .then(function (d) {
        if (!d || !d.available) {
          content.innerHTML = '<div class="impact-loading">No dependency data · '
            + _esc((d && (d.reason || d.error)) || 'not indexed') + '</div>';
          return;
        }
        content.innerHTML = _impactHtml(d);
        // click a box → select that file/symbol on the canvas if present
        content.querySelectorAll('.impact-box[data-file]').forEach(function (el) {
          el.addEventListener('click', function () {
            var fp = el.getAttribute('data-file');
            var nid = 'file:' + fp;
            var nd = (JUG.state.lastData.nodes || []).filter(function (x) { return x.id === nid; })[0];
            if (nd && JUG.emit) JUG.emit('graph:selectNode', nd);
          });
        });
      })
      .catch(function (e) {
        content.innerHTML = '<div class="impact-loading">Impact failed: ' + _esc(e.message) + '</div>';
      });
  }

  function _impactGroup(title, items, dir) {
    if (!items || !items.length) return '';
    var h = '<div class="impact-group"><div class="impact-group-title">'
      + (dir === 'up' ? '▲ ' : dir === 'down' ? '▼ ' : '') + _esc(title)
      + ' <span class="impact-count">' + items.length + '</span></div>';
    items.slice(0, 60).forEach(function (it) {
      var kindLabel = EDGE_KIND_LABEL[it.kind] || it.kind || '';
      var conf = (it.confidence != null && it.confidence < 1)
        ? ' <span class="impact-conf">' + Math.round(it.confidence * 100) + '%</span>' : '';
      h += '<div class="impact-box" data-file="' + _esc(it.file || '') + '">'
        + '<span class="impact-arrow">' + (dir === 'up' ? '←' : dir === 'down' ? '→' : '·') + '</span>'
        + '<span class="impact-name">' + _esc(it.label || it.name || it.file || '?') + '</span>'
        + '<span class="impact-edge">' + _esc(kindLabel) + conf + '</span>'
        + '</div>';
    });
    if (items.length > 60) h += '<div class="impact-loading">… ' + (items.length - 60) + ' more</div>';
    return h + '</div>';
  }

  // File-level rollup: distinct files this one depends on / is depended on
  // by, with edge counts — the "what does changing this break" view.
  function _impactFiles(title, items, dir) {
    if (!items || !items.length) return '';
    var h = '<div class="impact-group"><div class="impact-group-title">'
      + (dir === 'up' ? '▲ ' : '▼ ') + _esc(title)
      + ' <span class="impact-count">' + items.length + '</span></div>';
    items.slice(0, 40).forEach(function (it) {
      h += '<div class="impact-box" data-file="' + _esc(it.file || '') + '">'
        + '<span class="impact-arrow">' + (dir === 'up' ? '←' : '→') + '</span>'
        + '<span class="impact-name">' + _esc(it.label || it.file || '?') + '</span>'
        + '<span class="impact-edge">' + (it.edges || 0) + ' · ' + _esc((it.kinds || []).join('/')) + '</span>'
        + '</div>';
    });
    if (items.length > 40) h += '<div class="impact-loading">… ' + (items.length - 40) + ' more</div>';
    return h + '</div>';
  }

  // Causal chains: execution flows (processes) entered from this file.
  function _impactProcesses(procs) {
    if (!procs || !procs.length) return '';
    var h = '<div class="impact-group"><div class="impact-group-title">⚡ Causal chains (execution flows) '
      + '<span class="impact-count">' + procs.length + '</span></div>';
    procs.slice(0, 30).forEach(function (p) {
      h += '<div class="impact-box">'
        + '<span class="impact-arrow">⚡</span>'
        + '<span class="impact-name">' + _esc(p.label || p.entry || '?') + '</span>'
        + '<span class="impact-edge">' + _esc(p.kind || '') + ' · d' + (p.depth != null ? p.depth : '?')
        + ' · ' + (p.symbol_count != null ? p.symbol_count : '?') + ' sym</span>'
        + '</div>';
    });
    if (procs.length > 30) h += '<div class="impact-loading">… ' + (procs.length - 30) + ' more</div>';
    return h + '</div>';
  }

  function _impactVersions(v) {
    if (!v || !v.available || !(v.versions || []).length) return '';
    var h = '<div class="impact-group"><div class="impact-group-title">⎇ Versions '
      + '<span class="impact-count">' + v.versions.length + '</span></div>';
    v.versions.slice(0, 12).forEach(function (c) {
      h += '<div class="impact-box"><span class="impact-arrow">·</span>'
        + '<span class="impact-name">' + _esc(_short(c.subject || '', 44)) + '</span>'
        + '<span class="impact-edge">' + _esc((c.sha || '')) + ' ' + _esc((c.date || '').slice(0, 10)) + '</span>'
        + '</div>';
    });
    return h + '</div>';
  }

  function _impactHtml(d) {
    var center = d.center || {};
    var h = '<div class="impact-center">' + _esc(center.label || center.file || 'this file') + '</div>';
    // Lead with the file-level direction (developer blast-radius at a glance).
    h += _impactFiles('Depends on (files)', d.depends_on, 'down');
    h += _impactFiles('Depended on by (files)', d.depended_on_by, 'up');
    // Doc references (Markdown links → files) — all-file indexing.
    h += _impactGroup('References (docs → files)', d.references, 'down');
    h += _impactGroup('Referenced by (docs)', d.referenced_by, 'up');
    // Causal chains this file launches.
    h += _impactProcesses(d.processes);
    // Then the detailed symbol-level edges.
    h += _impactGroup('Calls / imports (symbols)', d.downstream, 'down');
    h += _impactGroup('Called / imported by (symbols)', d.upstream, 'up');
    h += _impactGroup('Defines', d.members, 'flat');
    h += _impactVersions(d.versions);
    if (!(d.downstream || []).length && !(d.upstream || []).length
        && !(d.members || []).length && !(d.processes || []).length
        && !(d.references || []).length && !(d.referenced_by || []).length
        && !(d.depends_on || []).length && !(d.depended_on_by || []).length) {
      h += '<div class="impact-loading">No dependencies found in the code-graph.</div>';
    }
    return h;
  }

  function _closeFlow() {
    var panel = document.getElementById('flow-panel');
    if (panel) panel.classList.remove('open');
  }

  // ── Live tail: poll expanded sessions + domains for new work ──────────
  function _ensureLiveTimer() {
    if (_liveTimer || !_liveOn) return;
    _liveTimer = setInterval(_liveTick, LIVE_MS);
  }

  function _stopLiveTimer() {
    if (_liveTimer) { clearInterval(_liveTimer); _liveTimer = null; }
  }

  function _liveTick() {
    if (!_mounted || !_liveOn) return;
    // 1. Tail every expanded session for new chain steps.
    Object.keys(_liveSince).forEach(function (sessNodeId) {
      var sid = sessNodeId.replace(/^session:/, '');
      var since = _liveSince[sessNodeId] || 0;
      _fetchJSON('/api/trace/chain?session=' + encodeURIComponent(sid) + '&since=' + since)
        .then(function (d) {
          if (d && d.nodes && d.nodes.length) {
            _apply(d);
            _flash((d.nodes || []).filter(function (n) {
              return (n.kind || n.type) === 'action' || (n.kind || n.type) === 'prompt';
            }).length + ' new in ' + sid.slice(0, 8));
          }
          if (typeof d.next_since === 'number') _liveSince[sessNodeId] = d.next_since;
        })
        .catch(function () { /* transient; retry next tick */ });
    });
    // 2. Surface NEW sessions in expanded domains.
    Object.keys(_liveDomains).forEach(function (domId) {
      _fetchJSON('/api/trace/sessions?domain=' + encodeURIComponent(domId))
        .then(function (d) {
          var n = (d.nodes || []).length;
          if (n > (_liveDomains[domId] || 0)) {
            _apply(d);   // dedup drops the ones already shown
            _liveDomains[domId] = n;
            _flash('+new session in ' + domId.replace(/^domain:/, ''));
          }
        })
        .catch(function () {});
    });
  }

  function _flash(msg) {
    _setStatus('● live · ' + msg);
  }

  function _setLive(on) {
    _liveOn = !!on;
    if (_liveOn) { _ensureLiveTimer(); _setStatus('● live on'); }
    else { _stopLiveTimer(); _setStatus('○ live paused'); }
  }

  function _edgeId(endpoint) {
    return endpoint && typeof endpoint === 'object' ? endpoint.id : endpoint;
  }

  // Extract only the directed fragments rooted at sessions that are both
  // expanded and visible in Trace. Activity batches normally contain one
  // session → action → target fragment, but replay/aggregation is allowed to
  // combine several sessions; following outgoing edges avoids leaking an
  // unrelated session through a shared file/tool target.
  function _visibleActivityFragment(nodes, edges) {
    if (!_mounted || !_liveOn) return null;
    nodes = nodes || [];
    edges = edges || [];

    var outgoing = Object.create(null);
    edges.forEach(function (e) {
      var source = _edgeId(e && e.source);
      if (!source) return;
      (outgoing[source] || (outgoing[source] = [])).push(e);
    });

    var keepNodes = Object.create(null);
    var keepEdges = Object.create(null);
    var queue = [];
    nodes.forEach(function (n) {
      var kind = n && (n.kind || n.type);
      if (kind === 'session' && _expanded[n.id]) {
        keepNodes[n.id] = true;
        queue.push(n.id);
      }
    });
    // A valid activity producer emits the session node on every fragment.
    // Still accept a root named only by an edge so a dedup-aware transport is
    // free to omit a node the client already owns.
    Object.keys(outgoing).forEach(function (source) {
      if (_expanded[source] && /^session:/.test(source) && !keepNodes[source]) {
        keepNodes[source] = true;
        queue.push(source);
      }
    });
    if (!queue.length) return null;

    for (var qi = 0; qi < queue.length; qi++) {
      var sourceId = queue[qi];
      (outgoing[sourceId] || []).forEach(function (e) {
        var targetId = _edgeId(e.target);
        if (!targetId) return;
        var edgeKey = e.id || (sourceId + '->' + targetId + ':' + (e.kind || e.type || ''));
        keepEdges[edgeKey] = true;
        if (!keepNodes[targetId]) {
          keepNodes[targetId] = true;
          queue.push(targetId);
        }
      });
    }

    var keptNodes = nodes.filter(function (n) { return n && keepNodes[n.id]; });
    var keptEdges = edges.filter(function (e) {
      var source = _edgeId(e && e.source);
      var target = _edgeId(e && e.target);
      var key = (e && e.id) || (source + '->' + target + ':' + ((e && (e.kind || e.type)) || ''));
      return !!keepEdges[key];
    });
    return keptNodes.length || keptEdges.length
      ? { nodes: keptNodes, edges: keptEdges }
      : null;
  }

  // Narrow contract used by activity_stream.js. Returns true only when some
  // observed activity was accepted for an expanded Trace session. It never
  // changes views or mounts a renderer; _apply uses the existing graph merge.
  function _acceptActivityBatch(nodes, edges) {
    var fragment = _visibleActivityFragment(nodes, edges);
    if (!fragment) return false;
    _apply(fragment);
    var actions = fragment.nodes.filter(function (n) {
      var kind = n.kind || n.type;
      return kind === 'action' || kind === 'prompt';
    }).length;
    if (actions) _flash(actions + ' new live action' + (actions === 1 ? '' : 's'));
    return true;
  }

  function _esc(s) {
    // Escapes the full HTML special set INCLUDING quotes, so the result is
    // safe in both element text AND quoted-attribute contexts (e.g.
    // data-file="..."). Without the quote escapes a value containing `"`
    // breaks out of the attribute → injection (CodeQL js/incomplete-sanitization).
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ── Trace detail panel: kind-dispatched rich info ────────────────────
  // Owns #detail-content for trace nodes. domain → counts; session →
  // linked conversation + chain summary; action → causal context + files;
  // prompt → full text; file → git diff + AST/impact.
  function _show() {
    var c = _container();
    if (c) c.style.display = '';
    _mounted = true;
    _boot();
    if (_liveOn && (Object.keys(_liveSince).length || Object.keys(_liveDomains).length)) {
      _ensureLiveTimer();
    }
  }
  function _hide() {
    _mounted = false;
    _cancelAllReveals(true);
    _stopLiveTimer();   // don't poll while another view is active
  }

  function _short(text, n) {
    n = n || 60;
    var s = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function _attach() {
    if (!window.JUG || !JUG.on) { setTimeout(_attach, 60); return; }
    JUG.on('state:activeView', function (ev) {
      if (ev && ev.value === 'trace') _show(); else _hide();
    });
    // Detail is rendered by detail_panel.js (the single panel). Trace only
    // drives canvas EXPANSION on select.
    JUG.on('graph:selectNode', function (node) {
      if (_mounted) _expand(node);
    });
    var flowClose = document.getElementById('flow-close');
    if (flowClose) flowClose.addEventListener('click', _closeFlow);
    if (JUG.state && JUG.state.activeView === 'trace') _show();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _attach);
  } else {
    _attach();
  }

  window.TraceView = {
    boot: _boot,
    reload: function () { _booted = false; _boot(); },
    setLive: _setLive,
    isLive: function () { return _liveOn; },
    acceptActivityBatch: _acceptActivityBatch,
    showImpact: _renderImpact,   // detail-panel "Impact" section opens this
  };
})();
