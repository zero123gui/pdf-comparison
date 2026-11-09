"""Aplicação web do comparador de PDFs (Flask)."""
import logging
import multiprocessing
import os
import re

from flask import Flask, Response, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from . import config as configuracao
from .jobs import ID_VALIDO, Jobs, ServidorOcupado

MB = 1024 * 1024
CSP_RELATORIO = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                 "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
CSP_PAGINA = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
              "frame-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


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


def create_app(extra=None):
    cfg = configuracao.carrega(extra)
    app = Flask(__name__)
    app.config.update(cfg)
    app.config["MAX_CONTENT_LENGTH"] = 2 * cfg["MAX_FILE_MB"] * MB + MB
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    jobs = Jobs(cfg)
    # Só o processo do servidor recupera e limpa. Um processo de comparação (filho, iniciado com
    # spawn) que importe este módulo não pode mexer nas pastas dos trabalhos em andamento.
    if multiprocessing.parent_process() is None:
        jobs.recupera()
        jobs.limpa_expirados()
        if not app.config.get("TESTING"):
            jobs.inicia_limpeza_periodica()
    app.jobs = jobs

    @app.after_request
    def cabecalhos(resposta):
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("Referrer-Policy", "no-referrer")
        if resposta.mimetype == "text/html" and "Content-Security-Policy" not in resposta.headers:
            resposta.headers["Content-Security-Policy"] = CSP_PAGINA
        return resposta

    @app.errorhandler(RequestEntityTooLarge)
    def arquivo_grande(_):
        return erro("Os arquivos passam do tamanho permitido (%d MB por PDF)." % cfg["MAX_FILE_MB"], 413)

    @app.get("/")
    def pagina():
        return render_template("index.html", max_mb=cfg["MAX_FILE_MB"], ignorar=cfg["DEFAULT_IGNORAR_PAGINAS"],
                               ttl_horas=cfg["TTL_S"] // 3600)

    @app.get("/saude")
    def saude():
        return Response("ok", mimetype="text/plain")

    @app.post("/api/jobs")
    def cria_trabalho():
        base, revisada = request.files.get("base"), request.files.get("revisada")
        if not base or not revisada or not base.filename or not revisada.filename:
            return erro("Envie os dois PDFs: a versão base e a versão revisada.", 400)
        try:
            ignorar = int(request.form.get("ignorar_paginas", 0) or 0)
        except ValueError:
            return erro("O campo «ignorar páginas» deve ser um número.", 400)
        if not 0 <= ignorar <= 50:
            return erro("O campo «ignorar páginas» deve ficar entre 0 e 50.", 400)
        somente = request.form.get("somente_marcados", "").lower() in ("1", "true", "on", "sim")

        try:
            jobs.reserva_vaga()
        except ServidorOcupado:
            return erro("O servidor está comparando outros documentos agora. Tente de novo em alguns minutos.", 429)
        job_id, pasta = jobs.novo()
        try:
            for campo, arquivo in (("base", base), ("revisada", revisada)):
                destino = pasta / (campo + ".pdf")
                arquivo.save(destino)
                if destino.stat().st_size > cfg["MAX_FILE_MB"] * MB:
                    raise ValueError(("413", "O PDF «%s» passa de %d MB." % (nome_para_exibir(arquivo), cfg["MAX_FILE_MB"])))
                if not eh_pdf(destino):
                    raise ValueError(("400", "O arquivo «%s» não é um PDF." % nome_para_exibir(arquivo)))
            jobs.inicia(job_id, {
                "nome_base": nome_para_exibir(base), "nome_revisada": nome_para_exibir(revisada),
                "opcoes": {"ignorar_paginas": ignorar, "somente_marcados": somente}})
        except ValueError as problema:
            jobs.libera_vaga()
            jobs.remove(job_id)
            codigo, mensagem = problema.args[0]
            return erro(mensagem, int(codigo))
        except Exception:
            jobs.libera_vaga()
            jobs.remove(job_id)
            raise
        resposta = jsonify({"id": job_id, "status_url": "/api/jobs/" + job_id,
                            "relatorio_url": "/jobs/%s/relatorio" % job_id})
        resposta.status_code = 202
        return resposta

    @app.get("/api/jobs/<job_id>")
    def consulta(job_id):
        estado = jobs.estado(job_id)
        if not estado:
            return erro("Este trabalho não existe mais: ele expirou ou foi apagado.", 404)
        estado["id"] = job_id
        resposta = jsonify(estado)
        resposta.headers["Cache-Control"] = "no-store"
        return resposta

    @app.get("/jobs/<job_id>/relatorio")
    def relatorio(job_id):
        estado = jobs.estado(job_id)
        if not estado:
            return erro("Este relatório não existe mais: ele expirou ou foi apagado.", 404)
        if estado.get("status") != "concluido":
            return erro("O relatório ainda não está pronto.", 409)
        resposta = send_file(jobs.pasta_do(job_id) / "relatorio.html", mimetype="text/html", conditional=False,
                             as_attachment=request.args.get("baixar") == "1",
                             download_name="relatorio-comparacao.html")
        resposta.headers["Content-Security-Policy"] = CSP_RELATORIO
        resposta.headers["Cache-Control"] = "no-store"
        return resposta

    @app.delete("/api/jobs/<job_id>")
    def apaga(job_id):
        if not jobs.remove(job_id):
            return erro("Este trabalho não existe mais.", 404)
        return "", 204

    return app
