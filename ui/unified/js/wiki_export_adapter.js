// Cortex — offline transport adapter for the static wiki export (#112).
//
// Installed on the port `wiki.js` reads (`JUG._wikiTransport`), so the exported
// bundle renders through the wiki view's own code with its data coming from an
// inlined payload instead of HTTP. Nothing here re-implements the view; it only
// answers requests.
//
// Every reply is shaped like the `fetch` Response the view already handles: an
// `ok` flag, a `status`, and a `json()`. Anything the bundle cannot answer is
// refused with `unavailable: true` — the marker #119 added — so the view renders
// its named "unavailable" state rather than mistaking silence for empty data.
(function () {
  var bundle = window.__CORTEX_WIKI_EXPORT__ || { responses: {} };
  var responses = bundle.responses || {};

  function response(body, status) {
    return Promise.resolve({
      ok: status === 200,
      status: status,
      headers: { get: function () { return 'application/json'; } },
      json: function () { return Promise.resolve(body); },
      text: function () { return Promise.resolve(JSON.stringify(body)); },
    });
  }

  // A write cannot be honoured by a file:// bundle. Saying so explicitly beats
  // letting the request fail as an opaque network error the editor would
  // surface as "Saving…" forever.
  function refuseWrite() {
    return response(
      { error: 'This is a static export — editing is not available.',
        unavailable: true },
      405
    );
  }

  function offlineTransport(url, options) {
    if (options && options.method && options.method !== 'GET') return refuseWrite();
    if (Object.prototype.hasOwnProperty.call(responses, url)) {
      return response(responses[url], 200);
    }
    // Not in the payload. `unavailable` is what makes this legible to the view
    // (see http_standalone_wiki._dispatch_get's unknown-op arm).
    return response(
      { ok: true, items: [], note: 'not_in_static_export', unavailable: true },
      200
    );
  }

  window.JUG = window.JUG || {};
  window.JUG._wikiTransport = offlineTransport;
  window.JUG._wikiExportTest = {
    transport: offlineTransport,
    urls: Object.keys(responses).sort(),
  };
})();
