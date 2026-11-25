FROM python:3.12-slim
# poppler-utils: turns scanned PDFs into images for the model
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 1000 ledger
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY skills ./skills
COPY prompts ./prompts
COPY static ./static
COPY samples ./samples
COPY tests ./tests
ENV LEDGER_DATA_DIR=/data PYTHONUNBUFFERED=1
RUN mkdir /data && chown ledger:ledger /data
USER ledger
VOLUME /data
EXPOSE 8890
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8890/healthz', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8890"]
