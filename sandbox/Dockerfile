# Sandbox image: no secrets, no network (enforced at run time), non-root. matplotlib baked in (no pip at run time).
FROM python:3.12-slim
RUN pip install --no-cache-dir matplotlib==3.* && useradd -u 10001 -M -s /usr/sbin/nologin sandbox && mkdir /work && chown sandbox /work
ENV MPLBACKEND=Agg
WORKDIR /work
USER 10001
ENTRYPOINT ["python", "-E", "-s", "_boot.py"]
