FROM python:3.13-slim

WORKDIR /app

# Install uv via pip
RUN pip install --no-cache-dir uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies into the project venv (system python, no extra venv)
RUN uv sync --frozen --no-dev

# Copy source
COPY agent/ ./agent/
COPY skills/ ./skills/
COPY server.py ./

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
