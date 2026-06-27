from flask import Flask, render_template, request, send_file, abort, jsonify, redirect, url_for, session
import pandas as pd
from pathlib import Path
import zipfile, io, math, json, re, unicodedata, os
from functools import wraps
from datetime import datetime, timedelta
from html import escape
import requests
from sqlalchemy import text
from database import engine

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
CNAES_ZIP = PASTA_DOWNLOADS / "Cnaes.zip"
HISTORICO_PRODUCAO_JSON = PASTA_BASE / "historico_producao.json"
PERFIS_META_JSON = PASTA_BASE / "perfis_meta.json"
DATA_USO_CNPJ_JSON = PASTA_BASE / "datas_uso_cnpj.json"

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
STATUS_NEGATIVOS = ["Restrito", "Checkpoint", "Descartado", "Precisa de mais informações"]


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

    if "Fabiano" not in dados and "fabiano" not in dados:
        salvar_usuario("fabiano", "123456", "admin")
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


def carregar_status_bm():
    return obter_status_bm()


def carregar_dados_postgres(tabela, padrao):
    try:
        with engine.connect() as conn:
            resultado = conn.execute(
                text(f"""
                SELECT dados
                FROM {tabela}
                ORDER BY id DESC
                LIMIT 1
                """)
            ).fetchone()

        if not resultado:
            return padrao

        dados = resultado[0]

        if isinstance(dados, str):
            dados = json.loads(dados)

        if isinstance(padrao, dict) and isinstance(dados, dict):
            return dados

        if isinstance(padrao, list) and isinstance(dados, list):
            return dados

        return padrao

    except Exception:
        return padrao


def salvar_dados_postgres(tabela, dados):
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM {tabela}"))
        conn.execute(
            text(f"INSERT INTO {tabela} (dados) VALUES (:dados)"),
            {"dados": json.dumps(dados, ensure_ascii=False)}
        )
        conn.commit()


def carregar_historico_producao():
    return carregar_dados_postgres("historico_producao", {})


def salvar_historico_producao(historico):
    salvar_dados_postgres("historico_producao", historico)


def carregar_datas_uso_cnpj():
    return carregar_dados_postgres("datas_uso_cnpj", {})


def salvar_datas_uso_cnpj(dados):
    salvar_dados_postgres("datas_uso_cnpj", dados)


def registrar_data_uso_cnpj(usuario, cnpj, status):
    if not usuario or not cnpj or status == STATUS_PADRAO:
        return

    cnpj_limpo = limpar_cnpj(cnpj)
    dados = carregar_datas_uso_cnpj()

    if usuario not in dados or not isinstance(dados.get(usuario), dict):
        dados[usuario] = {}

    if cnpj_limpo not in dados[usuario]:
        agora = datetime.now()
        dados[usuario][cnpj_limpo] = {
            "data_iso": agora.strftime("%Y-%m-%d"),
            "data_hora": agora.strftime("%d/%m/%Y %H:%M"),
            "status_inicial": status
        }
        salvar_datas_uso_cnpj(dados)


def remover_data_uso_cnpj(usuario, cnpj):
    cnpj_limpo = limpar_cnpj(cnpj)
    dados = carregar_datas_uso_cnpj()

    if usuario in dados and isinstance(dados.get(usuario), dict):
        dados[usuario].pop(cnpj_limpo, None)
        salvar_datas_uso_cnpj(dados)


def obter_data_uso_cnpj_usuario(dados, usuario, cnpj):
    cnpj_limpo = limpar_cnpj(cnpj)
    item = dados.get(usuario, {}).get(cnpj_limpo, {}) if isinstance(dados, dict) else {}

    if isinstance(item, dict):
        return item.get("data_hora", "") or "Não registrada"

    if isinstance(item, str) and item.strip():
        return item

    return "Não registrada"


def obter_data_iso_uso_cnpj_usuario(dados, usuario, cnpj):
    cnpj_limpo = limpar_cnpj(cnpj)
    item = dados.get(usuario, {}).get(cnpj_limpo, {}) if isinstance(dados, dict) else {}

    if isinstance(item, dict):
        return item.get("data_iso", "")

    return ""


def data_perfil_para_iso(perfil):
    data_iso = str(perfil.get("criado_em_iso", "")).strip()

    if data_iso:
        return data_iso

    criado_em = str(perfil.get("criado_em", "")).strip()

    for formato in ["%d/%m/%Y %H:%M", "%d/%m/%Y"]:
        try:
            return datetime.strptime(criado_em, formato).strftime("%Y-%m-%d")
        except:
            pass

    return ""


def perfil_dentro_periodo(perfil, data_inicio="", data_fim=""):
    data_perfil = data_perfil_para_iso(perfil)

    if not data_perfil:
        return True

    if data_inicio and data_perfil < data_inicio:
        return False

    if data_fim and data_perfil > data_fim:
        return False

    return True



def carregar_perfis_meta():
    return carregar_dados_postgres("perfis_meta", [])


def salvar_perfis_meta(perfis):
    salvar_dados_postgres("perfis_meta", perfis)


def gerar_id_perfil():
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


def cnpjs_vinculados_perfis():
    vinculados = set()

    for perfil in carregar_perfis_meta():
        cnpj = str(perfil.get("cnpj_limpo", "")).strip()

        if cnpj:
            vinculados.add(cnpj)

    return vinculados


def carregar_base_leve_para_perfis(cnpjs_interesse=None, usuario=None):
    if usuario is None:
        usuario = usuario_atual()

    colunas_necessarias = [
        "cnpj",
        "razao_social",
        "uf",
        "municipio",
        "capital_social"
    ]

    try:
        cabecalho = pd.read_csv(BASE_FINAL, dtype=str, nrows=0).columns.tolist()
        colunas_existentes = [col for col in colunas_necessarias if col in cabecalho]

        if "cnpj" not in colunas_existentes:
            return pd.DataFrame()

        df = pd.read_csv(BASE_FINAL, dtype=str, usecols=colunas_existentes)
    except:
        try:
            df = pd.read_csv(BASE_FINAL, dtype=str)
        except:
            return pd.DataFrame()

    df["cnpj_limpo"] = df["cnpj"].apply(limpar_cnpj)

    if cnpjs_interesse is not None:
        cnpjs_interesse = {limpar_cnpj(cnpj) for cnpj in cnpjs_interesse if str(cnpj).strip()}
        df = df[df["cnpj_limpo"].isin(cnpjs_interesse)].copy()

    status_geral = carregar_status_bm()
    datas_uso = carregar_datas_uso_cnpj()
    municipios = carregar_municipios()

    df["cnpj_formatado"] = df["cnpj_limpo"].apply(formatar_cnpj)
    df["status_bm"] = df["cnpj_limpo"].apply(lambda cnpj: status_usuario(status_geral, usuario, cnpj))
    df["data_uso_bm"] = df["cnpj_limpo"].apply(lambda cnpj: obter_data_uso_cnpj_usuario(datas_uso, usuario, cnpj))
    df["data_uso_bm_iso"] = df["cnpj_limpo"].apply(lambda cnpj: obter_data_iso_uso_cnpj_usuario(datas_uso, usuario, cnpj))

    if "razao_social" not in df.columns:
        df["razao_social"] = ""

    if "uf" not in df.columns:
        df["uf"] = ""

    if "municipio" not in df.columns:
        df["municipio"] = ""

    if "capital_social" in df.columns:
        capital_num = pd.to_numeric(
            df["capital_social"].astype(str).str.replace(",", ".", regex=False),
            errors="coerce"
        ).fillna(0)
        df["capital_formatado"] = capital_num.apply(formatar_capital)
    else:
        df["capital_formatado"] = ""

    df["municipio_nome"] = df["municipio"].map(municipios).fillna(df["municipio"])

    for coluna in [
        "razao_social",
        "uf",
        "municipio",
        "municipio_nome",
        "capital_formatado",
        "status_bm",
        "data_uso_bm",
        "data_uso_bm_iso"
    ]:
        if coluna in df.columns:
            df[coluna] = df[coluna].fillna("")

    return df


def empresas_disponiveis_para_perfil():
    usuario = usuario_atual()
    status_geral = carregar_status_bm()
    registros_usuario = status_geral.get(usuario, {})

    if not isinstance(registros_usuario, dict) or not registros_usuario:
        return []

    vinculados = cnpjs_vinculados_perfis()

    cnpjs_interesse = [
        cnpj for cnpj, status in registros_usuario.items()
        if status and status != STATUS_PADRAO and limpar_cnpj(cnpj) not in vinculados
    ]

    if not cnpjs_interesse:
        return []

    df = carregar_base_leve_para_perfis(cnpjs_interesse, usuario)

    if df.empty:
        return []

    df = df[
        (df["status_bm"] != STATUS_PADRAO) &
        (~df["cnpj_limpo"].isin(vinculados))
    ].copy()

    df = df.sort_values(by="razao_social", ascending=True)

    colunas = [
        "cnpj_limpo",
        "cnpj_formatado",
        "razao_social",
        "uf",
        "municipio_nome",
        "status_bm",
        "data_uso_bm"
    ]

    return df[[col for col in colunas if col in df.columns]].head(500).to_dict(orient="records")


def buscar_empresa_por_cnpj(cnpj):
    cnpj_limpo = limpar_cnpj(cnpj)

    try:
        df = carregar_base()
        encontrado = df[df["cnpj_limpo"] == cnpj_limpo]

        if encontrado.empty:
            return None

        return encontrado.iloc[0].to_dict()
    except:
        return None


def enriquecer_perfis_meta(perfis):
    perfis_enriquecidos = []
    cnpjs = [
        limpar_cnpj(perfil.get("cnpj_limpo", ""))
        for perfil in perfis
        if str(perfil.get("cnpj_limpo", "")).strip()
    ]

    df_empresas = carregar_base_leve_para_perfis(cnpjs, usuario_atual()) if cnpjs else pd.DataFrame()
    empresas_por_cnpj = {}

    if not df_empresas.empty:
        empresas_por_cnpj = {
            linha["cnpj_limpo"]: linha
            for linha in df_empresas.to_dict(orient="records")
        }

    for perfil in perfis:
        item = dict(perfil)
        cnpj_item = limpar_cnpj(item.get("cnpj_limpo", "")) if item.get("cnpj_limpo") else ""
        empresa = empresas_por_cnpj.get(cnpj_item)

        if empresa:
            item["cnpj_formatado"] = empresa.get("cnpj_formatado", formatar_cnpj(cnpj_item))
            item["razao_social"] = empresa.get("razao_social", item.get("razao_social", ""))
            item["uf"] = empresa.get("uf", "")
            item["municipio_nome"] = empresa.get("municipio_nome", "")
            item["status_bm"] = empresa.get("status_bm", item.get("status_bm", STATUS_PADRAO))
            item["capital_formatado"] = empresa.get("capital_formatado", "")
            item["data_uso_bm"] = empresa.get("data_uso_bm", "")
            item["data_uso_bm_iso"] = empresa.get("data_uso_bm_iso", "")
        else:
            item["cnpj_formatado"] = formatar_cnpj(cnpj_item) if cnpj_item else ""
            item["razao_social"] = item.get("razao_social", "")
            item["uf"] = item.get("uf", "")
            item["municipio_nome"] = item.get("municipio_nome", "")
            item["status_bm"] = item.get("status_bm", STATUS_PADRAO)
            item["data_uso_bm"] = item.get("data_uso_bm", "")
            item["data_uso_bm_iso"] = item.get("data_uso_bm_iso", "")

        perfis_enriquecidos.append(item)

    return perfis_enriquecidos


def resumo_perfis_meta(perfis):
    total = len(perfis)
    sem_empresa = len([p for p in perfis if not p.get("cnpj_limpo")])
    com_empresa = total - sem_empresa

    contagem = {
        "BM em Análise": 0,
        "Verificou 250": 0,
        "Verificou 2k": 0,
        "Verificou 100k": 0,
        "Precisa de mais informações": 0,
        "Restrito": 0,
        "Checkpoint": 0,
        "Descartado": 0
    }

    for perfil in perfis:
        status = perfil.get("status_bm", STATUS_PADRAO)

        if status in contagem:
            contagem[status] += 1

    sucesso = (
        contagem.get("Verificou 250", 0) +
        contagem.get("Verificou 2k", 0) +
        contagem.get("Verificou 100k", 0)
    )

    problemas = (
        contagem.get("Precisa de mais informações", 0) +
        contagem.get("Restrito", 0) +
        contagem.get("Checkpoint", 0) +
        contagem.get("Descartado", 0)
    )

    taxa_sucesso = (sucesso / com_empresa * 100) if com_empresa else 0

    return {
        "total": total,
        "sem_empresa": sem_empresa,
        "com_empresa": com_empresa,
        "sucesso": sucesso,
        "problemas": problemas,
        "taxa_sucesso": taxa_sucesso,
        "contagem": contagem
    }


def data_hoje():
    return datetime.now().strftime("%Y-%m-%d")


def registrar_historico_producao(usuario, status_antigo, status_novo):
    if not usuario or status_antigo == status_novo or status_novo == STATUS_PADRAO:
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


def resumo_evolucao_diaria(dias=15):
    labels = []
    valores = []
    sucessos = []

    hoje = datetime.now()

    for indice in range(dias - 1, -1, -1):
        data_obj = hoje - timedelta(days=indice)
        data_chave = data_obj.strftime("%Y-%m-%d")
        data_label = data_obj.strftime("%d/%m")

        resumo = resumo_historico_do_dia(data_chave)

        labels.append(data_label)
        valores.append(resumo["total"])
        sucessos.append(resumo["sucesso"])

    return {
        "labels": labels,
        "valores": valores,
        "sucessos": sucessos
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


def formatar_data_brasil(data_raw):
    data_raw = str(data_raw).replace(".0", "").strip()

    if len(data_raw) == 8 and data_raw.isdigit():
        return f"{data_raw[6:8]}/{data_raw[4:6]}/{data_raw[0:4]}"

    return data_raw


def calcular_idade_empresa(data_raw):
    try:
        data_raw = str(data_raw).replace(".0", "").strip().zfill(8)
        abertura = datetime.strptime(data_raw, "%Y%m%d")
        hoje = datetime.now()
        idade = hoje.year - abertura.year

        if (hoje.month, hoje.day) < (abertura.month, abertura.day):
            idade -= 1

        return max(0, idade)
    except:
        return 0


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


CNAES_PADRAO = {
    "6010100": "Atividades de rádio"
}


def formatar_codigo_cnae(codigo):
    codigo = str(codigo).replace(".0", "").replace(".", "").replace("-", "").replace("/", "").strip().zfill(7)

    if len(codigo) == 7 and codigo.isdigit():
        return f"{codigo[:2]}.{codigo[2:4]}-{codigo[4]}-{codigo[5:]}"

    return str(codigo).strip()


def carregar_cnaes():
    cnaes = dict(CNAES_PADRAO)

    if not CNAES_ZIP.exists():
        return cnaes

    try:
        with zipfile.ZipFile(CNAES_ZIP, "r") as zip_ref:
            nome_csv = zip_ref.namelist()[0]

            with zip_ref.open(nome_csv) as arquivo:
                tabela_cnaes = pd.read_csv(
                    arquivo,
                    sep=";",
                    header=None,
                    names=["codigo", "descricao"],
                    dtype=str,
                    encoding="latin1"
                )

        for _, linha in tabela_cnaes.iterrows():
            codigo = str(linha.get("codigo", "")).replace(".0", "").strip().zfill(7)
            descricao = str(linha.get("descricao", "")).strip()

            if codigo and descricao:
                cnaes[codigo] = descricao

        return cnaes
    except:
        return cnaes


def obter_descricao_cnae_por_coluna(df):
    possiveis_colunas = [
        "cnae_principal_descricao",
        "descricao_cnae_principal",
        "descricao_cnae",
        "cnae_descricao",
        "descricao_atividade_principal",
        "atividade_principal",
        "cnae_fiscal_descricao"
    ]

    for coluna in possiveis_colunas:
        if coluna in df.columns:
            return coluna

    return ""


def montar_cnae_exibicao(codigo, descricao=""):
    codigo_limpo = str(codigo).replace(".0", "").replace(".", "").replace("-", "").replace("/", "").strip().zfill(7)
    codigo_formatado = formatar_codigo_cnae(codigo_limpo)
    descricao = str(descricao).strip()

    if descricao and descricao.lower() not in ["nan", "none", ""]:
        return f"{codigo_formatado} - {descricao}"

    return codigo_formatado



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


def avaliar_ia_empresa(empresa):
    score = 40
    motivos = []
    pontos_atencao = []

    capital = float(empresa.get("capital_social_num", 0) or 0)
    idade = int(empresa.get("idade_empresa", 0) or 0)
    telefone = str(empresa.get("telefone_formatado", "")).strip()
    email = str(empresa.get("email", "")).strip()
    categoria = str(empresa.get("categoria_cnae", "")).strip()
    status_bm = str(empresa.get("status_bm", STATUS_PADRAO)).strip()
    usado_global = bool(empresa.get("usado_global", False))

    if capital >= 1000000:
        score += 25
        motivos.append("Capital social acima de R$ 1 milhão")
    elif capital >= 500000:
        score += 20
        motivos.append("Capital social acima de R$ 500 mil")
    elif capital >= 100000:
        score += 10
        motivos.append("Capital social acima de R$ 100 mil")
    else:
        pontos_atencao.append("Capital social baixo")

    if telefone:
        score += 10
        motivos.append("Possui telefone cadastrado")
    else:
        pontos_atencao.append("Telefone não encontrado")

    if email:
        score += 10
        motivos.append("Possui e-mail cadastrado")
    else:
        pontos_atencao.append("E-mail não encontrado")

    if idade >= 5:
        score += 15
        motivos.append("Empresa consolidada com mais de 5 anos")
    elif idade >= 2:
        score += 10
        motivos.append("Empresa com mais de 2 anos")
    elif idade >= 1:
        score += 5
        motivos.append("Empresa com mais de 1 ano")
    else:
        pontos_atencao.append("Empresa muito recente")

    if categoria and categoria.lower() not in ["outros", "indefinido", ""]:
        score += 5
        motivos.append("Categoria CNAE identificada")
    else:
        pontos_atencao.append("Categoria pouco específica")

    if usado_global:
        score -= 20
        pontos_atencao.append("Este CNPJ já possui histórico global de uso")

    if status_bm == "BM em Análise":
        score -= 15
        pontos_atencao.append("Este CNPJ já está em BM em Análise")
    elif status_bm == "Usado em BM":
        score -= 20
        pontos_atencao.append("Este CNPJ já foi usado em BM")
    elif status_bm == "Verificou 250":
        score -= 10
        pontos_atencao.append("Este CNPJ já verificou 250")
    elif status_bm == "Verificou 2k":
        score -= 10
        pontos_atencao.append("Este CNPJ já verificou 2k")
    elif status_bm == "Verificou 100k":
        score -= 10
        pontos_atencao.append("Este CNPJ já verificou 100k")
    elif status_bm == "Restrito":
        score -= 55
        pontos_atencao.append("Histórico de restrição")
    elif status_bm == "Checkpoint":
        score -= 45
        pontos_atencao.append("Histórico de checkpoint")
    elif status_bm == "Descartado":
        score -= 60
        pontos_atencao.append("Marcado como descartado")
    elif status_bm == "Precisa de mais informações":
        score -= 40
        pontos_atencao.append("Histórico de precisa de mais informações")

    score = max(0, min(100, int(score)))

    if status_bm in ["Restrito", "Checkpoint", "Descartado", "Precisa de mais informações"]:
        recomendacao = "🔴 Evitar"
        classe = "evitar"
    elif status_bm != STATUS_PADRAO or usado_global:
        recomendacao = "🟡 Atenção"
        classe = "atencao"
    elif score >= 80:
        recomendacao = "🟢 Excelente"
        classe = "excelente"
    elif score >= 60:
        recomendacao = "🟡 Atenção"
        classe = "atencao"
    else:
        recomendacao = "🔴 Evitar"
        classe = "evitar"

    if not motivos:
        motivos.append("Dados insuficientes para recomendação forte")

    return {
        "score_ia": score,
        "ia_recomendacao": recomendacao,
        "ia_classe": classe,
        "ia_motivos": motivos,
        "ia_pontos_atencao": pontos_atencao
    }


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
    cnaes = carregar_cnaes()
    favoritos = carregar_favoritos()
    status_geral = carregar_status_bm()
    datas_uso = carregar_datas_uso_cnpj()

    df["cnpj_formatado"] = df["cnpj_limpo"].apply(formatar_cnpj)
    df["capital_formatado"] = df["capital_social_num"].apply(formatar_capital)
    df["municipio_nome"] = df["municipio"].map(municipios).fillna(df["municipio"])
    df["favorito"] = df["cnpj_limpo"].isin(favoritos)

    df["status_bm"] = df["cnpj_limpo"].apply(lambda cnpj: status_usuario(status_geral, usuario, cnpj))
    df["bm_utilizada"] = df["status_bm"] != STATUS_PADRAO
    df["data_uso_bm"] = df["cnpj_limpo"].apply(lambda cnpj: obter_data_uso_cnpj_usuario(datas_uso, usuario, cnpj))
    df["data_uso_bm_iso"] = df["cnpj_limpo"].apply(lambda cnpj: obter_data_iso_uso_cnpj_usuario(datas_uso, usuario, cnpj))

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

    df["cnae_principal_codigo"] = df["cnae_principal"].astype(str).str.replace(".0", "", regex=False).str.strip().str.zfill(7)

    coluna_descricao_cnae = obter_descricao_cnae_por_coluna(df)

    if coluna_descricao_cnae:
        df[coluna_descricao_cnae] = df[coluna_descricao_cnae].fillna("").astype(str)
        df["cnae_principal_descricao"] = df[coluna_descricao_cnae]
    else:
        df["cnae_principal_descricao"] = df["cnae_principal_codigo"].map(cnaes).fillna("")

    df["cnae_principal"] = df.apply(
        lambda linha: montar_cnae_exibicao(
            linha.get("cnae_principal_codigo", ""),
            linha.get("cnae_principal_descricao", "")
        ),
        axis=1
    )

    df["sexo_provavel"] = df["sexo_provavel"].replace("", "Indefinido")
    df["categoria_cnae"] = df["categoria_cnae"].replace("", "Outros")
    df["idade_empresa"] = df["data_inicio"].apply(calcular_idade_empresa)
    df["data_inicio_formatada"] = df["data_inicio"].apply(formatar_data_brasil)

    avaliacoes = df.apply(lambda linha: avaliar_ia_empresa(linha.to_dict()), axis=1)

    df["score_ia"] = avaliacoes.apply(lambda item: item["score_ia"])
    df["ia_recomendacao"] = avaliacoes.apply(lambda item: item["ia_recomendacao"])
    df["ia_classe"] = avaliacoes.apply(lambda item: item["ia_classe"])
    df["ia_motivos"] = avaliacoes.apply(lambda item: item["ia_motivos"])
    df["ia_pontos_atencao"] = avaliacoes.apply(lambda item: item["ia_pontos_atencao"])

    return df


def ordenar_dataframe(df, ordenar_por="capital_maior"):
    ordenar_por = str(ordenar_por or "capital_maior").strip()

    if ordenar_por == "capital_menor":
        return df.sort_values(by="capital_social_num", ascending=True)

    if ordenar_por == "mais_antigas":
        df = df.copy()
        df["data_inicio_ordenacao"] = (
            df["data_inicio"]
            .astype(str)
            .str.replace(".0", "", regex=False)
            .str.zfill(8)
        )
        return df.sort_values(by="data_inicio_ordenacao", ascending=True)

    if ordenar_por == "mais_novas":
        df = df.copy()
        df["data_inicio_ordenacao"] = (
            df["data_inicio"]
            .astype(str)
            .str.replace(".0", "", regex=False)
            .str.zfill(8)
        )
        return df.sort_values(by="data_inicio_ordenacao", ascending=False)

    if ordenar_por == "nome_az":
        return df.sort_values(by="razao_social", ascending=True)

    if ordenar_por == "nome_za":
        return df.sort_values(by="razao_social", ascending=False)

    if ordenar_por == "score_ia_maior":
        return df.sort_values(by="score_ia", ascending=False)

    if ordenar_por == "score_ia_menor":
        return df.sort_values(by="score_ia", ascending=True)

    return df.sort_values(by="capital_social_num", ascending=False)


def aplicar_filtros(df, form):
    capital_minimo = form.get("capital_minimo", "0")
    uf = form.get("uf", "")
    sexo = form.get("sexo", "")
    categoria = form.get("categoria", "")
    ano_abertura = form.get("ano_abertura", "")
    busca = form.get("busca", "").strip().upper()
    status_bm = form.get("status_bm", "")
    ordenar_por = form.get("ordenar_por", "capital_maior")

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
            + " " + df["ia_recomendacao"].astype(str).str.upper()
            + " " + df["score_ia"].astype(str).str.upper()
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

    return ordenar_dataframe(df, ordenar_por)


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
            "ordenar_por": form.get("ordenar_por", "capital_maior"),
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


def calcular_top_ufs(df, limite=10):
    if "uf" not in df.columns or len(df) == 0:
        return {"labels": [], "valores": []}

    serie = (
        df["uf"]
        .fillna("")
        .replace("", "Indefinido")
        .value_counts()
        .head(limite)
    )

    return {
        "labels": list(serie.index),
        "valores": [int(valor) for valor in serie.values]
    }


def calcular_dashboard_master(df):
    total_base = len(df)
    total_favoritos = int(df["favorito"].sum()) if "favorito" in df.columns else 0
    total_usados = len(df[df["bm_utilizada"] == True]) if "bm_utilizada" in df.columns else 0
    total_disponiveis = len(df[df["bm_utilizada"] == False]) if "bm_utilizada" in df.columns else 0
    capital_medio = df["capital_social_num"].mean() if len(df) else 0

    estatisticas = estatisticas_gerais()

    total_250 = estatisticas["contagem"].get("Verificou 250", 0)
    total_2k = estatisticas["contagem"].get("Verificou 2k", 0)
    total_100k = estatisticas["contagem"].get("Verificou 100k", 0)

    taxa_250_para_2k = (total_2k / total_250 * 100) if total_250 else 0
    taxa_2k_para_100k = (total_100k / total_2k * 100) if total_2k else 0

    ranking_status = []

    for status, qtd in estatisticas["contagem"].items():
        percentual = (qtd / estatisticas["total"] * 100) if estatisticas["total"] else 0
        ranking_status.append({
            "status": status,
            "quantidade": qtd,
            "percentual": percentual
        })

    ranking_status = sorted(ranking_status, key=lambda item: item["quantidade"], reverse=True)

    return {
        "total_base": total_base,
        "total_favoritos": total_favoritos,
        "total_usados": total_usados,
        "total_disponiveis": total_disponiveis,
        "capital_medio": capital_medio,
        "total_250": total_250,
        "total_2k": total_2k,
        "total_100k": total_100k,
        "taxa_250_para_2k": taxa_250_para_2k,
        "taxa_2k_para_100k": taxa_2k_para_100k,
        "ranking_status": ranking_status
    }




def datas_periodo(data_inicio, data_fim):
    try:
        inicio = datetime.strptime(data_inicio, "%Y-%m-%d")
        fim = datetime.strptime(data_fim, "%Y-%m-%d")
    except:
        return []

    if fim < inicio:
        inicio, fim = fim, inicio

    datas = []
    atual = inicio

    while atual <= fim:
        datas.append(atual.strftime("%Y-%m-%d"))
        atual += timedelta(days=1)

    return datas


def resumo_relatorio_bm(data_inicio, data_fim, usuario_filtro=""):
    historico = carregar_historico_producao()
    contagem = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}
    por_usuario = {}

    for data in datas_periodo(data_inicio, data_fim):
        registros_dia = historico.get(data, {})

        if not isinstance(registros_dia, dict):
            continue

        for usuario, registros in registros_dia.items():
            if usuario_filtro and usuario != usuario_filtro:
                continue

            if not isinstance(registros, dict):
                continue

            if usuario not in por_usuario:
                por_usuario[usuario] = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}

            for status, qtd in registros.items():
                try:
                    qtd = int(qtd)
                except:
                    qtd = 0

                if status in contagem:
                    contagem[status] += qtd
                    por_usuario[usuario][status] += qtd

    total = sum(contagem.values())
    sucesso = sum(contagem.get(status, 0) for status in STATUS_SUCESSO)
    problemas = sum(contagem.get(status, 0) for status in STATUS_NEGATIVOS)
    taxa_sucesso = (sucesso / total * 100) if total else 0
    taxa_problemas = (problemas / total * 100) if total else 0

    maior_problema = "Nenhum"
    maior_qtd = 0

    for status in STATUS_NEGATIVOS:
        qtd = contagem.get(status, 0)
        if qtd > maior_qtd:
            maior_qtd = qtd
            maior_problema = status

    usuarios_resumo = []

    for usuario, registros in por_usuario.items():
        total_usuario = sum(registros.values())
        sucesso_usuario = sum(registros.get(status, 0) for status in STATUS_SUCESSO)
        taxa_usuario = (sucesso_usuario / total_usuario * 100) if total_usuario else 0

        usuarios_resumo.append({
            "usuario": usuario,
            "contagem": registros,
            "total": total_usuario,
            "sucesso": sucesso_usuario,
            "taxa_sucesso": taxa_usuario
        })

    usuarios_resumo = sorted(usuarios_resumo, key=lambda item: item["sucesso"], reverse=True)

    return {
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "contagem": contagem,
        "total": total,
        "sucesso": sucesso,
        "problemas": problemas,
        "taxa_sucesso": taxa_sucesso,
        "taxa_problemas": taxa_problemas,
        "maior_problema": maior_problema,
        "maior_problema_qtd": maior_qtd,
        "usuarios": usuarios_resumo
    }


def formatar_data_relatorio(data_iso):
    try:
        return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except:
        return data_iso


def gerar_texto_relatorio_bm(resumo):
    inicio = formatar_data_relatorio(resumo.get("data_inicio", ""))
    fim = formatar_data_relatorio(resumo.get("data_fim", ""))
    c = resumo.get("contagem", {})

    linhas = []
    linhas.append("RELATÓRIO OPERACIONAL DE BMs")
    linhas.append(f"Período: {inicio} a {fim}")
    linhas.append("")
    linhas.append(f"Durante o período analisado, foram movimentadas {resumo.get('total', 0)} BMs/CNPJs na operação.")
    linhas.append("")
    linhas.append("RESULTADOS POSITIVOS:")
    linhas.append(f"- {c.get('Verificou 250', 0)} verificaram 250.")
    linhas.append(f"- {c.get('Verificou 2k', 0)} verificaram 2k.")
    linhas.append(f"- {c.get('Verificou 100k', 0)} verificaram 100k.")
    linhas.append("")
    linhas.append("PROBLEMAS IDENTIFICADOS:")
    linhas.append(f"- {c.get('Precisa de mais informações', 0)} solicitaram mais informações.")
    linhas.append(f"- {c.get('Restrito', 0)} ficaram restritos.")
    linhas.append(f"- {c.get('Checkpoint', 0)} deram checkpoint.")
    linhas.append(f"- {c.get('Descartado', 0)} foram descartados.")
    linhas.append("")
    linhas.append("RESUMO OPERACIONAL:")
    linhas.append(f"Ao todo, {resumo.get('sucesso', 0)} BMs tiveram resultado positivo, representando uma taxa de sucesso de {resumo.get('taxa_sucesso', 0):.2f}%.")
    linhas.append(f"O volume de problemas foi de {resumo.get('problemas', 0)} ocorrência(s), com taxa de {resumo.get('taxa_problemas', 0):.2f}% sobre o total movimentado.")

    if resumo.get("maior_problema_qtd", 0) > 0:
        linhas.append(f"A maior incidência negativa no período foi: {resumo.get('maior_problema')} ({resumo.get('maior_problema_qtd')} ocorrência(s)).")

    if resumo.get("usuarios"):
        linhas.append("")
        linhas.append("RESUMO POR USUÁRIO:")

        for usuario in resumo.get("usuarios", []):
            linhas.append(f"- {usuario['usuario']}: {usuario['total']} movimentações, {usuario['sucesso']} sucesso(s), taxa de {usuario['taxa_sucesso']:.2f}%.")

    linhas.append("")
    linhas.append("CONCLUSÃO:")

    if resumo.get("total", 0) == 0:
        linhas.append("Não houve movimentações registradas no período selecionado.")
    elif resumo.get("taxa_sucesso", 0) >= 50:
        linhas.append("O período apresentou desempenho operacional positivo, com boa conversão de BMs verificadas.")
    else:
        linhas.append("O período apresentou instabilidade operacional relevante, exigindo atenção aos pontos de falha e ao fluxo de envio para análise.")

    return "\n".join(linhas)



# ============================================================
# GERADOR DE SITES INSTITUCIONAIS
# Implementação nova 100% SQL/PostgreSQL, sem JSON ativo.
# ============================================================

MODELOS_SITE = [
    {
        "slug": "institucional",
        "nome": "Institucional Clássico",
        "descricao": "Visual formal, confiável e corporativo. Bom para serviços, consultorias, educação e empresas tradicionais.",
        "icone": "🏛️"
    },
    {
        "slug": "moderno",
        "nome": "Moderno Premium",
        "descricao": "Layout de alto impacto, com hero forte, cards modernos e aparência mais sofisticada.",
        "icone": "✨"
    },
    {
        "slug": "varejo",
        "nome": "Comercial Varejo",
        "descricao": "Mais comercial, direto e visual. Bom para lojas, mercados, materiais, alimentos e comércio geral.",
        "icone": "🛒"
    },
    {
        "slug": "servicos",
        "nome": "Serviços Profissionais",
        "descricao": "Focado em atendimento, etapas de serviço, benefícios e credibilidade operacional.",
        "icone": "🧰"
    },
    {
        "slug": "minimalista",
        "nome": "Minimalista Clean",
        "descricao": "Site limpo, elegante e discreto, ideal para empresas pequenas ou com poucos dados públicos.",
        "icone": "◻️"
    },
    {
        "slug": "tech",
        "nome": "Tech Dark",
        "descricao": "Visual escuro, moderno e tecnológico. Bom para tecnologia, marketing, web, dados e inovação.",
        "icone": "🛰️"
    }
]

MODELOS_SITE_DICT = {modelo["slug"]: modelo for modelo in MODELOS_SITE}


def criar_tabela_sites_gerados():
    try:
        dialect = getattr(engine, "dialect", None)
        dialect_name = getattr(dialect, "name", "postgresql")

        if dialect_name == "postgresql":
            sql = """
            CREATE TABLE IF NOT EXISTS sites_gerados (
                id SERIAL PRIMARY KEY,
                usuario TEXT NOT NULL,
                cnpj TEXT NOT NULL,
                cnpj_formatado TEXT,
                nome_empresarial TEXT,
                nome_fantasia TEXT,
                cnae_principal TEXT,
                categoria_cnae TEXT,
                endereco TEXT,
                telefone TEXT,
                email TEXT,
                meta_tag TEXT NOT NULL,
                modelo_site TEXT NOT NULL,
                nome_arquivo TEXT NOT NULL,
                html_gerado TEXT NOT NULL,
                status TEXT DEFAULT 'Gerado',
                observacoes TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        else:
            sql = """
            CREATE TABLE IF NOT EXISTS sites_gerados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                cnpj TEXT NOT NULL,
                cnpj_formatado TEXT,
                nome_empresarial TEXT,
                nome_fantasia TEXT,
                cnae_principal TEXT,
                categoria_cnae TEXT,
                endereco TEXT,
                telefone TEXT,
                email TEXT,
                meta_tag TEXT NOT NULL,
                modelo_site TEXT NOT NULL,
                nome_arquivo TEXT NOT NULL,
                html_gerado TEXT NOT NULL,
                status TEXT DEFAULT 'Gerado',
                observacoes TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """

        colunas_extras = {
            "nome_exibicao": "TEXT",
            "telefone_exibicao": "TEXT",
            "whatsapp_exibicao": "TEXT",
            "email_exibicao": "TEXT",
            "endereco_exibicao": "TEXT",
            "cloudflare_worker_name": "TEXT",
            "cloudflare_url": "TEXT",
            "cloudflare_status": "TEXT DEFAULT 'Não publicado'",
            "cloudflare_publicado_em": "TIMESTAMP",
            "cloudflare_erro": "TEXT"
        }

        with engine.begin() as conn:
            conn.execute(text(sql))

            if dialect_name == "postgresql":
                for coluna, tipo in colunas_extras.items():
                    conn.execute(text(f"ALTER TABLE sites_gerados ADD COLUMN IF NOT EXISTS {coluna} {tipo}"))
            else:
                existentes = conn.execute(text("PRAGMA table_info(sites_gerados)")).fetchall()
                colunas_existentes = {linha[1] for linha in existentes}

                for coluna, tipo in colunas_extras.items():
                    if coluna not in colunas_existentes:
                        conn.execute(text(f"ALTER TABLE sites_gerados ADD COLUMN {coluna} {tipo}"))

    except Exception as erro:
        print("Erro ao criar tabela sites_gerados:", erro)


def valor_texto(valor, padrao=""):
    if valor is None:
        return padrao

    texto = str(valor).replace(".0", "").strip()

    if texto.lower() in ["nan", "none", "nat", "null"]:
        return padrao

    return texto


def valor_publico(valor, padrao="Não informado"):
    texto = valor_texto(valor, "")

    if not texto or texto.replace("*", "").strip() == "":
        return padrao

    return texto


def nome_exibicao_empresa(empresa):
    fantasia = valor_texto(empresa.get("nome_fantasia", ""))
    razao = valor_texto(empresa.get("razao_social", ""))

    if fantasia and fantasia.replace("*", "").strip():
        return fantasia

    return razao or "Empresa"


def normalizar_slug_site(texto):
    texto = valor_texto(texto, "empresa").lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    partes = [parte for parte in texto.split() if parte]

    if not partes:
        return "empresa"

    return partes[0][:32]


def gerar_nome_arquivo_site(empresa):
    base_nome = nome_exibicao_empresa(empresa)
    primeiro_nome = normalizar_slug_site(base_nome)

    if primeiro_nome == "empresa":
        primeiro_nome = normalizar_slug_site(empresa.get("razao_social", ""))

    return f"index {primeiro_nome}.html"


def montar_endereco_empresa(empresa):
    endereco_personalizado = valor_texto(empresa.get("endereco_site", ""))

    if endereco_personalizado:
        return endereco_personalizado

    partes = []

    for campo in ["logradouro", "numero", "complemento", "bairro"]:
        valor = valor_texto(empresa.get(campo, ""))

        if valor:
            partes.append(valor)

    municipio = valor_texto(empresa.get("municipio_nome", "")) or valor_texto(empresa.get("municipio", ""))
    uf = valor_texto(empresa.get("uf", ""))

    cidade_uf = " - ".join([item for item in [municipio, uf] if item])

    if cidade_uf:
        partes.append(cidade_uf)

    cep = valor_texto(empresa.get("cep", ""))

    if cep:
        partes.append(f"CEP {cep}")

    if partes:
        return ", ".join(partes)

    return cidade_uf or "Endereço não informado"


def identificar_segmento_site(empresa):
    texto = " ".join([
        valor_texto(empresa.get("cnae_principal", "")),
        valor_texto(empresa.get("categoria_cnae", "")),
        valor_texto(empresa.get("razao_social", "")),
        valor_texto(empresa.get("nome_fantasia", ""))
    ]).lower()

    regras = [
        ("educacao", ["educação", "ensino", "escola", "colégio", "curso", "treinamento"]),
        ("construcao", ["construção", "edifício", "obras", "material de construção", "ferragens", "hidráulico", "elétrico"]),
        ("varejo", ["comércio varejista", "loja", "mercado", "mercearia", "armazém", "autopeças", "varejo"]),
        ("alimentos", ["alimento", "doces", "restaurante", "lanchonete", "padaria", "bebida", "refeição"]),
        ("tecnologia", ["tecnologia", "web", "software", "informática", "dados", "internet", "programação"]),
        ("saude", ["saúde", "clínica", "médica", "odontológica", "hospital", "laboratório"]),
        ("beleza", ["estética", "beleza", "cabelo", "cosmético", "salão"]),
        ("imobiliario", ["imobiliário", "imóveis", "incorporação", "loteamento", "aluguel"]),
        ("transporte", ["transporte", "logística", "carga", "entrega", "armazenagem"]),
        ("marketing", ["marketing", "publicidade", "promoção de vendas", "comunicação"])
    ]

    for segmento, palavras in regras:
        if any(palavra in texto for palavra in palavras):
            return segmento

    return "servicos"


def obter_conteudo_segmento(segmento):
    conteudos = {
        "educacao": {
            "titulo": "Educação com estrutura, acolhimento e visão de futuro",
            "subtitulo": "Atuação voltada à formação, desenvolvimento e suporte educacional.",
            "servicos": [
                "Planejamento pedagógico e acompanhamento da aprendizagem",
                "Atendimento educacional com foco em organização e evolução",
                "Projetos de desenvolvimento profissional e formação continuada",
                "Comunicação clara com famílias, alunos, equipes e parceiros"
            ],
            "diferenciais": [
                "Ambiente orientado à confiança",
                "Rotinas bem estruturadas",
                "Compromisso com desenvolvimento humano"
            ],
            "keywords": "educacao, escola, estudantes"
        },
        "construcao": {
            "titulo": "Soluções para obras, manutenção e estruturação de projetos",
            "subtitulo": "Atuação voltada a materiais, serviços técnicos, instalações e apoio operacional.",
            "servicos": [
                "Atendimento para demandas de construção, reforma e manutenção",
                "Fornecimento e apoio em materiais, ferramentas e insumos técnicos",
                "Organização de processos para execução com segurança",
                "Suporte comercial para clientes, obras e parceiros"
            ],
            "diferenciais": [
                "Foco em segurança e durabilidade",
                "Atendimento prático e objetivo",
                "Conhecimento das necessidades de obra"
            ],
            "keywords": "construcao, obra, engenharia"
        },
        "varejo": {
            "titulo": "Comércio com atendimento ágil e variedade para o dia a dia",
            "subtitulo": "Atuação voltada ao fornecimento de produtos, conveniência e relacionamento comercial.",
            "servicos": [
                "Venda de produtos selecionados para consumidores e empresas",
                "Atendimento comercial com foco em clareza e disponibilidade",
                "Organização de mix, estoque e rotina de atendimento",
                "Relacionamento com clientes, fornecedores e parceiros locais"
            ],
            "diferenciais": [
                "Atendimento próximo",
                "Praticidade na compra",
                "Variedade e organização"
            ],
            "keywords": "loja, varejo, produtos"
        },
        "alimentos": {
            "titulo": "Produtos e experiências alimentares com cuidado em cada detalhe",
            "subtitulo": "Atuação voltada a alimentos, preparo, comercialização e atendimento ao cliente.",
            "servicos": [
                "Comercialização de alimentos e produtos relacionados",
                "Atendimento ao consumidor com foco em qualidade e confiança",
                "Organização de processos de preparo, seleção e entrega",
                "Relacionamento comercial com fornecedores e clientes"
            ],
            "diferenciais": [
                "Cuidado com qualidade",
                "Atendimento humanizado",
                "Rotina operacional organizada"
            ],
            "keywords": "alimentos, cozinha, restaurante"
        },
        "tecnologia": {
            "titulo": "Tecnologia, presença digital e soluções para negócios conectados",
            "subtitulo": "Atuação voltada a sistemas, internet, comunicação digital e suporte técnico.",
            "servicos": [
                "Desenvolvimento e suporte para presença digital",
                "Consultoria em tecnologia, dados e processos online",
                "Estruturação de soluções digitais para empresas",
                "Apoio técnico para operação, manutenção e evolução digital"
            ],
            "diferenciais": [
                "Visão moderna",
                "Processos digitais",
                "Soluções escaláveis"
            ],
            "keywords": "tecnologia, computador, equipe"
        },
        "saude": {
            "titulo": "Atendimento, cuidado e organização para serviços de saúde",
            "subtitulo": "Atuação voltada ao suporte, bem-estar, assistência e relacionamento com clientes.",
            "servicos": [
                "Apoio a rotinas de atendimento e serviços de saúde",
                "Organização de processos com atenção ao cliente",
                "Comunicação clara, responsável e acolhedora",
                "Gestão de relacionamento com pacientes, parceiros e fornecedores"
            ],
            "diferenciais": [
                "Cuidado no atendimento",
                "Postura responsável",
                "Ambiente de confiança"
            ],
            "keywords": "saude, clinica, atendimento"
        },
        "beleza": {
            "titulo": "Beleza, bem-estar e atendimento personalizado",
            "subtitulo": "Atuação voltada a estética, cuidado pessoal e experiências de atendimento.",
            "servicos": [
                "Serviços e soluções para beleza e bem-estar",
                "Atendimento personalizado para diferentes perfis de clientes",
                "Organização de rotinas, agenda e relacionamento",
                "Comunicação profissional com clientes e parceiros"
            ],
            "diferenciais": [
                "Cuidado estético",
                "Atendimento próximo",
                "Experiência agradável"
            ],
            "keywords": "beleza, estetica, cuidado"
        },
        "imobiliario": {
            "titulo": "Soluções imobiliárias com clareza, segurança e visão patrimonial",
            "subtitulo": "Atuação voltada a imóveis, negócios patrimoniais, incorporação e gestão comercial.",
            "servicos": [
                "Apoio a operações imobiliárias e patrimoniais",
                "Relacionamento com clientes, parceiros e fornecedores",
                "Organização de documentação e processos comerciais",
                "Estratégias para apresentação de empreendimentos e oportunidades"
            ],
            "diferenciais": [
                "Visão de patrimônio",
                "Segurança nas informações",
                "Atendimento consultivo"
            ],
            "keywords": "imoveis, predios, arquitetura"
        },
        "transporte": {
            "titulo": "Logística, movimentação e apoio operacional para empresas",
            "subtitulo": "Atuação voltada a transporte, entregas, armazenagem e rotinas de distribuição.",
            "servicos": [
                "Apoio a demandas de transporte e movimentação",
                "Organização de rotas, entregas e processos logísticos",
                "Relacionamento com clientes, fornecedores e parceiros operacionais",
                "Suporte para distribuição e atendimento comercial"
            ],
            "diferenciais": [
                "Organização operacional",
                "Agilidade no atendimento",
                "Compromisso com prazos"
            ],
            "keywords": "logistica, transporte, entrega"
        },
        "marketing": {
            "titulo": "Comunicação, promoção e presença de marca para empresas",
            "subtitulo": "Atuação voltada a publicidade, promoção de vendas e relacionamento comercial.",
            "servicos": [
                "Estratégias de comunicação e promoção comercial",
                "Apoio a campanhas, presença digital e materiais institucionais",
                "Relacionamento com clientes, parceiros e canais de venda",
                "Planejamento de ações para posicionamento e visibilidade"
            ],
            "diferenciais": [
                "Comunicação objetiva",
                "Visão comercial",
                "Presença de marca"
            ],
            "keywords": "marketing, comunicacao, escritorio"
        },
        "servicos": {
            "titulo": "Serviços profissionais com organização, clareza e compromisso",
            "subtitulo": "Atuação voltada ao atendimento empresarial, suporte operacional e soluções sob demanda.",
            "servicos": [
                "Atendimento profissional para clientes, empresas e parceiros",
                "Organização de processos, demandas e rotinas operacionais",
                "Soluções adaptadas ao perfil de cada cliente",
                "Comunicação clara em todas as etapas do atendimento"
            ],
            "diferenciais": [
                "Postura profissional",
                "Atendimento organizado",
                "Compromisso com o cliente"
            ],
            "keywords": "servicos, escritorio, atendimento"
        }
    }

    return conteudos.get(segmento, conteudos["servicos"])


IMAGENS_SEGMENTO = {
    "educacao": [
        "https://images.unsplash.com/photo-1523050854058-8df90110c9f1?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1509062522246-3755977927d7?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1497633762265-9d179a990aa6?auto=format&fit=crop&w=1200&q=80"
    ],
    "construcao": [
        "https://images.unsplash.com/photo-1503387762-592deb58ef4e?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1541888946425-d81bb19240f5?auto=format&fit=crop&w=1200&q=80"
    ],
    "varejo": [
        "https://images.unsplash.com/photo-1556740749-887f6717d7e4?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1472851294608-062f824d29cc?auto=format&fit=crop&w=1200&q=80"
    ],
    "alimentos": [
        "https://images.unsplash.com/photo-1504674900247-0877df9cc836?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1482049016688-2d3e1b311543?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1495195134817-aeb325a55b65?auto=format&fit=crop&w=1200&q=80"
    ],
    "tecnologia": [
        "https://images.unsplash.com/photo-1519389950473-47ba0277781c?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1497366754035-f200968a6e72?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1551434678-e076c223a692?auto=format&fit=crop&w=1200&q=80"
    ],
    "saude": [
        "https://images.unsplash.com/photo-1505751172876-fa1923c5c528?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1519494026892-80bbd2d6fd0d?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1532938911079-1b06ac7ceec7?auto=format&fit=crop&w=1200&q=80"
    ],
    "beleza": [
        "https://images.unsplash.com/photo-1560066984-138dadb4c035?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1516975080664-ed2fc6a32937?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1522337360788-8b13dee7a37e?auto=format&fit=crop&w=1200&q=80"
    ],
    "imobiliario": [
        "https://images.unsplash.com/photo-1560518883-ce09059eeffa?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1564013799919-ab600027ffc6?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?auto=format&fit=crop&w=1200&q=80"
    ],
    "transporte": [
        "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1494412574643-ff11b0a5c1c3?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1519003722824-194d4455a60c?auto=format&fit=crop&w=1200&q=80"
    ],
    "marketing": [
        "https://images.unsplash.com/photo-1557804506-669a67965ba0?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1552664730-d307ca884978?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1542744173-8e7e53415bb0?auto=format&fit=crop&w=1200&q=80"
    ],
    "servicos": [
        "https://images.unsplash.com/photo-1497366754035-f200968a6e72?auto=format&fit=crop&w=1600&q=80",
        "https://images.unsplash.com/photo-1521791136064-7986c2920216?auto=format&fit=crop&w=1200&q=80",
        "https://images.unsplash.com/photo-1556761175-b413da4baf72?auto=format&fit=crop&w=1200&q=80"
    ]
}


CONFIG_MODELOS_SITE = {
    "institucional": {
        "font": "Arial, Helvetica, sans-serif",
        "bg": "#f4f7fb",
        "surface": "#ffffff",
        "surface2": "#eef4ff",
        "text": "#142033",
        "muted": "#64748b",
        "primary": "#174ea6",
        "secondary": "#0f766e",
        "accent": "#dbeafe",
        "hero": "linear-gradient(135deg, rgba(23,78,166,.92), rgba(15,118,110,.84))"
    },
    "moderno": {
        "font": "Inter, Arial, Helvetica, sans-serif",
        "bg": "#0f172a",
        "surface": "#ffffff",
        "surface2": "#f8fafc",
        "text": "#111827",
        "muted": "#64748b",
        "primary": "#7c3aed",
        "secondary": "#06b6d4",
        "accent": "#ede9fe",
        "hero": "linear-gradient(135deg, rgba(124,58,237,.92), rgba(6,182,212,.82))"
    },
    "varejo": {
        "font": "Arial, Helvetica, sans-serif",
        "bg": "#fff7ed",
        "surface": "#ffffff",
        "surface2": "#ffedd5",
        "text": "#2b1705",
        "muted": "#7c2d12",
        "primary": "#ea580c",
        "secondary": "#16a34a",
        "accent": "#fed7aa",
        "hero": "linear-gradient(135deg, rgba(234,88,12,.92), rgba(22,163,74,.78))"
    },
    "servicos": {
        "font": "Arial, Helvetica, sans-serif",
        "bg": "#f8fafc",
        "surface": "#ffffff",
        "surface2": "#eef2ff",
        "text": "#172033",
        "muted": "#64748b",
        "primary": "#4338ca",
        "secondary": "#f59e0b",
        "accent": "#e0e7ff",
        "hero": "linear-gradient(135deg, rgba(67,56,202,.92), rgba(245,158,11,.75))"
    },
    "minimalista": {
        "font": "Georgia, 'Times New Roman', serif",
        "bg": "#fafafa",
        "surface": "#ffffff",
        "surface2": "#f4f4f5",
        "text": "#18181b",
        "muted": "#71717a",
        "primary": "#18181b",
        "secondary": "#52525b",
        "accent": "#e4e4e7",
        "hero": "linear-gradient(135deg, rgba(24,24,27,.90), rgba(82,82,91,.78))"
    },
    "tech": {
        "font": "Inter, Arial, Helvetica, sans-serif",
        "bg": "#020617",
        "surface": "#0f172a",
        "surface2": "#111827",
        "text": "#e5e7eb",
        "muted": "#94a3b8",
        "primary": "#38bdf8",
        "secondary": "#22c55e",
        "accent": "#0f172a",
        "hero": "linear-gradient(135deg, rgba(14,165,233,.88), rgba(34,197,94,.70))"
    }
}


def gerar_css_site(modelo):
    cfg = CONFIG_MODELOS_SITE.get(modelo, CONFIG_MODELOS_SITE["institucional"])

    return f"""
        :root {{
            --bg: {cfg['bg']};
            --surface: {cfg['surface']};
            --surface2: {cfg['surface2']};
            --text: {cfg['text']};
            --muted: {cfg['muted']};
            --primary: {cfg['primary']};
            --secondary: {cfg['secondary']};
            --accent: {cfg['accent']};
            --hero: {cfg['hero']};
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: {cfg['font']};
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
        }}

        a {{ color: inherit; text-decoration: none; }}

        .topbar {{
            background: var(--surface);
            border-bottom: 1px solid rgba(148, 163, 184, .22);
            position: sticky;
            top: 0;
            z-index: 10;
            box-shadow: 0 12px 30px rgba(15, 23, 42, .06);
        }}

        .container {{
            width: min(1160px, calc(100% - 32px));
            margin: 0 auto;
        }}

        .topbar-inner {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 24px;
            padding: 18px 0;
        }}

        .brand {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .brand strong {{
            font-size: 20px;
            letter-spacing: -.03em;
        }}

        .brand span {{
            color: var(--muted);
            font-size: 13px;
        }}

        .menu {{
            display: flex;
            align-items: center;
            gap: 18px;
            color: var(--muted);
            font-size: 14px;
            font-weight: 700;
        }}

        .menu a:hover {{ color: var(--primary); }}

        .hero {{
            background:
                var(--hero),
                url('__HERO_IMAGE__') center/cover;
            color: white;
            padding: 94px 0;
            position: relative;
            overflow: hidden;
        }}

        .hero-grid {{
            display: grid;
            grid-template-columns: 1.2fr .8fr;
            gap: 38px;
            align-items: center;
        }}

        .eyebrow {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(255,255,255,.16);
            border: 1px solid rgba(255,255,255,.28);
            padding: 8px 13px;
            border-radius: 999px;
            font-size: 13px;
            font-weight: 800;
            margin-bottom: 18px;
            backdrop-filter: blur(10px);
        }}

        h1 {{
            font-size: clamp(34px, 5vw, 64px);
            line-height: .98;
            letter-spacing: -.06em;
            max-width: 820px;
            margin-bottom: 20px;
        }}

        .hero p {{
            color: rgba(255,255,255,.88);
            font-size: 18px;
            max-width: 720px;
        }}

        .hero-card {{
            background: rgba(255,255,255,.16);
            border: 1px solid rgba(255,255,255,.26);
            border-radius: 28px;
            padding: 26px;
            backdrop-filter: blur(12px);
            box-shadow: 0 30px 80px rgba(0,0,0,.24);
        }}

        .hero-card strong {{
            display: block;
            font-size: 15px;
            color: rgba(255,255,255,.74);
            margin-bottom: 8px;
        }}

        .hero-card span {{
            display: block;
            font-size: 24px;
            font-weight: 900;
        }}

        .btn-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 28px;
        }}

        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 48px;
            padding: 0 20px;
            border-radius: 999px;
            font-weight: 900;
            border: 1px solid rgba(255,255,255,.38);
            background: white;
            color: #111827;
        }}

        .btn.secondary {{
            background: rgba(255,255,255,.14);
            color: white;
        }}

        section {{ padding: 76px 0; }}

        .section-title {{
            display: grid;
            gap: 10px;
            margin-bottom: 32px;
        }}

        .section-title span {{
            color: var(--primary);
            text-transform: uppercase;
            letter-spacing: .14em;
            font-size: 12px;
            font-weight: 900;
        }}

        .section-title h2 {{
            font-size: clamp(28px, 4vw, 44px);
            line-height: 1.05;
            letter-spacing: -.04em;
        }}

        .section-title p {{
            color: var(--muted);
            max-width: 760px;
        }}

        .grid-2 {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 26px;
            align-items: stretch;
        }}

        .card {{
            background: var(--surface);
            border: 1px solid rgba(148, 163, 184, .22);
            border-radius: 26px;
            padding: 28px;
            box-shadow: 0 18px 50px rgba(15, 23, 42, .07);
        }}

        .card h3 {{
            font-size: 22px;
            margin-bottom: 12px;
            letter-spacing: -.03em;
        }}

        .card p, .card li {{
            color: var(--muted);
        }}

        .services {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 18px;
        }}

        .service-card {{
            background: var(--surface);
            border: 1px solid rgba(148, 163, 184, .22);
            border-radius: 24px;
            padding: 24px;
            min-height: 210px;
            box-shadow: 0 18px 44px rgba(15, 23, 42, .06);
        }}

        .service-card b {{
            display: inline-flex;
            width: 38px;
            height: 38px;
            align-items: center;
            justify-content: center;
            border-radius: 13px;
            background: var(--accent);
            color: var(--primary);
            margin-bottom: 16px;
        }}

        .gallery {{
            display: grid;
            grid-template-columns: 1.1fr .9fr .9fr;
            gap: 18px;
        }}

        .gallery img {{
            width: 100%;
            height: 320px;
            object-fit: cover;
            border-radius: 28px;
            box-shadow: 0 18px 48px rgba(15, 23, 42, .14);
        }}

        .gallery img:first-child {{
            height: 420px;
            grid-row: span 2;
        }}

        .info-table {{
            display: grid;
            gap: 12px;
        }}

        .info-line {{
            display: grid;
            grid-template-columns: 190px 1fr;
            gap: 12px;
            padding: 16px;
            border-radius: 18px;
            background: var(--surface2);
            border: 1px solid rgba(148, 163, 184, .18);
        }}

        .info-line span {{
            color: var(--muted);
            font-weight: 800;
        }}

        .policy-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 18px;
        }}

        .footer {{
            background: #020617;
            color: white;
            padding: 38px 0;
        }}

        .footer-inner {{
            display: flex;
            justify-content: space-between;
            gap: 24px;
            color: rgba(255,255,255,.70);
            font-size: 14px;
        }}

        .footer strong {{ color: white; }}

        @media (max-width: 900px) {{
            .topbar-inner, .menu, .footer-inner {{
                align-items: flex-start;
                flex-direction: column;
            }}

            .hero-grid, .grid-2, .services, .policy-grid, .gallery {{
                grid-template-columns: 1fr;
            }}

            .gallery img, .gallery img:first-child {{
                height: 260px;
            }}

            .info-line {{
                grid-template-columns: 1fr;
            }}
        }}
    """


def montar_cards_servicos(servicos):
    cards = []

    for indice, servico in enumerate(servicos, start=1):
        cards.append(f"""
            <article class="service-card">
                <b>{indice:02d}</b>
                <h3>{escape(servico)}</h3>
                <p>Atendimento conduzido com organização, responsabilidade e comunicação clara em cada etapa.</p>
            </article>
        """)

    return "\n".join(cards)


def montar_cards_diferenciais(diferenciais):
    cards = []

    for diferencial in diferenciais:
        cards.append(f"""
            <div class="card">
                <h3>{escape(diferencial)}</h3>
                <p>Esse pilar orienta a forma como a empresa se apresenta, atende e constrói relações de confiança com clientes e parceiros.</p>
            </div>
        """)

    return "\n".join(cards)


def gerar_html_site_empresa(empresa, meta_tag, modelo_site, observacoes=""):
    modelo_site = modelo_site if modelo_site in MODELOS_SITE_DICT else "institucional"
    segmento = identificar_segmento_site(empresa)
    conteudo = obter_conteudo_segmento(segmento)
    imagens = IMAGENS_SEGMENTO.get(segmento, IMAGENS_SEGMENTO["servicos"])

    nome_site = valor_texto(empresa.get("nome_site", "")) or nome_exibicao_empresa(empresa)
    razao_social = valor_publico(empresa.get("razao_social", ""))
    nome_fantasia = valor_publico(empresa.get("nome_fantasia", ""))
    cnpj_formatado = formatar_cnpj(limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", ""))))
    cnae = valor_publico(empresa.get("cnae_principal", ""))
    categoria = valor_publico(empresa.get("categoria_cnae", ""))
    telefone = valor_publico(empresa.get("telefone_formatado", ""))
    whatsapp = valor_publico(empresa.get("whatsapp_site", "") or empresa.get("telefone_formatado", ""))
    email = valor_publico(empresa.get("email", ""))
    endereco = montar_endereco_empresa(empresa)
    data_abertura = valor_publico(empresa.get("data_inicio_formatada", ""))
    modelo_nome = MODELOS_SITE_DICT[modelo_site]["nome"]
    css = gerar_css_site(modelo_site).replace("__HERO_IMAGE__", imagens[0])

    servicos_html = montar_cards_servicos(conteudo["servicos"])
    diferenciais_html = montar_cards_diferenciais(conteudo["diferenciais"])

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {meta_tag.strip()}
    <title>{escape(nome_site)} | Site Institucional</title>
    <meta name="description" content="Site institucional de {escape(razao_social)}. Conheça a empresa, seus serviços, dados cadastrais e canais de contato.">
    <style>
{css}
    </style>
</head>
<body>

<header class="topbar">
    <div class="container topbar-inner">
        <a class="brand" href="#inicio" aria-label="{escape(nome_site)}">
            <strong>{escape(nome_site)}</strong>
            <span>{escape(categoria)} · {escape(modelo_nome)}</span>
        </a>

        <nav class="menu" aria-label="Menu principal">
            <a href="#sobre">Sobre</a>
            <a href="#servicos">Serviços</a>
            <a href="#dados">Dados</a>
            <a href="#contato">Contato</a>
        </nav>
    </div>
</header>

<main id="inicio">
    <section class="hero">
        <div class="container hero-grid">
            <div>
                <div class="eyebrow">Empresa institucional · Atendimento profissional</div>
                <h1>{escape(conteudo["titulo"])}</h1>
                <p>{escape(conteudo["subtitulo"])} A {escape(nome_site)} atua com foco em organização, transparência e construção de relações comerciais sólidas.</p>

                <div class="btn-row">
                    <a class="btn" href="#contato">Falar com a empresa</a>
                    <a class="btn secondary" href="#dados">Ver dados empresariais</a>
                </div>
            </div>

            <aside class="hero-card">
                <strong>Razão Social</strong>
                <span>{escape(razao_social)}</span>
                <br>
                <strong>CNPJ</strong>
                <span>{escape(cnpj_formatado)}</span>
            </aside>
        </div>
    </section>

    <section id="sobre">
        <div class="container">
            <div class="section-title">
                <span>Sobre a empresa</span>
                <h2>Presença institucional clara, confiável e alinhada ao segmento.</h2>
                <p>A {escape(razao_social)} mantém uma atuação voltada ao seu ramo principal, com comunicação objetiva e informações organizadas para clientes, fornecedores e parceiros.</p>
            </div>

            <div class="grid-2">
                <article class="card">
                    <h3>Atuação</h3>
                    <p>A empresa se apresenta ao mercado com foco em atendimento profissional, responsabilidade nas informações e estrutura compatível com sua atividade econômica. Seu posicionamento institucional valoriza clareza, compromisso e relacionamento de longo prazo.</p>
                </article>

                <article class="card">
                    <h3>Compromisso</h3>
                    <p>O objetivo é oferecer uma experiência simples e segura para quem busca conhecer a empresa, seus serviços, dados públicos e formas de contato. Cada seção foi organizada para facilitar a navegação e reforçar credibilidade.</p>
                </article>
            </div>
        </div>
    </section>

    <section id="servicos">
        <div class="container">
            <div class="section-title">
                <span>Serviços e soluções</span>
                <h2>Áreas de atuação relacionadas ao CNAE e ao perfil empresarial.</h2>
                <p>Os serviços abaixo representam frentes institucionais compatíveis com o segmento informado, mantendo uma apresentação profissional e objetiva.</p>
            </div>

            <div class="services">
                {servicos_html}
            </div>
        </div>
    </section>

    <section id="diferenciais">
        <div class="container">
            <div class="section-title">
                <span>Diferenciais</span>
                <h2>Uma base profissional para relacionamento com clientes e parceiros.</h2>
            </div>

            <div class="grid-2">
                {diferenciais_html}
            </div>
        </div>
    </section>

    <section id="galeria">
        <div class="container">
            <div class="section-title">
                <span>Galeria institucional</span>
                <h2>Imagens alinhadas ao segmento de atuação.</h2>
                <p>Elementos visuais ajudam a representar o ambiente profissional, os processos e a proposta institucional da empresa.</p>
            </div>

            <div class="gallery">
                <img src="{imagens[0]}" alt="Imagem institucional relacionada ao segmento da empresa">
                <img src="{imagens[1]}" alt="Atendimento e operação profissional">
                <img src="{imagens[2]}" alt="Ambiente de trabalho e relacionamento comercial">
            </div>
        </div>
    </section>

    <section id="dados">
        <div class="container">
            <div class="section-title">
                <span>Dados empresariais</span>
                <h2>Informações cadastrais organizadas para consulta.</h2>
            </div>

            <div class="card info-table">
                <div class="info-line"><span>Razão Social</span><strong>{escape(razao_social)}</strong></div>
                <div class="info-line"><span>Nome Fantasia</span><strong>{escape(nome_fantasia)}</strong></div>
                <div class="info-line"><span>CNPJ</span><strong>{escape(cnpj_formatado)}</strong></div>
                <div class="info-line"><span>CNAE Principal</span><strong>{escape(cnae)}</strong></div>
                <div class="info-line"><span>Categoria</span><strong>{escape(categoria)}</strong></div>
                <div class="info-line"><span>Data de Abertura</span><strong>{escape(data_abertura)}</strong></div>
                <div class="info-line"><span>Endereço</span><strong>{escape(endereco)}</strong></div>
                <div class="info-line"><span>Telefone</span><strong>{escape(telefone)}</strong></div>
                <div class="info-line"><span>WhatsApp</span><strong>{escape(whatsapp)}</strong></div>
                <div class="info-line"><span>E-mail</span><strong>{escape(email)}</strong></div>
            </div>
        </div>
    </section>

    <section id="contato">
        <div class="container">
            <div class="section-title">
                <span>Contato</span>
                <h2>Canais para atendimento e relacionamento institucional.</h2>
                <p>Entre em contato para informações comerciais, atendimento, parcerias ou solicitações relacionadas aos serviços apresentados.</p>
            </div>

            <div class="grid-2">
                <article class="card">
                    <h3>Atendimento</h3>
                    <p><strong>Telefone:</strong> {escape(telefone)}</p>
                    <p><strong>WhatsApp:</strong> {escape(whatsapp)}</p>
                    <p><strong>E-mail:</strong> {escape(email)}</p>
                    <p><strong>Endereço:</strong> {escape(endereco)}</p>
                </article>

                <article class="card">
                    <h3>Mensagem institucional</h3>
                    <p>A empresa mantém seus canais de comunicação preparados para receber demandas de clientes, fornecedores e parceiros, priorizando clareza nas informações e postura profissional.</p>
                </article>
            </div>
        </div>
    </section>

    <section id="politicas">
        <div class="container">
            <div class="section-title">
                <span>Políticas</span>
                <h2>Transparência, uso de informações e navegação responsável.</h2>
            </div>

            <div class="policy-grid">
                <article class="card">
                    <h3>Política de Privacidade</h3>
                    <p>As informações eventualmente fornecidas por visitantes são tratadas com responsabilidade e utilizadas apenas para fins de contato, atendimento e relacionamento institucional.</p>
                </article>

                <article class="card">
                    <h3>Termos de Uso</h3>
                    <p>O acesso a este site implica concordância com o uso das informações para consulta institucional. O conteúdo pode ser atualizado para refletir melhorias nos serviços e dados apresentados.</p>
                </article>

                <article class="card">
                    <h3>Política de Cookies</h3>
                    <p>Cookies podem ser utilizados para melhorar a experiência de navegação, análise de desempenho e funcionamento adequado das páginas.</p>
                </article>
            </div>
        </div>
    </section>
</main>

<footer class="footer">
    <div class="container footer-inner">
        <div>
            <strong>{escape(nome_site)}</strong><br>
            {escape(razao_social)} · {escape(cnpj_formatado)}
        </div>
        <div>
            {escape(endereco)}<br>
            {escape(telefone)} · {escape(email)}
        </div>
    </div>
</footer>

</body>
</html>"""

    return html



def aplicar_personalizacao_site(empresa, nome_site="", telefone_site="", email_site="", endereco_site="", whatsapp_site=""):
    empresa_site = dict(empresa)
    empresa_site["nome_site"] = valor_texto(nome_site, "") or nome_exibicao_empresa(empresa)
    empresa_site["telefone_formatado"] = valor_texto(telefone_site, "")
    empresa_site["whatsapp_site"] = valor_texto(whatsapp_site, "") or valor_texto(telefone_site, "")
    empresa_site["email"] = valor_texto(email_site, "")
    empresa_site["endereco_site"] = valor_texto(endereco_site, "")
    return empresa_site


def gerar_nome_worker_site(site):
    base = (
        valor_texto(site.get("nome_exibicao", ""))
        or valor_texto(site.get("nome_fantasia", ""))
        or valor_texto(site.get("nome_empresarial", ""))
        or "site"
    )
    slug = normalizar_slug_site(base).replace("_", "-")
    cnpj = limpar_cnpj(site.get("cnpj", ""))
    sufixo_cnpj = cnpj[-4:] if cnpj else datetime.now().strftime("%H%M")
    sufixo_id = valor_texto(site.get("id", ""))

    partes = [slug, sufixo_cnpj]

    if sufixo_id:
        partes.append(sufixo_id)

    nome = "-".join(partes)
    nome = re.sub(r"[^a-z0-9-]+", "-", nome.lower()).strip("-")
    nome = re.sub(r"-+", "-", nome)

    if not nome:
        nome = f"site-{sufixo_cnpj}"

    if len(nome) > 63:
        nome = nome[:63].strip("-")

    return nome or f"site-{sufixo_cnpj}"


def gerar_worker_js_site(html):
    html_literal = json.dumps(html, ensure_ascii=False)
    robots_literal = json.dumps("User-agent: *\nAllow: /\n")

    return f"""const HTML = {html_literal};
const ROBOTS = {robots_literal};

async function handleRequest(request) {{
  const url = new URL(request.url);

  if (url.pathname === "/robots.txt") {{
    return new Response(ROBOTS, {{
      headers: {{
        "content-type": "text/plain; charset=UTF-8",
        "cache-control": "public, max-age=3600"
      }}
    }});
  }}

  return new Response(HTML, {{
    headers: {{
      "content-type": "text/html; charset=UTF-8",
      "cache-control": "public, max-age=300"
    }}
  }});
}}

addEventListener("fetch", event => {{
  event.respondWith(handleRequest(event.request));
}});
"""


def obter_config_cloudflare():
    account_id = valor_texto(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    api_token = valor_texto(os.environ.get("CLOUDFLARE_API_TOKEN", ""))
    subdomain = valor_texto(os.environ.get("CLOUDFLARE_WORKERS_SUBDOMAIN", ""))
    subdomain = subdomain.replace("https://", "").replace("http://", "").strip().lower()
    subdomain = subdomain.replace(".workers.dev", "").strip("/")

    return {
        "account_id": account_id,
        "api_token": api_token,
        "subdomain": subdomain
    }


def validar_config_cloudflare():
    config = obter_config_cloudflare()
    faltando = []

    if not config["account_id"]:
        faltando.append("CLOUDFLARE_ACCOUNT_ID")

    if not config["api_token"]:
        faltando.append("CLOUDFLARE_API_TOKEN")

    if not config["subdomain"]:
        faltando.append("CLOUDFLARE_WORKERS_SUBDOMAIN")

    if faltando:
        raise RuntimeError("Variáveis ausentes na Railway: " + ", ".join(faltando))

    return config


def resumir_erros_cloudflare(payload, texto_resposta=""):
    mensagens = []

    if isinstance(payload, dict):
        for erro in payload.get("errors", []) or []:
            if isinstance(erro, dict):
                mensagens.append(valor_texto(erro.get("message", "")))
            else:
                mensagens.append(valor_texto(erro))

        for mensagem in payload.get("messages", []) or []:
            if isinstance(mensagem, dict):
                mensagens.append(valor_texto(mensagem.get("message", "")))
            else:
                mensagens.append(valor_texto(mensagem))

    if not mensagens and texto_resposta:
        mensagens.append(texto_resposta[:500])

    return " | ".join([m for m in mensagens if m]) or "Erro desconhecido na Cloudflare"


def atualizar_publicacao_cloudflare_site(site_id, status, worker_name="", cloudflare_url="", erro=""):
    criar_tabela_sites_gerados()

    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE sites_gerados
                SET
                    cloudflare_worker_name = :worker_name,
                    cloudflare_url = :cloudflare_url,
                    cloudflare_status = :status,
                    cloudflare_erro = :erro,
                    cloudflare_publicado_em = CASE
                        WHEN :status = 'Publicado' THEN CURRENT_TIMESTAMP
                        ELSE cloudflare_publicado_em
                    END
                WHERE id = :site_id
            """),
            {
                "worker_name": worker_name,
                "cloudflare_url": cloudflare_url,
                "status": status,
                "erro": erro,
                "site_id": site_id
            }
        )


def publicar_site_na_cloudflare(site):
    config = validar_config_cloudflare()
    worker_name = gerar_nome_worker_site(site)
    worker_js = gerar_worker_js_site(site.get("html_gerado", ""))

    headers = {
        "Authorization": f"Bearer {config['api_token']}",
        "Content-Type": "application/javascript; charset=UTF-8"
    }

    upload_url = f"https://api.cloudflare.com/client/v4/accounts/{config['account_id']}/workers/scripts/{worker_name}"

    resposta = requests.put(
        upload_url,
        headers=headers,
        data=worker_js.encode("utf-8"),
        timeout=45
    )

    try:
        payload = resposta.json()
    except Exception:
        payload = {}

    if not resposta.ok or payload.get("success") is False:
        erro = resumir_erros_cloudflare(payload, resposta.text)
        raise RuntimeError(erro)

    aviso_subdominio = ""

    try:
        subdomain_url = f"https://api.cloudflare.com/client/v4/accounts/{config['account_id']}/workers/scripts/{worker_name}/subdomain"
        subdomain_resposta = requests.post(
            subdomain_url,
            headers={
                "Authorization": f"Bearer {config['api_token']}",
                "Content-Type": "application/json"
            },
            json={"enabled": True},
            timeout=30
        )

        try:
            subdomain_payload = subdomain_resposta.json()
        except Exception:
            subdomain_payload = {}

        if not subdomain_resposta.ok or subdomain_payload.get("success") is False:
            aviso_subdominio = resumir_erros_cloudflare(subdomain_payload, subdomain_resposta.text)
    except Exception as erro_subdominio:
        aviso_subdominio = str(erro_subdominio)

    cloudflare_url = f"https://{worker_name}.{config['subdomain']}.workers.dev"

    return {
        "worker_name": worker_name,
        "cloudflare_url": cloudflare_url,
        "aviso": aviso_subdominio
    }


def gerar_wrangler_toml_site(nome_worker):
    hoje = datetime.now().strftime("%Y-%m-%d")

    return f"""name = \"{nome_worker}\"
main = \"src/worker.js\"
compatibility_date = \"{hoje}\"
workers_dev = true
"""


def gerar_package_json_worker(nome_worker):
    return json.dumps({
        "name": nome_worker,
        "version": "1.0.0",
        "private": True,
        "scripts": {
            "dev": "wrangler dev",
            "deploy": "wrangler deploy"
        },
        "devDependencies": {
            "wrangler": "latest"
        }
    }, ensure_ascii=False, indent=2)


def gerar_readme_worker(site, nome_worker):
    return f"""LTDAFinder Pro - Cloudflare Workers

Site: {site.get('nome_empresarial', '')}
Arquivo Worker: src/worker.js
Nome sugerido do Worker: {nome_worker}

Como publicar pelo Wrangler:

1. Instale as dependências:
   npm install

2. Faça login na Cloudflare:
   npx wrangler login

3. Publique no workers.dev:
   npx wrangler deploy

Depois do deploy, a URL ficará no padrão:
https://{nome_worker}.SEU-SUBDOMINIO.workers.dev

Observação:
O subdomínio workers.dev depende da configuração da sua conta Cloudflare.
"""

def salvar_site_gerado(dados):
    criar_tabela_sites_gerados()

    campos = [
        "usuario",
        "cnpj",
        "cnpj_formatado",
        "nome_empresarial",
        "nome_fantasia",
        "cnae_principal",
        "categoria_cnae",
        "endereco",
        "telefone",
        "email",
        "meta_tag",
        "modelo_site",
        "nome_arquivo",
        "html_gerado",
        "status",
        "observacoes",
        "nome_exibicao",
        "telefone_exibicao",
        "whatsapp_exibicao",
        "email_exibicao",
        "endereco_exibicao",
        "cloudflare_worker_name",
        "cloudflare_url",
        "cloudflare_status",
        "cloudflare_erro"
    ]

    params = {campo: dados.get(campo, "") for campo in campos}

    if not params.get("cloudflare_status"):
        params["cloudflare_status"] = "Não publicado"

    colunas_sql = ",\n                        ".join(campos)
    valores_sql = ",\n                        ".join([f":{campo}" for campo in campos])
    dialect_name = getattr(getattr(engine, "dialect", None), "name", "postgresql")

    with engine.begin() as conn:
        if dialect_name == "postgresql":
            resultado = conn.execute(
                text(f"""
                    INSERT INTO sites_gerados (
                        {colunas_sql}
                    )
                    VALUES (
                        {valores_sql}
                    )
                    RETURNING id
                """),
                params
            ).fetchone()

            return int(resultado[0])

        conn.execute(
            text(f"""
                INSERT INTO sites_gerados (
                    {colunas_sql}
                )
                VALUES (
                    {valores_sql}
                )
            """),
            params
        )

        resultado = conn.execute(text("SELECT last_insert_rowid()")).fetchone()
        return int(resultado[0])


def listar_sites_gerados(modelo_site="", busca=""):
    criar_tabela_sites_gerados()

    usuario = usuario_atual()
    filtros = []
    params = {}

    if tipo_usuario() != "admin":
        filtros.append("usuario = :usuario")
        params["usuario"] = usuario

    if modelo_site:
        filtros.append("modelo_site = :modelo_site")
        params["modelo_site"] = modelo_site

    if busca:
        filtros.append("""
            (
                LOWER(cnpj) LIKE :busca
                OR LOWER(cnpj_formatado) LIKE :busca
                OR LOWER(nome_empresarial) LIKE :busca
                OR LOWER(nome_fantasia) LIKE :busca
                OR LOWER(modelo_site) LIKE :busca
                OR LOWER(COALESCE(cloudflare_url, '')) LIKE :busca
            )
        """)
        params["busca"] = f"%{busca.lower()}%"

    where_sql = ""

    if filtros:
        where_sql = "WHERE " + " AND ".join(filtros)

    with engine.connect() as conn:
        resultado = conn.execute(
            text(f"""
                SELECT
                    id,
                    usuario,
                    cnpj,
                    cnpj_formatado,
                    nome_empresarial,
                    nome_fantasia,
                    cnae_principal,
                    categoria_cnae,
                    endereco,
                    telefone,
                    email,
                    modelo_site,
                    nome_arquivo,
                    status,
                    observacoes,
                    criado_em,
                    nome_exibicao,
                    telefone_exibicao,
                    whatsapp_exibicao,
                    email_exibicao,
                    endereco_exibicao,
                    cloudflare_worker_name,
                    cloudflare_url,
                    cloudflare_status,
                    cloudflare_publicado_em,
                    cloudflare_erro
                FROM sites_gerados
                {where_sql}
                ORDER BY id DESC
                LIMIT 200
            """),
            params
        ).mappings().fetchall()

    return [dict(row) for row in resultado]


def buscar_site_gerado(site_id):
    criar_tabela_sites_gerados()

    params = {"id": site_id}
    filtros = ["id = :id"]

    if tipo_usuario() != "admin":
        filtros.append("usuario = :usuario")
        params["usuario"] = usuario_atual()

    with engine.connect() as conn:
        resultado = conn.execute(
            text(f"""
                SELECT *
                FROM sites_gerados
                WHERE {" AND ".join(filtros)}
                LIMIT 1
            """),
            params
        ).mappings().fetchone()

    return dict(resultado) if resultado else None


def estatisticas_sites_gerados():
    criar_tabela_sites_gerados()

    params = {}
    where_sql = ""

    if tipo_usuario() != "admin":
        where_sql = "WHERE usuario = :usuario"
        params["usuario"] = usuario_atual()

    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) AS total FROM sites_gerados {where_sql}"),
            params
        ).mappings().fetchone()

        por_modelo = conn.execute(
            text(f"""
                SELECT modelo_site, COUNT(*) AS total
                FROM sites_gerados
                {where_sql}
                GROUP BY modelo_site
                ORDER BY total DESC
            """),
            params
        ).mappings().fetchall()

    return {
        "total": int(total["total"] if total else 0),
        "por_modelo": [dict(row) for row in por_modelo]
    }


def modelo_site_nome(slug):
    return MODELOS_SITE_DICT.get(slug, {}).get("nome", slug)


try:
    criar_tabela_sites_gerados()
except Exception:
    pass


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
            salvar_usuario(usuario, senha, "equipe")
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
    contexto["dashboard_master"] = calcular_dashboard_master(df)
    contexto["evolucao_diaria"] = resumo_evolucao_diaria(15)
    contexto["top_ufs"] = calcular_top_ufs(df, 10)

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
        remover_favorito(cnpj_limpo)
        favorito = False
        acao = "Removido dos favoritos"
    else:
        adicionar_favorito(cnpj_limpo)
        favorito = True
        acao = "Adicionado aos favoritos"

    favoritos = carregar_favoritos()

    try:
        registrar_evento(usuario_atual(), formatar_cnpj(cnpj_limpo), "-", acao)
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
        remover_status(usuario, cnpj_limpo)
        remover_data_uso_cnpj(usuario, cnpj_limpo)
    else:
        status_geral[usuario][cnpj_limpo] = novo_status
        salvar_status(usuario, cnpj_limpo, novo_status)
        registrar_data_uso_cnpj(usuario, cnpj_limpo, novo_status)

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

    form = request.form if request.method == "POST" else {}

    if request.method == "POST":
        df = aplicar_filtros(df, request.form)

        data_inicio = request.form.get("data_inicio", "").strip()
        data_fim = request.form.get("data_fim", "").strip()

        if data_inicio:
            df = df[(df["data_uso_bm_iso"] == "") | (df["data_uso_bm_iso"] >= data_inicio)]

        if data_fim:
            df = df[(df["data_uso_bm_iso"] == "") | (df["data_uso_bm_iso"] <= data_fim)]

        contexto = montar_contexto(df, df_base, request.form)
    else:
        df = df.sort_values(by="capital_social_num", ascending=False)
        contexto = montar_contexto(df, df_base)

    contexto["filtros"]["data_inicio"] = form.get("data_inicio", "")
    contexto["filtros"]["data_fim"] = form.get("data_fim", "")

    return render_template("usados.html", **contexto)



@app.route("/perfis-meta", methods=["GET", "POST"])
@login_obrigatorio
def perfis_meta():
    mensagem = ""
    erro = ""

    if request.method == "POST":
        acao = request.form.get("acao", "")

        if acao == "criar_perfil":
            nome = request.form.get("nome", "").strip()
            cnpj = limpar_cnpj(request.form.get("cnpj_limpo", "").strip())
            proxy = request.form.get("proxy", "").strip()
            navegador = request.form.get("navegador", "").strip()
            conta_facebook = request.form.get("conta_facebook", "").strip()
            senha_facebook = request.form.get("senha_facebook", "").strip()
            observacoes = request.form.get("observacoes", "").strip()

            if not nome:
                erro = "Informe o nome do perfil."
            else:
                perfis = carregar_perfis_meta()
                vinculados = cnpjs_vinculados_perfis()
                empresa = None

                if cnpj and cnpj != "00000000000000":
                    empresa = buscar_empresa_por_cnpj(cnpj)

                    if not empresa:
                        erro = "CNPJ não encontrado na base."
                    elif empresa.get("status_bm") == STATUS_PADRAO:
                        erro = "Este CNPJ ainda está como Disponível. Marque qualquer status antes de vincular ao Perfil Meta."
                    elif cnpj in vinculados:
                        erro = "Este CNPJ já está vinculado a outro perfil."

                if not erro:
                    agora = datetime.now()

                    novo_perfil = {
                        "id": gerar_id_perfil(),
                        "usuario": usuario_atual(),
                        "nome": nome,
                        "cnpj_limpo": cnpj if empresa else "",
                        "razao_social": empresa.get("razao_social", "") if empresa else "",
                        "proxy": proxy,
                        "navegador": navegador,
                        "conta_facebook": conta_facebook,
                        "senha_facebook": senha_facebook,
                        "observacoes": observacoes,
                        "criado_em": agora.strftime("%d/%m/%Y %H:%M"),
                        "criado_em_iso": agora.strftime("%Y-%m-%d"),
                        "atualizado_em": agora.strftime("%d/%m/%Y %H:%M")
                    }

                    perfis.append(novo_perfil)
                    salvar_perfis_meta(perfis)

                    if empresa:
                        registrar_data_uso_cnpj(usuario_atual(), cnpj, empresa.get("status_bm", STATUS_PADRAO))

                    try:
                        cnpj_evento = formatar_cnpj(cnpj) if empresa else "-"
                        registrar_evento(usuario_atual(), cnpj_evento, "-", f"Perfil Meta criado: {nome}")
                    except:
                        pass

                    mensagem = "Perfil Meta criado com sucesso."

        elif acao == "editar_perfil":
            perfil_id = request.form.get("perfil_id", "").strip()
            nome = request.form.get("nome", "").strip()
            proxy = request.form.get("proxy", "").strip()
            navegador = request.form.get("navegador", "").strip()
            conta_facebook = request.form.get("conta_facebook", "").strip()
            senha_facebook = request.form.get("senha_facebook", "").strip()
            observacoes = request.form.get("observacoes", "").strip()

            if not nome:
                erro = "Informe o nome do perfil."
            else:
                perfis = carregar_perfis_meta()
                atualizado = False

                for perfil in perfis:
                    if perfil.get("id") == perfil_id:
                        perfil["nome"] = nome
                        perfil["proxy"] = proxy
                        perfil["navegador"] = navegador
                        perfil["conta_facebook"] = conta_facebook
                        perfil["senha_facebook"] = senha_facebook
                        perfil["observacoes"] = observacoes
                        perfil["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                        atualizado = True
                        break

                if atualizado:
                    salvar_perfis_meta(perfis)
                    mensagem = "Perfil Meta atualizado com sucesso."

                    try:
                        registrar_evento(usuario_atual(), "-", "Perfil Meta", f"Perfil atualizado: {nome}")
                    except:
                        pass
                else:
                    erro = "Perfil não encontrado."

        elif acao == "vincular_cnpj":
            perfil_id = request.form.get("perfil_id", "").strip()
            cnpj = limpar_cnpj(request.form.get("cnpj_limpo", "").strip())

            perfis = carregar_perfis_meta()
            vinculados = cnpjs_vinculados_perfis()
            empresa = buscar_empresa_por_cnpj(cnpj)

            if not empresa:
                erro = "CNPJ não encontrado na base."
            elif empresa.get("status_bm") == STATUS_PADRAO:
                erro = "Este CNPJ ainda está como Disponível. Marque qualquer status antes de vincular ao Perfil Meta."
            elif cnpj in vinculados:
                erro = "Este CNPJ já está vinculado a outro perfil."
            else:
                atualizado = False
                nome_perfil = ""

                for perfil in perfis:
                    if perfil.get("id") == perfil_id:
                        perfil["cnpj_limpo"] = cnpj
                        perfil["razao_social"] = empresa.get("razao_social", "")
                        perfil["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                        nome_perfil = perfil.get("nome", "")
                        atualizado = True
                        break

                if atualizado:
                    salvar_perfis_meta(perfis)
                    registrar_data_uso_cnpj(usuario_atual(), cnpj, empresa.get("status_bm", STATUS_PADRAO))
                    mensagem = "CNPJ vinculado ao perfil com sucesso."

                    try:
                        registrar_evento(usuario_atual(), formatar_cnpj(cnpj), "-", f"CNPJ vinculado ao perfil: {nome_perfil}")
                    except:
                        pass
                else:
                    erro = "Perfil não encontrado."

    perfis = enriquecer_perfis_meta(carregar_perfis_meta())
    busca = request.args.get("busca", "").strip().upper()
    status = request.args.get("status", "").strip()
    data_inicio = request.args.get("data_inicio", "").strip()
    data_fim = request.args.get("data_fim", "").strip()

    if busca:
        perfis = [
            perfil for perfil in perfis
            if busca in str(perfil.get("nome", "")).upper()
            or busca in str(perfil.get("razao_social", "")).upper()
            or busca in str(perfil.get("cnpj_formatado", "")).upper()
            or busca in str(perfil.get("cnpj_limpo", "")).upper()
            or busca in str(perfil.get("conta_facebook", "")).upper()
        ]

    if status:
        perfis = [perfil for perfil in perfis if perfil.get("status_bm") == status]

    if data_inicio or data_fim:
        perfis = [perfil for perfil in perfis if perfil_dentro_periodo(perfil, data_inicio, data_fim)]

    perfis = sorted(perfis, key=lambda item: data_perfil_para_iso(item) + " " + str(item.get("atualizado_em", "")), reverse=True)

    return render_template(
        "perfis_meta.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        perfis=perfis,
        empresas_disponiveis=empresas_disponiveis_para_perfil(),
        resumo_perfis=resumo_perfis_meta(perfis),
        status_opcoes=STATUS_OPCOES,
        filtros={
            "busca": request.args.get("busca", ""),
            "status": request.args.get("status", ""),
            "data_inicio": data_inicio,
            "data_fim": data_fim
        },
        mensagem=mensagem,
        erro=erro
    )


@app.route("/perfis-meta/status/<perfil_id>", methods=["POST"])
@login_obrigatorio
def perfis_meta_status(perfil_id):
    novo_status = request.form.get("status_bm", STATUS_PADRAO)

    if novo_status not in STATUS_OPCOES:
        novo_status = STATUS_PADRAO

    perfis = carregar_perfis_meta()
    perfil_encontrado = None

    for perfil in perfis:
        if perfil.get("id") == perfil_id:
            perfil_encontrado = perfil
            break

    if not perfil_encontrado:
        return jsonify({"ok": False, "erro": "Perfil não encontrado."})

    cnpj = perfil_encontrado.get("cnpj_limpo", "")

    if not cnpj:
        return jsonify({"ok": False, "erro": "Este perfil ainda não possui CNPJ vinculado."})

    usuario = usuario_atual()
    status_geral = carregar_status_bm()

    if usuario not in status_geral or not isinstance(status_geral.get(usuario), dict):
        status_geral[usuario] = {}

    status_antigo = status_geral[usuario].get(cnpj, STATUS_PADRAO)

    if novo_status == STATUS_PADRAO:
        status_geral[usuario].pop(cnpj, None)
        remover_status(usuario, cnpj)
        remover_data_uso_cnpj(usuario, cnpj)
    else:
        status_geral[usuario][cnpj] = novo_status
        salvar_status(usuario, cnpj, novo_status)
        registrar_data_uso_cnpj(usuario, cnpj, novo_status)

    perfil_encontrado["status_bm"] = novo_status
    perfil_encontrado["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    salvar_perfis_meta(perfis)

    registrar_historico_producao(usuario, status_antigo, novo_status)

    try:
        registrar_evento(usuario, formatar_cnpj(cnpj), status_antigo, f"{novo_status} via Perfil Meta")
    except:
        pass

    return jsonify({
        "ok": True,
        "status_bm": novo_status
    })


@app.route("/perfis-meta/excluir/<perfil_id>", methods=["POST"])
@login_obrigatorio
def perfis_meta_excluir(perfil_id):
    perfis = carregar_perfis_meta()
    novos_perfis = [perfil for perfil in perfis if perfil.get("id") != perfil_id]

    if len(novos_perfis) == len(perfis):
        return redirect(url_for("perfis_meta"))

    salvar_perfis_meta(novos_perfis)

    try:
        registrar_evento(usuario_atual(), "-", "-", "Perfil Meta excluído")
    except:
        pass

    return redirect(url_for("perfis_meta"))


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
                salvar_usuario(novo_usuario, senha, tipo)
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
                senha_final = nova_senha if nova_senha else usuarios[alvo]["senha"]

                salvar_usuario(alvo, senha_final, novo_tipo)
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
                excluir_usuario(alvo)
                executar_backup()

                try:
                    registrar_evento(usuario_atual(), "-", f"Usuário existente: {alvo}", "Usuário excluído")
                except:
                    pass

                mensagem = "Usuário excluído com sucesso."

        usuarios = carregar_usuarios()

    return render_template(
        "admin.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        usuarios=usuarios,
        mensagem=mensagem,
        erro=erro,
        estatisticas_gerais=estatisticas_gerais(),
        historico_hoje=resumo_historico_do_dia(),
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
        remover_status(usuario, cnpj_limpo)
        remover_data_uso_cnpj(usuario, cnpj_limpo)
    else:
        salvar_status(usuario, cnpj_limpo, novo_status)
        registrar_data_uso_cnpj(usuario, cnpj_limpo, novo_status)

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




@app.route("/relatorios-bm", methods=["GET", "POST"])
@login_obrigatorio
def relatorios_bm():
    hoje = datetime.now()
    inicio_padrao = (hoje - timedelta(days=5)).strftime("%Y-%m-%d")
    fim_padrao = hoje.strftime("%Y-%m-%d")

    data_inicio = request.values.get("data_inicio", inicio_padrao)
    data_fim = request.values.get("data_fim", fim_padrao)
    usuario_filtro = request.values.get("usuario", "").strip().lower()

    if tipo_usuario() != "admin":
        usuario_filtro = usuario_atual()

    resumo = resumo_relatorio_bm(data_inicio, data_fim, usuario_filtro)
    texto_relatorio = gerar_texto_relatorio_bm(resumo)

    usuarios = sorted(carregar_usuarios().keys()) if tipo_usuario() == "admin" else [usuario_atual()]

    return render_template(
        "relatorios_bm.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        usuarios=usuarios,
        filtros={
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "usuario": usuario_filtro
        },
        resumo=resumo,
        texto_relatorio=texto_relatorio,
        status_opcoes=[status for status in STATUS_OPCOES if status != STATUS_PADRAO]
    )


@app.route("/relatorios-bm/txt", methods=["POST"])
@login_obrigatorio
def relatorios_bm_txt():
    data_inicio = request.form.get("data_inicio", data_hoje())
    data_fim = request.form.get("data_fim", data_hoje())
    usuario_filtro = request.form.get("usuario", "").strip().lower()

    if tipo_usuario() != "admin":
        usuario_filtro = usuario_atual()

    resumo = resumo_relatorio_bm(data_inicio, data_fim, usuario_filtro)
    texto = gerar_texto_relatorio_bm(resumo)

    output = io.BytesIO(texto.encode("utf-8"))
    nome_arquivo = f"relatorio_bm_{data_inicio}_a_{data_fim}.txt"

    return send_file(
        output,
        download_name=nome_arquivo,
        as_attachment=True,
        mimetype="text/plain; charset=utf-8"
    )



@app.route("/sites", methods=["GET"])
@login_obrigatorio
def sites_gerados():
    modelo_site = request.args.get("modelo_site", "").strip()
    busca = request.args.get("busca", "").strip()

    if modelo_site and modelo_site not in MODELOS_SITE_DICT:
        modelo_site = ""

    sites = listar_sites_gerados(modelo_site, busca)
    estatisticas_sites = estatisticas_sites_gerados()

    return render_template(
        "sites.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        sites=sites,
        modelos_site=MODELOS_SITE,
        modelos_site_dict=MODELOS_SITE_DICT,
        modelo_site_nome=modelo_site_nome,
        estatisticas_sites=estatisticas_sites,
        filtros={
            "modelo_site": modelo_site,
            "busca": busca
        }
    )


@app.route("/gerador-site", methods=["POST"])
@login_obrigatorio
def gerador_site_direto():
    cnpj = request.form.get("cnpj", "").strip()

    if not cnpj:
        return redirect(url_for("sites_gerados"))

    return redirect(url_for("gerador_site", cnpj=limpar_cnpj(cnpj)))


@app.route("/gerador-site/<cnpj>", methods=["GET", "POST"])
@login_obrigatorio
def gerador_site(cnpj):
    empresa = buscar_empresa_por_cnpj(cnpj)

    if not empresa:
        abort(404)

    erro = ""
    modelo_site = request.values.get("modelo_site", "institucional").strip()
    meta_tag = request.values.get("meta_tag", "").strip()
    observacoes = request.values.get("observacoes", "").strip()

    if request.method == "POST":
        nome_site = request.form.get("nome_site", "").strip()
        telefone_site = request.form.get("telefone_site", "").strip()
        whatsapp_site = request.form.get("whatsapp_site", "").strip()
        email_site = request.form.get("email_site", "").strip()
        endereco_site = request.form.get("endereco_site", "").strip()
    else:
        nome_site = nome_exibicao_empresa(empresa)
        telefone_site = valor_texto(empresa.get("telefone_formatado", ""))
        whatsapp_site = telefone_site
        email_site = valor_texto(empresa.get("email", ""))
        endereco_site = montar_endereco_empresa(empresa)

    if modelo_site not in MODELOS_SITE_DICT:
        modelo_site = "institucional"

    if request.method == "POST":
        if not meta_tag:
            erro = "Cole a meta tag da Meta antes de gerar o site."
        elif "<meta" not in meta_tag.lower():
            erro = "A meta tag parece inválida. Cole a tag completa começando com <meta."
        else:
            cnpj_limpo = limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", cnpj)))
            empresa_site = aplicar_personalizacao_site(empresa, nome_site, telefone_site, email_site, endereco_site, whatsapp_site)
            html_gerado = gerar_html_site_empresa(empresa_site, meta_tag, modelo_site, observacoes)
            nome_arquivo = gerar_nome_arquivo_site(empresa_site)

            site_id = salvar_site_gerado({
                "usuario": usuario_atual(),
                "cnpj": cnpj_limpo,
                "cnpj_formatado": formatar_cnpj(cnpj_limpo),
                "nome_empresarial": valor_texto(empresa.get("razao_social", "")),
                "nome_fantasia": valor_texto(nome_site) or valor_texto(empresa.get("nome_fantasia", "")),
                "cnae_principal": valor_texto(empresa.get("cnae_principal", "")),
                "categoria_cnae": valor_texto(empresa.get("categoria_cnae", "")),
                "endereco": valor_texto(endereco_site),
                "telefone": valor_texto(telefone_site),
                "email": valor_texto(email_site),
                "nome_exibicao": valor_texto(nome_site),
                "telefone_exibicao": valor_texto(telefone_site),
                "whatsapp_exibicao": valor_texto(whatsapp_site),
                "email_exibicao": valor_texto(email_site),
                "endereco_exibicao": valor_texto(endereco_site),
                "cloudflare_status": "Não publicado",
                "meta_tag": meta_tag,
                "modelo_site": modelo_site,
                "nome_arquivo": nome_arquivo,
                "html_gerado": html_gerado,
                "status": "Gerado",
                "observacoes": observacoes
            })

            try:
                registrar_evento(usuario_atual(), formatar_cnpj(cnpj_limpo), "-", f"Site gerado: {modelo_site_nome(modelo_site)}")
            except:
                pass

            return redirect(url_for("site_gerado_preview", site_id=site_id))

    return render_template(
        "gerador_site.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        empresa=empresa,
        modelos_site=MODELOS_SITE,
        modelo_site=modelo_site,
        meta_tag=meta_tag,
        observacoes=observacoes,
        nome_site=nome_site,
        telefone_site=telefone_site,
        email_site=email_site,
        whatsapp_site=whatsapp_site,
        endereco_site=endereco_site,
        erro=erro
    )


@app.route("/site-gerado/<int:site_id>", methods=["GET"])
@login_obrigatorio
def site_gerado_preview(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    return render_template(
        "site_preview.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        site=site,
        modelo_site_nome=modelo_site_nome,
        cloudflare_msg=request.args.get("cloudflare_msg", ""),
        cloudflare_erro=request.args.get("cloudflare_erro", "")
    )


@app.route("/site-gerado/<int:site_id>/publicar-cloudflare", methods=["POST"])
@login_obrigatorio
def site_gerado_publicar_cloudflare(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    worker_name = site.get("cloudflare_worker_name", "") or gerar_nome_worker_site(site)
    cloudflare_url = site.get("cloudflare_url", "")

    try:
        resultado = publicar_site_na_cloudflare(site)
        worker_name = resultado["worker_name"]
        cloudflare_url = resultado["cloudflare_url"]
        aviso = resultado.get("aviso", "")

        atualizar_publicacao_cloudflare_site(
            site_id,
            "Publicado",
            worker_name,
            cloudflare_url,
            aviso
        )

        try:
            registrar_evento(usuario_atual(), site.get("cnpj_formatado", site.get("cnpj", "")), "Site gerado", f"Publicado na Cloudflare: {cloudflare_url}")
        except Exception:
            pass

        return redirect(url_for("site_gerado_preview", site_id=site_id, cloudflare_msg="Site publicado na Cloudflare com sucesso."))

    except Exception as erro:
        mensagem = str(erro)

        atualizar_publicacao_cloudflare_site(
            site_id,
            "Erro",
            worker_name,
            cloudflare_url,
            mensagem
        )

        return redirect(url_for("site_gerado_preview", site_id=site_id, cloudflare_erro=mensagem))


@app.route("/site-gerado/<int:site_id>/download", methods=["GET"])
@login_obrigatorio
def site_gerado_download(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    html = site.get("html_gerado", "")
    nome_arquivo = site.get("nome_arquivo", "index.html") or "index.html"

    output = io.BytesIO(html.encode("utf-8"))
    output.seek(0)

    return send_file(
        output,
        download_name=nome_arquivo,
        as_attachment=True,
        mimetype="text/html; charset=utf-8"
    )



@app.route("/site-gerado/<int:site_id>/worker-download", methods=["GET"])
@login_obrigatorio
def site_gerado_worker_download(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    worker_js = gerar_worker_js_site(site.get("html_gerado", ""))
    nome_worker = gerar_nome_worker_site(site)
    output = io.BytesIO(worker_js.encode("utf-8"))
    output.seek(0)

    return send_file(
        output,
        download_name=f"{nome_worker}.worker.js",
        as_attachment=True,
        mimetype="application/javascript; charset=utf-8"
    )


@app.route("/site-gerado/<int:site_id>/worker-zip", methods=["GET"])
@login_obrigatorio
def site_gerado_worker_zip(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    nome_worker = gerar_nome_worker_site(site)
    worker_js = gerar_worker_js_site(site.get("html_gerado", ""))
    wrangler_toml = gerar_wrangler_toml_site(nome_worker)
    package_json = gerar_package_json_worker(nome_worker)
    readme = gerar_readme_worker(site, nome_worker)

    output = io.BytesIO()

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("src/worker.js", worker_js)
        zip_file.writestr("wrangler.toml", wrangler_toml)
        zip_file.writestr("package.json", package_json)
        zip_file.writestr("README.txt", readme)

    output.seek(0)

    return send_file(
        output,
        download_name=f"{nome_worker}_cloudflare_worker.zip",
        as_attachment=True,
        mimetype="application/zip"
    )

@app.route("/exportar", methods=["POST"])
@login_obrigatorio
def exportar():
    df_base = carregar_base()
    df = aplicar_filtros(df_base.copy(), request.form)

    colunas = [
        "cnpj_formatado", "razao_social", "capital_formatado", "uf",
        "municipio_nome", "telefone_formatado", "email", "nome_socio",
        "sexo_provavel", "categoria_cnae", "status_bm", "score_ia",
        "ia_recomendacao", "usado_por", "data_uso_bm", "data_inicio_formatada", "cnae_principal"
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
        "score_ia": "Score IA",
        "ia_recomendacao": "Recomendação IA",
        "usado_por": "Usado Por",
        "data_uso_bm": "Data de Uso",
        "data_inicio_formatada": "Data de Abertura",
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