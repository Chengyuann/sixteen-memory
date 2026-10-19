FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY sixteen_memory ./sixteen_memory
RUN pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home app \
    && mkdir /data && chown app:app /data
USER app
EXPOSE 8080
CMD ["uvicorn", "sixteen_memory.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
