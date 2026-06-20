from pathlib import Path
from datetime import datetime
import shutil

PASTA_BASE = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj")

PASTA_BACKUP = PASTA_BASE / "backup"
PASTA_BACKUP.mkdir(exist_ok=True)

ARQUIVOS = [
    PASTA_BASE / "usuarios.json",
    PASTA_BASE / "favoritos.json",
    PASTA_BASE / "status_bm.json"
]


def criar_backup():
    data = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    pasta_destino = PASTA_BACKUP / data
    pasta_destino.mkdir(exist_ok=True)

    for arquivo in ARQUIVOS:

        if arquivo.exists():
            shutil.copy2(
                arquivo,
                pasta_destino / arquivo.name
            )