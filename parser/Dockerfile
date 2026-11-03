# Parser sandbox image. Code is baked in (the app cannot change what runs). No secrets, no network (enforced at run time).
FROM python:3.12-slim
RUN pip install --no-cache-dir pypdf==5.* python-docx==1.* openpyxl==3.* beautifulsoup4==4.* extract-msg==0.* \
 && useradd -u 10001 -M -s /usr/sbin/nologin sandbox
COPY parse.py /opt/parse.py
USER 10001
ENTRYPOINT ["python", "-I", "/opt/parse.py"]
