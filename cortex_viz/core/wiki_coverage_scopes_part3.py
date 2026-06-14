"""Canonical documentation scopes — part 3 of the SCOPES table.

Split out of ``wiki_coverage.py`` to respect the 500-line file limit.
Pure data continuation; see ``wiki_coverage_scopes_part1``.
"""

from __future__ import annotations

from cortex_viz.core.wiki_coverage_scope_type import Scope

SCOPES_PART3: tuple[Scope, ...] = (
    Scope(
        name="tutorials",
        title="Tutorials & learning paths",
        description=(
            "Step-by-step learning sequences that take a beginner "
            "from zero to a working understanding of one concept. "
            "Distinct from onboarding (one-shot day-1 setup) and "
            "from how-to guides (which assume the reader knows what "
            "they're doing). A tutorial introduces concepts in "
            "order, with expected outputs at every step, ending "
            "with a working example the reader has built themselves."
        ),
        anchor_filenames=(
            "tutorials.md",
            "tutorial.md",
            "learn.md",
            "lessons.md",
        ),
        directories=("tutorial", "tutorials", "guides"),
        suggested_kind="tutorial",
    ),
    Scope(
        name="integration-guides",
        title="Integration guides",
        description=(
            "How to wire this project into external systems: the "
            "IDEs, CI providers, model APIs, databases, MCP "
            "clients, browser extensions, or downstream services "
            "that consume its output. Each integration entry gives "
            "the contract (what the external system must do), the "
            "wiring steps, and the smoke test that proves the "
            "integration works."
        ),
        anchor_filenames=(
            "integrations.md",
            "integration.md",
            "integration-guides.md",
        ),
        directories=("guides", "how-to", "reference"),
        suggested_kind="how-to",
    ),
    Scope(
        name="examples",
        title="Examples & sample code",
        description=(
            "End-to-end working examples in the form a reader can "
            "copy and run: a sample app, a demo workflow, a "
            "starter template. Distinct from recipes (which are "
            "snippets) — examples are complete buildable units "
            "that show several pieces working together."
        ),
        anchor_filenames=(
            "examples.md",
            "demos.md",
            "sample-apps.md",
        ),
        directories=("guides", "tutorial", "reference"),
        suggested_kind="tutorial",
    ),
    # ── Setup + local-dev ──────────────────────────────────────────────
    Scope(
        name="local-development",
        title="Local development",
        description=(
            "How a contributor sets the project up on their own "
            "machine: clone, install deps, run the dev server, run "
            "the watcher, swap in stub services. Includes the fast "
            "feedback loops (hot reload, incremental test runs) and "
            "the mocks / fixtures that make local work possible "
            "without production credentials."
        ),
        anchor_filenames=(
            "local-development.md",
            "dev-setup.md",
            "developing.md",
            "development.md",
        ),
        directories=("guides", "how-to"),
        suggested_kind="how-to",
    ),
    # ── Operational telemetry ──────────────────────────────────────────
    Scope(
        name="logging",
        title="Logging guide",
        description=(
            "Log format the project emits, log levels and what "
            "each one is for, where logs go (stdout / file / "
            "remote sink), how to filter, how to rotate, how to "
            "plumb structured fields. Names the libraries used and "
            "the conventions for adding new log sites."
        ),
        anchor_filenames=(
            "logging.md",
            "logs.md",
            "log-format.md",
        ),
        directories=("guides", "reference", "how-to"),
        suggested_kind="reference",
    ),
    Scope(
        name="observability",
        title="Observability",
        description=(
            "Metrics emitted, traces instrumented, dashboards "
            "available, alerts wired. The runbook a reader checks "
            "BEFORE production goes wrong: 'how do I see what this "
            "is doing?' Names the OpenTelemetry surface, "
            "Prometheus / Grafana boards, log queries to copy-paste."
        ),
        anchor_filenames=(
            "observability.md",
            "metrics.md",
            "telemetry.md",
            "tracing.md",
        ),
        directories=("guides", "reference", "runbook"),
        suggested_kind="reference",
    ),
    # ── Security ───────────────────────────────────────────────────────
    Scope(
        name="secrets-management",
        title="Secrets management",
        description=(
            "Where API keys, tokens, certificates, and other "
            "secrets live during local dev, in CI, and in "
            "production. Rotation procedure, scope of each "
            "credential, what happens when one leaks. Distinct "
            "from access-control because this is about the "
            "credentials themselves, not who can use them."
        ),
        anchor_filenames=(
            "secrets.md",
            "secrets-management.md",
            "credentials.md",
        ),
        directories=("guides", "how-to", "reference"),
        suggested_kind="how-to",
    ),
    Scope(
        name="access-control",
        title="Access control & permissions",
        description=(
            "Who can do what: roles, scopes, capabilities, the "
            "matrix of actions × principal. Includes the model "
            "(RBAC / ABAC / ACL / capability) and the enforcement "
            "points (middleware, policy engine). For projects "
            "without external users, documents the internal trust "
            "model."
        ),
        anchor_filenames=(
            "access-control.md",
            "permissions.md",
            "authorization.md",
            "rbac.md",
        ),
        directories=("reference", "guides"),
        suggested_kind="reference",
    ),
    # ── Contribution + governance ──────────────────────────────────────
    Scope(
        name="coding-standards",
        title="Coding standards & conventions",
        description=(
            "The mechanical rules every contribution honours: "
            "file-size limits, layer boundaries, naming, lint "
            "config, type-system discipline, source-citation "
            "requirements. Cite the lint / format / typecheck "
            "tools that enforce each rule."
        ),
        anchor_filenames=(
            "coding-standards.md",
            "style-guide.md",
            "conventions.md",
            "code-style.md",
        ),
        directories=("reference", "guides"),
        suggested_kind="reference",
    ),
    Scope(
        name="release-process",
        title="Release process",
        description=(
            "How a version ships: branch / tag / build pipeline, "
            "versioning scheme (semver / calver / commit-hash), "
            "changelog format, sign-off requirements, rollback "
            "procedure. Distinct from ci-cd which describes the "
            "pipeline; this describes how a human cuts a release."
        ),
        anchor_filenames=(
            "releasing.md",
            "release-process.md",
            "release.md",
            "publishing.md",
        ),
        directories=("guides", "how-to", "runbook"),
        suggested_kind="how-to",
    ),
    Scope(
        name="changelog",
        title="Changelog",
        description=(
            "User-facing record of what changed between versions. "
            "Each release entry calls out new features, breaking "
            "changes, deprecations, security fixes, and the "
            "migration guide that pairs with a breaking change. "
            "Format follows Keep a Changelog by default; project "
            "may override."
        ),
        anchor_filenames=(
            "CHANGELOG.md",
            "changelog.md",
            "history.md",
            "releases.md",
        ),
        directories=("reference",),
        suggested_kind="reference",
    ),
    Scope(
        name="roadmap",
        title="Roadmap",
        description=(
            "What's planned, what's in progress, what's deferred "
            "and why. Not a release schedule (use release-process "
            "for cadence) — this is the strategic surface a reader "
            "checks before betting on the project. Honest about "
            "what isn't going to happen."
        ),
        anchor_filenames=(
            "roadmap.md",
            "ROADMAP.md",
            "future-work.md",
            "planned.md",
        ),
        directories=("explanation", "reference", "guides"),
        suggested_kind="explanation",
    ),
    # ── Inclusive design ───────────────────────────────────────────────
    Scope(
        name="accessibility",
        title="Accessibility",
        description=(
            "Accessibility standards the project follows (WCAG "
            "level, screen-reader support, keyboard navigation, "
            "colour contrast audit). For non-UI projects, write "
            "'Not applicable — this project has no user interface; "
            "downstream consumers are responsible for their own "
            "a11y' rather than omitting."
        ),
        anchor_filenames=(
            "accessibility.md",
            "a11y.md",
        ),
        directories=("reference", "explanation"),
        suggested_kind="reference",
    ),
    Scope(
        name="localization",
        title="Localization & i18n",
        description=(
            "How the project handles translation, locale data, "
            "currency / date formatting, right-to-left layout. "
            "Names the i18n library, the source-of-truth strings "
            "file, the translator workflow. 'Not applicable — "
            "single-locale project' is a valid answer; write it "
            "explicitly."
        ),
        anchor_filenames=(
            "localization.md",
            "i18n.md",
            "translations.md",
        ),
        directories=("reference", "guides"),
        suggested_kind="reference",
    ),
)
