# The recorder API on Cloud Run (infra/api.tf). Built by `just api-deploy` (Cloud Build).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/ api/

USER nobody
# Cloud Run sets PORT
CMD ["sh", "-c", "exec uvicorn api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --no-access-log --proxy-headers --forwarded-allow-ips='*'"]
