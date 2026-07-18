FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir .

COPY web/ ./web/

RUN mkdir -p data

EXPOSE 8790

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8790"]
