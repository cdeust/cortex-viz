"""Canonical documentation scopes — part 2 of the SCOPES table.

Split out of ``wiki_coverage.py`` to respect the 500-line file limit.
Pure data continuation; see ``wiki_coverage_scopes_part1``.
"""

from __future__ import annotations

from cortex_viz.core.wiki_coverage_scope_type import Scope

SCOPES_PART2: tuple[Scope, ...] = (
    # ── Task-oriented how-to guides (Diátaxis: how-to quadrant) ────────
    #
    # Onboarding above and code-walkthrough cover the learning + reading
    # axes; what's missing is the *task-oriented* how-to layer — the
    # guides a user hits when they have a concrete goal already
    # (install, fix a known error, contribute). Three universal
    # categories cover the bulk of project-level user demand:
    Scope(
        name="installation",
        title="Installation & configuration",
        description=(
            "Step-by-step install + configuration walk-through, OS by OS, "
            "with every environment variable named and its default. Beyond "
            "onboarding (which is the day-one path) this is the reference "
            "for every configuration knob, every install variant (CLI, "
            "Docker, source clone, plugin marketplace), and the post-"
            "install verification commands. Include screenshots / sample "
            "configs where useful."
        ),
        anchor_filenames=(
            "installation.md",
            "install.md",
            "configuration.md",
            "configure.md",
        ),
        directories=("guides", "how-to"),
        suggested_kind="how-to",
    ),
    Scope(
        name="troubleshooting",
        title="Troubleshooting & known issues",
        description=(
            "Symptom → diagnosis → fix table for every known issue, "
            "common error message, and pitfall. Each entry: the exact "
            "log line or symptom, root cause, recovery steps, prevention. "
            "Sourced from runbook incidents + ADR consequences + lessons "
            "captured in memory. The page a user opens when something "
            "breaks at 3am."
        ),
        anchor_filenames=(
            "troubleshooting.md",
            "known-issues.md",
            "faq.md",
            "common-errors.md",
        ),
        directories=("guides", "how-to", "runbook"),
        suggested_kind="how-to",
    ),
    Scope(
        name="contributing",
        title="Contributing guide",
        description=(
            "How to land a change in this project: fork / branch policy, "
            "test commands, lint rules, commit-message convention, PR "
            "template, code-review expectations, who reviews what, how "
            "long things typically take. Distinct from onboarding "
            "(reader is already set up); this is the path from edit to "
            "merge."
        ),
        anchor_filenames=(
            "contributing.md",
            "CONTRIBUTING.md",
            "contribute.md",
            "development.md",
        ),
        directories=("guides", "how-to"),
        suggested_kind="how-to",
    ),
    Scope(
        name="configuration",
        title="Configuration reference",
        description=(
            "Exhaustive table of every configurable knob: every "
            "environment variable, every config-file key, every CLI "
            "flag, every plugin setting. Columns: name | type | "
            "required/optional | default | valid range | effect. "
            "Distinct from the installation guide (which is task-"
            "oriented step-by-step); this is the reference for people "
            "who already know what they're doing."
        ),
        anchor_filenames=(
            "configuration.md",
            "config.md",
            "settings.md",
            "env-vars.md",
        ),
        directories=("reference", "guides"),
        suggested_kind="reference",
    ),
    Scope(
        name="security",
        title="Security model & hardening",
        description=(
            "The project's threat model + security posture. What "
            "secrets the project handles, how they're stored, what "
            "isolation guarantees exist, who can do what, the "
            "attack surface, mitigations in place, and the hardening "
            "checklist for production deployment. Sourced from "
            "security ADRs + audit findings. The page security review "
            "boards consult."
        ),
        anchor_filenames=(
            "security.md",
            "SECURITY.md",
            "threat-model.md",
            "hardening.md",
        ),
        directories=("guides", "reference"),
        suggested_kind="reference",
    ),
    Scope(
        name="performance",
        title="Performance characteristics & tuning",
        description=(
            "Measured throughput / latency / resource consumption "
            "characteristics, with the benchmarks that produced them. "
            "Tuning knobs and when to turn each one. Capacity planning "
            "advice. Profiling instructions. Failure modes under load. "
            "Distinct from operations (which covers runbook recovery) — "
            "this covers expected steady-state behaviour and how to "
            "shape it."
        ),
        anchor_filenames=(
            "performance.md",
            "benchmarks.md",
            "tuning.md",
            "scaling.md",
        ),
        directories=("guides", "reference"),
        suggested_kind="reference",
    ),
    Scope(
        name="migration-guides",
        title="Migration & upgrade guides",
        description=(
            "Version-to-version upgrade paths. Every breaking change "
            "between releases should have a guide: what changed, why, "
            "what the user's existing code must do to keep working, "
            "and the deprecation timeline for the old behaviour. Plus "
            "migration FROM competing tools (if applicable). Write "
            '"Not applicable — no breaking changes yet" for fresh '
            "projects rather than omitting the heading."
        ),
        anchor_filenames=(
            "migration.md",
            "migrations.md",
            "upgrade.md",
            "upgrading.md",
            "breaking-changes.md",
        ),
        directories=("guides", "how-to", "reference"),
        suggested_kind="how-to",
    ),
    Scope(
        name="debugging",
        title="Debugging guide",
        description=(
            "How to figure out what's happening at runtime: where logs "
            "go, log-level controls, the verbose / debug flags, the "
            "diagnostic commands, how to read a stack trace from this "
            "project's code, how to attach a debugger, the trace IDs "
            "/ correlation IDs and how to follow them. Distinct from "
            "troubleshooting (which is symptom → fix table) — this is "
            "the methodology of investigation."
        ),
        anchor_filenames=(
            "debugging.md",
            "diagnostics.md",
            "logging.md",
            "observability.md",
        ),
        directories=("guides", "how-to"),
        suggested_kind="how-to",
    ),
    Scope(
        name="testing",
        title="Testing guide",
        description=(
            "How to write and run tests for this project: the test "
            "layout (unit / integration / e2e), conventions for "
            "fixtures + mocks + factories, coverage targets per layer, "
            "the test commands, how to debug a flaky test, how to add "
            "a new test category. Distinct from CI/CD (which covers "
            "the pipeline that runs tests) — this is the developer's "
            "test-authoring workflow."
        ),
        anchor_filenames=(
            "testing.md",
            "tests.md",
            "test-strategy.md",
            "writing-tests.md",
        ),
        directories=("guides", "how-to"),
        suggested_kind="how-to",
    ),
    Scope(
        name="plugins-extensions",
        title="Plugins & extensions",
        description=(
            "The project's extension API: what surfaces can be "
            "extended, the lifecycle of an extension, the manifest / "
            "hook shape, the testing strategy for an extension. Write "
            '"Not applicable — this project does not expose an '
            'extension API" for monolithic projects rather than '
            "omitting the heading."
        ),
        anchor_filenames=(
            "plugins.md",
            "extensions.md",
            "plugin-api.md",
            "addons.md",
            "extending.md",
        ),
        directories=("reference", "guides", "explanation"),
        suggested_kind="reference",
    ),
    Scope(
        name="recipes",
        title="Recipes & cookbook",
        description=(
            'Common task walkthroughs — "how do I X?" answered with '
            "the exact commands and code. 8–20 short worked examples "
            "ordered from beginner to advanced, each one self-"
            "contained with copy-pasteable steps + expected output. "
            "The page users land on when they ask the project a "
            "concrete question."
        ),
        anchor_filenames=(
            "recipes.md",
            "cookbook.md",
            "examples.md",
            "patterns.md",
        ),
        directories=("guides", "how-to"),
        suggested_kind="how-to",
    ),
    Scope(
        name="glossary",
        title="Glossary",
        description=(
            "Project-specific terms with a one-paragraph definition. "
            "Domain abbreviations, internal aliases, every term the "
            "reader might hit in another page without context should "
            "resolve here. Sorted alphabetically. Include 'what it is "
            "NOT' notes for common confusions."
        ),
        anchor_filenames=(
            "glossary.md",
            "terms.md",
            "terminology.md",
            "definitions.md",
        ),
        directories=("reference", "guides"),
        suggested_kind="reference",
    ),
    # ── Diátaxis how-to / tutorial axis ────────────────────────────────
    Scope(
        name="how-to-guides",
        title="How-to guides",
        description=(
            "Task-oriented guides answering 'how do I X?' for the "
            "most common operations the project supports. Distinct "
            "from tutorials (which teach concepts) and reference "
            "(which catalogues fields): a how-to is a recipe that "
            "gets a user from a stated starting point to a stated "
            "outcome with the minimum number of steps. Anchor page "
            "is an index of the available how-tos; each individual "
            "recipe is a sibling page."
        ),
        anchor_filenames=(
            "how-to.md",
            "how-to-guides.md",
            "guides.md",
            "howto.md",
        ),
        directories=("guides", "how-to"),
        suggested_kind="how-to",
    ),
)
