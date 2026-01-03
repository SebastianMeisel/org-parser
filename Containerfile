# Containerfile (works with podman build and docker build)
FROM python:3.12-slim-bookworm

# System deps:
# - latex + dvisvgm are required for math rendering (\( ... \) and $...$)
# - ghostscript helps dvisvgm in some setups
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        dvisvgm \
        ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY . /app

# Runtime dirs that must be writable (math cache)
RUN mkdir -p /app/.math-cache \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# Use tini for proper signal handling (important with gunicorn)
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default command: run gunicorn via entrypoint script (auto TLS if certs are mounted)
CMD ["./entrypoint.sh"]
