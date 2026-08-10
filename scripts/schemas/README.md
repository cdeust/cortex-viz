# Vendored MCP Registry server schema

`2025-12-11.json` is a copy of
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
— the exact URL in this repo's `server.json`'s `$schema` field, and the
canonical document a spec-conformant client fetches. It exists so
`scripts/validate_server_manifest.py` can run JSON Schema structural
validation on `server.json` **offline** — see that script's module
docstring for what this replicates (and does not replicate) from the
registry's live `/v0/validate` endpoint.

Fetched:

```
curl -fsSL https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
```

## A copied file drifts silently — this one is checked automatically

Provenance is the minimum, not a guarantee. `scripts/check_vendored_schema_drift.py`
reads the vendored file's own `$id` (which *is* the canonical URL above),
fetches it, and compares structurally. It runs as a non-blocking step in
`ci.yml` on every `test` job (never gates a PR — see that step's comment
for why), and can be run standalone:

```
uv run --no-sync python -m scripts.check_vendored_schema_drift
```

**This already caught real drift once.** The file was first vendored from
`modelcontextprotocol/registry`'s `v1.8.1` git tag
(`internal/validators/schemas/2025-12-11.json`), on the assumption that a
dated schema version is immutable once published. It measurably was not:
that copy carried a `maxLength: 255` constraint on `Package.version` that
the CDN's currently-served document does not have. The registry repo's own
schema-sync workflow (`sync-schema.yml`, upstream) is
`workflow_dispatch`-only — no schedule — so its embedded copy can lag the
canonical `modelcontextprotocol/static` source it mirrors. Re-vendoring
directly from the CDN (rather than the registry repo tag) removed the
stray constraint; see git blame on this line for the commit.

**Trust the CDN as the source, not a registry-repo git tag.** The registry
repo is a secondary distribution of the schema (embedded into its Go
binary at build time), not the schema's origin — `modelcontextprotocol/static`
is, and the CDN is `static`'s public face.

## When `server.json`'s `$schema` field changes

`validate_server_manifest.py` fails loudly, by design, if the vendored
file's `$id` no longer matches `server.json`'s `$schema` value — it will
not silently validate against a stale schema version. To fix that failure,
re-vendor the new version straight from the CDN URL server.json now
points to:

```
NEW_VERSION=<e.g. 2026-01-15>   # read off server.json's new $schema field
curl -fsSL "https://static.modelcontextprotocol.io/schemas/${NEW_VERSION}/server.schema.json" \
  -o "scripts/schemas/${NEW_VERSION}.json"
rm scripts/schemas/2025-12-11.json   # or keep both if migrating gradually
```

Then update `SCHEMA_FILE` in `scripts/validate_server_manifest.py` and
`SCHEMA_FILE` in `scripts/check_vendored_schema_drift.py`.
