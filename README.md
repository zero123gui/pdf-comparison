# pdf-comparison

Compara duas versões de um PDF (base x revisada) e mostra só o que mudou no conteúdo, num relatório HTML.
Funciona como linha de comando e como sistema web.

## Sistema web

Abre no navegador, recebe os dois PDFs, compara em segundo plano e mostra o relatório na própria página.

### Rodar localmente

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py            # http://127.0.0.1:5000
```

`run.py` é só para desenvolvimento.

### Rodar em servidor

```bash
.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app     # escuta em 0.0.0.0:8000
```

O `gunicorn.conf.py` usa **um processo com várias threads** de propósito: o limite de comparações
simultâneas e a fila ficam corretos sem coordenação entre processos. O trabalho pesado roda em
processos próprios, um por comparação. Há um `Dockerfile` (ainda não testado: o ambiente de
desenvolvimento não tinha o daemon do Docker).

O sistema **não tem login**. Deixe-o na rede interna ou atrás de um proxy com autenticação.

### Configuração (variáveis de ambiente)

| Variável | Padrão | O que controla |
| --- | --- | --- |
| `PDFDIFF_DATA_DIR` | `./data` | Pasta dos trabalhos (PDFs temporários e relatórios) |
| `PDFDIFF_MAX_FILE_MB` | 60 | Tamanho máximo de cada PDF |
| `PDFDIFF_MAX_CONCURRENT` | 2 | Comparações ao mesmo tempo |
| `PDFDIFF_MAX_QUEUE` | 6 | Quantas esperam na fila; além disso a resposta é `429` |
| `PDFDIFF_TIMEOUT_MIN` | 20 | Tempo limite de uma comparação |
| `PDFDIFF_TTL_HORAS` | 24 | Quanto tempo o relatório fica disponível |
| `PDFDIFF_WORKERS` | 4 | Processos de leitura de páginas dentro de cada comparação |
| `PDFDIFF_IGNORAR_PAGINAS` | 1 | Valor inicial do campo «páginas iniciais a ignorar» |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | Endereço do gunicorn |

Dimensione `PDFDIFF_WORKERS` pelos núcleos do servidor: `núcleos / PDFDIFF_MAX_CONCURRENT`. Com 4 núcleos
e 2 comparações simultâneas, cada uma leva cerca de 45 s em vez dos 20 s de uma comparação sozinha.

### Rotas

| Rota | Método | O que faz |
| --- | --- | --- |
| `/` | GET | Página do sistema |
| `/api/jobs` | POST | Recebe `base`, `revisada`, `ignorar_paginas`, `somente_marcados`; devolve `202` com o `id` |
| `/api/jobs/<id>` | GET | Andamento: `status` (`na_fila`, `executando`, `concluido`, `erro`), `etapa`, `progresso` |
| `/jobs/<id>/relatorio` | GET | Relatório pronto (`?baixar=1` baixa o arquivo) |
| `/api/jobs/<id>` | DELETE | Apaga o trabalho agora (cancela se estiver rodando) |
| `/saude` | GET | `ok`, para monitoramento |

### Privacidade

Os PDFs enviados são apagados pelo próprio processo de comparação antes de o trabalho ser dado como
concluído. O relatório e o estado ficam em `PDFDIFF_DATA_DIR/jobs/<id>/` e são removidos depois do prazo
(`PDFDIFF_TTL_HORAS`) ou quando o usuário apaga. O `id` tem 128 bits aleatórios; não há listagem de trabalhos.
Os logs registram id, tamanhos e tempos, nunca o conteúdo dos PDFs.

### Testes

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest              # rápidos, cerca de 6 s
.venv/bin/python -m pytest -m lento     # com os PDFs ITAB/ITAC, cerca de 45 s
```

## Linha de comando

```bash
python pdf_diff.py --base ITAB.pdf --revisada ITAC.pdf --saida relatorio.html --ignorar-paginas 1
```

Opções: `--ignorar-paginas N` (descarta as primeiras páginas; para ITAB/ITAC use `1`, a capa),
`--somente-marcados` (só itens marcados em cor), `--workers N`.

## Como a comparação funciona

Extrai o texto -> remove cabeçalho/rodapé (linhas de margem repetidas) -> lê a página por blocos
(colunas/células de tabela, usando as bordas desenhadas) e monta parágrafos -> alinha os parágrafos
iguais -> nos trechos que diferem, diff palavra a palavra do texto achatado (quebra de linha ou de
parágrafo diferente não gera diferença) -> cruza com a cor do texto na versão revisada.

Ignorado de propósito: diferença só de espaçamento ("d uração" x "duração"), número de página do
sumário e numeração de passo solta (8.4.1.1), que é comparada em separado.

Limitações conhecidas: só compara texto (imagens não); tabelas são lidas célula a célula, mas não
alinhadas linha a linha entre as versões; tabelas de valores numéricos e blocos de assinatura podem
gerar ruído; validado em um único par de documentos.
