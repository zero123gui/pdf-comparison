"""Servidor web do comparador de PDFs.

Recebe a versão base e a revisada de um documento, compara em segundo plano (pacote `pdfdiff`) e mostra o
relatório no navegador.

Para rodar:  python servidor.py   (abre em http://localhost:8080)
Pacotes externos: só Flask e pdfplumber (pip install -r requirements.txt).

O arquivo é um só, no estilo do mock_server.py, dividido em seções, de cima para baixo:

    configuração            constantes, que cada servidor muda por variável de ambiente (tabela no README)
    pastas e estado         uma pasta por comparação em PASTA_DADOS/comparacoes/<id>/, com meta.json (nomes e
                            opções), status.json (andamento), base.pdf, revisada.pdf e relatorio.html
    processo da comparação  cada comparação roda num processo `spawn` próprio, em grupo de processos próprio
    fila e supervisão       uma thread por comparação espera vaga, vigia o tempo limite e a encerra se preciso
    rotas                   a API e as páginas (Flask)

A fila e os processos existem só na memória deste processo: rode uma única instância do servidor. Um processo de
comparação (filho, iniciado com spawn) reimporta este arquivo, então nada aqui pode ter efeito colateral ao ser
importado: `inicia()` só roda no processo principal e o servidor só sobe sob `if __name__ == "__main__"`.
"""
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

from flask import Flask, Response, jsonify, render_template, request, send_file, url_for

import pdfdiff

app = Flask(__name__)


def _inteiro(nome, padrao):
    """Lê uma variável de ambiente que deve ser um número inteiro.

    Parâmetros:
        nome: nome da variável de ambiente.
        padrao: valor usado quando a variável não está definida.

    Levanta:
        SystemExit: se o valor não for um inteiro. O servidor não deve subir com configuração inválida.
    """
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

MB = 1024 * 1024                                                  # bytes em um megabyte
ID_VALIDO = re.compile(r"[0-9a-f]{32}")                           # formato do id (uuid4 em hexadecimal); barra ../ e afins
TERMINAIS = ("concluido", "erro")                                 # status finais: a comparação não muda mais
VERSOES_PDF = {"base": "base.pdf", "revisada": "revisada.pdf"}   # versão (como aparece na URL) -> arquivo
PAGINA_PDF_MAX = 99999                                            # maior página aceita em ?b= e ?r=

# Políticas de segurança de conteúdo (CSP). O relatório é um HTML autônomo, com estilo e script embutidos, que
# não carrega nada de fora; a página do sistema só carrega arquivos próprios (static/), sem estilo nem script inline.
CSP_RELATORIO = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                 "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
CSP_PAGINA = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
              "frame-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

log = logging.getLogger("pdfdiff")

# Estado da fila, só neste processo (o estado de cada comparação fica em arquivos: status.json)
_contexto = multiprocessing.get_context("spawn")
_trava = threading.Lock()
_semaforo = threading.BoundedSemaphore(MAX_SIMULTANEAS)
_ativos = 0          # na fila + executando
_processos = {}      # id da comparação -> processo que a executa
_canceladas = set()  # ids apagados pelo usuário com o processo ainda rodando


class AppException(Exception):
    """Erro esperado, com mensagem para o usuário e o código HTTP da resposta.

    É convertido em resposta JSON por `trata_erro_esperado`. A mensagem nunca leva o conteúdo dos PDFs.
    """

    def __init__(self, mensagem, codigo=400):
        """Parâmetros:
            mensagem: o texto que o usuário vê.
            codigo: o status HTTP da resposta (400 por padrão).
        """
        super().__init__(mensagem)
        self.codigo = codigo


# ------------------------------------------------------------------ pastas e estado em arquivos
def pasta_do(id_comparacao):
    """Pasta da comparação; None se o id não tem o formato esperado (impede ../ e afins).

    É a única porta de entrada para montar um caminho a partir de um id que veio da URL.

    Parâmetros:
        id_comparacao: o id, como veio da URL.

    Devolve:
        O caminho PASTA_DADOS/comparacoes/<id>, ou None se o id é inválido (a pasta pode não existir).
    """
    if not ID_VALIDO.fullmatch(id_comparacao or ""):
        return None
    return PASTA_DADOS / "comparacoes" / id_comparacao


def nova_comparacao():
    """Cria a pasta de uma comparação nova; a vaga na fila já deve estar reservada (`reserva_vaga`).

    Devolve:
        (id_comparacao, pasta). O id é um uuid4 em hexadecimal: 128 bits aleatórios, que ninguém adivinha.
    """
    id_comparacao = uuid.uuid4().hex
    pasta = PASTA_DADOS / "comparacoes" / id_comparacao
    pasta.mkdir()
    return id_comparacao, pasta


def escreve_status(pasta, **campos):
    """Atualiza o status.json da comparação de forma atômica, para ninguém ler um arquivo pela metade.

    Mescla `campos` ao conteúdo atual e grava num arquivo temporário que depois substitui o status.json. Não faz
    nada se a pasta já foi apagada (a comparação foi removida enquanto rodava). É chamada pelo processo da
    comparação (andamento e resultado) e pela supervisão (erros).

    Parâmetros:
        pasta: pasta da comparação.
        **campos: o que gravar, por exemplo status="executando", etapa="Lendo a versão base", progresso=0.4.
    """
    pasta = Path(pasta)
    if not pasta.exists():  # a comparação foi apagada enquanto rodava
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


def estado(id_comparacao):
    """Estado de uma comparação, como a API o devolve.

    É o conteúdo do status.json mais `nomes` (os nomes dos arquivos, de meta.json) e `expira_em` (quando a
    comparação será apagada).

    Parâmetros:
        id_comparacao: o id, como veio da URL.

    Devolve:
        O dicionário de estado; {} se o id é inválido, a comparação não existe ou o status é ilegível.
    """
    pasta = pasta_do(id_comparacao)
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
    """Apaga os PDFs enviados de uma comparação.

    Numa comparação concluída os PDFs ficam (os links «Ver nos PDFs» do relatório os abrem) e saem junto com a
    pasta, ao expirar ou ao apagar. Só uma comparação que deu erro os apaga na hora, porque não há relatório que
    precise deles.
    """
    for nome in VERSOES_PDF.values():
        (Path(pasta) / nome).unlink(missing_ok=True)


# ------------------------------------------------------------------ processo da comparação
def novo_grupo():
    """Faz deste processo o líder de um grupo de processos próprio.

    Assim o servidor encerra o grupo inteiro (os leitores de página incluídos) ao cancelar a comparação ou
    estourar o tempo, sem deixar processos órfãos gastando CPU. Não faz nada em sistemas sem `setsid` (Windows).
    """
    if hasattr(os, "setsid"):
        os.setsid()


def executa_comparacao(pasta, leitores):
    """Corpo do processo que faz UMA comparação; roda isolado do servidor web (processo `spawn`).

    Lê o meta.json, chama `pdfdiff.comparar_pdfs` gravando o andamento no status.json e termina de um de dois
    jeitos: grava o relatorio.html e o status «concluido» (os PDFs ficam), ou grava o status «erro» e apaga os
    PDFs. A mensagem de `ErroComparacao` é para o usuário; a causa técnica vai só para o log (stderr), nunca para
    a resposta.

    Parâmetros:
        pasta: pasta da comparação.
        leitores: processos de leitura de páginas (`PDFDIFF_WORKERS`).
    """
    novo_grupo()
    pasta = Path(pasta)
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    opcoes = meta["opcoes"]
    escreve_status(pasta, status="executando", etapa="Iniciando", progresso=0.01)
    try:
        resultado = pdfdiff.comparar_pdfs(
            str(pasta / "base.pdf"), str(pasta / "revisada.pdf"),
            ignorar_paginas=opcoes["ignorar_paginas"], somente_marcados=opcoes["somente_marcados"],
            workers=leitores,
            progresso=lambda etapa, fracao: escreve_status(pasta, etapa=etapa, progresso=round(fracao, 3)),
            nome_base=meta["nome_base"], nome_revisada=meta["nome_revisada"])
        tmp = pasta / "relatorio.html.tmp"
        tmp.write_text(resultado["html"], encoding="utf-8")
        os.replace(tmp, pasta / "relatorio.html")
        escreve_status(pasta, status="concluido", etapa="Concluído", progresso=1.0, totais=resultado["totais"])
    except pdfdiff.ErroComparacao as erro_leitura:
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
    """Encerra o processo da comparação e todos os filhos dele.

    Manda SIGTERM ao grupo de processos e, se o processo seguir vivo depois de `espera` segundos, SIGKILL.

    Parâmetros:
        proc: o processo da comparação (multiprocessing.Process).
        espera: segundos a esperar o processo morrer, a cada sinal.
    """
    def sinal(numero):
        """Manda o sinal ao grupo do processo; se o grupo ainda não existe ou o sistema não tem grupos, só ao processo."""
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
    """Reserva um lugar na fila (comparações esperando mais as que executam).

    Levanta:
        AppException: com código 429 se os MAX_SIMULTANEAS + MAX_FILA lugares já estão ocupados.
    """
    global _ativos
    with _trava:
        if _ativos >= MAX_SIMULTANEAS + MAX_FILA:
            raise AppException("O servidor está comparando outros documentos agora. "
                               "Tente de novo em alguns minutos.", 429)
        _ativos += 1


def libera_vaga():
    """Devolve o lugar reservado por `reserva_vaga`."""
    global _ativos
    with _trava:
        _ativos = max(0, _ativos - 1)


def inicia_comparacao(id_comparacao, meta):
    """Registra a comparação como «na fila» e a entrega a uma thread que espera a vez de rodar.

    Parâmetros:
        id_comparacao: o id da comparação, que já tem pasta e PDFs gravados.
        meta: o que vai para o meta.json: `nome_base`, `nome_revisada` e `opcoes`
            (`ignorar_paginas`, `somente_marcados`).
    """
    pasta = pasta_do(id_comparacao)
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    escreve_status(pasta, status="na_fila", etapa="Na fila", progresso=0.0, criado_em=time.time())
    threading.Thread(target=supervisiona, args=(id_comparacao,), name="supervisor-" + id_comparacao[:6], daemon=True).start()


def supervisiona(id_comparacao):
    """Espera uma vaga, roda a comparação num processo próprio e vigia o tempo limite.

    Roda numa thread por comparação. Se o processo passa de TEMPO_MAX_S, ela o encerra e grava o erro; se o
    processo morre sem gravar um status final (por exemplo, falta de memória), grava um erro. Ao terminar,
    sempre devolve a vaga e, se a comparação não chegou a «concluido», apaga os PDFs enviados. Se o usuário
    apagou a comparação enquanto rodava (`_canceladas`), não grava nada na pasta que está sendo removida.

    Parâmetros:
        id_comparacao: o id da comparação.
    """
    pasta = pasta_do(id_comparacao)
    try:
        with _semaforo:
            if not pasta.exists():  # apagado enquanto esperava na fila
                return
            inicio = time.time()
            proc = _contexto.Process(target=executa_comparacao, args=(str(pasta), LEITORES),
                                     name="comparacao-" + id_comparacao[:6])
            proc.start()
            _processos[id_comparacao] = proc
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
                _processos.pop(id_comparacao, None)
            if id_comparacao in _canceladas:  # o usuário apagou: nada a registrar nem a escrever
                return
            final = estado(id_comparacao)
            # {} = a pasta já foi apagada (ou está sendo): não há nada a registrar. Só um status que
            # existe e não é final significa que o processo morreu sem terminar.
            if final and final.get("status") not in TERMINAIS:
                escreve_status(pasta, status="erro", etapa="Erro", erro=(
                    "O processamento terminou de forma inesperada (talvez faltou memória). "
                    "Tente novamente com arquivos menores."))
            log.info("comparação %s terminou: %s em %.1fs", id_comparacao[:8], estado(id_comparacao).get("status"),
                     time.time() - inicio)
    except Exception:
        log.exception("falha ao supervisionar a comparação %s", id_comparacao[:8])
        escreve_status(pasta, status="erro", etapa="Erro", erro="Erro interno do servidor.")
    finally:
        _canceladas.discard(id_comparacao)
        if estado(id_comparacao).get("status") != "concluido":
            apaga_entradas(pasta)  # garantia extra: sem relatório pronto, os PDFs enviados não ficam guardados
        libera_vaga()


def remove_comparacao(id_comparacao):
    """Apaga a comparação e os arquivos dela; se estiver rodando, ela é interrompida.

    Parâmetros:
        id_comparacao: o id, como veio da URL.

    Devolve:
        True se a comparação existia, False se o id é inválido ou ela já não existe.
    """
    pasta = pasta_do(id_comparacao)
    if pasta is None or not pasta.exists():
        return False
    proc = _processos.get(id_comparacao)
    if proc is not None and proc.is_alive():
        # Marca antes de matar: sem isso, a thread de supervisão veria o processo morto e a
        # pasta sem status.json e gravaria um erro novo dentro da pasta que está sendo apagada.
        _canceladas.add(id_comparacao)
        encerra(proc)
    for _ in range(3):  # tenta de novo se alguma escrita tardia recriou algo
        shutil.rmtree(pasta, ignore_errors=True)
        if not pasta.exists():
            break
    return True


def limpa_expirados():
    """Apaga as comparações mais velhas que VALIDADE_S, com os PDFs e o relatório, menos as que ainda rodam.

    Uma pasta sem status (envio interrompido) vale pela data da pasta.

    Devolve:
        Quantas comparações foram removidas.
    """
    agora, removidos = time.time(), 0
    for pasta in (PASTA_DADOS / "comparacoes").iterdir():
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
        log.info("removidas %d comparações expiradas", removidos)
    return removidos


def recupera():
    """Ao iniciar: as comparações que estavam rodando morreram junto com o servidor anterior.

    Marca cada uma como «erro», com a mensagem para enviar os arquivos de novo, e apaga os PDFs.
    """
    for pasta in (PASTA_DADOS / "comparacoes").iterdir():
        if pasta.is_dir() and ID_VALIDO.fullmatch(pasta.name):
            if estado(pasta.name).get("status") in ("na_fila", "executando"):
                escreve_status(pasta, status="erro", etapa="Erro", erro=(
                    "O servidor foi reiniciado durante a comparação. Envie os arquivos novamente."))
                apaga_entradas(pasta)


def inicia(limpeza_periodica=False):
    """Prepara o servidor para atender: pasta de dados, fila vazia, comparações interrompidas e expiradas.

    Só o processo principal faz isto: um processo de comparação (filho, iniciado com spawn) que importe este
    arquivo não pode mexer nas pastas das comparações.

    Parâmetros:
        limpeza_periodica: se verdadeiro, uma thread chama `limpa_expirados` a cada 10 minutos.
    """
    global _semaforo, _ativos
    if multiprocessing.parent_process() is not None:
        return
    app.config["MAX_CONTENT_LENGTH"] = 2 * TAMANHO_MAX_MB * MB + MB
    (PASTA_DADOS / "comparacoes").mkdir(parents=True, exist_ok=True)
    _semaforo = threading.BoundedSemaphore(MAX_SIMULTANEAS)
    _ativos = 0
    recupera()
    limpa_expirados()
    if limpeza_periodica:
        def ciclo():
            """Chama `limpa_expirados` a cada 10 minutos, para sempre; um erro na limpeza vai para o log e não derruba a thread."""
            while True:
                time.sleep(600)
                try:
                    limpa_expirados()
                except Exception:
                    log.exception("falha na limpeza de comparações expiradas")
        threading.Thread(target=ciclo, name="limpeza", daemon=True).start()


# ------------------------------------------------------------------ rotas
# Funções de apoio das rotas, depois os tratadores de resposta e, por fim, as rotas
def erro(mensagem, codigo):
    """Resposta JSON {"erro": mensagem} com o código HTTP."""
    resposta = jsonify({"erro": mensagem})
    resposta.status_code = codigo
    return resposta


def nome_para_exibir(arquivo):
    """Nome do arquivo como o usuário o enviou; só serve para mostrar no relatório.

    Tira o caminho e os caracteres de controle e limita a 200 caracteres; sem nome, devolve «arquivo.pdf».
    """
    nome = os.path.basename((arquivo.filename or "").replace("\\", "/"))
    nome = re.sub(r"[\x00-\x1f\x7f]", "", nome).strip()
    return nome[:200] or "arquivo.pdf"


def eh_pdf(caminho):
    """O arquivo parece um PDF? Procura o marcador `%PDF-` nos primeiros 1024 bytes; não valida a estrutura."""
    with open(caminho, "rb") as f:
        return b"%PDF-" in f.read(1024)


def pagina_do_pdf(valor):
    """Página pedida em ?b= ou ?r=; None se faltar ou não for um número de 1 a PAGINA_PDF_MAX.

    O valor nunca é repetido na resposta: só o número que passou na validação chega à página.
    """
    try:
        pagina = int(valor)
    except (TypeError, ValueError):
        return None
    return pagina if 1 <= pagina <= PAGINA_PDF_MAX else None


def pdf_guardado(id_comparacao, versao):
    """Caminho do PDF guardado, ou None se ele não existe.

    Não existe quando a comparação é de antes de os PDFs serem guardados, deu erro, expirou ou foi apagada.

    Parâmetros:
        id_comparacao: o id, como veio da URL.
        versao: «base» ou «revisada»; qualquer outro valor devolve None.
    """
    pasta, nome = pasta_do(id_comparacao), VERSOES_PDF.get(versao)
    if pasta is None or nome is None:
        return None
    return pasta / nome if (pasta / nome).is_file() else None


@app.after_request
def cabecalhos(resposta):
    """Acrescenta a toda resposta os cabeçalhos de segurança.

    Põe `X-Content-Type-Options: nosniff` e `Referrer-Policy: no-referrer` e, nas respostas HTML que ainda não
    têm CSP (o relatório define a sua), a CSP da página (`CSP_PAGINA`).
    """
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("Referrer-Policy", "no-referrer")
    if resposta.mimetype == "text/html" and "Content-Security-Policy" not in resposta.headers:
        resposta.headers["Content-Security-Policy"] = CSP_PAGINA
    return resposta


@app.errorhandler(AppException)
def trata_erro_esperado(e):
    """Converte um `AppException` na resposta JSON dele, com o código HTTP dele."""
    return erro(str(e), e.codigo)


@app.errorhandler(413)
def arquivo_grande(_):
    """Resposta ao 413 do Flask: o envio passou de MAX_CONTENT_LENGTH (dois PDFs do tamanho máximo e uma folga)."""
    return erro("Os arquivos passam do tamanho permitido (%d MB por PDF)." % TAMANHO_MAX_MB, 413)


# Página inicial: o formulário para enviar os dois PDFs, a tela de andamento e o relatório
@app.route("/")
def pagina_inicial():
    """GET /: a página do sistema (formulário de envio, tela de andamento e o relatório num iframe)."""
    return render_template("index.html", max_mb=TAMANHO_MAX_MB, ignorar=IGNORAR_PAGINAS_PADRAO,
                           ttl_horas=VALIDADE_S // 3600)


# Verificação de saúde: responde "ok" quando o servidor está no ar (para monitoramento)
@app.route("/saude")
def saude():
    """GET /saude: responde «ok» quando o servidor está no ar, para o monitoramento."""
    return Response("ok", mimetype="text/plain")


# Inicia uma comparação: recebe os PDFs "base" e "revisada" (e as opções) por POST, confere os
# arquivos e responde 202 com o id da comparação, que continua rodando em segundo plano
@app.route("/api/comparacoes", methods=["POST"])
def cria_comparacao():
    """POST /api/comparacoes: recebe os dois PDFs e inicia a comparação em segundo plano.

    Campos do formulário: `base` e `revisada` (os PDFs), `ignorar_paginas` (0 a 50) e `somente_marcados`.
    Confere os campos, reserva a vaga, grava os PDFs e entrega a comparação à fila. Se qualquer passo falha,
    nada fica no servidor.

    Devolve:
        202 com {"id", "status_url", "relatorio_url"}.

    Levanta:
        AppException: 400 (campos faltando, `ignorar_paginas` inválido, arquivo que não é PDF), 413 (PDF
        acima do tamanho máximo) ou 429 (a fila está cheia).
    """
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
    id_comparacao, pasta = nova_comparacao()
    try:
        for campo, arquivo in (("base", base), ("revisada", revisada)):
            destino = pasta / (campo + ".pdf")
            arquivo.save(destino)
            if destino.stat().st_size > TAMANHO_MAX_MB * MB:
                raise AppException("O PDF «%s» passa de %d MB." % (nome_para_exibir(arquivo), TAMANHO_MAX_MB), 413)
            if not eh_pdf(destino):
                raise AppException("O arquivo «%s» não é um PDF." % nome_para_exibir(arquivo))
        inicia_comparacao(id_comparacao, {
            "nome_base": nome_para_exibir(base), "nome_revisada": nome_para_exibir(revisada),
            "opcoes": {"ignorar_paginas": ignorar, "somente_marcados": somente}})
    except Exception:  # envio recusado ou falha inesperada: nada fica no servidor
        libera_vaga()
        remove_comparacao(id_comparacao)
        raise
    resposta = jsonify({"id": id_comparacao, "status_url": "/api/comparacoes/%s/andamento" % id_comparacao,
                        "relatorio_url": "/comparacoes/%s/relatorio" % id_comparacao})
    resposta.status_code = 202
    return resposta


# Andamento de uma comparação: devolve o status (na_fila, executando, concluido ou erro), a etapa
# atual e o progresso; a página consulta este endpoint a cada 1,5 segundo
@app.route("/api/comparacoes/<id_comparacao>/andamento", methods=["GET"])
def consulta_andamento(id_comparacao):
    """GET /api/comparacoes/<id>/andamento: o estado da comparação, que a página consulta a cada 1,5 segundo.

    Devolve:
        O JSON de `estado` mais o `id`: `status` («na_fila», «executando», «concluido» ou «erro»), `etapa`,
        `progresso`, `nomes`, `expira_em` e, quando concluída, `totais`.

    Levanta:
        AppException: 404 se a comparação não existe mais.
    """
    status = estado(id_comparacao)
    if not status:
        raise AppException("Esta comparação não existe mais: ela expirou ou foi apagada.", 404)
    status["id"] = id_comparacao
    resposta = jsonify(status)
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


# Relatório da comparação concluída, em HTML (com ?baixar=1 vem como arquivo para download)
@app.route("/comparacoes/<id_comparacao>/relatorio")
def mostra_relatorio(id_comparacao):
    """GET /comparacoes/<id>/relatorio: o relatório da comparação concluída, em HTML.

    Com `?baixar=1` a resposta vem como arquivo para baixar. Leva a CSP do relatório (nada de fora).

    Levanta:
        AppException: 404 se a comparação não existe mais; 409 se ainda não terminou.
    """
    status = estado(id_comparacao)
    if not status:
        raise AppException("Este relatório não existe mais: ele expirou ou foi apagado.", 404)
    if status.get("status") != "concluido":
        raise AppException("O relatório ainda não está pronto.", 409)
    resposta = send_file(pasta_do(id_comparacao) / "relatorio.html", mimetype="text/html", conditional=False,
                         as_attachment=request.args.get("baixar") == "1",
                         download_name="relatorio-comparacao.html")
    resposta.headers["Content-Security-Policy"] = CSP_RELATORIO
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


# Um dos dois PDFs enviados ("base" ou "revisada"), aberto no próprio navegador; só de comparação concluída
@app.route("/comparacoes/<id_comparacao>/pdf/<versao>")
def mostra_pdf(id_comparacao, versao):
    """GET /comparacoes/<id>/pdf/<versao>: um dos PDFs enviados, aberto no próprio navegador.

    Parâmetros:
        id_comparacao: o id da comparação.
        versao: «base» ou «revisada».

    Levanta:
        AppException: 404 se a comparação ou o PDF não existem; 409 se a comparação ainda não terminou.
    """
    status = estado(id_comparacao)
    if not status:
        raise AppException("Esta comparação não existe mais: ela expirou ou foi apagada.", 404)
    if status.get("status") != "concluido":
        raise AppException("A comparação ainda não terminou.", 409)
    caminho = pdf_guardado(id_comparacao, versao)
    if caminho is None:
        raise AppException("Este PDF não está disponível.", 404)
    resposta = send_file(caminho, mimetype="application/pdf", as_attachment=False, conditional=True,
                         download_name=status.get("nomes", {}).get(versao) or VERSOES_PDF[versao])
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


# Página com os dois PDFs lado a lado, cada um já na página da diferença: ?b= é a página na versão
# base e ?r= na revisada. A versão sem página não tem o trecho (item adicionado ou removido) e fica
# sem painel; sem nenhuma das duas, abre os dois PDFs no início
@app.route("/comparacoes/<id_comparacao>/pdfs")
def mostra_pdfs(id_comparacao):
    """GET /comparacoes/<id>/pdfs?b=N&r=M: os dois PDFs lado a lado, cada um na página da diferença.

    `b` é a página na versão base e `r` na revisada. Valor ausente ou inválido é ignorado e nunca aparece na
    resposta. A versão sem página não tem o trecho (item adicionado ou removido) e fica sem painel; sem nenhuma
    das duas, os dois PDFs abrem no início. Se os PDFs não estão mais no servidor, a página explica.

    Devolve:
        A página `pdfs.html`: 200, ou 404 e 409 com uma mensagem no lugar dos painéis.
    """
    status = estado(id_comparacao)
    if not status:
        return render_template("pdfs.html", mensagem="Esta comparação não existe mais: ela expirou ou foi apagada."), 404
    if status.get("status") != "concluido":
        return render_template("pdfs.html", mensagem="A comparação ainda não terminou."), 409

    paginas = {"base": pagina_do_pdf(request.args.get("b")), "revisada": pagina_do_pdf(request.args.get("r"))}
    abre_no_inicio = all(pagina is None for pagina in paginas.values())
    paineis, sem_o_trecho = [], []
    for versao, rotulo in (("base", "Base"), ("revisada", "Revisada")):
        pagina = paginas[versao]
        if pagina is None and not abre_no_inicio:
            sem_o_trecho.append(versao)
            continue
        if pdf_guardado(id_comparacao, versao) is None:
            return render_template("pdfs.html", mensagem=(
                "Os PDFs desta comparação não estão disponíveis. O relatório continua valendo; "
                "para ver os PDFs, faça a comparação de novo.")), 404
        url = url_for("mostra_pdf", id_comparacao=id_comparacao, versao=versao)
        paineis.append({"rotulo": rotulo, "nome": status.get("nomes", {}).get(versao) or VERSOES_PDF[versao],
                        "pagina": pagina, "url": url, "src": url + ("#page=%d" % pagina if pagina else "")})
    return render_template("pdfs.html", paineis=paineis, sem_o_trecho=sem_o_trecho)


# Apaga a comparação e os arquivos dela agora; se ainda estiver rodando, ela é cancelada
@app.route("/api/comparacoes/<id_comparacao>", methods=["DELETE"])
def apaga_comparacao(id_comparacao):
    """DELETE /api/comparacoes/<id>: apaga a comparação e os arquivos dela agora; se estiver rodando, cancela.

    Devolve:
        204 sem corpo.

    Levanta:
        AppException: 404 se a comparação não existe.
    """
    if not remove_comparacao(id_comparacao):
        raise AppException("Esta comparação não existe mais.", 404)
    return "", 204


# Inicializa o servidor flask
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    inicia(limpeza_periodica=True)

    # Atenção, a opção debug=True permite execução remota de código. Usar com cuidado.
    app.run(debug=False, host=HOST, port=PORTA, threaded=True)
