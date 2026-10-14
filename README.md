# pdf-comparison

Compara duas versões de um PDF (base x revisada) e gera um relatório HTML com as diferenças.

```
pip install -r requirements.txt
python pdf_diff.py --base ITAB.pdf --revisada ITAC.pdf --saida relatorio.html
```

Opções: `--ignorar-paginas N` (descarta as primeiras páginas; para ITAB/ITAC use `1`, a capa), `--somente-marcados` (só itens marcados em cor), `--workers N`.

Etapas: extrai o texto -> remove cabeçalho/rodapé (linhas de margem repetidas) -> lê a página por
blocos (colunas/células de tabela, usando as bordas desenhadas) e monta parágrafos -> alinha os
parágrafos iguais -> nos trechos que diferem, diff palavra a palavra do texto achatado (quebra de
linha ou de parágrafo diferente não gera diferença) -> cruza com a cor do texto na versão revisada.

Ignorado de propósito: diferença só de espaçamento ("d uração" x "duração"), número de página do
sumário e numeração de passo solta (8.4.1.1), que é comparada em separado.

Limitações conhecidas: tabelas são lidas célula a célula, mas não alinhadas linha a linha entre as
versões; tabelas de valores numéricos e blocos de assinatura podem gerar ruído.
