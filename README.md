# AI Log Debugger

## Demo

![UI](./screenshots/ui.png)
![Result](./screenshots/result.png)

## Architecture

User -> Frontend -> FastAPI -> Embedding -> PostgreSQL (pgvector)
↓
Ollama (LLM)

## 1. Project Overview

AI Log Debugger is a full-stack application that analyzes runtime error messages and log lines, retrieves similar historical incidents from a vector-enabled PostgreSQL knowledge base, and generates structured troubleshooting output.

Business goal:

- Reduce time-to-diagnosis for operational and application errors.
- Reuse known fixes from prior incidents instead of starting analysis from scratch.

Real-world use case:

- An engineer pastes an error message or uploads a `.log` file.
- The system retrieves semantically similar historical errors and returns:
- Root cause
- Suggested fix
- Explanation grounded in historical matches

---

## 2. System Architecture

High-level architecture:

- Single-page frontend (static HTML/CSS/JS) calls FastAPI backend over HTTP.
- FastAPI orchestrates embedding generation, vector retrieval, LLM generation, and history persistence.
- PostgreSQL stores both vectorized troubleshooting knowledge and user search history.
- Ollama provides local models for embeddings and response generation.

Components:

- UI Layer:
- Upload/paste input
- Result rendering
- History rendering
- API Layer (FastAPI):
- `/search` for single-error analysis
- `/analyze-logs` for multi-error analysis
- `/history` for recent searches
- Retrieval Layer:
- pgvector cosine-distance query on `logs_knowledge`
- Generation Layer:
- Prompt construction + LLM response parsing
- Persistence Layer:
- `search_history` insert and retrieval

Module interaction:

1. User sends input (`error` or `logs`).
2. Backend creates embedding (Ollama embeddings API).
3. Backend retrieves top similar knowledge records from PostgreSQL.
4. Backend builds constrained prompt with retrieved context.
5. Backend calls Ollama generation API.
6. Backend parses structured JSON output.
7. Backend stores representative result in `search_history`.
8. Frontend renders results and refreshes history list.

---

## 3. Tech Stack

Backend:

- Python
- FastAPI
- Pydantic
- psycopg2
- requests

Frontend:

- HTML
- CSS
- Vanilla JavaScript (no framework)

AI/ML:

- Ollama (local)
- Embedding model: `nomic-embed-text`
- Generation model: `llama3`

Infrastructure/Data:

- PostgreSQL
- PostgreSQL with pgvector extension and `<=>` operator

Not observed in code:

- Docker
- Kafka
- Message queues
- External auth providers

---

## 4. Project Structure

Top-level files and responsibilities:

- [main.py](main.py): Main FastAPI application, request/response schemas, embedding + retrieval + LLM pipeline, log analysis endpoint, history persistence and retrieval.
- [index.html](index.html): Complete frontend UI, API calls, log file upload, rendering of analysis and history.
- [generate_embeddings.py](generate_embeddings.py): Batch utility to populate missing embeddings in `logs_knowledge`.
- [search.py](search.py): CLI utility for interactive similarity search against `logs_knowledge`.
- [**pycache**/](__pycache__/): Python bytecode cache directory.

---

## 5. Core Features

- Single error analysis (`POST /search`).
- Multi-error log analysis (`POST /analyze-logs`) with line extraction heuristics.
- Vector similarity retrieval using pgvector cosine distance.
- LLM-based structured diagnosis (`root_cause`, `fix`, `explanation`).
- Fallback explanation generation when LLM explanation is empty.
- Search history persistence and retrieval (`GET /history`).
- Frontend file upload (`.log`, `.txt`) and drag-drop support.
- Frontend confidence badge rendering from returned similarity metrics.

---

## 6. Data Flow (Very Important)

### A. Single Error Flow (`/search`)

1. Frontend sends `{ "error": "..." }`.
2. Backend validates non-empty input.
3. Backend calls Ollama embeddings API to generate vector.
4. Backend queries `logs_knowledge`:

- `embedding <=> input_vector` (cosine distance)
- top 3 nearest rows

5. Backend applies distance filter (`distance < 0.5`) with fallback to best match.
6. Backend builds prompt containing:

- User error
- Similar past errors + solutions
- JSON-only output constraints

7. Backend calls Ollama generation API.
8. Backend parses model response to extract:

- `root_cause`
- `fix`
- `explanation`

9. Backend ensures explanation exists (fallback from top retrieved match).
10. Backend saves analysis in `search_history`.
11. Backend returns structured response with matches.
12. Frontend renders result and refreshes previous searches.

### B. Log File Flow (`/analyze-logs`)

1. Frontend reads selected file text via `FileReader`.
2. Frontend sends `{ "logs": "multi-line text" }`.
3. Backend extracts up to 5 error-like lines (keyword heuristic).
4. For each extracted line:

- embedding generation
- vector retrieval
- prompt generation
- LLM call
- parse + explanation fallback

5. Backend builds per-line result objects:

- `error`, `root_cause`, `fix`, `explanation`

6. Backend stores one representative record (first result) in `search_history` with `user_input=full logs`.
7. Backend returns:

- `total_detected`
- `results` array

8. Frontend renders log analysis cards and refreshes previous searches.

### C. History Flow (`/history`)

1. Frontend calls backend history endpoint.
2. Backend returns last 10 rows ordered by newest.
3. Frontend renders clickable history cards.
4. Clicking history re-renders stored result in UI.

---

## 7. Database Design (Critical)

Database in use:

- PostgreSQL (with pgvector semantics used in SQL)

### Table: `logs_knowledge`

Purpose:

- Knowledge base of historical errors and resolutions used for retrieval.

Observed fields from code:

- `id` (type not explicitly declared in code; used as row identifier)
- `error_text` (text-like)
- `solution` (text-like)
- `embedding` (vector; cast via `%s::vector` and compared using `<=>`)

Observed operations:

- Select rows with `embedding IS NULL` (batch embedding script).
- Update `embedding` by `id`.
- Similarity search ordering by cosine distance.

### Table: `search_history`

Purpose:

- Persist user analyses for “Previous Searches” display.

Observed fields from code:

- `id`
- `user_input`
- `root_cause`
- `fix`
- `explanation`
- `created_at`

Observed operations:

- Insert one row per `/search`.
- Insert one representative row per `/analyze-logs`.
- Read latest 10 rows ordered by `created_at DESC`.

Relationships:

- No explicit foreign keys or joins are defined in code.

Indexes:

- No index definitions are present in code.
- Retrieval behavior implies typical usefulness of timestamp ordering index and vector index, but index DDL is not present.

Example data shape:

```json
{
  "user_input": "ERROR: connect ECONNREFUSED 10.0.0.5:5432",
  "root_cause": "Database connection issue",
  "fix": "Verify DB host/port and service availability",
  "explanation": "Similar to previous connection refused incidents."
}
```

---

## 8. API Layer

### `POST /search`

Purpose:

- Analyze one error message.

Request:

```json
{
  "error": "string"
}
```

Response:

```json
{
  "root_cause": "string",
  "fix": "string",
  "explanation": "string",
  "matches": [
    {
      "error": "string",
      "solution": "string",
      "distance": 0.123,
      "confidence": 0.88
    }
  ]
}
```

Errors:

- `400` when input is empty.
- `500` for DB errors.
- `502` for Ollama request failures.

### `POST /analyze-logs`

Purpose:

- Analyze multiple error lines extracted from a log text payload.

Request:

```json
{
  "logs": "multi-line string"
}
```

Response:

```json
{
  "total_detected": 3,
  "results": [
    {
      "error": "string",
      "root_cause": "string",
      "fix": "string",
      "explanation": "string"
    }
  ]
}
```

### `GET /history`

Purpose:

- Return latest saved analyses for UI history display.

Response:

```json
[
  {
    "id": 1,
    "user_input": "string",
    "root_cause": "string",
    "fix": "string",
    "explanation": "string",
    "created_at": "timestamp-string"
  }
]
```

---

## 9. Internal Logic Explanation

The named modules “Orchestrator”, “Lifecycle Manager”, “Dependency Resolver”, and “Plan Converter” do not exist as separate modules/classes in this codebase.

Implemented orchestration behavior in [main.py](main.py):

- Orchestration is function-driven inside endpoint handlers.
- Pipeline sequence:
- `generate_embedding` → `fetch_top_matches` → `build_prompt` → `call_llm` → `parse_llm_response` → `ensure_explanation` → optional `save_search_history`
- Error handling:
- `HTTPException` wrapping for external API and DB failures.
- Parsing control:
- `parse_llm_response` handles fenced JSON and string-wrapped JSON payloads.
- Explanation fallback:
- `ensure_explanation` derives explanation from top retrieval if LLM omits it.
- Multi-log extraction:
- `extract_errors` applies keyword-based filtering and max-count capping.

---

## 10. Integrations

External services observed:

- Ollama HTTP APIs:
- `POST /api/embeddings` for vector generation.
- `POST /api/generate` for text generation.
- PostgreSQL via psycopg2.

Webhooks:

- None observed.

Authentication:

- None observed for API endpoints.

---

## 11. RAG / AI System

RAG presence:

- Yes, retrieval-augmented generation is implemented.

Where integrated:

- In `/search` and per extracted line in `/analyze-logs`.

How retrieval works:

1. Convert input text to embedding (`nomic-embed-text`).
2. Query `logs_knowledge` by vector similarity (`<=>` distance).
3. Retrieve top examples (`error_text` + `solution`).
4. Inject examples into LLM prompt as grounding context.

Data used for retrieval:

- Historical records in `logs_knowledge` table:
- `error_text`
- `solution`
- `embedding`

---

## 12. Setup & Run Instructions

### Prerequisites

- Python 3.10+ (inferred from typing style)
- PostgreSQL database with required tables
- Ollama running locally at `http://localhost:11434`
- Models available in Ollama:
- `nomic-embed-text`
- `llama3`

### Install dependencies

```bash
pip install fastapi uvicorn psycopg2 requests pydantic
```

### Populate missing embeddings

```bash
python generate_embeddings.py
```

### Run API server

```bash
uvicorn main:app --reload
```

### Open frontend

- Open [index.html](index.html) in a browser.
- Frontend calls backend at:
- `http://127.0.0.1:8000/search`
- `http://127.0.0.1:8000/analyze-logs`
- `http://127.0.0.1:8000/history`

### Environment variables

- No environment variable usage is implemented.
- DB credentials and model names are hardcoded in [main.py](main.py), [search.py](search.py), and [generate_embeddings.py](generate_embeddings.py).

---

## 13. Known Limitations

Observed from code behavior:

- Configuration (DB credentials, model names, host URLs) is hardcoded.
- `/analyze-logs` uses keyword extraction and analyzes at most 5 lines.
- Multi-log endpoint stores only one representative history record (first analyzed result), not all per-line results.
- `fetch_top_matches` currently does not accept category/tags context in active code path.
- No authentication/authorization on API endpoints.
- No schema migration files or SQL DDL are present in repository.
- No automated test suite is present in repository.
- LLM output quality depends on local model behavior and JSON compliance.
