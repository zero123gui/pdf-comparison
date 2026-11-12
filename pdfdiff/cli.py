"""Compara duas versões de um PDF (base x revisada) e gera um relatório HTML.

Etapas: extrai o texto -> remove cabeçalho/rodapé -> monta parágrafos ->
alinha parágrafos -> diff (parágrafo e palavra) -> verifica contra a cor do texto.

Uso:
    python pdf_diff.py --base documento_base.pdf --revisada documento_revisado.pdf --saida relatorio.html
"""
import argparse
import sys

from .comparador import ErroComparacao, comparar_pdfs


def main():
    """Linha de comando: lê os argumentos, compara os dois PDFs e grava o relatório.

    Mostra no erro padrão o andamento e os títulos achados em cada versão (para conferir com o documento
    real). Em caso de erro esperado, sai com a mensagem para o usuário.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="PDF da versão base")
    ap.add_argument("--revisada", required=True, help="PDF da versão revisada")
    ap.add_argument("--saida", default="relatorio.html")
    ap.add_argument("--ignorar-paginas", type=int, default=0,
                    help="ignora as N primeiras páginas de cada PDF (capa, tabela de revisões)")
    ap.add_argument("--somente-marcados", action="store_true",
                    help="o relatório traz apenas os itens marcados em cor na versão revisada")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    try:
        resultado = comparar_pdfs(
            args.base, args.revisada, args.ignorar_paginas, args.somente_marcados, args.workers,
            progresso=lambda etapa, _fracao: print(etapa + "...", file=sys.stderr))
    except ErroComparacao as erro:
        sys.exit("Erro: %s" % erro)
    with open(args.saida, "w", encoding="utf-8") as f:
        f.write(resultado["html"])
    for rotulo, titulo in resultado["totais"]["titulos"].items():
        print("Título (%s): %s" % (rotulo, titulo or "não encontrado no cabeçalho"), file=sys.stderr)
    print("Pronto: %s (%s)" % (args.saida, {k: v for k, v in resultado["totais"].items()
                                           if k in ("adicionado", "removido", "modificado")}), file=sys.stderr)
