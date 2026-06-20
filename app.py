from flask import Flask, render_template, request, send_file, abort, jsonify, redirect, url_for, session
import pandas as pd
from pathlib import Path
import zipfile, io, math, json
from functools import wraps
from datetime import datetime

from backup_utils import criar_backup
from historico_utils import carregar_historico, registrar_evento
from db import criar_banco
from database_utils import (
    obter_usuarios,
    obter_favoritos,
    obter_status_bm,
    salvar_usuario,
    excluir_usuario,
    adicionar_favorito,
    remover_favorito,
    salvar_status,
    remover_status
    )
app = Flask(__name__)
app.secret_key = "ltdafinder-pro-chave-local"
criar_banco()
PASTA_BASE = Path(__file__).resolve().parent
PASTA_RESULTADOS = PASTA_BASE / "resultados"
PASTA_DOWNLOADS = PASTA_BASE / "downloads"

BASE_FINAL = PASTA_RESULTADOS / "base_final_minerador_cnpj.csv"
MUNICIPIOS_ZIP = PASTA_DOWNLOADS / "Municipios.zip"
FAVORITOS_JSON = PASTA_BASE / "favoritos.json"
STATUS_BM_JSON = PASTA_BASE / "status_bm.json"
USUARIOS_JSON = PASTA_BASE / "usuarios.json"
HISTORICO_PRODUCAO_JSON = PASTA_BASE / "historico_producao.json"

STATUS_PADRAO = "Disponível"

STATUS_OPCOES = [
    "Disponível",
    "BM em Análise",
    "Usado em BM",
    "Verificou 250",
    "Verificou 2k",
    "Verificou 100k",
    "Precisa de mais informações",
    "Restrito",
    "Checkpoint",
    "Descartado"
]

STATUS_SUCESSO = ["Verificou 250", "Verificou 2k", "Verificou 100k"]


def executar_backup():
    try:
        criar_backup()
    except:
        pass


def carregar_json(caminho, padrao):
    if not caminho.exists():
        return padrao
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except:
        return padrao


def salvar_json(caminho, dados):
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=4)


def carregar_usuarios():

    dados = obter_usuarios()

    if "Fabiano" not in dados:

        salvar_usuario(
            "Fabiano",
            "123456",
            "admin"
        )

        dados = obter_usuarios()

    return dados


def usuario_atual():
    return session.get("usuario", "")


def tipo_usuario():
    return session.get("tipo", "equipe")


def login_obrigatorio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "usuario" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


def admin_obrigatorio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "usuario" not in session:
            return redirect(url_for("login"))
        if tipo_usuario() != "admin":
            abort(403)
        return func(*args, **kwargs)
    return wrapper


def carregar_favoritos():
    return obter_favoritos()


def salvar_favoritos(favoritos):
    pass


def carregar_status_bm():
    return obter_status_bm()


def salvar_status_bm(status):
    pass


def carregar_historico_producao():
    dados = carregar_json(HISTORICO_PRODUCAO_JSON, {})
    return dados if isinstance(dados, dict) else {}


def salvar_historico_producao(historico):
    salvar_json(HISTORICO_PRODUCAO_JSON, historico)
    executar_backup()


def data_hoje():
    return datetime.now().strftime("%Y-%m-%d")


def registrar_historico_producao(usuario, status_antigo, status_novo):
    if not usuario:
        return

    if status_antigo == status_novo:
        return

    if status_novo == STATUS_PADRAO:
        return

    historico = carregar_historico_producao()
    hoje = data_hoje()

    if hoje not in historico or not isinstance(historico.get(hoje), dict):
        historico[hoje] = {}

    if usuario not in historico[hoje] or not isinstance(historico[hoje].get(usuario), dict):
        historico[hoje][usuario] = {}

    if status_novo not in historico[hoje][usuario]:
        historico[hoje][usuario][status_novo] = 0

    historico[hoje][usuario][status_novo] += 1
    salvar_historico_producao(historico)


def resumo_historico_do_dia(data=None):
    if data is None:
        data = data_hoje()

    historico = carregar_historico_producao()
    registros_dia = historico.get(data, {})

    contagem = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}
    usuarios = []

    if not isinstance(registros_dia, dict):
        registros_dia = {}

    for usuario, registros in registros_dia.items():
        if not isinstance(registros, dict):
            continue

        total_usuario = 0
        sucesso_usuario = 0
        contagem_usuario = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}

        for status, qtd in registros.items():
            try:
                qtd = int(qtd)
            except:
                qtd = 0

            if status in contagem:
                contagem[status] += qtd
                contagem_usuario[status] += qtd
                total_usuario += qtd

                if status in STATUS_SUCESSO:
                    sucesso_usuario += qtd

        taxa_usuario = (sucesso_usuario / total_usuario * 100) if total_usuario else 0

        usuarios.append({
            "usuario": usuario,
            "contagem": contagem_usuario,
            "total": total_usuario,
            "sucesso": sucesso_usuario,
            "taxa_sucesso": taxa_usuario
        })

    total = sum(contagem.values())
    sucesso = sum(contagem.get(status, 0) for status in STATUS_SUCESSO)
    taxa_sucesso = (sucesso / total * 100) if total else 0

    usuarios = sorted(usuarios, key=lambda item: item["total"], reverse=True)

    return {
        "data": data,
        "usuarios": usuarios,
        "contagem": contagem,
        "total": total,
        "sucesso": sucesso,
        "taxa_sucesso": taxa_sucesso
    }


def formatar_cnpj(cnpj):
    cnpj = str(cnpj).replace(".0", "").zfill(14)
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def limpar_cnpj(cnpj):
    return str(cnpj).replace(".", "").replace("/", "").replace("-", "").replace(".0", "").strip().zfill(14)


def formatar_capital(valor):
    try:
        valor = float(str(valor).replace(",", "."))
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"


def carregar_municipios():
    if not MUNICIPIOS_ZIP.exists():
        return {}

    try:
        with zipfile.ZipFile(MUNICIPIOS_ZIP, "r") as zip_ref:
            nome_csv = zip_ref.namelist()[0]
            with zip_ref.open(nome_csv) as arquivo:
                municipios = pd.read_csv(
                    arquivo,
                    sep=";",
                    header=None,
                    names=["codigo", "nome"],
                    dtype=str,
                    encoding="latin1"
                )
        return dict(zip(municipios["codigo"], municipios["nome"]))
    except:
        return {}


def status_usuario(status_geral, usuario, cnpj):
    return status_geral.get(usuario, {}).get(cnpj, STATUS_PADRAO)


def usuarios_que_usaram(status_geral, cnpj):
    usados = []

    for usuario, registros in status_geral.items():
        if not isinstance(registros, dict):
            continue

        status = registros.get(cnpj)

        if status and status != STATUS_PADRAO:
            usados.append({
                "usuario": usuario,
                "status": status
            })

    return usados


def carregar_base():
    usuario = usuario_atual()
    df = pd.read_csv(BASE_FINAL, dtype=str)

    df["cnpj_limpo"] = df["cnpj"].apply(limpar_cnpj)

    df["capital_social_num"] = (
        df["capital_social"]
        .astype(str)
        .str.replace(",", ".", regex=False)
    )

    df["capital_social_num"] = pd.to_numeric(
        df["capital_social_num"],
        errors="coerce"
    ).fillna(0)

    municipios = carregar_municipios()
    favoritos = carregar_favoritos()
    status_geral = carregar_status_bm()

    df["cnpj_formatado"] = df["cnpj_limpo"].apply(formatar_cnpj)
    df["capital_formatado"] = df["capital_social_num"].apply(formatar_capital)
    df["municipio_nome"] = df["municipio"].map(municipios).fillna(df["municipio"])
    df["favorito"] = df["cnpj_limpo"].isin(favoritos)

    df["status_bm"] = df["cnpj_limpo"].apply(lambda cnpj: status_usuario(status_geral, usuario, cnpj))
    df["bm_utilizada"] = df["status_bm"] != STATUS_PADRAO

    df["usos_globais"] = df["cnpj_limpo"].apply(lambda cnpj: usuarios_que_usaram(status_geral, cnpj))
    df["usado_global"] = df["usos_globais"].apply(lambda usos: len(usos) > 0)
    df["usado_por"] = df["usos_globais"].apply(
        lambda usos: " | ".join([f"{item['usuario']}: {item['status']}" for item in usos])
    )

    if "telefone_formatado" not in df.columns:
        df["telefone_formatado"] = (
            df["ddd1"].fillna("").astype(str).str.replace(".0", "", regex=False)
            + df["telefone1"].fillna("").astype(str).str.replace(".0", "", regex=False)
        )

    for coluna in [
        "telefone_formatado", "email", "nome_socio", "razao_social",
        "nome_fantasia", "sexo_provavel", "categoria_cnae",
        "data_inicio", "cnae_principal", "situacao_cadastral", "uf", "municipio"
    ]:
        if coluna in df.columns:
            df[coluna] = df[coluna].fillna("")

    df["sexo_provavel"] = df["sexo_provavel"].replace("", "Indefinido")
    df["categoria_cnae"] = df["categoria_cnae"].replace("", "Outros")

    return df


def aplicar_filtros(df, form):
    capital_minimo = form.get("capital_minimo", "0")
    uf = form.get("uf", "")
    sexo = form.get("sexo", "")
    categoria = form.get("categoria", "")
    ano_abertura = form.get("ano_abertura", "")
    busca = form.get("busca", "").strip().upper()
    status_bm = form.get("status_bm", "")

    try:
        capital_minimo = float(capital_minimo)
    except:
        capital_minimo = 0

    df = df[df["capital_social_num"] >= capital_minimo]

    if uf:
        df = df[df["uf"] == uf]

    if sexo:
        df = df[df["sexo_provavel"] == sexo]

    if categoria:
        df = df[df["categoria_cnae"] == categoria]

    if status_bm:
        df = df[df["status_bm"] == status_bm]

    if ano_abertura:
        data_corte = ano_abertura + "0101"
        df["data_inicio_limpa"] = df["data_inicio"].astype(str).str.replace(".0", "", regex=False).str.zfill(8)
        df = df[df["data_inicio_limpa"] >= data_corte]

    if busca:
        busca_limpa = busca.replace(".", "").replace("/", "").replace("-", "").replace(" ", "")

        campos_busca = (
            df["cnpj_limpo"].astype(str).str.upper()
            + " " + df["cnpj_formatado"].astype(str).str.upper()
            + " " + df["razao_social"].astype(str).str.upper()
            + " " + df["nome_socio"].astype(str).str.upper()
            + " " + df["email"].astype(str).str.upper()
            + " " + df["telefone_formatado"].astype(str).str.upper()
            + " " + df["municipio_nome"].astype(str).str.upper()
            + " " + df["status_bm"].astype(str).str.upper()
            + " " + df["usado_por"].astype(str).str.upper()
        )

        campos_busca_limpo = (
            campos_busca
            .str.replace(".", "", regex=False)
            .str.replace("/", "", regex=False)
            .str.replace("-", "", regex=False)
            .str.replace(" ", "", regex=False)
        )

        df = df[
            campos_busca.str.contains(busca, na=False) |
            campos_busca_limpo.str.contains(busca_limpa, na=False)
        ]

    return df.sort_values(by="capital_social_num", ascending=False)


def calcular_paginacao(total_registros, pagina_atual, por_pagina):
    total_paginas = max(1, math.ceil(total_registros / por_pagina))
    pagina_atual = max(1, min(pagina_atual, total_paginas))

    inicio = (pagina_atual - 1) * por_pagina
    fim = inicio + por_pagina

    inicio_lista = max(1, pagina_atual - 2)
    fim_lista = min(total_paginas, pagina_atual + 2)

    return {
        "pagina_atual": pagina_atual,
        "por_pagina": por_pagina,
        "total_paginas": total_paginas,
        "inicio": inicio,
        "fim": fim,
        "paginas": list(range(inicio_lista, fim_lista + 1)),
        "tem_anterior": pagina_atual > 1,
        "tem_proxima": pagina_atual < total_paginas,
        "pagina_anterior": pagina_atual - 1,
        "pagina_proxima": pagina_atual + 1
    }


def montar_contexto(df_filtrado, df_base, form=None):
    if form is None:
        form = {}

    total_empresas = len(df_filtrado)
    total_masculino = len(df_filtrado[df_filtrado["sexo_provavel"] == "Masculino"])
    total_feminino = len(df_filtrado[df_filtrado["sexo_provavel"] == "Feminino"])
    total_favoritos = int(df_base["favorito"].sum())
    total_utilizadas = len(df_base[df_base["bm_utilizada"] == True])
    capital_medio = df_filtrado["capital_social_num"].mean() if len(df_filtrado) else 0

    try:
        pagina_atual = int(form.get("pagina", 1))
    except:
        pagina_atual = 1

    try:
        por_pagina = int(form.get("por_pagina", 50))
    except:
        por_pagina = 50

    if por_pagina not in [25, 50, 100, 200]:
        por_pagina = 50

    paginacao = calcular_paginacao(total_empresas, pagina_atual, por_pagina)
    empresas_pagina = df_filtrado.iloc[paginacao["inicio"]:paginacao["fim"]].to_dict(orient="records")

    return {
        "usuario_logado": usuario_atual(),
        "tipo_usuario": tipo_usuario(),
        "total_empresas": total_empresas,
        "total_masculino": total_masculino,
        "total_feminino": total_feminino,
        "total_favoritos": total_favoritos,
        "total_utilizadas": total_utilizadas,
        "capital_medio": capital_medio,
        "ufs": sorted(df_base["uf"].dropna().unique()),
        "sexos": sorted(df_base["sexo_provavel"].dropna().unique()),
        "categorias": sorted(df_base["categoria_cnae"].dropna().unique()),
        "status_opcoes": STATUS_OPCOES,
        "empresas": empresas_pagina,
        "paginacao": paginacao,
        "filtros": {
            "busca": form.get("busca", ""),
            "capital_minimo": form.get("capital_minimo", "0"),
            "uf": form.get("uf", ""),
            "sexo": form.get("sexo", ""),
            "categoria": form.get("categoria", ""),
            "ano_abertura": form.get("ano_abertura", ""),
            "status_bm": form.get("status_bm", ""),
            "por_pagina": str(por_pagina)
        }
    }


def estatisticas_do_usuario(usuario):
    status_geral = carregar_status_bm()
    registros = status_geral.get(usuario, {})

    if not isinstance(registros, dict):
        registros = {}

    contagem = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}

    for status in registros.values():
        if status in contagem:
            contagem[status] += 1

    total = sum(contagem.values())
    sucesso = sum(contagem.get(status, 0) for status in STATUS_SUCESSO)
    taxa_sucesso = (sucesso / total * 100) if total else 0

    return {
        "usuario": usuario,
        "contagem": contagem,
        "total": total,
        "sucesso": sucesso,
        "taxa_sucesso": taxa_sucesso
    }


def estatisticas_gerais():
    usuarios = carregar_usuarios()
    dados = []
    total_geral = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}

    for usuario in usuarios.keys():
        est = estatisticas_do_usuario(usuario)
        dados.append(est)

        for status, qtd in est["contagem"].items():
            total_geral[status] += qtd

    total = sum(total_geral.values())
    sucesso = sum(total_geral.get(status, 0) for status in STATUS_SUCESSO)
    taxa_sucesso = (sucesso / total * 100) if total else 0

    dados = sorted(dados, key=lambda item: item["sucesso"], reverse=True)

    return {
        "usuarios": dados,
        "contagem": total_geral,
        "total": total,
        "sucesso": sucesso,
        "taxa_sucesso": taxa_sucesso
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    erro = ""

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip().lower()
        senha = request.form.get("senha", "").strip()
        usuarios = carregar_usuarios()

        if usuario in usuarios and usuarios[usuario]["senha"] == senha:
            session["usuario"] = usuario
            session["tipo"] = usuarios[usuario].get("tipo", "equipe")
            return redirect(url_for("minerador"))

        erro = "Usuário ou senha inválidos."

    return render_template("login.html", erro=erro)


@app.route("/registrar", methods=["GET", "POST"])
def registrar():
    erro = ""
    sucesso = ""

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip().lower()
        senha = request.form.get("senha", "").strip()
        confirmar = request.form.get("confirmar", "").strip()

        usuarios = carregar_usuarios()

        if not usuario or not senha:
            erro = "Preencha usuário e senha."
        elif len(usuario) < 3:
            erro = "O usuário precisa ter pelo menos 3 caracteres."
        elif senha != confirmar:
            erro = "As senhas não conferem."
        elif usuario in usuarios:
            erro = "Este usuário já existe."
        else:
            usuarios[usuario] = {
                "senha": senha,
                "tipo": "equipe"
            }

            salvar_usuario(
    usuario,
    senha,
    "equipe"
)
            executar_backup()

            try:
                registrar_evento("sistema", "-", "Usuário inexistente", f"Usuário criado: {usuario}")
            except:
                pass

            sucesso = "Conta criada com sucesso. Agora faça login."

    return render_template("registrar.html", erro=erro, sucesso=sucesso)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_obrigatorio
def minerador():
    df = carregar_base()
    df = df.sort_values(by="capital_social_num", ascending=False)
    return render_template("dashboard.html", **montar_contexto(df, df))


@app.route("/master")
@login_obrigatorio
def master():
    df = carregar_base()
    df = df.sort_values(by="capital_social_num", ascending=False)

    contexto = montar_contexto(df, df)
    contexto["estatisticas_gerais"] = estatisticas_gerais()
    contexto["historico_hoje"] = resumo_historico_do_dia()

    return render_template("master.html", **contexto)


@app.route("/historico")
@login_obrigatorio
def historico():
    return render_template(
        "historico.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        historico=carregar_historico()
    )


@app.route("/filtrar", methods=["POST"])
@login_obrigatorio
def filtrar():
    df_base = carregar_base()
    df_filtrado = aplicar_filtros(df_base.copy(), request.form)
    return render_template("dashboard.html", **montar_contexto(df_filtrado, df_base, request.form))


@app.route("/empresa/<cnpj>")
@login_obrigatorio
def empresa(cnpj):
    df = carregar_base()
    cnpj_limpo = limpar_cnpj(cnpj)
    encontrado = df[df["cnpj_limpo"] == cnpj_limpo]

    if encontrado.empty:
        abort(404)

    return render_template(
        "empresa.html",
        empresa=encontrado.iloc[0].to_dict(),
        status_opcoes=STATUS_OPCOES,
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario()
    )


@app.route("/favoritar/<cnpj>", methods=["POST"])
@login_obrigatorio
def favoritar(cnpj):
    cnpj_limpo = limpar_cnpj(cnpj)
    favoritos = carregar_favoritos()

    if cnpj_limpo in favoritos:
        favoritos.remove(cnpj_limpo)
        favorito = False
        acao = "Removido dos favoritos"
    else:
        favoritos.add(cnpj_limpo)
        favorito = True
        acao = "Adicionado aos favoritos"

    if favorito:
        adicionar_favorito(cnpj_limpo)
    else:
        remover_favorito(cnpj_limpo)

    try:
        registrar_evento(
            usuario_atual(),
            formatar_cnpj(cnpj_limpo),
            "-",
            acao
        )
    except:
        pass

    return jsonify({
        "ok": True,
        "cnpj": cnpj_limpo,
        "favorito": favorito,
        "total_favoritos": len(favoritos)
    })


@app.route("/status-bm/<cnpj>", methods=["POST"])
@login_obrigatorio
def atualizar_status_bm(cnpj):
    usuario = usuario_atual()
    cnpj_limpo = limpar_cnpj(cnpj)
    novo_status = request.form.get("status_bm", STATUS_PADRAO)

    if novo_status not in STATUS_OPCOES:
        novo_status = STATUS_PADRAO

    status_geral = carregar_status_bm()

    if usuario not in status_geral or not isinstance(status_geral.get(usuario), dict):
        status_geral[usuario] = {}

    status_antigo = status_geral[usuario].get(cnpj_limpo, STATUS_PADRAO)

    if novo_status == STATUS_PADRAO:
        status_geral[usuario].pop(cnpj_limpo, None)
    else:
        status_geral[usuario][cnpj_limpo] = novo_status

    if novo_status == STATUS_PADRAO:
        remover_status(
            usuario,
            cnpj_limpo
        )
    else:
        salvar_status(
            usuario,
            cnpj_limpo,
            novo_status
        )
    registrar_historico_producao(usuario, status_antigo, novo_status)

    try:
        registrar_evento(usuario, formatar_cnpj(cnpj_limpo), status_antigo, novo_status)
    except:
        pass

    usados = usuarios_que_usaram(status_geral, cnpj_limpo)

    return jsonify({
        "ok": True,
        "cnpj": cnpj_limpo,
        "status_bm": novo_status,
        "bm_utilizada": novo_status != STATUS_PADRAO,
        "usado_global": len(usados) > 0,
        "usado_por": " | ".join([f"{item['usuario']}: {item['status']}" for item in usados])
    })


@app.route("/favoritos")
@login_obrigatorio
def favoritos():
    df_base = carregar_base()
    df = df_base[df_base["favorito"] == True].copy()
    df = df.sort_values(by="capital_social_num", ascending=False)
    return render_template("dashboard.html", **montar_contexto(df, df_base))


@app.route("/usados", methods=["GET", "POST"])
@login_obrigatorio
def usados():
    df_base = carregar_base()
    df = df_base[df_base["bm_utilizada"] == True].copy()

    if request.method == "POST":
        df = aplicar_filtros(df, request.form)
        contexto = montar_contexto(df, df_base, request.form)
    else:
        df = df.sort_values(by="capital_social_num", ascending=False)
        contexto = montar_contexto(df, df_base)

    return render_template("usados.html", **contexto)


@app.route("/estatisticas")
@login_obrigatorio
def estatisticas():
    minha_estatistica = estatisticas_do_usuario(usuario_atual())
    geral = None

    if tipo_usuario() == "admin":
        geral = estatisticas_gerais()

    return render_template(
        "estatisticas.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        minha_estatistica=minha_estatistica,
        estatisticas_gerais=geral,
        status_opcoes=[status for status in STATUS_OPCOES if status != STATUS_PADRAO]
    )


@app.route("/admin", methods=["GET", "POST"])
@admin_obrigatorio
def admin():
    usuarios = carregar_usuarios()
    mensagem = ""
    erro = ""

    if request.method == "POST":
        acao = request.form.get("acao", "")

        if acao == "criar_usuario":
            novo_usuario = request.form.get("usuario", "").strip().lower()
            senha = request.form.get("senha", "").strip()
            tipo = request.form.get("tipo", "equipe").strip()

            if tipo not in ["admin", "equipe"]:
                tipo = "equipe"

            if not novo_usuario or not senha:
                erro = "Preencha usuário e senha."
            elif len(novo_usuario) < 3:
                erro = "O usuário precisa ter pelo menos 3 caracteres."
            elif novo_usuario in usuarios:
                erro = "Este usuário já existe."
            else:
                usuarios[novo_usuario] = {
                    "senha": senha,
                    "tipo": tipo
                }

                salvar_usuario(
    novo_usuario,
    senha,
    tipo
)
                executar_backup()

                try:
                    registrar_evento(usuario_atual(), "-", "Usuário inexistente", f"Usuário criado: {novo_usuario}")
                except:
                    pass

                mensagem = "Usuário criado com sucesso."

        elif acao == "alterar_usuario":
            alvo = request.form.get("usuario", "").strip().lower()
            nova_senha = request.form.get("senha", "").strip()
            novo_tipo = request.form.get("tipo", "equipe").strip()

            if alvo not in usuarios:
                erro = "Usuário não encontrado."
            elif novo_tipo not in ["admin", "equipe"]:
                erro = "Tipo de usuário inválido."
            else:
                tipo_antigo = usuarios[alvo].get("tipo", "equipe")
                usuarios[alvo]["tipo"] = novo_tipo

                if nova_senha:
                    usuarios[alvo]["senha"] = nova_senha

                salvar_usuario(
    alvo,
    usuarios[alvo]["senha"],
    usuarios[alvo]["tipo"]
)
                executar_backup()

                try:
                    registrar_evento(usuario_atual(), "-", f"Tipo: {tipo_antigo}", f"Usuário {alvo} atualizado para {novo_tipo}")
                except:
                    pass

                mensagem = "Usuário atualizado com sucesso."

                if alvo == usuario_atual():
                    session["tipo"] = novo_tipo

        elif acao == "excluir_usuario":
            alvo = request.form.get("usuario", "").strip().lower()

            if alvo not in usuarios:
                erro = "Usuário não encontrado."
            elif alvo == usuario_atual():
                erro = "Você não pode excluir o próprio usuário logado."
            elif alvo == "fabiano":
                erro = "O usuário principal fabiano não pode ser excluído."
            else:
                usuarios.pop(alvo, None)

                status_geral = carregar_status_bm()
                status_geral.pop(alvo, None)

                excluir_usuario(alvo)
                # status do usuário já foi removido da memória local acima
# SQLite: sem ação extra aqui por enquanto
                executar_backup()

                try:
                    registrar_evento(usuario_atual(), "-", f"Usuário existente: {alvo}", "Usuário excluído")
                except:
                    pass

                mensagem = "Usuário excluído com sucesso."

        usuarios = carregar_usuarios()

    estatisticas = estatisticas_gerais()
    historico_hoje = resumo_historico_do_dia()

    return render_template(
        "admin.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        usuarios=usuarios,
        mensagem=mensagem,
        erro=erro,
        estatisticas_gerais=estatisticas,
        historico_hoje=historico_hoje,
        status_opcoes=[status for status in STATUS_OPCOES if status != STATUS_PADRAO]
    )


@app.route("/admin/usuario/<usuario>")
@admin_obrigatorio
def admin_usuario(usuario):
    usuario = usuario.strip().lower()
    usuarios = carregar_usuarios()

    if usuario not in usuarios:
        abort(404)

    estatistica = estatisticas_do_usuario(usuario)
    status_geral = carregar_status_bm()
    registros = status_geral.get(usuario, {})

    empresas = []

    if isinstance(registros, dict) and len(registros) > 0:
        df = carregar_base()

        for cnpj, status in registros.items():
            encontrado = df[df["cnpj_limpo"] == cnpj]

            if encontrado.empty:
                empresas.append({
                    "cnpj_limpo": cnpj,
                    "cnpj_formatado": formatar_cnpj(cnpj),
                    "razao_social": "Empresa não encontrada na base",
                    "capital_formatado": "R$ 0,00",
                    "uf": "",
                    "municipio_nome": "",
                    "status_bm": status
                })
            else:
                item = encontrado.iloc[0].to_dict()
                item["status_bm"] = status
                empresas.append(item)

    return render_template(
        "admin_usuario.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        usuario_alvo=usuario,
        dados_usuario=usuarios[usuario],
        estatistica=estatistica,
        empresas=empresas,
        status_opcoes=STATUS_OPCOES
    )


@app.route("/admin/status/<usuario>/<cnpj>", methods=["POST"])
@admin_obrigatorio
def admin_atualizar_status(usuario, cnpj):
    usuario = usuario.strip().lower()
    usuarios = carregar_usuarios()

    if usuario not in usuarios:
        abort(404)

    cnpj_limpo = limpar_cnpj(cnpj)
    novo_status = request.form.get("status_bm", STATUS_PADRAO)

    if novo_status not in STATUS_OPCOES:
        novo_status = STATUS_PADRAO

    status_geral = carregar_status_bm()

    if usuario not in status_geral or not isinstance(status_geral.get(usuario), dict):
        status_geral[usuario] = {}

    status_antigo = status_geral[usuario].get(cnpj_limpo, STATUS_PADRAO)

    if novo_status == STATUS_PADRAO:
        status_geral[usuario].pop(cnpj_limpo, None)
    else:
        status_geral[usuario][cnpj_limpo] = novo_status

    if novo_status == STATUS_PADRAO:
        remover_status(
            usuario,
            cnpj_limpo
        )
    else:
        salvar_status(
            usuario,
            cnpj_limpo,
            novo_status
        )
    registrar_historico_producao(usuario, status_antigo, novo_status)

    try:
        registrar_evento(f"admin:{usuario_atual()}", formatar_cnpj(cnpj_limpo), status_antigo, novo_status)
    except:
        pass

    return redirect(url_for("admin_usuario", usuario=usuario))


@app.route("/historico-hoje")
@login_obrigatorio
def historico_hoje():
    return jsonify(resumo_historico_do_dia())


@app.route("/exportar", methods=["POST"])
@login_obrigatorio
def exportar():
    df_base = carregar_base()
    df = aplicar_filtros(df_base.copy(), request.form)

    colunas = [
        "cnpj_formatado", "razao_social", "capital_formatado", "uf",
        "municipio_nome", "telefone_formatado", "email", "nome_socio",
        "sexo_provavel", "categoria_cnae", "status_bm", "usado_por",
        "data_inicio", "cnae_principal"
    ]

    df = df[[col for col in colunas if col in df.columns]].copy()

    df.rename(columns={
        "cnpj_formatado": "CNPJ",
        "razao_social": "Razão Social",
        "capital_formatado": "Capital Social",
        "uf": "UF",
        "municipio_nome": "Município",
        "telefone_formatado": "Telefone",
        "email": "Email",
        "nome_socio": "Sócio",
        "sexo_provavel": "Sexo Provável",
        "categoria_cnae": "Categoria CNAE",
        "status_bm": "Meu Status BM",
        "usado_por": "Usado Por",
        "data_inicio": "Data de Abertura",
        "cnae_principal": "CNAE Principal"
    }, inplace=True)

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="LtdaFinder Pro")

    output.seek(0)

    return send_file(
        output,
        download_name="ltdafinder_exportacao.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


if __name__ == "__main__":
    app.run(debug=True)