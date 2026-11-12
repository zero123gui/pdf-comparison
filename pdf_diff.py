#!/usr/bin/env python3
"""Compara duas versões de um PDF (base x revisada) e gera um relatório HTML.

Etapas: extrai o texto -> remove cabeçalho/rodapé -> monta parágrafos ->
alinha parágrafos -> diff (parágrafo e palavra) -> verifica contra a cor do texto.

Uso:
    python pdf_diff.py --base documento_base.pdf --revisada documento_revisado.pdf --saida relatorio.html
"""
import argparse
import html
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher
from types import SimpleNamespace

import pdfplumber

MARGEM = 0.15          # fração da página considerada área de cabeçalho/rodapé
REPETICAO_MIN = 0.30   # linha de margem que aparece em >= 30% das páginas é descartada
MARCADO = 0.5          # fração de palavras alteradas coloridas para contar como "marcado"
SO_NUMERACAO = re.compile(r"^\d+(\.\d+)+\.?$")
LIDER_SUMARIO = re.compile(r"\.{4,}.*$")
# início de item: marcador, numeração de seção (8.4.1) ou "1." / "1)"; um número solto ("412 MW") não conta
NUMERACAO_PASSO = re.compile(r"\d+(\.\d+){2,}")
INICIO_ITEM = re.compile(r"^(\s*[•\-–▪●○◦]\s|\d+(\.\d+)+[.)]?\s|\d+[.)]\s)")


# --------------------------------------------------------------------- extração
def _colorido(cor):
    """Texto com cor (não preto/branco/cinza) é considerado marcado."""
    if not cor or len(cor) < 3:
        return False
    return max(cor[:3]) - min(cor[:3]) > 0.2


def _normaliza_linha(txt):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "0", txt.lower())).strip()


def _linhas_pagina(args):
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
    with pdfplumber.open(caminho) as pdf:
        n = len(pdf.pages)
    passo = max(1, n // (workers * 4))
    lotes = [(caminho, list(range(i, min(i + passo, n)))) for i in range(0, n, passo)]
    with ProcessPoolExecutor(workers) as ex:
        resultados = [r for lote in ex.map(_linhas_pagina, lotes) for r in lote]
    return n, [l for _, linhas in sorted(resultados) for l in linhas]


# ----------------------------------------------------- cabeçalho/rodapé/metadados
def remove_repetidas(linhas, n_paginas):
    """Descarta linhas de margem (topo/rodapé) repetidas em muitas páginas."""
    def na_margem(l):
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

    Heurística: dos blocos (células) do topo da página que aparecem em muitas páginas, o título é
    o de mais texto, sem o número da página. Quem descreve o documento é o texto, não o nome do arquivo."""
    por_pagina = {}
    for l in linhas:
        if l["bottom"] < l["altura_pag"] * MARGEM:
            por_pagina.setdefault(l["pagina"], []).append(l)
    paginas_com, textos, posicao = Counter(), {}, {}
    for ls in por_pagina.values():
        vistos = set()
        for b in _blocos(_segmentos(ls), ls[0]["regras"]):
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


def rotulo_do_titulo(titulo_b, titulo_r):
    """O que identifica o documento no relatório e na tela: o título, se é o mesmo nas duas versões;
    se diferem, os dois; se só um foi achado, ele com a versão. None quando nenhum foi achado."""
    if titulo_b and titulo_b == titulo_r:
        return titulo_b
    if titulo_b and titulo_r:
        return "«%s» → «%s»" % (titulo_b, titulo_r)
    if titulo_b or titulo_r:
        return "«%s» (%s)" % (titulo_b or titulo_r, "base" if titulo_b else "revisada")
    return None


# ------------------------------------------------------------------- parágrafos
# vão horizontal (pt) que separa colunas sem borda desenhada; as células de tabela são separadas
# pelas bordas verticais, e texto justificado em célula estreita pode ter vãos grandes entre palavras
VAO_COLUNA = 110


def _borda_vertical(l, xa, xb):
    meio = (l["top"] + l["bottom"]) / 2
    return any(xa - 1 <= x <= xb + 1 and y0 <= meio <= y1 for x, y0, y1 in l["regras"]["v"])


def _borda_horizontal(regras, y0, y1, x0, x1):
    """Há uma borda horizontal de tabela entre y0 e y1 sobre o intervalo x0..x1?"""
    return any(y0 - 1 <= y <= y1 + 1 and rx0 <= x1 and rx1 >= x0 for rx0, rx1, y in regras["h"])


def _segmentos(linhas_pag):
    """Divide cada linha em segmentos separados por vãos largos (células de tabela, colunas)."""
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


def _blocos(segs, regras):
    """Liga segmentos empilhados na mesma coluna em blocos (uma célula / um trecho de texto)."""
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
    """Posição (y) da borda horizontal logo acima do bloco: igual para todas as células da mesma linha."""
    ys = [y for rx0, rx1, y in regras["h"] if y < b["top"] + 1 and rx0 <= b["x1"] and rx1 >= b["x0"]]
    return round(max(ys)) if ys else -1


def _ordem_leitura(blocos, regras):
    """Faixas horizontais de cima para baixo; dentro da faixa, coluna da esquerda para a direita."""
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
    colunas = []
    for b in sorted(faixa, key=lambda b: b["x0"]):
        if colunas and b["x0"] < colunas[-1]["x1"]:
            colunas[-1]["blocos"].append(b)
            colunas[-1]["x1"] = max(colunas[-1]["x1"], b["x1"])
        else:
            colunas.append({"blocos": [b], "x1": b["x1"]})
    return colunas


def _junta(atual, palavras):
    """Acrescenta palavras ao parágrafo, unindo palavra quebrada com hífen no fim da linha."""
    pal = list(palavras)
    if atual["palavras"]:
        ant, cor_ant = atual["palavras"][-1]
        if len(ant) > 1 and ant.endswith("-") and ant[-2].isalnum():
            atual["palavras"].pop()
            # o hífen é mantido: no Word ele é do próprio vocábulo ("pré-requisitos"), não de hifenização
            pal[0] = (ant + pal[0][0], cor_ant or pal[0][1])
    atual["palavras"].extend(pal)


def monta_paragrafos(linhas, numeros=None):
    """Agrupa linhas em parágrafos: [{'palavras': [(txt, colorido)], 'pagina': n}].

    O texto é lido por blocos (colunas/células), então uma quebra de linha causada por
    mudança de fonte não altera a ordem nem o conteúdo do parágrafo.
    """
    por_pag = {}
    for l in linhas:
        por_pag.setdefault(l["pagina"], []).append(l)

    paragrafos = []
    for pag in sorted(por_pag):
        primeiro = True
        regras = por_pag[pag][0]["regras"]
        segs = _segmentos(por_pag[pag])
        if numeros is not None:
            solto = [s for s in segs if len(s["palavras"]) == 1 and NUMERACAO_PASSO.fullmatch(s["palavras"][0][0])]
            numeros += [s["palavras"][0][0] for s in solto]
            segs = [s for s in segs if s not in solto]
        for b in _ordem_leitura(_blocos(segs, regras), regras):
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
    t = t.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-")
    return t


def prepara(paragrafos):
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


# ---------------------------------------------------------------------- alinhar
CONTEXTO = 10  # palavras de contexto exibidas em volta de cada mudança


def _achata(paragrafos):
    """Concatena os parágrafos num fluxo único de palavras (independe de onde o texto quebra)."""
    tokens, cores, paginas, inicio = [], [], [], []
    for p in paragrafos:
        for k, (t, c) in enumerate(zip(p["tokens"], p["cores"])):
            tokens.append(t); cores.append(c); paginas.append(p["pagina"]); inicio.append(k == 0)
    return tokens, cores, paginas, inicio


def _texto(tokens, inicio, ini, fim):
    """Junta palavras escapadas; parágrafo novo dentro do trecho vira quebra de linha."""
    out = []
    for k in range(ini, fim):
        if inicio[k] and k > ini:
            out.append("<br>")
        out.append(html.escape(tokens[k]))
    return " ".join(out).replace(" <br> ", "<br>")


def _sem_espaco(tokens):
    return "".join("".join(tokens).split())


def _ignora_espacamento(ops, bt, rt):
    """Mudanças que só diferem em espaços ("d uração" x "duração", "- >" x "->") viram 'equal'."""
    saida, k = [], 0
    while k < len(ops):
        if ops[k][0] == "equal":
            saida.append(ops[k]); k += 1
            continue
        # junta mudanças separadas por até 3 palavras iguais
        fim = k
        while fim + 2 < len(ops) and ops[fim + 1][0] == "equal" and ops[fim + 1][2] - ops[fim + 1][1] <= 3 \
                and ops[fim + 2][0] != "equal":
            fim += 2
        i1, i2, j1, j2 = ops[k][1], ops[fim][2], ops[k][3], ops[fim][4]
        if _sem_espaco(bt[i1:i2]) == _sem_espaco(rt[j1:j2]):
            saida.append(("equal", i1, i2, j1, j2))
        else:
            saida.extend(ops[k:fim + 1])
        k = fim + 1
    # funde 'equal' vizinhos para o agrupamento por contexto
    fund = []
    for op in saida:
        if fund and fund[-1][0] == "equal" and op[0] == "equal":
            fund[-1] = ("equal", fund[-1][1], op[2], fund[-1][3], op[4])
        else:
            fund.append(op)
    return fund


def _itens_do_bloco(base, rev):
    """Diff palavra a palavra de um trecho que difere; devolve um item por grupo de mudanças."""
    bt, bc, bp, bi = _achata(base)
    rt, rc, rp, ri = _achata(rev)
    ops = _ignora_espacamento(SequenceMatcher(None, bt, rt, autojunk=False).get_opcodes(), bt, rt)
    if all(op[0] == "equal" for op in ops):
        return []  # só mudou a quebra de linha/parágrafo, o conteúdo é o mesmo

    # agrupa mudanças separadas por poucas palavras iguais
    grupos, atual = [], None
    for k, op in enumerate(ops):
        if op[0] == "equal":
            if atual is not None and op[2] - op[1] > 2 * CONTEXTO:
                grupos.append(atual); atual = None
            elif atual is not None:
                atual.append(k)
        else:
            if atual is None:
                atual = []
            atual.append(k)
    if atual:
        grupos.append(atual)

    itens = []
    for g in grupos:
        g = [k for k in g]
        while ops[g[-1]][0] == "equal":
            g.pop()
        partes, ins_cor, ins_tot = [], 0, 0
        tem_ins = tem_del = False
        pags_b, pags_r = [], []
        k0 = g[0]
        if k0 > 0 and ops[k0 - 1][0] == "equal":
            _, a, b, c, d = ops[k0 - 1]
            ini = max(c, d - CONTEXTO)
            partes.append(("… " if ini > c or k0 - 1 > 0 else "") + _texto(rt, ri, ini, d))
        for k in g:
            op, i1, i2, j1, j2 = ops[k]
            if op == "equal":
                partes.append(_texto(rt, ri, j1, j2))
                continue
            if i2 > i1:
                tem_del = True
                pags_b += bp[i1:i2]
                partes.append("<del>%s</del>" % _texto(bt, bi, i1, i2))
            if j2 > j1:
                tem_ins = True
                pags_r += rp[j1:j2]
                partes.append("<ins>%s</ins>" % _texto(rt, ri, j1, j2))
                ins_tot += j2 - j1
                ins_cor += sum(rc[j1:j2])
        kf = g[-1] + 1
        if kf < len(ops) and ops[kf][0] == "equal":
            _, a, b, c, d = ops[kf]
            fim = min(d, c + CONTEXTO)
            partes.append(_texto(rt, ri, c, fim) + (" …" if fim < d or kf < len(ops) - 1 else ""))
        # página de referência da outra versão (para itens de um lado só)
        ref = ops[g[0]]
        if not pags_b and bp:
            pags_b = [bp[min(ref[1], len(bp) - 1)]]
        if not pags_r and rp:
            pags_r = [rp[min(ref[3], len(rp) - 1)]]
        pag_b, pag_b_fim = (min(pags_b), max(pags_b)) if pags_b else (None, None)
        pag_r, pag_r_fim = (min(pags_r), max(pags_r)) if pags_r else (None, None)
        tipo = "modificado" if tem_ins and tem_del else ("adicionado" if tem_ins else "removido")
        itens.append({"tipo": tipo,
                      "pag_b": None if tipo == "adicionado" else pag_b,
                      "pag_b_fim": pag_b_fim,
                      "pag_r": None if tipo == "removido" else pag_r,
                      "pag_r_fim": pag_r_fim,
                      "html": " ".join(partes),
                      "frac": (ins_cor / ins_tot) if ins_tot else None})
    return itens


def compara(base, rev):
    sm = SequenceMatcher(None, [p["chave"] for p in base], [p["chave"] for p in rev], autojunk=False)
    itens, colorido_igual = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            colorido_igual += [rev[j] for j in range(j1, j2) if rev[j]["frac_cor"] >= MARCADO]
        else:
            itens += _itens_do_bloco(base[i1:i2], rev[j1:j2])
    return itens, colorido_igual


# ------------------------------------------------------------------------ HTML
def marcacao(item):
    f = item["frac"]
    if item["tipo"] == "removido":
        return "n/a"
    if f is None:
        return "n/a"
    if f >= MARCADO:
        return "marcado"
    return "parcial" if f > 0 else "sem"


CSS = """
:root{color-scheme:light dark;
--fundo:#f5f5f8;--superficie:#fff;--superficie-2:#ececf2;--linha:#dcdde6;--linha-forte:#737689;--texto:#15161d;--texto-2:#454859;
--acento:#4338ca;--acento-hover:#362ba3;--acento-suave:#e8e7fc;--sobre-acento:#fff;
--ok-fundo:#d6f3df;--ok-texto:#0a5427;--erro-fundo:#fde2e2;--erro-texto:#8c1a1a;--aviso-fundo:#fcebc2;--aviso-texto:#6a4000;
--esp-1:.25rem;--esp-2:.5rem;--esp-3:.75rem;--esp-4:1rem;--esp-5:1.5rem;--esp-6:2rem;--esp-7:3rem;--esp-8:4rem;
--raio-1:6px;--raio-2:8px;--raio-3:12px;--raio-4:16px;
--fonte:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
--fonte-mono:ui-monospace,"SF Mono","Cascadia Mono",Consolas,Menlo,monospace;
--t-pequeno:.875rem;--t-base:1rem;--t-medio:1.125rem;--t-grande:1.75rem;--altura-linha:1.55;
--mov-1:120ms;--mov-2:220ms;--curva:cubic-bezier(.2,.8,.2,1)}
@media(prefers-color-scheme:dark){:root{
--fundo:#0f1015;--superficie:#17181f;--superficie-2:#20222c;--linha:#2c2e3a;--linha-forte:#868a9e;--texto:#ededf2;--texto-2:#b9bccb;
--acento:#a3a8ff;--acento-hover:#bcc0ff;--acento-suave:#24264a;--sobre-acento:#0f1015;
--ok-fundo:#123d27;--ok-texto:#a7efc1;--erro-fundo:#4a1d22;--erro-texto:#ffc4c9;--aviso-fundo:#46330a;--aviso-texto:#ffdf9a}}
*,*::before,*::after{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--fundo);color:var(--texto);font:var(--t-base)/var(--altura-linha) var(--fonte);-webkit-text-size-adjust:100%}
main{max-width:54rem;margin:0 auto;padding:var(--esp-6) var(--esp-4) var(--esp-8)}
h1,h2{margin:0;font-weight:700;line-height:1.15;letter-spacing:-.015em;text-wrap:balance}
h1{font-size:var(--t-grande)}h2{margin:var(--esp-7) 0 var(--esp-2);font-size:var(--t-medio)}
p{margin:0;text-wrap:pretty}i,em,cite{font-style:normal}
.num{font-variant-numeric:tabular-nums}.mono{font-family:var(--fonte-mono);font-size:.9em}
:focus-visible{outline:3px solid var(--acento);outline-offset:2px}
.ic{flex:none;width:1.125rem;height:1.125rem;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}
.nota{margin-top:var(--esp-3);color:var(--texto-2);font-size:var(--t-pequeno)}
.doc{margin-bottom:var(--esp-1);color:var(--texto-2);font-weight:600;overflow-wrap:anywhere;text-wrap:balance}
.arqs{display:flex;flex-wrap:wrap;align-items:center;gap:var(--esp-2) var(--esp-3);margin-top:var(--esp-3);color:var(--texto-2)}
.arq{display:inline-flex;flex-wrap:wrap;align-items:center;gap:var(--esp-1) var(--esp-2)}
.nome{color:var(--texto);font-weight:600;overflow-wrap:anywhere}
.filtros{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;align-items:center;gap:var(--esp-2) var(--esp-3);margin-top:var(--esp-5);padding:var(--esp-3) 0;background:var(--fundo);border-bottom:1px solid var(--linha)}
.seg{--recuo:4px;display:inline-flex;flex-wrap:wrap;gap:2px;padding:var(--recuo);background:var(--superficie-2);border-radius:var(--raio-3)}
.seg button{display:inline-flex;align-items:center;gap:var(--esp-2);min-height:2.5rem;padding:0 var(--esp-3);font:inherit;font-size:var(--t-pequeno);font-weight:600;color:var(--texto-2);background:transparent;border:0;border-radius:calc(var(--raio-3) - var(--recuo));cursor:pointer}
.seg button:hover{background:var(--superficie)}
.seg button.on{color:var(--sobre-acento);background:var(--acento)}
.seg .num{font-weight:500}
.seg .alerta .num{padding:0 var(--esp-2);color:var(--aviso-texto);background:var(--aviso-fundo);border-radius:var(--raio-1)}
.total{margin-left:auto;color:var(--texto-2);font-size:var(--t-pequeno)}
.item{margin-top:var(--esp-4);overflow:hidden;background:var(--superficie);border:1px solid var(--linha);border-radius:var(--raio-3)}
.item header{display:flex;flex-wrap:wrap;align-items:center;gap:var(--esp-1) var(--esp-4);padding:var(--esp-3) var(--esp-4);color:var(--texto-2);font-size:var(--t-pequeno);border-bottom:1px solid var(--linha)}
.n{font-weight:600;font-variant-numeric:tabular-nums}
.tipo{display:inline-flex;align-items:center;gap:var(--esp-1);font-weight:600}
.t-adicionado{color:var(--ok-texto)}.t-removido{color:var(--erro-texto)}.t-modificado{color:var(--acento)}
.pags{font-variant-numeric:tabular-nums}
.flag{font-weight:600}.f-sem{padding:0 var(--esp-2);color:var(--aviso-texto);background:var(--aviso-fundo);border-radius:var(--raio-1)}
.item p{padding:var(--esp-4);font-size:var(--t-medio);line-height:1.65;overflow-wrap:anywhere}
ins{padding:0 .2em;color:var(--ok-texto);background:var(--ok-fundo);text-decoration:none;border-radius:3px}
del{padding:0 .2em;color:var(--erro-texto);background:var(--erro-fundo);text-decoration:line-through;text-decoration-thickness:.08em;border-radius:3px}
.ver{display:inline-flex;align-items:center;gap:var(--esp-1);min-height:2.25rem;margin-left:auto;padding:0 var(--esp-3) 0 var(--esp-2);color:var(--acento);font-weight:600;text-decoration:none;background:var(--superficie);border:1px solid var(--linha);border-radius:var(--raio-2)}
.ver:hover{background:var(--acento-suave)}
.vazio{margin-top:var(--esp-5);color:var(--texto-2)}
details{margin-top:var(--esp-3)}summary{color:var(--acento);font-weight:600;cursor:pointer}
details p,dl{margin-top:var(--esp-2);color:var(--texto-2)}dt{margin-top:var(--esp-3);color:var(--texto);font-weight:600}dd{margin:0}
ul.mono{padding-left:var(--esp-5)}
@keyframes entra{from{opacity:0;transform:translateY(6px)}}
@media(prefers-reduced-motion:no-preference){.item.novo{animation:entra var(--mov-2) var(--curva)}.seg button,.ver{transition:background-color var(--mov-1)}}
@media(max-width:640px){.arqs{flex-direction:column;align-items:flex-start;gap:var(--esp-1)}.arqs>.ic{display:none}.filtros{position:static}.seg{width:100%}.seg button{flex:1 1 auto;justify-content:center}.total{margin-left:0}}
"""

JS = """
const itens=[...document.querySelectorAll('.item')];let ft='todos',fm='marcado',ini=true;
function aplica(){let n=0;itens.forEach(e=>{const ok=(ft==='todos'||e.dataset.tipo===ft)&&(fm==='todas'||e.dataset.marc===fm);const antes=e.hidden;e.hidden=!ok;
if(ok){n++;if(!ini&&antes)e.classList.add('novo')}else e.classList.remove('novo')});
document.getElementById('n').textContent=n;document.getElementById('vazio').hidden=n>0;ini=false}
function liga(attr,set){const bs=[...document.querySelectorAll('['+attr+']')];
bs.forEach(b=>b.onclick=()=>{set(b.getAttribute(attr));bs.forEach(x=>{x.classList.toggle('on',x===b);x.setAttribute('aria-pressed',x===b)});aplica()})}
liga('data-ft',v=>ft=v);liga('data-fm',v=>fm=v);aplica();
const srv=location.pathname.match(/^\\/comparacoes\\/([0-9a-f]{32})\\/relatorio$/);
if(srv)itens.forEach(e=>{const q=[];if(e.dataset.pb)q.push('b='+e.dataset.pb);if(e.dataset.pr)q.push('r='+e.dataset.pr);
const a=e.querySelector('.ver');if(a&&q.length){a.href='/comparacoes/'+srv[1]+'/pdfs?'+q.join('&');a.hidden=false}});
"""


def _chave_num(s):
    return [int(x) for x in s.split(".")]


def _icone(corpo):
    # SVG embutido (o relatório não pode carregar nada de fora): um só conjunto, traço de 1,75 e pontas redondas
    return "<svg class='ic' viewBox='0 0 24 24' aria-hidden='true'>%s</svg>" % corpo


ICONES = {
    "arquivo": _icone("<path d='M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z'/><path d='M14 3v5h5'/>"),
    "seta": _icone("<path d='M5 12h14'/><path d='m13 6 6 6-6 6'/>"),
    "adicionado": _icone("<path d='M12 5v14M5 12h14'/>"),
    "removido": _icone("<path d='M5 12h14'/>"),
    "modificado": _icone("<path d='M4 20l1-4L16 5a2.1 2.1 0 0 1 3 3L8 19z'/><path d='m14 7 3 3'/>"),
    "lado_a_lado": _icone("<rect x='3' y='4' width='18' height='16' rx='2'/><path d='M12 4v16'/>"),
}
ROTULO_TIPO = {"adicionado": "Adicionado", "removido": "Removido", "modificado": "Modificado"}


def _plural(n, singular, varios):
    return "%d %s" % (n, singular if n == 1 else varios)


def _grupo(atributo, opcoes, rotulo):
    """Grupo de botões (um filtro): opcoes = [(valor, texto, quantidade, ligado, alerta)]."""
    botoes = "".join(
        "<button%s data-%s='%s' aria-pressed='%s'>%s <span class='num'>%d</span></button>" % (
            " class='%s'" % " ".join(c for c, ativo in (("on", ligado), ("alerta", alerta)) if ativo)
            if ligado or alerta else "",
            atributo, valor, "true" if ligado else "false", texto, n)
        for valor, texto, n, ligado, alerta in opcoes)
    return "<div class='seg' role='group' aria-label='%s'>%s</div>" % (rotulo, botoes)


def gera_html(args, itens, colorido_igual, info):
    c = Counter(i["tipo"] for i in itens)
    sem = sum(1 for i in itens if i["tipo"] != "removido" and marcacao(i) == "sem")
    parcial = sum(1 for i in itens if marcacao(i) == "parcial")
    marcados = sum(1 for i in itens if marcacao(i) == "marcado")
    titulo = ("%s entre as duas versões" % _plural(len(itens), "diferença", "diferenças")) if itens \
        else "Nenhuma diferença entre as duas versões"
    rotulo = rotulo_do_titulo(info.get("titulo_b"), info.get("titulo_r"))
    h = ["<!doctype html><html lang='pt-BR'><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<meta name='color-scheme' content='light dark'>",
         "<title>%s</title><style>%s</style><main>" % (
             html.escape("%s · Comparação de PDFs" % rotulo if rotulo else "Comparação de PDFs"), CSS),
         # o nome do arquivo diz pouco; o título do documento está dentro dele (cabeçalho das páginas)
         "<p class='doc'>%s</p>" % html.escape(rotulo) if rotulo else "",
         "<h1>%s</h1>" % titulo,
         "<p class='arqs'><span class='arq'>%s<span>Base</span><span class='nome'>%s</span><span class='num'>%d págs.</span></span>"
         "%s<span class='arq'>%s<span>Revisada</span><span class='nome'>%s</span><span class='num'>%d págs.</span></span></p>" % (
             ICONES["arquivo"], html.escape(args.base), info["n_b"], ICONES["seta"],
             ICONES["arquivo"], html.escape(args.revisada), info["n_r"])]
    if colorido_igual:
        h.append("<p class='nota'>%s coloridos na revisada são idênticos à base: marcação herdada de revisões anteriores.</p>"
                 % _plural(len(colorido_igual), "trecho", "trechos"))
    h.append("<div class='filtros'>")
    h.append(_grupo("ft", [("todos", "Todos", len(itens), True, False),
                           ("adicionado", "Adicionados", c["adicionado"], False, False),
                           ("removido", "Removidos", c["removido"], False, False),
                           ("modificado", "Modificados", c["modificado"], False, False)], "Tipo de diferença"))
    h.append(_grupo("fm", [("marcado", "Marcado em cor", marcados, True, False),
                           ("parcial", "Cor parcial", parcial, False, False),
                           ("sem", "Sem cor", sem, False, sem > 0),
                           ("todas", "Qualquer marcação", len(itens), False, False)], "Marcação em cor"))
    h.append("<p class='total'><span id='n' class='num'></span> de <span class='num'>%d</span> itens</p></div>" % len(itens))
    for k, it in enumerate(itens, 1):
        m = marcacao(it)
        def faixa(ini, fim):
            return "%d" % ini if not fim or fim == ini else "%d–%d" % (ini, fim)
        pags = []
        if it["pag_b"]:
            pags.append("Base p. " + faixa(it["pag_b"], it.get("pag_b_fim")))
        if it["pag_r"]:
            pags.append("Revisada p. " + faixa(it["pag_r"], it.get("pag_r_fim")))
        # só a exceção é sinalizada: «marcado em cor» é o esperado e já está no filtro
        sinal = {"sem": "<span class='flag f-sem'>Sem cor</span>", "parcial": "<span class='flag'>Cor parcial</span>",
                 "marcado": "", "n/a": ""}[m]
        # primeira página de cada lado; quando o relatório é aberto pelo servidor, o JS usa estes
        # números para ligar o link «Ver nos PDFs» (o arquivo baixado ou da linha de comando não tem PDFs)
        paginas = "".join(" data-%s='%d'" % (chave, it[campo])
                          for chave, campo in (("pb", "pag_b"), ("pr", "pag_r")) if it[campo])
        h.append("<section class='item' data-tipo='%s' data-marc='%s'%s><header><span class='n'>#%d</span>"
                 "<span class='tipo t-%s'>%s%s</span><span class='pags'>%s</span>%s"
                 "<a class='ver' target='_blank' rel='noopener' hidden>%sVer nos PDFs</a></header><p>%s</p></section>" % (
                     it["tipo"], m, paginas, k, it["tipo"], ICONES[it["tipo"]], ROTULO_TIPO[it["tipo"]],
                     " · ".join(pags), sinal, ICONES["lado_a_lado"], it["html"]))
    h.append("<p class='vazio' id='vazio' hidden>%s</p>" % (
        "Nenhum item com estes filtros. Use «Qualquer marcação» para ver todos." if itens
        else "O texto das duas versões é o mesmo."))
    h.append("<details><summary>Como ler este relatório</summary>"
             "<p>Por padrão aparecem só os itens marcados em cor na versão revisada. «Qualquer marcação» mostra também "
             "os sem cor e as remoções (texto removido não pode estar colorido).</p>"
             "<dl><dt>Sem cor <span class='num'>%d</span></dt>"
             "<dd>O texto mudou entre as versões, mas não está marcado na revisada: vale conferir.</dd>"
             "<dt>Coloridos sem diferença <span class='num'>%d</span></dt>"
             "<dd>Texto marcado em cor que é idêntico à base (marcação herdada de revisões anteriores).</dd></dl></details>" % (
                 sem, len(colorido_igual)))
    if info["num_so_base"] or info["num_so_rev"]:
        h.append("<h2>Numeração de passos em só uma das versões</h2><p class='nota'>Números de passo (ex.: 8.4.1.1) que "
                 "existem só em uma das versões. Ficam fora da lista acima porque ocupam coluna própria.</p><dl>")
        for rotulo, numeros in (("Só na base", info["num_so_base"]), ("Só na revisada", info["num_so_rev"])):
            h.append("<dt>%s <span class='num'>%d</span></dt><dd class='mono'>%s</dd>" % (
                rotulo, len(numeros), html.escape(", ".join(numeros)) or "&mdash;"))
        h.append("</dl>")
    h.append("<h2>Cabeçalhos e rodapés ignorados</h2><details><summary>Linhas detectadas (padrão normalizado)</summary>"
             "<ul class='mono'>")
    for linha in sorted(info["removidas"]):
        h.append("<li>%s</li>" % html.escape(linha))
    h.append("</ul></details></main><script>%s</script>" % JS)
    return "".join(h)


# ------------------------------------------------------------------------ main
def processa(caminho, workers, ignorar):
    n, linhas = extrai_linhas(caminho, workers)
    linhas = [l for l in linhas if l["pagina"] > ignorar]
    titulo = titulo_do_cabecalho(linhas, n - ignorar)  # antes de o cabeçalho ser descartado
    linhas, removidas = remove_repetidas(linhas, n - ignorar)
    # a numeração de passo fica em coluna própria e muda de posição entre as versões:
    # sai do fluxo de texto e é comparada em separado
    numeros = []
    paragrafos = prepara(monta_paragrafos(linhas, numeros))
    return n, paragrafos, removidas, numeros, titulo


class ErroComparacao(Exception):
    """Falha esperada (PDF ilegível, sem texto...) com mensagem própria para o usuário."""


def _le_pdf(caminho, nome, workers, ignorar):
    try:
        resultado = processa(caminho, workers, ignorar)
    except Exception as exc:  # pdfplumber/pdfminer levantam vários tipos para PDF corrompido
        raise ErroComparacao("Não foi possível ler o PDF \u00ab%s\u00bb: o arquivo parece corrompido ou "
                             "protegido por senha." % nome) from exc
    if not resultado[1]:
        raise ErroComparacao("O PDF \u00ab%s\u00bb não tem texto para comparar (pode ser um documento escaneado)." % nome)
    return resultado


def comparar_pdfs(base, revisada, ignorar_paginas=0, somente_marcados=False, workers=4,
                  progresso=None, nome_base=None, nome_revisada=None):
    """Compara dois PDFs e devolve {'html': ..., 'totais': {...}}.

    progresso(etapa, fracao) é chamado a cada etapa (fracao de 0 a 1).
    Levanta ErroComparacao quando um dos PDFs não pode ser lido ou não tem texto.
    """
    def avisa(etapa, fracao):
        if progresso:
            progresso(etapa, fracao)

    nome_base, nome_revisada = nome_base or base, nome_revisada or revisada
    avisa("Lendo a versão base", 0.02)
    n_b, texto_b, rem_b, num_b, titulo_b = _le_pdf(base, nome_base, workers, ignorar_paginas)
    avisa("Lendo a versão revisada", 0.40)
    n_r, texto_r, rem_r, num_r, titulo_r = _le_pdf(revisada, nome_revisada, workers, ignorar_paginas)
    avisa("Comparando os textos", 0.80)
    itens, colorido_igual = compara(texto_b, texto_r)
    if somente_marcados:
        itens = [i for i in itens if marcacao(i) == "marcado"]
    avisa("Montando o relatório", 0.95)
    info = {"n_b": n_b, "n_r": n_r, "removidas": rem_b | rem_r, "titulo_b": titulo_b, "titulo_r": titulo_r,
            "num_so_base": sorted(set(num_b) - set(num_r), key=_chave_num),
            "num_so_rev": sorted(set(num_r) - set(num_b), key=_chave_num)}
    nomes = SimpleNamespace(base=nome_base, revisada=nome_revisada)
    totais = dict(Counter(i["tipo"] for i in itens))
    totais["itens"] = len(itens)
    totais["marcados"] = sum(1 for i in itens if marcacao(i) == "marcado")
    totais["paginas_base"], totais["paginas_revisada"] = n_b, n_r
    # lidos dentro dos PDFs (None se não achou); «titulo» é o texto pronto para a tela
    totais["titulos"] = {"base": titulo_b, "revisada": titulo_r}
    totais["titulo"] = rotulo_do_titulo(titulo_b, titulo_r)
    return {"html": gera_html(nomes, itens, colorido_igual, info), "totais": totais}


def main():
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


if __name__ == "__main__":
    main()
