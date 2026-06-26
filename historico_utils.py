import json
from datetime import datetime
from sqlalchemy import text
from database import engine


def garantir_tabela_historico():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY,
                dados JSONB NOT NULL
            )
        """))
        conn.commit()


def carregar_historico():
    garantir_tabela_historico()

    try:
        with engine.connect() as conn:
            resultado = conn.execute(text("SELECT dados FROM historico LIMIT 1")).fetchone()

        if not resultado:
            return []

        dados = resultado[0]

        if isinstance(dados, str):
            dados = json.loads(dados)

        return dados if isinstance(dados, list) else []

    except Exception:
        return []


def salvar_historico(lista):
    garantir_tabela_historico()

    if not isinstance(lista, list):
        lista = []

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM historico"))
        conn.execute(
            text("INSERT INTO historico (dados) VALUES (:dados)"),
            {"dados": json.dumps(lista, ensure_ascii=False)}
        )
        conn.commit()


def registrar_evento(usuario, cnpj, status_antigo, status_novo):
    historico = carregar_historico()

    historico.insert(
        0,
        {
            "data": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "usuario": usuario,
            "cnpj": cnpj,
            "status_antigo": status_antigo,
            "status_novo": status_novo
        }
    )

    salvar_historico(historico)
