# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a FastAPI-based Q&A server for water facility management (배수지/가압장/감압시설). It converts natural language questions (Korean) into SQL queries using pattern matching against predefined examples, then generates templated natural language responses.

## Running the Server

```bash
# Install dependencies
pip install fastapi uvicorn psycopg2-binary pydantic

# Run the server
uvicorn ai_server:app --reload
```

The server exposes a single POST endpoint at `/ask` that accepts `{ "user_question": "..." }`.

## Architecture

**Question Processing Pipeline:**
1. `normalize_question()` - Strips whitespace, Korean particles (조사), and punctuation for fuzzy matching
2. `match_example()` - Matches normalized input against `questions` arrays in `example3.json`
3. Entity extraction functions populate SQL template placeholders:
   - `extract_site_name()` - Site names (신평, 송악1, 송악2, 행정, etc.)
   - `extract_block_level()` - Block levels (소블록, 중블록, 대블록)
   - `extract_facilitytype()` - Facility types (배수지, 가압장, 감압시설)
   - `extract_datainfo()` - Data types (압력, 유량, 수위)
4. `execute_query()` - Runs generated SQL against PostgreSQL
5. `agent_2_text_generation()` - Renders answer templates with query results

**example3.json Structure:**
Each entry contains:
- `intent` - Intent identifier
- `questions` - Array of example questions for matching
- `sql` - SQL template with `{sitename}`, `{facilitytype}`, `{block_level}`, `{datainfo}` placeholders
- `answer_template` - Response template with column name placeholders
- `graph_type` - Visualization type (currently "none")

## Key Patterns

- Korean particle removal in `normalize_question()` enables flexible question matching
- SQL placeholders are simple string replacements, not parameterized queries
- Empty/None values are filtered from final answers in `clean_answer()`
