(() => {
  "use strict";
  const $ = (seletor) => document.querySelector(seletor);
  const maxMb = Number(document.body.dataset.maxMb);
  const telas = { envio: $("#tela-envio"), andamento: $("#tela-andamento"), relatorio: $("#tela-relatorio") };
  const arquivos = { base: null, revisada: null };
  let trabalho = null;     // id do trabalho em andamento
  let temporizador = null;
  let falhasSeguidas = 0;

  function mostra(nome) {
    Object.entries(telas).forEach(([k, el]) => { el.hidden = k !== nome; });
  }
  function aviso(texto, informativo) {
    const el = $("#aviso");
    el.textContent = texto || "";
    el.hidden = !texto;
    el.classList.toggle("info", Boolean(informativo));
    el.setAttribute("role", informativo ? "status" : "alert");
  }
  function tamanho(bytes) {
    return bytes >= 1048576 ? (bytes / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  // ------------------------------------------------------------------ escolha dos arquivos
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
        peso.textContent = " (" + tamanho(arquivo.size) + ")";
        estado.append(nome, peso);
        zona.classList.add("cheia");
      }
    }
    $("#btn-comparar").disabled = !(arquivos.base && arquivos.revisada);
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
  $("#formulario").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!(arquivos.base && arquivos.revisada)) return;
    aviso("");
    const dados = new FormData();
    dados.append("base", arquivos.base);
    dados.append("revisada", arquivos.revisada);
    dados.append("ignorar_paginas", $("#ignorar").value || "0");
    if ($("#somente").checked) dados.append("somente_marcados", "1");

    mostra("andamento");
    $("#etapa").textContent = "Enviando os arquivos…";
    $("#barra").value = 0;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        const pct = Math.round((ev.loaded / ev.total) * 100);
        $("#barra").value = pct;
        $("#etapa").textContent = "Enviando os arquivos… " + pct + "%";
      }
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
    trabalho = null;
    history.replaceState(null, "", location.pathname);
    mostra("envio");
    aviso(mensagem, informativo);
  }

  // ------------------------------------------------------------------------------ andamento
  function acompanha(id) {
    trabalho = id;
    falhasSeguidas = 0;
    history.replaceState(null, "", "?job=" + id);
    mostra("andamento");
    consulta();
  }

  async function consulta() {
    const id = trabalho;
    if (!id) return;
    try {
      const resposta = await fetch("/api/jobs/" + id, { cache: "no-store" });
      if (id !== trabalho) return;
      const corpo = await resposta.json().catch(() => ({}));
      if (resposta.status === 404) return volta(corpo.erro || "Este trabalho não existe mais.");
      falhasSeguidas = 0;
      if (corpo.status === "concluido") return mostraRelatorio(corpo);
      if (corpo.status === "erro") return volta(corpo.erro || "A comparação falhou.");
      $("#etapa").textContent = corpo.etapa || "Comparando…";
      $("#barra").value = Math.round((corpo.progresso || 0) * 100);
    } catch (_) {
      falhasSeguidas += 1;
      if (falhasSeguidas >= 5) return volta("Perdi a conexão com o servidor. Abra o mesmo endereço mais tarde para ver o resultado.");
      $("#etapa").textContent = "Sem resposta do servidor, tentando de novo…";
    }
    temporizador = setTimeout(consulta, 1500);
  }

  $("#btn-cancelar").addEventListener("click", async () => {
    const id = trabalho;
    clearTimeout(temporizador);
    if (id) await fetch("/api/jobs/" + id, { method: "DELETE" }).catch(() => {});
    volta("");
  });

  // ------------------------------------------------------------------------------ relatório
  function mostraRelatorio(estado) {
    clearTimeout(temporizador);
    const t = estado.totais || {};
    const nomes = estado.nomes || {};
    const partes = [];
    if (nomes.base && nomes.revisada) partes.push(nomes.base + " → " + nomes.revisada);
    partes.push((t.itens || 0) + " diferenças");
    partes.push((t.marcados || 0) + " marcadas em cor");
    if (t.paginas_base) partes.push(t.paginas_base + " → " + t.paginas_revisada + " páginas");
    $("#resumo").textContent = partes.join(" · ");
    if (estado.expira_em) {
      $("#validade").textContent = "Disponível até " + new Date(estado.expira_em * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) + ".";
    }
    const url = "/jobs/" + trabalho + "/relatorio";
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
    if (!trabalho || !confirm("Apagar este relatório do servidor agora? O link deixa de funcionar.")) return;
    await fetch("/api/jobs/" + trabalho, { method: "DELETE" }).catch(() => {});
    $("#quadro").removeAttribute("src");
    volta("O relatório foi apagado do servidor.", true);
  });

  // quem abre ou atualiza a página com ?job=<id> volta ao trabalho
  const id = new URLSearchParams(location.search).get("job");
  if (id && /^[0-9a-f]{32}$/.test(id)) acompanha(id);
})();
