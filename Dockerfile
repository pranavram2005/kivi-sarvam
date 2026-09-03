# Kivi Semantic Memory — one container, one port.
#
# The frontend is built in a throwaway stage and copied in as static files, so
# the runtime image has no Node in it and the API serves the UI from its own
# origin. That means one process to keep alive and no CORS to configure.

# --- stage 1: build the interface ------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /app/frontend
# Copy manifests first so a dependency install is cached across code changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# --- stage 2: the application ----------------------------------------------
FROM python:3.13-slim

# Unbuffered so container logs appear as they happen rather than in bursts,
# and no .pyc files in a layer that is rebuilt every deploy.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
# The optional providers are commented out of requirements.txt because the
# offline engine needs none of them. They are installed here so every provider
# RUN.md documents actually works in the container with nothing but an
# environment variable set. An uninstalled SDK does not raise: build_provider
# catches the ImportError and falls back to the offline engine, which looks
# like a working deployment quietly giving lower-quality answers.
# sentence-transformers is excluded on purpose - it pulls in torch, and the
# hashing embedder is the documented default.
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir "groq>=0.11.0" "google-genai>=0.3.0" "openai>=1.59.0"

COPY backend/ ./backend/
COPY evaluation/ ./evaluation/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY data/development_corpus.jsonl ./data/development_corpus.jsonl
COPY --from=frontend /app/frontend/dist ./frontend/dist

# The database lives on a mounted volume, not in the image. Without this a
# container restart would silently discard everything a reviewer imported.
ENV KIVI_DATABASE_URL=sqlite:////data/kivi.db

# The offline engine is the default on purpose: no key required, and the
# evaluation finishes in seconds rather than minutes of rate-limited waiting.
ENV KIVI_LLM_PROVIDER=heuristic \
    KIVI_EMBEDDING_PROVIDER=hashing

EXPOSE 8000

# Seed on first boot only. If the volume already holds a database — because a
# reviewer imported their own corpus — leave it exactly as it is.
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh
ENTRYPOINT ["./docker-entrypoint.sh"]
