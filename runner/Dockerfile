FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker-cli && rm -rf /var/lib/apt/lists/*
COPY runner.py /runner.py
CMD ["python", "-u", "/runner.py"]
