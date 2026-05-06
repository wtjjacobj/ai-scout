FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source code
COPY src/ src/

# Copy data (read-only DB + TF-IDF index)
COPY data/ data/

# Environment
ENV AI_SCOUT_DB=/app/data/ai_scout.db
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8080

# Run as streamable-http server
CMD ["python", "-m", "ai_scout.server", "--streamable-http", "--host", "0.0.0.0", "--port", "8080"]
