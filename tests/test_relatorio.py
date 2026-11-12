import re
import unittest
from types import SimpleNamespace

import pdf_diff


def _item(tipo, pag_b, pag_r):
    return {"tipo": tipo, "pag_b": pag_b, "pag_b_fim": pag_b, "pag_r": pag_r, "pag_r_fim": pag_r,
            "html": "texto", "frac": None if tipo == "removido" else 1.0}


class TestLinkParaOsPdfs(unittest.TestCase):
    """O card de cada diferença leva as páginas dos dois PDFs; o JS do relatório monta o link."""

    def setUp(self):
        itens = [_item("adicionado", None, 5), _item("removido", 3, None), _item("modificado", 4, 7)]
        info = {"n_b": 10, "n_r": 10, "removidas": set(), "num_so_base": [], "num_so_rev": []}
        self.html = pdf_diff.gera_html(SimpleNamespace(base="a.pdf", revisada="b.pdf"), itens, [], info)
        self.cards = re.findall(r"<section class='item'([^>]*)>(.*?)</section>", self.html)

    def test_cada_card_leva_so_as_paginas_que_existem(self):
        self.assertEqual(len(self.cards), 3)
        atributos = [a for a, _ in self.cards]
        self.assertIn("data-pr='5'", atributos[0])      # adicionado: só existe na revisada
        self.assertNotIn("data-pb", atributos[0])
        self.assertIn("data-pb='3'", atributos[1])      # removido: só existe na base
        self.assertNotIn("data-pr", atributos[1])
        self.assertIn("data-pb='4'", atributos[2])      # modificado: existe nas duas
        self.assertIn("data-pr='7'", atributos[2])

    def test_link_nasce_escondido_e_sem_endereco(self):
        # sem endereço fixo: o arquivo baixado e o relatório da linha de comando não têm PDFs para abrir
        for _, corpo in self.cards:
            self.assertIn("<a class='ver' target='_blank' rel='noopener' hidden>", corpo)
            self.assertNotIn("href", corpo)

    def test_js_so_mostra_o_link_quando_o_relatorio_vem_do_servidor(self):
        self.assertIn(r"/^\/comparacoes\/([0-9a-f]{32})\/relatorio$/", self.html)
        self.assertIn("'/comparacoes/'+srv[1]+'/pdfs?'", self.html)


if __name__ == "__main__":
    unittest.main()
