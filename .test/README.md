# Skill Evaluation & Optimization

Evaluate and optimize SKILL.md files using [GEPA](https://github.com/gepa-ai/gepa) and a semantic assertion grader. Skills teach AI agents how to use Databricks features — this framework measures whether they actually help and uses evolutionary optimization to improve them.

The primary workflow is two steps:

1. **Evaluate** — `evaluate.py` runs test cases, grades assertions, and generates an HTML report for human review.
2. **Optimize** — `optimize.py --feedback` feeds human feedback into GEPA's evolutionary optimization loop.

For a deep technical explanation of the evaluation methodology, scoring, and architecture, see [TECHNICAL.md](TECHNICAL.md).

## Design Philosophy

This framework's evaluation approach is grounded in best practices from Anthropic's agent evaluation research and skill-building guidance:

- **WITH/WITHOUT comparison as a controlled experiment.** Anthropic's [Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf) recommends: "Compare the same task with and without the skill enabled. Count tool calls and total tokens consumed." Every evaluation runs the same prompt through the agent twice — once with the skill, once without — so the only variable is the skill content itself.

- **Human review before automated optimization.** Anthropic's [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) warns: "You won't know if your graders are working well unless you read the transcripts and grades from many trials." The HTML report from `evaluate.py` is this review step — it shows exactly what passed, what failed, and why, so a human can catch grader issues before GEPA optimizes in the wrong direction.

- **Granular per-assertion scoring, not binary judges.** The same Anthropic guide advises: "For tasks with multiple components, build in partial credit. A support agent that correctly identifies the problem and verifies the customer but fails to process a refund is meaningfully better than one that fails immediately." The semantic grader provides N+1 score levels from N assertions (e.g., 3/5 assertions passing) instead of a single yes/no, giving GEPA precise signal about what to fix.

- **Three grading tiers (deterministic, semantic, agent-based).** Anthropic identifies three grader types: code-based ("Fast, Cheap, Objective, Reproducible"), model-based ("Flexible, Captures nuance"), and human ("Gold standard quality"). The semantic grader mirrors this: deterministic checks (regex, substring) run first at zero cost, then an LLM evaluates remaining items, with agent-based grading adding transcript context when available.

- **Iterative feedback cycle.** The Skills Guide notes "Skills are living documents. Plan to iterate based on" user feedback. The evaluate → review → optimize → repeat workflow, with `feedback.json` as the bridge, follows this principle. The [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) methodology directly influenced the HTML viewer and feedback export mechanism.

---

## Setup

### 1. Install dependencies

```bash
# Core + optimization
uv pip install -e ".test/[all]"

# Agent evaluation only (optional)
uv pip install -e ".test/[agent]"
```

### 2. Configure authentication

Pick one authentication method for the LLM endpoints used by the evaluator (generation, semantic grading, reflection):

**Databricks AI Gateway (recommended)**

```bash
export DATABRICKS_API_KEY="dapi..."
export DATABRICKS_API_BASE="https://<account-id>.ai-gateway.cloud.databricks.com/mlflow/v1/serving-endpoints"
# litellm reads OPENAI_API_KEY for auth
export OPENAI_API_KEY="$DATABRICKS_API_KEY"
```

**Databricks direct**

```bash
export DATABRICKS_API_KEY="dapi..."
export DATABRICKS_API_BASE="https://<workspace>.cloud.databricks.com/serving-endpoints"
```

**OpenAI**

```bash
export OPENAI_API_KEY="sk-..."
export GEPA_REFLECTION_LM="openai/gpt-4o"
export GEPA_GEN_LM="openai/gpt-4o"
```

### 3. Configure the Claude Code agent

Agent evaluation runs a real Claude Code instance via the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python). The agent's environment is configured in `.test/claude_agent_settings.json`:

```json
{
    "env": {
        "ANTHROPIC_MODEL": "databricks-claude-opus-4-6",
        "ANTHROPIC_BASE_URL": "https://<account-id>.ai-gateway.cloud.databricks.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "${DATABRICKS_TOKEN:-dapi...}",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-4-6",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "databricks-claude-sonnet-4-6",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "databricks-claude-haiku-4-5",
        "ANTHROPIC_CUSTOM_HEADERS": "x-databricks-use-coding-agent-mode: true",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "DATABRICKS_CONFIG_PROFILE": "${DATABRICKS_CONFIG_PROFILE:-e2-demo-field-eng}",
        "DATABRICKS_API_KEY": "${DATABRICKS_TOKEN:-dapi...}"
    }
}
```

| Field | Purpose                                                                 |
|-------|-------------------------------------------------------------------------|
| `ANTHROPIC_MODEL` | Default model the agent uses. Currently points to Databricks by default |
| `ANTHROPIC_BASE_URL` | Claude API endpoint (Databricks AI Gateway or direct)                   |
| `ANTHROPIC_AUTH_TOKEN` | Auth token — supports `${VAR:-default}` interpolation                   |
| `ANTHROPIC_CUSTOM_HEADERS` | Extra headers (e.g., coding agent mode for Databricks)                  |
| `DATABRICKS_CONFIG_PROFILE` | Databricks CLI profile for MCP tools                                    |
| `DATABRICKS_API_KEY` | Databricks token for MCP tool calls                                     |

The `${VAR:-default}` syntax lets you reference environment variables with fallbacks. The agent runs with `bypassPermissions` mode so it doesn't prompt for tool approval.

---

## Quick Start

The recommended workflow is **evaluate first, then optimize with feedback**:

```bash
# Step 1: Evaluate and generate an HTML report
uv run python .test/scripts/evaluate.py databricks-metric-views

# Step 2: Open the HTML report, review results, click "Save Feedback" to export feedback.json

# Step 3: Optimize with human feedback
uv run python .test/scripts/optimize.py databricks-metric-views \
    --feedback .test/skills/databricks-metric-views/feedback.json --preset quick

# Step 4: Review and apply
diff databricks-skills/databricks-metric-views/SKILL.md \
     .test/skills/databricks-metric-views/optimized_SKILL.md
uv run python .test/scripts/optimize.py databricks-metric-views --apply-last
```

### Direct optimization (without prior evaluation)

```bash
# Check baseline scores (no optimization)
uv run python .test/scripts/optimize.py databricks-metric-views --dry-run

# Optimize with a quick pass (15 iterations)
uv run python .test/scripts/optimize.py databricks-metric-views --preset quick

# Optimize and immediately apply
uv run python .test/scripts/optimize.py databricks-metric-views --preset quick --apply
```

### Agent configuration

`evaluate.py` always runs the real Claude Code agent — there is no proxy mode. Agent configuration is set in `.test/claude_agent_settings.json` (see Setup section 3). Use `--agent-model` and `--agent-timeout` to control agent behavior:

```bash
# Custom agent model and timeout
uv run python .test/scripts/evaluate.py databricks-metric-views --agent-model claude-sonnet-4-20250514 --agent-timeout 180

# Multiple runs for variance analysis
uv run python .test/scripts/evaluate.py databricks-metric-views --runs 3
```

### With MLflow assessment feedback

```bash
# Inject real-world behavioral feedback from an MLflow experiment
uv run python .test/scripts/optimize.py databricks-metric-views \
    --mlflow-assessments <EXPERIMENT_ID> --preset quick
```

### Tool optimization

`--tools-only` runs a single global optimization pass using a cross-skill dataset. No per-skill loop is needed — tool descriptions are shared across all skills.

```bash
# Optimize all tool descriptions (single global pass)
uv run python .test/scripts/optimize.py --tools-only --preset quick

# Optimize specific tool modules only
uv run python .test/scripts/optimize.py --tools-only --tool-modules sql serving --preset quick

# Limit tasks per skill (useful with agent evaluation to reduce cost)
uv run python .test/scripts/optimize.py --tools-only --tool-modules sql --max-per-skill 2 --preset quick

# Dry run — score baseline without optimizing
uv run python .test/scripts/optimize.py --tools-only --preset quick --dry-run

# --all is accepted but has no effect (tools-only always runs a single pass)
uv run python .test/scripts/optimize.py --tools-only --all --preset quick

# Co-optimize skill + tool descriptions (per-skill, not global)
uv run python .test/scripts/optimize.py databricks-metric-views --include-tools \
    --tool-modules sql --preset quick
```

#### Cross-skill dataset filtering with `--tool-modules`

When `--tool-modules` is specified, both tool stats and the cross-skill dataset are filtered:

- **Tool stats** report only the requested modules (e.g., `Tool modules: 1, tools: 5` for `--tool-modules sql`).
- **Cross-skill dataset** includes only skills whose `tool_modules` in `manifest.yaml` overlap with the requested modules. Skills that *don't declare* `tool_modules` are always included as a safe fallback (e.g., `databricks-config`, `databricks-docs`). This means the dataset won't shrink to *only* SQL skills — general-purpose skills without the field are kept so the evaluator still has broad coverage.

To reduce the dataset further, add `tool_modules` to any remaining skills that should be excluded for certain module filters. Without `--tool-modules`, all skills are included regardless (no regression).

### Optimize all skills

```bash
uv run python .test/scripts/optimize.py --all --preset quick
```

---

## CLI Reference

### `evaluate.py` — Standalone evaluation (Step 1)

```
uv run python .test/scripts/evaluate.py <skill_name> [options]
```

Runs test cases WITH and WITHOUT the skill using the real Claude Code agent (via [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)), grades assertions using the semantic grader, and generates an HTML report for human review. Unlike `optimize.py`, which uses a fast litellm proxy for GEPA iterations, `evaluate.py` always runs the real agent for maximum accuracy.

| Flag | Description |
|------|-------------|
| `<skill_name>` | Name of the skill to evaluate |
| `--judge-model MODEL` | LLM model for semantic grading (default: `GEPA_JUDGE_LM` env or `databricks/databricks-claude-sonnet-4-6`) |
| `--runs N` | Number of evaluation runs for variance analysis (default: 1) |
| `--agent-model MODEL` | Claude model for agent execution (default: uses claude-agent-sdk default) |
| `--agent-timeout N` | Timeout in seconds for each agent run (default: 120) |
| `--no-html` | Skip HTML report generation |

After reviewing the HTML report, click "Save Feedback" to export `feedback.json`, then pass it to `optimize.py --feedback`.

### `audit_evals.py` — Test case quality audit

```
uv run python .test/scripts/audit_evals.py <skill_name> [options]
```

Runs the semantic grader in diagnostic mode to identify assertion quality issues:

- **Vague** assertions that would pass for wrong output
- **Strict** assertions that are exact substrings and miss valid variants
- **Missing coverage** where the response covers content no assertion checks

| Flag | Description |
|------|-------------|
| `<skill_name>` | Name of the skill to audit |
| `--judge-model MODEL` | Model for audit LLM calls |

Results are saved to `.test/skills/<skill-name>/eval_audit.json`.

### `optimize.py` — GEPA optimization (Step 2)

```
uv run python .test/scripts/optimize.py <skill_name> [options]
```

#### Core Options

| Flag | Description |
|------|-------------|
| `--preset quick\|standard\|thorough` | Optimization budget: 15 / 50 / 150 iterations per pass (default: `standard`) |
| `--dry-run` | Score baseline without optimizing |
| `--apply` | Optimize and immediately apply the result |
| `--apply-last` | Apply a previously saved result without re-running |
| `--all` | Optimize all skills that have `ground_truth.yaml` |
| `--feedback FILE` | Path to `feedback.json` from a prior evaluation (Step 1). Human feedback is injected into GEPA's reflection context |
| `--max-passes N` | Max optimization passes (default: 5). Early stops if improvement < 0.0005 |
| `--max-metric-calls N` | Override auto-scaled metric calls per pass |
| `--token-budget N` | Hard token ceiling — candidates over this are penalized |
| `--run-dir DIR` | Checkpoint directory. Resumes from last state if dir exists |

#### Model Selection

| Flag | Env Var | Default | Purpose |
|------|---------|---------|---------|
| `--gen-model` | `GEPA_GEN_LM` | `databricks/databricks-claude-sonnet-4-6` | Generates responses in proxy evaluator |
| `--reflection-lm` | `GEPA_REFLECTION_LM` | `databricks/databricks-claude-opus-4-6` | GEPA's reflection/mutation model |
| `--judge-model` | `GEPA_JUDGE_LM` | `databricks/databricks-claude-sonnet-4-6` | Semantic grading LLM |

Proxy evaluator models use [litellm provider prefixes](https://docs.litellm.ai/docs/providers): `databricks/`, `openai/`, `anthropic/`.

#### Tool Optimization

| Flag | Description |
|------|-------------|
| `--include-tools` | Include MCP tool docstrings as GEPA components alongside SKILL.md |
| `--tools-only` | Optimize only tool descriptions in a single global pass (no per-skill loop) |
| `--tool-modules sql serving ...` | Limit which tool modules are optimized (default: all) |
| `--max-per-skill N` | Max tasks per skill in the cross-skill dataset for `--tools-only` (default: 5) |

Available modules: `agent_bricks`, `aibi_dashboards`, `apps`, `compute`, `file`, `genie`, `jobs`, `lakebase`, `manifest`, `pipelines`, `serving`, `sql`, `unity_catalog`, `user`, `vector_search`, `volume_files`

#### Agent Evaluation

| Flag | Description |
|------|-------------|
| `--agent-model MODEL` | Model for agent execution (default: `ANTHROPIC_MODEL` env var) |
| `--agent-timeout N` | Timeout per agent run in seconds (default: 300) |
| `--parallel-agents N` | Number of parallel agent evaluations (default: 3) |
| `--mlflow-experiment NAME` | MLflow experiment for agent traces (default: `/Shared/skill-tests`) |

#### MLflow Feedback

| Flag | Description |
|------|-------------|
| `--mlflow-assessments EXPERIMENT_ID` | Fetch `ToolCallCorrectness` / `ToolCallEfficiency` assessments from an MLflow experiment and inject them into GEPA's reflection context |

#### Test Case Generation

| Flag | Description |
|------|-------------|
| `--generate-from FILE` | Generate test cases from a requirements file before optimizing |
| `--requirement "..."` | Inline requirement (repeatable) |

---

## Writing Test Cases

Test cases live at `.test/skills/<skill-name>/ground_truth.yaml`. Each test case defines what the skill should teach.

```yaml
metadata:
  skill_name: databricks-metric-views
  version: "1.0"

test_cases:
  - id: metric-views_create_sql_001
    inputs:
      prompt: "Create a metric view for order analytics with revenue measures"
    outputs:
      response: |  # Optional reference answer (not exact-matched)
        ```sql
        CREATE OR REPLACE VIEW main.default.order_metrics
        WITH METRICS LANGUAGE YAML
        $$
        source: main.default.orders
        measures:
          - name: Total Revenue
            expr: SUM(amount)
        $$
        ```
    expectations:
      expected_facts:
        - "Uses CREATE OR REPLACE VIEW with WITH METRICS LANGUAGE YAML"
        - "Defines measures with name and expr using aggregate functions"
      expected_patterns:
        - pattern: "WITH METRICS LANGUAGE YAML"
          description: "Metric view DDL syntax"
        - pattern: "MEASURE\\("
          description: "MEASURE() function for querying"
      guidelines:
        - "Must use WITH METRICS LANGUAGE YAML syntax"
      assertions:
        - "The response includes a complete CREATE VIEW statement with valid YAML metric definitions"
        - "Aggregate functions are used correctly within measure expressions"
        - "The metric view references a source table in Unity Catalog format (catalog.schema.table)"
      trace_expectations:  # Used when running with real agent (evaluate.py or --agent-model)
        required_tools:
          - mcp__databricks__execute_sql
        banned_tools:
          - Bash
        tool_limits:
          mcp__databricks__execute_sql: 3
    metadata:
      category: happy_path  # Used for stratified train/val splitting
```

| Field | Required | Description |
|-------|----------|-------------|
| `inputs.prompt` | Yes | The user question |
| `expectations.expected_facts` | No | Facts checked via substring match first, then semantic grading for failures (zero LLM cost for matches) |
| `expectations.expected_patterns` | No | Regex patterns checked deterministically (zero LLM cost) |
| `expectations.guidelines` | No | Natural-language guidelines converted to semantic assertions |
| `expectations.assertions` | No | Freeform assertion strings for semantic grading (1 LLM call for all) |
| `expectations.trace_expectations` | No | Agent behavioral validation (used by `evaluate.py` and when `--agent-model` is set) |
| `outputs.response` | No | Reference answer for grader comparison |
| `metadata.category` | Recommended | Stratified splitting (5+ test cases enables train/val split) |

### The `assertions` field

The `assertions` field accepts freeform natural-language strings. Unlike `expected_facts` (exact substring) or `expected_patterns` (regex), assertions are evaluated semantically by an LLM. This makes them ideal for checking higher-level properties:

```yaml
assertions:
  - "The response explains WHY metric views use YAML instead of SQL for definitions"
  - "Error handling is demonstrated for invalid measure expressions"
  - "The example shows how to query the metric view using the MEASURE() function"
```

All assertions from `expected_facts` (that fail substring match), `assertions`, and `guidelines` are batched into a single LLM call for semantic evaluation. This keeps cost low while providing per-assertion pass/fail with evidence.

### `manifest.yaml` — Scorer configuration

```yaml
skill_name: databricks-metric-views
tool_modules: [sql]  # Optional: MCP tool modules this skill uses

scorers:
  enabled: [sql_syntax, pattern_adherence, expected_facts_present]
  llm_scorers: [Safety, guidelines_from_expectations]
  default_guidelines:
    - "Responses must use Databricks-specific syntax"

quality_gates:
  syntax_valid: 1.0
  pattern_adherence: 0.9
```

The `tool_modules` field lists which MCP tool modules are relevant to the skill. When `--tools-only --tool-modules` is used, only skills whose `tool_modules` overlap with the requested modules are included in the cross-skill dataset. Behavior by value:

- **`tool_modules: [sql, compute]`** — included when `--tool-modules` contains `sql` or `compute`
- **`tool_modules: []`** — excluded from all `--tool-modules` filtered runs (no MCP tool dependency)
- **Field omitted** — always included (backward compatible fallback)

Without `--tool-modules`, all skills are included regardless. Available modules: `agent_bricks`, `aibi_dashboards`, `apps`, `compute`, `file`, `genie`, `jobs`, `lakebase`, `manifest`, `pipelines`, `serving`, `sql`, `unity_catalog`, `user`, `vector_search`, `volume_files`, `workspace`.

---

## Evaluation Criteria

Evaluation criteria are domain-specific rubrics that can be loaded on demand when scoring traces. They live in `.test/eval-criteria/` as SKILL.md files — the same format used by agent skills.

### Directory structure

Each criteria is a folder containing a `SKILL.md` (YAML frontmatter + markdown body) and an optional `references/` directory with detailed rubrics:

```
eval-criteria/
├── general-quality/          # Always loaded (applies_to: [])
│   └── SKILL.md
├── sql-correctness/          # Loaded for SQL-related skills (applies_to: [sql])
│   ├── SKILL.md
│   └── references/
│       └── DATABRICKS_SQL_PATTERNS.md
└── tool-selection/           # Always loaded (applies_to: [])
    ├── SKILL.md
    └── references/
        └── MCP_TOOL_GUIDE.md
```

### How it works

The framework scans `.test/eval-criteria/` for subdirectories containing a `SKILL.md` file, filtering by `applies_to` metadata against the skill's `tool_modules`. The discovered paths are used to load domain-specific rubrics during scoring. When MLflow supports the native `skills=` parameter (PR #21725), the criteria will be passed through automatically.

### `applies_to` filtering

The `applies_to` metadata field controls which criteria are available based on the skill's `tool_modules`:

- **`applies_to: [sql]`** — loaded only when the skill declares `tool_modules: [sql]`
- **`applies_to: []`** (or omitted) — always loaded (general-purpose criteria)

### Adding a new criteria

1. Create a folder: `.test/eval-criteria/<criteria-name>/`
2. Add `SKILL.md` with YAML frontmatter:
   ```yaml
   ---
   name: my-criteria
   description: >
     One-line description of when this criteria applies.
   metadata:
     category: evaluation
     version: "1.0"
     applies_to: [sql, compute]  # Empty list = always loaded
   ---

   ## Rubric content here...
   ```
3. Optionally add `references/` with detailed `.md` files
4. The criteria will be auto-discovered on the next evaluation run

For technical details on how criteria are loaded and injected, see [TECHNICAL.md — Adaptive Evaluation Criteria](TECHNICAL.md#adaptive-evaluation-criteria).

---

## Evaluation & Scoring

### Semantic assertion grader (default)

The evaluation uses a **semantic assertion grader** — a hybrid deterministic + LLM approach that provides per-assertion pass/fail with evidence. Each candidate skill is evaluated per-task using a WITH vs WITHOUT comparison:

1. **Generate WITH-skill response** — LLM generates with SKILL.md in context
2. **Generate WITHOUT-skill response** — LLM generates without skill (cached)
3. **Deterministic checks** (0 LLM calls):
   - `expected_patterns` — regex matching
   - `expected_facts` — case-insensitive substring matching
4. **Semantic grading** (1 LLM call per response):
   - Deterministic fact failures get a second chance via semantic matching
   - Freeform `assertions` are evaluated semantically
   - `guidelines` are converted to checkable assertions and evaluated semantically
   - All items batched into a single LLM call

**Cost per task:** 2 LLM generation calls (WITH + WITHOUT) + 1 semantic grading call per response. WITHOUT calls are cached, so subsequent iterations cost 1 generation + 1 grading call.

**Per-assertion classification:**

Each assertion is checked on BOTH responses and classified:

| Classification | Meaning |
|----------------|---------|
| `POSITIVE` | Fails without skill, passes with — skill is helping |
| `REGRESSION` | Passes without skill, fails with — skill is hurting |
| `NEEDS_SKILL` | Fails both — skill must teach this content |
| `NEUTRAL` | Same result either way — agent already knows this |

**Scoring weights:**

| Component | Weight | Source |
|-----------|--------|--------|
| Effectiveness delta | 40% | `pass_rate_with - pass_rate_without` |
| Pass rate WITH | 30% | Absolute assertion pass rate with skill |
| Token efficiency | 15% | Smaller candidates score higher |
| Structure | 5% | Syntax validation (Python, SQL, no hallucinated APIs) |
| Regression penalty | -10% | Rate of assertions that regressed |

### Why a semantic grader (not binary judges)?

The previous architecture used 3 binary MLflow judges (correctness, completeness, guideline_adherence), each returning `"yes"` / `"no"`. This collapsed multiple criteria into binary scores — when a mutation improved one fact but missed another, the score stayed at `"no"`. The semantic assertion grader provides more granular signal: 5 assertions produce 6 score levels (0/5 through 5/5) instead of 2 (`yes`/`no`). Per-assertion evidence tells GEPA exactly what is missing and what is passing, enabling targeted mutations.

The `judges.py` module still provides infrastructure used by the framework: `completion_with_fallback` (model fallback chain with exponential backoff), rate limiting, AI Gateway routing, and eval criteria discovery. It no longer performs the primary scoring.

### How GEPA uses evaluation feedback

GEPA's reflection LM reads `side_info` rendered as markdown headers. Key fields:

- **`Assertions`** — per-assertion pass/fail with evidence, method (deterministic/semantic), and type
- **`Failed_Assertions`** — exact list of what the skill should add, with evidence
- **`Passed_Assertions`** — what the skill already covers
- **`Regressions`** — assertions that pass WITHOUT but fail WITH (skill is confusing the agent)
- **`Needs_Skill`** — assertions that fail both ways (skill must teach this)
- **`Effectiveness`** — pass_rate_with, pass_rate_without, delta
- **`Human_Feedback`** — injected from feedback.json when `--feedback` is used
- **`scores`** — feeds GEPA's multi-objective Pareto frontier

This gives GEPA precise, per-assertion signals. A mutation that fixes one fact but misses another shows clear movement on individual assertions, guiding the next mutation.

### Agent evaluator

When using `evaluate.py` (which always runs the real agent) or `optimize.py` with `--agent-model`, the framework runs a real Claude Code agent and adds trace-level behavioral scoring on top of the semantic assertion grader:

**Scoring weights (same as proxy):**

| Component | Weight |
|-----------|--------|
| Effectiveness delta | 40% |
| Pass rate WITH | 30% |
| Token efficiency | 15% |
| Structure / execution success | 5% |
| Regression penalty | -10% |

The agent evaluator uses the same semantic assertion grader as the proxy evaluator, plus deterministic trace scorers (`required_tools`, `banned_tools`, `tool_sequence`) for behavioral compliance from `trace_expectations`.

### Dataset splitting

When a skill has 5 or more test cases, the framework supports stratified train/validation splitting via the `splitter.py` module. The `metadata.category` field on test cases (e.g., `happy_path`, `edge_case`, `error_handling`) is used for stratification to ensure both splits contain representative examples.

---

## Project Structure

```
.test/
├── eval-criteria/                  # Domain-specific evaluation rubrics
│   ├── general-quality/
│   │   └── SKILL.md
│   ├── sql-correctness/
│   │   ├── SKILL.md
│   │   └── references/
│   │       └── DATABRICKS_SQL_PATTERNS.md
│   └── tool-selection/
│       ├── SKILL.md
│       └── references/
│           └── MCP_TOOL_GUIDE.md
├── scripts/
│   ├── evaluate.py              # Step 1: standalone evaluation + HTML report
│   ├── optimize.py              # Step 2: GEPA optimization with --feedback
│   └── audit_evals.py           # Test case quality audit
├── claude_agent_settings.json   # Claude Code agent environment config
├── src/skill_test/
│   ├── agent/
│   │   └── executor.py          # Claude Agent SDK wrapper + MLflow tracing
│   └── optimize/
│       ├── runner.py            # Multi-pass GEPA orchestrator
│       ├── agent_evaluator.py   # Real Claude Code agent evaluator
│       ├── semantic_grader.py   # Hybrid deterministic + LLM assertion grader
│       ├── feedback.py          # Human feedback loader (feedback.json -> GEPA background)
│       ├── html_report.py       # Self-contained HTML report generator
│       ├── assessment_fetcher.py # MLflow assessment injection
│       ├── judges.py            # Infrastructure: completion_with_fallback, rate limiting, AI Gateway
│       ├── config.py            # Presets, model registration
│       ├── splitter.py          # Train/val dataset splitting
│       ├── tools.py             # MCP tool description extraction
│       └── utils.py             # Token counting, path resolution
└── skills/<skill-name>/
    ├── ground_truth.yaml        # Test cases
    ├── manifest.yaml            # Scorer configuration
    ├── evaluation.json          # Last evaluation results (from evaluate.py)
    ├── report.html              # Last HTML report (from evaluate.py)
    ├── feedback.json            # Human feedback (exported from HTML report)
    ├── eval_audit.json          # Test case audit results (from audit_evals.py)
    ├── optimized_SKILL.md       # Last optimization output
    └── last_optimization.json   # Metadata for --apply-last
```

---

## Troubleshooting

**Semantic grader returns unexpected results**: Run with debug logging:
```bash
MLFLOW_LOG_LEVEL=DEBUG uv run python .test/scripts/evaluate.py <skill-name>
```

**Rate limits**: The framework automatically falls back through alternative models (GPT-5-2, Gemini-3-1-Pro, Claude Opus 4.5, etc.) with exponential backoff when rate-limited. Configure custom fallbacks via `GEPA_FALLBACK_MODELS` env var (comma-separated).

**Agent eval fails**: Check that `.test/claude_agent_settings.json` has valid credentials and the model endpoint is accessible. The agent timeout is 120s for `evaluate.py` and 300s for `optimize.py` — increase with `--agent-timeout`.

**Resuming interrupted runs**: Use `--run-dir` for checkpointing:
```bash
# Start with checkpointing
uv run python .test/scripts/optimize.py databricks-metric-views --preset standard --run-dir ./opt_runs/mv

# Resume after interruption (same command)
uv run python .test/scripts/optimize.py databricks-metric-views --preset standard --run-dir ./opt_runs/mv

# Graceful stop mid-pass
touch ./opt_runs/mv/pass_1/gepa.stop
```

**Assertions too vague or strict**: Use the audit tool to diagnose:
```bash
uv run python .test/scripts/audit_evals.py <skill-name>
```
