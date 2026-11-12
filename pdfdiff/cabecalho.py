"""Cabeçalho e rodapé: descobre as linhas de margem que se repetem em muitas páginas, para tirá-las da
comparação, e lê delas o título do documento.
"""
import re
from collections import Counter

from .layout import agrupa_blocos, divide_segmentos


MARGEM = 0.15          # fração da página considerada área de cabeçalho/rodapé
REPETICAO_MIN = 0.30   # linha de margem que aparece em >= 30% das páginas é descartada


def _normaliza_linha(txt):
    """Forma de comparar linhas de cabeçalho e rodapé entre páginas.

    Minúsculas, números viram «0» (o número da página muda a cada página) e espaços repetidos se juntam.
    """
    return re.sub(r"\s+", " ", re.sub(r"\d+", "0", txt.lower())).strip()


def remove_repetidas(linhas, n_paginas):
    """Descarta as linhas de margem (topo e rodapé) que se repetem em muitas páginas.

    Uma linha é de margem se está nos primeiros ou nos últimos MARGEM (15%) da altura da página. É repetida se,
    depois de normalizada, aparece em ao menos REPETICAO_MIN (30%) das páginas.

    Parâmetros:
        linhas: linhas das páginas (veja «Linha» em `pdfdiff`).
        n_paginas: quantas páginas o documento tem, para calcular a repetição mínima.

    Devolve:
        (mantidas, repetidas): as linhas que ficam e o conjunto dos padrões normalizados que foram
        descartados (o relatório os lista).
    """
    def na_margem(l):
        """Verdadeiro se a linha está no cabeçalho (topo) ou no rodapé (base) da página."""
        h = l["altura_pag"]
        return l["bottom"] < h * MARGEM or l["top"] > h * (1 - MARGEM)

    cont = Counter()
    for pagina in {l["pagina"] for l in linhas}:
        vistas = {_normaliza_linha(" ".join(w for w, _ in l["palavras"]))
                  for l in linhas if l["pagina"] == pagina and na_margem(l)}
        cont.update(vistas)
    repetidas = {k for k, v in cont.items() if v >= REPETICAO_MIN * n_paginas}
    mantidas = [l for l in linhas
                if not (na_margem(l) and _normaliza_linha(" ".join(w for w, _ in l["palavras"])) in repetidas)]
    return mantidas, repetidas


# «Página 3 de 277», «Pág. 3/277», «3 de 277», «Página 3»: o número muda a cada página do cabeçalho
PAGINACAO = re.compile(r"\b(?:p[aá]g(?:ina)?\.?\s*)?\d+\s*(?:de|/|of)\s*\d+\b|\bp[aá]g(?:ina)?\.?\s*\d+\b", re.I)
TITULO_MIN_LETRAS = 8   # menos que isso é código ou sigla («ITAB»), não título
TITULO_MAX = 200


def titulo_do_cabecalho(linhas, n_paginas):
    """Título do documento, lido do cabeçalho que se repete nas páginas; None se não achar.

    Heurística: dos blocos (células) do topo da página que aparecem em muitas páginas, o título é o de mais
    texto, sem o número da página («Página 3 de 277»). Quem descreve o documento é o texto, não o nome do
    arquivo. Blocos com menos de TITULO_MIN_LETRAS letras (códigos, siglas) não contam.

    Parâmetros:
        linhas: linhas das páginas, ainda com o cabeçalho (veja «Linha» em `pdfdiff`).
        n_paginas: quantas páginas foram lidas, para calcular a repetição mínima.

    Devolve:
        O título (até TITULO_MAX caracteres) ou None.
    """
    por_pagina = {}
    for l in linhas:
        if l["bottom"] < l["altura_pag"] * MARGEM:
            por_pagina.setdefault(l["pagina"], []).append(l)
    paginas_com, textos, posicao = Counter(), {}, {}
    for ls in por_pagina.values():
        vistos = set()
        for b in agrupa_blocos(divide_segmentos(ls), ls[0]["regras"]):
            texto = re.sub(r"\s+", " ", PAGINACAO.sub(" ", " ".join(w for s in b["segs"] for w, _ in s["palavras"])))
            texto = texto.strip(" -–—|·:;,")
            chave = _normaliza_linha(texto)
            if sum(c.isalpha() for c in texto) < TITULO_MIN_LETRAS:
                continue
            vistos.add(chave)
            textos.setdefault(chave, Counter())[texto] += 1
            posicao[chave] = min(posicao.get(chave, (b["top"], b["x0"])), (b["top"], b["x0"]))
        paginas_com.update(vistos)
    candidatos = [k for k, v in paginas_com.items() if v >= REPETICAO_MIN * n_paginas]
    if not candidatos:
        return None
    melhor = max(candidatos, key=lambda k: (sum(c.isalpha() for c in k), tuple(-p for p in posicao[k])))
    return textos[melhor].most_common(1)[0][0][:TITULO_MAX]
