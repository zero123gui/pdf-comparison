"""Comparador: junta as etapas (leitura, cabeçalho, layout, parágrafos, comparação e relatório) em
`comparar_pdfs`, que é a API do motor.
"""
from collections import Counter
from types import SimpleNamespace

from .cabecalho import remove_repetidas, titulo_do_cabecalho
from .comparacao import compara, marcacao
from .extracao import extrai_linhas
from .paragrafos import monta_paragrafos, prepara
from .relatorio import gera_html, rotulo_do_titulo


def _chave_num(s):
    """Chave de ordenação de um número de passo: «8.4.1.1» vira [8, 4, 1, 1], para «8.10» vir depois de «8.9»."""
    return [int(x) for x in s.split(".")]


def processa(caminho, workers, ignorar):
    """Passa um PDF por todas as etapas até os parágrafos prontos para comparar.

    Parâmetros:
        caminho: caminho do PDF.
        workers: processos de leitura de páginas.
        ignorar: quantas páginas iniciais descartar (capa, tabela de revisões).

    Devolve:
        (n_paginas, paragrafos, removidas, numeros, titulo): o total de páginas do PDF, os parágrafos, os padrões
        de cabeçalho e rodapé descartados, os números de passo soltos e o título lido do cabeçalho (ou None).
    """
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
    """Falha esperada (PDF ilegível, sem texto...), com mensagem própria para o usuário."""


def _le_pdf(caminho, nome, workers, ignorar):
    """Lê um PDF com `processa` e transforma qualquer falha de leitura em `ErroComparacao`.

    A mensagem é para o usuário. A causa técnica fica em `__cause__`, para o servidor registrar no log sem
    mostrá-la.

    Parâmetros:
        caminho: caminho do PDF.
        nome: nome do arquivo, como o usuário o conhece, para a mensagem.
        workers: processos de leitura de páginas.
        ignorar: quantas páginas iniciais descartar.

    Devolve:
        O mesmo que `processa`.

    Levanta:
        ErroComparacao: se o PDF não puder ser lido (corrompido ou com senha) ou não tiver texto.
    """
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
    """Compara dois PDFs e devolve o relatório e os totais.

    É a API do motor: o servidor e a linha de comando só chamam esta função.

    Parâmetros:
        base: caminho do PDF da versão base.
        revisada: caminho do PDF da versão revisada.
        ignorar_paginas: quantas páginas iniciais descartar em cada PDF (capa, tabela de revisões).
        somente_marcados: se verdadeiro, o relatório traz só os itens marcados em cor na revisada.
        workers: processos de leitura de páginas dentro de cada PDF.
        progresso: função `progresso(etapa, fracao)` chamada a cada etapa, com a fração de 0 a 1.
        nome_base: nome do arquivo base para mostrar (por padrão, o caminho).
        nome_revisada: nome do arquivo revisado para mostrar (por padrão, o caminho).

    Devolve:
        {"html": o relatório, "totais": dicionário}. Os totais trazem a contagem por tipo («adicionado»,
        «removido», «modificado»), `itens`, `marcados`, `paginas_base`, `paginas_revisada`, `titulos`
        ({"base", "revisada"}, None onde não foi achado) e `titulo` (o texto pronto para a tela).

    Levanta:
        ErroComparacao: se um dos PDFs não puder ser lido ou não tiver texto.
    """
    def avisa(etapa, fracao):
        """Repassa o andamento a `progresso`, se houver."""
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
