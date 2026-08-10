#!/usr/bin/env python3
"""Validate server.json offline, against the vendored MCP Registry schema.

Replaces the CI step that downloaded `mcp-publisher` and ran
`mcp-publisher validate`, which POSTs server.json to the live
`https://registry.modelcontextprotocol.io/v0/validate` endpoint. That
endpoint is a required check with no retry budget of its own; a brief
outage or network blip on the runner fails CI on a diff that never touched
server.json (measured on cortex-viz PR #139: both `test` jobs failed with
`dial tcp ...: i/o timeout` while 1335 tests passed; re-running turned them
green). This script removes that dependency for the part of the check that
can be replicated exactly offline.

Scope, established by reading the registry's own source
(github.com/modelcontextprotocol/registry, tag v1.8.1):

  * `/v0/validate` (and `mcp-publisher validate`) runs
    `validators.ValidateServerJSON(server, ValidationAll)` --
    `internal/api/handlers/v0/validate.go`. `ValidationAll` is full JSON
    Schema (draft-07) structural validation PLUS a set of Go-only semantic
    checks -- `internal/validators/validation_types.go`.
  * The schema itself is compiled into the registry binary via
    `//go:embed schemas/*.json` (`internal/validators/schema.go`) and is
    NOT fetched from the network at request time -- it is a static
    document, published unmodified at
    `https://static.modelcontextprotocol.io/schemas/<version>/server.schema.json`.
    That makes the schema half of the check reproducible offline: the same
    schema document, run through any spec-conformant draft-07 validator,
    produces identical results to the Go implementation
    (`santhosh-tekuri/jsonschema/v5`), because both are generic
    implementations of the same specification over the same input.
  * The Go compiler in schema.go is constructed with
    `jsonschema.NewCompiler()` and never sets `AssertFormat`, which
    defaults to `false` in that library -- so `format: "uri"` etc. are
    annotations only, not asserted. This script matches that: it does NOT
    pass a `format_checker` to `jsonschema`, to avoid being *stricter*
    than the real endpoint and rejecting a server.json the registry would
    accept.

What this script does NOT replicate -- the Go-only semantic checks, which
have no equivalent expressible in the JSON Schema document itself (source:
`internal/validators/validators.go`):
  * version is not "latest" and does not look like a semver range/wildcard
  * repository URL validity is checked per declared `source` (github, etc.)
  * `websiteUrl` / icon `src` must be absolute https URLs with no raw
    control/quote characters
  * `title` must not be whitespace-only
  * package `identifier` must contain no spaces; argument name/value rules
  * package and remote transport URLs are resolved against declared
    template variables (env vars, runtime/package arguments) and validated

These are not silently dropped from the project's release pipeline: the
registry's `/v0/publish` handler runs the SAME semantic checks --
`validators.ValidateServerJSON(server, ValidationSchemaVersionAndSemantic)`
-- authoritatively, over the network, at release time
(`internal/api/handlers/v0/publish.go:58`), and Release.yaml's
`publish-registry` job fails the release loudly if they are violated. This
script narrows the CI gate to what CI should assert before a release
exists (RFC-free structural conformance); the semantic gate stays where it
already was authoritative.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator
from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[1]
SERVER_JSON = ROOT / "server.json"
# Bump this alongside re-vendoring — see scripts/schemas/README.md.
SCHEMA_FILE = ROOT / "scripts" / "schemas" / "2025-12-11.json"


def require(condition: bool, message: str) -> None:
    """Raise even when Python assertions are disabled."""
    if not condition:
        raise ValueError(message)


def load_schema() -> dict:
    schema = json.loads(SCHEMA_FILE.read_text())
    require("$id" in schema, f"{SCHEMA_FILE} is missing required '$id' field")
    require(
        schema.get("$schema") == "http://json-schema.org/draft-07/schema#",
        f"{SCHEMA_FILE} is not a draft-07 schema (upstream changed dialect; "
        "re-check whether jsonschema.Draft7Validator still applies)",
    )
    return schema


def main() -> None:
    server = json.loads(SERVER_JSON.read_text())
    schema = load_schema()

    require(
        server.get("$schema") == schema["$id"],
        f"server.json's $schema ({server.get('$schema')!r}) does not match the "
        f"vendored schema's $id ({schema['$id']!r}). server.json has moved to a "
        "schema version this repo has not vendored yet — re-vendor per "
        "scripts/schemas/README.md before trusting this check.",
    )

    require(
        validator_for(schema) is Draft7Validator,
        "vendored schema no longer resolves to Draft7Validator; the Go side "
        "may have changed dialect — re-verify before updating this script",
    )

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(server), key=lambda e: list(e.path))
    if errors:
        print(f"server.json FAILED schema validation with {len(errors)} issue(s):")
        for i, error in enumerate(errors, 1):
            path = "$" + "".join(f"[{p!r}]" for p in error.path) if error.path else "$"
            print(f"{i}. {path}: {error.message}")
        raise SystemExit(1)

    print(f"server.json is schema-valid against {schema['$id']}")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, KeyError, OSError, ValueError) as exc:
        print(f"server.json manifest validation FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
