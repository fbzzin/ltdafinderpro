from flask import Flask, render_template, request, send_file, abort, jsonify, redirect, url_for, session
import pandas as pd
from pathlib import Path
import zipfile, io, math, json
from functools import wraps
from datetime import datetime, timedelta
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