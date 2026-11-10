"""Corpo do processo que faz uma comparação. Roda isolado do servidor web (processo próprio)."""
import json
import os
import sys
import traceback
from pathlib import Path

import pdf_diff


def escreve_status(pasta, **campos):
    """Grava status.json de forma atômica, para o servidor nunca ler um arquivo pela metade."""
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


def apaga_entradas(pasta):
    """Os PDFs enviados não ficam guardados: saem antes de o trabalho ser dado como terminado."""
    for nome in ("base.pdf", "revisada.pdf"):
        (Path(pasta) / nome).unlink(missing_ok=True)


def novo_grupo():
    """Faz deste processo líder de um grupo próprio: o servidor encerra o grupo inteiro (filhos
    incluídos) ao cancelar ou estourar o tempo, sem deixar leitores de página órfãos."""
    if hasattr(os, "setsid"):
        os.setsid()


def run_job(pasta, workers):
    novo_grupo()
    pasta = Path(pasta)
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    opcoes = meta["opcoes"]
    escreve_status(pasta, status="executando", etapa="Iniciando", progresso=0.01)
    try:
        resultado = pdf_diff.comparar_pdfs(
            str(pasta / "base.pdf"), str(pasta / "revisada.pdf"),
            ignorar_paginas=opcoes["ignorar_paginas"], somente_marcados=opcoes["somente_marcados"],
            workers=workers,
            progresso=lambda etapa, fracao: escreve_status(pasta, etapa=etapa, progresso=round(fracao, 3)),
            nome_base=meta["nome_base"], nome_revisada=meta["nome_revisada"])
        apaga_entradas(pasta)
        tmp = pasta / "relatorio.html.tmp"
        tmp.write_text(resultado["html"], encoding="utf-8")
        os.replace(tmp, pasta / "relatorio.html")
        escreve_status(pasta, status="concluido", etapa="Concluído", progresso=1.0, totais=resultado["totais"])
    except pdf_diff.ErroComparacao as erro:
        apaga_entradas(pasta)
        if erro.__cause__ is not None:  # causa técnica só no log do servidor, nunca na resposta
            traceback.print_exception(erro.__cause__, file=sys.stderr)
        escreve_status(pasta, status="erro", etapa="Erro", erro=str(erro))
    except Exception:  # nunca devolve o conteúdo do PDF; o detalhe técnico fica só no log do servidor
        traceback.print_exc(file=sys.stderr)
        apaga_entradas(pasta)
        escreve_status(pasta, status="erro", etapa="Erro",
                       erro="Erro inesperado ao comparar os arquivos. Tente novamente; se repetir, avise o responsável pelo sistema.")
