# pdf-comparison

Compara duas versões de um PDF (base x revisada) e mostra só o que mudou no conteúdo, num relatório HTML.
Funciona como linha de comando e como sistema web.

## Arquivos

| Arquivo | O que é |
| --- | --- |
| `servidor.py` | O sistema web, num arquivo só, no estilo do `mock_server.py`: Flask, constantes no topo e `app.run(...)` no final |
| `pdf_diff.py` | O motor da comparação, que também funciona como linha de comando |
| `templates/`, `static/` | A página (HTML, CSS e JavaScript puros, sem etapa de build) |
| `tests/` | Testes automáticos |

## Bibliotecas externas

Só duas, e nenhuma é opcional:

- **Flask**: o servidor web (o mesmo do `mock_server.py`).
- **pdfplumber**: lê o texto, a posição, a cor e as bordas de tabela dos PDFs. Não há como comparar PDFs sem uma biblioteca que leia PDF.

Todo o resto é biblioteca padrão do Python. Os testes usam `unittest`, e o servidor é o próprio do Flask
(sem `gunicorn`). O `pip` instala também as dependências dessas duas (13 pacotes no total).

## Sistema web

Abre no navegador, recebe os dois PDFs, compara em segundo plano e mostra o relatório na própria página.

```bash
python -m venv .venv
.venv\Scripts\activate             # Windows (no Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
python servidor.py                 # http://localhost:8080
```

Para outras máquinas da rede acessarem, defina `HOST=0.0.0.0` (por padrão só a própria máquina acessa).
O servidor é o do Flask, como no `mock_server.py`: serve bem para uso interno com poucos usuários. O sistema
**não tem login**; deixe-o na rede interna ou atrás de um proxy com autenticação. O estado e a fila ficam num
único processo, então rode uma só instância.

### Configuração (variáveis de ambiente)

Os valores também aparecem como constantes no topo do `servidor.py`.

| Variável | Padrão | O que controla |
| --- | --- | --- |
| `HOST`, `PORT` | `localhost`, `8080` | Endereço do servidor |
| `PDFDIFF_DATA_DIR` | `./data` | Pasta das comparações (PDFs temporários e relatórios) |
| `PDFDIFF_MAX_FILE_MB` | 60 | Tamanho máximo de cada PDF |
| `PDFDIFF_MAX_CONCURRENT` | 2 | Comparações ao mesmo tempo |
| `PDFDIFF_MAX_QUEUE` | 6 | Quantas esperam na fila; além disso a resposta é `429` |
| `PDFDIFF_TIMEOUT_MIN` | 20 | Tempo limite de uma comparação |
| `PDFDIFF_TTL_HORAS` | 24 | Quanto tempo o relatório fica disponível |
| `PDFDIFF_WORKERS` | 4 | Processos de leitura de páginas dentro de cada comparação |
| `PDFDIFF_IGNORAR_PAGINAS` | 1 | Valor inicial do campo «páginas iniciais a ignorar» |

Dimensione `PDFDIFF_WORKERS` pelos núcleos do servidor: `núcleos / PDFDIFF_MAX_CONCURRENT`. Com 4 núcleos
e 2 comparações simultâneas, cada uma leva cerca de 45 s em vez dos 20 s de uma comparação sozinha. Cada
comparação usa até cerca de 1 GB de memória.

### Rotas

| Rota | Método | O que faz |
| --- | --- | --- |
| `/` | GET | Página do sistema |
| `/api/comparacoes` | POST | Recebe `base`, `revisada`, `ignorar_paginas`, `somente_marcados`; devolve `202` com o `id` |
| `/api/comparacoes/<id>/andamento` | GET | Andamento: `status` (`na_fila`, `executando`, `concluido`, `erro`), `etapa`, `progresso` |
| `/comparacoes/<id>/relatorio` | GET | Relatório pronto (`?baixar=1` baixa o arquivo) |
| `/api/comparacoes/<id>` | DELETE | Apaga a comparação agora (cancela se estiver rodando) |
| `/saude` | GET | `ok`, para monitoramento |

### Privacidade

Os PDFs enviados são apagados pelo próprio processo de comparação antes de a comparação ser dada como
concluída. O relatório e o estado ficam em `PDFDIFF_DATA_DIR/comparacoes/<id>/` e são removidos depois do prazo
(`PDFDIFF_TTL_HORAS`) ou quando o usuário apaga. O `id` tem 128 bits aleatórios; não há listagem de comparações.
Os logs registram id, tamanhos e tempos, nunca o conteúdo dos PDFs.

### Testes

Sem instalar nada além do `requirements.txt`:

```bash
python -m unittest                              # rápidos, cerca de 6 s
python -m unittest tests.integracao_pdfs_reais  # com os PDFs ITAB/ITAC, cerca de 45 s
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
