

# AGENTS.md — Structure & Content Guidelines

This file defines **what a high-quality `AGENTS.md` should contain**.
---

## 1. Mission & Priorities
The AGENTS.md should clearly state:
- The role of the agent in this repository
- The priority order for decisions (e.g. correctness, security, maintainability, performance, speed)
- Any non-obvious global goals or constraints

This section sets the optimization target for all agent behavior.

---

## 2. Executable Commands (Ground Truth)
The AGENTS.md must list **working, repo-specific commands**, including:
- Install / setup
- Development server
- Linting
- Formatting
- Type checking
- Unit tests
- Integration / e2e tests (if applicable)

Commands should be copy‑pasteable and authoritative.
If a command exists here, it is assumed to be correct.

---

## 3. Repository Map
The AGENTS.md should explain:
- The purpose of major directories
- Entry points (backend, frontend, CLI, services, etc.)
- Where configuration, schemas, migrations, or generated code live

This section reduces exploration cost and prevents agents from editing the wrong files.

---

## 4. Definition of Done
The AGENTS.md should define what “done” means for any change, such as:
- Required tests
- Required checks to run
- Documentation updates when behavior changes
- Expectations for summaries, notes, or PR descriptions

This should be written as a checklist-style reference, not prose.

---

## 5. Code Style & Conventions (Repo-Specific)
The AGENTS.md should capture only conventions that are:
- Specific to this repository
- Easy for agents to violate accidentally

Examples:
- Language/runtime versions
- Formatting tools and configs
- Naming conventions
- Error-handling patterns
- Logging expectations

Do not restate generic language best practices.

---

## 6. Boundaries & Guardrails
The AGENTS.md must explicitly state what an agent must **not** do, such as:
- Breaking public APIs without coordinated changes
- Disabling or bypassing tests, linting, or type checks
- Introducing new dependencies without justification
- Modifying unrelated files
- Committing secrets, credentials, or sensitive data

This section is critical for preventing high-impact failures.

---

## 7. Security & Privacy Constraints
If applicable, the AGENTS.md should describe:
- Locations of sensitive data
- Redaction or handling rules
- Approved crypto, storage, or security patterns
- Any relevant threat model assumptions

Only include what is necessary to avoid dangerous mistakes.

---

## 8. Common Pitfalls & Couplings
The AGENTS.md should list:
- Repeated mistakes agents tend to make
- Implicit couplings (e.g. “If you touch X, you must also update Y”)
- Forbidden imports or discouraged patterns

This section should be short and experience-driven.

---

## 9. Examples & Canonical Patterns (Optional but High-Value)
If included, examples should show:
- How to perform common tasks in this repo (e.g. adding an endpoint, feature flag, migration)
- Which files are touched
- Which tests are added
- Which commands are run

Examples should be concrete and minimal.

---

## 10. Branches and PRs
Branch naming conventions and whether to create PRs

---

## Quality Bar
A strong AGENTS.md is:
- Concise and bullet-based
- Actionable (commands, paths, constraints)
- Focused on preventing real failures
- Free of redundant documentation
- Optimized for agent consumption, not humans
