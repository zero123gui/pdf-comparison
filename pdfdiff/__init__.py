"""Motor de comparação de PDFs: recebe a versão base e a revisada de um documento e devolve o que mudou.

Uso:

    from pdfdiff import comparar_pdfs, ErroComparacao

    resultado = comparar_pdfs("base.pdf", "revisada.pdf", ignorar_paginas=1)
    resultado["html"]      # o relatório, um arquivo HTML autônomo
    resultado["totais"]    # contagens, páginas e títulos, para a interface

Etapas, na ordem em que `comparador.comparar_pdfs` as executa (um módulo por etapa):

    extracao          PDF -> linhas de texto de cada página (pdfplumber; um processo por lote de páginas)
    cabecalho         acha o título do documento e descarta os cabeçalhos e rodapés que se repetem
    layout            palavras -> segmentos -> blocos (células, colunas) -> ordem de leitura
    paragrafos        blocos -> parágrafos; normaliza o texto e tira o sumário
    comparacao        alinha os parágrafos e faz o diff palavra a palavra -> itens (as diferenças)
    relatorio         itens -> página HTML (o CSS, o JavaScript e os ícones ficam em relatorio_assets)
    comparador        junta as etapas (a API pública) e avisa o andamento
    cli               a linha de comando (o ponto de entrada é o pdf_diff.py, na raiz do projeto)

Estruturas de dados. São dicionários simples, sem classes; as medidas são em pontos (pt) do PDF, contadas a
partir do canto superior esquerdo da página:

    Linha       uma linha de texto da página (devolvida por `extracao`)
                pagina (de 1 a n), top, bottom, x0, x1, altura_pag, largura_pag,
                palavras [(texto, colorido)], xs [(x0, x1) de cada palavra],
                regras {"h": [(x0, x1, y)], "v": [(x, y0, y1)]}  (as bordas de tabela da página)
    Segmento    pedaço de uma linha sem vão largo nem borda de tabela no meio: top, bottom, x0, x1, palavras
    Bloco       segmentos empilhados, que formam uma célula de tabela ou um trecho de texto: segs, top,
                bottom, x0, x1 (e "linha", que `layout.ordem_leitura` acrescenta)
    Parágrafo   palavras [(texto, colorido)] e pagina; `paragrafos.prepara` acrescenta tokens, cores,
                chave e frac_cor
    Item        uma diferença entre as versões: tipo ("adicionado", "removido" ou "modificado"),
                pag_b / pag_b_fim e pag_r / pag_r_fim (primeira e última página em cada versão; None onde o
                trecho não existe), html (o trecho com <ins> e <del> e um pouco de contexto) e frac (a fração
                das palavras inseridas que está em cor; None se não há inserção)
"""

from .comparador import ErroComparacao, comparar_pdfs

__all__ = ["comparar_pdfs", "ErroComparacao"]
