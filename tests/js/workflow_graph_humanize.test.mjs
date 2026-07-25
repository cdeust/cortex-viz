// ui/unified/js/workflow_graph_humanize.js — the layer that turns graph
// vocabulary into what a non-technical reader sees (issue #35).
//
// heatBadge in particular is a threshold ladder over memory heat. An off-by-one
// there does not throw: it just relabels memories, so a user reads "Dormant"
// for something the store considers Warm. Boundaries are exactly where such a
// change hides, so the boundaries are what these tests pin.
import { describe, it, expect, beforeAll } from "vitest";
import { loadUiScript } from "./helpers/load-ui.mjs";

let H;
beforeAll(() => {
  globalThis.window.JUG = globalThis.window.JUG || {};
  loadUiScript("ui/unified/js/workflow_graph_humanize.js");
  H = globalThis.window.JUG._wfgHumanize;
});

describe("heatBadge — threshold ladder", () => {
  it("is published on the humanize namespace", () => {
    expect(typeof H.heatBadge).toBe("function");
  });

  it("labels each band at its lower boundary", () => {
    // The >= boundaries, read straight off the source ladder.
    expect(H.heatBadge(0.7).label).toBe("Active");
    expect(H.heatBadge(0.4).label).toBe("Warm");
    expect(H.heatBadge(0.15).label).toBe("Quiet");
    expect(H.heatBadge(0).label).toBe("Dormant");
  });

  it("labels just BELOW each boundary as the band underneath", () => {
    // The half of the boundary test that actually catches `>` vs `>=`.
    expect(H.heatBadge(0.6999).label).toBe("Warm");
    expect(H.heatBadge(0.3999).label).toBe("Quiet");
    expect(H.heatBadge(0.1499).label).toBe("Dormant");
  });

  it("clamps the reported percentage into 0..100", () => {
    expect(H.heatBadge(1.5).pct).toBe(100);
    expect(H.heatBadge(-3).pct).toBe(0);
    expect(H.heatBadge(0.5).pct).toBe(50);
  });

  it("preserves the raw value alongside the rounded percentage", () => {
    const b = H.heatBadge(0.333);
    expect(b.value).toBe(0.333);
    expect(b.pct).toBe(33);
  });

  it("returns null for a non-numeric value rather than a bogus badge", () => {
    // A degraded input must be visibly absent, not silently rendered as
    // "Dormant" — that would read as a real measurement of zero heat.
    for (const v of ["", null, undefined, "abc", {}, NaN]) {
      expect(H.heatBadge(v), `heatBadge(${JSON.stringify(v)})`).toBeNull();
    }
  });

  it("accepts a numeric string, because the API returns JSON", () => {
    expect(H.heatBadge("0.8").label).toBe("Active");
  });

  it("gives each band a distinct colour (negative assertion)", () => {
    const colors = [0.9, 0.5, 0.2, 0.0].map((v) => H.heatBadge(v).color);
    expect(new Set(colors).size).toBe(4);
  });
});

describe("humanize — label mappings", () => {
  it("publishes the helpers the workflow panel calls", () => {
    for (const k of ["kindLabel", "kindIntro", "stageLabel", "stageHint",
                     "symbolTypeLabel", "edgeVerb", "prettyFieldKey",
                     "primaryClusterLabel", "heatBadge"]) {
      expect(typeof H[k], `JUG._wfgHumanize.${k}`).toBe("function");
    }
  });

  it("prettyFieldKey turns a raw key into prose without throwing on edge input", () => {
    expect(typeof H.prettyFieldKey("some_raw_key")).toBe("string");
    expect(typeof H.prettyFieldKey("")).toBe("string");
  });

  it("degrades to a string for unknown vocabulary rather than undefined", () => {
    // The panel concatenates these into markup; `undefined` would render
    // literally as the word "undefined" to the user.
    for (const fn of ["kindLabel", "stageLabel", "symbolTypeLabel", "edgeVerb"]) {
      const out = H[fn]("a-kind-that-does-not-exist");
      expect(typeof out, `${fn} on unknown input`).toBe("string");
    }
  });
});
