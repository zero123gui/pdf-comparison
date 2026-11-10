"""Servidor de desenvolvimento local: `python run.py` e abra http://127.0.0.1:5000

Não use em produção (veja o README: gunicorn). O reloader fica desligado porque cada
comparação roda em um processo próprio.
"""
import os

from webapp import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)),
            debug=False, threaded=True)
