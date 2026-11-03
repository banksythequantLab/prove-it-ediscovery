FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python scripts/make_demo_corpus.py
ENV SANDBOX_MODE=docker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
