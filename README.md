# pdf-comparison

Compara duas versões de um PDF (base x revisada) e mostra só o que mudou no conteúdo, num relatório HTML.
Funciona como linha de comando e como sistema web.

- Lê o texto, a posição, a cor e as bordas de tabela de cada página, e compara o **conteúdo**: quebra de linha
  ou de parágrafo diferente não gera diferença.
- Cruza cada diferença com a cor do texto na versão revisada (quem revisa pinta de cor o que mudou).
- Mostra o **título do documento**, lido do cabeçalho das páginas (o nome do arquivo costuma ser outro).
- No sistema web, cada diferença tem um link «Ver nos PDFs» que abre os dois PDFs lado a lado, na página dela.

## Estrutura do projeto

```text
pdf-comparison/
├── servidor.py            # o sistema web (Flask), num arquivo só
├── pdf_diff.py            # a linha de comando; é só um atalho para pdfdiff.cli
├── pdfdiff/               # o motor da comparação, um módulo por etapa
│   ├── __init__.py        # a API pública (comparar_pdfs, ErroComparacao) e as estruturas de dados
│   ├── extracao.py        # PDF -> linhas de texto de cada página (pdfplumber)
│   ├── cabecalho.py       # cabeçalho/rodapé repetidos e título do documento
│   ├── layout.py          # segmentos, blocos (células, colunas) e ordem de leitura
│   ├── paragrafos.py      # blocos -> parágrafos; normalização e sumário
│   ├── comparacao.py      # alinhamento dos parágrafos e diff palavra a palavra -> itens
│   ├── relatorio.py       # itens -> página HTML
│   ├── relatorio_assets.py  # CSS, JavaScript e ícones do relatório
│   ├── comparador.py      # junta as etapas: comparar_pdfs
│   └── cli.py             # os argumentos da linha de comando
├── templates/             # index.html (a página) e pdfs.html (os dois PDFs lado a lado)
├── static/                # app.js e style.css (o design system)
├── tests/                 # testes automáticos
├── requirements.txt
└── data/                  # criada ao rodar o servidor, fora do Git: as comparações
```

`pdf_diff.py` (com sublinhado) e `pdfdiff/` (sem) são coisas diferentes: o primeiro é o comando que você roda, o
segundo é a biblioteca com o motor. O servidor e a linha de comando chamam a mesma função, `pdfdiff.comparar_pdfs`.

### Onde mexer para...

| Quero mudar... | Mexa em |
| --- | --- |
| como o texto da página é lido: colunas, células de tabela, ordem de leitura | `pdfdiff/layout.py` (`VAO_COLUNA`) |
| o que é cabeçalho ou rodapé, ou como o título do documento é achado | `pdfdiff/cabecalho.py` (`MARGEM`, `REPETICAO_MIN`, `TITULO_MIN_LETRAS`) |
| como as linhas viram parágrafos, o tratamento do sumário ou das aspas | `pdfdiff/paragrafos.py` |
| o que conta como diferença, o contexto em volta dela ou o que é «marcado em cor» | `pdfdiff/comparacao.py` (`CONTEXTO`, `MARCADO`) |
| o conteúdo e a aparência do relatório | `pdfdiff/relatorio.py` e `pdfdiff/relatorio_assets.py` |
| limites, fila, tempo limite, rotas e cabeçalhos de segurança | `servidor.py` |
| a aparência da página do sistema | `static/style.css` e `templates/` |

## Bibliotecas externas

Só duas, e nenhuma é opcional:

- **Flask**: o servidor web (o mesmo do `mock_server.py`).
- **pdfplumber**: lê o texto, a posição, a cor e as bordas de tabela dos PDFs. Não há como comparar PDFs sem uma biblioteca que leia PDF.

Todo o resto é biblioteca padrão do Python (3.8 ou mais novo; testado na 3.11). Os testes usam `unittest`, e o
servidor é o próprio do Flask (sem `gunicorn`). O `pip` instala também as dependências dessas duas: 15 pacotes no
total (16 no Windows). A página não carrega nada de fora: sem fonte, ícone, script ou imagem de outro endereço.

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
| `/api/comparacoes/<id>/andamento` | GET | Andamento: `status` (`na_fila`, `executando`, `concluido`, `erro`), `etapa`, `progresso` e, quando concluída, `totais` (contagens, páginas e títulos) |
| `/comparacoes/<id>/relatorio` | GET | Relatório pronto (`?baixar=1` baixa o arquivo) |
| `/comparacoes/<id>/pdfs` | GET | Os dois PDFs lado a lado: `?b=N` é a página na base e `?r=N` na revisada (o card da diferença liga para cá). Sem a página de um lado, esse lado não aparece |
| `/comparacoes/<id>/pdf/<versao>` | GET | Um dos PDFs enviados (`base` ou `revisada`), aberto no navegador |
| `/api/comparacoes/<id>` | DELETE | Apaga a comparação agora (cancela se estiver rodando) |
| `/saude` | GET | `ok`, para monitoramento |

### Como o servidor funciona

Cada comparação tem uma pasta em `PDFDIFF_DATA_DIR/comparacoes/<id>/`, com `meta.json` (nomes e opções),
`status.json` (andamento), `base.pdf`, `revisada.pdf` e, no fim, `relatorio.html`. O `status.json` é gravado de
forma atômica, e é por ele que a página acompanha a comparação (consulta o andamento a cada 1,5 s).

- Cada comparação roda num **processo próprio** (`spawn`), em **grupo de processos próprio**: ao cancelar ou
  estourar o tempo, o servidor encerra o grupo inteiro, leitores de página incluídos.
- Uma **thread por comparação** espera uma vaga (`PDFDIFF_MAX_CONCURRENT`), vigia o tempo limite e grava os erros.
  Quem passa de `PDFDIFF_MAX_CONCURRENT + PDFDIFF_MAX_QUEUE` recebe `429`.
- Uma limpeza a cada 10 minutos apaga as comparações que passaram de `PDFDIFF_TTL_HORAS`. Ao subir, o servidor marca
  como erro as comparações que o servidor anterior deixou rodando.
- O `spawn` **reimporta o `servidor.py`** em cada processo de comparação. Por isso nada ali pode ter efeito
  colateral ao ser importado: `inicia()` só roda no processo principal e o servidor só sobe sob
  `if __name__ == "__main__"`. Qualquer script que use `multiprocessing` com o servidor precisa dessa guarda.

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

### Segurança da página

A página do sistema e o relatório são servidos com uma política de segurança de conteúdo (CSP) estrita: a
página só carrega arquivos próprios (`static/`), sem estilo nem script inline, e o relatório, que é um HTML
autônomo, não carrega nada de fora. O relatório aparece num `iframe` com `sandbox`; o `allow-popups-to-escape-sandbox`
existe para o link «Ver nos PDFs» abrir uma aba normal, onde o visualizador de PDF do navegador funciona.

## Linha de comando

```bash
python pdf_diff.py --base documento_base.pdf --revisada documento_revisado.pdf --saida relatorio.html --ignorar-paginas 1
```

Opções: `--ignorar-paginas N` (descarta as primeiras páginas; use `1` para pular a capa),
`--somente-marcados` (só itens marcados em cor), `--workers N`.

Além do andamento, o comando mostra o título que achou em cada PDF (`Título (base)` e `Título (revisada)`), o que
ajuda a conferir a leitura do cabeçalho com o documento real.

## Como a comparação funciona

A função `pdfdiff.comparar_pdfs` passa cada PDF pelas etapas abaixo, uma por módulo, e depois compara os dois:

```text
PDF ─ extracao ─> linhas de cada página (texto, posição, cor, bordas de tabela)
    ─ cabecalho ─> título do documento; cabeçalhos e rodapés repetidos saem
    ─ layout ─────> segmentos -> blocos (células, colunas) -> ordem de leitura
    ─ paragrafos ─> parágrafos, com aspas normalizadas e sumário sem número de página
base + revisada ─ comparacao ─> itens (alinha os parágrafos iguais; diff palavra a palavra no resto)
itens ─ relatorio ─> HTML autônomo
```

Em detalhe:

1. **Extração** (`extracao.py`): lê as páginas em paralelo (um processo por lote) e devolve, para cada linha, as
   palavras com a cor e a posição, mais as bordas de tabela (retângulos finos) da página.
2. **Cabeçalho** (`cabecalho.py`): uma linha da margem (15% do topo ou da base) que se repete em 30% das páginas é
   cabeçalho ou rodapé e sai. O **título** é o bloco de mais texto do topo que se repete, sem o «Página N de M»
   (blocos com menos de 8 letras, como códigos e siglas, não contam). É uma heurística: confira com o seu documento.
3. **Layout** (`layout.py`): as bordas desenhadas separam as células; sem elas, um vão largo (`VAO_COLUNA`) separa
   as colunas. Os segmentos empilhados formam blocos, que são lidos em faixas, de cima para baixo e da esquerda
   para a direita. Tabela sem borda entre as linhas é lida linha a linha, senão uma linha movida de página viraria
   diferença falsa.
4. **Parágrafos** (`paragrafos.py`): cada bloco vira parágrafo; um parágrafo pode continuar na página seguinte.
   No sumário, o texto é cortado no pontilhado e o número de página é descartado. A numeração de passo solta
   (8.4.1.1) sai do fluxo e é comparada em separado.
5. **Comparação** (`comparacao.py`): alinha os parágrafos iguais e, nos trechos que diferem, faz o diff palavra a
   palavra do texto achatado. Cada grupo de mudanças vira um item (adicionado, removido ou modificado), com um pouco
   de contexto e as páginas nas duas versões. O item é «marcado em cor» se ao menos metade das palavras novas está
   em cor na revisada.
6. **Relatório** (`relatorio.py`): um HTML autônomo, com filtros por tipo e por marcação e um card por diferença.

Ignorado de propósito: diferença só de espaçamento ("d uração" x "duração"), número de página do
sumário e numeração de passo solta (8.4.1.1), que é comparada em separado.

Limitações conhecidas: só compara texto (imagens não); tabelas são lidas célula a célula, mas não
alinhadas linha a linha entre as versões; tabelas de valores numéricos e blocos de assinatura podem
gerar ruído; o título é uma heurística; validado em um único par de documentos.

## Documentação do código

Todo módulo, classe e função tem docstring, em português, e um teste (`tests/test_pacote.py`) confere isso.
O que cada uma recebe e devolve está nas docstrings; as **estruturas de dados** que passam de uma etapa para
a outra (linha, segmento, bloco, parágrafo e item) estão descritas na docstring de `pdfdiff/__init__.py`. As
funções do `static/app.js` têm comentários no formato JSDoc.

## Testes

Sem instalar nada além do `requirements.txt`:

```bash
python -m unittest                              # cerca de 1 minuto (cada comparação sobe processos)
python -m unittest tests.integracao_pdfs_reais  # com PDFs reais (veja abaixo), cerca de 45 s
```

| Arquivo | O que confere |
| --- | --- |
| `tests/test_servidor.py` | Rotas, envio, limites e fila, comparação completa, PDFs guardados e página lado a lado, tempo limite, expiração e o encerramento dos processos (esses, só no Linux) |
| `tests/test_relatorio.py` | O HTML do relatório: cards, filtros, link «Ver nos PDFs», nada vindo de fora, tokens de cor iguais aos da página |
| `tests/test_titulo.py` | A leitura do título no cabeçalho, com PDFs sintéticos |
| `tests/test_pacote.py` | Os pontos de entrada (`pdf_diff.py`, `pdfdiff`) e que tudo tem docstring |
| `tests/integracao_pdfs_reais.py` | O relatório do servidor é idêntico, byte a byte, ao da linha de comando, com PDFs reais |

O teste `tests.integracao_pdfs_reais` usa dois PDFs reais, `ITAB.pdf` e `ITAC.pdf`, que ficam **fora do Git**
(`*.pdf` está no `.gitignore`, porque são documentos internos). Copie os dois para a pasta do projeto para rodá-lo;
sem eles o teste é pulado. Os ajudantes `tests/pdfs.py` (gera PDFs mínimos à mão) e `tests/filho.py` (processos
filhos de mentira) servem aos outros testes.

Se ao mexer no motor você quiser ter certeza de que nada mudou no resultado, gere o relatório de um par de PDFs
antes e depois da mudança e compare os dois arquivos (`cmp relatorio-antes.html relatorio-depois.html`).

### Windows

No Windows, com a pasta `data/` dentro do OneDrive, a troca do `status.json` pode falhar de vez em quando com
`PermissionError` (o OneDrive segura o arquivo), e uma comparação termina com «Erro inesperado». Em Linux isso não
ocorre. Para desenvolver no Windows, aponte `PDFDIFF_DATA_DIR` para uma pasta fora do OneDrive.
