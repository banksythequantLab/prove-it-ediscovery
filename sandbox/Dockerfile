# Sandbox image: no secrets, no network (enforced at run time), non-root.
FROM python:3.12-slim
RUN useradd -u 10001 -M -s /usr/sbin/nologin sandbox && mkdir /work && chown sandbox /work
WORKDIR /work
USER 10001
ENTRYPOINT ["python", "-E", "-s", "main.py"]
