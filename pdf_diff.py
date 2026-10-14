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
LIMIAR_PAREAMENTO = 0.5
MARCADO = 0.5          # fração de palavras alteradas coloridas para contar como "marcado"
SO_NUMERACAO = re.compile(r"^\d+(\.\d+)+\.?$")
LIDER_SUMARIO = re.compile(r"\s*\.{4,}\s*\d*\s*$")
INICIO_ITEM = re.compile(r"^(\s*[•\-–▪●○◦]\s|\d+(\.\d+)*[.)]?\s)")


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
                if not (len(l["palavras"]) == 1 and SO_NUMERACAO.match(l["palavras"][0][0]))
                and not (na_margem(l) and _normaliza_linha(" ".join(w for w, _ in l["palavras"])) in repetidas)]
    return mantidas, repetidas


# ------------------------------------------------------------------- parágrafos
def monta_paragrafos(linhas):
    """Agrupa linhas em parágrafos: [{'palavras': [(txt, colorido)], 'pagina': n}]."""
    por_pag = {}
    for l in linhas:
        por_pag.setdefault(l["pagina"], []).append(l)

    paragrafos, atual = [], None

    def fecha():
        nonlocal atual
        if atual and atual["palavras"]:
            paragrafos.append(atual)
        atual = None

    for pag in sorted(por_pag):
        ls = por_pag[pag]
        alturas = sorted(l["bottom"] - l["top"] for l in ls)
        altura = alturas[len(alturas) // 2] if alturas else 10
        for k, l in enumerate(ls):
            texto = " ".join(w for w, _ in l["palavras"])
            anterior = ls[k - 1] if k else None
            nova = atual is None
            if not nova and anterior is not None:
                if l["top"] - anterior["bottom"] > altura * 0.6:
                    nova = True
                elif INICIO_ITEM.match(texto):
                    nova = True
                elif abs(l["x0"] - anterior["x0"]) > l["largura_pag"] * 0.15 and anterior["x1"] < l["largura_pag"] * 0.8:
                    nova = True
            elif not nova and anterior is None:
                # primeira linha da página: continua o parágrafo anterior só se ele não "fechou"
                ult = atual["palavras"][-1][0] if atual["palavras"] else ""
                if ult.endswith((".", ":", ";", "?", "!")) or INICIO_ITEM.match(texto):
                    nova = True
            if nova:
                fecha()
                atual = {"palavras": [], "pagina": pag}
            palavras = list(l["palavras"])
            if (atual["palavras"] and atual["palavras"][-1][0].endswith("-")
                    and len(atual["palavras"][-1][0]) > 1
                    and atual["palavras"][-1][0][-2].isalpha() and palavras[0][0][:1].islower()):
                ant, cor_ant = atual["palavras"].pop()
                palavras[0] = (ant[:-1] + palavras[0][0], cor_ant or palavras[0][1])
            atual["palavras"].extend(palavras)
    fecha()
    return paragrafos


def _norm_token(t):
    t = t.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-")
    return t


def prepara(paragrafos):
    for p in paragrafos:
        pal = [(w, c) for w, c in p["palavras"]]
        txt = LIDER_SUMARIO.sub("", " ".join(w for w, _ in pal))
        if len(txt) < len(" ".join(w for w, _ in pal)):
            pal = [(w, c) for w, c in pal if not re.fullmatch(r"\.{2,}\d*|\d+", w)] if len(pal) > 1 else pal
            pal = [(w, c) for w, c in pal if "...." not in w]
        p["tokens"] = [_norm_token(w) for w, _ in pal]
        p["cores"] = [c for _, c in pal]
        p["chave"] = " ".join(p["tokens"])
        p["frac_cor"] = sum(p["cores"]) / len(p["cores"]) if p["cores"] else 0.0
    return [p for p in paragrafos if p["chave"].strip()]


# ---------------------------------------------------------------------- alinhar
def pareia(base, rev):
    """Pareia parágrafos de um bloco 'replace' por similaridade, preservando a ordem."""
    n, m = len(base), len(rev)
    if n * m > 250_000:  # bloco gigante: pareia só o que casa em ordem, pelo mais parecido
        return pareia_guloso(base, rev)
    sim = [[0.0] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            sm = SequenceMatcher(None, base[i]["tokens"], rev[j]["tokens"], autojunk=False)
            if sm.real_quick_ratio() < LIMIAR_PAREAMENTO or sm.quick_ratio() < LIMIAR_PAREAMENTO:
                continue
            r = sm.ratio()
            if r >= LIMIAR_PAREAMENTO:
                sim[i][j] = r
    # programação dinâmica (LCS ponderado)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            melhor = max(dp[i + 1][j], dp[i][j + 1])
            if sim[i][j]:
                melhor = max(melhor, dp[i + 1][j + 1] + sim[i][j])
            dp[i][j] = melhor
    i = j = 0
    pares = []
    while i < n and j < m:
        if sim[i][j] and abs(dp[i][j] - (dp[i + 1][j + 1] + sim[i][j])) < 1e-9:
            pares.append((i, j)); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pares


def pareia_guloso(base, rev):
    pares, j0 = [], 0
    for i, b in enumerate(base):
        melhor, mj = LIMIAR_PAREAMENTO, None
        for j in range(j0, min(len(rev), j0 + 30)):
            r = SequenceMatcher(None, b["tokens"], rev[j]["tokens"], autojunk=False).ratio()
            if r > melhor:
                melhor, mj = r, j
        if mj is not None:
            pares.append((i, mj)); j0 = mj + 1
    return pares


def diff_palavras(b, r):
    """Retorna (html, frac_cor_alteradas) do diff palavra a palavra de dois parágrafos."""
    sm = SequenceMatcher(None, b["tokens"], r["tokens"], autojunk=False)
    partes, alteradas, coloridas = [], 0, 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            partes.append(html.escape(" ".join(r["tokens"][j1:j2])))
            continue
        if i2 > i1:
            partes.append("<del>%s</del>" % html.escape(" ".join(b["tokens"][i1:i2])))
        if j2 > j1:
            partes.append("<ins>%s</ins>" % html.escape(" ".join(r["tokens"][j1:j2])))
            alteradas += j2 - j1
            coloridas += sum(r["cores"][j1:j2])
    frac = coloridas / alteradas if alteradas else None
    return " ".join(partes), frac


def compara(base, rev):
    ids_b = [p["chave"] for p in base]
    ids_r = [p["chave"] for p in rev]
    sm = SequenceMatcher(None, ids_b, ids_r, autojunk=False)
    itens, colorido_igual = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            colorido_igual += [rev[j] for j in range(j1, j2) if rev[j]["frac_cor"] >= MARCADO]
            continue
        bb, rr = base[i1:i2], rev[j1:j2]
        pares = pareia(bb, rr) if bb and rr else []
        pb = {i: j for i, j in pares}
        pr = {j: i for i, j in pares}
        i = j = 0
        while i < len(bb) or j < len(rr):
            if i < len(bb) and i not in pb:
                itens.append({"tipo": "removido", "pag_b": bb[i]["pagina"], "pag_r": None,
                              "html": "<del>%s</del>" % html.escape(bb[i]["chave"]), "frac": None})
                i += 1
            elif j < len(rr) and j not in pr:
                itens.append({"tipo": "adicionado", "pag_b": None, "pag_r": rr[j]["pagina"],
                              "html": "<ins>%s</ins>" % html.escape(rr[j]["chave"]),
                              "frac": rr[j]["frac_cor"]})
                j += 1
            else:
                h, frac = diff_palavras(bb[i], rr[j])
                itens.append({"tipo": "modificado", "pag_b": bb[i]["pagina"], "pag_r": rr[j]["pagina"],
                              "html": h, "frac": frac})
                i += 1; j += 1
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
    return n, prepara(monta_paragrafos(linhas)), removidas


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
    n_b, base, rem_b = processa(args.base, args.workers, args.ignorar_paginas)
    print("Extraindo revisada...", file=sys.stderr)
    n_r, rev, rem_r = processa(args.revisada, args.workers, args.ignorar_paginas)
    print("Comparando %d x %d parágrafos..." % (len(base), len(rev)), file=sys.stderr)
    itens, colorido_igual = compara(base, rev)
    info = {"n_b": n_b, "n_r": n_r, "removidas": rem_b | rem_r}
    with open(args.saida, "w", encoding="utf-8") as f:
        f.write(gera_html(args, itens, colorido_igual, info))
    c = Counter(i["tipo"] for i in itens)
    print("Pronto: %s (%s)" % (args.saida, dict(c)), file=sys.stderr)


if __name__ == "__main__":
    main()
