def cria_app_no_filho(data_dir):
    """Reproduz um processo de comparação (spawn) que importa e cria a aplicação."""
    from webapp import create_app
    create_app({"TESTING": True, "DATA_DIR": data_dir})


def trabalho_lento(pasta, workers):
    """Comparação de mentira: cria um neto (como o leitor de páginas) e fica esperando."""
    import subprocess
    import time
    from pathlib import Path

    from webapp import worker
    worker.novo_grupo()
    neto = subprocess.Popen(["sleep", "120"])
    (Path(pasta) / "neto.pid").write_text(str(neto.pid))
    worker.escreve_status(pasta, status="executando", etapa="Lento", progresso=0.1)
    time.sleep(120)
