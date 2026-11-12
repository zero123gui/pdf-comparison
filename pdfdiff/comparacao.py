"""Comparação: alinha os parágrafos das duas versões e, nos trechos que diferem, faz o diff palavra a palavra,
gerando uma lista de itens (as diferenças).
"""
import html
from difflib import SequenceMatcher


CONTEXTO = 10  # palavras de contexto exibidas em volta de cada mudança
MARCADO = 0.5          # fração de palavras alteradas coloridas para contar como "marcado"


def _achata(paragrafos):
    """Concatena os parágrafos num fluxo único de palavras, que independe de onde o texto quebra.

    Devolve:
        (tokens, cores, paginas, inicio): quatro listas com uma posição por palavra; `inicio` diz se a palavra
        abre um parágrafo.
    """
    tokens, cores, paginas, inicio = [], [], [], []
    for p in paragrafos:
        for k, (t, c) in enumerate(zip(p["tokens"], p["cores"])):
            tokens.append(t); cores.append(c); paginas.append(p["pagina"]); inicio.append(k == 0)
    return tokens, cores, paginas, inicio


def _texto(tokens, inicio, ini, fim):
    """Junta as palavras de `ini` a `fim` (já com HTML escapado); parágrafo novo dentro do trecho vira quebra de linha."""
    out = []
    for k in range(ini, fim):
        if inicio[k] and k > ini:
            out.append("<br>")
        out.append(html.escape(tokens[k]))
    return " ".join(out).replace(" <br> ", "<br>")


def _sem_espaco(tokens):
    """Os tokens colados, sem nenhum espaço, para comparar trechos que só diferem em espaçamento."""
    return "".join("".join(tokens).split())


def _ignora_espacamento(ops, bt, rt):
    """Transforma em «equal» as mudanças que só diferem em espaços («d uração» x «duração», «- >» x «->»).

    Mudanças separadas por até 3 palavras iguais são avaliadas juntas. No fim, funde os «equal» vizinhos para o
    agrupamento por contexto.

    Parâmetros:
        ops: opcodes do `SequenceMatcher` (tipo, i1, i2, j1, j2).
        bt: tokens da base.
        rt: tokens da revisada.

    Devolve:
        A lista de opcodes sem as diferenças de espaçamento.
    """
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
    """Faz o diff palavra a palavra de um trecho que difere e devolve um item por grupo de mudanças.

    Mudanças separadas por poucas palavras iguais (até 2 x CONTEXTO) formam um só item. O HTML do item traz
    CONTEXTO palavras de cada lado, o texto inserido em <ins> e o removido em <del>. Se só mudou a quebra de
    linha ou de parágrafo, não há item.

    Parâmetros:
        base: parágrafos da base neste trecho.
        rev: parágrafos da revisada neste trecho.

    Devolve:
        Lista de itens (veja «Item» em `pdfdiff`).
    """
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
    """Alinha os parágrafos das duas versões e devolve as diferenças.

    O alinhamento usa a `chave` de cada parágrafo (o texto normalizado); os parágrafos iguais passam e, onde há
    diferença, `_itens_do_bloco` detalha palavra a palavra.

    Parâmetros:
        base: parágrafos da base (já preparados por `prepara`).
        rev: parágrafos da revisada.

    Devolve:
        (itens, colorido_igual): as diferenças e os parágrafos da revisada que são idênticos aos da base mas
        estão em cor (marcação herdada de revisões anteriores).
    """
    sm = SequenceMatcher(None, [p["chave"] for p in base], [p["chave"] for p in rev], autojunk=False)
    itens, colorido_igual = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            colorido_igual += [rev[j] for j in range(j1, j2) if rev[j]["frac_cor"] >= MARCADO]
        else:
            itens += _itens_do_bloco(base[i1:i2], rev[j1:j2])
    return itens, colorido_igual


def marcacao(item):
    """Classifica a marcação em cor de um item.

    Parâmetros:
        item: uma diferença (veja «Item» em `pdfdiff`).

    Devolve:
        «marcado» se ao menos MARCADO das palavras inseridas estão em cor; «parcial» se algumas estão; «sem» se
        nenhuma; «n/a» se o item não tem texto novo para colorir (removido).
    """
    f = item["frac"]
    if item["tipo"] == "removido":
        return "n/a"
    if f is None:
        return "n/a"
    if f >= MARCADO:
        return "marcado"
    return "parcial" if f > 0 else "sem"
