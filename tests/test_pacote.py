"""A estrutura do código: pontos de entrada, módulos e documentação."""
import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

import pdf_diff
import pdfdiff

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVOS_PYTHON = [RAIZ / "servidor.py", RAIZ / "pdf_diff.py", *sorted((RAIZ / "pdfdiff").glob("*.py"))]


def _sem_docstring(caminho):
    """Módulo, classes e funções (inclusive as aninhadas) do arquivo que não têm docstring."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    faltam = [] if ast.get_docstring(arvore) else ["(módulo)"]
    faltam += [no.name for no in ast.walk(arvore)
               if isinstance(no, (ast.FunctionDef, ast.ClassDef)) and not ast.get_docstring(no)]
    return faltam


class TestPontosDeEntrada(unittest.TestCase):
    def test_pdf_diff_continua_sendo_a_linha_de_comando(self):
        """`python pdf_diff.py` e `import pdf_diff` seguem valendo, agora como atalho para o pacote pdfdiff."""
        self.assertIs(pdf_diff.comparar_pdfs, pdfdiff.comparar_pdfs)
        self.assertIs(pdf_diff.ErroComparacao, pdfdiff.ErroComparacao)
        ambiente = dict(os.environ, PYTHONIOENCODING="utf-8")  # no Windows, a saída num pipe sairia em cp1252
        ajuda = subprocess.run([sys.executable, str(RAIZ / "pdf_diff.py"), "--help"], capture_output=True,
                               encoding="utf-8", check=True, env=ambiente).stdout
        for opcao in ("--base", "--revisada", "--saida", "--ignorar-paginas", "--somente-marcados", "--workers"):
            self.assertIn(opcao, ajuda)

    def test_api_publica_do_pacote(self):
        self.assertEqual(sorted(pdfdiff.__all__), ["ErroComparacao", "comparar_pdfs"])


class TestDocumentacao(unittest.TestCase):
    def test_todo_modulo_classe_e_funcao_tem_docstring(self):
        for caminho in ARQUIVOS_PYTHON:
            with self.subTest(arquivo=caminho.name):
                self.assertEqual(_sem_docstring(caminho), [], "sem docstring em %s" % caminho.name)


if __name__ == "__main__":
    unittest.main()
