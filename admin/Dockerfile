FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py sources.py pricing.py billing.py periods.py history.py local_source.py host_metrics.py usage_store.py vm_source.py ./
COPY static ./static
RUN mkdir /data && chown 65532:65532 /data
USER 65532:65532
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
