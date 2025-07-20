FROM ghcr.io/astral-sh/uv:debian

# Fix for OpenSSL issue with digital envelope routines
ENV PYTHONWARNINGS=ignore
ENV OPENSSL_LEGACY_PROVIDER=1
ENV OPENSSL_CONF=/etc/ssl/openssl-legacy.cnf
ENV OPENSSL_ENABLE_MD5_VERIFY=1
ENV NODE_OPTIONS=--openssl-legacy-provider

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libssl-dev \
    postgresql-client \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create OpenSSL legacy config to fix the digital envelope issue
COPY ./openssl/openssl-legacy.cnf /etc/ssl/openssl-legacy.cnf

# Set working directory
WORKDIR /app

COPY pyproject.toml .
RUN uv sync

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 
ENV PYTHONUNBUFFERED=1 
ENV FLASK_APP=app.py

# Expose the port
EXPOSE 5001

# Use multi-stage build support
ARG TARGETPLATFORM

# Use the absolute path to gunicorn from the virtual environment
CMD ["/app/.venv/bin/gunicorn", "--bind", "0.0.0.0:5001", "--workers=3", "--timeout=120", "app:app"]