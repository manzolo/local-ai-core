# Headless Chromium for the README screenshots (make screenshots). Browsers come with the base image.
FROM mcr.microsoft.com/playwright/python:v1.65.0-noble@sha256:f9294526294a7bdfb8ca8aa7d815c049125a10e73b65d47fb9a80f5d74f284a6
RUN pip install --no-cache-dir --break-system-packages playwright==1.65.0
COPY shoot.py /shoot.py
ENTRYPOINT ["python3", "/shoot.py"]
