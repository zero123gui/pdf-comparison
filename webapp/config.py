"""Configuração por variáveis de ambiente (cada servidor ajusta sem mexer no código)."""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _inteiro(nome, padrao):
    try:
        return int(os.environ.get(nome, padrao))
    except ValueError:
        raise SystemExit("A variável %s deve ser um número inteiro." % nome)


def carrega(extra=None):
    cfg = {
        "DATA_DIR": Path(os.environ.get("PDFDIFF_DATA_DIR", RAIZ / "data")),
        "MAX_FILE_MB": _inteiro("PDFDIFF_MAX_FILE_MB", 60),
        "MAX_CONCURRENT": _inteiro("PDFDIFF_MAX_CONCURRENT", 2),
        "MAX_QUEUE": _inteiro("PDFDIFF_MAX_QUEUE", 6),
        "TIMEOUT_S": _inteiro("PDFDIFF_TIMEOUT_MIN", 20) * 60,
        "TTL_S": _inteiro("PDFDIFF_TTL_HORAS", 24) * 3600,
        "WORKERS": _inteiro("PDFDIFF_WORKERS", 4),
        "DEFAULT_IGNORAR_PAGINAS": _inteiro("PDFDIFF_IGNORAR_PAGINAS", 1),
    }
    cfg.update(extra or {})
    cfg["DATA_DIR"] = Path(cfg["DATA_DIR"])
    return cfg
