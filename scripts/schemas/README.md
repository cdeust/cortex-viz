# Vendored MCP Registry server schema

`2025-12-11.json` is a byte-identical copy of
`internal/validators/schemas/2025-12-11.json` from
[`modelcontextprotocol/registry`](https://github.com/modelcontextprotocol/registry)
at tag `v1.8.1` (the same file the registry's Go binary embeds via
`//go:embed schemas/*.json` and serves, unmodified, as
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`).
Fetched:

```
curl -fsSL https://raw.githubusercontent.com/modelcontextprotocol/registry/v1.8.1/internal/validators/schemas/2025-12-11.json
```

It exists so `scripts/validate_server_manifest.py` can run JSON Schema
structural validation on `server.json` **offline** — see that script's
module docstring for what this replicates (and does not replicate) from
the registry's live `/v0/validate` endpoint.

## When `server.json`'s `$schema` field changes

`validate_server_manifest.py` fails loudly, by design, if the vendored
file's `$id` no longer matches `server.json`'s `$schema` value — it will
not silently validate against a stale schema version. To fix that failure,
re-vendor the new version:

```
NEW_VERSION=<e.g. 2026-01-15>
curl -fsSL "https://raw.githubusercontent.com/modelcontextprotocol/registry/<registry-tag-that-shipped-it>/internal/validators/schemas/${NEW_VERSION}.json" \
  -o "scripts/schemas/${NEW_VERSION}.json"
rm scripts/schemas/2025-12-11.json   # or keep both if migrating gradually
```

Then update `SCHEMA_FILENAME` in `scripts/validate_server_manifest.py`.
