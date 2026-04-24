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
    print("Generating embedding for input...")
    response = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": text},
        timeout=60,
    )
    response.raise_for_status()
    print("Embedding generated successfully.")
    return response.json()["embedding"]


def search_similar(cursor, embedding: list[float]):
    print("Running vector similarity search...")
    cursor.execute(
        """
        SELECT error_text, solution, embedding <=> %s::vector AS distance
        FROM logs_knowledge
        ORDER BY distance ASC
        LIMIT 3
        """,
        (str(embedding),),
    )
    return cursor.fetchall()


def main():
    user_input = input("Enter error message to search: ").strip()
    if not user_input:
        print("No input provided. Exiting.")
        sys.exit(1)

    try:
        embedding = get_embedding(user_input)
    except requests.RequestException as e:
        print(f"Ollama API error: {e}")
        sys.exit(1)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("Connected to database.")
    except psycopg2.Error as e:
        print(f"Failed to connect to database: {e}")
        sys.exit(1)

    try:
        results = search_similar(cursor, embedding)
        if not results:
            print("No matching results found.")
        else:
            print("(Lower distance = better match)\n")
            for i, (matched_error, solution, distance) in enumerate(results, start=1):
                print(f"Result {i}:")
                print(f"  Error:              {matched_error}")
                print(f"  Solution:           {solution}")
                print(f"  Distance (cosine):  {round(distance, 3)}")
                print()
    except psycopg2.Error as e:
        print(f"Database query error: {e}")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
