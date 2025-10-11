FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY reviewer.py /app/reviewer.py

RUN chmod +x /app/reviewer.py

ENTRYPOINT ["python", "/app/reviewer.py"]
