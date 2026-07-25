// Cortex — Coverage honesty model (issue #36).
//
// Pure, DOM-free decision layer that answers, for one view, "what is missing
// from what I am looking at?" — the anti-overtrust surface CBM ships as
// MissedCallout (graph-ui/src/components/MissedCallout.tsx) backed by
// check_index_coverage. A visualization that silently omits is worse than one
// that refuses: the user reads absence as evidence of absence. This module
// turns the live render/store/stream/progress/engine signals into an explicit,
// NAMED completeness verdict (coding-standards §13 F2 — degraded modes are
// explicit, never a silent default).
//
// This file is the testable seam (tests/js/coverage_model.test.mjs): it takes
// plain data in and returns a plain report out, with no DOM, no fetch, no
// closure state. coverage_indicator.js gathers the live sources and renders.
//
// Companion engine work (the source of files-indexed / extraction-failure /
// revision fields) is cdeust/automatised-pipeline#57 (parse-error persistence,
// missed graph) and #55 (snapshot revision / staleness contract). Those fields
// are OPTIONAL here: when the engine has not populated them, `engine` is null
// and the report carries a named 'engine-coverage-unavailable' degraded mode
// rather than fabricating a files/failures figure.
(function () {
  'use strict';

  var JUG = (window.JUG = window.JUG || {});

  // Non-negative integer coercion. A count read from a wire payload may be
  // null/undefined/negative/NaN; every downstream subtraction assumes a
  // non-negative integer, so normalise once at the boundary.
  function count(v) {
    var n = Number(v);
    if (!isFinite(n) || n < 0) return 0;
    return Math.floor(n);
  }

  // One rendered-vs-store axis (nodes or edges). Pre: rendered and inStore are
  // count()-coerced. Post: {rendered, inStore, missing, complete} where
  // missing = max(0, inStore - rendered) and complete iff missing === 0 AND a
  // denominator was actually known. When inStore is null the denominator is
  // unknown (e.g. the trace view streams on expand — no store-wide total): the
  // axis reports complete=null (neither proven complete nor proven short).
  function axis(rendered, inStore) {
    var r = count(rendered);
    if (inStore == null) {
      return { rendered: r, inStore: null, missing: null, complete: null };
    }
    var s = count(inStore);
    var missing = s > r ? s - r : 0;
    return { rendered: r, inStore: s, missing: missing, complete: missing === 0 };
  }

  // Engine parse-coverage axis (files indexed vs present) + extraction
  // failures. Pre: engine is the /api/graph/coverage payload OR null. Post:
  // null when engine data is absent/unavailable (caller records the named
  // degraded mode); else {indexed, present, missing, complete, failures}.
  function filesAxis(engine) {
    if (!engine || engine.available === false) return null;
    var present = count(engine.files_present);
    var indexed = count(engine.files_indexed);
    var missing = present > indexed ? present - indexed : 0;
    var failures = Array.isArray(engine.extraction_failures)
      ? engine.extraction_failures
      : [];
    return {
      indexed: indexed,
      present: present,
      missing: missing,
      // A view is file-complete only when every present file is indexed AND
      // no file reported a partial/failed extraction. parse_incomplete is the
      // count of files that indexed but with tree-sitter ERROR/MISSING ranges
      // (AP#57 shape) — a file can be "indexed" yet not fully represented.
      complete: missing === 0 && failures.length === 0 &&
                count(engine.parse_incomplete) === 0,
      failures: failures,
      parseIncomplete: count(engine.parse_incomplete),
    };
  }

  // Client-side LOD collapse. Pre: rendered may carry {droppedEdges, lodTier}
  // recorded by workflow_graph.js mount(); lodTier is
  // {heavy, snapToSlots, extreme} or absent. Post: {active, tier, collapsedEdges}
  // — active iff any LOD tier engaged (criterion 3: the aggregator's collapse
  // is a degraded mode and must SAY the collapsed count, not hide it).
  function lodAxis(rendered) {
    var tier = (rendered && rendered.lodTier) || null;
    var active = !!(tier && (tier.heavy || tier.snapToSlots || tier.extreme));
    return {
      active: active,
      tier: tier,
      collapsedEdges: count(rendered && rendered.droppedEdges),
      // Edges the renderer dropped because an endpoint node was not in the
      // rendered set (workflow_graph.js dangling prune). A silent edge
      // omission in its own right — surfaced as its own drill-down entry.
      danglingEdges: count(rendered && rendered.danglingEdges),
    };
  }

  // Staleness (criterion 4 + AP#55 revision contract). Pre: stream carries the
  // rendered snapshot's totals/revision; store carries the current store's
  // counts/revision; progress carries the build state; now is ms. Post:
  // {known, building, ageMs, storeRevision, snapshotRevision, stale}.
  //
  // A view is stale when the store has moved past the snapshot it was drawn
  // from. Two independent signals, either is sufficient:
  //   * revision mismatch — the store reports a revision (AP#55) different
  //     from the snapshot's; the authoritative signal when present.
  //   * count growth — the store node/edge total exceeds the snapshot total
  //     the view rendered; the fallback when no revision is wired yet.
  function staleness(stream, store, progress, now) {
    var building = !!(progress && progress.full_ready === false);
    var snapRev = stream && stream.revision != null ? stream.revision : null;
    var storeRev = store && store.revision != null ? store.revision : null;
    var capturedAt = stream ? stream.captured_at : null;
    var ageMs = capturedAt != null && now != null ? Math.max(0, now - capturedAt) : null;

    var stale = false;
    if (snapRev != null && storeRev != null) {
      stale = snapRev !== storeRev;
    } else if (store && stream) {
      var storeNodes = count(store.node_count);
      var snapNodes = count(stream.node_total);
      var storeEdges = count(store.edge_count);
      var snapEdges = count(stream.edge_total);
      // Only claim staleness from growth when the snapshot actually reported a
      // total (snapNodes>0); a zero snapshot total means "not yet loaded", not
      // "stale" — that is the building case, handled above.
      stale = (snapNodes > 0 && storeNodes > snapNodes) ||
              (snapEdges > 0 && storeEdges > snapEdges);
    }
    return {
      building: building,
      ageMs: ageMs,
      storeRevision: storeRev,
      snapshotRevision: snapRev,
      stale: stale,
    };
  }

  // Assemble the drill-down list (criterion 2) — one entry per specific
  // omission, each with a count so the user sees the magnitude, not just the
  // fact. Order: nodes, edges, files, extraction failures, LOD, staleness.
  function omissions(report, stream) {
    var out = [];
    if (report.nodes.missing) {
      out.push({ kind: 'nodes', label: 'nodes in store not rendered',
                 count: report.nodes.missing });
    }
    if (report.edges.missing) {
      out.push({ kind: 'edges', label: 'edges in store not rendered',
                 count: report.edges.missing });
    }
    if (report.files) {
      if (report.files.missing) {
        out.push({ kind: 'files', label: 'files present but not indexed',
                   count: report.files.missing });
      }
      if (report.files.parseIncomplete) {
        out.push({ kind: 'parse-incomplete',
                   label: 'files indexed with parse errors (partial)',
                   count: report.files.parseIncomplete });
      }
      if (report.files.failures.length) {
        out.push({ kind: 'extraction-failures',
                   label: 'files that failed extraction',
                   count: report.files.failures.length,
                   detail: report.files.failures });
      }
    }
    if (report.lod.active && report.lod.collapsedEdges) {
      out.push({ kind: 'lod', label: 'edges collapsed by LOD aggregation',
                 count: report.lod.collapsedEdges });
    }
    if (report.lod.danglingEdges) {
      out.push({ kind: 'dangling', label: 'edges dropped: endpoint not in view',
                 count: report.lod.danglingEdges });
    }
    if (stream && stream.truncated) {
      out.push({ kind: 'truncated', label: 'graph stream ended before the full snapshot arrived',
                 count: 1 });
    }
    if (report.staleness.stale) {
      out.push({ kind: 'stale', label: 'view drawn from a superseded snapshot',
                 count: 1 });
    }
    return out;
  }

  // computeCoverage(view, sources) → CoverageReport.
  //
  // Pre: view is a non-empty id; sources is
  //   { rendered, store, stream, progress, engine, denominatorMeaningful, now }
  //   any of rendered/store/stream/progress/engine may be null.
  // Post: a report whose `status` is one of:
  //   'unknown'    — no denominator yet (store null AND no stream totals): the
  //                  view cannot honestly claim complete OR incomplete.
  //   'complete'   — every known axis is complete, no LOD collapse, not
  //                  truncated, not stale (criterion 5: the quiet nominal path).
  //   'incomplete' — at least one omission (criterion 2/3/4).
  // Invariant: status==='complete' ⇒ omissions.length === 0, and vice-versa
  // when a denominator is known.
  function computeCoverage(view, sources) {
    sources = sources || {};
    var rendered = sources.rendered || null;
    var store = sources.store || null;
    var stream = sources.stream || null;
    var progress = sources.progress || null;
    var engine = sources.engine || null;
    // now may be undefined when the caller omits it; staleness() guards with
    // `now != null`, so no null-normalisation is needed here.
    var now = sources.now;
    // Some views (trace) stream a subtree on demand — there is no store-wide
    // denominator to render against, so the caller marks the node/edge
    // denominator as not meaningful and we treat inStore as unknown.
    var denomOk = sources.denominatorMeaningful !== false;

    var storeNodes = denomOk && store ? store.node_count : null;
    var storeEdges = denomOk && store ? store.edge_count : null;

    // status starts at 'unknown' — the honest default until a denominator or
    // an omission is known. The verdict below overrides it ONLY for the two
    // decided cases (single source of truth: no second 'unknown' assignment).
    var report = {
      view: view,
      status: 'unknown',
      nodes: axis(rendered && rendered.nodes, storeNodes),
      edges: axis(rendered && rendered.edges, storeEdges),
      files: filesAxis(engine),
      lod: lodAxis(rendered),
      staleness: staleness(stream, store, progress, now),
      degraded: [],
    };

    // Named degraded modes (§13 F2) — each is an explicit statement in the
    // output, never a silent default.
    if (!engine || engine.available === false) {
      report.degraded.push('engine-coverage-unavailable');
    }
    if (!denomOk) {
      report.degraded.push('on-demand-expansion');
    }
    if (report.staleness.building) {
      report.degraded.push('build-in-progress');
    }

    report.omissions = omissions(report, stream);

    // Verdict. An omission (of any kind) makes the view incomplete even with no
    // denominator; otherwise a known denominator with nothing missing is
    // complete; with neither, status stays 'unknown' (a false green refused).
    var haveDenominator = report.nodes.inStore != null ||
                          report.edges.inStore != null ||
                          report.files != null;
    if (report.omissions.length > 0) {
      report.status = 'incomplete';
    } else if (haveDenominator) {
      report.status = 'complete';
    }
    return report;
  }

  // Read-only test seam — same pattern as JUG._rendererTest / JUG.__wfgCtx.
  // No production path mutates through this; coverage_indicator.js calls
  // JUG.Coverage.computeCoverage directly.
  JUG.Coverage = {
    computeCoverage: computeCoverage,
    _axis: axis,
    _filesAxis: filesAxis,
    _lodAxis: lodAxis,
    _staleness: staleness,
  };
})();
