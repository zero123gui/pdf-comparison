import io
import json
import time

import pytest

from pdfs import BASE, REVISADA, pdf_simples
from webapp import create_app
from webapp.jobs import Jobs


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATA_DIR": tmp_path, "WORKERS": 1})


@pytest.fixture
def cliente(app):
    return app.test_client()


def arquivo(conteudo, nome="a.pdf"):
    return (io.BytesIO(conteudo), nome)


def envia(cliente, base=None, revisada=None, **campos):
    dados = {"base": arquivo(base or pdf_simples(BASE), "base.pdf"),
             "revisada": arquivo(revisada or pdf_simples(REVISADA, azuis={1, 2}), "revisada.pdf")}
    dados.update(campos)
    return cliente.post("/api/jobs", data=dados, content_type="multipart/form-data")


def espera(cliente, job_id, limite=60):
    fim = time.time() + limite
    while time.time() < fim:
        estado = cliente.get("/api/jobs/" + job_id).get_json()
        if estado["status"] in ("concluido", "erro"):
            return estado
        time.sleep(0.3)
    raise AssertionError("o trabalho não terminou em %ds" % limite)


def test_pagina_e_saude(cliente):
    pagina = cliente.get("/")
    assert pagina.status_code == 200
    assert "Comparador de PDFs".encode() in pagina.data
    assert "default-src 'self'" in pagina.headers["Content-Security-Policy"]
    assert cliente.get("/saude").data == b"ok"


def test_comparacao_completa(cliente, app):
    resposta = envia(cliente, ignorar_paginas="0")
    assert resposta.status_code == 202
    job_id = resposta.get_json()["id"]
    estado = espera(cliente, job_id)
    assert estado["status"] == "concluido", estado
    assert estado["totais"]["modificado"] == 1
    assert estado["totais"]["marcados"] >= 1
    assert estado["nomes"] == {"base": "base.pdf", "revisada": "revisada.pdf"}

    rel = cliente.get("/jobs/%s/relatorio" % job_id)
    assert rel.status_code == 200 and rel.mimetype == "text/html"
    assert b"CFC" in rel.data
    assert "default-src 'none'" in rel.headers["Content-Security-Policy"]
    assert "attachment" in cliente.get("/jobs/%s/relatorio?baixar=1" % job_id).headers["Content-Disposition"]

    # os PDFs enviados não ficam guardados
    pasta = app.jobs.pasta_do(job_id)
    assert not (pasta / "base.pdf").exists() and not (pasta / "revisada.pdf").exists()

    assert cliente.delete("/api/jobs/" + job_id).status_code == 204
    assert cliente.get("/api/jobs/" + job_id).status_code == 404
    assert cliente.get("/jobs/%s/relatorio" % job_id).status_code == 404


def test_somente_marcados(cliente):
    estado = espera(cliente, envia(cliente, somente_marcados="1").get_json()["id"])
    assert estado["status"] == "concluido"
    assert estado["totais"]["itens"] == estado["totais"]["marcados"]


def test_relatorio_antes_de_pronto_devolve_409(cliente, app):
    job_id, pasta = app.jobs.novo()
    (pasta / "status.json").write_text(json.dumps({"status": "executando"}))
    assert cliente.get("/jobs/%s/relatorio" % job_id).status_code == 409


@pytest.mark.parametrize("campos,trecho", [
    ({}, "Envie os dois PDFs"),
    ({"base": None}, "Envie os dois PDFs"),
])
def test_envio_incompleto(cliente, campos, trecho):
    dados = {"revisada": arquivo(pdf_simples(REVISADA))}
    resposta = cliente.post("/api/jobs", data=dados, content_type="multipart/form-data")
    assert resposta.status_code == 400 and trecho in resposta.get_json()["erro"]


def test_arquivo_que_nao_e_pdf(cliente, app):
    resposta = envia(cliente, base=b"isto nao e um pdf")
    assert resposta.status_code == 400
    assert "não é um PDF" in resposta.get_json()["erro"]
    assert list((app.jobs.pasta).iterdir()) == []  # nada sobra de um envio recusado


@pytest.mark.parametrize("valor", ["abc", "-1", "51"])
def test_ignorar_paginas_invalido(cliente, valor):
    assert envia(cliente, ignorar_paginas=valor).status_code == 400


def test_arquivo_maior_que_o_limite(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path, "MAX_FILE_MB": 0})
    resposta = envia(app.test_client())
    assert resposta.status_code == 413 and "passa de 0 MB" in resposta.get_json()["erro"]
    assert list(app.jobs.pasta.iterdir()) == []


def test_fila_cheia_devolve_429(cliente, app):
    app.jobs._ativos = app.jobs.max_concorrente + app.jobs.max_fila
    resposta = envia(cliente)
    assert resposta.status_code == 429
    assert list(app.jobs.pasta.iterdir()) == []


@pytest.mark.parametrize("job_id", ["abc", "../../etc/passwd", "0" * 32, "G" * 32])
def test_ids_invalidos_ou_inexistentes(cliente, job_id):
    assert cliente.get("/api/jobs/" + job_id).status_code == 404
    assert cliente.get("/jobs/%s/relatorio" % job_id).status_code == 404
    assert cliente.delete("/api/jobs/" + job_id).status_code == 404


def test_pdf_corrompido_gera_erro_claro(cliente):
    estado = espera(cliente, envia(cliente, base=b"%PDF-1.4\nlixo sem estrutura").get_json()["id"])
    assert estado["status"] == "erro"
    assert "base.pdf" in estado["erro"] and "corrompido" in estado["erro"]


def test_pdf_sem_texto_gera_erro_claro(cliente):
    estado = espera(cliente, envia(cliente, base=pdf_simples([])).get_json()["id"])
    assert estado["status"] == "erro"
    assert "não tem texto" in estado["erro"]


def test_tempo_limite_interrompe_a_comparacao(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path, "WORKERS": 1, "TIMEOUT_S": 0.2})
    cliente = app.test_client()
    estado = espera(cliente, envia(cliente).get_json()["id"])
    assert estado["status"] == "erro" and "tempo limite" in estado["erro"]
    assert app.jobs._ativos == 0  # a vaga foi devolvida


def test_expiracao_remove_trabalhos_antigos(app):
    velho, pasta_velho = app.jobs.novo()
    (pasta_velho / "status.json").write_text(json.dumps({"status": "concluido", "criado_em": time.time() - 90000}))
    novo, pasta_novo = app.jobs.novo()
    (pasta_novo / "status.json").write_text(json.dumps({"status": "concluido", "criado_em": time.time()}))
    assert app.jobs.limpa_expirados() == 1
    assert not pasta_velho.exists() and pasta_novo.exists()


def test_reinicio_marca_trabalhos_interrompidos(tmp_path, app):
    job_id, pasta = app.jobs.novo()
    (pasta / "status.json").write_text(json.dumps({"status": "executando", "criado_em": time.time()}))
    (pasta / "base.pdf").write_bytes(b"%PDF-")
    Jobs(app.config).recupera()
    estado = app.jobs.estado(job_id)
    assert estado["status"] == "erro" and "reiniciado" in estado["erro"]
    assert not (pasta / "base.pdf").exists()


def test_processo_filho_nao_recupera_nem_apaga_trabalhos(app, tmp_path):
    """Regressão: o filho (spawn) reimportava a aplicação e apagava os PDFs do trabalho em andamento."""
    import multiprocessing

    from filho import cria_app_no_filho
    job_id, pasta = app.jobs.novo()
    (pasta / "status.json").write_text(json.dumps({"status": "executando", "criado_em": time.time()}))
    (pasta / "base.pdf").write_bytes(b"%PDF-")
    filho = multiprocessing.get_context("spawn").Process(target=cria_app_no_filho, args=(str(tmp_path),))
    filho.start()
    filho.join(60)
    assert filho.exitcode == 0
    assert (pasta / "base.pdf").exists()
    assert app.jobs.estado(job_id)["status"] == "executando"


def _vivo(pid):
    """Processo existe e não é zumbi (num container sem init, o órfão morto fica como zumbi)."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


@pytest.mark.skipif(not __import__("os").path.exists("/proc/self/stat"), reason="precisa de /proc")
@pytest.mark.parametrize("como", ["tempo", "cancelar"])
def test_encerrar_mata_tambem_os_filhos_da_comparacao(tmp_path, monkeypatch, como):
    """Regressão: cancelar ou estourar o tempo deixava os leitores de página órfãos gastando CPU."""
    import filho
    from webapp import worker
    monkeypatch.setattr(worker, "run_job", filho.trabalho_lento)
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path, "WORKERS": 1,
                      "TIMEOUT_S": 2 if como == "tempo" else 600})
    cliente = app.test_client()
    job_id = envia(cliente).get_json()["id"]
    pasta = app.jobs.pasta_do(job_id)
    fim = time.time() + 20
    while not (pasta / "neto.pid").exists() and time.time() < fim:
        time.sleep(0.1)
    neto = int((pasta / "neto.pid").read_text())
    assert _vivo(neto)
    if como == "cancelar":
        assert cliente.delete("/api/jobs/" + job_id).status_code == 204
    else:
        assert espera(cliente, job_id, 30)["status"] == "erro"
    fim = time.time() + 10
    while _vivo(neto) and time.time() < fim:
        time.sleep(0.1)
    assert not _vivo(neto), "o processo filho da comparação continuou vivo"
