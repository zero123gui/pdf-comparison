"""Parágrafos: monta o texto corrido de cada página a partir dos blocos e o prepara para a comparação
(aspas e travessões normalizados, sumário sem número de página).
"""
import re
from collections import Counter

from .layout import agrupa_blocos, divide_segmentos, ordem_leitura


LIDER_SUMARIO = re.compile(r"\.{4,}.*$")
# início de item: marcador, numeração de seção (8.4.1) ou "1." / "1)"; um número solto ("412 MW") não conta
NUMERACAO_PASSO = re.compile(r"\d+(\.\d+){2,}")
INICIO_ITEM = re.compile(r"^(\s*[•\-–▪●○◦]\s|\d+(\.\d+)+[.)]?\s|\d+[.)]\s)")


def _junta(atual, palavras):
    """Acrescenta palavras ao parágrafo, unindo a palavra que foi quebrada com hífen no fim da linha.

    O hífen é mantido: no Word ele é do próprio vocábulo («pré-requisitos»), não de hifenização.

    Parâmetros:
        atual: o parágrafo em montagem (`palavras` é alterada).
        palavras: [(texto, colorido)] a acrescentar.
    """
    pal = list(palavras)
    if atual["palavras"]:
        ant, cor_ant = atual["palavras"][-1]
        if len(ant) > 1 and ant.endswith("-") and ant[-2].isalnum():
            atual["palavras"].pop()
            # o hífen é mantido: no Word ele é do próprio vocábulo ("pré-requisitos"), não de hifenização
            pal[0] = (ant + pal[0][0], cor_ant or pal[0][1])
    atual["palavras"].extend(pal)


def monta_paragrafos(linhas, numeros=None):
    """Agrupa as linhas em parágrafos.

    O texto é lido por blocos (colunas e células), então uma quebra de linha causada por mudança de fonte não
    altera a ordem nem o conteúdo do parágrafo. Cada bloco começa um parágrafo e, dentro dele, um parágrafo novo
    começa quando o espaço vertical passa de 0,6 altura de linha ou o texto abre um item (marcador, «8.4.1»,
    «1.»). O primeiro bloco de uma página continua o parágrafo da página anterior se ele não terminou em
    pontuação e o novo texto começa em minúscula.

    Parâmetros:
        linhas: as linhas das páginas, já sem cabeçalho e rodapé.
        numeros: lista que recebe os números de passo soltos («8.4.1.1»), que ocupam coluna própria e saem do
            fluxo de texto; se None, eles ficam no texto.

    Devolve:
        [{"palavras": [(texto, colorido)], "pagina": n}], na ordem do documento.
    """
    por_pag = {}
    for l in linhas:
        por_pag.setdefault(l["pagina"], []).append(l)

    paragrafos = []
    for pag in sorted(por_pag):
        primeiro = True
        regras = por_pag[pag][0]["regras"]
        segs = divide_segmentos(por_pag[pag])
        if numeros is not None:
            solto = [s for s in segs if len(s["palavras"]) == 1 and NUMERACAO_PASSO.fullmatch(s["palavras"][0][0])]
            numeros += [s["palavras"][0][0] for s in solto]
            segs = [s for s in segs if s not in solto]
        for b in ordem_leitura(agrupa_blocos(segs, regras), regras):
            atual = None
            for k, s in enumerate(b["segs"]):
                texto = " ".join(w for w, _ in s["palavras"])
                if k:
                    ant = b["segs"][k - 1]
                    altura = max(s["bottom"] - s["top"], 1)
                    nova = s["top"] - ant["bottom"] > altura * 0.6 or bool(INICIO_ITEM.match(texto))
                else:
                    nova = True
                    # primeiro bloco da página: pode continuar o parágrafo da página anterior
                    if (primeiro and paragrafos and paragrafos[-1]["pagina"] == pag - 1
                            and not paragrafos[-1]["palavras"][-1][0].endswith((".", ":", ";", "?", "!"))
                            and s["palavras"][0][0][:1].islower()):
                        atual, nova = paragrafos[-1], False
                    primeiro = False
                if nova:
                    atual = {"palavras": [], "pagina": pag}
                    paragrafos.append(atual)
                _junta(atual, s["palavras"])
    return paragrafos


def _norm_token(t):
    """Normaliza uma palavra: espaço sem quebra vira espaço, aspas e apóstrofos tipográficos viram os simples e
    travessões viram hífen.
    """
    t = t.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-")
    return t


def prepara(paragrafos):
    """Prepara os parágrafos para o diff e descarta os que ficam vazios.

    Em cada parágrafo acrescenta `tokens` (palavras normalizadas), `cores` (se cada palavra está em cor),
    `chave` (os tokens juntos, que é o que se compara) e `frac_cor` (a fração das palavras em cor). No sumário
    (páginas em que ao menos 30% dos parágrafos têm líder pontilhado) corta cada entrada no pontilhado e
    descarta o que vem depois, o número de página, que muda de uma versão para outra.

    Parâmetros:
        paragrafos: parágrafos de `monta_paragrafos`; são alterados no lugar.

    Devolve:
        Os parágrafos que têm texto.
    """
    # páginas de sumário: a maioria dos parágrafos tem líder pontilhado
    com_lider, total = Counter(), Counter()
    for p in paragrafos:
        total[p["pagina"]] += 1
        com_lider[p["pagina"]] += any(LIDER_SUMARIO.search(w) for w, _ in p["palavras"])
    pag_sumario = {pg for pg in total if com_lider[pg] >= 0.3 * total[pg]}
    for p in paragrafos:
        pal = []
        for w, c in p["palavras"]:
            # sumário: corta no líder pontilhado (a palavra pode vir colada: "DISTINTOS.....")
            # e descarta o que vem depois (nº de página, que muda de uma versão para outra)
            antes = LIDER_SUMARIO.sub("", w)
            if p["pagina"] in pag_sumario and re.fullmatch(r"\.{2,}", w):
                antes = ""
            if antes != w:
                if antes:
                    pal.append((antes, c))
                break
            pal.append((w, c))
        else:
            # entrada de sumário sem líder extraído: descarta o nº de página no fim
            if p["pagina"] in pag_sumario and len(pal) > 2 and pal[-1][0].isdigit():
                pal.pop()
        p["tokens"] = [_norm_token(w) for w, _ in pal]
        p["cores"] = [c for _, c in pal]
        p["chave"] = " ".join(p["tokens"])
        p["frac_cor"] = sum(p["cores"]) / len(p["cores"]) if p["cores"] else 0.0
    return [p for p in paragrafos if p["chave"].strip()]
