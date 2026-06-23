from database import engine
from sqlalchemy import text

with engine.connect() as conn:

    conn.execute(text("""
    CREATE TABLE IF NOT EXISTS perfis_meta (
        id SERIAL PRIMARY KEY,
        dados JSONB
    );
    """))

    conn.execute(text("""
    CREATE TABLE IF NOT EXISTS historico_producao (
        id SERIAL PRIMARY KEY,
        dados JSONB
    );
    """))

    conn.execute(text("""
    CREATE TABLE IF NOT EXISTS datas_uso_cnpj (
        id SERIAL PRIMARY KEY,
        dados JSONB
    );
    """))

    conn.commit()

print("Tabelas criadas com sucesso!")