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
| `PDFDIFF_DATA_DIR` | `./data` | Pasta das comparações (PDFs enviados, relatórios e estado) |
| `PDFDIFF_MAX_FILE_MB` | 60 | Tamanho máximo de cada PDF |
| `PDFDIFF_MAX_CONCURRENT` | 2 | Comparações ao mesmo tempo |
| `PDFDIFF_MAX_QUEUE` | 6 | Quantas esperam na fila; além disso a resposta é `429` |
| `PDFDIFF_TIMEOUT_MIN` | 20 | Tempo limite de uma comparação |
| `PDFDIFF_TTL_HORAS` | 24 | Quanto tempo o relatório e os PDFs ficam disponíveis |
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
| `/comparacoes/<id>/pdfs` | GET | Os dois PDFs lado a lado: `?b=N` é a página na base e `?r=N` na revisada (o card da diferença liga para cá). Sem a página de um lado, esse lado não aparece |
| `/comparacoes/<id>/pdf/<versao>` | GET | Um dos PDFs enviados (`base` ou `revisada`), aberto no navegador |
| `/api/comparacoes/<id>` | DELETE | Apaga a comparação agora (cancela se estiver rodando) |
| `/saude` | GET | `ok`, para monitoramento |

### Privacidade

Os PDFs enviados **ficam no servidor** enquanto a comparação existir, porque o relatório tem, em cada diferença,
um link «Ver nos PDFs» que os abre na página da diferença. Quem tiver o link da comparação consegue abrir os
dois PDFs inteiros, então use o sistema só em rede interna. Se a comparação der erro, os PDFs são apagados na hora.
Os PDFs, o relatório e o estado ficam em `PDFDIFF_DATA_DIR/comparacoes/<id>/` e são removidos juntos depois do
prazo (`PDFDIFF_TTL_HORAS`) ou quando o usuário apaga. O disco usado é de até duas vezes `PDFDIFF_MAX_FILE_MB` por
comparação e não há limite para a quantidade delas dentro do prazo. O `id` tem 128 bits aleatórios; não há
listagem de comparações. Os logs registram id, tamanhos e tempos, nunca o conteúdo dos PDFs.

O link «Ver nos PDFs» só aparece no relatório aberto pelo servidor. No arquivo baixado e no relatório da linha
de comando ele não existe. Ele leva à **página** do PDF, não ao trecho.

### Testes

Sem instalar nada além do `requirements.txt`:

```bash
python -m unittest                              # rápidos, cerca de 6 s
python -m unittest tests.integracao_pdfs_reais  # com PDFs reais (veja abaixo), cerca de 45 s
```

O teste `tests.integracao_pdfs_reais` usa dois PDFs reais, `ITAB.pdf` e `ITAC.pdf`, que ficam **fora do Git**
(`*.pdf` está no `.gitignore`, porque são documentos internos). Copie os dois para a pasta do projeto para rodá-lo;
sem eles o teste é pulado.

## Linha de comando

```bash
python pdf_diff.py --base documento_base.pdf --revisada documento_revisado.pdf --saida relatorio.html --ignorar-paginas 1
```

Opções: `--ignorar-paginas N` (descarta as primeiras páginas; use `1` para pular a capa),
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
