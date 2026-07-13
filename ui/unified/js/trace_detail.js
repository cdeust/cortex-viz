// Cortex — Trace detail-panel content (single panel, expandable sections).
//
// Renders trace nodes (domain/session/action/prompt/file) INTO the one
// #detail-content panel that detail_panel.js owns. Sections use native
// <details>/<summary> and lazy-fetch their data on first expand:
//   file    → Git diff, AST symbols, Impact (opens the flow diagram)
//   session → Conversation transcript, overview
//   action  → tool input + causal (its files / prompt)
//   prompt  → full text
// Exposed as JUG._traceDetail.{build, wire}.
(function () {
  'use strict';

  // G9/G5 (design gate): tool + kind colour resolved LIVE from the design
  // system, never a static hex table — the previous tables baked the SAME
  // paper-illegible hex as trace.js's old TOOL_COLOR (Edit 1.44:1 etc.) and,
  // for domain/session, a bright gold (#FCD34D, ~1:1 on cream) that made the
  // '● SESSION' chip unreadable on paper. Tool families map to the DS
  // --tool-* tokens (ui/shared/tokens/surfaces.css); kind badges use the
  // surface-aware -ink aliases (--warn-ink for the hub family, matching
  // workflow_graph.js's KIND_TOKEN.domain/session) so text/dot/border stay
  // legible on both surfaces.
  var TOOL_DOT_TOKEN = {
    Read: '--tool-read', NotebookRead: '--tool-read',
    Grep: '--tool-search', Glob: '--tool-search',
    Edit: '--tool-edit', MultiEdit: '--tool-edit', NotebookEdit: '--tool-edit',
    Write: '--tool-write', Bash: '--tool-exec', Task: '--tool-agent', Agent: '--tool-agent',
    WebFetch: '--tool-web', WebSearch: '--tool-web',
  };
  var KIND_TOKEN = {
    domain: '--warn-ink', session: '--warn-ink', prompt: '--stage-early',
    action: '--tool-read', file: '--tool-read',
  };
  function _resolveToken(token) {
    if (!token) return null;
    if (window.CortexPalette) return window.CortexPalette.hex(token);
    var v = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
    return v || null;
  }

  function esc(s) {
    // Full HTML escape incl. quotes → safe in both text and quoted-attribute
    // contexts (data-path="...", data-sid="..."). Quote escapes prevent
    // attribute breakout (CodeQL js/incomplete-sanitization).
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function shortStr(t, n) {
    n = n || 60;
    var s = String(t == null ? '' : t).replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  function color(node) {
    var k = node.kind || node.type;
    if (k === 'action' && node.tool) {
      var toolC = _resolveToken(TOOL_DOT_TOKEN[node.tool]);
      if (toolC) return toolC;
    }
    return _resolveToken(KIND_TOKEN[k]) || _resolveToken('--info-ink');
  }
  function kindLabel(node) {
    var k = node.kind || node.type;
    if (k === 'action') return node.tool || 'action';
    return k;
  }
  function header(node) {
    var c = color(node);
    return '<div class="node-badge" style="background:' + c + '14;border-color:'
      + c + '40;color:' + c + '">'
      + '<span style="width:5px;height:5px;border-radius:50%;background:' + c
      + ';display:inline-block"></span> ' + esc(kindLabel(node)) + '</div>'
      + '<h2>' + esc(node.label || node.id || '') + '</h2>';
  }
  // A collapsible section. ``open`` expands by default; ``lazy`` marks it
  // for wire() to fetch its body on first expand.
  function section(title, id, bodyHtml, opts) {
    opts = opts || {};
    return '<details class="td-sec"' + (opts.open ? ' open' : '')
      + (opts.lazy ? ' data-lazy="' + esc(opts.lazy) + '"' : '')
      + (opts.path ? ' data-path="' + esc(opts.path) + '"' : '')
      + (opts.sid ? ' data-sid="' + esc(opts.sid) + '"' : '') + '>'
      + '<summary>' + esc(title) + '</summary>'
      + '<div class="td-sec-body" id="' + id + '">' + (bodyHtml || '') + '</div>'
      + '</details>';
  }

  // ── build(node) → HTML for #detail-content ──
  function build(node) {
    var k = node.kind || node.type;
    var h = header(node);
    if (k === 'domain') {
      h += '<div class="conn-item">' + (node.session_count || '?')
        + ' sessions · click the hub to expand them on the graph</div>';
      return h;
    }
    if (k === 'prompt') {
      h += section('User message', 'td-prompt',
        '<div class="detail-text">' + esc(node.full || node.label || '') + '</div>',
        { open: true });
      if (node.ts) h += '<div class="conn-item" style="color:var(--text-dim)">' + esc(node.ts) + '</div>';
      return h;
    }
    if (k === 'action') {
      var tool = node.tool || 'action';
      h += '<div class="conn-item">Tool: ' + esc(tool) + '</div>'
        + (node.ts ? '<div class="conn-item" style="color:var(--text-dim)">' + esc(node.ts) + '</div>' : '');
      h += section('Causal context', 'td-action-causal',
        '<div class="conn-item" style="color:var(--text-dim)">Part of session '
        + esc((node.session_id || '').slice(0, 8))
        + '. Expand the session on the graph to see the full chain.</div>',
        { open: true });
      return h;
    }
    if (k === 'session') {
      var sid = node.session_id || String(node.id).replace(/^session:/, '');
      h += '<div class="conn-item">Actions: ' + (node.action_count != null ? node.action_count : '?') + '</div>'
        + (node.git_branch ? '<div class="conn-item">Branch: ' + esc(node.git_branch) + '</div>' : '')
        + (node.started_at ? '<div class="conn-item" style="color:var(--text-dim)">Started ' + esc(node.started_at) + '</div>' : '');
      h += section('Conversation', 'td-convo', 'loading transcript…',
        { open: true, lazy: 'convo', sid: sid });
      return h;
    }
    if (k === 'file') {
      // precondition: node.path is the ONLY trusted source for the value
      // sent to /api/trace/file; the id-stripped hash (String(id).replace)
      // is never a valid path and must never be sent to the API. When
      // node.path is absent, wire() resolves it via /api/graph/node?id=
      // before any lazy section fetches — see _resolveFilePath.
      var known = node.path || '';
      h += '<div class="conn-item" style="color:var(--text-dim)" id="td-file-path">'
        + esc(known || 'Resolving path…') + '</div>';
      h += section('Git diff', 'td-git', 'loading diff…', { open: true, lazy: 'file', path: known });
      h += section('Versions (git history)', 'td-versions', 'loading history…', { lazy: 'file', path: known });
      h += section('AST symbols', 'td-ast', '', { lazy: 'ast', path: known });
      h += section('Impact / dependencies', 'td-impact',
        '<div class="conn-item" style="color:var(--text-dim)">Open the impact diagram →</div>',
        { path: known });
      return h;
    }
    if (k === 'discussion') {
      // The assistant turn between actions. `full` is the verbatim message
      // (up to 4000 chars from session_trace.build_chain); show it in full,
      // plus a jump to the whole conversation — parity with how the galaxy
      // surfaces a node's primary content.
      h += section('Discussion', 'td-disc',
        '<div class="detail-text">' + esc(node.full || node.label || '') + '</div>',
        { open: true });
      if (node.ts) h += '<div class="conn-item" style="color:var(--text-dim)">' + esc(node.ts) + '</div>';
      var dsid = node.session_id || '';
      if (dsid) h += '<button class="disc-view-btn" data-session-id="' + esc(dsid)
        + '">View Full Conversation</button>';
      return h;
    }
    if (k === 'memory') {
      // A Cortex remember/recall op fired during the session. `full` carries
      // the remembered content / recalled query; `label` is prefixed with the
      // op ("remember · …" / "recall · …"). Show the op + the full content.
      var mop = /^recall/i.test(node.label || '') ? 'recall' : 'remember';
      h += '<div class="conn-item">Operation: ' + esc(mop) + '</div>';
      h += section(mop === 'recall' ? 'Recalled query' : 'Remembered content', 'td-mem',
        '<div class="detail-text">' + esc(node.full || node.label || '') + '</div>',
        { open: true });
      if (node.ts) h += '<div class="conn-item" style="color:var(--text-dim)">' + esc(node.ts) + '</div>';
      var msid = node.session_id || '';
      if (msid) h += '<button class="disc-view-btn" data-session-id="' + esc(msid)
        + '">View Full Conversation</button>';
      return h;
    }
    return h;
  }

  // ── wire(content, node) → lazy fetch on expand + impact button ──
  function wire(content, node) {
    var kind = node.kind || node.type;
    if (kind === 'file') {
      _wireFile(content, node);
    } else {
      _wireConvoSections(content);
    }
    _wireConvoModal(content);
  }

  // Session nodes: lazy-fetch the transcript on first expand of the
  // 'Conversation' section. Independent of file-path resolution.
  function _wireConvoSections(content) {
    var lazyDone = {};
    content.querySelectorAll('details[data-lazy="convo"]').forEach(function (det) {
      var run = function () {
        var key = det.querySelector('.td-sec-body').id;
        if (lazyDone[key]) return; lazyDone[key] = true;
        _loadConvo(det.getAttribute('data-sid'));
      };
      if (det.open) run();
      det.addEventListener('toggle', function () { if (det.open) run(); });
    });
  }

  // File nodes: node.path is the only trusted value. If absent, resolve it
  // once via /api/graph/node?id=<id> before wiring any lazy section — never
  // send the id-stripped hash to /api/trace/file.
  // precondition: node.id is set (build() already required it for the DOM).
  // postcondition: either every file section is wired to fetch with a real
  // path, or every section shows "Path unknown for this node" and issues
  // zero /api/trace/file calls.
  function _resolveFilePath(node) {
    if (node.path) return Promise.resolve(node.path);
    if (!node.id) return Promise.resolve(null);
    return _fetch('/api/graph/node?id=' + encodeURIComponent(node.id))
      .then(function (rec) { return (rec && rec.record && rec.record.path) || null; })
      .catch(function () { return null; });
  }

  function _wireFile(content, node) {
    _resolveFilePath(node).then(function (path) {
      var pathSlot = content.querySelector('#td-file-path');
      if (pathSlot) pathSlot.textContent = path || 'Path unknown for this node';
      if (!path) { _markFileSectionsUnknown(content); return; }
      _wireFileLazySections(content, path);
      _wireImpactButton(content, path);
    });
  }

  // Git/versions ('file') and AST ('ast') are decoupled fetches with
  // separate caches — AST only fetches on its own first expand, never
  // bundled into the fast git+versions request.
  function _wireFileLazySections(content, path) {
    var lazyDone = {};
    content.querySelectorAll('details[data-lazy]').forEach(function (det) {
      var kind = det.getAttribute('data-lazy');
      if (kind !== 'file' && kind !== 'ast') return;
      var run = function () {
        var key = det.querySelector('.td-sec-body').id;
        if (lazyDone[key]) return; lazyDone[key] = true;
        if (kind === 'ast') _loadAst(path); else _loadFile(path);
      };
      if (det.open) run();
      det.addEventListener('toggle', function () { if (det.open) run(); });
    });
  }

  function _markFileSectionsUnknown(content) {
    var msg = 'Path unknown for this node';
    ['td-git', 'td-versions', 'td-ast'].forEach(function (id) {
      var slot = document.getElementById(id);
      if (slot) slot.textContent = msg;
    });
    var impact = content.querySelector('#td-impact');
    if (impact) impact.innerHTML = '<div class="conn-item" style="color:var(--text-dim)">' + msg + '</div>';
  }

  function _wireImpactButton(content, path) {
    var impact = content.querySelector('#td-impact');
    if (!impact) return;
    impact.addEventListener('click', function () {
      if (window.TraceView && TraceView.showImpact) TraceView.showImpact(path);
    });
    impact.style.cursor = 'pointer';
  }

  // Discussion / memory: "View Full Conversation" opens the transcript modal
  // (shared with the galaxy discussion card via JUG._disc).
  function _wireConvoModal(content) {
    var convoBtn = content.querySelector('.disc-view-btn');
    if (convoBtn && window.JUG && JUG._disc && JUG._disc.openConversationModal) {
      convoBtn.addEventListener('click', function () {
        JUG._disc.openConversationModal(convoBtn.getAttribute('data-session-id'));
      });
    }
  }

  function _fetch(url) {
    return fetch(url).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }

  function _loadConvo(sid) {
    var slot = document.getElementById('td-convo');
    if (!slot || !sid) return;
    _fetch('/api/discussion/' + encodeURIComponent(sid))
      .then(function (d) {
        var all = (d && (d.messages || d.turns)) || [];
        var msgs = all.filter(function (m) {
          return String(m.text || m.content || '').trim().length > 0;
        });
        if (!msgs.length) { slot.textContent = 'No transcript text.'; return; }
        slot.innerHTML = msgs.slice(0, 14).map(function (m) {
          return '<div class="td-msg"><span class="td-role">' + esc(m.role || m.type || '')
            + '</span> ' + esc(shortStr(m.text || m.content || '', 200)) + '</div>';
        }).join('') + (msgs.length > 14 ? '<div class="conn-item">… ' + (msgs.length - 14) + ' more</div>' : '');
      })
      .catch(function () { slot.textContent = 'Transcript unavailable.'; });
  }

  // Server-side diff_type inventory (shared git_diff_engine, contract A.2 /
  // A.5) — every terminal status this section can receive must render an
  // honest message; none may fall through to a raw enum dump or leave the
  // 'loading diff…' placeholder in place.
  //   `git` is the `git` field of /api/trace/file's response, or
  //   null/undefined before the fetch resolves:
  //     available:false        → server-supplied `reason` (e.g. "no git
  //                               repository found for this path").
  //     diff_type:'none'       → tracked, clean, no commit touched it —
  //                               an honest state, never "outside checkout".
  //     diff_type:'uncommitted'→ worktree/staged diff (covers deletions).
  //     diff_type:'last_commit'→ last commit that touched the file; render
  //                               `commit.sha` + `commit.subject` above
  //                               the diff lines.
  //     diff_type:'untracked'  → full file content as `add` lines.
  // precondition: git is the `git` field described above.
  // postcondition: always returns a non-empty HTML string; every diff_type
  // above is rendered with a distinct, honest message — never left
  // un-rendered and never conflated with another diff_type's message.
  var _DIFF_TYPE_LABEL = {
    uncommitted: 'Uncommitted changes',
    untracked: 'Untracked file (full contents)',
  };
  function _diffHtml(git) {
    if (!git || !git.available) {
      return '<div class="conn-item" style="color:var(--text-dim)">'
        + esc(git && git.reason ? git.reason : 'Diff unavailable') + '</div>';
    }
    var lines = git.lines || [];
    if (git.diff_type === 'none' || !lines.length) {
      return '<div class="conn-item" style="color:var(--text-dim)">No changes — file clean since last commit</div>';
    }
    var header;
    if (git.diff_type === 'last_commit' && git.commit) {
      header = '<div class="conn-item"><span class="td-sha">' + esc(git.commit.sha || '') + '</span> '
        + esc(shortStr(git.commit.subject || '', 80)) + '</div>';
    } else {
      var label = _DIFF_TYPE_LABEL[git.diff_type] || git.diff_type || 'Diff';
      header = '<div class="conn-item" style="color:var(--text-dim)">' + esc(label) + '</div>';
    }
    var h = header + '<div class="td-diff">';
    lines.slice(0, 300).forEach(function (ln) {
      h += '<div class="td-diff-' + (ln.type || 'ctx') + '">' + esc(ln.text != null ? ln.text : ln) + '</div>';
    });
    if (git.truncated || lines.length > 300) h += '<div class="td-diff-ctx">… truncated</div>';
    return h + '</div>';
  }

  function _versionsHtml(v) {
    if (!v || !v.available) return '<div class="conn-item" style="color:var(--text-dim)">no history</div>';
    var rows = v.versions || [];
    if (!rows.length) return '<div class="conn-item" style="color:var(--text-dim)">untracked / no commits</div>';
    return rows.map(function (c) {
      var date = (c.date || '').slice(0, 10);
      return '<div class="conn-item"><span class="td-sha">' + esc(c.sha || '') + '</span> '
        + '<span style="color:var(--text-dim)">' + esc(date) + '</span> '
        + esc(shortStr(c.subject || '', 70)) + '</div>';
    }).join('');
  }

  // Git + versions: fast path, no AST/impact bridge — fetched without
  // `include=ast` so this section responds in ms per contract A.5.
  var _fileCache = {};
  function _loadFile(path) {
    if (!path) return;
    var apply = function (d) {
      var g = document.getElementById('td-git');
      if (g) g.innerHTML = _diffHtml(d && d.git);
      var ver = document.getElementById('td-versions');
      if (ver) ver.innerHTML = _versionsHtml(d && d.versions);
    };
    if (_fileCache[path]) { apply(_fileCache[path]); return; }
    _fetch('/api/trace/file?path=' + encodeURIComponent(path))
      .then(function (d) { _fileCache[path] = d; apply(d); })
      .catch(function () {
        var g = document.getElementById('td-git');
        if (g) g.textContent = 'Diff unavailable — could not reach the server';
        var ver = document.getElementById('td-versions');
        if (ver) ver.textContent = 'History unavailable — could not reach the server';
      });
  }

  // AST symbols: decoupled from git+versions, its own cache — only fetched
  // with `?include=ast` on the AST section's own first expand (contract
  // A.5: the bridge call can take 65-155s and must never block the fast
  // git+versions response).
  var _astCache = {};
  function _loadAst(path) {
    if (!path) return;
    var apply = function (d) {
      var a = document.getElementById('td-ast');
      if (!a) return;
      var ast = (d && d.ast) || {};
      var syms = ast.available ? ast.symbols : null;
      var rows = Array.isArray(syms) ? syms
        : (syms && Array.isArray(syms.rows) ? syms.rows
        : (syms && Array.isArray(syms.nodes) ? syms.nodes : []));
      if (ast.available && rows.length) {
        a.innerHTML = rows.slice(0, 50).map(function (s) {
          var nm = s.qualified_name || s.name || s.id || (s.properties && s.properties.name) || '?';
          return '<div class="conn-item"><span class="conn-label">' + esc(nm) + '</span></div>';
        }).join('') + (rows.length > 50 ? '<div class="conn-item">… ' + (rows.length - 50) + ' more</div>' : '');
      } else {
        a.innerHTML = '<div class="conn-item" style="color:var(--text-dim)">no AST · '
          + esc((ast.reason || ast.error) || 'not indexed') + '</div>';
      }
    };
    if (_astCache[path]) { apply(_astCache[path]); return; }
    _fetch('/api/trace/file?path=' + encodeURIComponent(path) + '&include=ast')
      .then(function (d) { _astCache[path] = d; apply(d); })
      .catch(function () {
        var a = document.getElementById('td-ast');
        if (a) a.textContent = 'AST unavailable — could not reach the server';
      });
  }

  window.JUG = window.JUG || {};
  window.JUG._traceDetail = { build: build, wire: wire };
})();
