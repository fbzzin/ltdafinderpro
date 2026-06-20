from pathlib import Path
from datetime import datetime
import json

PASTA_BASE = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj")

ARQUIVO_HISTORICO = PASTA_BASE / "historico.json"


def carregar_historico():
    if not ARQUIVO_HISTORICO.exists():
        return []

    try:
        with open(ARQUIVO_HISTORICO, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except:
        return []


def salvar_historico(lista):
    with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as arquivo:
        json.dump(
            lista,
            arquivo,
            ensure_ascii=False,
            indent=4
        )


def registrar_evento(
    usuario,
    cnpj,
    status_antigo,
    status_novo
):
    historico = carregar_historico()

    historico.insert(
        0,
        {
            "data": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "usuario": usuario,
            "cnpj": cnpj,
            "status_antigo": status_antigo,
            "status_novo": status_novo
        }
    )

    salvar_historico(historico)