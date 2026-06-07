FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.110" \
    "uvicorn[standard]>=0.29" \
    "sqlalchemy>=2.0" \
    "faker>=24.0" \
    "pyyaml>=6.0" \
    "python-dotenv>=1.0"

COPY mock_site/ mock_site/

ENV DATABASE_URL=sqlite:///./mockdb.sqlite
ENV CHAOS_CONFIG_PATH=/app/chaos_config.yaml

EXPOSE 8000
CMD ["uvicorn", "mock_site.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
