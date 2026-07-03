// Cortex Brain View — structural scaffold net.
//
// The glowing cyan wireframe that gives the brain its FORM is NOT the semantic
// graph edges (those link distant nodes and pile into a central hairball). It
// is a surface mesh: thousands of points sampled on the cortex, each linked to
// its nearest neighbours, so every line is short and hugs the surface — the
// delicate gyri-tracing net of a connectome render. This scaffold is purely
// structural (deterministic, seeded); the colored data nodes overlay on top.
//
// Neighbour search is a uniform spatial grid (not an O(n²) scan), so the net
// scales to a large vertex/edge count without a load-time cliff.

window.BRAIN = window.BRAIN || {};

(function () {
  // The scaffold is a faint FORM cue only — the colored data nodes are the
  // subject and must read clearly over it. It was competing with the nodes
  // (user report + screenshot 2026-07-02), so the net is dropped to a whisper:
  // fewer vertices, and edge/dot alpha cut ~2-3x. source: readability pass.
  var SCAFFOLD_N = 12000;    // surface vertices in the net (was 20000)
  var KNN = 5;               // nearest neighbours linked per vertex
  var GRID = 24;             // spatial-grid resolution per axis for neighbour search
  var NET_COLOR = 0x2c5372;  // dim steel-blue skeleton, well under the data web
  var EDGE_ALPHA = 0.022;
  var DOT_ALPHA = 0.05;

  // mulberry32 — seeded so the net is identical across reloads.
  // source: https://github.com/bryc/code/blob/master/jshash/PRNG.md
  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function pickTriangle(cum, target) {
    var lo = 0, hi = cum.length - 1;
    while (lo < hi) { var mid = (lo + hi) >> 1; if (cum[mid] < target) lo = mid + 1; else hi = mid; }
    return lo;
  }

  // Area-weighted points ON the surface (no inward jitter — the net is a skin).
  function sampleSurface(soup, n) {
    var verts = soup.triangles, cum = soup.cumAreas, total = soup.totalArea;
    var out = new Float32Array(n * 3), rand = mulberry32(0x01234567);
    for (var i = 0; i < n; i++) {
      var o = pickTriangle(cum, rand() * total) * 9;
      var u = rand(), v = rand();
      if (u + v > 1) { u = 1 - u; v = 1 - v; }
      var ax = verts[o], ay = verts[o + 1], az = verts[o + 2];
      var bx = verts[o + 3], by = verts[o + 4], bz = verts[o + 5];
      var cx = verts[o + 6], cy = verts[o + 7], cz = verts[o + 8];
      var j = i * 3;
      out[j] = ax + u * (bx - ax) + v * (cx - ax);
      out[j + 1] = ay + u * (by - ay) + v * (cy - ay);
      out[j + 2] = az + u * (bz - az) + v * (cz - az);
    }
    return out;
  }

  // k-nearest-neighbour edges via a uniform spatial grid: bin every point, then
  // for each point scan an outward ring of cells until enough candidates are
  // gathered, and keep the k closest. Deduped to one undirected line per pair.
  function knnEdges(pos, n, k) {
    var minx = Infinity, miny = Infinity, minz = Infinity;
    var maxx = -Infinity, maxy = -Infinity, maxz = -Infinity;
    for (var q = 0; q < n; q++) {
      var x = pos[q * 3], y = pos[q * 3 + 1], z = pos[q * 3 + 2];
      if (x < minx) minx = x; if (y < miny) miny = y; if (z < minz) minz = z;
      if (x > maxx) maxx = x; if (y > maxy) maxy = y; if (z > maxz) maxz = z;
    }
    var span = Math.max(maxx - minx, maxy - miny, maxz - minz) || 1;
    var cs = span / GRID;
    function cell(v, mn) { var c = Math.floor((v - mn) / cs); return c < 0 ? 0 : (c >= GRID ? GRID - 1 : c); }
    function keyOf(cx, cy, cz) { return (cx * GRID + cy) * GRID + cz; }

    var grid = new Map();
    var cellOf = new Int32Array(n * 3);
    for (var i = 0; i < n; i++) {
      var cx = cell(pos[i * 3], minx), cy = cell(pos[i * 3 + 1], miny), cz = cell(pos[i * 3 + 2], minz);
      cellOf[i * 3] = cx; cellOf[i * 3 + 1] = cy; cellOf[i * 3 + 2] = cz;
      var key = keyOf(cx, cy, cz), arr = grid.get(key);
      if (!arr) { arr = []; grid.set(key, arr); }
      arr.push(i);
    }

    var idx = new Int32Array(k), dist = new Float32Array(k), cand = [];
    var pairs = [], seen = new Set();
    for (i = 0; i < n; i++) {
      for (var f = 0; f < k; f++) { idx[f] = -1; dist[f] = Infinity; }
      var ix = pos[i * 3], iy = pos[i * 3 + 1], iz = pos[i * 3 + 2];
      var bx = cellOf[i * 3], by = cellOf[i * 3 + 1], bz = cellOf[i * 3 + 2];
      var ring = 1;
      while (true) {
        cand.length = 0;
        for (var dx = -ring; dx <= ring; dx++) {
          var gx = bx + dx; if (gx < 0 || gx >= GRID) continue;
          for (var dy = -ring; dy <= ring; dy++) {
            var gy = by + dy; if (gy < 0 || gy >= GRID) continue;
            for (var dz = -ring; dz <= ring; dz++) {
              var gz = bz + dz; if (gz < 0 || gz >= GRID) continue;
              var a = grid.get((gx * GRID + gy) * GRID + gz);
              if (a) for (var m = 0; m < a.length; m++) cand.push(a[m]);
            }
          }
        }
        if (cand.length >= k + 6 || ring >= GRID) break;
        ring++;
      }
      for (var c = 0; c < cand.length; c++) {
        var j = cand[c]; if (j === i) continue;
        var ddx = pos[j * 3] - ix, ddy = pos[j * 3 + 1] - iy, ddz = pos[j * 3 + 2] - iz;
        var d = ddx * ddx + ddy * ddy + ddz * ddz;
        if (d < dist[k - 1]) {
          var pp = k - 1;
          while (pp > 0 && dist[pp - 1] > d) { dist[pp] = dist[pp - 1]; idx[pp] = idx[pp - 1]; pp--; }
          dist[pp] = d; idx[pp] = j;
        }
      }
      for (var f2 = 0; f2 < k; f2++) {
        var nb = idx[f2]; if (nb < 0) continue;
        var lo = i < nb ? i : nb, hi = i < nb ? nb : i, kk = lo * n + hi;
        if (!seen.has(kk)) { seen.add(kk); pairs.push(lo, hi); }
      }
    }
    return pairs;
  }

  BRAIN.buildScaffold = function (soup) {
    var pos = sampleSurface(soup, SCAFFOLD_N);
    var pairs = knnEdges(pos, SCAFFOLD_N, KNN);

    var seg = new Float32Array(pairs.length * 3);
    for (var e = 0; e < pairs.length; e++) {
      var src = pairs[e] * 3, d = e * 3;
      seg[d] = pos[src]; seg[d + 1] = pos[src + 1]; seg[d + 2] = pos[src + 2];
    }
    var lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute('position', new THREE.BufferAttribute(seg, 3));
    var lines = new THREE.LineSegments(lineGeom, new THREE.LineBasicMaterial({
      color: NET_COLOR, transparent: true, opacity: EDGE_ALPHA,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    lines.renderOrder = 0;
    lines.frustumCulled = false;
    BRAIN.world.add(lines);

    var dotGeom = new THREE.BufferGeometry();
    dotGeom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    var dots = new THREE.Points(dotGeom, new THREE.PointsMaterial({
      color: NET_COLOR, size: 1.0, sizeAttenuation: true,
      transparent: true, opacity: DOT_ALPHA,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    dots.renderOrder = 0;
    dots.frustumCulled = false;
    BRAIN.world.add(dots);

    BRAIN.scaffold = { lines: lines, dots: dots, edgeCount: pairs.length / 2, vertexCount: SCAFFOLD_N };
    return BRAIN.scaffold;
  };
})();
