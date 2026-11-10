FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PDFDIFF_DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pdf_diff.py servidor.py ./
COPY templates templates
COPY static static

RUN useradd --system --uid 10001 app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/saude')"
CMD ["python", "servidor.py"]
