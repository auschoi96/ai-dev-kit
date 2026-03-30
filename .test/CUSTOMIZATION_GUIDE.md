# Customization Guide: Steering Skill Evaluation & Optimization

## 1. Overview

This guide covers how to customize the evaluation and optimization process:

- **Sections 1-5**: Using the `--focus` flag to steer optimization with natural language
- **Section 6**: Writing effective guidelines and assertions for the semantic grader
- **Section 7**: Understanding assertion classifications (POSITIVE/REGRESSION/NEEDS_SKILL/NEUTRAL)
- **Section 8**: The full feedback loop from HTML report to optimization

The `--focus` flag lets you steer skill optimization with natural language. It uses an LLM to make targeted adjustments to `manifest.yaml` and `ground_truth.yaml` before GEPA runs, so the optimizer prioritizes what matters to you.

### Quick Start

```bash
# Single focus area
uv run python .test/scripts/optimize.py databricks-bundles \
  --focus "prefix all catalogs with customer_ prefix"

# Multiple focus areas
uv run python .test/scripts/optimize.py databricks-bundles \
  --focus "prefix all catalogs with customer_ prefix" \
  --focus "always use serverless compute"

# Focus areas from a file
uv run python .test/scripts/optimize.py databricks-bundles \
  --focus-file my_focus_areas.txt

# Dry run to see what would change
uv run python .test/scripts/optimize.py databricks-bundles \
  --focus "prefix all catalogs with customer_ prefix" --dry-run

# Combined with presets
uv run python .test/scripts/optimize.py databricks-bundles \
  --focus "use DLT for all pipeline examples" --preset quick
```

### What Happens

1. The LLM reads SKILL.md, `manifest.yaml`, and `ground_truth.yaml`
2. It adds `[FOCUS]`-prefixed guidelines to the manifest
3. It adjusts relevant existing test cases (expectations, patterns, guidelines)
4. It generates 2-3 new test cases targeting the focus area
5. GEPA optimization then runs with these enhanced evaluation criteria

---

## 2. How Each `ground_truth.yaml` Field Impacts Optimization

### `outputs.response` - Reference Answer

**What it is:** The ideal response the judge compares agent output against.

**How it steers optimization:** The quality judge uses this as the gold standard. If the reference response includes specific patterns (e.g., parameterized catalogs), the optimizer learns to produce those patterns.

**Example focus prompt:** `"All examples should use variable substitution for catalog names"`

**Before:**
```yaml
outputs:
  response: |
    catalog: my_catalog
```

**After:**
```yaml
outputs:
  response: |
    catalog: ${var.catalog_prefix}_catalog
```

### `expectations.expected_facts` - Substring Assertions

**What it is:** Exact substrings that must appear in the response. Checked deterministically (case-insensitive).

**How it steers optimization:** Failed facts tell the optimizer exactly what content is missing. Adding facts about your focus area forces the optimizer to include that content.

**Example focus prompt:** `"Must explain the MEASURE() function wrapping"`

**Before:**
```yaml
expected_facts:
  - "Defines variables with default values"
```

**After:**
```yaml
expected_facts:
  - "Defines variables with default values"
  - "All catalog values use ${var.catalog_prefix} variable"
```

### `expectations.expected_patterns` - Regex Patterns

**What it is:** Regular expressions checked with `re.findall(pattern, response, re.IGNORECASE)`. Each has a `min_count` (minimum number of matches required) and a `description`.

**How it steers optimization:** Pattern failures are binary and precise. Adding patterns for your focus area creates hard requirements the optimizer must satisfy.

**Example focus prompt:** `"Prefix all catalogs with a configurable prefix variable"`

**Before:**
```yaml
expected_patterns:
  - pattern: 'catalog:'
    min_count: 2
    description: Defines catalog variable
```

**After:**
```yaml
expected_patterns:
  - pattern: 'catalog:'
    min_count: 2
    description: Defines catalog variable
  - pattern: '\$\{var\.catalog_prefix\}'
    min_count: 1
    description: Uses catalog prefix variable
```

### `expectations.assertions` - Freeform Semantic Assertions

**What it is:** Natural-language assertions evaluated by the semantic grader. Each assertion is a statement that the response should satisfy, checked semantically rather than by exact string match or regex.

**How it steers optimization:** Assertions are evaluated by the semantic grader and directly affect the pass_rate_with score (30% of total) and effectiveness_delta (40% of total). They are more flexible than `expected_facts` (which require exact substring matches) and more expressive than `expected_patterns` (which require regex syntax).

**When to use assertions vs expected_facts vs expected_patterns:**

| Field | Best for | Evaluation method |
|---|---|---|
| `expected_facts` | Exact substrings that must appear verbatim (e.g., a specific function name, keyword, or phrase) | Deterministic case-insensitive substring match |
| `expected_patterns` | Structural patterns that can be expressed as regex (e.g., variable substitution syntax, repeated code patterns) | Deterministic regex match with `min_count` |
| `assertions` | Semantic requirements that cannot be reduced to a substring or regex (e.g., "the response explains why X is preferred over Y", "error handling covers edge case Z") | Semantic grader (LLM-based) |

**Example focus prompt:** `"Must include complete SQL and handle missing tables"`

```yaml
expectations:
  assertions:
    - "Response includes a complete SQL CREATE VIEW statement"
    - "Error handling covers the case where the table doesn't exist"
```

**More examples:**
```yaml
expectations:
  assertions:
    - "The explanation distinguishes between batch and streaming modes"
    - "The code example is production-ready, not just a toy snippet"
    - "Security implications of the approach are mentioned"
```

### `expectations.guidelines` - Semantic Grader Assertions

**What it is:** Guidelines are converted to assertions and evaluated by the semantic grader. Each guideline becomes a checkable assertion.

**How it steers optimization:** Guidelines become assertions evaluated by the semantic grader. They affect the pass_rate_with score (30% of total) and effectiveness_delta (40% of total).

**Example focus prompt:** `"Must parameterize catalog names with a prefix variable"`

**Before:**
```yaml
guidelines:
  - "Must define variables at root level with defaults"
```

**After:**
```yaml
guidelines:
  - "Must define variables at root level with defaults"
  - "Must parameterize catalog names with a prefix variable"
```

### `metadata.tags` - Categorization

**What it is:** Tags for organizing and filtering test cases. No direct impact on optimization scoring.

**How it steers optimization:** Tags help identify which test cases were generated or adjusted by focus. Focus-generated test cases get tags matching the focus area.

---

## 3. How Each `manifest.yaml` Field Impacts Optimization

### `scorers.default_guidelines` - Global Guidelines

**What it is:** Guidelines applied to ALL test cases that don't define their own guidelines. These are converted to assertions and evaluated by the semantic grader.

**How it steers optimization:** Adding `[FOCUS]` guidelines here affects every evaluation, not just specific test cases. This is the broadest way to steer optimization. Each guideline becomes an assertion evaluated by the semantic grader, affecting the pass_rate_with score (30% of total) and effectiveness_delta (40% of total).

**What `--focus` does:** Prepends `[FOCUS]` to each new guideline and appends to the list. Duplicates are skipped.

**Before:**
```yaml
default_guidelines:
  - "Response must address the user's request completely"
  - "YAML examples must be valid and properly indented"
```

**After:**
```yaml
default_guidelines:
  - "Response must address the user's request completely"
  - "YAML examples must be valid and properly indented"
  - "[FOCUS] All catalog references must use a configurable prefix variable"
  - "[FOCUS] Variable substitution syntax ${var.prefix} must be demonstrated"
```

### `quality_gates` - Pass/Fail Thresholds

**What it is:** Minimum score thresholds for each scorer. If a score falls below the gate, the test case fails.

**How it steers optimization:** Higher thresholds make the optimizer work harder to satisfy that criterion. `--focus` can only make thresholds stricter (higher), never looser. Thresholds are evaluated against assertion pass rates (e.g., the fraction of assertions that pass for a given scorer).

**Before:**
```yaml
quality_gates:
  pattern_adherence: 0.9
  execution_success: 0.8
```

**After (if focus demands stricter pattern checking):**
```yaml
quality_gates:
  pattern_adherence: 0.95
  execution_success: 0.8
```

---

## 4. Prompting Examples

### Scenario: Customer wants all catalogs prefixed

```bash
--focus "When creating DABs, prefix all catalogs and schemas with a customer-specific prefix using variables"
```

**What changes:**
- **manifest.yaml**: Adds `[FOCUS] All catalog/schema references must use ${var.prefix}_catalog pattern`
- **ground_truth.yaml**: Existing multi-env test cases get new `expected_patterns` for `${var.prefix}` syntax; 2-3 new test cases about prefix configuration

### Scenario: Customer wants DLT examples in DABs

```bash
--focus "Include Delta Live Tables (DLT) pipeline examples in all DABs configurations"
```

**What changes:**
- **manifest.yaml**: Adds `[FOCUS] DABs examples should include DLT pipeline resource definitions`
- **ground_truth.yaml**: Existing pipeline test cases get DLT-specific patterns; new test cases cover DLT pipeline YAML configuration

### Scenario: Customer wants stricter SQL validation

```bash
--focus "All SQL examples must use parameterized queries, never string interpolation"
```

**What changes:**
- **manifest.yaml**: Adds `[FOCUS] SQL examples must use parameterized queries with bind variables`
- **quality_gates**: `pattern_adherence` may increase (e.g., 0.9 -> 0.95)
- **ground_truth.yaml**: SQL-related test cases get patterns checking for parameterized syntax

---

## 5. Reviewing and Rolling Back Changes

### Identifying Focus-Generated Content

- **Guidelines**: Look for the `[FOCUS]` prefix in `manifest.yaml` `default_guidelines`
- **Test cases**: Check `metadata.source: generated_from_focus` in `ground_truth.yaml`
- **Adjusted responses**: Check `metadata._focus_original_response` for the pre-focus original

### Rolling Back

**Remove focus guidelines from manifest:**
```bash
# Edit manifest.yaml, delete lines starting with "[FOCUS]"
grep -v "^\s*- \"\[FOCUS\]" .test/skills/<skill>/manifest.yaml > tmp && mv tmp .test/skills/<skill>/manifest.yaml
```

**Remove focus-generated test cases:**
```python
# In Python
import yaml
with open(".test/skills/<skill>/ground_truth.yaml") as f:
    data = yaml.safe_load(f)
data["test_cases"] = [
    tc for tc in data["test_cases"]
    if tc.get("metadata", {}).get("source") != "generated_from_focus"
]
with open(".test/skills/<skill>/ground_truth.yaml", "w") as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
```

**Restore original responses (for adjusted test cases):**
```python
for tc in data["test_cases"]:
    original = tc.get("metadata", {}).pop("_focus_original_response", None)
    if original:
        tc["outputs"]["response"] = original
```

**Or use git:**
```bash
git checkout -- .test/skills/<skill>/manifest.yaml .test/skills/<skill>/ground_truth.yaml
```

---

## 6. Writing Effective Guidelines and Assertions

The semantic grader evaluates assertions using a 3-phase pipeline. Understanding this pipeline helps you write assertions that are both cost-efficient and expressive.

### The 3-phase grading pipeline

| Phase | Method | Cost | Best for |
|-------|--------|------|----------|
| **1. Deterministic** | `expected_patterns` (regex), `expected_facts` (substring) | Zero LLM cost | Exact syntax, specific keywords, structural requirements |
| **2. Agent-based** | Freeform `assertions` and `guidelines`, evaluated via Anthropic API with execution transcript | 1 API call (batched) | Behavioral checks, tool usage validation, reasoning quality |
| **3. Semantic fallback** | Same items as Phase 2, evaluated via litellm if Phase 2 unavailable or fails | 1 LLM call (batched) | Automatic fallback — no action needed |

**Design your assertions to maximize Phase 1 coverage.** Every fact or pattern that can be checked deterministically saves an LLM call and produces faster, more reliable results. As Anthropic's [Demystifying Evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) notes, code-based graders are "Fast, Cheap, Objective, Reproducible, Easy to debug" — reserve freeform assertions for properties that genuinely require language understanding.

### Choosing the right assertion type

| If you need to verify... | Use | Example |
|---|---|---|
| A specific keyword or phrase appears | `expected_facts` | `"WITH METRICS LANGUAGE YAML"` |
| A structural pattern appears N times | `expected_patterns` | `pattern: "MEASURE\\("`, `min_count: 1` |
| A semantic property of the response | `assertions` | `"The response explains WHY metric views use YAML"` |
| A broad behavioral guideline | `guidelines` | `"Must use Unity Catalog three-level namespace"` |
| The agent used specific tools | `trace_expectations.required_tools` | `["mcp__databricks__execute_sql"]` |

### Writing good assertions

Following best practices from Anthropic's [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) and the [Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf):

1. **Make assertions unambiguous and objectively verifiable.** Anthropic recommends that two domain experts "should independently reach the same pass/fail verdict." Bad: `"Response is good quality"`. Good: `"Response includes a complete CREATE VIEW statement with valid YAML metric definitions"`.

2. **Grade what was produced, not the path taken.** Anthropic warns: "There is a common instinct to check that agents followed very specific steps like a sequence of tool calls in the right order. We've found this approach too rigid... agents regularly find valid approaches that eval designers didn't anticipate." Prefer outcome-based assertions (`"The Genie Space is created with correct tables"`) over step-sequence assertions (`"Agent called create_or_update_genie then get_genie"`).

3. **Test the skill's unique contribution, not baseline knowledge.** Assertions that the model already satisfies without the skill (classified as NEUTRAL) waste evaluation budget. Focus on content the skill uniquely teaches — these become POSITIVE classifications that drive the effectiveness_delta score.

4. **Start small, expand once core behavior is validated.** Anthropic's Skills Guide advises: "Iterate on a single task before expanding." Begin with 3-5 test cases covering the happy path. Add edge cases and error handling after the core assertions pass consistently.

5. **Prefer `expected_facts` for verifiable content.** If you can express an assertion as an exact substring, use `expected_facts` — it is deterministic, zero-cost, and produces no false positives. Use freeform `assertions` only when substring matching is too brittle.

6. **Use `guidelines` for cross-cutting concerns.** Guidelines from `manifest.yaml` `default_guidelines` apply to ALL test cases. Use them for requirements like "Must use Databricks-specific syntax" that should hold universally.

7. **Build in partial credit.** Anthropic recommends: "For tasks with multiple components, build in partial credit. A support agent that correctly identifies the problem... is meaningfully better than one that fails immediately." Write multiple specific assertions per test case rather than one catch-all assertion — this gives the optimizer granular signal about what's passing and what needs improvement.

---

## 7. Understanding Assertion Classifications

Every assertion is checked on both the WITH-skill and WITHOUT-skill responses, then classified into one of four categories. These classifications drive how GEPA optimizes the skill and map to Anthropic's distinction between capability evals and regression evals.

### Classification table

| Classification | WITH result | WITHOUT result | What it means | GEPA action |
|---|---|---|---|---|
| **POSITIVE** | Pass | Fail | Skill is helping — it taught the agent something new | Protect this content (don't remove it) |
| **REGRESSION** | Fail | Pass | Skill is hurting — it confused the agent | Remove or rewrite the confusing content |
| **NEEDS_SKILL** | Fail | Fail | Neither response covers this — skill must add it | Add new content teaching this concept |
| **NEUTRAL** | Pass | Pass | Agent already knows this without the skill | Candidate for removal (save tokens) |

### How classifications map to Anthropic's eval framework

Anthropic's [Demystifying Evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) identifies two complementary eval types:

- **Capability evals** ask "What can this agent do well?" and "should start at a low pass rate, targeting tasks the agent struggles with." → **POSITIVE** and **NEEDS_SKILL** assertions measure capability: what the skill enables that the baseline doesn't.
- **Regression evals** ask "Does the agent still handle all the tasks it used to?" and "should have a nearly 100% pass rate." → **REGRESSION** assertions detect backsliding: the skill breaking something the agent already handles.

Both are tracked simultaneously in every evaluation, giving GEPA both a hill to climb (capability) and a floor to protect (regression).

### How classifications affect scoring

| Scoring component | Weight | Driven by |
|---|---|---|
| Effectiveness delta | 40% | POSITIVE count — more skill-specific wins = higher delta |
| Pass rate WITH | 30% | POSITIVE + NEUTRAL count — absolute quality of WITH-skill response |
| Regression penalty | -10% | REGRESSION count — even one regression is costly |
| Token efficiency | 15% | NEUTRAL items suggest content to remove (save tokens) |

### Practical implications for test case design

- **Most assertions are NEUTRAL?** Your test cases are too easy — the model already knows this content. Write harder assertions that test the skill's unique domain knowledge.
- **Seeing REGRESSION on a specific assertion?** Check whether the skill is teaching something incorrect or confusing that overrides the model's correct baseline behavior.
- **NEEDS_SKILL is the most actionable classification** — it tells you exactly what content the skill is missing. These items appear prominently in GEPA's `side_info` as targets for content addition.
- **Balance capability and regression tests.** Anthropic advises: "Test both the cases where a behavior should occur and where it shouldn't. One-sided evals create one-sided optimization."

---

## 8. The Feedback Loop: HTML Report to Optimization

The evaluate → review → optimize cycle is the core improvement loop. Anthropic's Skills Guide states that "Skills are living documents. Plan to iterate based on" user feedback, and their Evals guide emphasizes: "You won't know if your graders are working well unless you read the transcripts and grades from many trials."

### Step-by-step

1. **Run evaluation:**
   ```bash
   uv run python .test/scripts/evaluate.py <skill-name>
   ```
   This runs the real Claude Code agent on all test cases WITH and WITHOUT the skill, grades assertions using the semantic grader, and produces:
   - `.test/skills/<skill-name>/evaluation.json` — machine-readable results
   - `.test/skills/<skill-name>/report.html` — interactive HTML report for human review

2. **Review the HTML report:**
   Open `report.html` in a browser. For each test case, review:
   - The WITH vs WITHOUT responses side by side
   - Which assertions passed/failed and their POSITIVE/REGRESSION/NEEDS_SKILL/NEUTRAL classifications
   - The evidence quotes explaining each judgment
   - The aggregate scores (pass rate, effectiveness delta)

3. **Provide feedback:**
   For each test case that needs improvement, use the verdict dropdown (Good / Needs Work / Regression) and type notes explaining what should change. Focus on **patterns**, not individual test cases — feedback like "Missing concrete syntax examples throughout" is more useful than "Test 3 is wrong."

4. **Export feedback:**
   Click "Save Feedback" to download `feedback.json`. Save it to `.test/skills/<skill-name>/feedback.json`.

5. **Run optimization with feedback:**
   ```bash
   uv run python .test/scripts/optimize.py <skill-name> \
       --feedback .test/skills/<skill-name>/feedback.json --preset quick
   ```
   GEPA reads the feedback and injects it into the reflection LM's context alongside machine diagnostics (failed assertions, regressions, classification labels).

6. **Review and apply:**
   ```bash
   diff databricks-skills/<skill-name>/SKILL.md \
        .test/skills/<skill-name>/optimized_SKILL.md
   uv run python .test/scripts/optimize.py <skill-name> --apply-last
   ```

7. **Repeat.** Run evaluation again to measure improvement, then optimize further if needed.

### Feedback principles

The feedback module (`feedback.py`) injects these improvement principles into GEPA's context, following Anthropic's [skill-creator](https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md) methodology:

- **Generalize**: If feedback says "missing X in test 3", the fix should teach X broadly, not add a special case for test 3. Anthropic's Evals guide reinforces this: "One-sided evals create one-sided optimization."
- **Stay lean**: Remove content that is not helping. If the skill causes confusion (REGRESSION assertions), cut the confusing parts. Token efficiency is 15% of the score.
- **Explain why**: Prefer reasoning over rigid rules. "Use X because Y" is better than "ALWAYS use X" — reasoning generalizes to novel situations.
- **Bundle patterns**: If multiple test cases hit the same issue, address it once clearly. The Skills Guide advises: "Iterate on a single task before expanding" — fix the root cause, not individual symptoms.
