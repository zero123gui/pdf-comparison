"""Relatório HTML: monta a página autônoma com o resumo, os filtros e um card por diferença."""
import html
from collections import Counter

from .comparacao import marcacao
from .relatorio_assets import CSS, ICONES, JS


ROTULO_TIPO = {"adicionado": "Adicionado", "removido": "Removido", "modificado": "Modificado"}


def _plural(n, singular, varios):
    """O número com a palavra no singular ou no plural: «1 diferença», «2 diferenças»."""
    return "%d %s" % (n, singular if n == 1 else varios)


def rotulo_do_titulo(titulo_b, titulo_r):
    """O que identifica o documento no relatório e na tela.

    É o título, se for o mesmo nas duas versões; se diferem, os dois («A» → «B»); se só um foi achado, ele com
    a versão («A» (base)).

    Parâmetros:
        titulo_b: título achado na versão base, ou None.
        titulo_r: título achado na versão revisada, ou None.

    Devolve:
        O texto pronto para mostrar, ou None quando nenhum título foi achado.
    """
    if titulo_b and titulo_b == titulo_r:
        return titulo_b
    if titulo_b and titulo_r:
        return "«%s» → «%s»" % (titulo_b, titulo_r)
    if titulo_b or titulo_r:
        return "«%s» (%s)" % (titulo_b or titulo_r, "base" if titulo_b else "revisada")
    return None


def _grupo(atributo, opcoes, rotulo):
    """Um grupo de botões (um filtro) em HTML.

    Parâmetros:
        atributo: o `data-` que identifica o filtro («ft» ou «fm»), lido pelo JavaScript do relatório.
        opcoes: [(valor, texto, quantidade, ligado, alerta)], um botão por opção.
        rotulo: o nome do grupo, para leitores de tela.
    """
    botoes = "".join(
        "<button%s data-%s='%s' aria-pressed='%s'>%s <span class='num'>%d</span></button>" % (
            " class='%s'" % " ".join(c for c, ativo in (("on", ligado), ("alerta", alerta)) if ativo)
            if ligado or alerta else "",
            atributo, valor, "true" if ligado else "false", texto, n)
        for valor, texto, n, ligado, alerta in opcoes)
    return "<div class='seg' role='group' aria-label='%s'>%s</div>" % (rotulo, botoes)


def gera_html(args, itens, colorido_igual, info):
    """Monta o relatório: um arquivo HTML autônomo (CSS e JavaScript embutidos, sem nada de fora).

    Mostra o título do documento (se foi achado), os arquivos comparados, os filtros por tipo e por marcação em
    cor e um card por diferença. O link «Ver nos PDFs» de cada card nasce escondido; o JavaScript do relatório o
    mostra só quando o endereço é o do servidor.

    Parâmetros:
        args: objeto com `base` e `revisada`, os nomes dos arquivos.
        itens: as diferenças (veja «Item» em `pdfdiff`).
        colorido_igual: parágrafos em cor que não mudaram (marcação herdada).
        info: dicionário com n_b, n_r (páginas), titulo_b, titulo_r, num_so_base, num_so_rev (números de passo
            em uma só versão) e removidas (cabeçalhos e rodapés descartados).

    Devolve:
        O HTML, como texto.
    """
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
            """O número da página ou o intervalo de páginas do item: «12» ou «12–14»."""
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
