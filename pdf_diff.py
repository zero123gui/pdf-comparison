#!/usr/bin/env python3
"""Compara duas versões de um PDF (base x revisada) e gera um relatório HTML.

Este arquivo é só o ponto de entrada da linha de comando. O motor da comparação está no pacote `pdfdiff/`
(veja a documentação em `pdfdiff/__init__.py`).

Uso:
    python pdf_diff.py --base documento_base.pdf --revisada documento_revisado.pdf --saida relatorio.html
"""
from pdfdiff import ErroComparacao, comparar_pdfs  # noqa: F401  (compatibilidade)
from pdfdiff.cli import main


if __name__ == "__main__":
    main()
