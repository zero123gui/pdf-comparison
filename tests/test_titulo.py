import tempfile
import unittest
from pathlib import Path

import pdf_diff
from tests.pdfs import pdf_paginas

# Cabeçalho típico de documento do Word: código, título (em duas linhas) e número da página, em células
TITULO = "Especificacao Funcional do Sistema Integrado"
CABECALHO = [(72, 90, ["ITA-001"]), (162, 260, ["Especificacao Funcional do", "Sistema Integrado"]),
             (422, 100, ["Pagina {n} de {total}"])]


def cabecalho_com(titulo):
    return [(72, 90, ["ITA-001"]), (162, 260, [titulo]), (422, 100, ["Pagina {n} de {total}"])]


class TestTituloDoDocumento(unittest.TestCase):
    def compara(self, base, revisada, **opcoes):
        with tempfile.TemporaryDirectory() as tmp:
            caminhos = []
            for nome, conteudo in (("base.pdf", base), ("revisada.pdf", revisada)):
                (Path(tmp) / nome).write_bytes(conteudo)
                caminhos.append(str(Path(tmp) / nome))
            opcoes.setdefault("workers", 1)
            return pdf_diff.comparar_pdfs(*caminhos, nome_base="ITAB.pdf", nome_revisada="ITAC.pdf", **opcoes)

    def test_titulo_vem_do_cabecalho_sem_codigo_nem_numero_da_pagina(self):
        pdf = pdf_paginas(6, CABECALHO)
        resultado = self.compara(pdf, pdf)
        self.assertEqual(resultado["totais"]["titulos"], {"base": TITULO, "revisada": TITULO})
        self.assertEqual(resultado["totais"]["titulo"], TITULO)
        # título igual nas duas versões: aparece uma vez, acima do resumo, e na aba do navegador
        self.assertIn("<p class='doc'>%s</p><h1>" % TITULO, resultado["html"])
        self.assertIn("<title>%s · Comparação de PDFs</title>" % TITULO, resultado["html"])
        self.assertIn("<span class='nome'>ITAB.pdf</span>", resultado["html"])  # o nome do arquivo continua lá

    def test_numero_da_pagina_na_mesma_celula_do_titulo_e_descartado(self):
        for rodape in ("Pagina {n} de {total}", "Pag. {n}/{total}", "{n} de {total}", "Pagina {n}"):
            with self.subTest(rodape=rodape):
                pdf = pdf_paginas(12, [(72, 450, [TITULO + "   " + rodape])])
                self.assertEqual(self.compara(pdf, pdf)["totais"]["titulos"], {"base": TITULO, "revisada": TITULO})

    def test_titulo_nao_depende_de_quantas_paginas_sao_ignoradas(self):
        pdf = pdf_paginas(6, CABECALHO)
        for ignorar in (0, 1, 3):
            with self.subTest(ignorar=ignorar):
                self.assertEqual(self.compara(pdf, pdf, ignorar_paginas=ignorar)["totais"]["titulos"]["base"], TITULO)

    def test_titulos_diferentes_aparecem_os_dois(self):
        a, b = "Especificacao Funcional Versao A", "Especificacao Funcional Versao B"
        resultado = self.compara(pdf_paginas(6, cabecalho_com(a)), pdf_paginas(6, cabecalho_com(b)))
        self.assertEqual(resultado["totais"]["titulos"], {"base": a, "revisada": b})
        self.assertEqual(resultado["totais"]["titulo"], "«%s» → «%s»" % (a, b))
        self.assertIn("<p class='doc'>«%s» → «%s»</p>" % (a, b), resultado["html"])

    def test_titulo_achado_so_numa_versao_diz_qual(self):
        resultado = self.compara(pdf_paginas(6, CABECALHO), pdf_paginas(6))
        self.assertEqual(resultado["totais"]["titulos"], {"base": TITULO, "revisada": None})
        self.assertEqual(resultado["totais"]["titulo"], "«%s» (base)" % TITULO)
        resultado = self.compara(pdf_paginas(6), pdf_paginas(6, CABECALHO))
        self.assertEqual(resultado["totais"]["titulo"], "«%s» (revisada)" % TITULO)

    def test_sem_cabecalho_o_relatorio_continua_como_antes(self):
        pdf = pdf_paginas(6)
        resultado = self.compara(pdf, pdf)
        self.assertEqual(resultado["totais"]["titulos"], {"base": None, "revisada": None})
        self.assertIsNone(resultado["totais"]["titulo"])
        self.assertNotIn("class='doc'", resultado["html"])
        self.assertIn("<title>Comparação de PDFs</title>", resultado["html"])

    def test_texto_que_aparece_em_poucas_paginas_nao_e_titulo(self):
        pdf = pdf_paginas(6, CABECALHO, paginas_com_cabecalho={3})
        self.assertEqual(self.compara(pdf, pdf)["totais"]["titulos"], {"base": None, "revisada": None})

    def test_codigo_ou_sigla_nao_vira_titulo(self):
        pdf = pdf_paginas(6, [(72, 90, ["ITA-001"]), (422, 100, ["Pagina {n} de {total}"])])
        self.assertEqual(self.compara(pdf, pdf)["totais"]["titulos"], {"base": None, "revisada": None})

    def test_titulo_e_escapado_no_html(self):
        malicioso = "<b>Titulo</b> & <script>alert(1)</script>"
        pdf = pdf_paginas(6, [(72, 300, [malicioso]), (372, 100, ["Pagina {n} de {total}"])])
        html = self.compara(pdf, pdf)["html"]
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<b>Titulo</b>", html)
        self.assertIn("&lt;b&gt;Titulo&lt;/b&gt; &amp; &lt;script&gt;alert(1)&lt;/script&gt;", html)


if __name__ == "__main__":
    unittest.main()
