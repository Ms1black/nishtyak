FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels '.[conda]'

FROM python:3.11-slim
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links /wheels 'nishtyak-core[conda]' \
    && rm -rf /wheels
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
USER 65532:65532
ENTRYPOINT ["python", "-I", "-m", "nishtyak_core.worker"]
CMD ["--archive", "-", "--output", "-"]
