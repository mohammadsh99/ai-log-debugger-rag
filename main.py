import json

import psycopg2
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="AI Log Debugger")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_CONFIG = {
    "dbname": "ai_debugger",
    "user": "postgres",
    "password": "1234",
    "host": "localhost",
}

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "llama3"


# ---------- schemas ----------

class SearchRequest(BaseModel):
    error: str


class MatchResult(BaseModel):
    error: str
    solution: str
    distance: float
    confidence: float


class SearchResponse(BaseModel):
    root_cause: str
    fix: str
    explanation: str
    matches: list[MatchResult]


class AnalyzeLogsRequest(BaseModel):
    logs: str


# ---------- helpers ----------

def generate_embedding(text: str) -> list[float]:
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Ollama embedding error: {e}")


def fetch_top_matches(embedding: list[float]) -> list[dict]:
    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT error_text, solution, embedding <=> %s::vector AS distance
                    FROM logs_knowledge
                    ORDER BY distance ASC
                    LIMIT 3
                    """,
                    (str(embedding),),
                )
                rows = cursor.fetchall()
        matches = [
            {
                "error": r[0],
                "solution": r[1],
                "distance": r[2],
                "confidence": round(1 - r[2], 2),
            }
            for r in rows
        ]

        filtered_matches = [m for m in matches if m["distance"] < 0.5]

        if filtered_matches:
            return filtered_matches

        # Fallback: if nothing passes threshold, return the best match.
        return matches[:1]
    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


def build_prompt(user_error: str, matches: list[dict]) -> str:
    similar = "\n".join(
        f"{idx}. Error: {match['error']}\n   Solution: {match['solution']}"
        for idx, match in enumerate(matches, start=1)
    )

    return f"""You are a senior backend engineer.

User error:
{user_error}

Similar past errors:
{similar}

Rules:
- Base your answer ONLY on the similar past errors listed above.
- Use the similar past errors as primary evidence. Do not invent unrelated causes.
- Prefer the closest match (lowest distance) when deciding the root cause.
- Avoid generic answers like "user error".
- Always provide meaningful, non-empty values for root_cause, fix, and explanation.
- Respond ONLY with valid JSON.
- Do not include text before or after JSON.

Example:
User error: database connection problem

Similar errors:
1. Error: DB connection timeout
    Solution: Increase connection pool size
2. Error: Connection refused
    Solution: Check if database server is running

Expected output:
{{
"root_cause": "Database connection issue (timeout or server not reachable)",
"fix": "Check if the database server is running and increase connection pool size",
"explanation": "This input closely matches previous connection timeout and connection refused errors."
}}

Now return JSON in exactly this format:
{{
"root_cause": "...",
"fix": "...",
"explanation": "..."
}}

Return ONLY valid JSON. Do not include explanations, markdown, or extra text."""


def call_llm(prompt: str) -> dict:
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": CHAT_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        raw = str(resp.json().get("response", "")).strip()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Ollama generate error: {e}")

    if not raw:
        return {"root_cause": "", "fix": "", "explanation": ""}

    return parse_llm_response(raw)


def parse_llm_response(raw: str) -> dict:
    cleaned = raw.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        payload = json.loads(cleaned)
        
        if isinstance(payload, str):
            payload = json.loads(payload)
        
        if not isinstance(payload, dict):
            raise ValueError("LLM output JSON is not an object")

        return {
            "root_cause": str(payload.get("root_cause", "")).strip(),
            "fix": str(payload.get("fix", "")).strip(),
            "explanation": str(payload.get("explanation", "")).strip(),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"root_cause": raw, "fix": "", "explanation": ""}


def ensure_explanation(llm_result: dict, matches: list[dict]) -> dict:
    explanation = str(llm_result.get("explanation", "")).strip()
    if explanation:
        return llm_result

    if matches:
        top_error = str(matches[0].get("error", "")).strip()
        top_solution = str(matches[0].get("solution", "")).strip()
        llm_result["explanation"] = (
            f"This issue is similar to: {top_error}. Recommended solution: {top_solution}."
        )
    else:
        llm_result["explanation"] = "No similar past errors were found to support a detailed explanation."

    return llm_result


def save_search_history(user_input: str, root_cause: str, fix: str, explanation: str) -> None:
    try:
        print(f"[history] Saving search history for input: {user_input}")
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO search_history (user_input, root_cause, fix, explanation)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (user_input, root_cause, fix, explanation),
                )
        print("[history] Search history saved successfully.")
    except psycopg2.Error as e:
        print(f"[history] Failed to save search history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def extract_errors(logs: str, max_errors: int = 5) -> list[str]:
    error_keywords = {"error", "exception", "failed", "timeout", "refused"}
    extracted: list[str] = []

    for line in logs.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(k in line.lower() for k in error_keywords):
            extracted.append(line)
            if len(extracted) >= max_errors:
                break

    return extracted


# ---------- endpoint ----------

@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    user_error = request.error.strip()
    if not user_error:
        raise HTTPException(status_code=400, detail="Error message must not be empty.")

    print(f"[search] Received: {user_error}")

    embedding = generate_embedding(user_error)
    print("[search] Embedding generated.")

    matches = fetch_top_matches(embedding)
    print(f"[search] Found {len(matches)} matches.")

    prompt = build_prompt(user_error, matches)
    llm_result = call_llm(prompt)
    llm_result = ensure_explanation(llm_result, matches)
    print("[search] LLM response received.")

    save_search_history(
        user_input=user_error,
        root_cause=llm_result["root_cause"],
        fix=llm_result["fix"],
        explanation=llm_result["explanation"],
    )

    return SearchResponse(
        root_cause=llm_result["root_cause"],
        fix=llm_result["fix"],
        explanation=llm_result["explanation"],
        matches=[MatchResult(**m) for m in matches],
    )


@app.post("/analyze-logs")
def analyze_logs(request: AnalyzeLogsRequest):
    logs = request.logs.strip()
    if not logs:
        return {"total_detected": 0, "results": []}

    errors = extract_errors(logs, max_errors=5)
    if not errors:
        return {"total_detected": 0, "results": []}

    results: list[dict] = []
    for error_text in errors:
        try:
            embedding = generate_embedding(error_text)
            matches = fetch_top_matches(embedding)
            prompt = build_prompt(error_text, matches)
            llm_result = call_llm(prompt)
            llm_result = ensure_explanation(llm_result, matches)

            results.append(
                {
                    "error": error_text,
                    "root_cause": llm_result.get("root_cause", ""),
                    "fix": llm_result.get("fix", ""),
                    "explanation": llm_result.get("explanation", ""),
                }
            )
        except HTTPException:
            continue

    if results:
        representative = results[0]
        save_search_history(
            user_input=logs,
            root_cause=representative.get("root_cause", ""),
            fix=representative.get("fix", ""),
            explanation=representative.get("explanation", ""),
        )

    return {"total_detected": len(errors), "results": results}


@app.get("/history")
def get_history():
    try:
        with psycopg2.connect(**DB_CONFIG) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_input, root_cause, fix, explanation, created_at
                    FROM search_history
                    ORDER BY created_at DESC
                    LIMIT 10
                    """
                )
                rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "user_input": row[1],
                "root_cause": row[2],
                "fix": row[3],
                "explanation": row[4],
                "created_at": str(row[5]),
            }
            for row in rows
        ]
    except psycopg2.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
