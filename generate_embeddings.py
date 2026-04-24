import psycopg2
import requests
import sys

DB_CONFIG = {
    "dbname": "ai_debugger",
    "user": "postgres",
    "password": "1234",
    "host": "localhost",
}

OLLAMA_URL = "http://localhost:11434/api/embeddings"
OLLAMA_MODEL = "nomic-embed-text"


def get_embedding(text: str) -> list[float]:
    response = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": text},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["embedding"]


def main():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("Connected to database.")
    except psycopg2.Error as e:
        print(f"Failed to connect to database: {e}")
        sys.exit(1)

    try:
        cursor.execute("SELECT id, error_text FROM logs_knowledge WHERE embedding IS NULL")
        rows = cursor.fetchall()
        print(f"Found {len(rows)} rows with NULL embeddings.")

        if not rows:
            print("Nothing to process.")
            return

        for i, (row_id, error_text) in enumerate(rows, start=1):
            print(f"[{i}/{len(rows)}] Generating embedding for row id={row_id}...")
            try:
                embedding = get_embedding(error_text)
                cursor.execute(
                    "UPDATE logs_knowledge SET embedding = %s::vector WHERE id = %s",
                    (str(embedding), row_id),
                )
                conn.commit()
                print(f"  Updated row id={row_id}.")
            except requests.RequestException as e:
                print(f"  Ollama API error for row id={row_id}: {e}")
                conn.rollback()
            except psycopg2.Error as e:
                print(f"  Database error for row id={row_id}: {e}")
                conn.rollback()

        print("Done.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
