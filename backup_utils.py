from pathlib import Path
from datetime import datetime

PASTA_BASE = Path(__file__).resolve().parent
PASTA_BACKUP = PASTA_BASE / "backup"
PASTA_BACKUP.mkdir(parents=True, exist_ok=True)


def criar_backup():
    """
    Backup antigo por JSON desativado.
    Os dados principais agora estão em SQL/PostgreSQL.
    Mantido para não quebrar chamadas antigas do app.py.
    """
    pasta = PASTA_BACKUP / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    pasta.mkdir(parents=True, exist_ok=True)

    marcador = pasta / "backup_desativado.txt"
    marcador.write_text(
        "Backup por JSON desativado. Dados principais migrados para SQL/PostgreSQL.",
        encoding="utf-8"
    )

    return pasta
