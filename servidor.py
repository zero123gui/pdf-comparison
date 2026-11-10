# Servidor web do comparador de PDFs: recebe a versão base e a revisada, compara em segundo
# plano (pdf_diff.py) e mostra o relatório no navegador.
#
# Para rodar:  python servidor.py   (abre em http://localhost:8080)
# Pacotes externos: só Flask e pdfplumber (pip install -r requirements.txt).

import json
import logging
import multiprocessing
import os
import re
import shutil
import signal
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

import pdf_diff

app = Flask(__name__)


def _inteiro(nome, padrao):
    try:
        return int(os.environ.get(nome, padrao))
    except ValueError:
        raise SystemExit("A variável de ambiente %s deve ser um número inteiro." % nome)


# Configuração: cada servidor pode mudar por variável de ambiente, sem mexer no código
HOST = os.environ.get("HOST", "localhost")                       # use 0.0.0.0 para abrir para a rede
PORTA = _inteiro("PORT", 8080)
PASTA_DADOS = Path(os.environ.get("PDFDIFF_DATA_DIR", Path(__file__).resolve().parent / "data"))
TAMANHO_MAX_MB = _inteiro("PDFDIFF_MAX_FILE_MB", 60)              # por PDF
MAX_SIMULTANEAS = _inteiro("PDFDIFF_MAX_CONCURRENT", 2)           # comparações ao mesmo tempo
MAX_FILA = _inteiro("PDFDIFF_MAX_QUEUE", 6)                       # esperando; além disso, erro 429
TEMPO_MAX_S = _inteiro("PDFDIFF_TIMEOUT_MIN", 20) * 60            # por comparação
VALIDADE_S = _inteiro("PDFDIFF_TTL_HORAS", 24) * 3600             # quanto o relatório fica disponível
LEITORES = _inteiro("PDFDIFF_WORKERS", 4)                         # processos de leitura de páginas
IGNORAR_PAGINAS_PADRAO = _inteiro("PDFDIFF_IGNORAR_PAGINAS", 1)   # valor inicial do campo da página

MB = 1024 * 1024
ID_VALIDO = re.compile(r"[0-9a-f]{32}")
TERMINAIS = ("concluido", "erro")
CSP_RELATORIO = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                 "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
CSP_PAGINA = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
              "frame-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

log = logging.getLogger("pdfdiff")

# Estado da fila, só neste processo (o estado de cada trabalho fica em arquivos: status.json)
_contexto = multiprocessing.get_context("spawn")
_trava = threading.Lock()
_semaforo = threading.BoundedSemaphore(MAX_SIMULTANEAS)
_ativos = 0          # na fila + executando
_processos = {}      # id do trabalho -> processo da comparação


class AppException(Exception):
    """Erro esperado, com mensagem para o usuário e o código HTTP da resposta."""

    def __init__(self, mensagem, codigo=400):
        super().__init__(mensagem)
        self.codigo = codigo


# ------------------------------------------------------------------ pastas e estado em arquivos
def pasta_do(job_id):
    """Pasta do trabalho; None se o id não tem o formato esperado (impede ../ e afins)."""
    if not ID_VALIDO.fullmatch(job_id or ""):
        return None
    return PASTA_DADOS / "jobs" / job_id


def novo_trabalho():
    """Cria a pasta de um trabalho novo (a vaga já deve estar reservada)."""
    job_id = uuid.uuid4().hex
    pasta = PASTA_DADOS / "jobs" / job_id
    pasta.mkdir()
    return job_id, pasta


def escreve_status(pasta, **campos):
    """Grava status.json de forma atômica, para ninguém ler um arquivo pela metade."""
    pasta = Path(pasta)
    if not pasta.exists():  # o trabalho foi apagado enquanto rodava
        return
    caminho = pasta / "status.json"
    atual = {}
    if caminho.exists():
        try:
            atual = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            atual = {}
    atual.update(campos)
    tmp = pasta / ("status.json.%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, caminho)


def estado(job_id):
    """Conteúdo de status.json mais os nomes dos arquivos e a validade; {} se não existe."""
    pasta = pasta_do(job_id)
    if pasta is None:
        return {}
    try:
        status = json.loads((pasta / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
        status["nomes"] = {"base": meta["nome_base"], "revisada": meta["nome_revisada"]}
    except (OSError, ValueError, KeyError):
        pass
    if "criado_em" in status:
        status["expira_em"] = status["criado_em"] + VALIDADE_S
    return status


def apaga_entradas(pasta):
    """Os PDFs enviados não ficam guardados: saem antes de o trabalho ser dado como terminado."""
    for nome in ("base.pdf", "revisada.pdf"):
        (Path(pasta) / nome).unlink(missing_ok=True)


# ------------------------------------------------------------------ processo da comparação
def novo_grupo():
    """Faz deste processo líder de um grupo próprio: o servidor encerra o grupo inteiro (filhos
    incluídos) ao cancelar ou estourar o tempo, sem deixar leitores de página órfãos."""
    if hasattr(os, "setsid"):
        os.setsid()


def executa_comparacao(pasta, leitores):
    """Corpo do processo que faz UMA comparação. Roda isolado do servidor web."""
    novo_grupo()
    pasta = Path(pasta)
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    opcoes = meta["opcoes"]
    escreve_status(pasta, status="executando", etapa="Iniciando", progresso=0.01)
    try:
        resultado = pdf_diff.comparar_pdfs(
            str(pasta / "base.pdf"), str(pasta / "revisada.pdf"),
            ignorar_paginas=opcoes["ignorar_paginas"], somente_marcados=opcoes["somente_marcados"],
            workers=leitores,
            progresso=lambda etapa, fracao: escreve_status(pasta, etapa=etapa, progresso=round(fracao, 3)),
            nome_base=meta["nome_base"], nome_revisada=meta["nome_revisada"])
        apaga_entradas(pasta)
        tmp = pasta / "relatorio.html.tmp"
        tmp.write_text(resultado["html"], encoding="utf-8")
        os.replace(tmp, pasta / "relatorio.html")
        escreve_status(pasta, status="concluido", etapa="Concluído", progresso=1.0, totais=resultado["totais"])
    except pdf_diff.ErroComparacao as erro_leitura:
        apaga_entradas(pasta)
        if erro_leitura.__cause__ is not None:  # causa técnica só no log do servidor, nunca na resposta
            traceback.print_exception(erro_leitura.__cause__, file=sys.stderr)
        escreve_status(pasta, status="erro", etapa="Erro", erro=str(erro_leitura))
    except Exception:  # o detalhe técnico fica só no log; a resposta nunca leva o conteúdo do PDF
        traceback.print_exc(file=sys.stderr)
        apaga_entradas(pasta)
        escreve_status(pasta, status="erro", etapa="Erro", erro=(
            "Erro inesperado ao comparar os arquivos. Tente novamente; se repetir, avise o responsável pelo sistema."))


def encerra(proc, espera=5):
    """Encerra o processo da comparação e todos os filhos dele."""
    def sinal(numero):
        try:
            os.killpg(proc.pid, numero)
        except (ProcessLookupError, PermissionError, AttributeError):
            # grupo ainda não criado (o filho acabou de nascer) ou sistema sem grupos (Windows)
            (proc.terminate if numero == signal.SIGTERM else proc.kill)()
    sinal(signal.SIGTERM)
    proc.join(espera)
    if proc.is_alive():
        sinal(getattr(signal, "SIGKILL", signal.SIGTERM))
        proc.join(espera)


# ------------------------------------------------------------------ fila e supervisão
def reserva_vaga():
    global _ativos
    with _trava:
        if _ativos >= MAX_SIMULTANEAS + MAX_FILA:
            raise AppException("O servidor está comparando outros documentos agora. "
                               "Tente de novo em alguns minutos.", 429)
        _ativos += 1


def libera_vaga():
    global _ativos
    with _trava:
        _ativos = max(0, _ativos - 1)


def inicia_trabalho(job_id, meta):
    """Registra o trabalho como 'na fila' e o entrega a uma thread que espera a vez de rodar."""
    pasta = pasta_do(job_id)
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    escreve_status(pasta, status="na_fila", etapa="Na fila", progresso=0.0, criado_em=time.time())
    threading.Thread(target=supervisiona, args=(job_id,), name="job-" + job_id[:6], daemon=True).start()


def supervisiona(job_id):
    """Espera uma vaga, roda a comparação num processo próprio e vigia o tempo limite."""
    pasta = pasta_do(job_id)
    try:
        with _semaforo:
            if not pasta.exists():  # apagado enquanto esperava na fila
                return
            inicio = time.time()
            proc = _contexto.Process(target=executa_comparacao, args=(str(pasta), LEITORES),
                                     name="comparacao-" + job_id[:6])
            proc.start()
            _processos[job_id] = proc
            try:
                while proc.is_alive():
                    proc.join(0.5)
                    if time.time() - inicio > TEMPO_MAX_S and proc.is_alive():
                        encerra(proc)
                        escreve_status(pasta, status="erro", etapa="Erro", erro=(
                            "A comparação passou do tempo limite (%d minutos) e foi interrompida. "
                            "Tente ignorar mais páginas ou usar arquivos menores." % (TEMPO_MAX_S // 60)))
                        break
            finally:
                _processos.pop(job_id, None)
            if pasta.exists() and estado(job_id).get("status") not in TERMINAIS:
                escreve_status(pasta, status="erro", etapa="Erro", erro=(
                    "O processamento terminou de forma inesperada (talvez faltou memória). "
                    "Tente novamente com arquivos menores."))
            log.info("trabalho %s terminou: %s em %.1fs", job_id[:8], estado(job_id).get("status"),
                     time.time() - inicio)
    except Exception:
        log.exception("falha ao supervisionar o trabalho %s", job_id[:8])
        escreve_status(pasta, status="erro", etapa="Erro", erro="Erro interno do servidor.")
    finally:
        apaga_entradas(pasta)  # garantia extra: nada do que foi enviado fica guardado
        libera_vaga()


def remove_trabalho(job_id):
    """Apaga o trabalho; se a comparação estiver rodando, ela é interrompida."""
    pasta = pasta_do(job_id)
    if pasta is None or not pasta.exists():
        return False
    proc = _processos.get(job_id)
    if proc is not None and proc.is_alive():
        encerra(proc)
    shutil.rmtree(pasta, ignore_errors=True)
    return True


def limpa_expirados():
    agora, removidos = time.time(), 0
    for pasta in (PASTA_DADOS / "jobs").iterdir():
        if not (pasta.is_dir() and ID_VALIDO.fullmatch(pasta.name)):
            continue
        try:
            criado = json.loads((pasta / "status.json").read_text(encoding="utf-8")).get("criado_em")
        except (OSError, ValueError):
            criado = None
        if criado is None:  # pasta sem status (envio interrompido): vale a data da pasta
            criado = pasta.stat().st_mtime
        if agora - criado > VALIDADE_S and pasta.name not in _processos:
            shutil.rmtree(pasta, ignore_errors=True)
            removidos += 1
    if removidos:
        log.info("removidos %d trabalhos expirados", removidos)
    return removidos


def recupera():
    """Ao iniciar: os trabalhos que estavam rodando morreram junto com o servidor anterior."""
    for pasta in (PASTA_DADOS / "jobs").iterdir():
        if pasta.is_dir() and ID_VALIDO.fullmatch(pasta.name):
            if estado(pasta.name).get("status") in ("na_fila", "executando"):
                escreve_status(pasta, status="erro", etapa="Erro", erro=(
                    "O servidor foi reiniciado durante a comparação. Envie os arquivos novamente."))
                apaga_entradas(pasta)


def inicia(limpeza_periodica=False):
    """Prepara o servidor. Só o processo principal faz isto: um processo de comparação (filho,
    iniciado com spawn) que importe este arquivo não pode mexer nas pastas dos trabalhos."""
    global _semaforo, _ativos
    if multiprocessing.parent_process() is not None:
        return
    app.config["MAX_CONTENT_LENGTH"] = 2 * TAMANHO_MAX_MB * MB + MB
    (PASTA_DADOS / "jobs").mkdir(parents=True, exist_ok=True)
    _semaforo = threading.BoundedSemaphore(MAX_SIMULTANEAS)
    _ativos = 0
    recupera()
    limpa_expirados()
    if limpeza_periodica:
        def ciclo():
            while True:
                time.sleep(600)
                try:
                    limpa_expirados()
                except Exception:
                    log.exception("falha na limpeza de trabalhos expirados")
        threading.Thread(target=ciclo, name="limpeza", daemon=True).start()


# ------------------------------------------------------------------ rotas
def erro(mensagem, codigo):
    resposta = jsonify({"erro": mensagem})
    resposta.status_code = codigo
    return resposta


def nome_para_exibir(arquivo):
    """Nome do arquivo como o usuário o enviou; só serve para mostrar no relatório."""
    nome = os.path.basename((arquivo.filename or "").replace("\\", "/"))
    nome = re.sub(r"[\x00-\x1f\x7f]", "", nome).strip()
    return nome[:200] or "arquivo.pdf"


def eh_pdf(caminho):
    with open(caminho, "rb") as f:
        return b"%PDF-" in f.read(1024)


@app.after_request
def cabecalhos(resposta):
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("Referrer-Policy", "no-referrer")
    if resposta.mimetype == "text/html" and "Content-Security-Policy" not in resposta.headers:
        resposta.headers["Content-Security-Policy"] = CSP_PAGINA
    return resposta


@app.errorhandler(AppException)
def trata_erro_esperado(e):
    return erro(str(e), e.codigo)


@app.errorhandler(413)
def arquivo_grande(_):
    return erro("Os arquivos passam do tamanho permitido (%d MB por PDF)." % TAMANHO_MAX_MB, 413)


@app.route("/")
def pagina():
    return render_template("index.html", max_mb=TAMANHO_MAX_MB, ignorar=IGNORAR_PAGINAS_PADRAO,
                           ttl_horas=VALIDADE_S // 3600)


@app.route("/saude")
def saude():
    return Response("ok", mimetype="text/plain")


@app.route("/api/jobs", methods=["POST"])
def cria_trabalho():
    base, revisada = request.files.get("base"), request.files.get("revisada")
    if not base or not revisada or not base.filename or not revisada.filename:
        raise AppException("Envie os dois PDFs: a versão base e a versão revisada.")
    try:
        ignorar = int(request.form.get("ignorar_paginas", 0) or 0)
    except ValueError:
        raise AppException("O campo «ignorar páginas» deve ser um número.")
    if not 0 <= ignorar <= 50:
        raise AppException("O campo «ignorar páginas» deve ficar entre 0 e 50.")
    somente = request.form.get("somente_marcados", "").lower() in ("1", "true", "on", "sim")

    reserva_vaga()
    job_id, pasta = novo_trabalho()
    try:
        for campo, arquivo in (("base", base), ("revisada", revisada)):
            destino = pasta / (campo + ".pdf")
            arquivo.save(destino)
            if destino.stat().st_size > TAMANHO_MAX_MB * MB:
                raise AppException("O PDF «%s» passa de %d MB." % (nome_para_exibir(arquivo), TAMANHO_MAX_MB), 413)
            if not eh_pdf(destino):
                raise AppException("O arquivo «%s» não é um PDF." % nome_para_exibir(arquivo))
        inicia_trabalho(job_id, {
            "nome_base": nome_para_exibir(base), "nome_revisada": nome_para_exibir(revisada),
            "opcoes": {"ignorar_paginas": ignorar, "somente_marcados": somente}})
    except Exception:  # envio recusado ou falha inesperada: nada fica no servidor
        libera_vaga()
        remove_trabalho(job_id)
        raise
    resposta = jsonify({"id": job_id, "status_url": "/api/jobs/" + job_id,
                        "relatorio_url": "/jobs/%s/relatorio" % job_id})
    resposta.status_code = 202
    return resposta


@app.route("/api/jobs/<job_id>", methods=["GET"])
def consulta(job_id):
    status = estado(job_id)
    if not status:
        raise AppException("Este trabalho não existe mais: ele expirou ou foi apagado.", 404)
    status["id"] = job_id
    resposta = jsonify(status)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.route("/jobs/<job_id>/relatorio")
def relatorio(job_id):
    status = estado(job_id)
    if not status:
        raise AppException("Este relatório não existe mais: ele expirou ou foi apagado.", 404)
    if status.get("status") != "concluido":
        raise AppException("O relatório ainda não está pronto.", 409)
    resposta = send_file(pasta_do(job_id) / "relatorio.html", mimetype="text/html", conditional=False,
                         as_attachment=request.args.get("baixar") == "1",
                         download_name="relatorio-comparacao.html")
    resposta.headers["Content-Security-Policy"] = CSP_RELATORIO
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def apaga(job_id):
    if not remove_trabalho(job_id):
        raise AppException("Este trabalho não existe mais.", 404)
    return "", 204


# Inicializa o servidor flask
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    inicia(limpeza_periodica=True)

    # Atenção, a opção debug=True permite execução remota de código. Usar com cuidado.
    app.run(debug=False, host=HOST, port=PORTA, threaded=True)
