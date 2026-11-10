"""Gerência dos trabalhos: pasta por trabalho, fila, limite de tempo, expiração.

O estado de cada trabalho vive em arquivos (status.json), não em memória. Cada comparação
roda num processo próprio, então pode ser interrompida e não trava o servidor web.
"""
import json
import logging
import multiprocessing
import os
import re
import signal
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import worker

log = logging.getLogger("pdfdiff")
ID_VALIDO = re.compile(r"[0-9a-f]{32}")
TERMINAIS = ("concluido", "erro")


class ServidorOcupado(Exception):
    pass


def encerra(proc, espera=5):
    """Encerra o processo da comparação e todos os filhos dele (o worker cria um grupo próprio)."""
    def sinal(numero):
        try:
            os.killpg(proc.pid, numero)
        except (ProcessLookupError, PermissionError, AttributeError):
            # grupo ainda não criado (o filho acabou de nascer) ou sistema sem grupos
            (proc.terminate if numero == signal.SIGTERM else proc.kill)()
    sinal(signal.SIGTERM)
    proc.join(espera)
    if proc.is_alive():
        sinal(getattr(signal, "SIGKILL", signal.SIGTERM))
        proc.join(espera)


class Jobs:
    def __init__(self, cfg):
        self.pasta = Path(cfg["DATA_DIR"]) / "jobs"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.max_concorrente = cfg["MAX_CONCURRENT"]
        self.max_fila = cfg["MAX_QUEUE"]
        self.timeout_s = cfg["TIMEOUT_S"]
        self.ttl_s = cfg["TTL_S"]
        self.workers = cfg["WORKERS"]
        self._sem = threading.BoundedSemaphore(self.max_concorrente)
        self._trava = threading.Lock()
        self._ativos = 0          # na fila + executando, neste processo
        self._processos = {}      # id -> processo em execução
        self._ctx = multiprocessing.get_context("spawn")

    # ------------------------------------------------------------------ criação
    def pasta_do(self, job_id):
        if not ID_VALIDO.fullmatch(job_id or ""):
            return None
        return self.pasta / job_id

    def reserva_vaga(self):
        with self._trava:
            if self._ativos >= self.max_concorrente + self.max_fila:
                raise ServidorOcupado()
            self._ativos += 1

    def libera_vaga(self):
        with self._trava:
            self._ativos = max(0, self._ativos - 1)

    def novo(self):
        """Cria a pasta de um trabalho novo (a vaga já deve estar reservada)."""
        job_id = uuid.uuid4().hex
        pasta = self.pasta / job_id
        pasta.mkdir()
        return job_id, pasta

    def inicia(self, job_id, meta):
        pasta = self.pasta / job_id
        (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        worker.escreve_status(pasta, status="na_fila", etapa="Na fila", progresso=0.0, criado_em=time.time())
        threading.Thread(target=self._supervisiona, args=(job_id,), name="job-" + job_id[:6], daemon=True).start()

    # -------------------------------------------------------------- supervisão
    def _supervisiona(self, job_id):
        pasta = self.pasta / job_id
        try:
            with self._sem:
                if not pasta.exists():  # apagado enquanto esperava na fila
                    return
                inicio = time.time()
                proc = self._ctx.Process(target=worker.run_job, args=(str(pasta), self.workers),
                                         name="comparacao-" + job_id[:6])
                proc.start()
                self._processos[job_id] = proc
                try:
                    while proc.is_alive():
                        proc.join(0.5)
                        if time.time() - inicio > self.timeout_s and proc.is_alive():
                            encerra(proc)
                            worker.escreve_status(pasta, status="erro", etapa="Erro", erro=(
                                "A comparação passou do tempo limite (%d minutos) e foi interrompida. "
                                "Tente com `ignorar páginas` ou arquivos menores." % (self.timeout_s // 60)))
                            break
                finally:
                    self._processos.pop(job_id, None)
                if pasta.exists() and self.estado(job_id).get("status") not in TERMINAIS:
                    worker.escreve_status(pasta, status="erro", etapa="Erro", erro=(
                        "O processamento terminou de forma inesperada (talvez faltou memória). "
                        "Tente novamente com arquivos menores."))
                log.info("trabalho %s terminou: %s em %.1fs", job_id[:8],
                         self.estado(job_id).get("status"), time.time() - inicio)
        except Exception:
            log.exception("falha ao supervisionar o trabalho %s", job_id[:8])
            worker.escreve_status(pasta, status="erro", etapa="Erro", erro="Erro interno do servidor.")
        finally:
            for nome in ("base.pdf", "revisada.pdf"):  # os PDFs enviados não ficam guardados
                try:
                    (pasta / nome).unlink()
                except OSError:
                    pass
            self.libera_vaga()

    # ----------------------------------------------------------------- consulta
    def estado(self, job_id):
        pasta = self.pasta_do(job_id)
        if pasta is None:
            return {}
        try:
            estado = json.loads((pasta / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        try:
            meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
            estado["nomes"] = {"base": meta["nome_base"], "revisada": meta["nome_revisada"]}
        except (OSError, ValueError, KeyError):
            pass
        if "criado_em" in estado:
            estado["expira_em"] = estado["criado_em"] + self.ttl_s
        return estado

    # ------------------------------------------------------------------ remoção
    def remove(self, job_id):
        pasta = self.pasta_do(job_id)
        if pasta is None or not pasta.exists():
            return False
        proc = self._processos.get(job_id)
        if proc is not None and proc.is_alive():
            encerra(proc)
        shutil.rmtree(pasta, ignore_errors=True)
        return True

    def limpa_expirados(self):
        agora, removidos = time.time(), 0
        for pasta in self.pasta.iterdir():
            if not (pasta.is_dir() and ID_VALIDO.fullmatch(pasta.name)):
                continue
            try:
                criado = json.loads((pasta / "status.json").read_text(encoding="utf-8")).get("criado_em")
            except (OSError, ValueError):
                criado = None
            if criado is None:  # pasta sem status (envio interrompido): usa a data da pasta
                criado = pasta.stat().st_mtime
            if agora - criado > self.ttl_s and pasta.name not in self._processos:
                shutil.rmtree(pasta, ignore_errors=True)
                removidos += 1
        if removidos:
            log.info("removidos %d trabalhos expirados", removidos)
        return removidos

    def recupera(self):
        """Ao iniciar: trabalhos que estavam rodando morreram com o servidor anterior."""
        for pasta in self.pasta.iterdir():
            if pasta.is_dir() and ID_VALIDO.fullmatch(pasta.name):
                if self.estado(pasta.name).get("status") in ("na_fila", "executando"):
                    worker.escreve_status(pasta, status="erro", etapa="Erro", erro=(
                        "O servidor foi reiniciado durante a comparação. Envie os arquivos novamente."))
                    for nome in ("base.pdf", "revisada.pdf"):
                        (pasta / nome).unlink(missing_ok=True)

    def inicia_limpeza_periodica(self, intervalo_s=600):
        def ciclo():
            while True:
                time.sleep(intervalo_s)
                try:
                    self.limpa_expirados()
                except Exception:
                    log.exception("falha na limpeza de trabalhos expirados")
        threading.Thread(target=ciclo, name="limpeza", daemon=True).start()
