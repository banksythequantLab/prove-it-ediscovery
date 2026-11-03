# Parser sandbox image. Code is baked in (the app cannot change what runs). No secrets, no network (enforced at run time).
FROM python:3.12-slim
# OCR for scanned PDFs and images. Runs under the same sandbox limits (no network, 512 MB, 1 CPU, 64 pids, 20 s).
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng poppler-utils \
 && rm -rf /var/lib/apt/lists/*
ENV OMP_THREAD_LIMIT=1
RUN pip install --no-cache-dir pypdf==5.* python-docx==1.* openpyxl==3.* beautifulsoup4==4.* extract-msg==0.* \
 && useradd -u 10001 -M -s /usr/sbin/nologin sandbox
COPY parse.py /opt/parse.py
USER 10001
ENTRYPOINT ["python", "-I", "/opt/parse.py"]
