"""Leitura do PDF: transforma cada página em linhas de texto com posição, cor e bordas de tabela (pdfplumber)."""
from concurrent.futures import ProcessPoolExecutor

import pdfplumber


def _colorido(cor):
    """Diz se a cor do texto é uma cor de verdade (nem preto, nem branco, nem cinza).

    O relatório trata o texto colorido como «marcado»: quem revisa o documento pinta de cor o que mudou.

    Parâmetros:
        cor: a cor de preenchimento do texto (`non_stroking_color` do pdfplumber), ou None. Só conta quem tem
            ao menos 3 componentes (RGB) cuja diferença entre o maior e o menor passa de 0,2.
    """
    if not cor or len(cor) < 3:
        return False
    return max(cor[:3]) - min(cor[:3]) > 0.2


def _linhas_pagina(args):
    """Lê um lote de páginas e devolve as linhas de texto de cada uma.

    Roda em cada processo auxiliar de `extrai_linhas`. As palavras cujo `top` difere até 3 pt formam uma
    linha. Em cada linha vão também as bordas de tabela da página (`regras`), tiradas dos retângulos finos que
    o Word desenha: horizontais (até 2,5 pt de altura e ao menos 15 pt de largura) e verticais (até 2,5 pt de
    largura e ao menos 8 pt de altura).

    Parâmetros:
        args: (caminho, indices), com o caminho do PDF e os índices (base 0) das páginas do lote.

    Devolve:
        [(numero_da_pagina, [linha, ...])], com as linhas de cima para baixo (veja «Linha» em `pdfdiff`).
    """
    caminho, indices = args
    saida = []
    with pdfplumber.open(caminho) as pdf:
        for i in indices:
            pag = pdf.pages[i]
            palavras = pag.extract_words(
                extra_attrs=["non_stroking_color"], keep_blank_chars=False, use_text_flow=False
            )
            regras = {"h": [], "v": []}
            for r in pag.rects:
                w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
                if h <= 2.5 and w >= 15:
                    regras["h"].append((r["x0"], r["x1"], (r["top"] + r["bottom"]) / 2))
                elif w <= 2.5 and h >= 8:
                    regras["v"].append(((r["x0"] + r["x1"]) / 2, r["top"], r["bottom"]))
            linhas = []
            for grupo in pdfplumber.utils.cluster_objects(palavras, "top", 3):
                grupo = sorted(grupo, key=lambda w: w["x0"])
                linhas.append({
                    "pagina": i + 1,
                    "top": min(w["top"] for w in grupo),
                    "bottom": max(w["bottom"] for w in grupo),
                    "x0": grupo[0]["x0"],
                    "x1": grupo[-1]["x1"],
                    "altura_pag": pag.height,
                    "largura_pag": pag.width,
                    "palavras": [(w["text"], _colorido(w.get("non_stroking_color"))) for w in grupo],
                    "xs": [(w["x0"], w["x1"]) for w in grupo],
                    "regras": regras,
                })
            saida.append((i + 1, sorted(linhas, key=lambda l: l["top"])))
    return saida


def extrai_linhas(caminho, workers):
    """Lê o PDF inteiro e devolve o número de páginas e as linhas de texto de todas elas.

    As páginas são divididas em lotes (cerca de 4 por processo), que `workers` processos leem em paralelo.

    Parâmetros:
        caminho: caminho do arquivo PDF.
        workers: quantos processos leem páginas ao mesmo tempo.

    Devolve:
        (n_paginas, linhas), com as linhas na ordem das páginas e, em cada página, de cima para baixo.
    """
    with pdfplumber.open(caminho) as pdf:
        n = len(pdf.pages)
    passo = max(1, n // (workers * 4))
    lotes = [(caminho, list(range(i, min(i + passo, n)))) for i in range(0, n, passo)]
    with ProcessPoolExecutor(workers) as ex:
        resultados = [r for lote in ex.map(_linhas_pagina, lotes) for r in lote]
    return n, [l for _, linhas in sorted(resultados) for l in linhas]
