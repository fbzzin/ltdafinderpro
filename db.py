import sqlite3
from pathlib import Path

PASTA_BASE = Path(__file__).resolve().parent
BANCO = PASTA_BASE / "database.db"


def conectar():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


def criar_banco():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        usuario TEXT PRIMARY KEY,
        senha TEXT NOT NULL,
        tipo TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS favoritos (
        cnpj TEXT PRIMARY KEY
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS status_bm (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT NOT NULL,
        cnpj TEXT NOT NULL,
        status TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_evento TEXT,
        usuario TEXT,
        cnpj TEXT,
        status_antigo TEXT,
        status_novo TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS historico_producao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_evento TEXT,
        usuario TEXT,
        status TEXT
    )
    """)

    conn.commit()
    conn.close()


def banco_existe():
    return BANCO.exists()