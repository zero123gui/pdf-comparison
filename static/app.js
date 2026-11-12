(() => {
  "use strict";
  const $ = (seletor) => document.querySelector(seletor);
  const maxMb = Number(document.body.dataset.maxMb);
  const telas = { envio: $("#tela-envio"), andamento: $("#tela-andamento"), relatorio: $("#tela-relatorio") };
  const arquivos = { base: null, revisada: null };
  let idComparacao = null;  // id da comparação em andamento
  let temporizador = null;
  let falhasSeguidas = 0;

  function mostra(nome) {
    Object.entries(telas).forEach(([k, el]) => { el.hidden = k !== nome; });
    // quem usa leitor de tela ouve o título da tela nova
    if (nome !== "envio") telas[nome].querySelector("h1").focus({ preventScroll: true });
  }
  function aviso(texto, informativo) {
    const el = $("#aviso");
    $("#aviso-texto").textContent = texto || "";
    el.hidden = !texto;
    el.classList.toggle("info", Boolean(informativo));
    el.setAttribute("role", informativo ? "status" : "alert");
  }
  function tamanho(bytes) {
    return bytes >= 1048576 ? (bytes / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(bytes / 1024)) + " KB";
  }
  function plural(n, singular, varios) {
    return n + " " + (n === 1 ? singular : varios);
  }
  // botão com indicador de carregamento (aria-busy); o clique é ignorado enquanto ele estiver ocupado
  function ocupado(botao, sim) {
    if (sim) botao.setAttribute("aria-busy", "true"); else botao.removeAttribute("aria-busy");
  }
  const ocupadoAgora = (botao) => botao.getAttribute("aria-busy") === "true";

  // ------------------------------------------------------------------ escolha dos arquivos
  function atualizaDica() {
    let texto = "";
    if (!arquivos.base && !arquivos.revisada) texto = "Escolha a versão base e a versão revisada.";
    else if (!arquivos.base) texto = "Falta a versão base.";
    else if (!arquivos.revisada) texto = "Falta a versão revisada.";
    $("#dica-comparar").textContent = texto;
    $("#dica-comparar").hidden = !texto;
  }

  function escolhe(campo, arquivo) {
    const zona = $("#zona-" + campo);
    const estado = zona.querySelector(".zona-estado");
    estado.textContent = "";
    zona.classList.remove("cheia");
    arquivos[campo] = null;
    if (arquivo) {
      if (!/\.pdf$/i.test(arquivo.name)) {
        aviso("«" + arquivo.name + "» não é um PDF. Escolha um arquivo com extensão .pdf.");
      } else if (arquivo.size > maxMb * 1048576) {
        aviso("«" + arquivo.name + "» tem " + tamanho(arquivo.size) + ", e o limite é " + maxMb + " MB por arquivo.");
      } else {
        aviso("");
        arquivos[campo] = arquivo;
        const nome = document.createElement("strong");
        nome.textContent = arquivo.name;
        const peso = document.createElement("span");
        peso.className = "tamanho";
        peso.textContent = tamanho(arquivo.size);
        estado.append(nome, peso);
        zona.classList.add("cheia");
      }
    }
    $("#btn-comparar").disabled = !(arquivos.base && arquivos.revisada);
    atualizaDica();
  }

  ["base", "revisada"].forEach((campo) => {
    const entrada = $("#arq-" + campo);
    const zona = $("#zona-" + campo);
    entrada.addEventListener("change", () => escolhe(campo, entrada.files[0] || null));
    ["dragenter", "dragover"].forEach((ev) => zona.addEventListener(ev, (e) => { e.preventDefault(); zona.classList.add("arrastando"); }));
    ["dragleave", "drop"].forEach((ev) => zona.addEventListener(ev, () => zona.classList.remove("arrastando")));
    zona.addEventListener("drop", (e) => {
      e.preventDefault();
      const arquivo = e.dataTransfer.files[0];
      if (arquivo) {
        try { const lista = new DataTransfer(); lista.items.add(arquivo); entrada.files = lista.files; } catch (_) { /* navegador sem suporte */ }
        escolhe(campo, arquivo);
      }
    });
  });

  // ---------------------------------------------------------------------------------- envio
  function andamento(etapa, progresso) {
    $("#etapa").textContent = etapa;
    const barra = $("#barra");
    if (progresso === null) {  // sem número ainda (na fila): a barra fica em movimento, sem valor
      barra.removeAttribute("value");
      $("#pct").textContent = "";
    } else {
      barra.value = progresso;
      $("#pct").textContent = progresso + "%";
    }
  }
  function titulo(nomes) {
    $("#titulo-andamento").textContent = nomes && nomes.base && nomes.revisada
      ? "Comparando «" + nomes.base + "» com «" + nomes.revisada + "»"
      : "Comparando os documentos";
  }

  $("#formulario").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!(arquivos.base && arquivos.revisada)) return;
    aviso("");
    const dados = new FormData();
    dados.append("base", arquivos.base);
    dados.append("revisada", arquivos.revisada);
    dados.append("ignorar_paginas", $("#ignorar").value || "0");
    if ($("#somente").checked) dados.append("somente_marcados", "1");

    titulo({ base: arquivos.base.name, revisada: arquivos.revisada.name });
    mostra("andamento");
    andamento("Enviando os arquivos…", 0);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/comparacoes");
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) andamento("Enviando os arquivos…", Math.round((ev.loaded / ev.total) * 100));
    };
    xhr.onload = () => {
      let corpo = {};
      try { corpo = JSON.parse(xhr.responseText); } catch (_) { /* resposta que não é JSON */ }
      if (xhr.status === 202 && corpo.id) {
        acompanha(corpo.id);
      } else {
        volta(corpo.erro || "Não foi possível enviar os arquivos (erro " + xhr.status + ").");
      }
    };
    xhr.onerror = () => volta("Sem conexão com o servidor. Verifique a rede e tente de novo.");
    xhr.send(dados);
  });

  function volta(mensagem, informativo) {
    clearTimeout(temporizador);
    idComparacao = null;
    history.replaceState(null, "", location.pathname);
    mostra("envio");
    aviso(mensagem, informativo);
  }

  // ------------------------------------------------------------------------------ andamento
  function acompanha(id) {
    idComparacao = id;
    falhasSeguidas = 0;
    history.replaceState(null, "", "?comparacao=" + id);
    mostra("andamento");
    consulta();
  }

  async function consulta() {
    const id = idComparacao;
    if (!id) return;
    try {
      const resposta = await fetch("/api/comparacoes/" + id + "/andamento", { cache: "no-store" });
      if (id !== idComparacao) return;
      const corpo = await resposta.json().catch(() => ({}));
      if (resposta.status === 404) return volta(corpo.erro || "Esta comparação não existe mais.");
      falhasSeguidas = 0;
      if (corpo.status === "concluido") return mostraRelatorio(corpo);
      if (corpo.status === "erro") return volta(corpo.erro || "A comparação falhou.");
      titulo(corpo.nomes);
      if (corpo.status === "na_fila") andamento("Na fila, esperando uma vaga…", null);
      else andamento(corpo.etapa || "Comparando…", Math.round((corpo.progresso || 0) * 100));
    } catch (_) {
      falhasSeguidas += 1;
      if (falhasSeguidas >= 5) return volta("Perdi a conexão com o servidor. Abra o mesmo endereço mais tarde para ver o resultado.");
      $("#etapa").textContent = "Sem resposta do servidor, tentando de novo…";
    }
    temporizador = setTimeout(consulta, 1500);
  }

  $("#btn-cancelar").addEventListener("click", async () => {
    const botao = $("#btn-cancelar");
    if (ocupadoAgora(botao)) return;
    const id = idComparacao;
    clearTimeout(temporizador);
    ocupado(botao, true);
    if (id) await fetch("/api/comparacoes/" + id, { method: "DELETE" }).catch(() => {});
    ocupado(botao, false);
    volta("");
  });

  // ------------------------------------------------------------------------------ relatório
  function mostraRelatorio(estado) {
    clearTimeout(temporizador);
    const t = estado.totais || {};
    const nomes = estado.nomes || {};
    const itens = t.itens || 0;
    // o título do documento vem de dentro dos PDFs (cabeçalho das páginas); o nome do arquivo é outro
    $("#doc-titulo").textContent = t.titulo || "";
    $("#doc-titulo").hidden = !t.titulo;
    $("#titulo-relatorio").textContent = itens
      ? plural(itens, "diferença", "diferenças") + " entre as duas versões"
      : "Nenhuma diferença entre as duas versões";
    // o número de diferenças já está no título; o resumo traz o resto
    const partes = [];
    if (nomes.base && nomes.revisada) partes.push(nomes.base + " → " + nomes.revisada);
    if (itens) partes.push((t.marcados || 0) + " marcadas em cor");
    if (t.paginas_base) partes.push(t.paginas_base + " → " + t.paginas_revisada + " páginas");
    $("#resumo").textContent = partes.join(" · ");
    if (estado.expira_em) {
      $("#validade").textContent = "Disponível até " + new Date(estado.expira_em * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) + ".";
    }
    const url = "/comparacoes/" + idComparacao + "/relatorio";
    $("#quadro").src = url;
    $("#lnk-aba").href = url;
    $("#lnk-baixar").href = url + "?baixar=1";
    aviso("");
    mostra("relatorio");
  }

  $("#btn-nova").addEventListener("click", () => {
    $("#quadro").removeAttribute("src");
    volta("");
  });
  $("#btn-apagar").addEventListener("click", async () => {
    const botao = $("#btn-apagar");
    if (ocupadoAgora(botao) || !idComparacao || !confirm("Apagar este relatório do servidor agora? O link deixa de funcionar.")) return;
    ocupado(botao, true);
    await fetch("/api/comparacoes/" + idComparacao, { method: "DELETE" }).catch(() => {});
    ocupado(botao, false);
    $("#quadro").removeAttribute("src");
    volta("O relatório foi apagado do servidor.", true);
  });

  // quem abre ou atualiza a página com ?comparacao=<id> volta à comparação
  const id = new URLSearchParams(location.search).get("comparacao");
  if (id && /^[0-9a-f]{32}$/.test(id)) acompanha(id);
})();
