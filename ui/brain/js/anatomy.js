// Cortex Brain View — anatomical atlas (memory systems → brain regions).
//
// Pure domain knowledge: WHERE each kind of node lives in the brain, and HOW
// regions connect. No mesh, no graph, no rendering — those layers consume this.
//
// The mapping follows the taxonomy of memory: declarative memory (episodic +
// semantic) in the medial temporal lobe and neocortex; nondeclarative
// (procedural) in the basal ganglia and cerebellum; organizational hubs at the
// connectome's rich-club seats.
//   source: Squire, L.R. (2004) "Memory systems of the brain", Neurobiol Learn
//   Mem 82(3):171-177.
//   source: van den Heuvel & Sporns (2011) "Rich-club organization of the human
//   connectome", J Neurosci 31:15775-15786.
//
// HONESTY NOTE (zetetic §8): the vendored mesh is a single UNLABELED cortical
// surface — there is no parcellation to register against. Every region center
// below is therefore an APPROXIMATE anatomical placement, hand-located in this
// mesh's normalized frame from gross neuroanatomy (qualitative MNI/ICBM152
// positions; Mazziotta et al. 2001). They are principled, not atlas-exact.
//
// Mesh orientation (post-root-matrix world frame the layout uses), derived
// empirically from the GLB vertices via mirror-symmetry + silhouette analysis:
//   X = left(-) -> right(+)      (the bilateral mirror axis; symmetry 0.76)
//   Y = inferior(-) -> superior(+)   (brainstem/cerebellum tail at low Y)
//   Z = anterior(-) -> posterior(+)  (cerebellar bulge at high Z)
//   source: measured 2026-06-30 on ui/brain/models/brain.glb.

window.BRAIN = window.BRAIN || {};

(function () {
  // Region metadata — everything EXCEPT the centre position. `bilateral`,
  // `sigma` (fractional Gaussian spread), and `group` (drives tract selection)
  // are display/topology tuning owned here. The CENTRE of each region is no
  // longer hand-picked: it comes from atlas_mni.js, which registers the
  // region's real MNI152 centroid into this mesh's fractional frame. `frac`
  // is filled from that atlas at load (see below) — a region with no atlas
  // centroid keeps its `fracFallback` so the layer degrades gracefully.
  //   source (per-region anatomy): cited in atlas_mni.js CENTROID_MM.
  var REGION_META = {
    // Episodic core (medial temporal). Hippocampus is a curved tube running
    // antero-posteriorly — elongated Z sigma.
    hippocampus:      { bilateral: true,  sigma: [0.06, 0.06, 0.11], group: 'mtl',         fracFallback: [0.40, -0.45, 0.04] },
    amygdala:         { bilateral: true,  sigma: [0.05, 0.05, 0.05], group: 'mtl',         fracFallback: [0.33, -0.51, 0.19] },
    parahippocampal:  { bilateral: true,  sigma: [0.07, 0.06, 0.12], group: 'mtl',         fracFallback: [0.33, -0.48, 0.17] },
    // Semantic temporal neocortex.
    atl:              { bilateral: true,  sigma: [0.08, 0.08, 0.08], group: 'temporal',    fracFallback: [0.55, -0.72, -0.33] },
    lateral_temporal: { bilateral: true,  sigma: [0.08, 0.12, 0.18], group: 'temporal',    fracFallback: [0.78, -0.33, 0.20] },
    // Multimodal association cortex (code symbols — design analogy).
    parietal_temporal:{ bilateral: true,  sigma: [0.12, 0.15, 0.18], group: 'parietal',    fracFallback: [0.64, 0.36, 0.46] },
    // Executive frontal.
    dlpfc:            { bilateral: true,  sigma: [0.10, 0.12, 0.10], group: 'frontal',      fracFallback: [0.53, 0.27, -0.50] },
    ofc:              { bilateral: true,  sigma: [0.08, 0.06, 0.08], group: 'frontal',      fracFallback: [0.31, -0.48, -0.52] },
    // Nondeclarative / procedural.
    striatum:         { bilateral: true,  sigma: [0.06, 0.08, 0.10], group: 'subcortical',  fracFallback: [0.36, -0.21, -0.22] },
    cerebellum:       { bilateral: true,  sigma: [0.18, 0.10, 0.12], group: 'cerebellum',   fracFallback: [0.33, -0.82, 0.52] },
    // Connectome rich-club / default-mode hubs.
    //   source: van den Heuvel & Sporns (2011) J Neurosci 31:15775-15786.
    //   source: Buckner, Andrews-Hanna & Schacter (2008) Ann N Y Acad Sci 1124:1.
    precuneus_pcc:    { bilateral: false, sigma: [0.05, 0.10, 0.10], group: 'hub',          fracFallback: [0.08, 0.30, 0.46] },
    superior_frontal: { bilateral: false, sigma: [0.05, 0.10, 0.08], group: 'hub',          fracFallback: [0.22, 0.52, -0.43] },
    thalamus:         { bilateral: true,  sigma: [0.05, 0.06, 0.06], group: 'hub',          fracFallback: [0.14, -0.12, 0.02] },
  };

  // Resolve each region's CENTRE from the registered MNI atlas (atlas_mni.js),
  // falling back to the baked value if the atlas module is absent. This is the
  // one place hand-tuned centres were replaced by real stereotaxic coordinates.
  var REGIONS = (function () {
    var atlas = BRAIN.MNI_ATLAS || null;
    var out = {};
    for (var key in REGION_META) {
      if (!Object.prototype.hasOwnProperty.call(REGION_META, key)) continue;
      var meta = REGION_META[key];
      var frac = atlas && atlas.fracFor(key);
      out[key] = {
        frac: frac || meta.fracFallback,
        bilateral: meta.bilateral,
        sigma: meta.sigma,
        group: meta.group,
      };
    }
    return out;
  })();

  // The three rich-club seats domains are round-robined across (boot/layout
  // pick by domain index). source: van den Heuvel & Sporns (2011).
  var HUB_SEATS = ['precuneus_pcc', 'superior_frontal', 'thalamus'];

  // Node kind -> region. `memory` is handled specially in layout (consolidation
  // gradient hippocampus -> neocortex by heat) so it is not listed here.
  //   source: Squire (2004) for the declarative assignments (entity->semantic
  //   hub, file->MTL context).
  //   Procedural memory is NOT monolithic: habit/stimulus-response learning is
  //   striatal (Knowlton, Mangels & Squire 1996) while motor-SEQUENCE learning
  //   is cerebellar (Doyon, Penhune & Ungerleider 2003) — hence the split below.
  //   `symbol -> parietal_temporal` is OUR design analogy (code structure ~
  //   multimodal association cortex), not a mapping Squire makes.
  var KIND_REGION = {
    entity: 'atl',                 // semantic (Squire 2004)
    symbol: 'parietal_temporal',   // structural knowledge (design analogy)
    file: 'parahippocampal',       // episodic context / source documents
    discussion: 'precuneus_pcc',   // session context (default-mode hub)
    skill: 'striatum',             // procedural: habit learning (Knowlton 1996)
    command: 'striatum',
    mcp: 'striatum',
    tool_hub: 'striatum',
    hook: 'cerebellum',            // procedural: motor sequences (Doyon 2003)
    agent: 'cerebellum',
  };

  // Major white-matter tracts, keyed by an unordered pair of region groups.
  // `bow` is a fractional world-space displacement applied to the edge
  // midpoint; the caller scales it by edge length. `name` is for legend/debug.
  //   source: Catani & Thiebaut de Schotten (2008) "A diffusion tensor imaging
  //   tractography atlas for virtual in vivo dissections", Cortex 44:1105-1132.
  // fornix/cingulum: medial temporal <-> medial hubs, arching superior-medial.
  // uncinate: temporal <-> orbitofrontal, hooking inferior-anterior.
  // SLF/arcuate: frontal <-> parietal, bowing lateral-superior.
  var TRACT_RULES = [
    { a: 'mtl',      b: 'hub',      bow: [0.0, 0.85, 0.0],   name: 'fornix/cingulum' },
    { a: 'mtl',      b: 'frontal',  bow: [0.0, 0.55, -0.35], name: 'cingulum' },
    { a: 'temporal', b: 'frontal',  bow: [0.0, -0.45, -0.4], name: 'uncinate' },
    { a: 'frontal',  b: 'parietal', bow: [0.0, 0.6, 0.1],    name: 'SLF/arcuate' },
    { a: 'temporal', b: 'parietal', bow: [0.0, 0.4, 0.2],    name: 'middle long. fasc.' },
  ];

  function groupOf(regionKey) {
    var r = REGIONS[regionKey];
    return r ? r.group : null;
  }

  // Pick a tract bow for an edge given its endpoints' region keys + hemispheres.
  // Cross-hemisphere edges route over the corpus callosum (superior midline
  // arch). Same-hemisphere edges match a TRACT_RULE by region group, else fall
  // back to a gentle superior bow (generic association fibre). Returns a
  // fractional [x,y,z] bow, or null for intra-region edges (caller draws
  // straight). source: Catani & Thiebaut de Schotten (2008).
  function tractBow(regA, hemiA, regB, hemiB) {
    if (regA === regB && hemiA === hemiB) return null;
    if (hemiA !== hemiB) {
      // Corpus callosum: arch up and pull toward the midline (x handled by the
      // caller, which knows the endpoint x signs).
      return { bow: [0.0, 0.7, 0.0], name: 'corpus callosum', midline: true };
    }
    var ga = groupOf(regA), gb = groupOf(regB);
    for (var i = 0; i < TRACT_RULES.length; i++) {
      var t = TRACT_RULES[i];
      if ((t.a === ga && t.b === gb) || (t.a === gb && t.b === ga)) {
        return { bow: t.bow.slice(), name: t.name, midline: false };
      }
    }
    return { bow: [0.0, 0.35, 0.0], name: 'association', midline: false };
  }

  // Resolve the fractional atlas into WORLD coordinates given the mesh's world
  // bounding box (min/max THREE.Vector3 from brain_mesh.js).
  BRAIN.buildAtlas = function (box) {
    var cx = (box.min.x + box.max.x) / 2;
    var cy = (box.min.y + box.max.y) / 2;
    var cz = (box.min.z + box.max.z) / 2;
    var hx = (box.max.x - box.min.x) / 2;
    var hy = (box.max.y - box.min.y) / 2;
    var hz = (box.max.z - box.min.z) / 2;

    function centerOf(key, hemi) {
      var r = REGIONS[key];
      if (!r) return new THREE.Vector3(cx, cy, cz);
      var fx = r.bilateral ? hemi * Math.abs(r.frac[0]) : r.frac[0];
      return new THREE.Vector3(
        cx + fx * hx,
        cy + r.frac[1] * hy,
        cz + r.frac[2] * hz
      );
    }
    function sigmaOf(key) {
      var r = REGIONS[key];
      var s = r ? r.sigma : [0.1, 0.1, 0.1];
      return new THREE.Vector3(s[0] * hx, s[1] * hy, s[2] * hz);
    }
    return {
      centerOf: centerOf,
      sigmaOf: sigmaOf,
      regionForKind: function (kind) { return KIND_REGION[kind] || 'parietal_temporal'; },
      isBilateral: function (key) { var r = REGIONS[key]; return !r || r.bilateral; },
      hubSeat: function (domainIndex) { return HUB_SEATS[domainIndex % HUB_SEATS.length]; },
      tractBow: tractBow,
      // World-space bow vector from a fractional bow, scaled to half-extents.
      bowToWorld: function (bow) {
        return new THREE.Vector3(bow[0] * hx, bow[1] * hy, bow[2] * hz);
      },
      half: new THREE.Vector3(hx, hy, hz),
      center: new THREE.Vector3(cx, cy, cz),
    };
  };

  // Memory-system grouping for the legend (boot.js renders it). `repKind` is
  // the system's representative NODE KIND — the legend swatch is drawn in that
  // kind's ACTUAL rendered colour (boot.js firstColor[repKind]), so the
  // memory-system swatches match the colours actually on screen rather than a
  // separate hand-picked palette. `color` is only a fallback if the kind is
  // absent from the current graph. source: legend-alignment fix 2026-07-03.
  BRAIN.MEMORY_SYSTEMS = [
    { label: 'episodic (memory, files)', repKind: 'memory', color: '#7fe3a0', regions: ['hippocampus', 'parahippocampal', 'amygdala'] },
    { label: 'semantic (entities)', repKind: 'entity', color: '#8ab4ff', regions: ['atl', 'lateral_temporal'] },
    { label: 'structural (symbols)', repKind: 'symbol', color: '#c9a0ff', regions: ['parietal_temporal'] },
    { label: 'procedural (skills, tools, agents)', repKind: 'skill', color: '#ffc97a', regions: ['striatum', 'cerebellum'] },
    { label: 'hubs (domains)', repKind: 'domain', color: '#ff8fb0', regions: ['precuneus_pcc', 'superior_frontal', 'thalamus'] },
  ];
})();
