# Releasing

Releases use the canonical distribution identity `hypermnesia-mcp-viz`. The
GitHub repository, Cortex Viz product name, Claude Code plugin name, Python
import package, and `cortex-viz` compatibility command do not change.

## One-time PyPI setup

Before the first release, create a pending Trusted Publisher for the
`hypermnesia-mcp-viz` PyPI project with these exact values:

- Owner: `cdeust`
- Repository: `cortex-viz`
- Workflow: `release.yml`
- Environment: `pypi`

No long-lived PyPI API token is used. The release job obtains an OpenID Connect
identity through the `pypi` GitHub environment.

## Release flow

1. Ensure `pyproject.toml`, `cortex_viz/identity.py`, `server.json`, and the
   Claude plugin manifest carry the same release version.
2. Merge the release change and tag that commit as `v<version>`.
3. Push the tag. The release workflow builds and validates the wheel, source
   archive, SBOM, and UI manifest; attests them; publishes to PyPI; then creates
   the GitHub release.
4. Publish `server.json` with the official MCP Registry publisher after the
   matching PyPI version is available.
