# ---- Builder Stage ----
# This stage installs all dependencies into a virtual environment.
FROM python:3.12-slim AS builder

WORKDIR /opt/venv
RUN python -m venv .

COPY requirements.prod.txt .
RUN . /opt/venv/bin/activate && pip install --no-cache-dir -r requirements.prod.txt


# ---- Final Stage ----
# This stage builds the final, lean image for production.
FROM python:3.12-slim

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root user and group for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Set working directory for the application
WORKDIR /home/appuser/app

# Copy virtual environment and application code from builder stage
COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appgroup app/ ./

# Switch to the non-root user
USER appuser

# Expose the application port
EXPOSE 8004

# Add healthcheck for container monitoring
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8004/health || exit 1

# Define the command to run the application
CMD ["/opt/venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"]
