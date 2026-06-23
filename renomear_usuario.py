import sqlite3
from pathlib import Path

PASTA_BASE = Path(__file__).resolve().parent
BANCO = PASTA_BASE / "database.db"

ANTIGO = "ngtnnnz"
NOVO = "hector"

conn = sqlite3.connect(BANCO)
cursor = conn.cursor()

cursor.execute("SELECT usuario FROM usuarios")
usuarios = [row[0] for row in cursor.fetchall()]

print("Usuários encontrados:", usuarios)

if ANTIGO not in usuarios:
    print(f"Usuário antigo não encontrado: {ANTIGO}")
elif NOVO in usuarios:
    print(f"Já existe um usuário chamado: {NOVO}")
else:
    cursor.execute("""
        UPDATE usuarios
        SET usuario = ?
        WHERE usuario = ?
    """, (NOVO, ANTIGO))

    cursor.execute("""
        UPDATE status_bm
        SET usuario = ?
        WHERE usuario = ?
    """, (NOVO, ANTIGO))

    cursor.execute("""
        UPDATE historico
        SET usuario = ?
        WHERE usuario = ?
    """, (NOVO, ANTIGO))

    cursor.execute("""
        UPDATE historico_producao
        SET usuario = ?
        WHERE usuario = ?
    """, (NOVO, ANTIGO))

    conn.commit()
    print(f"Usuário renomeado com sucesso: {ANTIGO} -> {NOVO}")

conn.close()