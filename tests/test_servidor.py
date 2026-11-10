import io
import json
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import servidor
from tests import filho
from tests.pdfs import BASE, REVISADA, pdf_simples


class Base(unittest.TestCase):
    """Cada teste roda com uma pasta de dados própria e o servidor inicializado do zero."""
    ajustes = {}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pasta_dados = Path(tmp.name)
        config = dict(PASTA_DADOS=self.pasta_dados, LEITORES=1)
        config.update(self.ajustes)
        patcher = mock.patch.multiple(servidor, **config)
        patcher.start()
        self.addCleanup(patcher.stop)
        servidor.inicia()
        self.addCleanup(self._espera_supervisao)  # roda antes de apagar a pasta temporária
        servidor.app.config["TESTING"] = True
        self.cliente = servidor.app.test_client()
        self.pasta_comparacoes = self.pasta_dados / "comparacoes"

    # ---------------------------------------------------------------- utilitários
    @staticmethod
    def _espera_supervisao(limite=15):
        fim = time.time() + limite
        while servidor._ativos and time.time() < fim:
            time.sleep(0.05)

    def envia(self, base=None, revisada=None, **campos):
        dados = {"base": (io.BytesIO(base or pdf_simples(BASE)), "base.pdf"),
                 "revisada": (io.BytesIO(revisada or pdf_simples(REVISADA, azuis={1, 2})), "revisada.pdf")}
        dados.update(campos)
        return self.cliente.post("/api/comparacoes", data=dados, content_type="multipart/form-data")

    def espera(self, id_comparacao, limite=60):
        fim = time.time() + limite
        while time.time() < fim:
            estado = self.cliente.get("/api/comparacoes/%s/andamento" % id_comparacao).get_json()
            if estado["status"] in ("concluido", "erro"):
                return estado
            time.sleep(0.3)
        self.fail("a comparação não terminou em %ds" % limite)


class TestPaginasEEnvio(Base):
    def test_pagina_e_saude(self):
        pagina = self.cliente.get("/")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("Comparador de PDFs".encode(), pagina.data)
        self.assertIn("default-src 'self'", pagina.headers["Content-Security-Policy"])
        self.assertEqual(self.cliente.get("/saude").data, b"ok")

    def test_envio_incompleto(self):
        for dados in ({}, {"revisada": (io.BytesIO(pdf_simples(REVISADA)), "r.pdf")}):
            with self.subTest(campos=list(dados)):
                resposta = self.cliente.post("/api/comparacoes", data=dados, content_type="multipart/form-data")
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("Envie os dois PDFs", resposta.get_json()["erro"])

    def test_arquivo_que_nao_e_pdf(self):
        resposta = self.envia(base=b"isto nao e um pdf")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("não é um PDF", resposta.get_json()["erro"])
        self.assertEqual(list(self.pasta_comparacoes.iterdir()), [])  # nada sobra de um envio recusado

    def test_ignorar_paginas_invalido(self):
        for valor in ("abc", "-1", "51"):
            with self.subTest(valor=valor):
                self.assertEqual(self.envia(ignorar_paginas=valor).status_code, 400)

    def test_ids_invalidos_ou_inexistentes(self):
        for id_comparacao in ("abc", "../../etc/passwd", "0" * 32, "G" * 32):
            with self.subTest(id_comparacao=id_comparacao):
                self.assertEqual(self.cliente.get("/api/comparacoes/%s/andamento" % id_comparacao).status_code, 404)
                self.assertEqual(self.cliente.get("/comparacoes/%s/relatorio" % id_comparacao).status_code, 404)
                self.assertEqual(self.cliente.delete("/api/comparacoes/" + id_comparacao).status_code, 404)

    def test_relatorio_antes_de_pronto_devolve_409(self):
        id_comparacao, pasta = servidor.nova_comparacao()
        (pasta / "status.json").write_text(json.dumps({"status": "executando"}))
        self.assertEqual(self.cliente.get("/comparacoes/%s/relatorio" % id_comparacao).status_code, 409)


class TestLimites(Base):
    ajustes = {"TAMANHO_MAX_MB": 0}

    def test_arquivo_maior_que_o_limite(self):
        resposta = self.envia()
        self.assertEqual(resposta.status_code, 413)
        self.assertIn("passa de 0 MB", resposta.get_json()["erro"])
        self.assertEqual(list(self.pasta_comparacoes.iterdir()), [])


class TestFila(Base):
    def test_fila_cheia_devolve_429(self):
        servidor._ativos = servidor.MAX_SIMULTANEAS + servidor.MAX_FILA
        resposta = self.envia()
        self.assertEqual(resposta.status_code, 429)
        self.assertEqual(list(self.pasta_comparacoes.iterdir()), [])


class TestComparacao(Base):
    def test_comparacao_completa(self):
        resposta = self.envia(ignorar_paginas="0")
        self.assertEqual(resposta.status_code, 202)
        id_comparacao = resposta.get_json()["id"]
        estado = self.espera(id_comparacao)
        self.assertEqual(estado["status"], "concluido", estado)
        self.assertEqual(estado["totais"]["modificado"], 1)
        self.assertGreaterEqual(estado["totais"]["marcados"], 1)
        self.assertEqual(estado["nomes"], {"base": "base.pdf", "revisada": "revisada.pdf"})

        rel = self.cliente.get("/comparacoes/%s/relatorio" % id_comparacao)
        self.assertEqual((rel.status_code, rel.mimetype), (200, "text/html"))
        self.assertIn(b"CFC", rel.data)
        self.assertIn("default-src 'none'", rel.headers["Content-Security-Policy"])
        baixar = self.cliente.get("/comparacoes/%s/relatorio?baixar=1" % id_comparacao)
        self.assertIn("attachment", baixar.headers["Content-Disposition"])

        # os PDFs enviados não ficam guardados
        pasta = servidor.pasta_do(id_comparacao)
        self.assertFalse((pasta / "base.pdf").exists() or (pasta / "revisada.pdf").exists())

        self.assertEqual(self.cliente.delete("/api/comparacoes/" + id_comparacao).status_code, 204)
        self.assertEqual(self.cliente.get("/api/comparacoes/%s/andamento" % id_comparacao).status_code, 404)
        self.assertEqual(self.cliente.get("/comparacoes/%s/relatorio" % id_comparacao).status_code, 404)

    def test_somente_marcados(self):
        estado = self.espera(self.envia(somente_marcados="1").get_json()["id"])
        self.assertEqual(estado["status"], "concluido")
        self.assertEqual(estado["totais"]["itens"], estado["totais"]["marcados"])

    def test_pdf_corrompido_gera_erro_claro(self):
        estado = self.espera(self.envia(base=b"%PDF-1.4\nlixo sem estrutura").get_json()["id"])
        self.assertEqual(estado["status"], "erro")
        self.assertIn("base.pdf", estado["erro"])
        self.assertIn("corrompido", estado["erro"])

    def test_pdf_sem_texto_gera_erro_claro(self):
        estado = self.espera(self.envia(base=pdf_simples([])).get_json()["id"])
        self.assertEqual(estado["status"], "erro")
        self.assertIn("não tem texto", estado["erro"])


class TestTempoLimite(Base):
    ajustes = {"TEMPO_MAX_S": 0.2}

    def test_tempo_limite_interrompe_a_comparacao(self):
        estado = self.espera(self.envia().get_json()["id"])
        self.assertEqual(estado["status"], "erro")
        self.assertIn("tempo limite", estado["erro"])
        self.assertEqual(servidor._ativos, 0)  # a vaga foi devolvida


class TestManutencao(Base):
    def test_expiracao_remove_comparacoes_antigas(self):
        _, velho = servidor.nova_comparacao()
        (velho / "status.json").write_text(json.dumps({"status": "concluido", "criado_em": time.time() - 90000}))
        _, novo = servidor.nova_comparacao()
        (novo / "status.json").write_text(json.dumps({"status": "concluido", "criado_em": time.time()}))
        self.assertEqual(servidor.limpa_expirados(), 1)
        self.assertFalse(velho.exists())
        self.assertTrue(novo.exists())

    def test_reinicio_marca_comparacoes_interrompidas(self):
        id_comparacao, pasta = servidor.nova_comparacao()
        (pasta / "status.json").write_text(json.dumps({"status": "executando", "criado_em": time.time()}))
        (pasta / "base.pdf").write_bytes(b"%PDF-")
        servidor.recupera()
        estado = servidor.estado(id_comparacao)
        self.assertEqual(estado["status"], "erro")
        self.assertIn("reiniciado", estado["erro"])
        self.assertFalse((pasta / "base.pdf").exists())

    def test_processo_filho_nao_recupera_nem_apaga_comparacoes(self):
        """Regressão: o filho (spawn) reimportava o servidor e apagava os PDFs da comparação em andamento."""
        id_comparacao, pasta = servidor.nova_comparacao()
        (pasta / "status.json").write_text(json.dumps({"status": "executando", "criado_em": time.time()}))
        (pasta / "base.pdf").write_bytes(b"%PDF-")
        proc = multiprocessing.get_context("spawn").Process(target=filho.cria_app_no_filho,
                                                            args=(str(self.pasta_dados),))
        proc.start()
        proc.join(60)
        self.assertEqual(proc.exitcode, 0)
        self.assertTrue((pasta / "base.pdf").exists())
        self.assertEqual(servidor.estado(id_comparacao)["status"], "executando")


def _vivo(pid):
    """Processo existe e não é zumbi (num container sem init, o órfão morto fica como zumbi)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@unittest.skipUnless(os.path.exists("/proc/self/stat"), "precisa de /proc (Linux)")
class TestEncerramento(Base):
    """Regressão: cancelar ou estourar o tempo deixava os leitores de página órfãos gastando CPU."""
    ajustes = {"TEMPO_MAX_S": 2}

    def _sobe_comparacao_lenta(self):
        patcher = mock.patch.object(servidor, "executa_comparacao", filho.comparacao_lenta)
        patcher.start()
        self.addCleanup(patcher.stop)
        id_comparacao = self.envia().get_json()["id"]
        arquivo = servidor.pasta_do(id_comparacao) / "neto.pid"
        fim = time.time() + 20
        while not arquivo.exists() and time.time() < fim:
            time.sleep(0.1)
        neto = int(arquivo.read_text())
        self.assertTrue(_vivo(neto))
        return id_comparacao, neto

    def _espera_morrer(self, neto):
        fim = time.time() + 10
        while _vivo(neto) and time.time() < fim:
            time.sleep(0.1)
        self.assertFalse(_vivo(neto), "o processo filho da comparação continuou vivo")

    def test_tempo_limite_mata_tambem_os_filhos(self):
        id_comparacao, neto = self._sobe_comparacao_lenta()
        self.assertEqual(self.espera(id_comparacao, 30)["status"], "erro")
        self._espera_morrer(neto)

    def test_apagar_com_processo_rodando_nao_deixa_a_comparacao_para_tras(self):
        """Regressão: a supervisão gravava um status novo na pasta que estava sendo apagada.

        O defeito real é uma corrida (a supervisão acorda no meio da remoção da pasta). Para
        reproduzir sempre, o último passo da remoção é atrasado e a supervisão usa um estado lido
        antes de a pasta ser apagada, de modo que ela tenta gravar com a pasta já sem status.json."""
        real_rmdir, real_estado = os.rmdir, servidor.estado

        def rmdir_lento(*args, **kwargs):
            time.sleep(1.0)
            return real_rmdir(*args, **kwargs)

        def estado_lento_na_supervisao(id_comparacao):
            valor = real_estado(id_comparacao)  # lido antes de a pasta ser apagada...
            if threading.current_thread().name.startswith("supervisor-"):
                time.sleep(0.5)                 # ...e usado só depois, quando já não vale
            return valor

        with mock.patch.object(servidor, "TEMPO_MAX_S", 600):
            id_comparacao, _ = self._sobe_comparacao_lenta()
            with mock.patch.object(os, "rmdir", rmdir_lento), \
                    mock.patch.object(servidor, "estado", estado_lento_na_supervisao):
                self.assertEqual(self.cliente.delete("/api/comparacoes/" + id_comparacao).status_code, 204)
                fim = time.time() + 10
                while servidor._ativos and time.time() < fim:  # espera a supervisão terminar
                    time.sleep(0.05)
            self.assertFalse(servidor.pasta_do(id_comparacao).exists())
            self.assertEqual(self.cliente.get("/api/comparacoes/%s/andamento" % id_comparacao).status_code, 404)

    def test_cancelar_mata_tambem_os_filhos(self):
        with mock.patch.object(servidor, "TEMPO_MAX_S", 600):
            id_comparacao, neto = self._sobe_comparacao_lenta()
            self.assertEqual(self.cliente.delete("/api/comparacoes/" + id_comparacao).status_code, 204)
            self._espera_morrer(neto)


if __name__ == "__main__":
    unittest.main()
