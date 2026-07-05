// Cortex Neural Graph — Canvas Drawing
// Large glowing circles, bright cyan edges, neural network aesthetic
(function() {
  var animFrame = 0;
  (function tick() { animFrame++; requestAnimationFrame(tick); })();

  JUG._draw = {};
  JUG._draw.animFrame = function() { return animFrame; };

  // ── Surface-correct baked colours (canvas cannot read CSS custom
  //    properties) — read once via CortexPalette, refreshed on
  //    cortex:surface-change. Falls back to the ink-tuned literals if
  //    CortexPalette hasn't loaded yet (module-order safety only). */
  var pal = {
    ok: '#40D870', warn: '#E0B040', danger: '#E05050',
    accentInk: '#8B6914', text: '#FFFFFF', canvas: '#08080f',
    // DD-04 point-cloud ambient + selection — the node-fill data family
    // itself lives in JUG._tok (config.js); this is only what draw.js
    // needs directly: the ambient mass and the selection override.
    fieldPoint: '#B8AC98', accentDeep: '#A53E00',
  };
  function refreshPalette() {
    if (!window.CortexPalette) return;
    var hex = window.CortexPalette.hex;
    pal.ok = hex('--ok-ink') || pal.ok;
    pal.warn = hex('--warn-ink') || pal.warn;
    pal.danger = hex('--danger-ink') || pal.danger;
    pal.accentInk = hex('--accent-ink') || pal.accentInk;
    pal.text = hex('--text') || pal.text;
    pal.canvas = hex('--canvas') || pal.canvas;
    pal.fieldPoint = hex('--field-point') || pal.fieldPoint;
    pal.accentDeep = hex('--accent-deep') || pal.accentDeep;
  }
  refreshPalette();
  if (window.CortexSurface) {
    window.addEventListener(window.CortexSurface.EVENT, refreshPalette);
  }
  JUG._draw.palette = pal;

  // ── Node sizing — hierarchy-aware ──
  JUG._draw.nodeRadius = function(n) {
    var base = n.size || 3;
    if (n.type === 'root') return 12;
    if (n.type === 'category') return Math.max(6, base * 0.5);
    // Domain hub — DD-04 (cards/data-pointcloud.html): "one --warn-deep
    // hub per domain, 1.3x larger."
    if (n.type === 'domain') return Math.max(5, base * 0.9) * 1.3;
    if (n.type === 'agent') return 4;
    if (n.type === 'type-group') return 2.5;
    if (n.type === 'topic') return Math.max(4, base * 0.7);
    if (n.type === 'bridge-entity') return Math.max(3.5, base * 0.6);
    if (n.type === 'discussion') return Math.max(2.5, base * 0.5);
    return Math.max(2.2, base * 0.45);
  };

  JUG._draw.hitArea = function(node, color, ctx) {
    var r = JUG._draw.nodeRadius(node) + 3;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
    ctx.fill();
  };

  // ── Color utilities ──
  function rgba(hex, a) {
    var r = parseInt(hex.slice(1, 3), 16) || 0;
    var g = parseInt(hex.slice(3, 5), 16) || 0;
    var b = parseInt(hex.slice(5, 7), 16) || 0;
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }
  JUG._draw.colorAlpha = rgba;

  // ── Node rendering — large solid glowing circles ──
  JUG._draw.node = function(node, ctx, globalScale, hoveredId, selectedId, neighbors) {
    try { _drawNodeInner(node, ctx, globalScale, hoveredId, selectedId, neighbors); }
    catch(e) { if (!JUG._drawErr) { JUG._drawErr = true; console.error('[draw] Error rendering node:', node.id, node.type, e); } }
  };
  function _drawNodeInner(node, ctx, globalScale, hoveredId, selectedId, neighbors) {
    var x = node.x, y = node.y;
    if (x === undefined || y === undefined) return;
    var r = JUG._draw.nodeRadius(node);
    var isSelected = node.id === selectedId;
    var isHighlit = node.id === hoveredId || isSelected;
    var isDimmed = selectedId && !isSelected && !neighbors[node.id];

    // Ambient mass (G7 — cards/data-pointcloud.html DD-04): once a node is
    // selected, everything outside its neighborhood becomes ambient
    // texture — opaque --field-point, never an alpha-faded copy of the
    // node's own data hue. Alpha-fading here would both violate the token
    // contract and make the mass invisible on the paper surface (a
    // translucent dark dot vanishes on cream). Cheap flat dot, no glow,
    // no rings, no label — this IS the ambient-mass rendering path.
    if (isDimmed) {
      ctx.globalAlpha = 1.0;
      ctx.fillStyle = pal.fieldPoint;
      ctx.beginPath();
      ctx.arc(x, y, Math.max(1.4, r * 0.55), 0, 2 * Math.PI);
      ctx.fill();
      return;
    }

    // Selected/focus node renders in the accent, nothing else does
    // (G4 — terracotta is selection only, never a data category).
    var color = isSelected ? pal.accentDeep : JUG.getNodeColor(node);

    ctx.globalAlpha = 1.0;

    // Selection halo — the ONE glow this system draws (DD-04: "Selected/
    // focus = --accent-deep + halo"). G6 forbids decorative glow/gradient
    // everywhere else ("no glow — reactor dot excepted"); the diffuse
    // radial-gradient wash this file used to draw around EVERY node (a
    // "neural network aesthetic" left over from before the design gate)
    // is exactly that forbidden pattern, and at ~90k overlapping
    // instances it compounds into a pale cyan haze that hides the deep
    // per-node hue entirely, regardless of how correct the underlying
    // token is. Hover gets a crisp rim instead (its detail lives in the
    // tooltip DOM overlay, not the canvas — DD-04: "hover = kind badge +
    // verbatim path + heat meter").
    if (isSelected) {
      var haloR = r * 4;
      var halo = ctx.createRadialGradient(x, y, r * 0.6, x, y, haloR);
      halo.addColorStop(0, rgba(color, 0.4));
      halo.addColorStop(1, 'transparent');
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(x, y, haloR, 0, 2 * Math.PI);
      ctx.fill();
    }

    // Flat true-hue body — the point IS the deep token, undiluted; no
    // specular-highlight gradient (G6 — the heat track is the system's
    // only sanctioned gradient) to dilute it toward a lighter tint.
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, 2 * Math.PI);
    ctx.fill();

    // Thin rim — crisp edge definition; brighter/thicker on hover only.
    ctx.strokeStyle = rgba(color, isHighlit ? 0.9 : 0.5);
    ctx.lineWidth = isHighlit ? 0.9 : 0.4;
    ctx.stroke();

    // Quality indicator ring — green (≥0.6), amber (≥0.3), red (<0.3)
    if (node.quality !== undefined) {
      var q = node.quality;
      var qColor = q >= 0.6 ? pal.ok : q >= 0.3 ? pal.warn : pal.danger;
      var qAlpha = 0.5 + q * 0.4;
      ctx.strokeStyle = rgba(qColor, qAlpha);
      ctx.lineWidth = 0.6;
      // Draw arc proportional to quality (full circle = 1.0)
      ctx.beginPath();
      ctx.arc(x, y, r + 1.5, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * q);
      ctx.stroke();
    }

    // Global indicator — golden double ring
    if (node.isGlobal) {
      ctx.strokeStyle = rgba(pal.accentInk, 0.7);
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      ctx.arc(x, y, r + 1.8, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.strokeStyle = rgba(pal.accentInk, 0.35);
      ctx.lineWidth = 0.4;
      ctx.beginPath();
      ctx.arc(x, y, r + 3.2, 0, 2 * Math.PI);
      ctx.stroke();
    }

    // Protected indicator
    if (node.isProtected) {
      ctx.strokeStyle = rgba(pal.accentInk, 0.6);
      ctx.lineWidth = 0.5;
      ctx.setLineDash([2, 2]);
      ctx.beginPath();
      ctx.arc(x, y, r + 2.8, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Emotion pulse
    if (node.emotion && node.emotion !== 'neutral' && (node.arousal || 0) > 0.2) {
      var pulse = 0.3 + 0.5 * Math.sin(animFrame * 0.04 * (0.5 + (node.arousal || 0)));
      ctx.strokeStyle = rgba(color, Math.abs(pulse) * 0.5);
      ctx.lineWidth = 0.4;
      ctx.beginPath();
      ctx.arc(x, y, r + 3, 0, 2 * Math.PI);
      ctx.stroke();
    }

    // Consolidation stage ring — colored per stage, line style indicates stability
    if (node.consolidationStage && node.type === 'memory') {
      var sc = JUG.CONSOLIDATION_COLORS[node.consolidationStage] || '#50C8E0';
      ctx.strokeStyle = rgba(sc, 0.7);
      ctx.lineWidth = node.consolidationStage === 'consolidated' ? 1.0 : 0.7;
      if (node.consolidationStage === 'labile') ctx.setLineDash([0.8, 1.2]);
      else if (node.consolidationStage === 'reconsolidating') ctx.setLineDash([1.5, 1.5]);
      ctx.beginPath();
      ctx.arc(x, y, r + 4.5, 0, 2 * Math.PI);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Interference spikes — red radiating lines for high-interference memories
    if (node.interferenceScore > 0.5 && node.type === 'memory') {
      var iAlpha = Math.min(0.6, (node.interferenceScore - 0.5) * 1.2);
      ctx.strokeStyle = rgba(pal.danger, iAlpha);
      ctx.lineWidth = 0.4;
      for (var spike = 0; spike < 4; spike++) {
        var angle = spike * Math.PI / 2 + animFrame * 0.01;
        ctx.beginPath();
        ctx.moveTo(x + Math.cos(angle) * (r + 2), y + Math.sin(angle) * (r + 2));
        ctx.lineTo(x + Math.cos(angle) * (r + 5), y + Math.sin(angle) * (r + 5));
        ctx.stroke();
      }
    }

    // Schema badge — small gold diamond for schema-matched memories
    if (node.schemaMatchScore > 0.5 && node.type === 'memory') {
      ctx.fillStyle = rgba(pal.accentInk, 0.6);
      var dx = x + r * 0.7, dy = y + r * 0.7;
      ctx.beginPath();
      ctx.moveTo(dx, dy - 1.5);
      ctx.lineTo(dx + 1.5, dy);
      ctx.lineTo(dx, dy + 1.5);
      ctx.lineTo(dx - 1.5, dy);
      ctx.closePath();
      ctx.fill();
    }

    // Labels
    drawLabel(ctx, node, x, y, r, globalScale, isHighlit);

    ctx.globalAlpha = 1.0;
  }

  function drawLabel(ctx, node, x, y, r, scale, isHighlit) {
    var isLabeled = node.type === 'root' || node.type === 'category'
      || node.type === 'domain' || node.type === 'agent'
      || node.type === 'topic' || node.type === 'bridge-entity';
    // Only show labels for structural nodes; others only on hover/select
    if (!isLabeled && !isHighlit) return;

    var isTop = node.type === 'root' || node.type === 'category' || node.type === 'domain';
    var fs = isTop ? Math.max(4, r * 0.5) : Math.max(2.8, 3.2 / Math.max(scale * 0.3, 0.6));
    var weight = isTop ? '700 ' : (node.type === 'agent' ? '600 ' : '500 ');
    ctx.font = weight + fs + 'px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    var text = JUG._bestNodeLabel(node).slice(0, 28);
    var ty = y + r + fs * 0.5;

    // Canvas-colour outline for contrast against the current surface
    // (was a hardcoded dark rgba — didn't invert on the paper surface).
    ctx.strokeStyle = rgba(pal.canvas, 0.85);
    ctx.lineWidth = fs * 0.35;
    ctx.lineJoin = 'round';
    ctx.strokeText(text, x, ty);

    // Text-colour fill for readability, re-inked per surface.
    ctx.fillStyle = rgba(pal.text, isHighlit ? 1.0 : 0.92);
    ctx.fillText(text, x, ty);
  }

})();
