# Crash 1000 alert bot — long-running Deriv websocket watcher.
# Runs as a plain process (no ports); used by Northflank and any
# other container host. Secrets come from environment variables,
# never from files baked into the image.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
