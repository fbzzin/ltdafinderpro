import json
from pathlib import Path
from datetime import datetime

ARQUIVO = Path("historico_producao.json")
BACKUP = Path("historico_producao_backup_antes_reset.json")

USUARIO = "fabiano"
HOJE = datetime.now().strftime("%Y-%m-%d")

if ARQUIVO.exists():
    BACKUP.write_text(ARQUIVO.read_text(encoding="utf-8"), encoding="utf-8")

novo_historico = {
    HOJE: {
        USUARIO: {
            "BM em Análise": 4
        }
    }
}

ARQUIVO.write_text(
    json.dumps(novo_historico, ensure_ascii=False, indent=4),
    encoding="utf-8"
)

print("Histórico antigo zerado com sucesso.")
print("Backup criado em:", BACKUP)
print("Nova contagem iniciada hoje com 4 BMs em Análise.")