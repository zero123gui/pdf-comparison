FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PDFDIFF_DATA_DIR=/data \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pdf_diff.py wsgi.py gunicorn.conf.py ./
COPY webapp webapp

RUN useradd --system --uid 10001 app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saude')"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
