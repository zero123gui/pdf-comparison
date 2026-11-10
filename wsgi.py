"""Ponto de entrada para o servidor de produção: `gunicorn -c gunicorn.conf.py wsgi:app`."""
from webapp import create_app

app = create_app()
