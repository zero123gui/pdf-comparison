import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from pdfdiff.relatorio import gera_html
from pdfdiff.relatorio_assets import CSS


def _item(tipo, pag_b, pag_r):
    return {"tipo": tipo, "pag_b": pag_b, "pag_b_fim": pag_b, "pag_r": pag_r, "pag_r_fim": pag_r,
            "html": "texto", "frac": None if tipo == "removido" else 1.0}


class TestLinkParaOsPdfs(unittest.TestCase):
    """O card de cada diferença leva as páginas dos dois PDFs; o JS do relatório monta o link."""

    def setUp(self):
        itens = [_item("adicionado", None, 5), _item("removido", 3, None), _item("modificado", 4, 7)]
        info = {"n_b": 10, "n_r": 10, "removidas": set(), "num_so_base": [], "num_so_rev": []}
        self.html = gera_html(SimpleNamespace(base="a.pdf", revisada="b.pdf"), itens, [], info)
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


def _tokens_de_cor(css):
    """(claro, escuro): token de cor -> #rrggbb, do :root e do @media (prefers-color-scheme: dark)."""
    def le(trecho):
        return {nome: ("#" + "".join(c * 2 for c in h) if len(h) == 3 else "#" + h).lower()
                for nome, h in re.findall(r"(--[\w-]+)\s*:\s*#([0-9a-fA-F]{3,6})\b", trecho)}
    claro = le(css.split("@media")[0])
    escuro = le(re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}", css, re.S).group(1))
    return claro, escuro


def _sem_bloco_media(css, consulta):
    """`css` sem o bloco @media cuja consulta contém `consulta` (conta as chaves até fechá-lo)."""
    ini = css.index(consulta)
    ini = css.rindex("@media", 0, ini)
    fundo, fim = 0, css.index("{", ini)
    for fim in range(fim, len(css)):
        fundo += {"{": 1, "}": -1}.get(css[fim], 0)
        if fundo == 0:
            break
    return css[:ini] + css[fim + 1:]


class TestRelatorioNovo(unittest.TestCase):
    """O relatório é um arquivo só (baixado ou gerado pela linha de comando): sem recurso de fora e com o
    mesmo sistema de design da página."""

    def setUp(self):
        itens = [_item("adicionado", None, 5), _item("removido", 3, None), _item("modificado", 4, 7)]
        itens[2]["frac"] = 0.0           # modificado sem cor
        itens[1]["frac"] = None
        itens.append(dict(_item("modificado", 8, 8), frac=0.2))  # cor parcial
        self.info = {"n_b": 10, "n_r": 10, "removidas": {"pagina 0 de 0"}, "num_so_base": ["8.4.1"], "num_so_rev": []}
        self.html = gera_html(SimpleNamespace(base="a.pdf", revisada="b.pdf"), itens, [], self.info)

    def test_nada_vem_de_fora(self):
        # o script e o estilo são inline; fora deles, nenhum atributo carrega arquivo, imagem ou ícone por endereço
        marcacao = re.sub(r"<(script|style)>.*?</\1>", "", self.html, flags=re.S)
        for trecho in (" src=", " href=", "<use", "<img", "<link", "<iframe"):
            self.assertNotIn(trecho, marcacao.replace("<a class='ver' target='_blank' rel='noopener' hidden>", ""))
        for trecho in ("url(", "@import", "http://", "https://"):
            self.assertNotIn(trecho, self.html)

    def test_so_a_excecao_de_marcacao_e_sinalizada(self):
        cards = dict(zip(("adicionado", "removido", "sem", "parcial"),
                         [c for _, c in re.findall(r"<section class='item'([^>]*)>(.*?)</section>", self.html)]))
        self.assertNotIn("marcado em cor", cards["adicionado"])   # o esperado não ganha selo
        self.assertNotIn("flag", cards["removido"])
        self.assertIn("<span class='flag f-sem'>Sem cor</span>", cards["sem"])
        self.assertIn("<span class='flag'>Cor parcial</span>", cards["parcial"])

    def test_filtros_levam_a_contagem_e_o_aviso_de_lista_vazia(self):
        self.assertIn("data-ft='todos' aria-pressed='true'>Todos <span class='num'>4</span>", self.html)
        self.assertIn("data-ft='modificado' aria-pressed='false'>Modificados <span class='num'>2</span>", self.html)
        self.assertIn("data-fm='sem' aria-pressed='false'>Sem cor <span class='num'>1</span>", self.html)
        self.assertIn("<span id='n' class='num'></span> de <span class='num'>4</span> itens", self.html)
        self.assertIn("<p class='vazio' id='vazio' hidden>Nenhum item com estes filtros.", self.html)

    def test_sem_diferencas_diz_que_o_texto_e_o_mesmo(self):
        html = gera_html(SimpleNamespace(base="a.pdf", revisada="b.pdf"), [], [], self.info)
        self.assertIn("<h1>Nenhuma diferença entre as duas versões</h1>", html)
        self.assertIn("O texto das duas versões é o mesmo.", html)

    def test_monoespacada_so_para_numeracao_e_linhas_de_cabecalho(self):
        self.assertIn("<dd class='mono'>8.4.1</dd>", self.html)
        self.assertIn("<ul class='mono'><li>pagina 0 de 0</li></ul>", self.html)
        self.assertEqual(self.html.count("class='mono'"), 3)  # as duas linhas da numeração e a lista de cabeçalhos

    def test_movimento_so_com_a_preferencia_por_movimento(self):
        """Animação e transição ficam dentro de @media (prefers-reduced-motion: no-preference), na página e no relatório."""
        pagina = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(encoding="utf-8")
        for nome, css in (("style.css", pagina), ("relatório", CSS)):
            with self.subTest(css=nome):
                self.assertRegex(css, r"(animation|transition)\s*:")  # o teste não pode passar à toa
                fora = _sem_bloco_media(css, "prefers-reduced-motion:no-preference" if nome == "relatório"
                                        else "prefers-reduced-motion: no-preference")
                self.assertNotRegex(fora, r"(animation|transition)\s*:")

    def test_tokens_de_cor_iguais_aos_da_pagina(self):
        """Dois arquivos de CSS (a página usa style.css, o relatório é autônomo): as cores não podem divergir."""
        pagina = Path(__file__).resolve().parent.parent / "static" / "style.css"
        pagina_claro, pagina_escuro = _tokens_de_cor(pagina.read_text(encoding="utf-8"))
        relatorio_claro, relatorio_escuro = _tokens_de_cor(CSS)
        self.assertTrue(relatorio_claro and relatorio_escuro)
        for nome, valor in relatorio_claro.items():
            self.assertEqual(pagina_claro.get(nome), valor, "claro " + nome)
        for nome, valor in relatorio_escuro.items():
            self.assertEqual(pagina_escuro.get(nome, pagina_claro.get(nome)), valor, "escuro " + nome)


if __name__ == "__main__":
    unittest.main()
