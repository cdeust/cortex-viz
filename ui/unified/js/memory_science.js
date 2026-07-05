// Cortex — Memory Science components
// Shared measurement primitives for the memory card exhibit (Knowledge +
// Board) and the right-dock detail inspector. Renders the AI Architect
// design-system vocabulary verbatim (aia-heat / aia-ledger / aia-chip /
// aia-footnotes) — no invented widgets, no plain-English explainer boxes.
//
// Authority: AI Architect Design System / cards/data-memory-card.html
// (spec DD-01, "the memory card") for the card-level feel/meaning/meters,
// and da-anatomy-spec.md §2.12 (Ledger) for the detail-panel PROPERTIES.
//
// Conventions:
//   * Reads fields in both snake_case (native) and camelCase (legacy
//     alias) so the layer stays robust across schema drift.
//   * Falls back silently when a field is absent — memories written
//     before a given instrument existed remain renderable.
//   * Every displayed number is the measured value, never rounded for
//     effect (zetetic discipline, da-anatomy-spec.md §0.8).
(function () {
  var JUG = window.JUG = window.JUG || {};

  function pick(mem, keys) {
    for (var i = 0; i < keys.length; i++) {
      var v = mem[keys[i]];
      if (v !== undefined && v !== null) return v;
    }
    return undefined;
  }

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  function fmtNum(v) {
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    if (Math.abs(n) >= 100) return n.toFixed(0);
    if (Math.abs(n) >= 10)  return n.toFixed(1);
    return n.toFixed(2);
  }

  function fmtAge(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    if (isNaN(t)) return null;
    var delta = (Date.now() - t) / 1000;
    if (delta < 60) return Math.round(delta) + "s";
    if (delta < 3600) return Math.round(delta / 60) + "m";
    if (delta < 86400) return Math.round(delta / 3600) + "h";
    if (delta < 86400 * 30) return Math.round(delta / 86400) + "d";
    return Math.round(delta / 86400 / 30) + "mo";
  }

  // Drop tool-capture boilerplate tags to find the tags that carry actual
  // meaning (schema/topic words), used as a Meaning-quote fallback.
  function meaningfulTags(tags) {
    return (tags || []).filter(function (t) {
      var s = String(t).toLowerCase();
      return s !== "auto-captured" && s.indexOf("_") !== 0
        && s.indexOf("tool:") !== 0 && s.indexOf("project:") !== 0;
    });
  }

  // First non-boilerplate line of the body — the shortest textual handle
  // on what the memory is about (used by the Meaning quote).
  function extractGist(body) {
    if (!body) return "";
    var lines = String(body).split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i].trim();
      if (!ln) continue;
      if (ln.indexOf("# Tool:") === 0) continue;
      if (ln.indexOf("**Command:**") === 0) continue;
      if (ln.indexOf("**File:**") === 0) continue;
      if (ln.indexOf("**Read:**") === 0) continue;
      if (ln.indexOf("**Output:**") === 0) continue;
      if (ln.indexOf("```") === 0) continue;
      if (ln.length < 8) continue;
      return ln.length > 160 ? ln.slice(0, 160) + "…" : ln;
    }
    return "";
  }

  // ── aia-heat block (label + value + full-track gradient fill) ───────
  // source: AI Architect DS components/verification/HeatBar.jsx — the
  // ONE licensed gradient in the system (a data scale, not decoration).
  function buildHeatBar(label, value) {
    var v = clamp01(Number(value) || 0);
    var wrap = document.createElement("div");
    wrap.className = "aia-heat";
    var meta = document.createElement("div");
    meta.className = "aia-heat__meta";
    var lab = document.createElement("span");
    lab.textContent = label;
    meta.appendChild(lab);
    var val = document.createElement("span");
    val.className = "aia-heat__val";
    val.textContent = v.toFixed(3);
    meta.appendChild(val);
    wrap.appendChild(meta);
    var track = document.createElement("div");
    track.className = "aia-heat__track";
    var fill = document.createElement("div");
    fill.className = "aia-heat__fill";
    fill.style.setProperty("--heat-scale", Math.max(v, 0.001));
    fill.style.width = (v * 100).toFixed(1) + "%";
    track.appendChild(fill);
    wrap.appendChild(track);
    return wrap;
  }

  // Inline bar for a ledger value slot: track+fill only, mono value at
  // the end. Visually consistent with aia-heat but without the repeated
  // label row (the ledger key column already carries the label).
  function buildInlineBar(value, lo, hi, signed) {
    var n = Number(value);
    var pct = isFinite(n) ? clamp01((n - lo) / (hi - lo)) : 0;
    var holder = document.createElement("span");
    holder.className = "ms-ledger-bar";
    var track = document.createElement("span");
    track.className = "aia-heat__track ms-ledger-track";
    var fill = document.createElement("span");
    fill.className = "aia-heat__fill";
    fill.style.setProperty("--heat-scale", Math.max(pct, 0.001));
    fill.style.width = (pct * 100).toFixed(0) + "%";
    track.appendChild(fill);
    holder.appendChild(track);
    var num = document.createElement("span");
    num.className = "ms-ledger-val";
    num.textContent = (signed && n > 0 ? "+" : "") + fmtNum(n);
    holder.appendChild(num);
    return holder;
  }

  // Fixed-order meter row (DD-01 ".meter"): label + track+fill, amber
  // ink, no numeric readout — matches the blueprint literally (the
  // number lives in the ledger/detail panel, not the card meter).
  function buildMeterRow(label, value, lo, hi) {
    var row = document.createElement("div");
    row.className = "ms-meter";
    var k = document.createElement("span");
    k.className = "ms-meter-k";
    k.textContent = label;
    row.appendChild(k);
    var track = document.createElement("span");
    track.className = "ms-meter-track";
    var fill = document.createElement("span");
    fill.className = "ms-meter-fill";
    var n = Number(value);
    var pct = isFinite(n) ? clamp01((n - lo) / (hi - lo)) : 0;
    fill.style.width = (pct * 100).toFixed(0) + "%";
    track.appendChild(fill);
    row.appendChild(track);
    return row;
  }

  function appendKv(wrap, k, v) {
    var row = document.createElement("div");
    row.className = "ms-kv";
    var kEl = document.createElement("span");
    kEl.textContent = k;
    row.appendChild(kEl);
    var vEl = document.createElement("b");
    vEl.textContent = v;
    row.appendChild(vEl);
    wrap.appendChild(row);
  }

  // ── Feeling line — dot + emotion word + signed valence + arousal,
  // mono, never colour-only (da-anatomy-spec.md §4: "Feeling" row).
  // The dot is chrome-neutral: emotion category is not itself a P-01
  // data channel here (heat/stage/valence are); it is a mono word.
  function buildEmotionChip(mem) {
    var emo = pick(mem, ["dominant_emotion", "dominantEmotion", "emotion"]);
    var valence = pick(mem, ["emotional_valence", "emotionalValence"]);
    var arousal = pick(mem, ["arousal"]);
    if (!emo && valence === undefined && arousal === undefined) return null;

    var chip = document.createElement("div");
    chip.className = "ms-emotion";

    var dot = document.createElement("span");
    dot.className = "ms-emotion-dot";
    chip.appendChild(dot);

    var parts = [String(emo || "neutral").toLowerCase()];
    if (valence !== undefined && valence !== null) {
      var v = Number(valence);
      if (isFinite(v)) parts.push((v >= 0 ? "↑" : "↓") + " " + fmtNum(Math.abs(v)));
    }
    if (arousal !== undefined && arousal !== null) {
      var a = Number(arousal);
      if (isFinite(a)) parts.push("↑ " + fmtNum(a));
    }
    var text = document.createElement("span");
    text.className = "ms-emotion-text";
    text.textContent = parts.join(" · ");
    chip.appendChild(text);
    return chip;
  }

  // ── Meaning — kind + verbatim excerpt in one italic mono quote line
  // (da-anatomy-spec.md §4 / DD-01 ledger row "Meaning"). No icon grid,
  // no plain-English gloss — the quote IS the content, verbatim.
  function buildMeaningSection(mem) {
    var store = pick(mem, ["store_type", "storeType"]);
    var body = mem.body || mem.content || mem.label || "";
    var gist = extractGist(body);
    var fallback = meaningfulTags(mem.tags)[0];
    var excerpt = gist || fallback;
    if (!excerpt) return null;

    var kind = store === "semantic" ? "Knowledge" : "Experience";

    var section = document.createElement("div");
    section.className = "ms-meaning";
    var header = document.createElement("div");
    header.className = "ms-meaning-header";
    header.textContent = "Meaning";
    section.appendChild(header);
    var quote = document.createElement("div");
    quote.className = "ms-meaning-quote";
    quote.textContent = "“ " + kind + " · " + excerpt + " ”";
    section.appendChild(quote);
    return section;
  }

  // ── Card-level measurement block ────────────────────────────────────
  //   "compact" (Board kb-card)   — single Heat aia-heat footer (the
  //                                 SATURATION exhibit convention already
  //                                 verified on the Knowledge card).
  //   "full"    (Knowledge kv-card) — DD-01's four fixed meters (Heat /
  //                                 Importance / Valence / Arousal) plus
  //                                 the three provenance rows (Emotion·
  //                                 Store, Accessed, Created).
  function buildSciencePanel(mem, variant) {
    if (!mem) return null;
    var heat = pick(mem, ["heat"]);

    if (variant === "compact") {
      if (heat === undefined || heat === null) return null;
      var footer = document.createElement("div");
      footer.className = "ms-heat-footer";
      footer.appendChild(buildHeatBar("Heat", heat));
      return footer;
    }

    var wrap = document.createElement("div");
    wrap.className = "ms-panel-full";

    var meters = document.createElement("div");
    meters.className = "ms-meters";
    meters.appendChild(buildMeterRow("Heat", heat, 0, 1));
    meters.appendChild(buildMeterRow("Importance", pick(mem, ["importance"]), 0, 1));
    meters.appendChild(buildMeterRow("Valence", pick(mem, ["emotional_valence", "emotionalValence"]), -1, 1));
    meters.appendChild(buildMeterRow("Arousal", pick(mem, ["arousal"]), 0, 1));
    wrap.appendChild(meters);

    var prov = document.createElement("div");
    prov.className = "ms-provenance";
    var store = pick(mem, ["store_type", "storeType"]);
    var emo = pick(mem, ["dominant_emotion", "dominantEmotion"]) || "neutral";
    var storeLabel = store === "semantic" ? "Semantic" : "Episodic";
    appendKv(prov, "Emotion", emo + " · " + storeLabel);
    var accessedAge = fmtAge(pick(mem, ["last_accessed", "lastAccessed"]));
    if (accessedAge) appendKv(prov, "Accessed", accessedAge + " ago");
    var createdAge = fmtAge(pick(mem, ["created_at", "createdAt"]));
    if (createdAge) appendKv(prov, "Created", createdAge + " ago");
    if (prov.childElementCount) wrap.appendChild(prov);

    return wrap.childElementCount ? wrap : null;
  }

  // ── Detail-panel PROPERTIES ledger ──────────────────────────────────
  // Replaces the old plain-English "Scientific measurements" explainer
  // boxes. Ruled ledger (aia-ledger), continuous metrics get an inline
  // HeatBar-style fill + exact mono value; discrete fields are plain
  // mono/text. TAGS render as outline chips; an honest footnote states
  // capture provenance when the memory is auto-captured.
  function buildExplainedPanel(mem) {
    if (!mem) return null;

    var stage = mem.stage || mem.consolidation_stage || mem.consolidationStage;
    var rows = [];
    if (stage) rows.push({ k: "Stage", v: String(stage) });
    var heat = pick(mem, ["heat"]);
    if (heat != null) rows.push({ k: "Heat", bar: true, v: heat, lo: 0, hi: 1 });
    var imp = pick(mem, ["importance"]);
    if (imp != null) rows.push({ k: "Importance", bar: true, v: imp, lo: 0, hi: 1 });
    var val = pick(mem, ["emotional_valence", "emotionalValence"]);
    if (val != null) rows.push({ k: "Valence", bar: true, v: val, lo: -1, hi: 1, signed: true });
    var aro = pick(mem, ["arousal"]);
    if (aro != null) rows.push({ k: "Arousal", bar: true, v: aro, lo: 0, hi: 1 });
    var emo = pick(mem, ["dominant_emotion", "dominantEmotion"]);
    if (emo) rows.push({ k: "Emotion", v: String(emo) });
    var created = fmtAge(pick(mem, ["created_at", "createdAt"]));
    if (created) rows.push({ k: "Created", v: created + " ago" });
    var accessed = fmtAge(pick(mem, ["last_accessed", "lastAccessed"]));
    if (accessed) rows.push({ k: "Accessed", v: accessed + " ago" });
    if (!rows.length) return null;

    var container = document.createElement("div");
    container.className = "ms-properties-wrap";

    var ledger = document.createElement("div");
    ledger.className = "aia-ledger ms-properties";
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "aia-ledger__row";
      var k = document.createElement("span");
      k.className = "aia-ledger__k";
      k.textContent = r.k;
      row.appendChild(k);
      var v = document.createElement("span");
      v.className = "aia-ledger__v";
      if (r.bar) v.appendChild(buildInlineBar(r.v, r.lo, r.hi, r.signed));
      else v.textContent = r.v;
      row.appendChild(v);
      ledger.appendChild(row);
    });
    container.appendChild(ledger);

    var tags = mem.tags || [];
    if (tags.length) {
      var tagsWrap = document.createElement("div");
      tagsWrap.className = "ms-tags";
      tags.slice(0, 8).forEach(function (t) {
        var chip = document.createElement("span");
        chip.className = "aia-chip ms-tag-chip";
        chip.textContent = String(t);
        tagsWrap.appendChild(chip);
      });
      container.appendChild(tagsWrap);
    }

    var isAutoCaptured = tags.some(function (t) { return String(t).toLowerCase() === "auto-captured"; });
    if (isAutoCaptured) {
      var toolTag = tags.filter(function (t) { return String(t).toLowerCase().indexOf("tool:") === 0; })[0];
      var fn = document.createElement("div");
      fn.className = "aia-footnotes";
      var item = document.createElement("div");
      item.className = "aia-footnotes__item";
      item.textContent = "· auto-captured" + (toolTag ? " " + toolTag : "");
      fn.appendChild(item);
      container.appendChild(fn);
    }

    return container;
  }

  JUG._memSci = {
    buildSciencePanel: buildSciencePanel,
    buildEmotionChip: buildEmotionChip,
    buildMeaningSection: buildMeaningSection,
    buildExplainedPanel: buildExplainedPanel,
  };
})();
