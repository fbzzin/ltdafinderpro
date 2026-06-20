from db import conectar


def obter_usuarios():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT usuario, senha, tipo
        FROM usuarios
    """)

    usuarios = {}

    for row in cursor.fetchall():
        usuarios[row["usuario"]] = {
            "senha": row["senha"],
            "tipo": row["tipo"]
        }

    conn.close()

    return usuarios


def salvar_usuario(usuario, senha, tipo):

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO usuarios
        (
            usuario,
            senha,
            tipo
        )
        VALUES (?, ?, ?)
    """, (
        usuario,
        senha,
        tipo
    ))

    conn.commit()
    conn.close()


def excluir_usuario(usuario):

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM usuarios
        WHERE usuario = ?
    """, (usuario,))

    conn.commit()
    conn.close()


def obter_favoritos():

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT cnpj
        FROM favoritos
    """)

    favoritos = {
        row["cnpj"]
        for row in cursor.fetchall()
    }

    conn.close()

    return favoritos


def adicionar_favorito(cnpj):

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR IGNORE INTO favoritos
        (
            cnpj
        )
        VALUES (?)
    """, (cnpj,))

    conn.commit()
    conn.close()


def remover_favorito(cnpj):

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM favoritos
        WHERE cnpj = ?
    """, (cnpj,))

    conn.commit()
    conn.close()


def obter_status_bm():

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT usuario, cnpj, status
        FROM status_bm
    """)

    status_geral = {}

    for row in cursor.fetchall():

        usuario = row["usuario"]

        if usuario not in status_geral:
            status_geral[usuario] = {}

        status_geral[usuario][row["cnpj"]] = row["status"]

    conn.close()

    return status_geral


def salvar_status(usuario, cnpj, status):

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM status_bm
        WHERE usuario = ?
        AND cnpj = ?
    """, (
        usuario,
        cnpj
    ))

    cursor.execute("""
        INSERT INTO status_bm
        (
            usuario,
            cnpj,
            status
        )
        VALUES (?, ?, ?)
    """, (
        usuario,
        cnpj,
        status
    ))

    conn.commit()
    conn.close()


def remover_status(usuario, cnpj):

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM status_bm
        WHERE usuario = ?
        AND cnpj = ?
    """, (
        usuario,
        cnpj
    ))

    conn.commit()
    conn.close()


def obter_historico():

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM historico
        ORDER BY id DESC
    """)

    historico = [
        dict(row)
        for row in cursor.fetchall()
    ]

    conn.close()

    return historico