FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.9.5

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-cache

RUN useradd --system --no-create-home walls
USER walls

ENV PATH=/app/.venv/bin:$PATH
ENTRYPOINT ["walls"]
CMD ["serve"]
