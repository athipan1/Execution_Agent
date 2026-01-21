# ---- Builder Stage ----
# This stage installs all dependencies into a virtual environment.
FROM python:3.12-slim AS builder

WORKDIR /opt/venv
RUN python -m venv .

COPY requirements.txt .
RUN . /opt/venv/bin/activate && pip install --no-cache-dir -r requirements.txt


# ---- Final Stage ----
# This stage builds the final, lean image for production.
FROM python:3.12-slim

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root user and group for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Set working directory for the application
WORKDIR /home/appuser

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source code from the local 'src' directory into the image
COPY --chown=appuser:appgroup src/ .

# Set PYTHONPATH to include the application root
ENV PYTHONPATH=/home/appuser

# Switch to the non-root user
USER appuser

# Expose the application port
EXPOSE 8005

# Add healthcheck for container monitoring
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8005/health || exit 1

# Define the command to run the application using Gunicorn for production
CMD ["/opt/venv/bin/gunicorn", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8005", "app.main:app"]
