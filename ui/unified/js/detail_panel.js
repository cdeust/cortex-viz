// Cortex Neural Graph — Detail Panel
// Uses JUG._fmt (detail_format.js) for human-readable rendering
(function() {

  function getConnections(node) {
    var edges = JUG._currentEdges || [];
    var result = [];
    edges.forEach(function(e) {
      var sid = typeof e.source === 'object' ? e.source.id : e.source;
      var tid = typeof e.target === 'object' ? e.target.id : e.target;
      if (sid === node.id || tid === node.id) result.push(e);
    });
    return result;
  }

  function findNode(id) {
    // O(1) fast path when a renderer publishes a node index (the brain view
    // has 279k nodes — a linear scan per connection would stall selection).
    // The galaxy never sets _nodeIndex, so its behaviour is unchanged.
    if (JUG._nodeIndex) return JUG._nodeIndex.get(id) || null;
    var gd = JUG.getGraph ? JUG.getGraph().graphData() : { nodes: [] };
    for (var i = 0; i < gd.nodes.length; i++) {
      if (gd.nodes[i].id === id) return gd.nodes[i];
    }
    return null;
  }

  // ── Emotion section ──

  function buildEmotion(data) {
    var colors = {
      urgency: '#ff3366', frustration: '#ef4444', satisfaction: '#22c55e',
      discovery: '#f59e0b', confusion: '#8b5cf6',
    };
    if (!data.emotion || data.emotion === 'neutral') return '';
    var col = colors[data.emotion] || '#90a4ae';
    var labels = {
      urgency: 'This memory has a sense of urgency',
      frustration: 'This memory captures a frustrating moment',
      satisfaction: 'This memory reflects a positive outcome',
      discovery: 'This memory marks a discovery or insight',
      confusion: 'This memory involves uncertainty',
    };
    var h = '<div class="section-title">Emotional State</div>';
    h += '<div class="emo-card" style="border-color:' + col + '30">';
    h += '<div class="emo-name" style="color:' + col + '">' +
      data.emotion.charAt(0).toUpperCase() + data.emotion.slice(1) + '</div>';
    h += '<div class="emo-desc">' + (labels[data.emotion] || '') + '</div>';
    if (data.arousal !== undefined) {
      h += '<div class="emo-meter"><span>Intensity</span>' +
        '<div class="bio-bar"><div class="bio-fill" style="width:' +
        Math.round(data.arousal * 100) + '%;background:' + col +
        '"></div></div></div>';
    }
    h += '</div>';
    return h;
  }

  // ── Connections section ──

  // Bug fix (2026-07-10, user-reported): a wiki hub can link to several
  // distinct FILE/SYMBOL/WIKI nodes that share the exact same display
  // label (e.g. 6 different `run_benchmark.py` harnesses in 6 different
  // directories, or two wiki pages with identical titles). Rendered as
  // identical rows, they read as a duplication bug rather than 6 real
  // connections. Disambiguate ONLY the colliding rows within a group,
  // using the shortest directory suffix (from `node.path`) that makes
  // them unique; nodes without a `path` fall back to a short id suffix.
  // Non-colliding rows are untouched — no visual noise when there is
  // nothing to disambiguate.
  //
  // precondition: `items` is one edge-type group (array of
  //   {node, weight, confidence, reason}), all sharing the group's
  //   edgeType — this is why disambiguation is scoped PER GROUP, not
  //   across the whole connections list.
  // postcondition: returns a Map from item.node.id -> distinguishing
  //   suffix string, containing an entry ONLY for nodes whose label
  //   collides with at least one sibling in `items`.
  function shortestDistinguishingSuffix(paths, names) {
    // paths: array of full path strings (same length/order as callers'
    // colliding items). names: the rendered label per path — dropped
    // from the comparison ONLY when it equals the path's last segment
    // (true for FILE/SYMBOL, whose label IS the basename — repeating it
    // would be redundant). For WIKI-like nodes the label is a title, not
    // a path segment (e.g. two "Ingestion pipeline" pages living in the
    // SAME directory but different files, pipeline.md vs pipeline-v2.md)
    // — there the basename is exactly the distinguishing signal and
    // must stay in the comparison, or both would show "wiki/ingestion"
    // and remain visually identical (the bug this fix exists to kill).
    // Returns a parallel array of path suffixes (POSIX-joined, no
    // trailing slash) that are pairwise unique at the smallest possible
    // depth. Falls back to the full path if even that does not
    // disambiguate (genuinely identical path — a real duplicate, which
    // SHOULD render identically).
    var dirParts = paths.map(function(p, i) {
      var parts = String(p || '').split('/');
      if (parts.length && parts[parts.length - 1] === names[i]) parts.pop();
      return parts;
    });
    var maxDepth = dirParts.reduce(function(m, d) { return Math.max(m, d.length); }, 0);
    for (var depth = 1; depth <= maxDepth; depth++) {
      var suffixes = dirParts.map(function(d) {
        return d.slice(Math.max(0, d.length - depth)).join('/');
      });
      var seen = {};
      var unique = true;
      for (var i = 0; i < suffixes.length; i++) {
        if (Object.prototype.hasOwnProperty.call(seen, suffixes[i])) { unique = false; break; }
        seen[suffixes[i]] = true;
      }
      if (unique) return suffixes;
    }
    return dirParts.map(function(d) { return d.join('/'); });
  }

  function buildLabelDisambiguation(items, names) {
    // invariant: names[i] is the already-computed display label for
    // items[i] (fullLabel of node.label || node.id) — collisions are
    // judged on that exact rendered string.
    var byLabel = {};
    names.forEach(function(name, i) {
      if (!byLabel[name]) byLabel[name] = [];
      byLabel[name].push(i);
    });
    var subByIndex = {};
    Object.keys(byLabel).forEach(function(label) {
      var idxs = byLabel[label];
      if (idxs.length < 2) return; // no collision — leave untouched
      var withPath = idxs.filter(function(i) { return !!items[i].node.path; });
      if (withPath.length === idxs.length) {
        // Every colliding node carries a `path` — disambiguate by the
        // shortest unique directory suffix (e.g. "benchmarks/locomo").
        var paths = idxs.map(function(i) { return items[i].node.path; });
        var labelsForPaths = idxs.map(function(i) { return names[i]; });
        var suffixes = shortestDistinguishingSuffix(paths, labelsForPaths);
        idxs.forEach(function(i, k) { subByIndex[i] = suffixes[k]; });
      } else {
        // No usable path data on at least one colliding node (non-file
        // kinds without a path field) — last-resort disambiguator: a
        // short id suffix, still better than an indistinguishable row.
        idxs.forEach(function(i) {
          var id = String(items[i].node.id || '');
          subByIndex[i] = id.length > 10 ? '…' + id.slice(-8) : id;
        });
      }
    });
    return subByIndex;
  }

  function buildConnections(data, edges) {
    if (!edges.length) return '';
    var byType = {};
    edges.forEach(function(e) {
      var sid = typeof e.source === 'object' ? e.source.id : e.source;
      var tid = typeof e.target === 'object' ? e.target.id : e.target;
      var otherId = sid === data.id ? tid : sid;
      var other = findNode(otherId);
      if (!other) return;
      var t = e.type || 'related';
      if (!byType[t]) byType[t] = [];
      // Gap 6: preserve edge-level confidence + reason so the detail
      // panel can show WHY an edge exists and how trustworthy it is.
      byType[t].push({
        node: other,
        weight: e.weight || 0,
        confidence: typeof e.confidence === 'number' ? e.confidence : null,
        reason: e.reason || null,
      });
    });

    var friendlyEdge = {
      'memory-entity': 'Mentioned in',
      'domain-entity': 'Belongs to',
      'groups': 'Grouped under',
      'bridge': 'Cross-domain link',
      'co_occurrence': 'Often seen with',
      'caused_by': 'Caused by',
      'resolved_by': 'Resolved by',
      'imports': 'Imports',
      'calls': 'Calls',
      'has-discussion': 'Discussion in',
      'wiki_links': 'Related page',
      'documents': 'Documents memory',
      'wiki_source': 'Documents file',
      'cited_in': 'Cited in session',
    };

    var h = '<div class="section-title">Connections (' + edges.length + ')</div>';
    Object.keys(byType).sort().forEach(function(edgeType) {
      var items = byType[edgeType];
      var edgeColor = JUG.EDGE_COLORS[edgeType] || '#90a4ae';
      var label = friendlyEdge[edgeType] || edgeType.replace(/_/g, ' ');
      h += '<div class="conn-group">';
      h += '<div class="conn-type" style="color:' + edgeColor + '">' +
        label + ' <span class="conn-count">' + items.length + '</span></div>';
      var names = items.map(function(item) {
        return JUG._fmt.fullLabel(item.node.label || item.node.id);
      });
      var disambiguation = buildLabelDisambiguation(items, names);
      items.forEach(function(item, idx) {
        var c = JUG.getNodeColor(item.node);
        var name = names[idx];
        // Gap 6: show confidence + reason chips ONLY for heuristic
        // edges (calls / imports / unresolved). Structural defaults
        // ("100% direct-ast" / "100% memory-entities-link") are
        // tautological — rendering them on 8000 defined_in rows adds
        // pure visual noise. Suppress if both values match a known
        // structural default.
        var meta = '';
        var isStructuralDefault =
          item.confidence === 1.0 &&
          (item.reason === 'direct-ast' ||
           item.reason === 'memory-entities-link');
        if (!isStructuralDefault) {
          if (item.confidence != null) {
            var pct = Math.round(item.confidence * 100);
            meta += ' <span class="conn-confidence" title="Edge confidence">'
              + pct + '%</span>';
          }
          if (item.reason) {
            meta += ' <span class="conn-reason" title="Edge reason">'
              + JUG._fmt.esc(item.reason) + '</span>';
          }
        }
        // Homonym fix: `sub` is set only for rows whose label collides
        // with a sibling in this group (see buildLabelDisambiguation).
        // The full path/id always goes in `title` regardless, so a
        // hover reveals the exact target even when no collision fired.
        var sub = disambiguation[idx];
        var subHtml = sub ? '<span class="conn-sub">' + JUG._fmt.esc(sub) + '</span>' : '';
        var fullRef = item.node.path || item.node.id || '';
        h += '<div class="conn-item" data-node-id="' + item.node.id +
          '" title="' + JUG._fmt.esc(fullRef) + '">' +
          '<span class="conn-dot" style="background:' + c + '"></span>' +
          '<span class="conn-label">' + JUG._fmt.esc(name) + subHtml + '</span>' +
          meta + '</div>';
      });
      h += '</div>';
    });
    return h;
  }

  // ── Wiki-page section ──
  // Wiki nodes previously fell through to the generic (non-discussion)
  // branch below, which renders memory-shaped sections (emotion / bio /
  // badges) that make no sense for a documentation page. This builder
  // surfaces the fields ``core.workflow_graph_wiki.ingest_wiki_page``
  // actually attaches (page_kind/status/heat/path — see
  // ``WorkflowNode``'s ``extra="allow"`` passthrough) instead.
  function buildWikiDetail(data) {
    var h = '<div class="section-title">Page</div>';
    h += '<div class="disc-timeline">';
    if (data.page_kind) {
      h += '<div class="disc-timeline-row"><span>Kind</span><span class="disc-val">' + JUG._fmt.esc(data.page_kind) + '</span></div>';
    }
    if (data.domain) {
      h += '<div class="disc-timeline-row"><span>Domain</span><span class="disc-val">' + JUG._fmt.esc(data.domain) + '</span></div>';
    }
    if (data.status) {
      h += '<div class="disc-timeline-row"><span>Status</span><span class="disc-val">' + JUG._fmt.esc(data.status) + '</span></div>';
    }
    if (data.path) {
      h += '<div class="disc-timeline-row"><span>Path</span><span class="disc-val">' + JUG._fmt.esc(data.path) + '</span></div>';
    }
    h += '</div>';
    return h;
  }

  // ── Main panel builder ──

  function openDetailPanel(data) {
    var panel = document.getElementById('detail-panel');
    var content = document.getElementById('detail-content');
    if (!panel || !content || !data) return;

    // Trace nodes get their own renderer (header + expandable sections),
    // in THIS one panel. Galaxy-memory rendering below is bypassed.
    // discussion/memory exist in BOTH views — in the galaxy they're rich
    // session-summary / PG-memory cards (handled below); in trace they're
    // single chain events whose `full` text the trace renderer shows. Gate
    // those two kinds on the active view so neither path steals the other's.
    var _k = data.kind || data.type;
    var _trace = !!(window.JUG && JUG.state && JUG.state.activeView === 'trace');
    if (_k === 'domain' || _k === 'session' || _k === 'action' ||
        _k === 'prompt' || _k === 'file' ||
        ((_k === 'discussion' || _k === 'memory') && _trace)) {
      content.innerHTML = JUG._traceDetail.build(data);
      panel.classList.add('open');
      panel.classList.remove('minimized');
      JUG._traceDetail.wire(content, data);
      return;
    }

    var col = JUG.getNodeColor(data);
    var typeLabel = JUG.NODE_LABELS[data.type] || data.type || data.kind || '';
    var edges = getConnections(data);

    var h = '';
    if (data.type === 'discussion') {
      h += JUG._fmt.header(data, col, typeLabel);
      h += JUG._fmt.quality(data);
      h += JUG._disc.buildDiscussionDetail(data);
      h += buildConnections(data, edges);
    } else if (data.type === 'wiki') {
      // Title/kind/domain + citations (linked discussions via the
      // cited_in edge, "Documents memory"/"Related page" via documents/
      // wiki_links) — buildConnections already groups by edge type and
      // wireInteractions (called below, common to every branch) wires
      // its .conn-item clicks, so citations are click-to-select for free.
      h += JUG._fmt.header(data, col, typeLabel);
      h += buildWikiDetail(data);
      h += buildConnections(data, edges);
    } else {
      h += JUG._fmt.header(data, col, typeLabel);
      h += JUG._fmt.quality(data);
      h += JUG._fmt.gauges(data);
      h += JUG._fmt.content(data);
      h += JUG._fmt.tags(data.tags);
      h += buildEmotion(data);
      h += JUG._fmt.bioSection(data);
      h += buildConnections(data, edges);
      h += JUG._fmt.badges(data);
    }

    content.innerHTML = h;

    // Memory nodes get the rich emotion + meaning + explained
    // measurements panels (same components used in Knowledge cards) so
    // Board ticket details match Knowledge card details information
    // parity with plain-language explanations for every number.
    if (data && data.type === 'memory' && window.JUG && JUG._memSci) {
      if (typeof JUG._memSci.buildEmotionChip === 'function') {
        var emo = JUG._memSci.buildEmotionChip(data);
        if (emo) {
          emo.classList.add('ms-emotion--detail');
          content.appendChild(emo);
        }
      }
      if (typeof JUG._memSci.buildMeaningSection === 'function') {
        var meaning = JUG._memSci.buildMeaningSection(data);
        if (meaning) content.appendChild(meaning);
      }
      if (typeof JUG._memSci.buildExplainedPanel === 'function') {
        var explained = JUG._memSci.buildExplainedPanel(data);
        if (explained) content.appendChild(explained);
      }
    }

    panel.classList.add('open');
    wireInteractions(content);
  }

  function wireInteractions(content) {
    content.querySelectorAll('.conn-item[data-node-id]').forEach(function(el) {
      el.addEventListener('click', function() {
        JUG.selectNodeById(el.dataset.nodeId);
      });
    });
    var expandBtn = document.getElementById('detail-expand-btn');
    if (expandBtn) {
      expandBtn.addEventListener('click', function() {
        var block = document.getElementById('detail-content-text');
        if (!block) return;
        var collapsed = block.classList.toggle('collapsed');
        expandBtn.textContent = collapsed ? 'Show more' : 'Show less';
      });
    }
    var viewBtn = content.querySelector('.disc-view-btn');
    if (viewBtn) {
      viewBtn.addEventListener('click', function() {
        JUG._disc.openConversationModal(viewBtn.dataset.sessionId);
      });
    }
    var rawBtn = document.getElementById('detail-raw-btn');
    if (rawBtn) {
      rawBtn.addEventListener('click', function() {
        var raw = document.getElementById('detail-raw-text');
        if (!raw) return;
        var hidden = raw.classList.toggle('hidden');
        rawBtn.textContent = hidden ? 'Show raw' : 'Hide raw';
      });
    }
  }

  function closeDetailPanel() {
    var panel = document.getElementById('detail-panel');
    if (panel) {
      panel.classList.remove('open');
      panel.classList.remove('minimized');
    }
  }

  // Event listeners
  // On select: open the panel immediately with the lightweight snapshot
  // node (id/kind/connections render instantly), then enrich it in place
  // with the full PG record fetched on demand. The CXGB snapshot only
  // carries 6 fields/node so the galaxy loads in ~30 ms; the rich body /
  // heat / stage / scientific fields are drilled here.
  // source: design 2026-05-31 — top-25k galaxy + on-demand cold-tail drill.
  var _enrichCache = {};
  var _lastSelectedId = null;     // closure-tracked; avoids depending on JUG state field name
  JUG.on('graph:selectNode', function(node) {
    // Trace nodes render in this SAME panel via the trace-aware
    // openDetailPanel branch — no second panel, no galaxy-memory PG
    // enrichment. discussion/memory are trace nodes ONLY while the trace view
    // is active (in the galaxy they fall through to the rich PG-enriched path).
    var _k = node && (node.kind || node.type);
    var _trace = !!(window.JUG && JUG.state && JUG.state.activeView === 'trace');
    if (_k === 'domain' || _k === 'session' || _k === 'action' ||
        _k === 'prompt' || _k === 'file' ||
        ((_k === 'discussion' || _k === 'memory') && _trace)) {
      openDetailPanel(node);
      _lastSelectedId = node && node.id;
      return;
    }
    openDetailPanel(node);
    if (!node || !node.id) return;
    _lastSelectedId = node.id;
    if (node.body || node.content) return;            // already rich
    if (_enrichCache[node.id]) {
      Object.assign(node, _enrichCache[node.id]);
      openDetailPanel(node);
      return;
    }
    fetch('/api/graph/node?id=' + encodeURIComponent(node.id))
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){
        if (!d || !d.found || !d.record) return;
        var rec = d.record;
        // Map PG columns onto the field names the panel renderers expect.
        var enrich = {
          body: rec.content || rec.body,
          content: rec.content,
          heat: rec.heat_base != null ? rec.heat_base : rec.heat,
          stage: rec.consolidation_stage || rec.stage,
          tags: rec.tags,
          created_at: rec.created_at,
          arousal: rec.arousal,
          emotional_valence: rec.emotional_valence,
          dominant_emotion: rec.dominant_emotion,
          importance: rec.importance,
          confidence: rec.confidence,
          access_count: rec.access_count,
        };
        _enrichCache[node.id] = enrich;
        Object.assign(node, enrich);
        // Re-render only if this node is still the selected one.
        if (JUG.state && JUG.state.selectedNode &&
            JUG.state.selectedNode.id === node.id) {
          openDetailPanel(node);
        }
      })
      .catch(function(){ /* keep the lightweight panel on failure */ });
  });
  JUG.on('graph:deselectNode', function(){ _lastSelectedId = null; closeDetailPanel(); });

  function minimizeDetailPanel() {
    var panel = document.getElementById('detail-panel');
    if (!panel) return;
    panel.classList.remove('open');
    panel.classList.add('minimized');
  }

  function restoreDetailPanel() {
    var panel = document.getElementById('detail-panel');
    if (!panel) return;
    panel.classList.remove('minimized');
    panel.classList.add('open');
  }

  document.addEventListener('DOMContentLoaded', function() {
    // Minimize: slide panel down to peek bar, keep selection
    var minBtn = document.getElementById('minimize-detail');
    if (minBtn) minBtn.addEventListener('click', function() {
      minimizeDetailPanel();
    });

    // Peek bar: click to restore full panel
    var peekBar = document.getElementById('detail-peek');
    if (peekBar) peekBar.addEventListener('click', function() {
      restoreDetailPanel();
    });

    // Close: deselect node entirely
    var closeBtn = document.getElementById('close-detail');
    if (closeBtn) closeBtn.addEventListener('click', function() {
      JUG.deselectNode();
    });
  });

  window.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'Escape') {
      var panel = document.getElementById('detail-panel');
      if (panel && panel.classList.contains('open')) minimizeDetailPanel();
      else if (panel && panel.classList.contains('minimized')) {
        panel.classList.remove('minimized');
        JUG.deselectNode();
      }
    }
  });

  JUG.openDetailPanel = openDetailPanel;
  JUG.closeDetailPanel = closeDetailPanel;
})();
