// Cortex Brain View — cortical surface geometry + domain anchoring.
//
// Two services derived purely from the brain mesh:
//   1. radiusInDir(dir): the distance from the brain centre to the cortical
//      surface in a given direction, from a coarse lon/lat depth map of the
//      triangle soup. layout.js uses it to (a) clamp every placed node just
//      inside the visible surface and (b) find the cold end of the memory
//      consolidation gradient on the neocortex.
//   2. anchorForDomain(galaxyPos): a stable neocortical SURFACE point for a
//      domain, so a domain's cooled memories / entities / symbols cluster in
//      one cortical territory instead of smearing across the whole brain. The
//      domain's 2D galaxy position is projected disk -> sphere (same mapping
//      the unified graph uses), then pushed out to the surface.
//
// Both are deterministic functions of the mesh, so the layout is stable across
// reloads.

window.BRAIN = window.BRAIN || {};

(function () {
  var NLON = 48;            // azimuth bins
  var NLAT = 24;            // polar bins
  var SURFACE_INSET = 0.96; // anchors sit just inside the surface, not on it

  // Galaxy domain x/y sit in ~[-0.9, 0.9]; mapping that extent to the equator
  // keeps the outermost domains just shy of the inferior pole.
  // source: measured domain x/y extent of /api/graph/full on 2026-06-30.
  var DISK_R = 0.95;

  function lonBin(x, z) {
    var lon = Math.atan2(z, x);            // [-pi, pi]
    var b = Math.floor((lon + Math.PI) / (2 * Math.PI) * NLON);
    return b < 0 ? 0 : (b >= NLON ? NLON - 1 : b);
  }
  function latBin(ny) {
    var lat = Math.asin(Math.max(-1, Math.min(1, ny)));  // [-pi/2, pi/2]
    var b = Math.floor((lat + Math.PI / 2) / Math.PI * NLAT);
    return b < 0 ? 0 : (b >= NLAT ? NLAT - 1 : b);
  }

  // Build the lon/lat -> max-radius envelope from triangle vertices. Radius is
  // distance from the origin (the mesh is normalized to bbox-centre origin).
  function buildDepthMap(verts) {
    var map = new Float32Array(NLON * NLAT);   // max radius per bin
    var n = verts.length;
    for (var o = 0; o < n; o += 3) {
      var x = verts[o], y = verts[o + 1], z = verts[o + 2];
      var r = Math.sqrt(x * x + y * y + z * z);
      if (r <= 0) continue;
      var bi = lonBin(x, z) * NLAT + latBin(y / r);
      if (r > map[bi]) map[bi] = r;
    }
    // Fill any empty bin from its neighbours so lookups never return 0.
    var fallback = 0, count = 0;
    for (var i = 0; i < map.length; i++) { if (map[i] > 0) { fallback += map[i]; count++; } }
    fallback = count ? fallback / count : 1;
    for (i = 0; i < map.length; i++) { if (map[i] === 0) map[i] = fallback; }
    return map;
  }

  // FNV-1a — stable string hash for the unlocated-domain fallback direction.
  function fnv1a(s) {
    var h = 0x811c9dc5;
    s = String(s == null ? '' : s);
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }

  // A domain with no galaxy position (the layout pipeline never placed it —
  // e.g. the cross-project "domain:__global__" hub) has no natural cortical
  // seat. The old code anchored EVERY such domain at the inferior pole
  // (0,-1,0), which piled all of a large global domain's cooled memories onto a
  // single degenerate point and would stack multiple unlocated domains on top
  // of each other. Instead, give each unlocated domain a STABLE pseudo-anchor
  // on the lateral neocortex, hashed from its id: a fixed azimuth and a
  // latitude bounded to [-0.4, 0.4] so the territory sits on the convex lateral
  // surface, never the inferior tip. Deterministic, so the layout is stable
  // across reloads. NOTE: this is a display fallback — the real fix is to give
  // __global__ a position in the layout store.
  function fallbackDir(domainId) {
    var h = fnv1a(domainId);
    var lon = (h % 4096) / 4096 * 2 * Math.PI - Math.PI;      // stable azimuth
    var y = (((h >>> 12) % 4096) / 4096) * 0.8 - 0.4;         // lateral band
    var s = Math.sqrt(Math.max(1 - y * y, 0));
    return new THREE.Vector3(s * Math.cos(lon), y, s * Math.sin(lon));
  }

  // Project a domain's 2D galaxy position to a unit direction (disk -> sphere).
  // Unlocated domains fall back to a stable hashed lateral direction.
  function anchorDir(pos, domainId) {
    if (!pos || pos.x == null || pos.y == null) return fallbackDir(domainId);
    var r = Math.min(Math.sqrt(pos.x * pos.x + pos.y * pos.y) / DISK_R, 1);
    var lon = Math.atan2(pos.y, pos.x);
    var phi = r * Math.PI;
    var s = Math.sin(phi);
    return new THREE.Vector3(s * Math.cos(lon), Math.cos(phi), s * Math.sin(lon));
  }

  // soup: { triangles } from brain_mesh.js.
  BRAIN.buildSurface = function (soup) {
    var depth = buildDepthMap(soup.triangles);

    function radiusInDir(x, y, z) {
      var r = Math.sqrt(x * x + y * y + z * z) || 1;
      return depth[lonBin(x, z) * NLAT + latBin(y / r)];
    }

    return {
      radiusInDir: radiusInDir,
      // Stable neocortical surface point for a domain (cold-gradient + symbol/
      // entity coherence anchor). domainId drives the fallback for domains with
      // no galaxy position (see fallbackDir).
      anchorForDomain: function (galaxyPos, domainId) {
        var d = anchorDir(galaxyPos, domainId);
        var rad = radiusInDir(d.x, d.y, d.z) * SURFACE_INSET;
        return d.multiplyScalar(rad);
      },
    };
  };
})();
