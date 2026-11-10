"""Funções que rodam dentro de processos filhos (spawn) nos testes."""


def cria_app_no_filho(pasta_dados):
    """Reproduz um processo de comparação que importa o servidor e o inicializa."""
    import servidor
    servidor.PASTA_DADOS = __import__("pathlib").Path(pasta_dados)
    servidor.inicia()


def trabalho_lento(pasta, leitores):
    """Comparação de mentira: cria um neto (como o leitor de páginas) e fica esperando."""
    import subprocess
    import time
    from pathlib import Path

    import servidor
    servidor.novo_grupo()
    neto = subprocess.Popen([__import__("sys").executable, "-c", "import time; time.sleep(120)"])
    (Path(pasta) / "neto.pid").write_text(str(neto.pid))
    servidor.escreve_status(pasta, status="executando", etapa="Lento", progresso=0.1)
    time.sleep(120)
