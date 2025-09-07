import os

# Base directory of the application
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    """Base configuration settings."""
    # Secret key for session management and other security purposes
    SECRET_KEY = os.environ.get('SECRET_KEY', 'a-very-secret-key')

    # --- Database Configuration ---
    # Use SQLite for simple, local, file-based storage
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Project Data Storage ---
    # Directory to store uploaded documents, FAISS indexes, etc.
    PROJECTS_DATA_DIR = os.path.join(BASE_DIR, 'instance', 'projects_data')

    # --- Ollama & LLM Configuration ---
    # Model for generating text, summaries, and BibTeX
    OLLAMA_CHAT_MODEL = "gpt-oss:20b"
    # Model specifically for creating text embeddings
    OLLAMA_EMBEDDING_MODEL = "mxbai-embed-large"

    # --- RAG Configuration ---
    # Number of relevant document chunks to retrieve for a query
    RAG_TOP_K = 5
