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

# Set working directory. All subsequent paths are relative to this directory.
WORKDIR /home/appuser

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy the application source code from the local 'src' directory into a 'src'
# subdirectory in the container. This preserves the project's src layout.
COPY --chown=appuser:appgroup src/ ./src/

# Set the PYTHONPATH to the 'src' directory.
ENV PYTHONPATH=/home/appuser/src
ENV PORT=8006

# Switch to the non-root user for security
USER appuser

# Expose the application port used by Manager_Agent docker-compose.yml
EXPOSE 8006

# Add healthcheck for container monitoring
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8006/health || exit 1

# Define the command to run the application.
CMD ["/opt/venv/bin/gunicorn", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8006", "src.app.main:app"]
