"""Explicitly initialize the dedicated Neon database."""
from main import initialize_schema


if __name__ == "__main__":
    initialize_schema()
    print("Neon database schema initialized.")
