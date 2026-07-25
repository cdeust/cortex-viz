// ui/unified/js/detail_format.js — label cleaning and HTML escaping (issue #35).
//
// `esc()` is the ONLY thing standing between memory/wiki content and
// `innerHTML`: detail_panel.js builds markup by string concatenation
// (`'<span class="conn-label">' + JUG._fmt.esc(name) + ...`), so a gap in this
// function is stored-XSS in a panel that renders whatever the memory store
// holds — including text Cortex ingested from a codebase or a web page. It had
// no test.
import { describe, it, expect, beforeAll } from "vitest";
import { loadUiScript } from "./helpers/load-ui.mjs";

let fmt;
beforeAll(() => {
  globalThis.window.JUG = globalThis.window.JUG || {};
  loadUiScript("ui/unified/js/detail_format.js");
  fmt = globalThis.window.JUG._fmt;
});

describe("esc — the innerHTML boundary", () => {
  it("neutralises a script tag", () => {
    expect(fmt.esc('<script>alert(1)</script>')).toBe(
      "&lt;script&gt;alert(1)&lt;/script&gt;",
    );
  });

  it("escapes every character that can break out of an attribute", () => {
    // detail_panel.js interpolates into BOTH text and quoted attributes
    // (title="..."), so quotes matter as much as angle brackets.
    expect(fmt.esc(`" onload="x`)).toBe("&quot; onload=&quot;x");
    expect(fmt.esc("' onload='x")).toBe("&#x27; onload=&#x27;x");
  });

  it("escapes ampersands FIRST so an escape cannot be double-decoded", () => {
    // Order is load-bearing: replacing < before & would turn "&lt;" into
    // "&amp;lt;" only if & ran later — and running & later on already-emitted
    // entities would corrupt them. This pins the ordering.
    expect(fmt.esc("&lt;script&gt;")).toBe("&amp;lt;script&amp;gt;");
    expect(fmt.esc("&")).toBe("&amp;");
  });

  it("escapes every occurrence, not just the first", () => {
    expect(fmt.esc("<<>>")).toBe("&lt;&lt;&gt;&gt;");
  });

  it("coerces non-strings rather than throwing", () => {
    expect(fmt.esc(42)).toBe("42");
    expect(fmt.esc({})).toBe("[object Object]");
  });

  it("maps falsy input to the empty string", () => {
    for (const v of ["", null, undefined, 0, false, NaN]) {
      expect(fmt.esc(v)).toBe("");
    }
  });

  it("leaves ordinary prose untouched (negative assertion)", () => {
    const plain = "normalizePaymentAmount handles rounding";
    expect(fmt.esc(plain)).toBe(plain);
  });
});

describe("cleanLabel / fullLabel — markdown stripping", () => {
  it("strips heading markers, bold, code spans and link syntax", () => {
    expect(fmt.fullLabel("## **Bold** `code` [text](http://x)")).toBe("Bold code text");
  });

  it("collapses runs of whitespace", () => {
    expect(fmt.fullLabel("a   \n  b")).toBe("a b");
  });

  it("keeps the link TEXT and drops the target", () => {
    expect(fmt.fullLabel("[label](https://example.com/very/long)")).toBe("label");
  });

  it("returns empty for falsy input", () => {
    expect(fmt.fullLabel("")).toBe("");
    expect(fmt.cleanLabel(null)).toBe("");
  });

  it("does NOT escape — escaping is esc()'s job, not the label cleaner's", () => {
    // A negative assertion that pins the separation of concerns: if
    // cleanLabel started escaping, callers that also esc() would
    // double-escape and users would see "&amp;lt;" in the panel.
    expect(fmt.fullLabel("<b>x</b>")).toBe("<b>x</b>");
  });
});

describe("JUG._fmt — published surface", () => {
  it("exposes exactly the helpers detail_panel.js calls", () => {
    // detail_panel.js reaches for these by name at render time; a rename that
    // compiles fine here would blow up only when a user opens the panel.
    for (const k of ["cleanLabel", "fullLabel", "header", "quality", "gauges",
                     "content", "tags", "badges", "bioSection", "gauge", "esc"]) {
      expect(typeof fmt[k], `JUG._fmt.${k}`).toBe("function");
    }
  });
});

// NOTE: colorForPct is deliberately NOT tested — it is module-private and not
// reachable through JUG._fmt. Testing it would mean widening the module's
// public surface to suit the test, which is the tail wagging the dog.
