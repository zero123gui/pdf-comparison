"""Integração com os PDFs do repositório (ITAB x ITAC). Lento: rode com `pytest -m lento`."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
ITAB, ITAC = RAIZ / "ITAB.pdf", RAIZ / "ITAC.pdf"

pytestmark = [pytest.mark.lento, pytest.mark.skipif(not (ITAB.exists() and ITAC.exists()), reason="PDFs de exemplo ausentes")]


def test_relatorio_web_igual_ao_da_linha_de_comando(tmp_path):
    from webapp import create_app
    cli = tmp_path / "cli.html"
    subprocess.run([sys.executable, str(RAIZ / "pdf_diff.py"), "--base", "ITAB.pdf", "--revisada", "ITAC.pdf",
                    "--saida", str(cli), "--ignorar-paginas", "1"], cwd=RAIZ, check=True, capture_output=True)

    cliente = create_app({"TESTING": True, "DATA_DIR": tmp_path / "dados"}).test_client()
    with open(ITAB, "rb") as b, open(ITAC, "rb") as r:
        resposta = cliente.post("/api/jobs", data={"base": (b, "ITAB.pdf"), "revisada": (r, "ITAC.pdf"),
                                                   "ignorar_paginas": "1"}, content_type="multipart/form-data")
    job_id = resposta.get_json()["id"]
    fim = time.time() + 180
    while time.time() < fim:
        estado = cliente.get("/api/jobs/" + job_id).get_json()
        if estado["status"] in ("concluido", "erro"):
            break
        time.sleep(1)
    assert estado["status"] == "concluido", estado
    assert estado["totais"]["itens"] == 26 and estado["totais"]["marcados"] == 24
    assert cliente.get("/jobs/%s/relatorio" % job_id).data == cli.read_bytes()
