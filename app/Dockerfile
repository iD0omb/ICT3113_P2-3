FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 5000
# One worker process with a thread per in-flight request; threads mostly wait
# on Ollama, and share one Postgres connection pool.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:5000 --workers 1 --worker-class gthread --threads ${GUNICORN_THREADS:-16} --timeout 0 app:app"]
