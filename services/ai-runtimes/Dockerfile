FROM python:3.11-slim

ARG RUNTIME_KIND=voice

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LAURA_RUNTIME_KIND=${RUNTIME_KIND} \
    LAURA_RUNTIME_MODE=model \
    LAURA_MODEL_ROOT=/models

WORKDIR /opt/laura-runtime

RUN useradd --create-home --uid 10001 laura \
    && mkdir -p /models /workspace \
    && chown -R laura:laura /models /workspace /opt/laura-runtime

COPY runtime_server.py /opt/laura-runtime/runtime_server.py

USER laura

EXPOSE 8898 8899 8901

CMD ["python", "/opt/laura-runtime/runtime_server.py"]
