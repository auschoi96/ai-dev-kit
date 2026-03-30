# Technical Deep Dive: Skill Evaluation & Optimization

This document explains the internals of the evaluation and optimization framework — how scoring works, what GEPA does under the hood, the agent evaluation pipeline, and MLflow integration.

For setup instructions and CLI usage, see [README.md](README.md).

---

## Table of Contents

- [The Core Question](#the-core-question)
- [Two-Step Workflow](#two-step-workflow)
- [Evaluation Methodology](#evaluation-methodology)
- [Proxy Evaluator (SkillBench)](#proxy-evaluator-skillbench)
- [Agent Evaluator](#agent-evaluator)
- [Semantic Grader](#semantic-grader)
- [GEPA Optimization Loop](#gepa-optimization-loop)
- [Multi-Pass Optimization](#multi-pass-optimization)
- [Human Feedback Integration](#human-feedback-integration)
- [Adaptive Evaluation Criteria](#adaptive-evaluation-criteria)
- [MLflow Assessment Injection](#mlflow-assessment-injection)
- [MLflow Tracing Integration](#mlflow-tracing-integration)
- [Component Scaling](#component-scaling)
- [Scoring Weights](#scoring-weights)
- [Dataset Splitting](#dataset-splitting)
- [Model Fallback Chain](#model-fallback-chain)
- [Skills vs Tools Optimization](#skills-vs-tools-optimization)
- [Architecture Diagram](#architecture-diagram)

---

## The Core Question

> "Does this skill actually help an agent produce better responses?"

A SKILL.md is only valuable if an agent produces **better responses with the skill than without it**. This is a testable claim — we generate responses both ways and compare. That comparison is the foundation of all evaluation and optimization.

---

## Two-Step Workflow

The framework separates evaluation from optimization into two distinct phases with a human review checkpoint in between. This approach follows established best practices from Anthropic's agent evaluation research:

- Anthropic's [Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf) recommends a "Performance comparison" phase: "Prove the skill improves results vs. baseline" by comparing "the same task with and without the skill enabled."
- Anthropic's [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) stresses that "LLM-as-judge graders should be closely calibrated with human experts to gain confidence that there is little divergence between the human grading and model grading" — the HTML report serves this calibration role.
- The [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) methodology establishes the pattern of parallel WITH/WITHOUT runs, an HTML viewer for human review, and `feedback.json` export for iterative improvement.

```
Step 1: evaluate.py                    Step 2: optimize.py --feedback
┌─────────────────────────┐            ┌──────────────────────────────┐
│  Run skill against all  │            │  GEPA reads feedback.json    │
│  test cases             │            │  + baseline diagnostics      │
│                         │            │                              │
│  Generate WITH/WITHOUT  │            │  Targeted mutations based    │
│  responses              │            │  on human + grader feedback  │
│                         │            │                              │
│  Semantic grading       │   human    │  Multi-pass optimization     │
│                         │  reviews   │                              │
│  HTML report output     ├───────────►│  Validation on held-out set  │
│                         │  feedback  │                              │
│  baseline scores +      │   .json    │  Output: optimized SKILL.md  │
│  diagnostics saved      │            │                              │
└─────────────────────────┘            └──────────────────────────────┘
```

### Why two steps?

1. **Human-in-the-loop** — The HTML report from `evaluate.py` lets a human review exactly where the skill is helping, hurting, or missing content. They can annotate feedback before optimization begins, preventing GEPA from optimizing toward the wrong objective.
2. **Cached baselines** — `evaluate.py` computes and caches WITHOUT-skill responses. These remain stable across all GEPA iterations in `optimize.py`, so the baseline is computed once and never re-generated.
3. **Feedback-driven optimization** — `optimize.py --feedback feedback.json` reads human annotations and injects them into GEPA's background context. The reflection LM sees both machine diagnostics (failed assertions, regressions) and human intent (what needs to change and why).

### Typical workflow

```bash
# Step 1: Evaluate current skill using the real Claude Code agent, produce HTML report
uv run python .test/scripts/evaluate.py databricks-genie

# Human reviews the HTML report, clicks "Save Feedback" to export feedback.json

# Step 2: Optimize with human feedback
uv run python .test/scripts/optimize.py databricks-genie \
    --feedback .test/skills/databricks-genie/feedback.json --preset quick
```

### evaluate.py vs agent_evaluator.py

`evaluate.py` (`.test/scripts/evaluate.py`) is a **standalone CLI script** that always runs the real Claude Code agent via the Claude Agent SDK. It is the entry point for Step 1 — producing an HTML report and `evaluation.json` for human review.

`agent_evaluator.py` (`.test/src/skill_test/optimize/agent_evaluator.py`) is an **internal module** used by `optimize.py` during GEPA iterations. It provides the `AgentEvaluator` class that wraps agent execution with GEPA's `(score, side_info)` evaluator interface.

Both use the same underlying `run_agent_sync_wrapper()` from `executor.py` and the same `semantic_grader.py` for assertion grading. The difference is their role:

| | `evaluate.py` | `agent_evaluator.py` |
|---|---|---|
| **Role** | Standalone evaluation (Step 1) | GEPA iteration evaluator (Step 2) |
| **Invocation** | CLI: `uv run python .test/scripts/evaluate.py <skill>` | Internal: called by `runner.py` |
| **Output** | HTML report + evaluation.json + feedback.json | GEPA score + side_info |
| **Agent mode** | Always real Claude Code agent | Configurable (proxy or real agent) |
| **Caching** | Fresh runs each time | Caches WITHOUT-skill baselines by prompt hash |

---

## Evaluation Methodology

Every evaluation follows a controlled experiment:

```
                      ┌─────────────────────────────┐
                      │        Same LLM + Prompt     │
                      │                               │
                      │   ┌─────────┐   ┌─────────┐  │
                      │   │  WITH   │   │ WITHOUT │  │
                      │   │  skill  │   │  skill  │  │
                      │   └────┬────┘   └────┬────┘  │
                      │        │              │       │
                      │   ┌────▼────┐   ┌────▼────┐  │
                      │   │Semantic │   │Semantic │  │
                      │   │ grader  │   │ grader  │  │
                      │   └────┬────┘   └────┬────┘  │
                      │        │              │       │
                      │  pass_rate_with  pass_rate_without
                      │        │              │       │
                      │   effectiveness = delta       │
                      └─────────────────────────────┘
```

1. **WITH-skill trial** — LLM generates a response with the SKILL.md in system context. The skill teaches Databricks-specific patterns the model wouldn't otherwise know.
2. **WITHOUT-skill trial** — Same LLM, same prompt, no skill in context. This is the control — what the model already knows on its own.
3. **Semantic grading** — A hybrid deterministic + LLM grader checks per-assertion pass/fail on both responses. Deterministic checks (patterns, facts) cost zero; only failures and freeform assertions are sent to an LLM for semantic evaluation (1 LLM call).

The WITHOUT-skill response is **cached by prompt hash** — since the model and prompt don't change, the baseline is stable across all GEPA iterations. Every candidate SKILL.md is compared against the same fixed control.

### Two layers of comparison

| Layer | What's compared | What it measures |
|-------|----------------|-----------------|
| **Within each evaluation** | WITH vs WITHOUT skill | Whether a given SKILL.md adds value over a bare LLM |
| **Across optimization** | Original vs optimized SKILL.md | Whether GEPA's mutations improved the skill |

### Why this is rigorous

- **Same model, same prompts** — the only variable is the skill content
- **Cached baselines** — WITHOUT-skill responses don't change between iterations
- **Per-assertion evidence** — every assertion has a quote or explanation from the response (auditable)
- **Train/val split** — with 5+ test cases, stratified splitting prevents overfitting
- **Deterministic structure checks** — syntax validation uses regex/AST parsing, not LLM judgment

---

## Proxy Evaluator (SkillBench)

The proxy evaluator uses `litellm.completion` to generate responses and the semantic assertion grader (`semantic_grader.py`) to score them. It's fast (~1 LLM call per task per iteration for grading) and doesn't test actual tool usage.

### Per-task evaluation flow

1. **Phase 1: Generate WITH-skill response** — `litellm.completion` with skill + tool descriptions as system context, temperature=0
2. **Phase 2: Generate WITHOUT-skill response** — Same prompt, no skill. Cached by prompt hash (computed once, reused across all GEPA iterations)
3. **Phase 3: Semantic grading** — Hybrid deterministic + LLM grading:
   - Check `expected_patterns` deterministically (regex — zero cost)
   - Check `expected_facts` deterministically (substring — zero cost)
   - Collect: deterministic fact failures + freeform `assertions` + `guidelines`
   - Batch all collected items into **1 LLM call** for semantic evaluation
   - Upgrade deterministic fact failures that pass semantic grading
   - Classify each assertion as POSITIVE / REGRESSION / NEEDS_SKILL / NEUTRAL by comparing WITH vs WITHOUT results

**Cost per task:** 1 LLM call for semantic grading (down from 5 in the previous multi-judge architecture). The single call handles all assertion types — facts that failed substring matching, freeform assertions, and guideline compliance — in one batch. Deterministic pattern and fact checks are free.

### Proxy scoring weights

| Weight | Dimension | Source |
|--------|-----------|--------|
| **40%** | Effectiveness Delta | `pass_rate_with - pass_rate_without` — primary skill contribution signal |
| **30%** | Pass Rate With | Absolute assertion pass rate on WITH-skill response |
| **15%** | Token Efficiency | Smaller candidates score higher (bonus up to 1.15x) |
| **5%** | Structure | Python/SQL syntax validation (deterministic, zero cost) |
| **-10%** | Regression Penalty | Assertions that pass WITHOUT but fail WITH (skill is hurting) |

### Rate limiting

A module-level rate limiter caps concurrent LLM calls at 4 with a 0.2s minimum interval to avoid overwhelming serving endpoints. Configurable via `GEPA_MAX_CONCURRENT_LLM` and `GEPA_MIN_LLM_INTERVAL` environment variables.

---

## Agent Evaluator

The agent evaluator (`agent_evaluator.py`) runs a **real Claude Code instance** via `claude_agent_sdk.ClaudeSDKClient` and scores actual agent behavior — tool selection, multi-turn reasoning, and execution success.

### How it works

1. **Run agent WITH skill** — Claude Code executes with candidate SKILL.md injected as system prompt
2. **Run agent WITHOUT skill** — Same task, no skill (cached by prompt hash)
3. **Semantic grading** — Same hybrid deterministic + LLM grader as the proxy evaluator
4. **Per-assertion classification** — POSITIVE / REGRESSION / NEEDS_SKILL / NEUTRAL
5. **Execution success** — Ratio of successful tool calls
6. **Token efficiency** — Candidate size vs budget
7. **Regression penalty** — Penalty proportional to regression rate

### Agent scoring weights

| Weight | Dimension | Source |
|--------|-----------|--------|
| **40%** | Effectiveness delta | `pass_rate_with - pass_rate_without` |
| **30%** | Pass Rate With | Absolute assertion pass rate on WITH-skill response |
| **15%** | Token efficiency | Smaller candidates score higher |
| **5%** | Structure | Syntax validity (deterministic) |
| **-10%** | Regression penalty | Assertions that regressed |

### Two modes

| Mode | How | GEPA iterations | Baseline + validation | Speed |
|------|-----|----------------|----------------------|-------|
| **Hybrid** | `evaluate.py` for baseline, `optimize.py` for GEPA | Fast proxy | Real agent | ~12-20 min |
| **Full agent** | `optimize.py --agent-model` for all iterations | Real agent | Real agent | ~30-60 min |

Hybrid mode is recommended — use `evaluate.py` for a real agent baseline with human review, then `optimize.py` with the fast proxy for GEPA iterations.

### Hybrid mode flow

```
1. Agent baseline:   Run real agent on original SKILL.md (all training tasks)
2. GEPA loop:        Use fast proxy evaluator for mutations
3. Agent validation:  Run real agent on best candidate (all training tasks)
4. Compare:          Report proxy scores vs agent scores side-by-side
```

### Claude Code agent execution (`executor.py`)

The `run_agent_sync_wrapper()` function:

1. **Loads environment** from `.test/claude_agent_settings.json` with `${VAR:-default}` interpolation
2. **Creates `ClaudeAgentOptions`** with MCP servers, system prompt (candidate SKILL.md), allowed tools, and `bypassPermissions` mode
3. **Streams events** via `ClaudeSDKClient` — captures `AssistantMessage` (tool uses, text), `UserMessage` (tool results), `SystemMessage`, `ResultMessage`
4. **Builds `TraceMetrics`** from events — tool calls, token counts, file operations, turn counts
5. **MLflow Stop hook** fires on completion — calls `mlflow.claude_code.tracing.process_transcript()` to convert the transcript into an MLflow trace

### Estimated cost (hybrid mode)

| Phase | Calls | Cost | Time |
|-------|-------|------|------|
| Agent baseline (8 tasks x WITH + WITHOUT) | 16 agent runs | ~$4 | ~5-10 min |
| GEPA proxy iterations (quick preset) | ~350 LLM calls | ~$0 (Databricks) | ~3-4 min |
| Agent validation (8 tasks x WITH only) | 8 agent runs | ~$2 | ~3-5 min |
| **Total** | | **~$6** | **~12-20 min** |

---

## Semantic Grader

The framework uses a unified semantic grading approach (`semantic_grader.py`) that replaces the previous separate multi-judge + assertions architecture. It implements a **3-phase hybrid strategy** that mirrors the three grader types identified in Anthropic's [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents): code-based graders ("Fast, Cheap, Objective, Reproducible"), model-based graders ("Flexible, Captures nuance"), and transcript-aware grading for behavioral verification.

### How it works: 3-phase grading pipeline

```
                     PHASE 1: Deterministic (zero LLM cost)
                     ─────────────────────────────────────
Expected patterns ──► Regex match (_check_patterns)
                           │
Expected facts    ──► Substring match (_check_facts)
                           │
                     ┌─────▼──────────────────────────┐
                     │   Collect for LLM grading:      │
                     │   • Fact failures (re-check)    │
                     │   • Freeform assertions         │
                     │   • Guidelines                  │
                     └─────┬──────────────────────────┘
                           │
                     PHASE 2: Agent-based grading (when transcript available)
                     ─────────────────────────────────────────────────────
                     ┌─────▼──────────────────────────┐
                     │  _agent_grade()                 │
                     │  Anthropic API + transcript     │
                     │  Per-item: pass/fail + evidence │
                     │                                 │
                     │  On failure, falls back to ─────┼──┐
                     └─────┬──────────────────────────┘  │
                           │                              │
                     PHASE 3: Semantic fallback (1 LLM call)
                     ──────────────────────────────────────
                     ┌─────▼──────────────────────────┐  │
                     │  _semantic_grade()         ◄────┘  │
                     │  litellm batched LLM call       │
                     │  Per-item: pass/fail + evidence │
                     └─────┬──────────────────────────┘
                           │
                     ┌─────▼──────────────────────────┐
                     │  Upgrade facts that pass        │
                     │  semantic/agent check            │
                     │                                 │
                     │  Classify: POSITIVE/REGRESSION/ │
                     │  NEEDS_SKILL/NEUTRAL            │
                     └────────────────────────────────┘
```

### Phase 1: Deterministic checks (zero LLM cost)

| Check type | How it works | Example |
|------------|-------------|---------|
| **Pattern** | Regex with `min_count`/`max_count` | `MEASURE\(` with `min_count: 1` |
| **Fact** | Case-insensitive substring match | `"MEASURE() function"` found/missing |

These run first on both WITH and WITHOUT responses. Deterministic checks are authoritative for patterns (regex is exact) and serve as a fast pre-filter for facts. As Anthropic notes, code-based graders are the first choice because they are "Fast, Cheap, Objective, Reproducible, Easy to debug."

### Phase 2: Agent-based grading (Anthropic API + transcript)

When `evaluate.py` provides an agent execution transcript (tool calls, tool results, text events), the grading step uses **agent-based grading** via a direct Anthropic API call (`_agent_grade()`). The grader prompt includes both the response text and the full execution transcript, allowing it to verify behavioral assertions (e.g., "agent called the correct MCP tool") that cannot be checked from the response text alone.

This implements Anthropic's recommendation to "grade the transcript" — as the Evals guide explains: "heuristics-based code quality rules can evaluate the generated code based on more than passing tests, and model-based graders with clear rubrics can assess behaviors like how the agent calls tools or interacts with the user."

Agent-based grading uses the same auth as the Claude Code agent (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` from `.test/claude_agent_settings.json`). If the Anthropic API call fails for any reason, it falls back transparently to Phase 3.

### Phase 3: Semantic grading fallback (1 LLM call, batched)

Items that need semantic evaluation are batched into a single LLM call via `_semantic_grade()` using litellm:

- **Fact failures** — Deterministic substring matching is conservative. A fact like `"MEASURE() function"` might fail substring match even though the response explains the concept using different wording. Failed facts get a second chance via semantic grading, and are upgraded if the LLM confirms the content is present.
- **Freeform assertions** — Natural-language checks from `assertions` in the test case (e.g., "Response explains why MEASURE() is preferred over COUNT()"). These require LLM understanding and cannot be checked deterministically.
- **Guidelines** — Converted to checkable assertions (e.g., `"The response follows this guideline: Use Unity Catalog three-level namespace"`). Guidelines from `ground_truth.yaml`, `manifest.yaml` defaults, and `--focus` areas are all evaluated here.

The LLM returns a JSON array with per-assertion `passed` (bool) and `evidence` (quote from response). This provides more granular signal than binary judges — 5 assertions produce 6 possible score levels (0/5 through 5/5) vs. 2 levels from a yes/no judge.

### WITH vs WITHOUT classification

After grading both responses, each assertion is classified by comparing results:

| Classification | WITH result | WITHOUT result | Meaning |
|---------------|-------------|----------------|---------|
| **POSITIVE** | pass | fail | Skill is helping — it taught the agent something |
| **REGRESSION** | fail | pass | Skill is hurting — it confused the agent |
| **NEEDS_SKILL** | fail | fail | Skill must add this content — neither response covers it |
| **NEUTRAL** | pass | pass | Agent already knows this — skill isn't needed here |

These classifications map directly to Anthropic's distinction between **capability evals** ("What can this agent do well?") and **regression evals** ("Does the agent still handle all the tasks it used to?"). POSITIVE assertions are capability wins; REGRESSION assertions are regression failures. Both are tracked simultaneously in every evaluation.

The classifications appear in GEPA's `side_info` and give the reflection LM precise, actionable targets:
- **NEEDS_SKILL** items tell GEPA what content to add to the skill
- **REGRESSION** items tell GEPA what content is causing confusion
- **POSITIVE** items confirm what's working (don't remove these)
- **NEUTRAL** items are candidates for removal (save tokens)

### Why this grading approach?

The design follows several principles from Anthropic's [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents):

1. **Partial credit over binary scoring.** "For tasks with multiple components, build in partial credit. A support agent that correctly identifies the problem and verifies the customer but fails to process a refund is meaningfully better than one that fails immediately." The per-assertion approach provides N+1 score levels from N assertions, versus 2 levels from a binary yes/no judge.

2. **Grade what was produced, not the path taken.** "There is a common instinct to check that agents followed very specific steps like a sequence of tool calls in the right order. We've found this approach too rigid and results in overly brittle tests, as agents regularly find valid approaches that eval designers didn't anticipate." Semantic assertions grade the outcome; `trace_expectations` are opt-in and reserved for cases where tool selection genuinely matters.

3. **Structured rubrics per dimension.** "It can also help to create clear, structured rubrics to grade each dimension of a task, and then grade each dimension with an isolated LLM-as-judge rather than using one to grade all dimensions." Each assertion is an independent dimension with its own pass/fail and evidence.

4. **Cost efficiency.** The previous architecture used three separate MLflow judges — **5 LLM calls per task**. The semantic grader collapses this to **1 LLM call per task** while providing more granular signal:

| Aspect | Previous (multi-judge) | Current (semantic grader) |
|--------|----------------------|--------------------------|
| LLM calls per task | 5 (3 judges x WITH + WITHOUT, minus cached) | 1 (batch all assertions) |
| Score granularity | 2 levels per judge (yes/no) | N+1 levels (N assertions) |
| Failure signal | "Correctness: no" (opaque) | "Missing: MEASURE() function" (specific) |
| Regression detection | Separate regression judge (conditional) | Built into classification (always) |
| Guidelines | Separate judge dimension | Evaluated as assertions in same call |

### Worked example: grading a Genie Space test case

To make the grading pipeline concrete, here's a step-by-step walkthrough using a real test case from `databricks-genie`. The full prompt templates are in `semantic_grader.py` — see `_SEMANTIC_GRADER_PROMPT` (line ~108) and `_AGENT_GRADER_PROMPT` (line ~212).

**Test case input:**

```yaml
# From .test/skills/databricks-genie/ground_truth.yaml
- id: create_genie_space
  inputs:
    prompt: "Create a Genie Space for sales analytics using ac_demo.dc_assistant.customers"
  expectations:
    expected_patterns:
      - pattern: create_or_update_genie
        min_count: 1
        description: Must call the create_or_update_genie MCP tool
    expected_facts:
      - ac_demo.dc_assistant.customers
      - sample_questions
    assertions:
      - "The response attempts to actually invoke the create_or_update_genie MCP tool"
    guidelines:
      - "The agent should pass both tables and sample questions to create_or_update_genie"
```

#### Phase 1: Deterministic checks (zero LLM cost)

`_check_patterns()` and `_check_facts()` run regex and substring matching directly on the response text:

```
Pattern "create_or_update_genie" (min_count: 1)
  → re.findall() finds 1 match
  → PASS  (method: deterministic, type: pattern)
  → evidence: "Found 1 matches (need >=1)"

Fact "ac_demo.dc_assistant.customers"
  → case-insensitive substring found
  → PASS  (method: deterministic, type: fact)
  → evidence: "Found: ac_demo.dc_assistant.customers"

Fact "sample_questions"
  → substring NOT found (response used "example questions" instead)
  → FAIL  (method: deterministic, type: fact)
  → evidence: "Missing: sample_questions"
  → ⚠ queued for semantic re-check in Phase 2
```

Two assertions resolved at zero cost. One failure gets a second chance.

#### Phase 2: Agent-based grading (1 LLM call, batched)

The grader collects three kinds of items into a single batch — the failed fact (for a second chance), freeform assertions, and guidelines:

```
Items sent to LLM:
  0. "The response mentions or explains: sample_questions"     ← failed fact retry
  1. "The response attempts to actually invoke the             ← freeform assertion
      create_or_update_genie MCP tool"
  2. "The response follows this guideline: The agent should    ← guideline
      pass both tables and sample questions to
      create_or_update_genie"
```

Since `evaluate.py` provides an agent execution transcript, the grader uses `_agent_grade()` which sends the response **and** the transcript to the Anthropic API. The prompt includes:

```
Grade each assertion below against the agent's response and transcript.

## Agent Response
I'll create a Genie Space for sales analytics. [calls create_or_update_genie
with tables=["ac_demo.dc_assistant.customers"], example_questions=["What is
the total revenue by region?", ...]]

## Execution Transcript
[TOOL_USE] mcp__databricks__create_or_update_genie: {"display_name": "Sales
  Analytics", "table_names": ["ac_demo.dc_assistant.customers"], ...}
[TOOL_RESULT] {"space_id": "01f2...", "status": "CREATED"}

## Assertions to Evaluate
0. The response mentions or explains: sample_questions
1. The response attempts to actually invoke the create_or_update_genie MCP tool
2. The response follows this guideline: The agent should pass both tables and
   sample questions to create_or_update_genie
```

The LLM returns structured JSON:

```json
[
  {"index": 0, "passed": true,  "evidence": "Tool call includes example_questions param (synonym for sample_questions)"},
  {"index": 1, "passed": true,  "evidence": "Transcript shows [TOOL_USE] mcp__databricks__create_or_update_genie"},
  {"index": 2, "passed": true,  "evidence": "Tool call includes both table_names and example_questions"}
]
```

The `"sample_questions"` fact that failed deterministic substring matching now **passes** — the LLM recognized that `example_questions` is semantically equivalent. The grader upgrades the original fact result:

```
Fact "sample_questions"
  → was FAIL (deterministic), now PASS (semantic)
  → evidence updated: "Semantic match: Tool call includes example_questions param"
```

If the Anthropic API call had failed (auth error, timeout, etc.), the grader would fall back transparently to `_semantic_grade()` which uses `litellm` with the same batched prompt — just without the transcript context.

#### Phase 3: WITH vs WITHOUT classification

The same assertions are graded on the WITHOUT-skill response (agent running without SKILL.md). Then `_classify_assertion()` compares each pair:

```
Assertion                          WITH    WITHOUT   Classification
─────────────────────────────────  ──────  ────────  ──────────────
Pattern: create_or_update_genie    PASS    FAIL      POSITIVE
Fact: ac_demo.dc_assistant         PASS    PASS      NEUTRAL
Fact: sample_questions             PASS    FAIL      POSITIVE
Assertion: invokes the MCP tool    PASS    FAIL      POSITIVE
Guideline: tables + questions      PASS    PASS      NEUTRAL
```

3 POSITIVE assertions (skill taught the agent something), 2 NEUTRAL (agent already knew). Zero REGRESSION.

#### Final score

`compute_score()` calculates the composite:

```
pass_rate_with       = 5/5 = 1.00
pass_rate_without    = 2/5 = 0.40
effectiveness_delta  = 1.00 - 0.40 = 0.60
regression_rate      = 0/5 = 0.00
token_efficiency     = 1.00  (candidate size within budget)
structure            = 1.00  (no syntax errors)

final = 0.40 × 0.60    effectiveness_delta
      + 0.30 × 1.00    pass_rate_with
      + 0.15 × 1.00    token_efficiency
      + 0.05 × 1.00    structure
      - 0.10 × 0.00    regression_rate
      ────────────
      = 0.74
```

This score, along with per-assertion evidence and classifications, flows into GEPA's `side_info` for targeted reflection.

### Effectiveness scoring

Effectiveness is the primary optimization signal:

```
effectiveness_delta = pass_rate_with - pass_rate_without
```

Where `pass_rate_with` is the fraction of assertions passing on the WITH-skill response, and `pass_rate_without` is the same fraction on the WITHOUT-skill response. A positive delta means the skill is adding value; a negative delta means it's hurting. This directly implements the Skills Guide's recommendation to "Compare the same task with and without the skill enabled" to prove the skill improves results versus baseline.

---

## GEPA Optimization Loop

[GEPA](https://github.com/gepa-ai/gepa) (Generalized Evolutionary Prompt Architect) treats the SKILL.md as a text artifact to optimize. Its `optimize_anything` API takes a seed candidate, an evaluator function, and a dataset.

```
┌──────────────────────────────────────────────────┐
│                GEPA optimize_anything             │
│                                                   │
│  seed_candidate ──► evaluator(candidate, task)    │
│       │                    │                      │
│       │              (score, side_info)           │
│       │                    │                      │
│       │           reflection LM reads             │
│       │           side_info diagnostics           │
│       │                    │                      │
│       │              proposes mutation             │
│       │                    │                      │
│       └──── best_candidate (Pareto frontier) ◄───┘│
└──────────────────────────────────────────────────┘
```

Each iteration within a pass:

1. **Reflect** — The reflection LM reads `side_info` from the previous evaluation. This includes per-assertion pass/fail with evidence, classification labels (POSITIVE/REGRESSION/NEEDS_SKILL/NEUTRAL), human feedback, and effectiveness metrics.
2. **Mutate** — Based on the diagnostics, proposes a targeted mutation to the SKILL.md. Mutations are surgical — informed by exactly which assertions failed and why.
3. **Evaluate** — The evaluator scores the mutated candidate on a task (WITH/WITHOUT, semantic grading, composite score).
4. **Select** — GEPA tracks a Pareto frontier of best candidates. Improvements are kept; others discarded.

The key insight: because `side_info` contains **per-assertion evidence** (not just aggregate scores), the reflection LM sees exactly what failed and what the expected content should be — leading to more targeted mutations than aggregate "correctness: no" feedback.

### `side_info` structure

```python
side_info = {
    "Task": "Create a metric view for order analytics...",

    # Per-assertion results — GEPA sees each as an actionable item
    "Assertions": [
        {
            "text": "Uses CREATE OR REPLACE VIEW with WITH METRICS LANGUAGE YAML",
            "passed": True,
            "evidence": "Response says 'use CREATE OR REPLACE VIEW'",
            "classification": "POSITIVE",
        },
        {
            "text": "MEASURE() function for querying metric views",
            "passed": False,
            "evidence": "No mention of MEASURE() function",
            "classification": "NEEDS_SKILL",
        },
    ],

    # Quick-scan lists for the reflection LM
    "Failed_Assertions": [
        "MEASURE() function for querying metric views — No mention of MEASURE() function"
    ],
    "Passed_Assertions": [
        "Uses CREATE OR REPLACE VIEW with WITH METRICS LANGUAGE YAML — Response says 'use CREATE OR REPLACE VIEW'"
    ],

    # Classification-specific lists
    "Regressions": [
        "Correct aggregate syntax — WITH-skill response uses deprecated SUM() form"
    ],
    "Needs_Skill": [
        "MEASURE() function for querying metric views — No mention of MEASURE() function"
    ],

    # Effectiveness metrics
    "Effectiveness": {
        "pass_rate_with": 0.67,
        "pass_rate_without": 0.33,
        "delta": 0.34,
    },

    # Human feedback (from feedback.json, injected via --feedback flag)
    "Human_Feedback": "The MEASURE() section needs concrete syntax examples, not just a description",

    # Composite scores for GEPA's Pareto frontier
    "scores": {
        "pass_rate_with": 0.67,
        "pass_rate_without": 0.33,
        "effectiveness_delta": 0.34,
        "regression_rate": 0.1,
        "token_efficiency": 0.92,
        "structure": 1.0,
        "final": 0.52,
    },

    "token_counts": {"candidate_total": 1198, "original_total": 1234, "budget": 2000},

    # If MLflow assessments were injected:
    "real_world_assessments": [
        {"name": "ToolCallCorrectness", "value": "no", "rationale": "Agent used Bash instead of execute_sql"}
    ],
}
```

GEPA renders each top-level key as a markdown header. The **key names are the headers** — so `Failed_Assertions` becomes `## Failed_Assertions` followed by a bulleted list, and `Needs_Skill` becomes `## Needs_Skill`. This gives the reflection LM precise, actionable information instead of having to parse prose rationale.

---

## Multi-Pass Optimization

The runner (`runner.py`) wraps GEPA in a multi-pass loop (default: up to 5 passes):

```
Pass 1: seed = original SKILL.md
  └─► GEPA runs up to max_metric_calls iterations
  └─► Re-evaluate best candidate on ALL training tasks
  └─► If improvement > 0.0005: seed Pass 2 with best

Pass 2: seed = best from Pass 1
  └─► GEPA runs again, starting from the improved candidate
  └─► If improvement > 0.0005: seed Pass 3 with best

...continues until improvement ≤ 0.0005 or max_passes reached
```

Each pass creates a refinement chain — incremental improvements compound across passes. Early stopping prevents wasting compute when the skill has converged.

### Baseline scoring

Before optimization starts, the evaluator scores the original SKILL.md on all training tasks:

- **Per-task score** — composite score for each test case
- **Mean baseline score** — average across all tasks (e.g., `0.909`)
- **Diagnostic labels** — each task classified:
  - **OK** — skill helped (effectiveness delta > +0.05)
  - **NEEDS_SKILL** — WITH-skill assertions failing (skill isn't teaching enough)
  - **REGRESSION** — skill hurt the response (assertions that pass WITHOUT but fail WITH)

This baseline context is included in GEPA's background prompt so the reflection LM knows what's working and what needs improvement.

### What "improvement" means

```
improvement = optimized_score - original_score
```

Both scores come from the same evaluator, same semantic grader, same prompts, same cached WITHOUT-skill baselines. The only variable is the SKILL.md content. An improvement of +0.03 means the optimized skill produced measurably better assertion pass rates across test cases.

---

## Human Feedback Integration

Human feedback bridges the evaluation step (Step 1) to the optimization step (Step 2). As Anthropic's Evals guide emphasizes: "You won't know if your graders are working well unless you read the transcripts and grades from many trials... Reading transcripts is how you verify that your eval is measuring what actually matters." The HTML report from `evaluate.py` serves exactly this purpose — it presents the transcripts, grades, and evidence so a human can verify the evaluation before optimization begins.

### HTML report features

The HTML report (`html_report.py`) is a self-contained, zero-dependency HTML file inspired by Anthropic's [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) `generate_review.py` viewer:

- **Per-task cards** showing the prompt, WITH-skill response, and WITHOUT-skill response side by side
- **Assertion table** with pass/fail status, evidence quotes, and POSITIVE/REGRESSION/NEEDS_SKILL/NEUTRAL classification badges (color-coded: green, red, yellow, gray)
- **Aggregate score summary** showing pass_rate_with, effectiveness_delta, and other scoring components
- **Feedback controls per task** with a verdict dropdown (Good / Needs Work / Regression) and a freeform notes textarea
- **"Save Feedback" button** that exports all feedback as `feedback.json` via browser download (client-side only, no server needed)

The feedback export produces a JSON array matching the `feedback.py` simple format, ready to be passed directly to `optimize.py --feedback`.

After reviewing the report, a human exports a `feedback.json` file that is consumed by `optimize.py --feedback`.

### `feedback.json` format

Two formats are supported:

**Anthropic-style** (exported from the HTML viewer):
```json
{
  "reviews": [
    {"run_id": "task_001", "feedback": "Missing concrete MEASURE() syntax examples", "timestamp": "2026-03-29T..."}
  ]
}
```

**Simple format** (manual creation):
```json
[
  {
    "task_id": "task_001",
    "notes": "Missing concrete MEASURE() syntax examples",
    "verdict": "needs_work",
    "suggested_changes": "Add a complete SQL example showing MEASURE() with GROUP BY"
  }
]
```

### How feedback flows into GEPA

```
feedback.json
    │
    ▼
feedback.load_feedback()
    │
    ▼
feedback.feedback_to_gepa_background()
    │
    ├─► Formats as "## Human Review Feedback" section
    │   with improvement principles:
    │   • Generalize (don't add narrow fixes for specific test cases)
    │   • Stay lean (remove content that isn't helping)
    │   • Explain why (prefer reasoning over rigid rules)
    │   • Bundle patterns (address repeated issues once)
    │
    ├─► Separates regressions from needs-improvement items
    │
    └─► Injected into GEPA's background parameter
         alongside baseline scores + assessment summaries + focus areas
```

The feedback module (`feedback.py`) follows Anthropic's skill-creator principles: feedback should drive generalized improvements, not narrow test-case-specific patches. The improvement principles are included in the GEPA background so the reflection LM treats human feedback as directional guidance rather than literal instructions.

### Per-task feedback in `side_info`

When `assessment_by_task` mappings exist, individual task feedback appears in the `Human_Feedback` field of each task's `side_info`. This gives the reflection LM task-specific human context alongside the machine-generated assertion diagnostics.

---

## Adaptive Evaluation Criteria

The semantic grader can load domain-specific evaluation criteria during scoring. Evaluation criteria are packaged as SKILL.md files in `.test/eval-criteria/` — the same format used by agent skills. This implements the `Skill`/`SkillSet` data model from the [MLflow #21255 design spec](https://github.com/mlflow/mlflow/issues/21255#issuecomment-3997922398).

### Discovery and filtering

The framework scans `.test/eval-criteria/` for subdirectories containing a `SKILL.md` file. It parses each file's YAML frontmatter to check the `applies_to` metadata field and filters based on the skill's `tool_modules`:

- **`applies_to: [sql]`** — only included when the skill declares `tool_modules` containing `sql`
- **`applies_to: []`** (or omitted) — always included (general-purpose criteria, e.g., `general-quality`, `tool-selection`)

The discovered directory paths can be passed to evaluation criteria loaders when the native MLflow `skills=` parameter is available (PR #21725). Until then, criteria are injected as guidelines into the semantic grader.

### Forward compatibility

The framework detects whether the installed MLflow version supports `skills=` via signature inspection. When MLflow ships the native API from the #21255 spec, no code changes are needed — the parameter will be automatically passed through.

The SKILL.md files and `references/` directories remain unchanged — only the discovery mechanism lives in the evaluation criteria module instead of separate modules.

---

## MLflow Assessment Injection

The `--mlflow-assessments EXPERIMENT_ID` flag fetches real-world behavioral feedback from MLflow traces and injects it into GEPA's optimization context.

### How it works

1. **Fetch** (`assessment_fetcher.py`): Searches the MLflow experiment for traces with `ToolCallCorrectness` and `ToolCallEfficiency` assessments
2. **Summarize**: Computes pass/fail rates across all traces (e.g., "ToolCallCorrectness: 60% pass (3/5)")
3. **Match**: Maps assessments to training tasks by prompt similarity (using `difflib.SequenceMatcher` with threshold >= 0.6)
4. **Inject**: Matched assessments appear in `side_info` for each task, so GEPA's reflection LM can see real-world failures

### Data flow

```
MLflow Experiment (with assessed traces)
    │
    ▼
assessment_fetcher.fetch_assessments(experiment_id)
    │
    ├─► summarize_assessment_patterns() → background context for GEPA
    │
    └─► match_assessments_to_tasks() → per-task assessment injection
         │
         ▼
    SkillBenchEvaluator receives assessment_by_task
         │
         ▼
    side_info["real_world_assessments"] per task
         │
         ▼
    GEPA reflection LM reads failures → targeted mutations
```

This allows GEPA to learn from actual agent behavior — if the agent consistently picks the wrong tool or produces inefficient tool call patterns, those failures feed directly into the optimization loop.

---

## MLflow Tracing Integration

### Agent execution tracing

When running `evaluate.py` or `optimize.py --agent-model`, each agent execution produces an MLflow trace:

1. A **Stop hook** is attached to the Claude Agent SDK client
2. When the agent completes, the hook calls `mlflow.claude_code.tracing.process_transcript()` to convert the transcript into an MLflow trace
3. The trace is tagged with `skill_name`, `databricks.requested_model`, and `mlflow.source=skill-test-agent-eval`
4. The trace is returned to the `AgentEvaluator` for scoring with `ToolCallCorrectness` and `ToolCallEfficiency` judges

### Optimization run logging

Each optimization run is logged to MLflow:

```python
with mlflow.start_run(run_name=f"{skill_name}_optimize_{preset}"):
    mlflow.set_tags({
        "optimizer": "gepa",
        "skill_name": skill_name,
        "preset": preset,
        "evaluator_type": "skillbench",
    })
    mlflow.log_metrics({
        "original_score": 0.909,
        "optimized_score": 0.935,
        "improvement": 0.026,
        "original_tokens": 1234,
        "optimized_tokens": 1198,
        "token_reduction_pct": 2.9,
        "total_metric_calls": 30,
    })
```

The experiment name defaults to `/Shared/skill-tests` and is overridable with `--mlflow-experiment`.

---

## Component Scaling

When optimizing multiple components (e.g., SKILL.md + tool modules with `--include-tools`), metric calls scale:

- **Base formula**: `base_calls x num_components`
- **Per-preset caps**: quick -> 45, standard -> 150, thorough -> 300
- **Global cap**: 300 (applied for slower reflection models like Sonnet/Haiku)
- **Round-robin**: GEPA's component selector alternates which component to mutate each iteration

Example: `--include-tools --tool-modules sql serving` (3 components: `skill_md` + `tools_sql` + `tools_serving`), `quick` preset -> min(15 x 3, 45) = **45** metric calls per pass.

---

## Scoring Weights

### Proxy evaluator (SkillBench) and Agent evaluator

Both evaluators use the same scoring formula from `semantic_grader.compute_score()`:

| Weight | Dimension | What it measures |
|--------|-----------|-----------------|
| **40%** | Effectiveness Delta | `pass_rate_with - pass_rate_without` — the primary skill contribution signal |
| **30%** | Pass Rate With | Absolute assertion pass rate — how good the WITH-skill response is |
| **15%** | Token Efficiency | Token count vs original — smaller skills save context window |
| **5%** | Structure | Python/SQL syntax validity (deterministic, zero cost) |
| **-10%** | Regression Rate | Fraction of assertions that regressed (pass WITHOUT, fail WITH) |

### Score formula

```python
final = max(0.0, min(1.0,
    0.40 * effectiveness_delta
  + 0.30 * pass_rate_with
  + 0.15 * token_efficiency
  + 0.05 * structure
  - 0.10 * regression_rate
))
```

### Why these weights?

- **Effectiveness delta (40%)** is the largest weight because it directly measures the core question: does the skill help? A skill that improves assertion pass rates by 0.5 (from 0.3 to 0.8) contributes 0.20 to the score.
- **Pass rate with (30%)** ensures absolute quality matters, not just relative improvement. A skill that achieves 0.9 pass rate gets more credit than one at 0.5, even if both improved equally.
- **Token efficiency (15%)** incentivizes conciseness. Skills consume context window — every token saved is context available for the actual task. Candidates smaller than the original get a bonus (up to 1.15x), while oversized candidates are penalized.
- **Structure (5%)** is a low-weight sanity check. Syntax errors in code examples mean the skill is teaching broken patterns.
- **Regression rate (-10%)** is a penalty, not a bonus. Even small regressions are costly in practice — a skill that breaks one task while improving three is suspect.

---

## Dataset Splitting

Handled by `splitter.py`:

- **< 5 test cases**: All used as training, no validation set (single-task mode)
- **>= 5 test cases**: Stratified train/val split by `metadata.category` (80/20 default)
- **`--tools-only` mode**: Cross-skill dataset — auto-discovers all skills with `ground_truth.yaml`, samples up to 5 tasks per skill
- **No `ground_truth.yaml`**: `generate_bootstrap_tasks()` auto-generates tasks from SKILL.md headers and code blocks

---

## Model Fallback Chain

When a model is rate-limited (`REQUEST_LIMIT_EXCEEDED`), the framework automatically cycles through fallback models:

1. **Primary model**: 3 retries with exponential backoff (2^N seconds, max 30s)
2. **Fallback chain**: GPT-5-2 -> Gemini-3-1-Pro -> Claude Opus 4.5 -> GPT-5 -> Claude Sonnet 4.6 -> Claude Sonnet 4.5
3. Each fallback model gets 3 retries
4. If all exhausted: returns score 0.0 with rationale "All models rate limited"

This applies to both semantic grader calls and response generation via `completion_with_fallback()`.

---

## Skills vs Tools Optimization

Skills and tools operate at different layers:

| | Skills | Tools |
|---|--------|-------|
| **What** | Domain knowledge (API syntax, patterns, best practices) | Tool selection (what each MCP tool does, when to use it) |
| **Where** | `databricks-skills/<skill>/SKILL.md` | `databricks-mcp-server/tools/*.py` (`@mcp.tool` docstrings) |
| **Scope** | One skill = one domain | Shared across ALL skills |
| **Risk** | Isolated — only affects one domain | Global — changes affect every agent session |

### Why optimize separately

Optimizing both simultaneously creates a **confounding variable problem**:
- Did the score improve because the skill got better, or because the tool description changed?
- Will the tool description change break other skills?
- GEPA's reflection LM can't distinguish which component caused the improvement.

### Recommended workflow

1. **Tools first** (`--tools-only`): Optimize tool descriptions against a cross-skill dataset so they generalize
2. **Skills second** (default): Optimize each skill with stable tool descriptions as read-only context
3. **Co-optimize** (`--include-tools`): Only for fixing skill/tool interaction edge cases after separate optimization

### Optimization modes

| Mode | Flag | Components mutated | Dataset | Use case |
|------|------|--------------------|---------|----------|
| Skill only | *(default)* | `skill_md` | Single skill's `ground_truth.yaml` | Domain knowledge |
| Tools only | `--tools-only` | `tools_sql`, `tools_serving`, etc. | Cross-skill (all skills sampled) | Universal tool selection |
| Both | `--include-tools` | `skill_md` + tool modules | Single skill's `ground_truth.yaml` | Skill/tool interaction fixes |

---

## Architecture Diagram

```
    evaluate.py (Step 1)                    optimize.py (Step 2)
    ════════════════════                    ════════════════════
    Standalone CLI                          GEPA optimization CLI
    Always real agent                       Proxy or real agent
           │                                        │
           │                                        ▼
           │                                  runner.py
           │                             (multi-pass orchestrator)
           │                                  │         │
           │                        ┌─────────┘         └──────────┐
           │                        ▼                               ▼
           │               proxy evaluator                    agent_evaluator.py
           │               (litellm.completion +             (real Claude Code +
           │                semantic grader)                  semantic grader)
           │                        │                            │
           ▼                        ▼                            ▼
      executor.py ◄──────── shared module ──────────────► executor.py
      (ClaudeSDKClient,                                   (same module)
       event streaming,
       TraceMetrics)
           │
           ▼
      semantic_grader.py ◄───────── shared by all paths ─────────►
      (3-phase grading pipeline:
       Phase 1: deterministic — patterns (regex) + facts (substring)
       Phase 2: agent-based — Anthropic API + execution transcript
       Phase 3: semantic fallback — litellm batched LLM call
       → POSITIVE/REGRESSION/NEEDS_SKILL/NEUTRAL classification)
           │
           ├──────────────────────┐
           ▼                      ▼
      html_report.py         completion_with_fallback
      (Step 1 only:           (model fallback chain,
       HTML viewer +           rate limiting)
       feedback export)
           │
           ▼                                        ┌── assessment_fetcher.py
      feedback.json ──────────────────────────────► │   (real-world MLflow
                                                    │    assessments)
                                                    ▼
                                              GEPA optimize_anything
                                              (reflection → mutation
                                               → evaluation → Pareto)
                                                    │
                                                    ├── feedback.py
                                                    │   (human feedback →
                                                    │    GEPA background)
                                                    ▼
                                              splitter.py / config.py
                                              (train/val split, presets)
```
