#!/usr/bin/env python3
"""Compara duas versões de um PDF (base x revisada) e gera um relatório HTML.

Etapas: extrai o texto -> remove cabeçalho/rodapé -> monta parágrafos ->
alinha parágrafos -> diff (parágrafo e palavra) -> verifica contra a cor do texto.

Uso:
    python pdf_diff.py --base ITAB.pdf --revisada ITAC.pdf --saida relatorio.html
"""
import argparse
import html
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher

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


# ------------------------------------------------------------------- parágrafos
# vão horizontal (pt) que separa colunas sem borda desenhada; as células de tabela são separadas
# pelas bordas verticais, e texto justificado em célula estreita pode ter vãos grandes entre palavras
VAO_COLUNA = 60


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
    colunas = []
    for b in sorted(faixa, key=lambda b: b["x0"]):
        if colunas and b["x0"] < colunas[-1]["x1"]:
            colunas[-1]["blocos"].append(b)
            colunas[-1]["x1"] = max(colunas[-1]["x1"], b["x1"])
        else:
            colunas.append({"blocos": [b], "x1": b["x1"]})
    return [b for c in colunas for b in sorted(c["blocos"], key=lambda b: b["top"])]


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
        pag_b = pag_r = None
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
                pag_b = pag_b or bp[i1]
                partes.append("<del>%s</del>" % _texto(bt, bi, i1, i2))
            if j2 > j1:
                tem_ins = True
                pag_r = pag_r or rp[j1]
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
        if pag_b is None and bp:
            pag_b = bp[min(ref[1], len(bp) - 1)]
        if pag_r is None and rp:
            pag_r = rp[min(ref[3], len(rp) - 1)]
        tipo = "modificado" if tem_ins and tem_del else ("adicionado" if tem_ins else "removido")
        itens.append({"tipo": tipo,
                      "pag_b": None if tipo == "adicionado" else pag_b,
                      "pag_r": None if tipo == "removido" else pag_r,
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
:root{--bg:#fff;--fg:#1c2127;--mut:#5d6773;--card:#f5f7f9;--bd:#d9dee3;--add:#d7f3de;--addf:#0b5d27;--del:#fbdada;--delf:#8a1c1c;--acc:#0b5cab}
@media(prefers-color-scheme:dark){:root{--bg:#14181d;--fg:#e6e9ec;--mut:#9aa5b1;--card:#1c2229;--bd:#2f3842;--add:#16422a;--addf:#a6efbf;--del:#4a1f1f;--delf:#ffb4b4;--acc:#6db1ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);margin:0 0 20px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:20px}
.c{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:10px 12px}.c b{display:block;font-size:22px}.c span{color:var(--mut);font-size:13px}
.bar{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--bd);display:flex;flex-wrap:wrap;gap:6px;z-index:2}
.bar button{border:1px solid var(--bd);background:var(--card);color:var(--fg);border-radius:999px;padding:4px 12px;cursor:pointer;font:inherit;font-size:13px}
.bar button.on{background:var(--acc);border-color:var(--acc);color:#fff}.bar i{flex-basis:100%;height:0}
.item{border:1px solid var(--bd);border-radius:8px;margin:12px 0;background:var(--card)}
.item header{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:8px 12px;border-bottom:1px solid var(--bd);font-size:13px;color:var(--mut)}
.item p{margin:0;padding:10px 12px;overflow-wrap:anywhere}
.tag{border-radius:4px;padding:1px 8px;font-weight:600;font-size:12px}.t-adicionado{background:var(--add);color:var(--addf)}.t-removido{background:var(--del);color:var(--delf)}.t-modificado{background:var(--bd);color:var(--fg)}
.m-sem{color:var(--delf);font-weight:600}.m-marcado{color:var(--addf)}
ins{background:var(--add);color:var(--addf);text-decoration:none;border-radius:2px;padding:0 2px}del{background:var(--del);color:var(--delf);border-radius:2px;padding:0 2px}
h2{font-size:17px;margin:28px 0 6px}details{margin:8px 0}summary{cursor:pointer;color:var(--acc)}
"""

JS = """
const itens=[...document.querySelectorAll('.item')];let ft='todos',fm='todas';
function aplica(){let n=0;itens.forEach(e=>{const ok=(ft==='todos'||e.dataset.tipo===ft)&&(fm==='todas'||e.dataset.marc===fm);e.hidden=!ok;if(ok)n++});document.getElementById('n').textContent=n}
document.querySelectorAll('[data-ft]').forEach(b=>b.onclick=()=>{ft=b.dataset.ft;document.querySelectorAll('[data-ft]').forEach(x=>x.classList.toggle('on',x===b));aplica()});
document.querySelectorAll('[data-fm]').forEach(b=>b.onclick=()=>{fm=b.dataset.fm;document.querySelectorAll('[data-fm]').forEach(x=>x.classList.toggle('on',x===b));aplica()});aplica();
"""


def _chave_num(s):
    return [int(x) for x in s.split(".")]


def gera_html(args, itens, colorido_igual, info):
    c = Counter(i["tipo"] for i in itens)
    sem = sum(1 for i in itens if i["tipo"] != "removido" and marcacao(i) == "sem")
    parcial = sum(1 for i in itens if marcacao(i) == "parcial")
    h = ["<!doctype html><html lang='pt-BR'><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>Comparação de PDFs</title><style>%s</style><main>" % CSS,
         "<h1>Comparação de PDFs</h1>",
         "<p class='sub'>Base: <b>%s</b> (%d págs.) &rarr; Revisada: <b>%s</b> (%d págs.)</p>" % (
             html.escape(args.base), info["n_b"], html.escape(args.revisada), info["n_r"]),
         "<div class='cards'>"]
    for rot, val in [("Adicionados", c["adicionado"]), ("Removidos", c["removido"]),
                     ("Modificados", c["modificado"]),
                     ("Alterados sem cor", sem),
                     ("Coloridos sem diferença", len(colorido_igual))]:
        h.append("<div class='c'><b>%d</b><span>%s</span></div>" % (val, rot))
    h.append("</div><p class='sub'>&ldquo;Alterados sem cor&rdquo;: o texto mudou entre as versões mas não está "
             "marcado na revisada &mdash; vale conferir. &ldquo;Coloridos sem diferença&rdquo;: texto marcado em "
             "cor que é idêntico à base (marcação herdada de revisões anteriores).</p>")
    h.append("<div class='bar'>"
             "<button class='on' data-ft='todos'>Todos</button><button data-ft='adicionado'>Adicionados</button>"
             "<button data-ft='removido'>Removidos</button><button data-ft='modificado'>Modificados</button><i></i>"
             "<button class='on' data-fm='todas'>Qualquer marcação</button><button data-fm='sem'>Sem cor</button>"
             "<button data-fm='parcial'>Parcial</button><button data-fm='marcado'>Marcado</button>"
             "<span style='margin-left:auto;color:var(--mut);font-size:13px;align-self:center'><span id='n'></span> itens</span></div>")
    for k, it in enumerate(itens, 1):
        m = marcacao(it)
        pags = []
        if it["pag_b"]:
            pags.append("base p. %d" % it["pag_b"])
        if it["pag_r"]:
            pags.append("revisada p. %d" % it["pag_r"])
        rot = {"sem": "<span class='m-sem'>sem cor</span>", "parcial": "cor parcial",
               "marcado": "<span class='m-marcado'>marcado em cor</span>", "n/a": ""}[m]
        h.append("<section class='item' data-tipo='%s' data-marc='%s'><header><b>#%d</b>"
                 "<span class='tag t-%s'>%s</span><span>%s</span>%s</header><p>%s</p></section>" % (
                     it["tipo"], m, k, it["tipo"], it["tipo"], " · ".join(pags), rot, it["html"]))
    if info["num_so_base"] or info["num_so_rev"]:
        h.append("<h2>Numeração de passos</h2><p class='sub'>Números de passo (ex.: 8.4.1.1) que existem "
                 "só em uma das versões. Ficam fora da lista acima porque ocupam coluna própria.</p>")
        h.append("<p><b>Só na base (%d):</b> %s</p>" % (len(info["num_so_base"]), html.escape(", ".join(info["num_so_base"])) or "&mdash;"))
        h.append("<p><b>Só na revisada (%d):</b> %s</p>" % (len(info["num_so_rev"]), html.escape(", ".join(info["num_so_rev"])) or "&mdash;"))
    h.append("<h2>Cabeçalhos/rodapés removidos</h2><details><summary>Linhas detectadas (padrão normalizado)</summary><ul>")
    for linha in sorted(info["removidas"]):
        h.append("<li>%s</li>" % html.escape(linha))
    h.append("</ul></details></main><script>%s</script>" % JS)
    return "".join(h)


# ------------------------------------------------------------------------ main
def processa(caminho, workers, ignorar):
    n, linhas = extrai_linhas(caminho, workers)
    linhas = [l for l in linhas if l["pagina"] > ignorar]
    linhas, removidas = remove_repetidas(linhas, n - ignorar)
    # a numeração de passo fica em coluna própria e muda de posição entre as versões:
    # sai do fluxo de texto e é comparada em separado
    numeros = []
    paragrafos = prepara(monta_paragrafos(linhas, numeros))
    return n, paragrafos, removidas, numeros


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="PDF da versão base")
    ap.add_argument("--revisada", required=True, help="PDF da versão revisada")
    ap.add_argument("--saida", default="relatorio.html")
    ap.add_argument("--ignorar-paginas", type=int, default=0,
                    help="ignora as N primeiras páginas de cada PDF (capa, tabela de revisões)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    print("Extraindo base...", file=sys.stderr)
    n_b, base, rem_b, num_b = processa(args.base, args.workers, args.ignorar_paginas)
    print("Extraindo revisada...", file=sys.stderr)
    n_r, rev, rem_r, num_r = processa(args.revisada, args.workers, args.ignorar_paginas)
    print("Comparando %d x %d parágrafos..." % (len(base), len(rev)), file=sys.stderr)
    itens, colorido_igual = compara(base, rev)
    info = {"n_b": n_b, "n_r": n_r, "removidas": rem_b | rem_r,
            "num_so_base": sorted(set(num_b) - set(num_r), key=_chave_num),
            "num_so_rev": sorted(set(num_r) - set(num_b), key=_chave_num)}
    with open(args.saida, "w", encoding="utf-8") as f:
        f.write(gera_html(args, itens, colorido_igual, info))
    c = Counter(i["tipo"] for i in itens)
    print("Pronto: %s (%s)" % (args.saida, dict(c)), file=sys.stderr)


if __name__ == "__main__":
    main()
