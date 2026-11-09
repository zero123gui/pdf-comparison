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


BASE = ["Primeira linha do documento", "O sistema opera no modo CF.",
        "Trecho que sera removido.", "Ultima linha do documento"]
REVISADA = ["Primeira linha do documento", "O sistema opera no modo CFC.",
            "Paragrafo novo adicionado aqui.", "Ultima linha do documento"]
