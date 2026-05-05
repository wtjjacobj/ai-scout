FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install the package + scikit-learn for semantic search
RUN pip install --no-cache-dir .

# Copy pre-built database and index
# These are built locally and included via .dockerignore exception
COPY data/ai_scout.db /app/data/ai_scout.db
COPY data/tfidf_index.pkl /app/data/tfidf_index.pkl

# Environment variables
ENV AI_SCOUT_DB=/app/data/ai_scout.db
ENV AI_SCOUT_INDEX=/app/data/tfidf_index.pkl

# Expose MCP server via HTTP
EXPOSE 8900

CMD ["ai-scout", "--streamable-http", "--host", "0.0.0.0", "--port", "8900"]
