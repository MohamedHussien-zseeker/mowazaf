FROM python:3.11-slim

WORKDIR /app

# Install uv (lightweight package manager)
RUN pip install --no-cache-dir uv

# Copy lock / requirements
COPY requirements.txt .

# Install dependencies into the venv uv creates
RUN uv venv /app/.venv --python 3.11 && \
    uv pip install --python /app/.venv/bin/python -r requirements.txt

# Copy source
COPY . .

ENV PYTHONPATH=/app
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
