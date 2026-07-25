// Workflow-graph filter predicate contract (issue #35 AC#4: filter predicates,
// the state->visible-set contract) for ui/unified/js/workflow_graph_filters.js.
// Drives the pure buildPredicate(state) factory the file publishes on
// JUG._wfgFilterTest.
import { describe, it, expect, beforeAll } from 'vitest';
import { loadScript, makeJUG } from './helpers/load-globals.mjs';

let buildPredicate;

// A small graph exercising every predicate branch. ctx.byId resolves a node's
// domain label; ctx.edges backs the edge-kind filter.
const nodes = {
  domainA: { id: 'domain:alpha', kind: 'domain', label: 'Alpha' },
  domainB: { id: 'domain:beta', kind: 'domain', label: 'Beta' },
  fileA: { id: 'file:1', kind: 'file', label: 'a.py', path: 'src/a.py', domain_id: 'domain:alpha', primary_cluster: 'core' },
  fileB: { id: 'file:2', kind: 'file', label: 'b.py', path: 'src/b.py', domain_id: 'domain:beta', primary_cluster: 'io' },
  skill: { id: 'skill:s', kind: 'skill', label: 'do-thing', domain_id: 'domain:alpha' },
  symbol: { id: 'sym:1', kind: 'symbol', label: 'parseInput', domain_id: 'domain:alpha' },
  cross: { id: 'file:3', kind: 'file', label: 'shared.py', domain_id: 'domain:alpha', extra_domain_ids: ['domain:beta'] },
};
const ctx = {
  byId: {
    'domain:alpha': nodes.domainA,
    'domain:beta': nodes.domainB,
  },
  edges: [
    { source: 'sym:1', target: 'file:1', kind: 'defined_in' },
    { source: 'file:1', target: 'file:2', kind: 'imports' },
  ],
};

beforeAll(() => {
  globalThis.JUG = makeJUG({ wfgApplyFilter() {} });
  window.JUG = globalThis.JUG;
  loadScript('ui/unified/js/workflow_graph_filters.js');
  ({ buildPredicate } = window.JUG._wfgFilterTest);
});

function pred(state) {
  return buildPredicate(state);
}

describe('buildPredicate — default (all)', () => {
  // Negative-space anchor (AC#6): with no filter, every node is visible.
  it('passes every node when filter is "all" and no domain/query set', () => {
    const p = pred({ wfgFilter: 'all', domain: '', query: '' });
    for (const n of Object.values(nodes)) expect(p(n, ctx)).toBe(true);
  });
});

describe('buildPredicate — domain filter', () => {
  it('keeps nodes in the selected domain and the domain node itself', () => {
    const p = pred({ wfgFilter: 'all', domain: 'Alpha', query: '' });
    expect(p(nodes.domainA, ctx)).toBe(true);
    expect(p(nodes.fileA, ctx)).toBe(true);
  });
  it('excludes nodes of a different domain', () => {
    const p = pred({ wfgFilter: 'all', domain: 'Alpha', query: '' });
    expect(p(nodes.fileB, ctx)).toBe(false);
    expect(p(nodes.domainB, ctx)).toBe(false);
  });
  it('keeps a node whose extra_domain_ids includes the selected domain', () => {
    const p = pred({ wfgFilter: 'all', domain: 'Beta', query: '' });
    expect(p(nodes.cross, ctx)).toBe(true); // primary Alpha, extra Beta
  });

  it('derives a label-less domain node\'s name from its id (strips "domain:")', () => {
    // Kills the n.id.replace('domain:','') fallback mutants: a domain node
    // with no label must still match when the selection equals its id tail.
    const bare = { id: 'domain:gamma', kind: 'domain' };
    const p = pred({ wfgFilter: 'all', domain: 'gamma', query: '' });
    expect(p(bare, { byId: {} })).toBe(true);
    // and it must NOT match a different domain selection
    const p2 = pred({ wfgFilter: 'all', domain: 'delta', query: '' });
    expect(p2(bare, { byId: {} })).toBe(false);
  });

  it('excludes a node whose domain_id is unresolved (dom resolves to "")', () => {
    // Kills the else-branch label-lookup mutants: an orphan domain_id yields an
    // empty domain name, which cannot equal a real selected domain.
    const orphan = { id: 'file:orphan', kind: 'file', domain_id: 'domain:missing' };
    const p = pred({ wfgFilter: 'all', domain: 'Alpha', query: '' });
    expect(p(orphan, { byId: {} })).toBe(false);
  });
});

describe('buildPredicate — layer / kind / file / edge / cross-domain', () => {
  it('L1 layer keeps skills but not files', () => {
    const p = pred({ wfgFilter: 'L1', domain: '', query: '' });
    expect(p(nodes.skill, ctx)).toBe(true);
    expect(p(nodes.fileA, ctx)).toBe(false);
  });
  it('kind:file keeps only file nodes', () => {
    const p = pred({ wfgFilter: 'kind:file', domain: '', query: '' });
    expect(p(nodes.fileA, ctx)).toBe(true);
    expect(p(nodes.skill, ctx)).toBe(false);
  });
  it('file:<cluster> keeps files of that cluster + domain anchors', () => {
    const p = pred({ wfgFilter: 'file:core', domain: '', query: '' });
    expect(p(nodes.fileA, ctx)).toBe(true); // primary_cluster core
    expect(p(nodes.fileB, ctx)).toBe(false); // cluster io
    expect(p(nodes.domainA, ctx)).toBe(true); // anchor kept
  });
  it('edge:<kind> keeps only nodes touching that edge kind (+ domain anchors)', () => {
    const p = pred({ wfgFilter: 'edge:defined_in', domain: '', query: '' });
    expect(p(nodes.symbol, ctx)).toBe(true); // sym:1 -> file:1 defined_in
    expect(p(nodes.fileA, ctx)).toBe(true); // target of defined_in
    expect(p(nodes.skill, ctx)).toBe(false); // untouched by defined_in
    expect(p(nodes.domainA, ctx)).toBe(true); // anchor kept
  });

  it('edge:<kind> resolves object edge endpoints (not just id strings)', () => {
    // Kills the object/typeof mutants in rebuildEdgeHits: endpoints may arrive
    // as force-graph-hydrated {id} objects.
    const objCtx = {
      byId: ctx.byId,
      edges: [{ source: { id: 'sym:1' }, target: { id: 'file:1' }, kind: 'defined_in' }],
    };
    const p = pred({ wfgFilter: 'edge:defined_in', domain: '', query: '' });
    expect(p(nodes.symbol, objCtx)).toBe(true);
    expect(p(nodes.fileA, objCtx)).toBe(true);
    expect(p(nodes.skill, objCtx)).toBe(false);
  });

  it('edge:<unknown-kind> applies no edge filter (AST_EDGE_KINDS miss keeps node)', () => {
    // Kills the "AST_EDGE_KINDS[ek]" guard mutant: an edge kind not in the AST
    // set must leave non-domain nodes untouched, not silently drop them.
    const p = pred({ wfgFilter: 'edge:not_a_real_kind', domain: '', query: '' });
    expect(p(nodes.skill, ctx)).toBe(true);
  });

  it('edge-hit cache rebuilds when the edge set changes size (same predicate)', () => {
    // Kills the cache-key mutants (edgeKind + '@' + edge-count): reusing one
    // predicate across a grown edge set must re-scan, not serve a stale set.
    const p = pred({ wfgFilter: 'edge:imports', domain: '', query: '' });
    const ctx1 = { byId: ctx.byId, edges: [] };
    expect(p(nodes.fileA, ctx1)).toBe(false); // no imports edge yet
    const ctx2 = {
      byId: ctx.byId,
      edges: [{ source: 'file:1', target: 'file:2', kind: 'imports' }],
    };
    expect(p(nodes.fileA, ctx2)).toBe(true); // rebuilt: now a hit
  });

  it('file:<cluster> excludes a non-file, non-domain node', () => {
    // Kills the "n.kind !== 'file'" branch mutant.
    const p = pred({ wfgFilter: 'file:core', domain: '', query: '' });
    expect(p(nodes.skill, ctx)).toBe(false);
  });

  it('file:<cluster> is scoped to file nodes: a non-file with a matching cluster is still excluded', () => {
    // Kills the "n.kind !== 'file' || ..." → "false || ..." mutant: only FILE
    // nodes are cluster-filtered; a non-file that happens to carry
    // primary_cluster must not sneak through on the cluster match alone.
    const impostor = { id: 'skill:x', kind: 'skill', primary_cluster: 'core' };
    const p = pred({ wfgFilter: 'file:core', domain: '', query: '' });
    expect(p(impostor, ctx)).toBe(false);
  });

  it('an unrecognised main-filter value applies no filter (keeps the node)', () => {
    // Kills the "f === 'cross-domain'" → "true" mutant: a filter value that
    // matches none of the known prefixes must fall through and keep the node,
    // not accidentally impose the cross-domain rule.
    const p = pred({ wfgFilter: 'zzz-bogus-filter', domain: '', query: '' });
    expect(p(nodes.skill, ctx)).toBe(true);
  });
  it('cross-domain keeps only nodes with extra_domain_ids (+ anchors)', () => {
    const p = pred({ wfgFilter: 'cross-domain', domain: '', query: '' });
    expect(p(nodes.cross, ctx)).toBe(true);
    expect(p(nodes.fileA, ctx)).toBe(false);
    expect(p(nodes.domainA, ctx)).toBe(true);
  });
});

describe('buildPredicate — text query', () => {
  it('matches case-insensitively across label/path/id', () => {
    const p = pred({ wfgFilter: 'all', domain: '', query: 'PARSE' });
    expect(p(nodes.symbol, ctx)).toBe(true); // label parseInput
  });
  it('excludes nodes that do not contain the query anywhere', () => {
    const p = pred({ wfgFilter: 'all', domain: '', query: 'zzz-nomatch' });
    expect(p(nodes.symbol, ctx)).toBe(false);
  });

  // Each field of the search haystack is load-bearing: a query that matches
  // ONLY via that field must still hit. These kill the mutants that drop an
  // individual field term (label / path / body / id) from the concatenation.
  it('matches via the path field alone', () => {
    const n = { id: 'x:1', kind: 'file', label: 'zzz', path: 'src/unique_path.py' };
    expect(pred({ wfgFilter: 'all', domain: '', query: 'unique_path' })(n, ctx)).toBe(true);
  });
  it('matches via the id field alone', () => {
    const n = { id: 'uniqueid-777', kind: 'file', label: 'zzz' };
    expect(pred({ wfgFilter: 'all', domain: '', query: 'uniqueid-777' })(n, ctx)).toBe(true);
  });
  it('matches via the body field alone', () => {
    const n = { id: 'x:2', kind: 'file', label: 'zzz', body: 'a unique_body token' };
    expect(pred({ wfgFilter: 'all', domain: '', query: 'unique_body' })(n, ctx)).toBe(true);
  });
  it('matches via the label field alone', () => {
    const n = { id: 'x:3', kind: 'file', label: 'unique_label_here' };
    expect(pred({ wfgFilter: 'all', domain: '', query: 'unique_label_here' })(n, ctx)).toBe(true);
  });
});

describe('buildPredicate — isolation', () => {
  // Each predicate owns its edge-hit cache; two predicates over different edge
  // kinds must not cross-contaminate.
  it('two predicates keep independent edge-hit caches', () => {
    const pDefined = pred({ wfgFilter: 'edge:defined_in', domain: '', query: '' });
    const pImports = pred({ wfgFilter: 'edge:imports', domain: '', query: '' });
    expect(pDefined(nodes.symbol, ctx)).toBe(true); // defined_in touches sym:1
    expect(pImports(nodes.symbol, ctx)).toBe(false); // imports does not
  });
});
