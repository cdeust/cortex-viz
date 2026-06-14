// Cortex — Galaxy viewer (deck.gl GPU scatterplot).
//
// Engages for large graphs (and on the ``?viz=tilemap`` override). Fetches
// every node's position/id/kind once from /api/quadtree, then renders the
// whole set as a deck.gl ScatterplotLayer over an OrthographicView in the
// Cartesian coordinate system. The GPU draws millions of points in one call
// and resolves per-point hover/click via the GPU picking buffer — so the
// field scales to the full Cortex + ecosystem corpus (500k–1M+ nodes) while
// every node stays individually clickable and readable. (A datashader
// raster path once lived here; it scaled but produced an image with no
// pickable nodes, so it was replaced by the GPU scatterplot.)
//
// Public API:
//   window.JUG.mountTilemap(container)
//
// Mounted by workflow_graph.js when the gate flag is on.
(function () {
  'use strict';

  // Tilemap third-party deps. Served from the standalone (vendored under
  // ``ui/unified/vendor/`` so the view never depends on a CDN being
  // reachable — unpkg/jsdelivr outages, restricted networks, or air-gapped
  // installs all worked previously by accident; now they work by design.
  //
  // Fallback: if a vendored file is unexpectedly missing (e.g. a partial
  // sync), the loader falls back to the original CDN URL so the view
  // still works on a developer machine. In production / sandboxed
  // environments the local path resolves first.
  var DECKGL_URL = '/vendor/deck.gl.min.js';
  var ARROW_URL  = '/vendor/apache-arrow.min.js';
  var DECKGL_FALLBACK = 'https://unpkg.com/deck.gl@9.0.27/dist.min.js';
  var ARROW_FALLBACK  = 'https://unpkg.com/apache-arrow@17.0.0/Arrow.es2015.min.js';

  var KIND_COLOR = {
    domain:    [252, 211,  77, 230],
    tool_hub:  [249, 115,  22, 230],
    skill:     [251, 146,  60, 230],
    command:   [250, 204,  21, 230],
    hook:      [168,  85, 247, 230],
    agent:     [236,  72, 153, 230],
    mcp:       [ 99, 102, 241, 230],
    memory:    [ 16, 185, 129, 230],
    discussion:[239,  68,  68, 230],
    entity:    [ 80, 176, 200, 230],
    file:      [  6, 182, 212, 230],
    symbol:    [100, 116, 139, 230],
  };

  function loadScriptOne(url) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = url; s.crossOrigin = 'anonymous';
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('failed to load ' + url)); };
      document.head.appendChild(s);
    });
  }

  // Try the local (vendored) URL first; on failure, fall back to the
  // upstream CDN. Either succeeds → the dep is on window. Both fail →
  // reject with the CDN error so the caller's message stays
  // informative for offline diagnostics.
  function loadScript(localUrl, fallbackUrl) {
    return loadScriptOne(localUrl).catch(function () {
      if (!fallbackUrl) throw new Error('failed to load ' + localUrl);
      return loadScriptOne(fallbackUrl);
    });
  }

  // Decode the gzipped Apache Arrow IPC payload from /api/quadtree
  // into parallel Float32Array x/y plus parallel id+kind arrays. The
  // browser decompresses gzip transparently when the response
  // declares Content-Encoding: gzip — fetch returns the inflated
  // bytes already.
  //
  // ``no_layout`` (HTTP 503 with reason="no_layout") surfaces as a
  // distinct error class so the caller can drive the auto-recompute
  // path without re-parsing the body.
  async function fetchQuadtree() {
    var resp = await fetch('/api/quadtree');
    if (resp.status === 503) {
      var detail = await resp.json().catch(function () { return {}; });
      var err = new Error('quadtree 503: ' + (detail.reason || 'unknown'));
      err.reason = detail.reason || 'unknown';
      err.detail = detail.detail || null;
      throw err;
    }
    if (!resp.ok) throw new Error('quadtree fetch failed: ' + resp.status);
    var buf = await resp.arrayBuffer();
    var Arrow = window.Arrow || window.apacheArrow || (window['arrow'] || {});
    if (!Arrow.tableFromIPC) {
      throw new Error('Apache Arrow JS not loaded');
    }
    var table = Arrow.tableFromIPC(new Uint8Array(buf));
    var n = table.numRows;
    var ids = new Array(n);
    var kinds = new Array(n);
    var xs = new Float32Array(n);
    var ys = new Float32Array(n);
    var idCol = table.getChild('id');
    var kindCol = table.getChild('kind');
    var xCol = table.getChild('x');
    var yCol = table.getChild('y');
    for (var i = 0; i < n; i++) {
      ids[i] = idCol.get(i);
      kinds[i] = kindCol.get(i);
      xs[i] = xCol.get(i);
      ys[i] = yCol.get(i);
    }
    return { ids: ids, kinds: kinds, xs: xs, ys: ys, count: n };
  }

  async function mount(container) {
    container.innerHTML = '';
    var status = document.createElement('div');
    status.style.cssText = 'position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);'
      + 'color:#9aa4b2;font:13px/1.4 system-ui,sans-serif;'
      + 'padding:14px 22px;border:1px solid rgba(120,180,200,0.25);'
      + 'border-radius:6px;background:rgba(8,8,16,0.8);';
    status.textContent = 'Loading tilemap dependencies…';
    container.appendChild(status);

    try {
      // deck.gl renders + GPU-picks the nodes; Arrow decodes the quadtree
      // position payload. (Flatbush is gone — GPU picking replaced the
      // CPU spatial index.)
      await Promise.all([
        loadScript(DECKGL_URL, DECKGL_FALLBACK),
        loadScript(ARROW_URL, ARROW_FALLBACK),
      ]);
    } catch (err) {
      status.textContent = 'Failed to load tilemap deps: ' + err.message;
      status.style.color = '#ff8888';
      return;
    }

    // Self-healing layout pass: if /api/quadtree returns 503 with
    // ``no_layout``, the page itself triggers /api/recompute_layout
    // and retries. Covers direct-URL access where the MCP entry point
    // didn't pre-prepare the layout.
    status.textContent = 'Fetching quadtree…';
    var qt;
    try {
      qt = await fetchQuadtree();
    } catch (err) {
      if (err && err.reason === 'no_layout') {
        status.textContent = 'No layout in PG. Computing now (≈90 s for 1M nodes)…';
        status.style.color = '#ffb86b';
        var recompute;
        try {
          var rr = await fetch('/api/recompute_layout');
          recompute = await rr.json();
        } catch (e2) {
          status.textContent = 'Layout request failed: ' + e2.message;
          status.style.color = '#ff8888';
          return;
        }
        if (recompute && recompute.status === 'ok') {
          status.textContent = 'Layout ready (' + recompute.node_count + ' nodes); fetching quadtree…';
          try {
            qt = await fetchQuadtree();
          } catch (e3) {
            status.textContent = 'Quadtree refetch failed: ' + e3.message;
            status.style.color = '#ff8888';
            return;
          }
        } else if (recompute && recompute.reason === 'igraph_missing') {
          status.innerHTML =
            '<div style="margin-bottom:6px;color:#ffb86b">'
            + '<b>viz-tile extras required</b></div>'
            + '<div style="color:#9aa4b2;font:11px JetBrains Mono,monospace">'
            + 'Install with one of:<br>'
            + '&nbsp;&nbsp;pip install -e \'.[viz-tile]\'<br>'
            + '&nbsp;&nbsp;uv pip install \'.[viz-tile]\'</div>';
          return;
        } else if (recompute && recompute.reason === 'no_graph_cached') {
          status.textContent = 'Graph not built yet. Visit /api/graph first then retry.';
          status.style.color = '#ffb86b';
          return;
        } else {
          status.textContent = 'Layout failed: ' + JSON.stringify(recompute || {});
          status.style.color = '#ff8888';
          return;
        }
      } else {
        status.textContent = 'Quadtree fetch failed: ' + err.message;
        status.style.color = '#ff8888';
        return;
      }
    }
    if (!qt.count) {
      status.textContent = 'Layout empty. Run /api/recompute_layout to populate.';
      status.style.color = '#ffb86b';
      return;
    }
    status.remove();

    var canvasHost = document.createElement('div');
    canvasHost.style.cssText = 'position:absolute;inset:0;background:#080810;';
    container.appendChild(canvasHost);

    var deck = window.deck;
    if (!deck || !deck.Deck) {
      var msg = document.createElement('div');
      msg.style.cssText = 'position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);color:#ff8888;font:13px system-ui;';
      msg.textContent = 'deck.gl failed to expose window.deck';
      canvasHost.appendChild(msg);
      return;
    }

    var ScatterplotLayer = deck.ScatterplotLayer;
    var OrthographicView = deck.OrthographicView;
    var COORDINATE_SYSTEM = deck.COORDINATE_SYSTEM;

    // --- GPU scatterplot of EVERY node -----------------------------------
    //
    // deck.gl renders the whole point set in a single GPU draw call and
    // resolves per-point hover/click on the GPU picking buffer. That makes
    // the field BOTH scalable (Cortex + every ingested project — 500k–1M+
    // nodes at 60 fps) AND individually clickable/readable. The previous
    // datashader TileLayer rasterised the points into an IMAGE: fast, but
    // an image has no pickable nodes and no legible structure — the wrong
    // tool for an interactive graph. (The /api/tile + /api/quadtree server
    // path stays; this renderer consumes the quadtree's full position set,
    // which we already fetched above.)
    //
    // Positions and colours are packed ONCE into typed arrays and handed to
    // deck as binary attributes, so there is no per-point JS in the hot
    // path — deck uploads them straight to GPU vertex attributes. This is
    // the standard deck.gl pattern for million-point layers.
    var count = qt.count;
    var positions = new Float32Array(count * 2);
    var colors = new Uint8Array(count * 4);
    var DEFAULT_COLOR = [148, 163, 184, 220]; // slate — unknown kind
    for (var i = 0; i < count; i++) {
      positions[i * 2] = qt.xs[i];
      positions[i * 2 + 1] = qt.ys[i];
      var c = KIND_COLOR[qt.kinds[i]] || DEFAULT_COLOR;
      colors[i * 4] = c[0];
      colors[i * 4 + 1] = c[1];
      colors[i * 4 + 2] = c[2];
      colors[i * 4 + 3] = c.length > 3 ? c[3] : 230;
    }

    var hoverLabel = document.createElement('div');
    hoverLabel.style.cssText = 'position:absolute;pointer-events:none;background:rgba(8,8,16,0.92);'
      + 'border:1px solid rgba(120,180,200,0.4);border-radius:4px;padding:4px 8px;'
      + "color:#e0e6ec;font:11px/1.3 'JetBrains Mono', monospace;display:none;z-index:5;";
    container.appendChild(hoverLabel);

    var deckInstance = new deck.Deck({
      parent: canvasHost,
      style: { position: 'absolute', inset: 0 },
      views: [new OrthographicView({ id: 'ortho', controller: { dragRotate: false } })],
      // Start at the zoom where the 2-unit world [-1,1] fills the smaller
      // viewport axis: Z0 = log2(minDim / 2). At zoom=0 an OrthographicView
      // renders 1 px per world-unit, so the whole galaxy would be a 2-pixel
      // dot at screen centre. minDim falls back to a sane default if the
      // host is not yet laid out.
      initialViewState: (function () {
        var w = canvasHost.clientWidth || container.clientWidth || 1000;
        var h = canvasHost.clientHeight || container.clientHeight || 800;
        var minDim = Math.max(1, Math.min(w, h));
        return { target: [0, 0, 0], zoom: Math.log2(minDim / 2) };
      })(),
      onHover: function (info) {
        // GPU picking: info.index is the node under the cursor (or -1).
        if (!info || info.index == null || info.index < 0) {
          hoverLabel.style.display = 'none';
          return;
        }
        hoverLabel.style.display = 'block';
        hoverLabel.style.left = (info.x + 12) + 'px';
        hoverLabel.style.top = (info.y + 12) + 'px';
        hoverLabel.textContent = qt.kinds[info.index] + ' · ' + qt.ids[info.index];
      },
      onClick: function (info) {
        if (!info || info.index == null || info.index < 0) return;
        // Emit on the global bus — #detail-panel (detail_panel.js) is the
        // SOLE owner of the node-detail panel. The scatterplot path joins
        // the same single-panel contract as the canvas/svg renderers.
        if (window.JUG && typeof JUG.emit === 'function') {
          var datum = { id: qt.ids[info.index], kind: qt.kinds[info.index] };
          try { JUG.emit('graph:selectNode', datum); } catch (_) {}
        }
      },
      layers: [
        new ScatterplotLayer({
          id: 'graph-nodes',
          coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
          // Binary attributes: zero per-point JS, straight to GPU.
          data: {
            length: count,
            attributes: {
              getPosition: { value: positions, size: 2 },
              getFillColor: { value: colors, size: 4 },
            },
          },
          getRadius: 2,
          radiusUnits: 'pixels',
          radiusMinPixels: 1.2,
          radiusMaxPixels: 7,
          pickable: true,
          autoHighlight: true,
          highlightColor: [255, 255, 255, 200],
        }),
      ],
    });

    return {
      destroy: function () {
        try { deckInstance.finalize(); } catch (_) {}
        if (canvasHost.parentNode) canvasHost.parentNode.removeChild(canvasHost);
        hoverLabel.remove();
      },
    };
  }

  window.JUG = window.JUG || {};
  window.JUG.mountTilemap = mount;
})();
