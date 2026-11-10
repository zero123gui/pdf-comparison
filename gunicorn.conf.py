"""Configuração do gunicorn.

Um único processo com várias threads: o limite de comparações simultâneas e o estado da fila
ficam corretos sem coordenação entre processos. O trabalho pesado roda em processos próprios.
"""
import os

bind = "%s:%s" % (os.environ.get("HOST", "0.0.0.0"), os.environ.get("PORT", "8000"))
workers = 1
threads = 8
timeout = 300          # envio de PDFs grandes por conexão lenta
accesslog = "-"
errorlog = "-"
