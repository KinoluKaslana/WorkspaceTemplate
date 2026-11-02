<div align="center">

[简体中文](README.md) | **English**

# WorkspaceTemplate

**A business-agnostic Workspace governance template that is not tied to a single Agent client.**

Start from a clean, auditable rule skeleton so an Agent confirms the Python runtime, business boundaries, tool facts, and skills sources before doing real work.

[![Policy](https://img.shields.io/badge/policy-v3.3.1-single%20source%20of%20truth-6f42c1)](AGENT_RULES.md)
[![Integrity](https://img.shields.io/badge/integrity-clause%20IDs%20%2B%20trust%20chain-success)](scripts/verify_rules.py)
[![Python](https://img.shields.io/badge/Python-local%20venv%20%7C%20MCP-3776AB?logo=python&logoColor=white)](.workspace/bootstrap.json)
[![Bootstrap](https://img.shields.io/badge/bootstrap-zero%20third--party%20dependencies-2ea44f)](scripts/inspect_skills.py)

[Quick Start](#quick-start) · [First Open](#what-happens-on-first-open) · [Rule Layering & Integrity](#rule-layering-and-integrity-verification) · [Lifecycle Skills](#workspace-lifecycle-skills) · [Repository Layout](#repository-layout) · [FAQ](#faq)

</div>

---

## Why It Exists

Most new Agent Workspaces do not lack code. They lack a stable foundation that will not drift from one conversation to the next:

- Multiple Agent clients should follow one policy instead of maintaining similar copies that slowly diverge.
- Python is extremely useful, but an Agent should not create environments, access the network, or install a large dependency bundle before the user decides how Python should run.
- Skills should be onboarded by directory while preserving source, revision, name-conflict, and side-effect boundaries.
- Business goals, runtime facts, long-term experience, and Agent policy should live in separate layers.
- Your own custom rules should not be tangled into the template baseline, where they would cause sync conflicts.
- External documents and tools may add capability, but they must not grant themselves additional authority.

WorkspaceTemplate turns those principles into a small, explicit set of files. It can grow into a software project, data-analysis workspace, research repository, content workflow, operations project, or any other authorized business environment without forcing a technology stack up front.

> [!IMPORTANT]
> The repository root is the usable Workspace template; there is no second template directory to enter. The README is a GitHub landing page and onboarding guide, not an Agent policy source. [`AGENT_RULES.md`](AGENT_RULES.md) is the sole authoritative policy.

## Core Capabilities

| Capability | How the template provides it |
|---|---|
| One policy source | [`AGENT_RULES.md`](AGENT_RULES.md) governs primary and delegated Agents; client entry files only point to it |
| Rule layering | Template-layer files are **locked read-only** (sync = whole-file replacement); local rules live in `<file>.custom.md` and **custom takes precedence** (§10) |
| Integrity verification | Stable clause IDs (`<!-- id:Rn -->`) + registry-based two-level verification; `--vs-template` byte-compares against the template clone with a **git trust anchor**, closing the "edit the file then regenerate the registry" self-attestation hole |
| Lifecycle skills | Seven skills ship in `.workspace/skills/`: `template-update` (sync from any ref), `workspace-repo-sync` (cross-device repo sync), `workspace-init` (adopt / first landing), `git-init` (create repo), `delivery-closure` (finish check), `docs-retrieval-maintenance` (docs index & aliases), `legacy-workspace-upgrade` (bootstrap an old workspace). Each must satisfy the 8-dimension contract, enforced by `skill_audit.py` |
| First-init protocol | [`.workspace/bootstrap.json`](.workspace/bootstrap.json) persists `python.mode` (**mandatory**: `uv` / `local-venv` / `mcp`, uv by default) and `git.status` (**optional**: initialized / declined); a configured mode is never rewritten by template upgrades |
| Business-agnostic foundation | [`WORKSPACE.md`](WORKSPACE.md) starts undefined and is shaped by the user's stable goals, scope, and acceptance criteria |
| One-path skills onboarding | The user provides a root directory; the Agent discovers entries, records fingerprints, and resolves duplicate names |
| Traceable environment facts | [`TOOLS.md`](TOOLS.md) records verified runtimes, MCP services, tools, sources, and side effects without granting authority |
| Progressive knowledge reuse | [`notes/`](notes/) stores reusable experience with a layered index (compact routing + on-demand inverted index) and a curation cadence against fragmentation |
| Client memory boundary | Workspace experience goes only to `notes/`; each client's global memory (Hermes memory / Claude Auto Memory / Codex built-in memory) keeps at most one pointer to `notes/INDEX.md` (§9.1) |
| Safe defaults | External skills are read-only by default; Hooks, dependency installation, networking, and data transfer are never automatic |

## Quick Start

### Option 1: Create a Repository from the GitHub Template (Recommended)

1. Click **Use this template** on the repository page and choose **Create a new repository**.
2. Clone the new repository and open its root with an Agent that supports Workspace rules.
3. The Agent first asks about the Python runtime (mandatory), then the git repo decision (optional).
4. Describe your business goal and get to work.

### Option 2: Clone Directly

```bash
git clone https://github.com/KinoluKaslana/WorkspaceTemplate.git my-workspace
cd my-workspace
```

> [!NOTE]
> The clone ships a `.git` that points at the template repository. On first init the Agent **wipes the template git markers** per §0.3 (only after confirming the remote and exporting any local-only commits as patch backups), then asks whether you want to turn the workspace into its own git repository. You don't have to manage remotes by hand; if you skip the init protocol and develop directly, remove `.git` yourself (`rm -rf .git`) or repoint it at your own remote to avoid pushing back to the template.

Then open `my-workspace` with your Agent. Codex reads [`AGENTS.md`](AGENTS.md), Claude Code reads [`CLAUDE.md`](CLAUDE.md), and Hermes reads [`HERMES.md`](HERMES.md). Other clients should be explicitly instructed to read [`AGENT_RULES.md`](AGENT_RULES.md) and [`.workspace/bootstrap.json`](.workspace/bootstrap.json) in full before working.

**Workspace built from a 2.x template (missing even the current skills)?** It cannot upgrade itself: there is no `template_sync.py` and no `legacy-workspace-upgrade` skill to run. Paste the prompt from the Chinese README section "旧版本工作区怎么升级（自举）" into that workspace's agent; the procedure behind it is [`UPGRADE.md`](UPGRADE.md) — Python is the only prerequisite, the rest comes from a template snapshot, and it ends with a `--rollback` path.

**Already on an older template workspace?** Say "initialize / adopt this workspace" to it, or have the Agent run the [workspace-init skill](#workspace-lifecycle-skills) — it inventories the workspace, migrates local edits into the custom layer, syncs the latest template, and handles the venv and git decisions.

## What Happens on First Open

The template does not choose a Python environment for the user. While `python.mode` is `unconfigured`, the first Agent must ask:

> Which Python runtime should this Workspace use? (**default: uv**)
> 1. **uv** (`uv venv`, default) — if uv is missing I show you how to install it and you run the install;
> 2. **Manual venv** (`python -m venv`, with the interpreter you choose);
> 3. **MCP Python** (use the Python tooling provided by this session; no local venv).

```mermaid
flowchart LR
    A[Agent opens Workspace] --> B{python.mode}
    B -->|unconfigured| C[Ask: uv / manual venv / MCP]
    C -->|uv| D1[uv venv .venv-major-minor]
    C -->|manual venv| D2[python -m venv .venv-major-minor]
    C -->|MCP Python| E[Verify MCP capability boundaries]
    D1 --> F[Record bootstrap and TOOLS facts]
    D2 --> F
    E --> F
    F --> G{git decision §0.3}
    G -->|wipe template .git first| H[Ask whether to create a repo]
    H -->|create now| I[git init + remote + bootstrap record]
    H -->|decline for now| J[git.status = declined<br>flip later via git-init skill]
    I --> K[workspace_status = active]
    J --> K
    B -->|configured| L[Verify the existing choice only]
```

The three Python modes are mutually exclusive; switching later requires an explicit user request, and a configured mode is never rewritten by template upgrades. **Choosing a Python runtime is mandatory; a local venv only exists if you pick uv or manual venv; git is optional** — a workspace that declines a repo stays a plain file workspace, and you can say "create a git repo" at any time to trigger the git-init skill.

### Python Runtime Options

| | uv (default) | Manual venv | MCP Python |
|---|---|---|---|
| Best for | Fast, reproducible dependency resolution | A box with a known-good interpreter and no uv | No local Python initialization, or a managed compute environment |
| Initialization | `uv venv .venv-<mm> --python <ver>` | `<python> -m venv .venv-<mm>` | Creates no local venv |
| Installing packages | `uv pip install --python .venv-<mm>/bin/python <pkg>` | `.venv-<mm>/bin/python -m pip install <pkg>` | Depends on verified MCP capabilities |
| Network by default | No; **when uv is missing we only show install methods, never install it for you** | No | Depends on the provider |
| Dependency persistence | Persists inside the venv | Persists inside the venv | Must not be assumed |
| If unavailable | `pyenv_init.py` exits 3 with install methods; the user may decline | Reports the exact interpreter or `venv`-module blocker | Never pretends configuration succeeded |

The bundled Python scripts use only the standard library and require Python 3.10 or later. None of them run before a runtime has been selected.

## Rule Layering and Integrity Verification

Governance files live in two layers. This is the fundamental reason template sync produces no merge conflicts:

| Layer | Files | Owner | Sync behavior |
|---|---|---|---|
| **Template layer (locked)** | `AGENT_RULES.md`, `AGENTS/CLAUDE/HERMES.md`, `README*`, `notes/rebuild_index.py`, `notes/_TEMPLATE.md`, `scripts/*.py`, `.workspace/skills/`, `.workspace/rule-clauses.json` | Template | **Whole-file replacement**, byte-identical to the template |
| **Custom layer** | `*.custom.md` (e.g. `AGENT_RULES.custom.md`) | Workspace | Never touched; **takes precedence on conflict with the template layer** |
| **Workspace layer** | `WORKSPACE.md`, `TOOLS.md`, `notes/*.md`, `skills/REGISTRY.md`, bootstrap local values, task directories | Workspace | Never touched |

Custom clauses declare their relationship to template clauses via `overrides: R<id>` / `extends: R<id>`. If a template clause is deleted or rewritten and a reference goes stale, the verification script names it and the sync skill asks you to rule on each one — **custom semantics are never silently dropped**.

**How do you confirm a rules file was not tampered with?** Two paths:

```bash
# Fast self-check: file hashes + clause-level comparison (add/delete/edit/reorder)
python3 scripts/verify_rules.py --workspace .
# Authoritative check: byte-compare against the template clone + git trust anchor
# (GitHub object hashes -> clone HEAD -> workspace bytes equal)
python3 scripts/verify_rules.py --workspace . --vs-template ~/Github/WorkspaceTemplate
```

Clause IDs (`<!-- id:Rn -->`, maintained by [`scripts/mark_rules.py`](scripts/mark_rules.py), registry at `.workspace/rule-clauses.json`; IDs are **globally unique and never reused** across files) make "the user added a clause or reordered clauses in a template file" show up as precise drift instead of a sync conflict later. The local registry can be regenerated, so **the authoritative verdict only trusts `--vs-template`**. `--vs-template` requires a local `git` (it runs `git status` / `rev-parse` on the template clone as the trust anchor); without git, use the fast self-check path.

## Hook Governance (Workspace-Level Mechanical Gate)

The template ships a **Hermes project-level plugin** (`.hermes/plugins/workspace-gate/`) that turns "read the governance files before writing workspace files" from a soft reminder into a **mechanical block** — see `AGENT_RULES.md` §12:

> **Want the mechanics?** The full write-up — beginner analogy, the read-evidence / fingerprint / coverage semantics, a per-harness breakdown, and a troubleshooting FAQ — is in **[docs/hooks-guide.md](docs/hooks-guide.md)** (Chinese).
> **Adding a new harness?** Event catalogue (when it fires / why it is required / what breaks without it), the 7-step adoption path and acceptance assertions: **[docs/hooks-catalog.md](docs/hooks-catalog.md)** (Chinese).
> **Docs index**: **[docs/README.md](docs/README.md)** — architecture, skills, tools, hooks, and the three must-run verification commands (Chinese).

| Item | Fact |
|---|---|
| Location | `.hermes/plugins/workspace-gate/` (`plugin.yaml` + `__init__.py` + `selfcheck.py`; template-layer locked files, hash-verified) |
| Gate rule | The target path resolves inside the workspace **and** this session lacks a complete, gap-free coverage (`1..total`) of the **current** revision → the call is blocked with a reason; subagents additionally need an exact-match delegation with a still-valid parent evidence; writes outside the workspace are unaffected |
| Events | `pre_tool_call` gate (internal errors block too — fail-closed), `post_tool_call` bookkeeping (`status=ok` only), `pre_llm_call` reminder (at most once per session), `on_session_end` audit (bounded, per turn) |
| Enabling it | `HERMES_ENABLE_PROJECT_PLUGINS=true` (allows project-plugin discovery) **and** `plugins.enabled: [workspace-gate]` in `~/.hermes/config.yaml` (loads it) — both are required |
| Tuning | Write the workspace-owned override `.hermes/plugins/workspace-gate/plugin.custom.json` (`enabled` / `gate_files` / `write_tools`); **do not edit the script itself** (locked layer — a template sync replaces it wholesale) |
| Self-check | `<configured-python> .hermes/plugins/workspace-gate/selfcheck.py` (synthetic-payload assertions; all green means the gate logic has not regressed) |
| Trust boundary | Prevents "accidental skipping", not "deliberate bypass": state files live in `.workspace/.hook-state/` (gitignored), and an agent holding `terminal`/`execute_code` can step outside the tool layer — defending against that needs a sandbox/OS permission layer |

The block is a **safety net, not a substitute**: the rules themselves still come from `AGENT_RULES.md` §2/§9/§11 plus the `delivery-closure` skill (**skill treats the will, hook treats the hand**). Obligations are unchanged when the hook is not enabled.

**How to confirm the gate is actually present** (do *not* use `hermes plugins list` — measured: it only lists bundled plugins, and a working project plugin never shows up there):

```bash
# (1) presence signal: the plugin logs one line every time it loads (v1.2.0+)
tail -3 "$HERMES_HOME/logs/workspace-gate-plugin.log"   # expect: workspace-gate active (v1.4.3, hermes_home=…)  ← version follows the plugin
# (2) behavioural check: inside the workspace, write a file before reading the rules — it must be blocked
#     with the workspace path and the resolved target in the message
# (3) state file: <workspace>/.workspace/.hook-state/<session_id>.json records what the session read
```

## Client Governance Hooks (Codex / Claude Code / DeepSeek Harness)

The template also ships a **client-side pre-write gate** (see `AGENT_RULES.md` §13): `.workspace/hooks/{hook,gate_core,wtarget,authz,template_anchor,cc_transcript}.py` plus `.codex/hooks.json` and `.claude/settings.json`. Coverage truth table:

| Write path | Codex | Claude Code |
|---|---|---|
| `apply_patch` / `Write`/`Edit` | Blocked + audited | Blocked + audited |
| Bash statically decidable writes (`>`/`>>`/`tee`/`sed -i` …) | Blocked when unread / audited | **Not blocked**; post-hoc attribution by transcript replay at `Stop`/`SessionEnd` (`cc-bash-write`) |
| Bash interpreter/wrapper (`python*`/`sh`/`bash`/…) | Blocked when unread (B′) | **Not blocked**; attributed post hoc (`class=interpreter_wrapper`) |
| Other undecidable Bash shapes | Audit only (`unresolved-command`) | **Not blocked**; `cc-bash-gap` with a reason code when attribution is impossible |

Boundary that must be stated alongside: **Claude Code Bash writes are neither subscribed nor blocked** — all it gets is **post-hoc attribution at session boundaries** (`would_deny` recomputed from the transcript). Three limits: `would_deny=true` does **not** mean the write was prevented; the transcript is same-uid mutable, so this is not a security boundary; read evidence requires byte-equal transcript content against disk (truncated/short reads grant nothing). Never infer that the whole repo's writes are governed; hook timeouts / load failures / untrusted projects are client-side fail-open. Per-harness details: **[docs/hooks-guide.md](docs/hooks-guide.md)**; event mapping and trust steps: `TOOLS.md` §1.1.

## Workspace Lifecycle Skills

Six skills ship with the template in `.workspace/skills/` (workspace-skills namespace) and are registered in `skills/REGISTRY.md` and `TOOLS.md`; each must satisfy the 8-dimension contract checked by `skill_audit.py`:

| Skill | Version | Trigger | What it does |
|---|---|---|---|
| [template-update](.workspace/skills/template-update/SKILL.md) | 3.2.1 | "sync the template / switch to a branch or tag", or version lag / verify drift | Sync template layer from **any ref** (branch/tag/commit): dry-run → backup (MANIFEST) → whole-file replacement → custom migration → `--vs-template`; `--prune` removes files deleted upstream |
| [workspace-repo-sync](.workspace/skills/workspace-repo-sync/SKILL.md) | 1.1.1 | New device, notes/TOOLS changed elsewhere, or tooling looks broken | `workspace_preflight.py` (9 checks) → user decides direction/merge/conflicts → pull → rebuild indexes → four-way re-verification |
| [workspace-init](.workspace/skills/workspace-init/SKILL.md) | 1.2.0 | "initialize / adopt this workspace", or no trustworthy lineage / no clause markers | Inventory → diff verdict → pre-v2.0 migration → Python (keep if configured) → git decision → registry → verify |
| [git-init](.workspace/skills/git-init/SKILL.md) | 1.1.0 | "create a git repo", or `git.status=declined` then mind changed | init → .gitignore → register → review staging → commit → verify tree; includes rollback |
| [delivery-closure](.workspace/skills/delivery-closure/SKILL.md) | 1.1.0 | Before declaring a task-mode deliverable complete | Artifact inventory (timestamp listing when no repo) → INDEX → note decision → index write-back → external verification → three-tier completion wording |
| [legacy-workspace-upgrade](.workspace/skills/legacy-workspace-upgrade/SKILL.md) | 1.0.0 | workspace built from an old template, missing current skills / `template_sync.py` | clone snapshot → `--apply` → `--sync-registry` + TOOLS rows → four acceptances |
| [docs-retrieval-maintenance](.workspace/skills/docs-retrieval-maintenance/SKILL.md) | 1.2.0 | docs changed, index stale, hit rate dropped | build/check → hit report → aliases only from real miss logs (cap 8, leave-one-out) → five acceptances |

## How It Works

The template separates entry points, policy, custom rules, business context, state, facts, routing, and experience:

| Layer | File | Responsibility |
|---|---|---|
| Client entry points | [`AGENTS.md`](AGENTS.md), [`CLAUDE.md`](CLAUDE.md), [`HERMES.md`](HERMES.md) | Direct different clients to the shared policy without copying it |
| Authoritative policy | [`AGENT_RULES.md`](AGENT_RULES.md) (locked) | Modes, scope, trust, safety, multi-Agent collaboration, delivery, rule layering, and completion criteria |
| Local rules | `*.custom.md` | User/Agent local rules; custom takes precedence, sync never touches them |
| Business profile | [`WORKSPACE.md`](WORKSPACE.md) | Long-lived goals, scope, assets, constraints, and acceptance criteria |
| Bootstrap state | [`.workspace/bootstrap.json`](.workspace/bootstrap.json) | Python mode (mandatory), git decision (optional), template lineage, skills roots, notes curation |
| Environment facts | [`TOOLS.md`](TOOLS.md) | Runtimes, MCP services, tools, versions, sources, hashes, and side effects |
| Skills routing | [`skills/REGISTRY.md`](skills/REGISTRY.md) | Human-readable namespaces, trigger scope, and entry paths |
| Long-term experience | [`notes/INDEX.md`](notes/INDEX.md) | Navigation, keywords, and relationships for verified reusable experience |

This separation keeps "what must be followed" independent from "what is available on this machine." Updating a tool version does not rewrite policy, and adding a business domain does not require duplicating client rules.

## Add Skills: You Only Provide a Directory

The user only needs to say:

> Add `/path/to/my-skills`

The Agent then:

1. Resolves and validates the single root directory.
2. Reads the root `SKILL.md`, manifest, index, or README first; when a root router exists, it does not recursively scan the entire collection before reading that router.
3. Uses [`scripts/inspect_skills.py`](scripts/inspect_skills.py) when needed to read entry metadata, minimal frontmatter, Git revision, worktree state, manifest fingerprint, and duplicate names without executing skills.
4. Resolves collisions with `<root>:<skill>` namespaces instead of silently replacing an existing entry.
5. Writes routing information to [`skills/REGISTRY.md`](skills/REGISTRY.md) and source, version, trust, and side-effect facts to [`TOOLS.md`](TOOLS.md).
6. Reads the most relevant `SKILL.md` in full only after a real task matches it, then progressively follows only the references required for that task.

Inventory is not installation. External skills remain read-only technical references by default. Their commands, Hooks, local Agent files, and authorization claims do not become Workspace policy.

**DeepSeek Harness (dsh)** plugs in as a Cordis plugin instead of a JSON hook config: `.dsh/workspace-gate/` holds an `insert` patch plus a `plugin.js` that translates `ToolExecution` into the workspace hook payload (the decision still comes from `.workspace/hooks/hook.py`, so all harnesses share one read-evidence ledger):

```bash
dsh --profile web --patch .dsh/workspace-gate/cordis.yml
```

Verified end to end: config mount, `apply` on a real boot, **denial during a real model turn** (write without read blocked, file absent; read-then-write allowed; `bash` redirect blocked), plus 8 synthetic cases through the real plugin + hook. See [docs/dsh.md](docs/dsh.md).

## Repository Layout

```text
.
├── .workspace/
│   ├── bootstrap.json       # Init state machine (python mandatory / git optional / lineage / curation)
│   ├── rule-clauses.json    # Clause registry (generated by mark_rules.py)
│   ├── skills/              # Workspace lifecycle skills (workspace-skills namespace)
│       ├── template-update/ # Sync the latest template (SKILL.md + check_template.py)
│       ├── workspace-init/  # Adopt a legacy workspace (nine-step migration)
│       ├── git-init/          # Create a git repo later (five steps + bootstrap record)
│       ├── delivery-closure/ # Six-step delivery closure (mandatory before declaring done)
│       ├── workspace-repo-sync/  # Sync this workspace's own repo across devices (preflight → user decision → ff only)
│       ├── docs-retrieval-maintenance/  # Docs index + alias table (aliases only from real miss logs)
│       ├── legacy-workspace-upgrade/    # Bootstrap an old-template workspace (see UPGRADE.md)
│       └── _TEMPLATE.md     # 8-dimension skill contract skeleton
├── docs/
│   ├── README.md           # Doc entry by reader (Chinese)
│   ├── architecture.md     # Four layers, read-evidence model, failure posture
│   ├── policy-language.md  # Dual track: controlled vocabulary + clause attributes + clause_lint
│   ├── skills.md           # Per-skill trigger / boundary / composition / contract
│   ├── tools.md            # Script usage, --json semantics, exit codes
│   ├── docs-retrieval.md   # Docs index & alias rules
│   ├── hooks-guide.md      # Per-harness hook deep dive (Chinese): mechanics, purpose, troubleshooting
│   └── hooks-catalog.md    # Event catalogue + new-harness adoption path (Chinese)
├── notes/
│   ├── INDEX.md             # Generated experience index (compact routing)
│   ├── _INVERTED.md         # Inverted index / related graph (on demand)
│   ├── _TEMPLATE.md         # Note template
│   └── rebuild_index.py     # Standard-library index builder (layered + curation status)
├── .hermes/
│   └── plugins/
│       └── workspace-gate/  # Workspace-level governance hook (read rules before writing; §12)
│           ├── plugin.yaml  # Plugin manifest (provides_hooks)
│           ├── __init__.py  # Gate implementation (template-layer locked)
│           └── selfcheck.py # Synthetic-payload self-check (no Hermes runtime needed)
├── scripts/
│   ├── inspect_skills.py    # Read-only skills inventory
│   ├── mark_rules.py        # Clause-ID tagging + registry generation
│   ├── verify_rules.py      # Two-level verification + --vs-template authoritative compare
│   ├── template_sync.py     # Sync template layer from any ref (dry-run / apply / prune / rollback)
│   ├── skill_audit.py       # 8-dim contract + dual registration + --sync-registry / --tools-snippet
│   ├── workspace_preflight.py  # 9-point cross-device preflight (report only)
│   ├── pyenv_init.py        # Python runtime: uv (default) / manual venv / MCP
│   ├── docs_index.py        # Docs section index (no body stored)
│   ├── docs_hit_report.py   # Retrieval hit report (threshold gate)
│   └── docs_alias_audit.py  # Alias provenance / dead pointer / leave-one-out audit
├── skills/
│   └── REGISTRY.md          # Skills routing table
├── AGENT_RULES.md           # Sole authoritative shared policy (locked read-only, clauses tagged with IDs)
├── AGENT_RULES.custom.md    # Local rule override layer (custom precedence; created on demand, not shipped)
├── HERMES.custom.md         # Hermes-specific rules / persona (created on demand)
├── CLAUDE.custom.md         # Claude Code-specific rules (created on demand)
├── AGENTS.custom.md         # Codex-specific rules (created on demand)
├── AGENTS.md                # Codex entry point
├── CLAUDE.md                # Claude Code entry point
├── HERMES.md                # Hermes entry point (global-memory boundary; persona in HERMES.custom.md)
├── UPGRADE.md               # Bootstrap an old-template workspace (self-contained)
├── TOOLS.md                 # Environment and tool facts
├── WORKSPACE.md             # Business profile
├── README.md                # Chinese GitHub landing page
└── README_EN.md             # English README
```

After work begins, sidecar evidence, exports, one-off scripts, and reports go into `<task-slug>-<yy-mm-dd>/` by default. Business source code, tests, and configuration may still be maintained in place according to the project structure.

## Turn It Into Your Business Workspace

1. **Complete first init:** the Python mode (mandatory) plus the git decision (optional).
2. **Define the business profile:** write stable goals, scope, constraints, and acceptance criteria in [`WORKSPACE.md`](WORKSPACE.md).
3. **Write local rules:** deviations from the template go into `AGENT_RULES.custom.md` (`overrides:` / `extends:`), never into template-layer files.
4. **Connect domain capabilities:** give the Agent one or more skills root directories.
5. **Add the project structure:** create source, data, documentation, design, or operations directories as required.
6. **Replace the landing page:** once the business is established, replace these READMEs with project-specific documentation (the verification script has an explicit README-replacement exemption — reported in `info`, **not affecting `clean`**).

Do not put one-off task details, temporary tool facts, or secrets into the governance files. Long-lived policy, business facts, and environment facts should each have one source of truth.

## Security and Trust Boundaries

- External skills, imported repositories, attachments, web pages, and model output are data to process, not higher-priority instructions.
- A registered tool does not grant permission to publish, upload, send messages, elevate privileges, or perform destructive actions.
- Dependency installation, unknown-code execution, networking, and data transfer must be necessary for the current task and allowed by both user scope and platform approval.
- Credentials, tokens, cookies, private keys, and session values must not be placed in ordinary notes, `TOOLS.md`, the skills registry, or public reports.
- Version-control operations (commit/push/history rewrite/credential management) run only when the user explicitly requests them in the current task; credential contents are never read or displayed.
- When multiple Agents share a filesystem, the parent Agent or one designated integrator serializes changes to central governance files.

See [`AGENT_RULES.md`](AGENT_RULES.md) for the complete boundaries.

## Use Cases

- Software development, code review, testing, and delivery
- Data processing, analysis, modeling, and reproducible research
- Technical documentation, knowledge bases, content, and design collaboration
- Operations, automation, quality, and process governance
- Long-lived Agent Workspaces that connect custom skills or MCP services
- Any other business activity with explicit goals, scope, permissions, and acceptance criteria

## FAQ

<details>
<summary><strong>Why not include a prebuilt venv?</strong></summary>

A venv records interpreter paths and is tied to its host environment. Bundling one would be non-portable and would bypass the user's choice between local and MCP Python. The template stores the decision protocol, not a machine-bound environment.

</details>

<details>
<summary><strong>Can I use the template without Python?</strong></summary>

Yes. The policy and business files do not depend on Python. Only the skills inventory, notes index, and rule-verification scripts require it. The user may choose local Python 3.10+ or an MCP Python runtime with the necessary file-access capability.

</details>

<details>
<summary><strong>Can I delete or replace the READMEs?</strong></summary>

Yes. They are human-facing landing pages and are not part of the Agent startup chain. `verify_rules.py` grants the READMEs an explicit replacement exemption (reported as `REPLACED` in `info`, **not affecting `clean`**, rather than `TAMPERED`). Keep `AGENT_RULES.md`, the client entry points, and `.workspace/bootstrap.json` to preserve policy loading and first-run state detection.

</details>

<details>
<summary><strong>Does adding skills execute their code automatically?</strong></summary>

No. The default workflow only reads entry metadata, computes fingerprints, and builds routing information. Installation, Hooks, networking, and code execution require separate necessity, authorization, and approval in a real task.

</details>

<details>
<summary><strong>Is this limited to Codex, Claude Code, or Hermes?</strong></summary>

No. The repository provides thin entry points for those three clients. Any Agent that can read Workspace files and is instructed to follow `AGENT_RULES.md` can use the same governance structure. Automatic loading behavior still depends on the client. Note: the Workspace-level persona definition lives only in `HERMES.custom.md` (binding Hermes alone) and never goes into `AGENT_RULES.md`, which governs every client.

</details>

<details>
<summary><strong>Can I edit AGENT_RULES.md directly?</strong></summary>

No, and you don't need to. Template-layer files are locked read-only — a direct edit is reported as drift by `verify_rules.py` and overwritten by the template original on sync. Put your custom rules in `AGENT_RULES.custom.md`: use `overrides: R<id>` to override a template clause, or `extends:` for a pure addition. On conflict with the template layer, custom wins.

</details>

<details>
<summary><strong>How do I confirm a rules file hasn't been changed?</strong></summary>

Two levels of verification. `verify_rules.py --workspace .` runs file hashes plus a clause-level comparison (a fast self-check). `--vs-template <template clone>` is the authoritative check — it first anchors the clone's git state (clean status + HEAD), then byte-compares the workspace files. The latter closes the "edit the file, then re-run mark_rules.py to forge the registry" self-attestation hole: local state cannot be its own judge, and the only trust anchor is the template's own git history.

</details>

<details>
<summary><strong>How do I upgrade a workspace built from an older template?</strong></summary>

Say "initialize / adopt this workspace" to it, or run the workspace-init skill directly. The nine-step flow inventories the current state, externalizes your local edits into `*.custom.md` (zero semantic loss), restores the template layer to the latest originals, completes the venv/git decisions, and runs full verification. If the workspace `.git` still points at the template repo, it first exports local-only commits as patches before wiping — no commits are lost.

</details>

<details>
<summary><strong>Does my workspace need its own git repository?</strong></summary>

That's your call (§0.3): choosing a Python runtime is mandatory (a local venv exists only for uv/manual venv), git is optional. Choosing "decline for now" during init is recorded as `git.status: declined` — not a terminal state. Say "create a git repo" later and the git-init skill completes repo creation, remote configuration, and bootstrap recording for multi-device sync.

</details>

<details>
<summary><strong>How do Hermes global memories relate to Workspace notes?</strong></summary>

Per the memory boundary in `AGENT_RULES.md` §9.1: reusable experience produced inside the Workspace goes to `notes/` only. Hermes does not proactively write such experience to its global persistent memory (`~/.hermes/memories/`); at most one pointer entry referencing `notes/INDEX.md` may remain there. Each Workspace's knowledge therefore stays in its own repository — versioned, auditable, and free of cross-workspace drift. The same boundary applies to any other client's global memory or preference mechanism.

</details>

## Maintenance and Contributions

- Change policy only in [`AGENT_RULES.md`](AGENT_RULES.md), update the template policy version, **and re-run `scripts/mark_rules.py mark` to refresh clause IDs and the registry** (clause IDs are verification anchors and must never be hand-edited).
- Change runtime, tool, or MCP facts only in [`TOOLS.md`](TOOLS.md) and bootstrap state.
- When adding skills routes, update both the facts registry and [`skills/REGISTRY.md`](skills/REGISTRY.md).
- The lifecycle skills (template-update / workspace-init / git-init) are owned here in `.workspace/skills/`; instances refresh them by sync, not by maintaining their own copies.
- README changes do not alter Agent authority or completion criteria.

Questions and suggestions are welcome through [Issues](https://github.com/KinoluKaslana/WorkspaceTemplate/issues). Improvements can be submitted through [Pull Requests](https://github.com/KinoluKaslana/WorkspaceTemplate/pulls).

---

<div align="center">

Start with a clear Workspace. Let the business choose the capabilities, and let the rules constrain them.

</div>
