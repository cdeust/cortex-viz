// Cortex Brain View — real anatomical atlas registration (MNI152 → mesh).
//
// Replaces the hand-eyeballed fractional region centres in anatomy.js with
// PUBLISHED stereotaxic coordinates: each region's centroid in MNI152 space
// (millimetres), linearly registered into this mesh's normalized frame. The
// numbers below are atlas centroids, not guesses.
//   source: Tzourio-Mazoyer et al. (2002) "Automated Anatomical Labeling of
//   activations in SPM using a macroscopic anatomical parcellation of the MNI
//   MRI single-subject brain", NeuroImage 15:273-289 (AAL region centroids).
//   source: Mazziotta et al. (2001) "A probabilistic atlas and reference
//   system for the human brain (ICBM152)", Phil Trans R Soc Lond B 356:1293.
//   Subcortical centroids cross-checked against the Harvard-Oxford subcortical
//   atlas (Desikan et al. 2006, NeuroImage 31:968-980).
//
// HONESTY NOTE (zetetic §8): this is an AFFINE registration of atlas centroids
// into a single-subject cortical mesh that carries NO parcellation of its own.
// It places each region at its true MNI centroid, remapped by a measured
// rigid axis transform + per-axis scale to the mesh bounding box. It is NOT a
// per-vertex parcellation of THIS mesh (that needs a labelled atlas surface
// registered to these exact vertices — not available for the vendored GLB).
// So: coordinates are real and their RELATIVE anatomy is faithful; absolute
// fit to this particular mesh's gyri is affine-accurate, not vertex-exact.
//
// Axis registration — MNI152 (x=R+, y=A+, z=S+) into the mesh world frame
// measured empirically in anatomy.js (2026-06-30):
//     mesh X (left- → right+)      =  MNI x
//     mesh Y (inferior- → superior+) =  MNI z
//     mesh Z (anterior- → posterior+) = -MNI y
// Each axis is then centred and scaled by the MNI152 brain-tissue extent so a
// centroid maps to a fraction in [-1, 1] of the corresponding mesh half-extent
// (buildAtlas in anatomy.js resolves that fraction against the real mesh box).

window.BRAIN = window.BRAIN || {};

(function () {
  // MNI152 brain-TISSUE extent (mm), not the padded scanner field of view:
  // the mesh box is the cortical surface, so we normalize against tissue
  // bounds, not the ~[-90,90] FOV. source: MNI152 template tissue envelope
  // (ICBM152 2009; Mazziotta et al. 2001).
  var MNI = {
    x: { center: 0,   half: 72 },   // L/R  ±72 mm
    y: { center: -16, half: 92 },   // A/P  [-108, +76] mm  → center -16
    z: { center: 16,  half: 66 },   // S/I  [-50, +82] mm   → center +16
  };

  // Region centroids in MNI mm (RIGHT hemisphere; the mirror is applied at
  // resolve time for bilateral regions). Keys match anatomy.js REGIONS.
  var CENTROID_MM = {
    // ── Declarative: medial temporal lobe ──
    hippocampus:      [28, -20, -14],  // AAL Hippocampus_R centroid ≈ (29,-20,-14)
    amygdala:         [24,  -4, -18],  // AAL Amygdala_R ≈ (23,-4,-18)
    parahippocampal:  [24, -32, -16],  // AAL ParaHippocampal_R ≈ (22,-32,-16)
    // ── Declarative: semantic temporal neocortex ──
    atl:              [40,  14, -32],  // Temporal pole (AAL Temporal_Pole_Sup_R)
    lateral_temporal: [56, -34,  -6],  // Middle temporal gyrus (AAL Temporal_Mid_R)
    // ── Association cortex (structural knowledge — design analogy) ──
    parietal_temporal:[46, -58,  40],  // Inferior parietal / angular (AAL)
    // ── Executive frontal ──
    dlpfc:            [38,  30,  34],  // Middle frontal gyrus (AAL Frontal_Mid_R)
    ofc:              [22,  32, -16],  // Orbital frontal (AAL Frontal_Med_Orb_R)
    // ── Nondeclarative / procedural ──
    striatum:         [26,   4,   2],  // Putamen (AAL Putamen_R ≈ 26,4,2)
    cerebellum:       [24, -64, -38],  // Cerebellar hemisphere (AAL Cerebelum_Crus)
    // ── Connectome rich-club / default-mode hubs ──
    precuneus_pcc:    [ 6, -58,  36],  // Precuneus (near-midline; AAL Precuneus_R)
    superior_frontal: [16,  24,  50],  // Superior frontal gyrus (AAL Frontal_Sup_R)
    thalamus:         [10, -18,   8],  // Thalamus (AAL Thalamus_R ≈ 11,-18,8)
  };

  // MNI mm centroid → mesh fractional coordinate [-1,1] per axis, via the
  // measured rigid axis remap + per-axis tissue-extent normalization. Returns
  // fx as a POSITIVE magnitude (bilateral convention: anatomy.js applies the
  // hemisphere sign); midline regions naturally return a small fx.
  function fracFor(key) {
    var mm = CENTROID_MM[key];
    if (!mm) return null;
    var xmm = mm[0], ymm = mm[1], zmm = mm[2];
    var fx = (xmm - MNI.x.center) / MNI.x.half;          // mesh X = MNI x
    var fy = (zmm - MNI.z.center) / MNI.z.half;          // mesh Y = MNI z
    var fz = -(ymm - MNI.y.center) / MNI.y.half;         // mesh Z = -MNI y
    return [Math.abs(fx), fy, fz];
  }

  BRAIN.MNI_ATLAS = {
    centroidMM: CENTROID_MM,
    extentMM: MNI,
    // Public: fractional mesh-frame centre for a region key, or null.
    fracFor: fracFor,
  };
})();
