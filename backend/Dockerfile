FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=8 \
    PORT=8000
WORKDIR /workspace/backend

RUN addgroup --system --gid 1001 app && adduser --system --uid 1001 --ingroup app app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# backend/.data holds the encrypted provider connections, support drafts and
# order-tracking watches. On AWS it is an EFS mount so a task restart keeps them.
RUN mkdir -p /workspace/backend/.data && chown -R app:app /workspace

USER app
EXPOSE 8000
# Migrations run before the server on every start. The ECS service runs at most
# one backend task (MaximumPercent 100), so two tasks never migrate at once.
# Keep-alive outlives the ALB idle timeout (120 s) so the ALB never reuses a
# connection uvicorn already closed (intermittent 502s).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 130"]
