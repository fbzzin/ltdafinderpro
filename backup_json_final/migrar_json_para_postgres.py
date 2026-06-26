import json
from pathlib import Path
from sqlalchemy import text
from database import engine

PASTA_BASE = Path(__file__).resolve().parent

ARQUIVOS = {
    "perfis_meta": PASTA_BASE / "perfis_meta.json",
    "historico_producao": PASTA_BASE / "historico_producao.json",
    "datas_uso_cnpj": PASTA_BASE / "datas_uso_cnpj.json",
}

def carregar_json(caminho, padrao):
    if not caminho.exists():
        return padrao
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)

with engine.connect() as conn:
    for tabela, caminho in ARQUIVOS.items():
        dados = carregar_json(caminho, [] if tabela == "perfis_meta" else {})

        conn.execute(text(f"DELETE FROM {tabela}"))
        conn.execute(
            text(f"INSERT INTO {tabela} (dados) VALUES (:dados)"),
            {"dados": json.dumps(dados, ensure_ascii=False)}
        )

    conn.commit()

print("Migração concluída com sucesso!")