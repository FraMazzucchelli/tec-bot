FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY manuals ./manuals

# Railway/Render/Fly passano la porta in $PORT
CMD ["sh", "-c", "uvicorn argo.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
