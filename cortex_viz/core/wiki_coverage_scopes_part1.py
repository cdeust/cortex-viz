"""Canonical documentation scopes — part 1 of the SCOPES table.

Split out of ``wiki_coverage.py`` (was 1396 lines) to respect the
500-line file limit. Pure data — the ``Scope`` dataclass plus the
first group of canonical scopes. ``wiki_coverage_scopes`` concatenates
the three parts into the public ``SCOPES`` tuple.
"""

from __future__ import annotations

from cortex_viz.core.wiki_coverage_scope_type import Scope

SCOPES_PART1: tuple[Scope, ...] = (
    Scope(
        name="product-overview",
        title="Product overview",
        description=(
            "What the project IS, in one page a non-engineer (customer, "
            "partner, exec) can read. Problem, solution, who it's for, "
            "what makes it different. The page sales picks up to "
            "explain the project to a customer."
        ),
        anchor_filenames=(
            "product-overview.md",
            "overview.md",
            "what-is-this.md",
            "elevator-pitch.md",
        ),
        directories=("guides", "reference", "explanation"),
        suggested_kind="reference",
    ),
    Scope(
        name="architecture",
        title="Architecture overview",
        description=(
            "Overall design: layers, the dependency rule, the major "
            "subsystems and how they relate. The first page a new "
            "engineer should read."
        ),
        anchor_filenames=(
            "architecture-overview.md",
            "architecture.md",
        ),
        directories=("reference", "explanation", "architecture"),
        suggested_kind="explanation",
    ),
    Scope(
        name="services",
        title="Services / components inventory",
        description=(
            "Catalogue of the project's components, handlers, modules "
            "or services — what each one is responsible for, where its "
            "boundary lies, what it must not do."
        ),
        anchor_filenames=(
            "services.md",
            "components.md",
            "modules.md",
            "handlers.md",
        ),
        directories=("reference", "explanation"),
        suggested_kind="reference",
    ),
    Scope(
        name="code-walkthrough",
        title="Code walkthrough",
        description=(
            "Reading-the-code guide: where to start in the source "
            "tree, how the entry points connect, what each top-level "
            "directory means. Targeted at a new engineer cloning the "
            "repo for the first time."
        ),
        anchor_filenames=(
            "code-walkthrough.md",
            "codebase-tour.md",
            "reading-the-code.md",
        ),
        directories=("explanation", "guides", "reference"),
        suggested_kind="explanation",
    ),
    Scope(
        name="api",
        title="Public API surface",
        description=(
            "The contract the project exposes to callers — CLI flags, "
            "HTTP endpoints, MCP tools, library functions. What is "
            "stable, what is experimental, what is deprecated."
        ),
        anchor_filenames=(
            "api.md",
            "api-reference.md",
            "endpoints.md",
        ),
        directories=("reference",),
        suggested_kind="reference",
    ),
    Scope(
        name="data-flow",
        title="Data flow",
        description=(
            "Lifecycle of a record through the system: how it enters, "
            "what transforms it, where it is stored, how it is "
            "retrieved. Diagrams strongly preferred."
        ),
        anchor_filenames=(
            "data-flow.md",
            "dataflow.md",
            "pipeline.md",
            "ingest.md",
        ),
        directories=("reference", "explanation"),
        suggested_kind="explanation",
    ),
    Scope(
        name="commands",
        title="Commands & CLI",
        description=(
            "Every slash command, CLI subcommand, or invocable entry "
            "point. What each one does, when to use it, what to expect "
            "in the response. The reference a user hits when they ask "
            "'how do I do X?'"
        ),
        anchor_filenames=(
            "commands.md",
            "cli.md",
            "slash-commands.md",
        ),
        directories=("reference", "guides"),
        suggested_kind="reference",
    ),
    Scope(
        name="mcp",
        title="MCP integration",
        description=(
            "How the project exposes (or consumes) Model Context "
            "Protocol surfaces — tools, resources, prompts, the "
            "stability model. Only applies to projects that integrate "
            "with MCP; otherwise the scope page documents 'we don't "
            "use MCP.'"
        ),
        anchor_filenames=(
            "mcp.md",
            "mcp-integration.md",
            "mcp-tools.md",
        ),
        directories=("reference", "explanation"),
        suggested_kind="reference",
    ),
    Scope(
        name="tools",
        title="Tooling & dependencies",
        description=(
            "What's in the toolchain — runtime, package manager, "
            "testing framework, linters, build system, hosted services, "
            "third-party libraries the project takes hard dependencies "
            "on. Each entry with a one-line 'why this one'."
        ),
        anchor_filenames=(
            "tools.md",
            "tooling.md",
            "stack.md",
            "dependencies.md",
        ),
        directories=("reference",),
        suggested_kind="reference",
    ),
    Scope(
        name="ci-cd",
        title="CI / CD",
        description=(
            "Build, test, release pipeline. What runs on every push, "
            "what runs on merge to main, what runs on tag. Where the "
            "configuration lives, what fails block a merge."
        ),
        anchor_filenames=(
            "ci-cd.md",
            "ci.md",
            "build.md",
            "release.md",
            "deploy.md",
        ),
        directories=("reference", "runbook", "guides"),
        suggested_kind="reference",
    ),
    Scope(
        name="ai-usage",
        title="AI usage",
        description=(
            "Where AI / LLMs are used in the project — which models, "
            "which prompts, what's auto vs. human-in-the-loop, what "
            "data the models see, how cost and rate-limit are bounded. "
            "Skip with 'no AI usage' if the project doesn't use LLMs."
        ),
        anchor_filenames=(
            "ai-usage.md",
            "llm-usage.md",
            "ai-integration.md",
            "prompts.md",
        ),
        directories=("explanation", "reference"),
        suggested_kind="explanation",
    ),
    Scope(
        name="operations",
        title="Operations & runbooks",
        description=(
            "Deploy, monitor, recover. On-call procedures, health "
            "checks, common failure modes and how to respond to them."
        ),
        anchor_filenames=(
            "operations.md",
            "runbook.md",
            "monitoring.md",
        ),
        directories=("runbook", "guides"),
        suggested_kind="runbook",
    ),
    Scope(
        name="prd",
        title="Product requirements",
        description=(
            "Spec / RFC / PRD documents — what's been formally proposed "
            "and accepted as scope. Each PRD frames a problem, the "
            "goals, the non-goals, the design, and the open questions."
        ),
        anchor_filenames=(),  # any spec or rfc page counts
        directories=("specs", "rfc"),
        suggested_kind="rfc",
    ),
    Scope(
        name="decisions",
        title="Decisions / task-records",
        description=(
            "Documented decisions and completed tasks — Entry, "
            "Mandatory elements, How, Result, Serves. The project's "
            "causal history."
        ),
        anchor_filenames=(),  # any ADR counts
        directories=("adr", "adrs"),
        suggested_kind="adr",
    ),
    Scope(
        name="onboarding",
        title="Onboarding & getting started",
        description=(
            "Day-one path for a new contributor or integrator: install, "
            "configure, run the smoke test, ship the first change. "
            "The 'I just landed in this repo, what do I do' page."
        ),
        anchor_filenames=(
            "getting-started.md",
            "onboarding.md",
            "quickstart.md",
            "setup.md",
        ),
        directories=("guides", "tutorial"),
        suggested_kind="tutorial",
    ),
)
