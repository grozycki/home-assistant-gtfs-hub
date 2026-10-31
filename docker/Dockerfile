# A test instance only. The libraries come from local wheels, so nothing is published.
FROM ghcr.io/home-assistant/home-assistant:2026.10.4

COPY wheels/ /wheels/
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
