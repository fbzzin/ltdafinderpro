import json
from pathlib import Path
from db import conectar, criar_banco

PASTA_BASE = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj")

USUARIOS_JSON = PASTA_BASE / "usuarios.json"
FAVORITOS_JSON = PASTA_BASE / "favoritos.json"
STATUS_BM_JSON = PASTA_BASE / "status_bm.json"
HISTORICO_JSON = PASTA_BASE / "historico.json"


def carregar_json(caminho, padrao):
    if not caminho.exists():
        return padrao

    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except:
        return padrao


def migrar_usuarios():
    usuarios = carregar_json(USUARIOS_JSON, {})
    conn = conectar()
    cursor = conn.cursor()

    for usuario, dados in usuarios.items():
        cursor.execute("""
            INSERT OR REPLACE INTO usuarios
            (usuario, senha, tipo)
            VALUES (?, ?, ?)
        """, (
            usuario,
            dados.get("senha", ""),
            dados.get("tipo", "equipe")
        ))

    conn.commit()
    conn.close()

    print("Usuários migrados.")


def migrar_favoritos():
    favoritos = carregar_json(FAVORITOS_JSON, [])

    conn = conectar()
    cursor = conn.cursor()

    for cnpj in favoritos:
        cursor.execute("""
            INSERT OR REPLACE INTO favoritos
            (cnpj)
            VALUES (?)
        """, (cnpj,))

    conn.commit()
    conn.close()

    print("Favoritos migrados.")


def migrar_status_bm():
    status_geral = carregar_json(STATUS_BM_JSON, {})

    conn = conectar()
    cursor = conn.cursor()

    for usuario, registros in status_geral.items():

        if not isinstance(registros, dict):
            continue

        for cnpj, status in registros.items():

            cursor.execute("""
                INSERT INTO status_bm
                (usuario, cnpj, status)
                VALUES (?, ?, ?)
            """, (
                usuario,
                cnpj,
                status
            ))

    conn.commit()
    conn.close()

    print("Status BM migrados.")


def migrar_historico():
    historico = carregar_json(HISTORICO_JSON, [])

    conn = conectar()
    cursor = conn.cursor()

    for item in historico:

        cursor.execute("""
            INSERT INTO historico
            (
                data_evento,
                usuario,
                cnpj,
                status_antigo,
                status_novo
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            item.get("data", ""),
            item.get("usuario", ""),
            item.get("cnpj", ""),
            item.get("status_antigo", ""),
            item.get("status_novo", "")
        ))

    conn.commit()
    conn.close()

    print("Histórico migrado.")


if __name__ == "__main__":

    criar_banco()

    migrar_usuarios()
    migrar_favoritos()
    migrar_status_bm()
    migrar_historico()

    print()
    print("Migração concluída com sucesso.")