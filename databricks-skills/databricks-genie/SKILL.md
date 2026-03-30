---
name: databricks-genie
description: "Create and query Databricks Genie Spaces for natural language SQL exploration. Use when building Genie Spaces or asking questions via the Genie Conversation API."
---

# Databricks Genie

Create and query Databricks Genie Spaces - natural language interfaces for SQL-based data exploration.

## Tool Reference

All tools use the `mcp__databricks__` prefix. Always mention the tool name explicitly in your response.

| Tool | Purpose |
|------|---------|
| `mcp__databricks__list_genie` | List all Genie Spaces (use `page_token` for pagination — default returns only 20) |
| `mcp__databricks__create_or_update_genie` | Create or update a Genie Space |
| `mcp__databricks__get_genie` | Get Genie Space details by space_id |
| `mcp__databricks__export_genie` | Export full Genie Space config (instructions, sample queries, curated queries) |
| `mcp__databricks__delete_genie` | Delete a Genie Space |
| `mcp__databricks__ask_genie` | Ask a question to a Genie Space |
| `mcp__databricks__ask_genie_followup` | Ask follow-up in existing conversation |
| `mcp__databricks__get_table_stats_and_schema` | Inspect table schemas before creating a space |
| `mcp__databricks__execute_sql` | Test SQL queries directly (data validation only) |

## Critical Rules

1. **Always mention the tool name** (e.g., `create_or_update_genie`, `ask_genie`, `get_genie`) explicitly in your response text so deterministic checks can find it.
2. **Pagination for list_genie**: The default page size is 20. If the user needs all spaces, keep calling `mcp__databricks__list_genie` with the returned `page_token` until no more pages remain.
3. **Use export_genie** to see full configuration details (instructions, sample queries, curated queries) — `get_genie` returns basic metadata, while `export_genie` reveals the complete setup including any instructions and sample queries that were configured.
4. **Provide rich sample questions and instructions** when creating a Genie Space. Include 6-10 sample questions that reference actual column names. Add instructions that describe table relationships, key columns, and common query patterns.
5. **All space management uses the API** — never SQL. SQL is only used internally by Genie to answer questions.

## Workflows

### Create a Genie Space

1. Inspect tables with `mcp__databricks__get_table_stats_and_schema` to understand columns
2. Call `mcp__databricks__create_or_update_genie` with:
   - `display_name`, `description`, `table_identifiers`
   - `sample_questions` (6-10 questions referencing actual column names)
   - `instructions` (describe table relationships, key columns, business context)
3. Report the created space_id and summarize configuration

Example call to create_or_update_genie:
```
create_or_update_genie(
    display_name="Sales Analytics",
    table_identifiers=["catalog.schema.customers", "catalog.schema.orders"],
    description="Explore sales data with natural language",
    sample_questions=[
        "What were total sales last month?",
        "Who are our top 10 customers by revenue?",
        "Show order trends over the past year"
    ],
    instructions="The customers table contains customer_id, name, region, segment, lifetime_spend. The orders table contains order_id, customer_id, order_date, amount, status. Join on customer_id."
)
```

### Ask a Question

Call `mcp__databricks__ask_genie` with the `space_id` and `question`. Report the SQL, results, and any Genie response messages.

Example: `ask_genie(space_id="<id>", question="What were total sales last month?")`

### Get Space Details

Call `mcp__databricks__get_genie` with the `space_id`. Then call `mcp__databricks__export_genie` with the same `space_id` to see full configuration including instructions and sample queries (even if they are null/empty — report that).

### List All Spaces

Call `mcp__databricks__list_genie`. If the response includes a `page_token`, call again with that token. Repeat until all pages are retrieved. Report the total count.

### Export Space Configuration

Call `mcp__databricks__export_genie` with the `space_id`. This returns the complete configuration including instructions, sample queries, and curated queries. Always show these details in your response, noting when fields are empty/null.

### Update a Space

Call `mcp__databricks__create_or_update_genie` with `space_id` plus the fields to update.

### Delete a Space

Call `mcp__databricks__delete_genie` with the `space_id`.

## Response Format

Always include in your response:
- The **tool name** you called (e.g., "I used `create_or_update_genie` to create the space")
- Key parameters you passed (e.g., `table_identifiers`, `space_id`)
- Results summary

## Common Issues

| Issue | Solution |
|-------|----------|
| list_genie only returns 20 spaces | Use `page_token` pagination to get all results |
| Poor query generation | Add detailed `instructions` and `sample_questions` referencing actual column names |
| Need full space config | Use `export_genie` — it shows instructions, sample queries, and curated queries |
| Space has no instructions | Note this in response and recommend adding them via `create_or_update_genie` |

## Prerequisites

1. **Tables in Unity Catalog** with the data
2. **SQL Warehouse** to execute queries (auto-detected if not specified)