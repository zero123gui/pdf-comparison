"""Layout da página: agrupa as palavras de cada linha em segmentos, os segmentos em blocos (célula de tabela,
coluna, trecho de texto) e define a ordem de leitura. As bordas de tabela desenhadas no PDF decidem onde uma
célula termina; sem elas, só o vão horizontal separa as colunas.
"""


# vão horizontal (pt) que separa colunas sem borda desenhada; as células de tabela são separadas
# pelas bordas verticais, e texto justificado em célula estreita pode ter vãos grandes entre palavras
VAO_COLUNA = 110


def _borda_vertical(l, xa, xb):
    """Há uma borda vertical de tabela entre as posições `xa` e `xb`, na altura da linha `l`?"""
    meio = (l["top"] + l["bottom"]) / 2
    return any(xa - 1 <= x <= xb + 1 and y0 <= meio <= y1 for x, y0, y1 in l["regras"]["v"])


def _borda_horizontal(regras, y0, y1, x0, x1):
    """Há uma borda horizontal de tabela entre `y0` e `y1` sobre o intervalo `x0`..`x1`?"""
    return any(y0 - 1 <= y <= y1 + 1 and rx0 <= x1 and rx1 >= x0 for rx0, rx1, y in regras["h"])


def divide_segmentos(linhas_pag):
    """Divide cada linha em segmentos: corta onde o vão entre duas palavras passa de VAO_COLUNA (colunas sem borda)
    ou onde há uma borda vertical de tabela entre elas (células).

    Parâmetros:
        linhas_pag: as linhas de uma página (veja «Linha» em `pdfdiff`).

    Devolve:
        Lista de segmentos (veja «Segmento» em `pdfdiff`).
    """
    segs = []
    for l in linhas_pag:
        ini = 0
        for k in range(1, len(l["palavras"]) + 1):
            if k == len(l["palavras"]) or l["xs"][k][0] - l["xs"][k - 1][1] > VAO_COLUNA or \
                    _borda_vertical(l, l["xs"][k - 1][1], l["xs"][k][0]):
                segs.append({"top": l["top"], "bottom": l["bottom"],
                             "x0": l["xs"][ini][0], "x1": l["xs"][k - 1][1],
                             "palavras": l["palavras"][ini:k]})
                ini = k
    return segs


def agrupa_blocos(segs, regras):
    """Liga os segmentos empilhados na mesma coluna em blocos (uma célula ou um trecho de texto).

    Um segmento entra no bloco acima dele se começa abaixo da última linha do bloco, a menos de 1,5 altura de
    linha, sem borda horizontal de tabela no meio, e se está alinhado a ele na horizontal (sobreposição, margem
    parecida ou uma continuação que volta ao recuo).

    Parâmetros:
        segs: segmentos de uma página.
        regras: bordas de tabela da página (`regras` da linha).

    Devolve:
        Lista de blocos (veja «Bloco» em `pdfdiff`).
    """
    blocos = []
    for s in sorted(segs, key=lambda s: (s["top"], s["x0"])):
        h = max(s["bottom"] - s["top"], 1)
        melhor, ov_melhor = None, 0
        for b in blocos:
            u = b["segs"][-1]
            dv = s["top"] - u["bottom"]
            # empilhado de verdade: começa abaixo da linha anterior (senão é coluna vizinha)
            if s["top"] - u["top"] <= 0.5 * h or dv > 1.5 * h or dv < -1:
                continue
            if _borda_horizontal(regras, u["bottom"], s["top"], s["x0"], s["x1"]):
                continue
            ov = min(s["x1"], u["x1"]) - max(s["x0"], u["x0"])
            # recuo deslocado: a continuação volta à margem, à esquerda da linha anterior
            if ov > 0 or abs(s["x0"] - u["x0"]) < 15 or (dv <= 0.6 * h and s["x0"] <= u["x0"]):
                if melhor is None or ov > ov_melhor:
                    melhor, ov_melhor = b, ov
        if melhor is None:
            blocos.append({"segs": [s]})
        else:
            melhor["segs"].append(s)
    for b in blocos:
        b["top"] = b["segs"][0]["top"]
        b["bottom"] = max(s["bottom"] for s in b["segs"])
        b["x0"] = min(s["x0"] for s in b["segs"])
        b["x1"] = max(s["x1"] for s in b["segs"])
    return blocos


def _linha_tabela(b, regras):
    """Posição (y) da borda horizontal logo acima do bloco, ou -1 se não há.

    É igual para todas as células da mesma linha de uma tabela, o que permite agrupá-las.
    """
    ys = [y for rx0, rx1, y in regras["h"] if y < b["top"] + 1 and rx0 <= b["x1"] and rx1 >= b["x0"]]
    return round(max(ys)) if ys else -1


def ordem_leitura(blocos, regras):
    """Põe os blocos na ordem de leitura: faixas horizontais de cima para baixo; dentro da faixa, coluna da
    esquerda para a direita.

    Uma faixa reúne blocos que se sobrepõem na vertical e estão na mesma linha de tabela. Acrescenta a chave
    "linha" a cada bloco.

    Parâmetros:
        blocos: blocos de uma página.
        regras: bordas de tabela da página.

    Devolve:
        Os blocos, na ordem em que o texto deve ser lido.
    """
    for b in blocos:
        b["linha"] = _linha_tabela(b, regras)
    ordenados, faixa, base = [], [], None
    for b in sorted(blocos, key=lambda b: (b["top"], b["x0"])):
        if faixa and b["top"] < base - 1 and b["linha"] == faixa[0]["linha"]:
            faixa.append(b)
            base = max(base, b["bottom"])
        else:
            ordenados += _ordena_faixa(faixa)
            faixa, base = [b], b["bottom"]
    return ordenados + _ordena_faixa(faixa)


def _ordena_faixa(faixa):
    """Ordena os blocos de uma faixa.

    Normalmente é coluna por coluna, de cima para baixo. Em uma tabela sem borda entre as linhas (ao menos 2
    linhas com células de colunas diferentes à mesma altura, que juntas reúnem 70% dos segmentos), lê cada
    linha da tabela da esquerda para a direita, para que uma linha movida de página não vire diferença falsa.
    """
    colunas = _colunas(faixa)
    segs = [(s, k) for k, c in enumerate(colunas) for b in c["blocos"] for s in b["segs"]]
    if len(colunas) >= 2:
        # tabela sem borda entre as linhas: células de colunas diferentes na mesma altura
        # (cada linha lida da esquerda para a direita; a linha 5 entra depois da 4 em qualquer página)
        linhas = []
        for s, k in sorted(segs, key=lambda t: t[0]["top"]):
            if linhas and abs(s["top"] - linhas[-1]["top"]) <= 2.5:
                linhas[-1]["itens"].append((s, k))
            else:
                linhas.append({"top": s["top"], "itens": [(s, k)]})
        alinhadas = [l for l in linhas if len({k for _, k in l["itens"]}) >= 2]
        if len(alinhadas) >= 2 and sum(len(l["itens"]) for l in alinhadas) >= 0.7 * len(segs):
            return [{"segs": [s], "top": l["top"], "bottom": s["bottom"], "x0": s["x0"], "x1": s["x1"]}
                    for l in linhas for s, _ in sorted(l["itens"], key=lambda t: t[0]["x0"])]
    return [b for c in colunas for b in sorted(c["blocos"], key=lambda b: b["top"])]


def _colunas(faixa):
    """Agrupa os blocos de uma faixa em colunas: blocos que se sobrepõem na horizontal ficam na mesma coluna."""
    colunas = []
    for b in sorted(faixa, key=lambda b: b["x0"]):
        if colunas and b["x0"] < colunas[-1]["x1"]:
            colunas[-1]["blocos"].append(b)
            colunas[-1]["x1"] = max(colunas[-1]["x1"], b["x1"])
        else:
            colunas.append({"blocos": [b], "x1": b["x1"]})
    return colunas
