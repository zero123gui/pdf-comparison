"""Integração com os PDFs do repositório (ITAB x ITAC), cerca de 45 s.

Não roda junto com os demais testes. Para rodar:  python -m unittest tests.integracao_pdfs_reais
"""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import servidor

RAIZ = Path(__file__).resolve().parent.parent
ITAB, ITAC = RAIZ / "ITAB.pdf", RAIZ / "ITAC.pdf"


@unittest.skipUnless(ITAB.exists() and ITAC.exists(), "PDFs de exemplo ausentes")
class TestPdfsReais(unittest.TestCase):
    def test_relatorio_web_igual_ao_da_linha_de_comando(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cli = tmp / "cli.html"
            subprocess.run([sys.executable, str(RAIZ / "pdf_diff.py"), "--base", "ITAB.pdf", "--revisada",
                            "ITAC.pdf", "--saida", str(cli), "--ignorar-paginas", "1"],
                           cwd=RAIZ, check=True, capture_output=True)
            with mock.patch.multiple(servidor, PASTA_DADOS=tmp / "dados"):
                servidor.inicia()
                cliente = servidor.app.test_client()
                with open(ITAB, "rb") as b, open(ITAC, "rb") as r:
                    resposta = cliente.post("/api/jobs", content_type="multipart/form-data", data={
                        "base": (b, "ITAB.pdf"), "revisada": (r, "ITAC.pdf"), "ignorar_paginas": "1"})
                job_id = resposta.get_json()["id"]
                fim = time.time() + 180
                while time.time() < fim:
                    estado = cliente.get("/api/jobs/" + job_id).get_json()
                    if estado["status"] in ("concluido", "erro"):
                        break
                    time.sleep(1)
                self.assertEqual(estado["status"], "concluido", estado)
                self.assertEqual((estado["totais"]["itens"], estado["totais"]["marcados"]), (26, 24))
                self.assertEqual(cliente.get("/jobs/%s/relatorio" % job_id).data, cli.read_bytes())


if __name__ == "__main__":
    unittest.main()
