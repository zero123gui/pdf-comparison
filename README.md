# pdf-comparison

Compara duas versões de um PDF (base x revisada) e gera um relatório HTML com as diferenças.

```
pip install -r requirements.txt
python pdf_diff.py --base ITAB.pdf --revisada ITAC.pdf --saida relatorio.html
```

Opções: `--ignorar-paginas N` (descarta capa/tabela de revisões), `--workers N`.

Etapas: extrai o texto -> remove cabeçalho/rodapé (linhas de margem repetidas) -> monta parágrafos ->
alinha parágrafos por similaridade -> diff por palavra -> cruza com a cor do texto na versão revisada.

Limitações conhecidas: tabelas são tratadas como texto linear; números de página do sumário e
numerações de passo (ex.: `8.17.1`) em coluna própria podem gerar ruído.
