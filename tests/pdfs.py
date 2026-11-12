"""Gera PDFs minúsculos (sem dependência extra) para os testes."""


def _esc(texto):
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_simples(linhas, azuis=()):
    """Uma página com uma linha de texto por item; os índices em `azuis` saem na cor das alterações."""
    y, partes = 600, []
    for i, texto in enumerate(linhas):
        cor = "0 0.439 0.753 rg" if i in azuis else "0 g"
        partes.append("%s BT /F1 11 Tf 72 %d Td (%s) Tj ET" % (cor, y, _esc(texto)))
        y -= 24
    conteudo = "\n".join(partes).encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    saida, posicoes = bytearray(b"%PDF-1.4\n"), []
    for n, corpo in enumerate(objetos, 1):
        posicoes.append(len(saida))
        saida += b"%d 0 obj\n" % n + corpo + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for pos in posicoes:
        saida += b"%010d 00000 n \n" % pos
    saida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio_xref)
    return bytes(saida)


def pdf_paginas(n_paginas, celulas_cabecalho=(), paginas_com_cabecalho=None):
    """PDF de várias páginas com um cabeçalho em tabela, sem o cabeçalho na capa (página 1).

    `celulas_cabecalho`: [(x, largura, [linhas])]; cada célula leva bordas verticais e
    «{n}» / «{total}» nas linhas viram o número da página e o total. Cada página tem uma linha de
    corpo própria. `paginas_com_cabecalho`: quais páginas levam o cabeçalho (padrão: da 2ª em diante)."""
    com = set(paginas_com_cabecalho if paginas_com_cabecalho is not None else range(2, n_paginas + 1))
    objetos = [b"<< /Type /Catalog /Pages 2 0 R >>",
               ("<< /Type /Pages /Kids [%s] /Count %d >>" % (
                   " ".join("%d 0 R" % (4 + 2 * i) for i in range(n_paginas)), n_paginas)).encode(),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    for n in range(1, n_paginas + 1):
        partes = ["0 g BT /F1 11 Tf 72 600 Td (%s) Tj ET" % _esc("Texto do corpo da pagina %d." % n)]
        if n in com:
            for x, largura, linhas in celulas_cabecalho:
                partes.append("%d 780 1 40 re f %d 780 1 40 re f" % (x, x + largura))  # bordas da célula
                for k, texto in enumerate(linhas):
                    texto = texto.replace("{n}", str(n)).replace("{total}", str(n_paginas))
                    partes.append("BT /F1 10 Tf %d %d Td (%s) Tj ET" % (x + 5, 810 - 12 * k, _esc(texto)))
        conteudo = "\n".join(partes).encode("latin-1")
        objetos.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                        "/Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * (n - 1))).encode())
        objetos.append(b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream")
    saida, posicoes = bytearray(b"%PDF-1.4\n"), []
    for k, corpo in enumerate(objetos, 1):
        posicoes.append(len(saida))
        saida += b"%d 0 obj\n" % k + corpo + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for pos in posicoes:
        saida += b"%010d 00000 n \n" % pos
    saida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio_xref)
    return bytes(saida)


BASE = ["Primeira linha do documento", "O sistema opera no modo CF.",
        "Trecho que sera removido.", "Ultima linha do documento"]
REVISADA = ["Primeira linha do documento", "O sistema opera no modo CFC.",
            "Paragrafo novo adicionado aqui.", "Ultima linha do documento"]
