from flask import Flask, render_template, request, send_file, abort, jsonify, redirect, url_for, session
import pandas as pd
from pathlib import Path
import zipfile, io, math, json, re, unicodedata, os, hashlib, threading
from functools import wraps
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from html import escape
import requests
from urllib.parse import urlparse
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
from ia_operacional import construir_modelo_ia, avaliar_historico_empresa

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
    "Análise permanente",
    "Restrito",
    "WABA restrita",
    "Conta desabilitada",
    "Checkpoint",
    "Descartado"
]

STATUS_SUCESSO = ["Verificou 250", "Verificou 2k", "Verificou 100k"]
STATUS_NEGATIVOS = ["Restrito", "WABA restrita", "Conta desabilitada", "Checkpoint", "Descartado", "Precisa de mais informações", "Análise permanente"]

try:
    FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
except Exception:
    # Fallback para ambientes Windows sem o pacote tzdata instalado.
    FUSO_BRASILIA = timezone(timedelta(hours=-3), name="America/Sao_Paulo")

FUSO_UTC = timezone.utc
NOME_FUSO_BRASILIA = "America/Sao_Paulo"


def agora_brasilia():
    """Retorna a data/hora atual de Brasília, sem depender do fuso do servidor."""
    return datetime.now(FUSO_BRASILIA)


def texto_data_hora_brasilia(valor=None):
    data = valor or agora_brasilia()

    if data.tzinfo is None:
        data = data.replace(tzinfo=FUSO_BRASILIA)
    else:
        data = data.astimezone(FUSO_BRASILIA)

    return data.strftime("%d/%m/%Y %H:%M")


def converter_data_hora_sql_para_brasilia(valor):
    """Converte timestamps UTC vindos do PostgreSQL/SQLite para exibição em Brasília."""
    if valor is None or str(valor).strip() == "":
        return ""

    if isinstance(valor, datetime):
        data = valor
    else:
        texto_data = str(valor).strip()
        data = None

        try:
            data = datetime.fromisoformat(texto_data.replace("Z", "+00:00"))
        except Exception:
            for formato in [
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
            ]:
                try:
                    data = datetime.strptime(texto_data, formato)
                    break
                except Exception:
                    pass

        if data is None:
            return texto_data

    if data.tzinfo is None:
        data = data.replace(tzinfo=FUSO_UTC)

    return data.astimezone(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")


def converter_texto_legado_utc_para_brasilia(valor):
    """Converte registros antigos gravados como texto pelo servidor em UTC."""
    texto_data = str(valor or "").strip()

    if not texto_data:
        return "", ""

    for formato in ["%d/%m/%Y %H:%M", "%d/%m/%Y"]:
        try:
            data_utc = datetime.strptime(texto_data, formato).replace(tzinfo=FUSO_UTC)
            data_brasilia = data_utc.astimezone(FUSO_BRASILIA)
            formato_saida = "%d/%m/%Y %H:%M" if "%H" in formato else "%d/%m/%Y"
            return data_brasilia.strftime(formato_saida), data_brasilia.strftime("%Y-%m-%d")
        except Exception:
            pass

    return texto_data, ""


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
    dados = carregar_dados_postgres("datas_uso_cnpj", {})
    alterou = False

    if isinstance(dados, dict):
        for registros_usuario in dados.values():
            if not isinstance(registros_usuario, dict):
                continue

            for item in registros_usuario.values():
                if not isinstance(item, dict) or item.get("fuso_horario") == NOME_FUSO_BRASILIA:
                    continue

                data_convertida, data_iso = converter_texto_legado_utc_para_brasilia(item.get("data_hora", ""))

                if data_convertida:
                    item["data_hora"] = data_convertida
                if data_iso:
                    item["data_iso"] = data_iso

                item["fuso_horario"] = NOME_FUSO_BRASILIA
                alterou = True

    if alterou:
        salvar_dados_postgres("datas_uso_cnpj", dados)

    return dados


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
        agora = agora_brasilia()
        dados[usuario][cnpj_limpo] = {
            "data_iso": agora.strftime("%Y-%m-%d"),
            "data_hora": agora.strftime("%d/%m/%Y %H:%M"),
            "status_inicial": status,
            "fuso_horario": NOME_FUSO_BRASILIA
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
    perfis = carregar_dados_postgres("perfis_meta", [])
    alterou = False

    if not isinstance(perfis, list):
        return []

    campos_data_hora = ["criado_em", "atualizado_em", "status_bm_atualizado_em"]

    for perfil in perfis:
        if not isinstance(perfil, dict) or perfil.get("fuso_horario") == NOME_FUSO_BRASILIA:
            continue

        data_iso_principal = ""

        for campo in campos_data_hora:
            data_convertida, data_iso = converter_texto_legado_utc_para_brasilia(perfil.get(campo, ""))

            if data_convertida:
                perfil[campo] = data_convertida
            if campo == "criado_em" and data_iso:
                data_iso_principal = data_iso

        if data_iso_principal:
            perfil["criado_em_iso"] = data_iso_principal

        perfil["fuso_horario"] = NOME_FUSO_BRASILIA
        alterou = True

    if alterou:
        salvar_dados_postgres("perfis_meta", perfis)

    return perfis


def salvar_perfis_meta(perfis):
    for perfil in perfis:
        if isinstance(perfil, dict):
            perfil["fuso_horario"] = NOME_FUSO_BRASILIA

    salvar_dados_postgres("perfis_meta", perfis)


_CACHE_MODELO_IA = {
    "assinatura": None,
    "modelo": None,
}


def assinatura_modelo_ia(perfis):
    itens = []

    for perfil in perfis if isinstance(perfis, list) else []:
        itens.append((
            str(perfil.get("id", "")),
            str(perfil.get("cnpj_limpo", "")),
            str(perfil.get("status_bm", "")),
            str(perfil.get("status_bm_atualizado_em", "")),
            str(perfil.get("atualizado_em", "")),
        ))

    return hash(tuple(sorted(itens)))


def obter_modelo_ia_operacional(forcar=False):
    perfis = carregar_perfis_meta()

    try:
        versao_base = BASE_FINAL.stat().st_mtime_ns
    except Exception:
        versao_base = 0

    assinatura = (assinatura_modelo_ia(perfis), versao_base)

    if (
        not forcar
        and _CACHE_MODELO_IA.get("modelo") is not None
        and _CACHE_MODELO_IA.get("assinatura") == assinatura
    ):
        return _CACHE_MODELO_IA["modelo"]

    modelo = construir_modelo_ia(perfis, BASE_FINAL)
    modelo["gerado_em"] = texto_data_hora_brasilia()

    _CACHE_MODELO_IA["assinatura"] = assinatura
    _CACHE_MODELO_IA["modelo"] = modelo
    return modelo


def gerar_id_perfil():
    return agora_brasilia().strftime("%Y%m%d%H%M%S%f")


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
        "logradouro",
        "numero",
        "complemento",
        "cep",
        "bairro",
        "bairro_distrito",
        "uf",
        "municipio",
        "municipio_nome",
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

    for coluna_endereco in ["logradouro", "numero", "complemento", "cep", "bairro", "bairro_distrito"]:
        if coluna_endereco not in df.columns:
            df[coluna_endereco] = ""

    municipio_mapeado = df["municipio"].map(municipios)

    if "municipio_nome" in df.columns:
        municipio_salvo = df["municipio_nome"].fillna("").astype(str).str.strip()
        df["municipio_nome"] = municipio_salvo.where(municipio_salvo.ne(""), municipio_mapeado)
    else:
        df["municipio_nome"] = municipio_mapeado

    df["municipio_nome"] = df["municipio_nome"].fillna("")
    df["endereco_completo"] = df.apply(lambda linha: montar_endereco_empresa(linha), axis=1)

    for coluna in [
        "razao_social",
        "logradouro",
        "numero",
        "complemento",
        "cep",
        "bairro",
        "bairro_distrito",
        "uf",
        "municipio",
        "municipio_nome",
        "endereco_completo",
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
        "endereco_completo",
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
            item["endereco_completo"] = empresa.get("endereco_completo", "Endereço não informado")
            status_empresa = empresa.get("status_bm", STATUS_PADRAO)
            status_salvo = status_perfil_salvo(item)
            item["status_bm"] = status_empresa if status_empresa != STATUS_PADRAO else status_salvo
            item["capital_formatado"] = empresa.get("capital_formatado", "")
            item["data_uso_bm"] = empresa.get("data_uso_bm", "")
            item["data_uso_bm_iso"] = empresa.get("data_uso_bm_iso", "")
        else:
            item["cnpj_formatado"] = formatar_cnpj(cnpj_item) if cnpj_item else ""
            item["razao_social"] = item.get("razao_social", "")
            item["uf"] = item.get("uf", "")
            item["municipio_nome"] = item.get("municipio_nome", "")
            item["endereco_completo"] = item.get("endereco_completo", "Endereço não informado")
            item["status_bm"] = item.get("status_bm", STATUS_PADRAO)
            item["data_uso_bm"] = item.get("data_uso_bm", "")
            item["data_uso_bm_iso"] = item.get("data_uso_bm_iso", "")

        perfis_enriquecidos.append(item)

    return perfis_enriquecidos




# ============================================================
# PERSISTÊNCIA DE STATUS DOS PERFIS META
# O perfil guarda um snapshot do status para não voltar a Disponível
# quando a tabela de status for recarregada ou quando houver deploy.
# ============================================================

def status_perfil_salvo(perfil):
    status = str(perfil.get("status_bm", "") or "").strip()
    return status if status in STATUS_OPCOES else STATUS_PADRAO


def sincronizar_status_perfis_meta(cnpj, novo_status, usuario=""):
    cnpj_limpo = limpar_cnpj(cnpj)
    novo_status = novo_status if novo_status in STATUS_OPCOES else STATUS_PADRAO

    if not cnpj_limpo:
        return

    perfis = carregar_perfis_meta()
    alterou = False
    agora = agora_brasilia().strftime("%d/%m/%Y %H:%M")

    for perfil in perfis:
        perfil_cnpj = limpar_cnpj(perfil.get("cnpj_limpo", "")) if perfil.get("cnpj_limpo") else ""

        if perfil_cnpj == cnpj_limpo:
            perfil["status_bm"] = novo_status
            perfil["status_bm_usuario"] = usuario or perfil.get("status_bm_usuario", "")
            perfil["status_bm_atualizado_em"] = agora
            perfil["atualizado_em"] = agora
            alterou = True

    if alterou:
        salvar_perfis_meta(perfis)


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
        "Análise permanente": 0,
        "Restrito": 0,
        "WABA restrita": 0,
        "Conta desabilitada": 0,
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
        contagem.get("Análise permanente", 0) +
        contagem.get("Restrito", 0) +
        contagem.get("WABA restrita", 0) +
        contagem.get("Conta desabilitada", 0) +
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
    return agora_brasilia().strftime("%Y-%m-%d")


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

    hoje = agora_brasilia()

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
        hoje = agora_brasilia()
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


def avaliar_ia_empresa(empresa, modelo_ia=None, detalhado=False):
    score_regras = 40
    motivos_regras = []
    pontos_regras = []

    capital = float(empresa.get("capital_social_num", 0) or 0)
    idade = int(empresa.get("idade_empresa", 0) or 0)
    telefone = str(empresa.get("telefone_formatado", "")).strip()
    email = str(empresa.get("email", "")).strip()
    categoria = str(empresa.get("categoria_cnae", "")).strip()
    status_bm = str(empresa.get("status_bm", STATUS_PADRAO)).strip()
    usado_global = bool(empresa.get("usado_global", False))

    if capital >= 1000000:
        score_regras += 25
        motivos_regras.append("Capital social acima de R$ 1 milhão")
    elif capital >= 500000:
        score_regras += 20
        motivos_regras.append("Capital social acima de R$ 500 mil")
    elif capital >= 100000:
        score_regras += 10
        motivos_regras.append("Capital social acima de R$ 100 mil")
    else:
        pontos_regras.append("Capital social baixo")

    if telefone:
        score_regras += 10
        motivos_regras.append("Possui telefone cadastrado")
    else:
        pontos_regras.append("Telefone não encontrado")

    if email:
        score_regras += 10
        motivos_regras.append("Possui e-mail cadastrado")
    else:
        pontos_regras.append("E-mail não encontrado")

    if idade >= 5:
        score_regras += 15
        motivos_regras.append("Empresa consolidada com mais de 5 anos")
    elif idade >= 2:
        score_regras += 10
        motivos_regras.append("Empresa com mais de 2 anos")
    elif idade >= 1:
        score_regras += 5
        motivos_regras.append("Empresa com mais de 1 ano")
    else:
        pontos_regras.append("Empresa muito recente")

    if categoria and categoria.lower() not in ["outros", "indefinido", ""]:
        score_regras += 5
        motivos_regras.append("Categoria CNAE identificada")
    else:
        pontos_regras.append("Categoria pouco específica")

    if usado_global:
        score_regras -= 20
        pontos_regras.append("Este CNPJ já possui histórico global de uso")

    penalidades_status = {
        "BM em Análise": (-15, "Este CNPJ já está em BM em Análise"),
        "Usado em BM": (-20, "Este CNPJ já foi usado em BM"),
        "Verificou 250": (-10, "Este CNPJ já verificou 250"),
        "Verificou 2k": (-10, "Este CNPJ já verificou 2k"),
        "Verificou 100k": (-10, "Este CNPJ já verificou 100k"),
        "Restrito": (-55, "Histórico de restrição"),
        "Checkpoint": (-45, "Histórico de checkpoint"),
        "Descartado": (-60, "Marcado como descartado"),
        "Precisa de mais informações": (-40, "Histórico de precisa de mais informações"),
        "Análise permanente": (-45, "Histórico de análise permanente"),
        "WABA restrita": (-50, "Histórico de WABA restrita"),
        "Conta desabilitada": (-55, "Histórico de conta desabilitada"),
    }

    if status_bm in penalidades_status:
        pontos, descricao = penalidades_status[status_bm]
        score_regras += pontos
        pontos_regras.append(descricao)

    score_regras = max(0, min(100, int(score_regras)))
    modelo_ia = modelo_ia or obter_modelo_ia_operacional()
    historico = avaliar_historico_empresa(empresa, modelo_ia, detalhado=detalhado)

    fase = modelo_ia.get("fase", {}) if modelo_ia else {}
    peso_historico = float(fase.get("peso_historico", 0) or 0)
    peso_regras = float(fase.get("peso_regras", 1) or 1)

    score_historico = int(historico.get("score_historico", 50) or 50)
    score = round((score_regras * peso_regras) + (score_historico * peso_historico))
    score = max(0, min(100, int(score)))

    if status_bm in STATUS_NEGATIVOS:
        score = min(score, 25)
        recomendacao = "🔴 Evitar"
        classe = "evitar"
    elif status_bm != STATUS_PADRAO or usado_global:
        score = min(score, 59)
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

    if not motivos_regras:
        motivos_regras.append("Dados insuficientes para recomendação forte")

    motivos_historicos = list(historico.get("motivos", []))
    pontos_historicos = list(historico.get("pontos_atencao", []))

    resultado = {
        "score_ia": score,
        "score_ia_regras": score_regras,
        "score_ia_historico": score_historico,
        "ia_peso_regras": round(peso_regras * 100),
        "ia_peso_historico": round(peso_historico * 100),
        "ia_recomendacao": recomendacao,
        "ia_classe": classe,
        "ia_confianca_historica": historico.get("confianca", 0),
        "ia_amostras_semelhantes": historico.get("amostras_semelhantes", 0),
        "ia_taxa_estimada": historico.get("taxa_estimada", 50.0),
        "ia_resultado_mais_comum": historico.get("resultado_mais_comum", "Sem dados"),
        "ia_fase": fase.get("nome", "Fase 1 · Regras fixas"),
        "ia_total_amostras": modelo_ia.get("total_concluidas", 0) if modelo_ia else 0,
    }

    if detalhado:
        resultado.update({
            "ia_motivos": motivos_regras + [f"Histórico: {item}" for item in motivos_historicos],
            "ia_pontos_atencao": pontos_regras + [f"Histórico: {item}" for item in pontos_historicos],
            "ia_motivos_regras": motivos_regras,
            "ia_pontos_regras": pontos_regras,
            "ia_motivos_historicos": motivos_historicos,
            "ia_pontos_historicos": pontos_historicos,
            "ia_fatores_historicos": historico.get("fatores", []),
        })

    return resultado



TERMOS_EMPRESA_RAZAO = {
    "COMERCIO", "COMERCIAL", "SERVICO", "SERVICOS", "SOLUCOES", "TECNOLOGIA",
    "DIGITAL", "MARKETING", "CONSULTORIA", "ASSESSORIA", "TRANSPORTES",
    "TRANSPORTE", "LOGISTICA", "CONSTRUCAO", "CONSTRUCOES", "ENGENHARIA",
    "INDUSTRIA", "INDUSTRIAL", "DISTRIBUIDORA", "DISTRIBUICAO", "IMPORTACAO",
    "EXPORTACAO", "MERCADO", "MINIMERCADO", "MERCEARIA", "ARMAZEM", "LOJA",
    "RESTAURANTE", "LANCHONETE", "PIZZARIA", "PADARIA", "DOCERIA", "DOCES",
    "HAMBURGUERIA", "BAR", "LOUNGE", "CLINICA", "LABORATORIO", "HOSPITAL",
    "ESCOLA", "COLEGIO", "CURSO", "CURSOS", "ACADEMIA", "AUTO", "PECAS",
    "AUTOPECAS", "OFICINA", "MECANICA", "ESTETICA", "BELEZA", "COSMETICOS",
    "FARMACIA", "DROGARIA", "IMOBILIARIA", "IMOVEIS", "PARTICIPACOES",
    "HOLDING", "INVESTIMENTOS", "ADMINISTRADORA", "EMPREENDIMENTOS",
    "INCORPORADORA", "AGRO", "AGROPECUARIA", "FAZENDA", "SITIO", "POSTO",
    "HOTEL", "POUSADA", "TURISMO", "VIAGENS", "EVENTOS", "PRODUCOES",
    "COMUNICACAO", "PUBLICIDADE", "PROPAGANDA", "ALIMENTOS", "BEBIDAS",
    "VAREJISTA", "ATACADISTA", "MATERIAIS", "ELETRICA", "ELETRICO",
    "INFORMATICA", "TELECOM", "GAMING", "BET", "BRASIL", "NACIONAL",
    "CENTER", "CENTRO", "SHOP", "STORE", "GROUP", "GRUPO", "MASTER",
    "PRIME", "ALPHA", "OMEGA", "NOVA", "NOVO", "IDEAL", "REAL", "TOP",
    "PLUS", "MAX", "VIP", "EXPRESS", "DELIVERY", "COMPANY", "EMPRESA",
    "CASA", "CASAS", "ATELIE", "ATELIER", "MODAS", "CONFECCOES", "MOVEIS",
    "MOVEIS", "EQUIPAMENTOS", "SUPRIMENTOS", "REPRESENTACOES", "REPRESENTACAO"
}

CONECTORES_NOME_RAZAO = {"DA", "DE", "DO", "DAS", "DOS", "E"}

NOMES_MASCULINOS_RAZAO = {
    "JOAO", "JOSE", "ANTONIO", "FRANCISCO", "CARLOS", "PAULO", "PEDRO",
    "LUCAS", "LUIZ", "LUIS", "MARCOS", "MARCO", "MATEUS", "MATHEUS",
    "GABRIEL", "RAFAEL", "DANIEL", "BRUNO", "FELIPE", "ANDRE", "RODRIGO",
    "RICARDO", "MARCELO", "ALEXANDRE", "GUSTAVO", "LEANDRO", "EDUARDO",
    "FERNANDO", "ROBERTO", "ROGERIO", "CLAUDIO", "FABIO", "FABIANO",
    "DIEGO", "THIAGO", "TIAGO", "VINICIUS", "VITOR", "VICTOR", "HUGO",
    "MIGUEL", "ARTHUR", "DAVI", "BERNARDO", "HEITOR", "ENZO", "NICOLAS",
    "NICOLAU", "SAMUEL", "BENJAMIN", "LORENZO", "MURILO", "CAIO",
    "ARMANDO", "ALBERTO", "ALFREDO", "ALCIR", "ALCIDES", "ALAN", "ALEX",
    "ADRIANO", "ADILSON", "ANDERSON", "WAGNER", "WELLINGTON", "WASHINGTON",
    "WILLIAM", "ROBSON", "RONALDO", "RENATO", "MAURICIO", "NELSON",
    "OSVALDO", "SEBASTIAO", "GERALDO", "MANOEL", "MANUEL", "EDSON",
    "ELIAS", "EVANDRO", "IVAN", "JULIO", "JULIANO", "LEONARDO", "ALESSANDRO",
    "AUGUSTO", "ADALBERTO", "ADEMIR", "AMILTON", "HAMILTON", "SIDNEY", "SIDNEI"
}

NOMES_FEMININOS_RAZAO = {
    "MARIA", "ANA", "ANTONIA", "FRANCISCA", "ADRIANA", "JULIANA",
    "MARCIA", "FERNANDA", "PATRICIA", "ALINE", "SANDRA", "CAMILA",
    "AMANDA", "BRUNA", "JESSICA", "LETICIA", "JULIA", "LUCIA", "LUCIANA",
    "VANESSA", "CRISTIANE", "CRISTINA", "CLAUDIA", "ANDREA", "RAQUEL",
    "CARLA", "CAROLINA", "DANIELA", "PRISCILA", "ROBERTA", "RENATA",
    "SIMONE", "TALITA", "TATIANE", "TATIANA", "ELIANE", "ELAINE",
    "ELISANGELA", "FABIANA", "GABRIELA", "ISABELA", "ISABELLA",
    "LARISSA", "MARIANA", "NATALIA", "PAULA", "VITORIA", "VANUSA",
    "ALICE", "HELENA", "LAURA", "VALENTINA", "HELOISA", "LORENA",
    "MANUELA", "LUIZA", "MELISSA", "LIVIA", "CECILIA", "BEATRIZ",
    "CLARA", "SOPHIA", "SOFIA", "YASMIN", "EMANUELA", "MIRELLA",
    "ALESSANDRA", "ALEXANDRA", "APARECIDA", "ROSA", "TEREZINHA",
    "TERESA", "VERA", "MARTA", "SUELI", "REGINA", "DENISE", "MONICA",
    "ANGELA", "ANGELICA", "SILVIA", "SILVANA", "SOLANGE", "VALERIA", "VIVIANE"
}


def normalizar_texto_razao(texto):
    texto = str(texto or "").strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    texto = texto.upper()
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def limpar_razao_social_para_nome(razao_social):
    texto = normalizar_texto_razao(razao_social)

    # Alguns MEIs antigos possuem o número de inscrição no início da razão social,
    # por exemplo: "07.416.669 SIDNEY DA SILVA SOARES".
    texto = re.sub(r"^(?:\d+\s+){1,4}", "", texto)

    termos_juridicos = [
        "SOCIEDADE EMPRESARIA LIMITADA",
        "SOCIEDADE LIMITADA UNIPESSOAL",
        "SOCIEDADE UNIPESSOAL",
        "EMPRESA INDIVIDUAL DE RESPONSABILIDADE LIMITADA",
        "EMPRESARIO INDIVIDUAL",
        "MICROEMPREENDEDOR INDIVIDUAL",
        "LIMITADA",
        "LTDA",
        "EIRELI",
        "SLU",
        "MEI",
        "ME",
        "EPP"
    ]

    for termo in termos_juridicos:
        texto = re.sub(rf"\b{re.escape(termo)}\b", " ", texto)

    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def classificar_razao_social_nome_pessoa(razao_social):
    razao_limpa = limpar_razao_social_para_nome(razao_social)

    if not razao_limpa:
        return {"razao_limpa": "", "eh_nome_pessoa": False, "sexo": "Indefinido"}

    partes_brutas = razao_limpa.split()
    partes_validas = [parte for parte in partes_brutas if parte not in CONECTORES_NOME_RAZAO]

    if len(partes_validas) < 2:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    if len(partes_validas) > 7:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    if any(parte in TERMOS_EMPRESA_RAZAO for parte in partes_validas):
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    primeiro = partes_validas[0]

    if primeiro.isdigit():
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    if primeiro in NOMES_MASCULINOS_RAZAO:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": True, "sexo": "Masculino"}

    if primeiro in NOMES_FEMININOS_RAZAO:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": True, "sexo": "Feminino"}

    # Aceita razões sociais com iniciais, exemplo:
    # A ALBUQUERQUE MARTINS CORREA LTDA
    # A K DA SILVA VIEIRA LTDA
    if len(primeiro) == 1 and primeiro.isalpha() and len(partes_validas) >= 3:
        segundo_nome_real = ""
        for parte in partes_validas[1:]:
            if len(parte) > 1:
                segundo_nome_real = parte
                break

        sexo = "Indefinido"

        if segundo_nome_real in NOMES_MASCULINOS_RAZAO:
            sexo = "Masculino"
        elif segundo_nome_real in NOMES_FEMININOS_RAZAO:
            sexo = "Feminino"

        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": True, "sexo": sexo}

    return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

# ============================================================
# CACHE INTELIGENTE DA BASE
# Mantém os dados estáticos processados em memória e recalcula
# apenas os campos que mudam durante a operação.
# ============================================================

_CACHE_BASE_ESTATICA = {
    "assinatura": None,
    "df": None,
}

_CACHE_AVALIACOES_IA = {
    "assinatura": None,
    "df": None,
}

_CACHE_BASE_LOCK = threading.RLock()

CAMPOS_IA_CACHE = [
    "score_ia",
    "score_ia_regras",
    "score_ia_historico",
    "ia_peso_regras",
    "ia_peso_historico",
    "ia_recomendacao",
    "ia_classe",
    "ia_confianca_historica",
    "ia_amostras_semelhantes",
    "ia_taxa_estimada",
    "ia_resultado_mais_comum",
    "ia_fase",
    "ia_total_amostras",
]

PENALIDADES_STATUS_IA = {
    "BM em Análise": -15,
    "Usado em BM": -20,
    "Verificou 250": -10,
    "Verificou 2k": -10,
    "Verificou 100k": -10,
    "Restrito": -55,
    "Checkpoint": -45,
    "Descartado": -60,
    "Precisa de mais informações": -40,
    "Análise permanente": -45,
    "WABA restrita": -50,
    "Conta desabilitada": -55,
}


def assinatura_arquivo_base():
    try:
        status_arquivo = BASE_FINAL.stat()
        return status_arquivo.st_mtime_ns, status_arquivo.st_size
    except Exception:
        return 0, 0


def construir_base_estatica():
    """Lê e prepara apenas os dados que não mudam entre requisições."""
    df = pd.read_csv(BASE_FINAL, dtype=str)

    if "natureza_juridica" not in df.columns:
        df["natureza_juridica"] = ""

    df["natureza_juridica"] = (
        df["natureza_juridica"]
        .fillna("")
        .astype(str)
        .str.replace(".0", "", regex=False)
        .str.strip()
    )

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

    if "municipio" not in df.columns:
        df["municipio"] = ""
    if "uf" not in df.columns:
        df["uf"] = ""

    for coluna_endereco in ["logradouro", "numero", "complemento", "cep", "bairro", "bairro_distrito"]:
        if coluna_endereco not in df.columns:
            df[coluna_endereco] = ""

    df["cnpj_formatado"] = df["cnpj_limpo"].apply(formatar_cnpj)
    df["capital_formatado"] = df["capital_social_num"].apply(formatar_capital)

    municipio_mapeado = df["municipio"].map(municipios)

    if "municipio_nome" in df.columns:
        municipio_salvo = df["municipio_nome"].fillna("").astype(str).str.strip()
        df["municipio_nome"] = municipio_salvo.where(municipio_salvo.ne(""), municipio_mapeado)
    else:
        df["municipio_nome"] = municipio_mapeado

    df["municipio_nome"] = df["municipio_nome"].fillna("")
    df["endereco_completo"] = df.apply(lambda linha: montar_endereco_empresa(linha), axis=1)

    if "telefone_formatado" not in df.columns:
        if "ddd1" not in df.columns:
            df["ddd1"] = ""
        if "telefone1" not in df.columns:
            df["telefone1"] = ""

        df["telefone_formatado"] = (
            df["ddd1"].fillna("").astype(str).str.replace(".0", "", regex=False)
            + df["telefone1"].fillna("").astype(str).str.replace(".0", "", regex=False)
        )

    for coluna in [
        "telefone_formatado", "email", "nome_socio", "razao_social",
        "nome_fantasia", "sexo_provavel", "categoria_cnae",
        "data_inicio", "cnae_principal", "situacao_cadastral", "logradouro",
        "numero", "complemento", "cep", "bairro", "bairro_distrito",
        "endereco_completo", "uf", "municipio", "municipio_nome"
    ]:
        if coluna not in df.columns:
            df[coluna] = ""
        else:
            df[coluna] = df[coluna].fillna("")

    df["cnae_principal_codigo"] = (
        df["cnae_principal"]
        .astype(str)
        .str.replace(".0", "", regex=False)
        .str.strip()
        .str.zfill(7)
    )

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

    if "sexo_provavel" in df.columns:
        df["sexo_socio"] = df["sexo_provavel"].replace("", "Indefinido")
    else:
        df["sexo_socio"] = "Indefinido"

    classificacao_razao = df["razao_social"].apply(classificar_razao_social_nome_pessoa)

    df["razao_limpa_nome"] = classificacao_razao.apply(lambda item: item["razao_limpa"])
    df["razao_nome_pessoa"] = classificacao_razao.apply(lambda item: item["eh_nome_pessoa"])
    df["sexo_razao"] = classificacao_razao.apply(lambda item: item["sexo"])
    df["sexo_provavel"] = df["sexo_razao"].replace("", "Indefinido")

    # As LTDAs continuam seguindo a regra de nome de pessoa pela razão social.
    # Os registros 2135 desta base foram confirmados no Simples.zip como MEI.
    df = df[
        (df["natureza_juridica"] == "2135") |
        (df["razao_nome_pessoa"] == True)
    ].copy()

    df["categoria_cnae"] = df["categoria_cnae"].replace("", "Outros")
    df["idade_empresa"] = df["data_inicio"].apply(calcular_idade_empresa)
    df["data_inicio_formatada"] = df["data_inicio"].apply(formatar_data_brasil)

    return df.reset_index(drop=True)


def carregar_base_estatica():
    assinatura = assinatura_arquivo_base()

    with _CACHE_BASE_LOCK:
        cache_valido = (
            _CACHE_BASE_ESTATICA.get("df") is not None
            and _CACHE_BASE_ESTATICA.get("assinatura") == assinatura
        )

        if cache_valido:
            return _CACHE_BASE_ESTATICA["df"]

        df = construir_base_estatica()
        _CACHE_BASE_ESTATICA["assinatura"] = assinatura
        _CACHE_BASE_ESTATICA["df"] = df

        # Uma base nova exige novas avaliações históricas.
        _CACHE_AVALIACOES_IA["assinatura"] = None
        _CACHE_AVALIACOES_IA["df"] = None

        return df


def assinatura_cache_avaliacoes_ia():
    return (
        assinatura_arquivo_base(),
        _CACHE_MODELO_IA.get("assinatura"),
    )


def carregar_avaliacoes_ia_estaticas(df_base, modelo_ia):
    assinatura = assinatura_cache_avaliacoes_ia()

    with _CACHE_BASE_LOCK:
        cache_valido = (
            _CACHE_AVALIACOES_IA.get("df") is not None
            and _CACHE_AVALIACOES_IA.get("assinatura") == assinatura
            and len(_CACHE_AVALIACOES_IA["df"]) == len(df_base)
        )

        if cache_valido:
            return _CACHE_AVALIACOES_IA["df"]

        registros = df_base.to_dict(orient="records")
        avaliacoes = [
            avaliar_ia_empresa(registro, modelo_ia)
            for registro in registros
        ]

        df_avaliacoes = pd.DataFrame(avaliacoes, index=df_base.index)

        for campo in CAMPOS_IA_CACHE:
            if campo not in df_avaliacoes.columns:
                df_avaliacoes[campo] = None

        df_avaliacoes = df_avaliacoes[CAMPOS_IA_CACHE]
        _CACHE_AVALIACOES_IA["assinatura"] = assinatura
        _CACHE_AVALIACOES_IA["df"] = df_avaliacoes
        return df_avaliacoes


def montar_mapas_datas_usuario(datas_uso, usuario):
    mapa_data_hora = {}
    mapa_data_iso = {}

    registros = datas_uso.get(usuario, {}) if isinstance(datas_uso, dict) else {}
    if not isinstance(registros, dict):
        return mapa_data_hora, mapa_data_iso

    for cnpj, item in registros.items():
        cnpj_limpo = limpar_cnpj(cnpj)
        if not cnpj_limpo:
            continue

        if isinstance(item, dict):
            mapa_data_hora[cnpj_limpo] = item.get("data_hora", "") or "Não registrada"
            mapa_data_iso[cnpj_limpo] = item.get("data_iso", "") or ""
        elif isinstance(item, str) and item.strip():
            mapa_data_hora[cnpj_limpo] = item.strip()
            mapa_data_iso[cnpj_limpo] = ""

    return mapa_data_hora, mapa_data_iso


def montar_mapas_usos_globais(status_geral):
    usos_por_cnpj = {}
    texto_por_cnpj = {}

    if not isinstance(status_geral, dict):
        return usos_por_cnpj, texto_por_cnpj

    for nome_usuario, registros in status_geral.items():
        if not isinstance(registros, dict):
            continue

        for cnpj, status in registros.items():
            status = str(status or "").strip()
            if not status or status == STATUS_PADRAO:
                continue

            cnpj_limpo = limpar_cnpj(cnpj)
            if not cnpj_limpo:
                continue

            usos_por_cnpj.setdefault(cnpj_limpo, []).append({
                "usuario": nome_usuario,
                "status": status,
            })

    for cnpj, usos in usos_por_cnpj.items():
        texto_por_cnpj[cnpj] = " | ".join(
            f"{item['usuario']}: {item['status']}"
            for item in usos
        )

    return usos_por_cnpj, texto_por_cnpj


def aplicar_ajustes_ia_dinamicos(df):
    penalidade_status = df["status_bm"].map(PENALIDADES_STATUS_IA).fillna(0).astype(int)
    penalidade_uso = df["usado_global"].astype(int) * -20

    score_regras = (
        pd.to_numeric(df["score_ia_regras"], errors="coerce").fillna(40)
        + penalidade_status
        + penalidade_uso
    ).clip(lower=0, upper=100).astype(int)

    df["score_ia_regras"] = score_regras

    peso_regras = pd.to_numeric(df["ia_peso_regras"], errors="coerce").fillna(100) / 100
    peso_historico = pd.to_numeric(df["ia_peso_historico"], errors="coerce").fillna(0) / 100
    score_historico = pd.to_numeric(df["score_ia_historico"], errors="coerce").fillna(50)

    score = (
        (score_regras * peso_regras)
        + (score_historico * peso_historico)
    ).round().clip(lower=0, upper=100).astype(int)

    mascara_negativa = df["status_bm"].isin(STATUS_NEGATIVOS)
    mascara_atencao = (~mascara_negativa) & (
        (df["status_bm"] != STATUS_PADRAO) |
        (df["usado_global"] == True)
    )

    score = score.where(~mascara_negativa, score.clip(upper=25))
    score = score.where(~mascara_atencao, score.clip(upper=59))
    df["score_ia"] = score.astype(int)

    df["ia_recomendacao"] = "🔴 Evitar"
    df["ia_classe"] = "evitar"

    mascara_excelente = (~mascara_negativa) & (~mascara_atencao) & (df["score_ia"] >= 80)
    mascara_regular = (~mascara_negativa) & (~mascara_atencao) & (df["score_ia"] >= 60) & (~mascara_excelente)

    df.loc[mascara_atencao | mascara_regular, "ia_recomendacao"] = "🟡 Atenção"
    df.loc[mascara_atencao | mascara_regular, "ia_classe"] = "atencao"
    df.loc[mascara_excelente, "ia_recomendacao"] = "🟢 Excelente"
    df.loc[mascara_excelente, "ia_classe"] = "excelente"

    return df


def carregar_base():
    usuario = usuario_atual()
    df_base = carregar_base_estatica()

    # A cópia rasa compartilha somente os blocos estáticos e recebe colunas
    # dinâmicas novas, reduzindo tempo e consumo de memória por requisição.
    df = df_base.copy(deep=False)

    favoritos = carregar_favoritos()
    status_geral = carregar_status_bm()
    datas_uso = carregar_datas_uso_cnpj()

    favoritos = {
        limpar_cnpj(cnpj)
        for cnpj in favoritos
        if limpar_cnpj(cnpj)
    }

    status_usuario_atual = status_geral.get(usuario, {}) if isinstance(status_geral, dict) else {}
    if not isinstance(status_usuario_atual, dict):
        status_usuario_atual = {}

    status_usuario_atual = {
        limpar_cnpj(cnpj): str(status or STATUS_PADRAO).strip() or STATUS_PADRAO
        for cnpj, status in status_usuario_atual.items()
        if limpar_cnpj(cnpj)
    }

    mapa_data_hora, mapa_data_iso = montar_mapas_datas_usuario(datas_uso, usuario)
    usos_por_cnpj, texto_usos_por_cnpj = montar_mapas_usos_globais(status_geral)

    df["favorito"] = df["cnpj_limpo"].isin(favoritos)
    df["status_bm"] = df["cnpj_limpo"].map(status_usuario_atual).fillna(STATUS_PADRAO)
    df["bm_utilizada"] = df["status_bm"] != STATUS_PADRAO
    df["data_uso_bm"] = df["cnpj_limpo"].map(mapa_data_hora).fillna("Não registrada")
    df["data_uso_bm_iso"] = df["cnpj_limpo"].map(mapa_data_iso).fillna("")
    df["usos_globais"] = df["cnpj_limpo"].map(usos_por_cnpj)
    df["usos_globais"] = df["usos_globais"].apply(
        lambda usos: usos if isinstance(usos, list) else []
    )
    df["usado_global"] = df["cnpj_limpo"].isin(usos_por_cnpj.keys())
    df["usado_por"] = df["cnpj_limpo"].map(texto_usos_por_cnpj).fillna("")

    modelo_ia = obter_modelo_ia_operacional()
    avaliacoes_ia = carregar_avaliacoes_ia_estaticas(df_base, modelo_ia)

    for campo in CAMPOS_IA_CACHE:
        df[campo] = avaliacoes_ia[campo].to_numpy(copy=False)

    return aplicar_ajustes_ia_dinamicos(df)

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
    natureza_juridica = str(form.get("natureza_juridica", "") or "").strip()
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

    if natureza_juridica in {"2135", "2062"}:
        df = df[df["natureza_juridica"] == natureza_juridica]

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
            + " " + df["email"].astype(str).str.upper()
            + " " + df["telefone_formatado"].astype(str).str.upper()
            + " " + df["municipio_nome"].astype(str).str.upper()
            + " " + df["endereco_completo"].astype(str).str.upper()
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
            "natureza_juridica": form.get("natureza_juridica", ""),
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


# ============================================================
# ESTATÍSTICAS DA DASHBOARD MASTER BASEADAS NOS PERFIS META
# A Dashboard Master usa os Perfis Meta como fonte operacional
# e deixa o status do CNPJ apenas como apoio de base/disponibilidade.
# ============================================================

def usuario_perfil_meta(perfil):
    usuario = str(
        perfil.get("status_bm_usuario")
        or perfil.get("usuario")
        or perfil.get("operador")
        or "Sem usuário"
    ).strip()

    return usuario if usuario else "Sem usuário"


def status_operacional_perfil_meta(perfil):
    status = str(perfil.get("status_bm", "") or "").strip()
    return status if status in STATUS_OPCOES else STATUS_PADRAO


def data_texto_para_iso(data_texto):
    data_texto = str(data_texto or "").strip()

    if not data_texto:
        return ""

    if re.match(r"^\d{4}-\d{2}-\d{2}$", data_texto):
        return data_texto

    for formato in ["%d/%m/%Y %H:%M", "%d/%m/%Y"]:
        try:
            return datetime.strptime(data_texto, formato).strftime("%Y-%m-%d")
        except:
            pass

    return ""


def data_operacional_perfil_meta(perfil):
    for campo in ["status_bm_atualizado_em", "atualizado_em", "criado_em", "criado_em_iso"]:
        data_iso = data_texto_para_iso(perfil.get(campo, ""))

        if data_iso:
            return data_iso

    return ""


def perfis_meta_operacionais(perfis=None):
    if perfis is None:
        perfis = carregar_perfis_meta()

    operacionais = []

    for perfil in perfis:
        status = status_operacional_perfil_meta(perfil)

        if status == STATUS_PADRAO:
            continue

        item = dict(perfil)
        item["status_bm"] = status
        item["usuario_operacional"] = usuario_perfil_meta(item)
        item["data_operacional_iso"] = data_operacional_perfil_meta(item)
        operacionais.append(item)

    return operacionais


def estatisticas_gerais_perfis_meta(perfis=None):
    perfis = perfis_meta_operacionais(perfis)
    usuarios_cadastrados = carregar_usuarios()

    total_geral = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}
    por_usuario = {}

    for usuario in usuarios_cadastrados.keys():
        por_usuario[usuario] = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}

    for perfil in perfis:
        status = perfil.get("status_bm", STATUS_PADRAO)
        usuario = perfil.get("usuario_operacional") or usuario_perfil_meta(perfil)

        if status not in total_geral:
            continue

        total_geral[status] += 1

        if usuario not in por_usuario:
            por_usuario[usuario] = {status_item: 0 for status_item in STATUS_OPCOES if status_item != STATUS_PADRAO}

        por_usuario[usuario][status] += 1

    dados_usuarios = []

    for usuario, contagem in por_usuario.items():
        total_usuario = sum(contagem.values())
        sucesso_usuario = sum(contagem.get(status, 0) for status in STATUS_SUCESSO)
        taxa_usuario = (sucesso_usuario / total_usuario * 100) if total_usuario else 0

        dados_usuarios.append({
            "usuario": usuario,
            "contagem": contagem,
            "total": total_usuario,
            "sucesso": sucesso_usuario,
            "taxa_sucesso": taxa_usuario
        })

    total = sum(total_geral.values())
    sucesso = sum(total_geral.get(status, 0) for status in STATUS_SUCESSO)
    taxa_sucesso = (sucesso / total * 100) if total else 0

    dados_usuarios = sorted(dados_usuarios, key=lambda item: (item["sucesso"], item["total"]), reverse=True)

    return {
        "usuarios": dados_usuarios,
        "contagem": total_geral,
        "total": total,
        "sucesso": sucesso,
        "taxa_sucesso": taxa_sucesso
    }


def resumo_perfis_meta_do_dia(data=None, perfis=None):
    if data is None:
        data = data_hoje()

    perfis = perfis_meta_operacionais(perfis)
    total_geral = {status: 0 for status in STATUS_OPCOES if status != STATUS_PADRAO}
    por_usuario = {}

    for perfil in perfis:
        if perfil.get("data_operacional_iso", "") != data:
            continue

        status = perfil.get("status_bm", STATUS_PADRAO)
        usuario = perfil.get("usuario_operacional") or usuario_perfil_meta(perfil)

        if status not in total_geral:
            continue

        total_geral[status] += 1

        if usuario not in por_usuario:
            por_usuario[usuario] = {status_item: 0 for status_item in STATUS_OPCOES if status_item != STATUS_PADRAO}

        por_usuario[usuario][status] += 1

    usuarios = []

    for usuario, contagem in por_usuario.items():
        total_usuario = sum(contagem.values())
        sucesso_usuario = sum(contagem.get(status, 0) for status in STATUS_SUCESSO)
        taxa_usuario = (sucesso_usuario / total_usuario * 100) if total_usuario else 0

        usuarios.append({
            "usuario": usuario,
            "contagem": contagem,
            "total": total_usuario,
            "sucesso": sucesso_usuario,
            "taxa_sucesso": taxa_usuario
        })

    total = sum(total_geral.values())
    sucesso = sum(total_geral.get(status, 0) for status in STATUS_SUCESSO)
    taxa_sucesso = (sucesso / total * 100) if total else 0

    usuarios = sorted(usuarios, key=lambda item: item["total"], reverse=True)

    return {
        "data": data,
        "usuarios": usuarios,
        "contagem": total_geral,
        "total": total,
        "sucesso": sucesso,
        "taxa_sucesso": taxa_sucesso
    }


def resumo_evolucao_diaria_perfis_meta(dias=15, perfis=None):
    labels = []
    valores = []
    sucessos = []

    hoje = agora_brasilia()

    for indice in range(dias - 1, -1, -1):
        data_obj = hoje - timedelta(days=indice)
        data_chave = data_obj.strftime("%Y-%m-%d")
        data_label = data_obj.strftime("%d/%m")
        resumo = resumo_perfis_meta_do_dia(data_chave, perfis)

        labels.append(data_label)
        valores.append(resumo["total"])
        sucessos.append(resumo["sucesso"])

    return {
        "labels": labels,
        "valores": valores,
        "sucessos": sucessos
    }


def cnpjs_usados_por_perfis_meta(perfis=None):
    if perfis is None:
        perfis = carregar_perfis_meta()

    usados = set()

    for perfil in perfis:
        cnpj = limpar_cnpj(perfil.get("cnpj_limpo", "")) if perfil.get("cnpj_limpo") else ""

        if cnpj and cnpj != "00000000000000":
            usados.add(cnpj)

    return usados


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


def calcular_dashboard_master(df, estatisticas=None, perfis_meta=None):
    total_base = len(df)
    total_favoritos = int(df["favorito"].sum()) if "favorito" in df.columns else 0
    capital_medio = df["capital_social_num"].mean() if len(df) else 0

    cnpjs_usados = set()

    if "cnpj_limpo" in df.columns and "bm_utilizada" in df.columns:
        cnpjs_usados.update(
            str(cnpj)
            for cnpj in df[df["bm_utilizada"] == True]["cnpj_limpo"].dropna().astype(str).tolist()
            if str(cnpj).strip()
        )

    cnpjs_usados.update(cnpjs_usados_por_perfis_meta(perfis_meta))

    if "cnpj_limpo" in df.columns:
        cnpjs_base = set(df["cnpj_limpo"].dropna().astype(str).tolist())
        total_usados = len(cnpjs_usados.intersection(cnpjs_base))
    else:
        total_usados = len(cnpjs_usados)

    total_disponiveis = max(0, total_base - total_usados)

    if estatisticas is None:
        estatisticas = estatisticas_gerais_perfis_meta(perfis_meta)

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
        "slug": "ia_2_0",
        "nome": "Template IA 2.0",
        "descricao": "Site institucional simples e completo, com identidade empresarial, atividade descrita, telefone associado, dados oficiais, privacidade e termos.",
        "icone": "🧠"
    },
    {
        "slug": "ia_3_0",
        "nome": "Template IA 3.0",
        "descricao": "Modelo cadastral limpo e responsivo, com os dados oficiais disponíveis da empresa e sem exibir campos vazios.",
        "icone": "🪪"
    },
    {
        "slug": "meta_waba_clean",
        "nome": "Meta/WABA Clean",
        "descricao": "Modelo limpo para verificação Meta/WhatsApp: CNPJ, atividade, contato, privacidade e termos sem excesso visual.",
        "icone": "🧼"
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
            "cloudflare_slug_personalizado": "TEXT",
            "cloudflare_worker_name": "TEXT",
            "cloudflare_url": "TEXT",
            "cloudflare_dominio_base": "TEXT",
            "cloudflare_hostname": "TEXT",
            "cloudflare_status": "TEXT DEFAULT 'Não publicado'",
            "cloudflare_publicado_em": "TIMESTAMP",
            "cloudflare_erro": "TEXT",
            "politica_html": "TEXT",
            "termos_html": "TEXT",
            "arquivos_verificacao": "TEXT",
            "dns_txt_nome": "TEXT",
            "dns_txt_valor": "TEXT",
            "atualizado_em": "TIMESTAMP",
            "versao_template": "TEXT DEFAULT '1.0'"
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


def obter_campo_endereco(empresa, *nomes):
    for nome in nomes:
        valor = valor_texto(empresa.get(nome, ""))
        if valor:
            return valor
    return ""


def formatar_cep_endereco(cep):
    cep_texto = valor_texto(cep, "")
    digitos = re.sub(r"\D", "", cep_texto)

    if len(digitos) == 8:
        return f"{digitos[:2]}.{digitos[2:5]}-{digitos[5:]}"

    return cep_texto


def numero_ja_esta_no_logradouro(logradouro, numero):
    """Evita endereços como 'AVENIDA X 1946, 1946'.

    Alguns registros da Receita trazem o número anexado ao final do campo
    de logradouro e, ao mesmo tempo, repetido na coluna própria de número.
    A base continua preservando os dados brutos; esta proteção atua apenas
    na apresentação do endereço.
    """
    logradouro_texto = valor_texto(logradouro, "")
    numero_texto = valor_texto(numero, "")

    if not logradouro_texto or not numero_texto:
        return False

    def normalizar_parte(valor):
        valor = unicodedata.normalize("NFKD", valor)
        valor = valor.encode("ascii", "ignore").decode("ascii").upper()
        valor = re.sub(r"[^A-Z0-9]+", " ", valor)
        return re.sub(r"\s+", " ", valor).strip()

    logradouro_normalizado = normalizar_parte(logradouro_texto)
    numero_normalizado = normalizar_parte(numero_texto)

    if not logradouro_normalizado or not numero_normalizado:
        return False

    # Exige algum contexto de rua antes do número para reduzir falsos
    # positivos em nomes curtos como "RUA 25".
    if len(logradouro_normalizado.split()) < 3:
        return False

    return logradouro_normalizado.endswith(f" {numero_normalizado}")


def montar_endereco_empresa(empresa):
    endereco_personalizado = valor_texto(empresa.get("endereco_site", ""))

    if endereco_personalizado:
        return endereco_personalizado

    logradouro = obter_campo_endereco(empresa, "logradouro", "descricao_logradouro")
    numero = obter_campo_endereco(empresa, "numero", "numero_estabelecimento")
    complemento = obter_campo_endereco(empresa, "complemento")
    cep = formatar_cep_endereco(obter_campo_endereco(empresa, "cep"))
    bairro = obter_campo_endereco(empresa, "bairro", "bairro_distrito", "distrito")
    municipio = obter_campo_endereco(empresa, "municipio_nome", "nome_municipio")

    # O campo `municipio` da Receita é um código numérico. Só o utiliza
    # diretamente quando já vier com um nome legível, nunca como "7107".
    if not municipio:
        municipio_bruto = obter_campo_endereco(empresa, "municipio")
        if municipio_bruto and not municipio_bruto.isdigit():
            municipio = municipio_bruto

    uf = obter_campo_endereco(empresa, "uf")

    partes = []

    if logradouro:
        partes.append(logradouro)

    if numero and not numero_ja_esta_no_logradouro(logradouro, numero):
        partes.append(numero)

    if complemento:
        partes.append(complemento)

    if cep:
        partes.append(f"CEP {cep}")

    for valor in [bairro, municipio, uf]:
        if valor:
            partes.append(valor)

    return ", ".join(partes) if partes else "Endereço não informado"


def identificar_segmento_site(empresa):
    texto = " ".join([
        valor_texto(empresa.get("cnae_principal", "")),
        valor_texto(empresa.get("cnae_principal_descricao", "")),
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




# ============================================================
# MODELO META/WABA CLEAN
# Site enxuto para reduzir ruído em análise de verificação.
# ============================================================

def gerar_html_site_meta_waba_clean(empresa, meta_tag, observacoes=""):
    nome_site = valor_texto(empresa.get("nome_site", "")) or nome_exibicao_empresa(empresa)
    razao_social = valor_publico(empresa.get("razao_social", ""))
    nome_fantasia = valor_publico(empresa.get("nome_fantasia", ""))
    cnpj_formatado = formatar_cnpj(limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", ""))))
    cnae = valor_publico(empresa.get("cnae_principal", ""))
    categoria = valor_publico(empresa.get("categoria_cnae", ""))
    telefone = valor_texto(empresa.get("telefone_formatado", ""))
    whatsapp = valor_texto(empresa.get("whatsapp_site", "") or empresa.get("telefone_formatado", ""))
    endereco = montar_endereco_empresa(empresa)
    data_abertura = valor_publico(empresa.get("data_inicio_formatada", ""))
    municipio = valor_texto(empresa.get("municipio_nome", "")) or valor_texto(empresa.get("municipio", ""))
    uf = valor_texto(empresa.get("uf", ""))

    titulo_atividade = atividade_site_sem_codigo(empresa)
    nome_publico = nome_site or nome_fantasia or razao_social
    localidade = " - ".join([item for item in [municipio, uf] if item]) or "Brasil"

    telefone_base = whatsapp or telefone
    telefone_digits = re.sub(r"\D", "", telefone_base or "")

    if telefone_digits and not telefone_digits.startswith("55"):
        whatsapp_numero = f"55{telefone_digits}"
    else:
        whatsapp_numero = telefone_digits

    if whatsapp_numero:
        telefone_display = f"+{whatsapp_numero[:2]} {whatsapp_numero[2:]}" if whatsapp_numero.startswith("55") else whatsapp_numero
        telefone_href = f"+{whatsapp_numero}"
        whatsapp_href = f"https://wa.me/{whatsapp_numero}"
    else:
        telefone_display = "Não informado"
        telefone_href = "#"
        whatsapp_href = "#"

    logo_letra = "E"
    for caractere in normalizar_texto_razao(nome_publico):
        if caractere.isalnum():
            logo_letra = caractere[0]
            break

    valores = {
        "__META_TAG__": meta_tag.strip(),
        "__NOME_PUBLICO__": escape(nome_publico),
        "__LOGO_LETRA__": escape(logo_letra),
        "__RAZAO_SOCIAL__": escape(razao_social),
        "__CNPJ__": escape(cnpj_formatado),
        "__DATA_ABERTURA__": escape(data_abertura),
        "__ATIVIDADE__": escape(titulo_atividade),
        "__LOCALIDADE__": escape(localidade),
        "__TELEFONE_DISPLAY__": escape(telefone_display),
        "__TELEFONE_HREF__": escape(telefone_href),
        "__WHATSAPP_HREF__": escape(whatsapp_href),
        "__ANO__": escape(str(agora_brasilia().year)),
    }

    template = """<!doctype html>
<html lang="pt-BR"><head>
__META_TAG__
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <title>__NOME_PUBLICO__ | __ATIVIDADE__</title>
  <meta name="description" content="__ATIVIDADE__ em __LOCALIDADE__. Qualidade e confiança. Contato via WhatsApp.">
  <meta property="og:title" content="__NOME_PUBLICO__">
  <meta property="og:description" content="__ATIVIDADE__ em __LOCALIDADE__.">
  <meta property="og:type" content="website">

  <style>
:root{
  --primary: #5b2aa7;
  --primary-2: #4b61d3;
  --primary-3: #5ea0ff;

  --text: #0f172a;
  --muted: #64748b;

  --bg: #ffffff;
  --soft: #f1f6fb;

  --card: #ffffff;
  --border: rgba(15, 23, 42, .08);

  --radius: 14px;
  --shadow: 0 10px 25px rgba(15, 23, 42, .08);
  --max: 1100px;
}

/* Reset leve */
*{ box-sizing: border-box; }
html,body{ margin:0; padding:0; }
body{
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
  color: var(--text);
  background: var(--bg);
}

/* Layout */
.gdl-container{ width: 100%; max-width: var(--max); margin: 0 auto; padding: 0 18px; }

/* Topbar */
.gdl-topbar{
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(255,255,255,.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
}
.gdl-topbar-inner{
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 74px;
  gap: 16px;
}

.gdl-brand{
  display: flex;
  align-items: center;
  gap: 12px;
  text-decoration: none;
  color: inherit;
  min-width: 230px;
}
.gdl-logo{
  width: 44px; height: 44px;
  border-radius: 12px;
  display: grid; place-items: center;
  color: #fff;
  font-weight: 800;
  background: linear-gradient(135deg, var(--primary), var(--primary-2));
  box-shadow: 0 10px 20px rgba(91, 42, 167, .25);
  flex: 0 0 auto;
}
.gdl-brand-text{ line-height: 1.1; }
.gdl-brand-text strong{ display: block; font-size: 14px; letter-spacing: .3px; }
.gdl-brand-text span{ display: block; font-size: 12px; color: var(--muted); margin-top: 3px; }

.gdl-nav{
  display: flex;
  gap: 18px;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
}
.gdl-nav a{
  text-decoration: none;
  color: #1f2937;
  font-size: 14px;
  padding: 8px 10px;
  border-radius: 10px;
  transition: .2s ease;
}
.gdl-nav a:hover{ background: rgba(2, 6, 23, .04); }

.gdl-btn{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-radius: 12px;
  padding: 11px 16px;
  font-weight: 700;
  font-size: 14px;
  border: 1px solid transparent;
  text-decoration: none;
  cursor: pointer;
  transition: .2s ease;
  white-space: nowrap;
}
.gdl-btn-primary{
  color: #fff;
  background: linear-gradient(135deg, var(--primary), var(--primary-2));
  box-shadow: 0 12px 25px rgba(75, 97, 211, .25);
}
.gdl-btn-primary:hover{ transform: translateY(-1px); filter: brightness(1.02); }
.gdl-btn-outline{
  color: #fff;
  border-color: rgba(255,255,255,.35);
  background: rgba(255,255,255,.10);
}
.gdl-btn-outline:hover{ background: rgba(255,255,255,.16); transform: translateY(-1px); }

/* Hero */
.gdl-hero{
  background: linear-gradient(90deg, var(--primary) 0%, var(--primary-2) 55%, var(--primary-3) 100%);
  color: #fff;
  padding: 58px 0 62px;
}
.gdl-hero h1{
  margin: 0;
  font-size: clamp(28px, 4vw, 44px);
  letter-spacing: -0.02em;
  line-height: 1.12;
  text-align: center;
}
.gdl-hero p{
  margin: 14px auto 0;
  max-width: 780px;
  font-size: 16px;
  opacity: .92;
  text-align: center;
}
.gdl-hero-cta{
  display: flex;
  gap: 14px;
  justify-content: center;
  margin-top: 26px;
  flex-wrap: wrap;
}
.gdl-btn-light{
  background: #fff;
  color: var(--primary);
  border: 1px solid rgba(255,255,255,.6);
}
.gdl-btn-light:hover{ transform: translateY(-1px); }

/* Cards */
.gdl-section{ padding: 34px 0; }
.gdl-grid-3{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 18px;
  margin-top: 18px;
}
.gdl-card{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px;
  min-height: 140px;
}
.gdl-card h3{ margin: 0 0 10px; font-size: 16px; }
.gdl-card p{
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.6;
}
.gdl-list{
  margin: 0;
  padding-left: 16px;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.8;
}
.gdl-contact{
  display: grid;
  gap: 10px;
  margin-top: 6px;
}
.gdl-contact a{
  text-decoration: none;
  color: #111827;
  font-weight: 600;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.gdl-ic{
  width: 30px; height: 30px;
  border-radius: 10px;
  background: rgba(2, 6, 23, .04);
  display: grid;
  place-items: center;
  font-size: 14px;
}

/* Dados Oficiais */
.gdl-soft{
  background: var(--soft);
  padding: 34px 0 40px;
}
.gdl-soft h2{
  margin: 0 0 14px;
  font-size: 22px;
  letter-spacing: -0.01em;
}
.gdl-info-box{
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px;
  box-shadow: 0 8px 20px rgba(15, 23, 42, .06);
}
.gdl-info-grid{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.gdl-info-item small{
  display: block;
  color: var(--muted);
  font-weight: 700;
  font-size: 12px;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: .06em;
}
.gdl-info-item div{
  font-weight: 700;
  color: #111827;
  font-size: 14px;
  line-height: 1.35;
}

/* Footer */
.gdl-footer{
  background: #0b1220;
  color: rgba(255,255,255,.82);
  padding: 26px 0;
}
.gdl-footer-inner{
  display: grid;
  grid-template-columns: 1.5fr .8fr;
  gap: 18px;
  align-items: start;
}
.gdl-footer a{ color: rgba(255,255,255,.86); text-decoration: none; }
.gdl-footer a:hover{ text-decoration: underline; }
.gdl-foot-title{ font-weight: 800; color: #fff; margin-bottom: 8px; }
.gdl-foot-muted{ color: rgba(255,255,255,.70); font-size: 14px; line-height: 1.6; }
.gdl-foot-links{
  display: grid;
  gap: 10px;
  justify-content: end;
}

/* Modal */
.gdl-modal-overlay{
  position: fixed;
  inset: 0;
  background: rgba(2,6,23,.65);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  padding: 20px;
}
.gdl-modal-overlay.active{ display: flex; }

.gdl-modal-box{
  background: #fff;
  width: 100%;
  max-width: 720px;
  max-height: 80vh;
  border-radius: 14px;
  padding: 26px;
  position: relative;
  overflow-y: auto;
  box-shadow: 0 30px 60px rgba(0,0,0,.25);
}
.gdl-modal-box h2{
  margin: 0 0 10px;
  font-size: 22px;
}
.gdl-modal-box p{
  font-size: 14px;
  line-height: 1.7;
  color: #334155;
}
.gdl-modal-close{
  position: absolute;
  top: 12px;
  right: 14px;
  border: none;
  background: transparent;
  font-size: 22px;
  cursor: pointer;
}
.gdl-modal-content{ display: none; }
.gdl-modal-content.active{ display: block; }

/* Responsivo */
@media (max-width: 980px){
  .gdl-grid-3{ grid-template-columns: 1fr; margin-top: 18px; }
  .gdl-info-grid{ grid-template-columns: 1fr 1fr; }
  .gdl-topbar-inner{ height: auto; padding: 14px 0; align-items: flex-start; }
  .gdl-brand{ min-width: unset; }
}
@media (max-width: 520px){
  .gdl-info-grid{ grid-template-columns: 1fr; }
  .gdl-nav{ gap: 8px; }
}
/* Painel simples de automação CNPJ */
.gdl-editor{
  background:#07111f;
  color:#fff;
  padding:14px 0;
  border-bottom:1px solid rgba(255,255,255,.12);
}
.gdl-editor strong{display:block;margin-bottom:10px;font-size:16px;}
.gdl-editor-grid{display:grid;grid-template-columns:1fr auto auto;gap:10px;align-items:center;}
.gdl-editor input,.gdl-editor textarea{
  width:100%;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.08);color:#fff;border-radius:10px;padding:11px 12px;outline:none;
}
.gdl-editor input::placeholder,.gdl-editor textarea::placeholder{color:rgba(255,255,255,.65);}
.gdl-editor button{
  border:0;border-radius:10px;padding:11px 14px;font-weight:800;cursor:pointer;background:#fff;color:#111827;
}
.gdl-editor button:last-child{background:#22c55e;color:#06120b;}
.gdl-editor details{margin-top:10px;}
.gdl-editor summary{cursor:pointer;color:rgba(255,255,255,.86);font-weight:700;}
.gdl-editor-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px;}
.gdl-editor-fields textarea{grid-column:1/-1;min-height:72px;resize:vertical;}
.gdl-editor small{display:block;color:rgba(255,255,255,.65);margin-top:8px;}
@media(max-width:760px){.gdl-editor-grid,.gdl-editor-fields{grid-template-columns:1fr}.gdl-editor button{width:100%;}}

  </style>
</head>
<body>

  

  <div class="gdl-topbar">
    <div class="gdl-container">
      <div class="gdl-topbar-inner">
        <a class="gdl-brand" href="#home" aria-label="Ir para o início">
          <div class="gdl-logo">__LOGO_LETRA__</div>
          <div class="gdl-brand-text">
            <strong>__NOME_PUBLICO__</strong>
            <span>Atendimento profissional</span>
          </div>
        </a>
        <nav class="gdl-nav" aria-label="Menu principal">
          <a href="#home">Home</a>
          <a href="#sobre">Sobre</a>
          <a href="#servicos">Serviços</a>
          <a class="gdl-btn gdl-btn-primary" href="#contato">Contato</a>
        </nav>
      </div>
    </div>
  </div>

  <section id="home" class="gdl-hero">
    <div class="gdl-container">
      <h1>__ATIVIDADE__</h1>
      <p>Atendimento profissional em <strong>__LOCALIDADE__</strong>. Qualidade e confiança.</p>
      <div class="gdl-hero-cta">
        <a class="gdl-btn gdl-btn-light" href="#servicos">Ver Serviços</a>
        <a class="gdl-btn gdl-btn-outline" href="__WHATSAPP_HREF__" target="_blank" rel="noopener">Chamar no WhatsApp</a>
      </div>
    </div>
  </section>

  <section class="gdl-section">
    <div class="gdl-container">
      <div class="gdl-grid-3">
        <div id="sobre" class="gdl-card">
          <h3>Quem somos</h3>
          <p>A empresa <strong>__RAZAO_SOCIAL__</strong> atua com <strong>__ATIVIDADE__</strong>.</p>
        </div>
        <div id="servicos" class="gdl-card">
          <h3>O que fazemos</h3>
          <ul class="gdl-list">
            <li>__NOME_PUBLICO__</li>
            <li>Atendimento profissional</li>
            <li>Qualidade e confiança</li>
            <li>Suporte e agilidade</li>
          </ul>
        </div>
        <div id="contato" class="gdl-card">
          <h3>Contato</h3>
          <div class="gdl-contact">
            <a href="tel:__TELEFONE_HREF__"><span class="gdl-ic">📞</span><span>__TELEFONE_DISPLAY__</span></a>
            <a href="__WHATSAPP_HREF__" target="_blank" rel="noopener"><span class="gdl-ic">💬</span>Chamar no WhatsApp</a>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section class="gdl-soft">
    <div class="gdl-container">
      <h2>Dados Oficiais</h2>
      <div class="gdl-info-box">
        <div class="gdl-info-grid">
          <div class="gdl-info-item"><small>Razão Social</small><div>__RAZAO_SOCIAL__</div></div>
          <div class="gdl-info-item"><small>CNPJ</small><div>__CNPJ__</div></div>
          <div class="gdl-info-item"><small>Data de Abertura</small><div>__DATA_ABERTURA__</div></div>
          <div class="gdl-info-item"><small>Atividade Principal</small><div>__ATIVIDADE__</div></div>
        </div>
      </div>
    </div>
  </section>

  <footer class="gdl-footer">
    <div class="gdl-container">
      <div class="gdl-footer-inner">
        <div>
          <div class="gdl-foot-title">__NOME_PUBLICO__</div>
          <div class="gdl-foot-muted">CNPJ: <span>__CNPJ__</span><br>© <span>__ANO__</span> Todos os direitos reservados.</div>
        </div>
        <div class="gdl-foot-links">
          <a href="javascript:void(0)" onclick="openModal('privacidade')">Política de Privacidade</a>
          <a href="javascript:void(0)" onclick="openModal('termos')">Termos de Uso</a>
        </div>
      </div>
    </div>
  </footer>

  <div id="modalOverlay" class="gdl-modal-overlay" onclick="closeModal(event)">
    <div class="gdl-modal-box" role="dialog" aria-modal="true" aria-label="Conteúdo legal">
      <button class="gdl-modal-close" onclick="closeModal()" aria-label="Fechar">✕</button>
      <div id="modal-privacidade" class="gdl-modal-content">
        <h2>Política de Privacidade</h2>
        <p>A <strong>__NOME_PUBLICO__</strong>, inscrita no CNPJ <strong>__CNPJ__</strong>, respeita a sua privacidade e está comprometida com a proteção dos dados pessoais dos usuários.</p>
        <p>As informações coletadas neste site são utilizadas exclusivamente para fins de atendimento, comunicação e melhoria dos serviços prestados, em conformidade com a Lei Geral de Proteção de Dados.</p>
      </div>
      <div id="modal-termos" class="gdl-modal-content">
        <h2>Termos de Uso</h2>
        <p>O acesso a este site implica na aceitação dos presentes Termos de Uso. A <strong>__NOME_PUBLICO__</strong> atua no segmento de <strong>__ATIVIDADE__</strong>.</p>
        <p>Todo o conteúdo deste site é protegido por direitos autorais, sendo proibida a reprodução sem autorização prévia.</p>
      </div>
    </div>
  </div>

  <script>
(function(){
  const overlay = document.getElementById('modalOverlay');
  const priv = document.getElementById('modal-privacidade');
  const termos = document.getElementById('modal-termos');

  function setActive(type){
    priv.classList.remove('active');
    termos.classList.remove('active');
    if(type === 'privacidade') priv.classList.add('active');
    if(type === 'termos') termos.classList.add('active');
  }

  window.openModal = function(type){
    overlay.classList.add('active');
    setActive(type);
  }

  window.closeModal = function(e){
    if(!e || e.target === overlay){
      overlay.classList.remove('active');
    }
  }

  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape'){
      overlay.classList.remove('active');
    }
  });
})();
  </script>
  

</body></html>"""

    for chave, valor in valores.items():
        template = template.replace(chave, valor)

    return template


def normalizar_meta_tag_site(valor):
    valor = valor_texto(valor, "").strip()
    if not valor:
        return ""
    if "<meta" in valor.lower():
        return valor
    codigo = escape(valor, quote=True)
    return f'<meta name="facebook-domain-verification" content="{codigo}" />'


def atividade_site_sem_codigo(empresa):
    """Retorna a descrição da atividade, nunca somente a numeração do CNAE."""

    def somente_descricao(valor):
        texto_valor = valor_texto(valor, "")
        if not texto_valor:
            return ""

        # Ex.: "43.30-4-99 - Outras obras de acabamento da construção".
        if " - " in texto_valor:
            prefixo, descricao = texto_valor.split(" - ", 1)
            if re.fullmatch(r"[0-9.\-/\s]+", prefixo.strip()) and descricao.strip():
                texto_valor = descricao.strip()

        # Nunca devolve um código puro como texto institucional.
        if re.fullmatch(r"[0-9.\-/\s]+", texto_valor):
            return ""

        return texto_valor.strip()

    descricao_salva = somente_descricao(empresa.get("cnae_principal_descricao", ""))
    if descricao_salva:
        return descricao_salva

    cnae_bruto = valor_texto(empresa.get("cnae_principal", ""))
    descricao_embutida = somente_descricao(cnae_bruto)
    if descricao_embutida:
        return descricao_embutida

    codigo = re.sub(r"\D", "", cnae_bruto)[:7]
    if codigo:
        descricao_mapeada = somente_descricao(carregar_cnaes().get(codigo.zfill(7), ""))
        if descricao_mapeada:
            return descricao_mapeada

    categoria = somente_descricao(empresa.get("categoria_cnae", ""))
    if categoria and categoria.lower() not in {"outros", "indefinido"}:
        return categoria

    return "Atividade empresarial cadastrada"


def situacao_cadastral_site(empresa):
    valor = valor_texto(empresa.get("situacao_cadastral", ""))
    mapa = {
        "01": "NULA", "02": "ATIVA", "03": "SUSPENSA", "04": "INAPTA",
        "08": "BAIXADA", "1": "NULA", "2": "ATIVA", "3": "SUSPENSA",
        "4": "INAPTA", "8": "BAIXADA"
    }
    return mapa.get(valor.zfill(2), valor or "Não informada")


def paleta_template_dinamico(cnpj):
    paletas = [
        ("#174ea6", "#22d3ee", "#eef5ff"),
        ("#155e75", "#2f8bff", "#ecfeff"),
        ("#1d4ed8", "#06b6d4", "#eff6ff"),
        ("#0f4c81", "#3b82f6", "#edf6ff"),
        ("#164e63", "#2563eb", "#eef8fb"),
        ("#1e3a8a", "#0891b2", "#f0f7ff"),
    ]
    digest = hashlib.sha256(limpar_cnpj(cnpj).encode("utf-8")).hexdigest()
    return paletas[int(digest[:8], 16) % len(paletas)]


def gerar_html_site_dinamico(empresa, meta_tag="", observacoes=""):
    """Template inspirado no modelo de referência, preservando seu corpo.

    A lógica Meta/WABA Clean atua apenas na consistência: dados oficiais,
    atividade real, contatos informados e texto factual. A estrutura visual
    permanece: cabeçalho, apresentação, serviços, compromisso, cadastro e contato.
    """
    segmento = identificar_segmento_site(empresa)
    conteudo = obter_conteudo_segmento(segmento)
    cnpj_limpo = limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", "")))
    cnpj_formatado = formatar_cnpj(cnpj_limpo)
    nome_publico = valor_texto(empresa.get("nome_site", "")) or nome_exibicao_empresa(empresa)
    razao_social = valor_publico(empresa.get("razao_social", ""))
    atividade = atividade_site_sem_codigo(empresa)
    cnae_completo = valor_publico(empresa.get("cnae_principal", ""))
    natureza = valor_publico(empresa.get("natureza_juridica", ""))
    situacao = situacao_cadastral_site(empresa)
    endereco = montar_endereco_empresa(empresa)
    municipio = valor_texto(empresa.get("municipio_nome", "")) or valor_texto(empresa.get("municipio", ""))
    uf = valor_texto(empresa.get("uf", ""))
    localidade = ", ".join([item for item in [municipio, uf] if item]) or "Brasil"
    telefone = valor_texto(empresa.get("telefone_formatado", ""))
    email = valor_texto(empresa.get("email", ""))
    telefone_href = re.sub(r"\D", "", telefone)
    meta_tag = normalizar_meta_tag_site(meta_tag)
    primary, accent, bg_soft = paleta_template_dinamico(cnpj_limpo)

    servicos = list(conteudo.get("servicos", []))[:3]
    while len(servicos) < 3:
        servicos.append("Atendimento relacionado à atividade empresarial cadastrada")

    compromisso = (
        f"A {nome_publico} mantém uma atuação alinhada à sua atividade cadastrada, "
        "com informações claras, canais de contato identificados e atenção à organização dos serviços oferecidos."
    )
    descricao = (
        f"{nome_publico} - {atividade} em {localidade}. "
        "Informações institucionais, dados cadastrais e canais de contato."
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="viewport" content="width=device-width,initial-scale=1.0" />
  <title>{escape(nome_publico)}</title>
  <meta name="description" content="{escape(descricao, quote=True)}" />
  {meta_tag}
  <meta property="og:title" content="{escape(nome_publico, quote=True)} — {escape(localidade, quote=True)}" />
  <meta property="og:type" content="website" />
  <style>
    :root {{
      --primary: {primary};
      --accent: {accent};
      --bg-soft: {bg_soft};
      --text-main: #232323;
      --text-light: #fff;
      --shadow: 0 2px 8px rgba(40,40,40,0.06);
      --radius: 7px;
    }}
    html {{ box-sizing:border-box;font-size:16px;background:var(--bg-soft);color:var(--text-main); }}
    *,*::before,*::after{{box-sizing:inherit;}}
    body {{font-family:'Segoe UI','Arial',sans-serif;margin:0;padding:0;min-height:100vh;display:flex;flex-direction:column;background:var(--bg-soft);}}
    header {{background:var(--primary);color:var(--text-light);padding:2.2rem 0 1.2rem;text-align:center;box-shadow:var(--shadow);}}
    .header-logo {{display:flex;flex-direction:column;align-items:center;}}
    .header-logo svg {{width:54px;height:54px;margin-bottom:.45rem;}}
    .header-tit {{font-weight:700;font-size:2.1rem;letter-spacing:1.5px;line-height:1.1;margin-bottom:.25rem;}}
    main {{flex:1;max-width:720px;margin:0 auto;padding:2.5rem 1.3rem 1.8rem;}}
    section {{background:#fff;border-radius:var(--radius);box-shadow:var(--shadow);margin-bottom:2rem;padding:1.5rem 1.2rem;}}
    section h2 {{font-size:1.35rem;font-weight:700;color:var(--primary);margin-top:0;margin-bottom:1.1rem;letter-spacing:.4px;}}
    .cta {{background:var(--accent);color:var(--text-light);border:none;font-weight:600;font-size:1rem;border-radius:var(--radius);padding:.9rem 2.2rem;margin-top:1.4rem;cursor:pointer;box-shadow:0 1px 4px rgba(40,40,40,.07);transition:background .15s;display:inline-block;text-decoration:none;}}
    .cta:hover {{background:var(--primary);}}
    .dados-cadastrais {{font-size:1rem;line-height:1.6;background:var(--bg-soft);border:1px solid #ececec;color:#393939;margin-bottom:2.2rem;}}
    .dados-list {{list-style:none;margin:0;padding:0;}}
    .dados-list li {{margin-bottom:.4rem;}}
    .contato {{display:flex;flex-direction:column;gap:.6rem;}}
    .contato a {{color:var(--accent);font-weight:600;text-decoration:none;word-break:break-all;}}
    .contato a:hover {{text-decoration:underline;}}
    footer {{background:#232323;color:#fff;text-align:center;font-size:.97rem;padding:1.3rem 0 .5rem;margin-top:3rem;}}
    .footer-links a {{color:var(--accent);text-decoration:none;margin:0 .7rem;}}
    .footer-links a:hover {{text-decoration:underline;}}
    @media(max-width:600px){{main{{padding:1.2rem .3rem 2.5rem}}section{{padding:1.1rem .7rem}}.header-tit{{font-size:1.3rem}}}}
  </style>
</head>
<body>
  <header>
    <div class="header-logo">
      <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <rect x="6" y="14" width="36" height="20" rx="4" fill="var(--accent)"></rect>
        <ellipse cx="24" cy="24" rx="14" ry="8" fill="var(--primary)" opacity="0.8"></ellipse>
        <rect x="15" y="20" width="18" height="8" rx="2" fill="#fff" opacity="0.9"></rect>
      </svg>
      <span class="header-tit">{escape(nome_publico)}</span>
    </div>
    <div style="font-size:1.1rem;font-weight:500;opacity:.83">{escape(atividade)}</div>
  </header>
  <main>
    <section>
      <h2>Bem-vindo à {escape(nome_publico)}</h2>
      <p>Com atuação em {escape(localidade)}, a empresa desenvolve atividades relacionadas a {escape(atividade.lower())}, apresentando seus dados oficiais e canais de contato de forma clara e acessível.</p>
    </section>
    <section>
      <h2>Nossos Serviços</h2>
      <ul style="padding-left:1.1em">
        <li>{escape(servicos[0])}</li>
        <li>{escape(servicos[1])}</li>
        <li>{escape(servicos[2])}</li>
      </ul>
    </section>
    <section>
      <h2>Compromisso com Qualidade</h2>
      <p>{escape(compromisso)}</p>
    </section>
    <section class="dados-cadastrais">
      <h2>Dados cadastrais oficiais</h2>
      <ul class="dados-list">
        <li>Razão social: <strong>{escape(razao_social)}</strong></li>
        <li>CNPJ: <strong>{escape(cnpj_formatado)}</strong></li>
        <li>Natureza jurídica: <strong>{escape(natureza)}</strong></li>
        <li>Situação cadastral: <strong>{escape(situacao)}</strong></li>
        <li>Atividade principal: <strong>{escape(cnae_completo)}</strong></li>
        <li>Endereço: <strong>{escape(endereco)}</strong></li>
      </ul>
    </section>
    <section>
      <h2>Contato</h2>
      <div class="contato">
        <div>E-mail: <a href="mailto:{escape(email, quote=True)}">{escape(email or 'Não informado')}</a></div>
        <div>Telefone: <a href="tel:{escape(telefone_href, quote=True)}">{escape(telefone or 'Não informado')}</a></div>
      </div>
      {f'<a class="cta" href="mailto:{escape(email, quote=True)}">Solicitar contato</a>' if email else ''}
    </section>
  </main>
  <footer>
    <div>{escape(nome_publico)} · {escape(cnpj_formatado)} · {agora_brasilia().year}</div>
    <div class="footer-links">
      <a href="/politica-de-privacidade.html">Política de Privacidade</a> |
      <a href="/termos-de-uso.html">Termos de Uso</a>
    </div>
  </footer>
</body>
</html>"""



def gerar_html_site_ia_2_0(empresa, meta_tag="", observacoes=""):
    """Template institucional enxuto e factual.

    O objetivo é apresentar, no mesmo documento, a identidade pública usada no
    site, a razão social, a atividade descrita, os dados cadastrais e os canais
    de contato. O modelo não inventa certificações, tempo de mercado, avaliações
    ou promessas que não existam na base.
    """
    segmento = identificar_segmento_site(empresa)
    conteudo = obter_conteudo_segmento(segmento)

    cnpj_limpo = limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", "")))
    cnpj_formatado = formatar_cnpj(cnpj_limpo)
    nome_publico = valor_texto(empresa.get("nome_site", "")) or nome_exibicao_empresa(empresa)
    razao_social = valor_publico(empresa.get("razao_social", ""))
    nome_fantasia = valor_texto(empresa.get("nome_fantasia", ""))
    atividade = atividade_site_sem_codigo(empresa)
    cnae_bruto = valor_texto(empresa.get("cnae_principal", ""))
    natureza = valor_publico(empresa.get("natureza_juridica", ""))
    situacao = situacao_cadastral_site(empresa)
    data_abertura = valor_publico(empresa.get("data_inicio_formatada", ""))
    endereco = montar_endereco_empresa(empresa)
    municipio = valor_texto(empresa.get("municipio_nome", "")) or valor_texto(empresa.get("municipio", ""))
    uf = valor_texto(empresa.get("uf", ""))
    localidade = ", ".join([item for item in [municipio, uf] if item]) or "Brasil"
    telefone = valor_texto(empresa.get("telefone_formatado", ""))
    email = valor_texto(empresa.get("email", ""))
    meta_tag = normalizar_meta_tag_site(meta_tag)

    telefone_digitos = re.sub(r"\D", "", telefone)
    telefone_href = f"+{telefone_digitos}" if telefone_digitos else ""
    whatsapp_digitos = telefone_digitos
    if whatsapp_digitos and len(whatsapp_digitos) in {10, 11}:
        whatsapp_digitos = f"55{whatsapp_digitos}"
    whatsapp_url = f"https://wa.me/{whatsapp_digitos}" if whatsapp_digitos else ""

    if cnae_bruto and atividade.lower() not in cnae_bruto.lower():
        cnae_oficial = f"{cnae_bruto} - {atividade}"
    else:
        cnae_oficial = cnae_bruto or atividade

    servicos = list(conteudo.get("servicos", []))[:3]
    while len(servicos) < 3:
        servicos.append(f"Atendimento relacionado a {atividade.lower()}")

    primary, accent, bg_soft = paleta_template_dinamico(cnpj_limpo)
    descricao = (
        f"{nome_publico}, identificação institucional de {razao_social}. "
        f"Atuação em {atividade.lower()} em {localidade}, com dados cadastrais e canais de contato."
    )

    identificacao_publica = ""
    if nome_publico and normalizar_texto_razao(nome_publico) != normalizar_texto_razao(razao_social):
        identificacao_publica = f"""
        <div class="identity-note">
          <strong>Identificação utilizada neste site</strong>
          <span>{escape(nome_publico)}</span>
          <small>Razão social vinculada: {escape(razao_social)}</small>
        </div>"""

    contato_itens = []
    if telefone:
        contato_itens.append(
            f'<div class="contact-row"><span>Telefone de atendimento</span><a href="tel:{escape(telefone_href, quote=True)}">{escape(telefone)}</a></div>'
        )
    if email:
        contato_itens.append(
            f'<div class="contact-row"><span>E-mail de contato</span><a href="mailto:{escape(email, quote=True)}">{escape(email)}</a></div>'
        )
    contato_html = "".join(contato_itens) or '<div class="contact-row"><span>Canais de contato</span><strong>Não informados</strong></div>'

    botoes = []
    if whatsapp_url:
        botoes.append(f'<a class="button primary" href="{escape(whatsapp_url, quote=True)}">Falar pelo WhatsApp</a>')
    if email:
        botoes.append(f'<a class="button secondary" href="mailto:{escape(email, quote=True)}">Enviar e-mail</a>')
    botoes_html = "".join(botoes)

    schema = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": nome_publico,
        "legalName": razao_social,
        "taxID": cnpj_formatado,
        "description": atividade,
        "address": endereco,
    }
    if nome_fantasia:
        schema["alternateName"] = nome_fantasia
    if telefone:
        schema["telephone"] = telefone
    if email:
        schema["email"] = email
    schema_json = json.dumps(schema, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(nome_publico)} | {escape(atividade)}</title>
  <meta name="description" content="{escape(descricao, quote=True)}">
  {meta_tag}
  <meta property="og:title" content="{escape(nome_publico, quote=True)}">
  <meta property="og:description" content="{escape(descricao, quote=True)}">
  <meta property="og:type" content="website">
  <script type="application/ld+json">{schema_json}</script>
  <style>
    :root{{--primary:{primary};--accent:{accent};--soft:{bg_soft};--text:#172033;--muted:#64748b;--line:#dce5f0;--surface:#fff}}
    *{{box-sizing:border-box}}
    html{{scroll-behavior:smooth}}
    body{{margin:0;font-family:Segoe UI,Arial,sans-serif;background:var(--soft);color:var(--text);line-height:1.6}}
    a{{color:inherit}}
    .wrap{{width:min(940px,calc(100% - 32px));margin:auto}}
    header{{background:var(--primary);color:#fff;padding:42px 0 34px}}
    .brand-row{{display:flex;align-items:center;gap:16px}}
    .mark{{width:50px;height:50px;border-radius:14px;background:var(--accent);display:grid;place-items:center;font-weight:800;font-size:22px}}
    h1{{font-size:clamp(1.8rem,5vw,2.65rem);line-height:1.1;margin:0 0 8px}}
    header p{{margin:0;opacity:.88}}
    main{{padding:28px 0 44px}}
    .card{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:18px;box-shadow:0 8px 24px rgba(15,23,42,.05)}}
    h2{{font-size:1.22rem;margin:0 0 14px;color:var(--primary)}}
    p{{margin:0 0 12px}}
    .intro{{font-size:1.05rem}}
    .identity-note{{display:grid;gap:2px;margin-top:18px;padding:14px 16px;border-left:4px solid var(--accent);background:var(--soft);border-radius:8px}}
    .identity-note small{{color:var(--muted)}}
    .services{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:0;margin:0;list-style:none}}
    .services li{{padding:14px;background:var(--soft);border:1px solid var(--line);border-radius:10px}}
    .details{{display:grid;grid-template-columns:180px 1fr;gap:0;margin:0}}
    .details dt,.details dd{{margin:0;padding:10px 0;border-bottom:1px solid var(--line)}}
    .details dt{{font-weight:700;color:var(--muted)}}
    .details dd{{word-break:break-word}}
    .contact-box{{display:grid;gap:10px}}
    .contact-row{{display:flex;justify-content:space-between;gap:20px;padding:12px 14px;background:var(--soft);border-radius:10px}}
    .contact-row span{{color:var(--muted);font-weight:700}}
    .contact-row a{{font-weight:700;color:var(--primary);text-decoration:none;word-break:break-all}}
    .actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}}
    .button{{display:inline-flex;text-decoration:none;border-radius:9px;padding:11px 16px;font-weight:700}}
    .button.primary{{background:var(--accent);color:#fff}}
    .button.secondary{{border:1px solid var(--primary);color:var(--primary)}}
    footer{{background:#111827;color:#fff;padding:24px 0;text-align:center;font-size:.92rem}}
    footer p{{margin:0 0 8px}}
    footer a{{color:#bae6fd;text-decoration:none;margin:0 8px}}
    @media(max-width:700px){{.services{{grid-template-columns:1fr}}.details{{grid-template-columns:1fr}}.details dt{{padding-bottom:0;border-bottom:0}}.contact-row{{flex-direction:column;gap:2px}}}}
  </style>
</head>
<body>
  <header>
    <div class="wrap brand-row">
      <div class="mark" aria-hidden="true">{escape((nome_publico or 'E')[:1].upper())}</div>
      <div>
        <h1>{escape(nome_publico)}</h1>
        <p>{escape(atividade)} · {escape(localidade)}</p>
      </div>
    </div>
  </header>

  <main class="wrap">
    <section class="card intro">
      <h2>Sobre a empresa</h2>
      <p>A <strong>{escape(razao_social)}</strong> exerce atividades relacionadas a <strong>{escape(atividade.lower())}</strong> em {escape(localidade)}. Este site reúne sua identificação empresarial, dados cadastrais e canais informados de atendimento.</p>
      {identificacao_publica}
    </section>

    <section class="card">
      <h2>Atuação</h2>
      <ul class="services">
        <li>{escape(servicos[0])}</li>
        <li>{escape(servicos[1])}</li>
        <li>{escape(servicos[2])}</li>
      </ul>
    </section>

    <section class="card">
      <h2>Dados oficiais da empresa</h2>
      <dl class="details">
        <dt>Nome exibido</dt><dd>{escape(nome_publico)}</dd>
        <dt>Razão social</dt><dd>{escape(razao_social)}</dd>
        <dt>CNPJ</dt><dd>{escape(cnpj_formatado)}</dd>
        <dt>Situação cadastral</dt><dd>{escape(situacao)}</dd>
        <dt>Natureza jurídica</dt><dd>{escape(natureza)}</dd>
        <dt>Atividade principal</dt><dd>{escape(cnae_oficial)}</dd>
        <dt>Data de abertura</dt><dd>{escape(data_abertura)}</dd>
        <dt>Endereço</dt><dd>{escape(endereco)}</dd>
        <dt>Telefone informado</dt><dd>{escape(telefone or 'Não informado')}</dd>
        <dt>E-mail informado</dt><dd>{escape(email or 'Não informado')}</dd>
      </dl>
    </section>

    <section class="card">
      <h2>Canais de atendimento</h2>
      <p>Os canais abaixo são apresentados neste site como formas de contato com a empresa.</p>
      <div class="contact-box">{contato_html}</div>
      {f'<div class="actions">{botoes_html}</div>' if botoes_html else ''}
    </section>
  </main>

  <footer>
    <div class="wrap">
      <p>{escape(razao_social)} · {escape(cnpj_formatado)} · {agora_brasilia().year}</p>
      <a href="/politica-de-privacidade.html">Política de Privacidade</a>
      <a href="/termos-de-uso.html">Termos de Uso</a>
    </div>
  </footer>
</body>
</html>"""

def gerar_paginas_legais_site(empresa):
    nome = valor_texto(empresa.get("nome_site", "")) or nome_exibicao_empresa(empresa)
    razao = valor_publico(empresa.get("razao_social", ""))
    cnpj = formatar_cnpj(limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", ""))))
    email = valor_texto(empresa.get("email", ""))
    telefone = valor_texto(empresa.get("telefone_formatado", ""))
    endereco = montar_endereco_empresa(empresa)
    primary, accent, bg_soft = paleta_template_dinamico(cnpj)

    def pagina(titulo, conteudo):
        return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(titulo)} | {escape(nome)}</title><style>:root{{--primary:{primary};--accent:{accent};--soft:{bg_soft}}}*{{box-sizing:border-box}}body{{margin:0;font-family:Segoe UI,Arial,sans-serif;background:var(--soft);color:#232323}}header{{background:var(--primary);color:#fff;padding:28px 20px;text-align:center}}main{{max-width:760px;margin:32px auto;padding:0 16px}}article{{background:#fff;border-radius:8px;padding:28px;box-shadow:0 2px 8px rgba(40,40,40,.06);line-height:1.7}}h1{{margin:0;font-size:1.8rem}}h2{{color:var(--primary);font-size:1.2rem;margin-top:1.7rem}}a{{color:var(--accent)}}footer{{text-align:center;padding:24px;color:#555}}</style></head><body><header><h1>{escape(titulo)}</h1><div>{escape(nome)}</div></header><main><article>{conteudo}<p><a href="/">Voltar ao site</a></p></article></main><footer>{escape(razao)} · {escape(cnpj)}</footer></body></html>"""

    politica = pagina("Política de Privacidade", f"""
<p>Esta página apresenta como a {escape(nome)} trata informações enviadas voluntariamente por seus canais de contato.</p>
<h2>Informações de contato</h2><p>Quando uma pessoa entra em contato por e-mail ou telefone, os dados informados são utilizados somente para responder à solicitação e manter o atendimento relacionado à atividade da empresa.</p>
<h2>Compartilhamento</h2><p>Os dados de contato não são comercializados. Poderão ser tratados por serviços técnicos necessários ao funcionamento do site e da comunicação.</p>
<h2>Contato da empresa</h2><p>E-mail: {escape(email or 'Não informado')}<br>Telefone: {escape(telefone or 'Não informado')}<br>Endereço: {escape(endereco)}</p>""")

    termos = pagina("Termos de Uso", f"""
<p>Ao acessar este site, o visitante reconhece que seu conteúdo possui finalidade institucional e informativa.</p>
<h2>Conteúdo</h2><p>As informações empresariais exibidas correspondem aos dados cadastrados e aos canais de contato informados pela empresa.</p>
<h2>Uso adequado</h2><p>O conteúdo não deve ser utilizado de forma ilícita, fraudulenta ou que prejudique a empresa, terceiros ou a disponibilidade do site.</p>
<h2>Contato</h2><p>Dúvidas podem ser encaminhadas para {escape(email or telefone or 'os canais indicados no site')}.</p>""")
    return politica, termos



# ============================================================
# TEMPLATE IA 3.0
# Modelo cadastral responsivo baseado no HTML de referência.
# Campos opcionais só são exibidos quando possuem valor real.
# ============================================================
def gerar_html_site_ia_3_0(empresa, meta_tag="", observacoes=""):
    cnpj_limpo = limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", "")))
    cnpj_formatado = formatar_cnpj(cnpj_limpo)
    razao_social = valor_texto(empresa.get("razao_social", ""))
    nome_publico = valor_texto(empresa.get("nome_site", "")) or nome_exibicao_empresa(empresa)
    data_abertura = valor_texto(empresa.get("data_inicio_formatada", ""))
    endereco = valor_texto(empresa.get("endereco_site", "")) or montar_endereco_empresa(empresa)
    email = valor_texto(empresa.get("email", ""))
    telefone = valor_texto(empresa.get("telefone_formatado", ""))
    meta_tag = normalizar_meta_tag_site(meta_tag)

    situacao_raw = valor_texto(empresa.get("situacao_cadastral", ""))
    situacao = situacao_cadastral_site(empresa) if situacao_raw else ""

    natureza_codigo = valor_texto(empresa.get("natureza_juridica", ""))
    natureza_map = {
        "2135": "Empresário (individual)",
        "2062": "Sociedade Empresária Limitada",
    }
    natureza = natureza_map.get(natureza_codigo, natureza_codigo)

    porte_raw = ""
    for coluna in ["porte_descricao", "descricao_porte", "porte_empresa", "porte"]:
        porte_raw = valor_texto(empresa.get(coluna, ""))
        if porte_raw:
            break

    porte_map = {
        "01": "MICRO EMPRESA",
        "1": "MICRO EMPRESA",
        "03": "EMPRESA DE PEQUENO PORTE",
        "3": "EMPRESA DE PEQUENO PORTE",
        "05": "DEMAIS",
        "5": "DEMAIS",
    }
    porte = porte_map.get(porte_raw, porte_raw)

    atividade = atividade_site_sem_codigo(empresa)
    cnae_codigo = valor_texto(empresa.get("cnae_principal_codigo", ""))
    if not cnae_codigo:
        cnae_codigo = re.sub(r"\D", "", valor_texto(empresa.get("cnae_principal", "")))[:7]
    cnae_formatado = formatar_codigo_cnae(cnae_codigo) if cnae_codigo else ""
    atividade_principal = atividade
    if cnae_formatado:
        atividade_principal = f"{atividade} (CNAE {cnae_formatado})"

    atividades_secundarias = ""
    for coluna in [
        "atividades_secundarias",
        "cnae_secundaria_descricao",
        "cnaes_secundarios",
        "cnae_secundaria",
    ]:
        atividades_secundarias = valor_texto(empresa.get(coluna, ""))
        if atividades_secundarias:
            break

    linhas = []

    def adicionar_linha(rotulo, valor, html_valor=None):
        valor = valor_texto(valor, "")
        valor_normalizado = normalizar_texto_razao(valor)
        if (
            not valor
            or valor.replace("*", "").strip() == ""
            or valor_normalizado in {
                "NAO INFORMADO", "NAO INFORMADA", "NAO INFORMADOS", "NAO INFORMADAS",
                "SEM INFORMACAO", "SEM INFORMACOES"
            }
        ):
            return
        conteudo = html_valor if html_valor is not None else escape(valor)
        linhas.append(f'<p><strong>{escape(rotulo)}:</strong> {conteudo}</p>')

    adicionar_linha("Razão Social", razao_social)
    adicionar_linha("CNPJ", cnpj_formatado)
    adicionar_linha("Data de Abertura", data_abertura)
    adicionar_linha("Porte", porte)
    adicionar_linha("Situação Cadastral", situacao)
    adicionar_linha("Atividade Principal", atividade_principal)
    adicionar_linha("Atividades Secundárias", atividades_secundarias)
    adicionar_linha("Natureza Jurídica", natureza)
    adicionar_linha("Endereço", endereco)

    if email:
        adicionar_linha(
            "Email",
            email,
            f'<a href="mailto:{escape(email, quote=True)}">{escape(email)}</a>'
        )

    if telefone:
        telefone_digitos = re.sub(r"\D", "", telefone)
        telefone_href = f"+{telefone_digitos}" if telefone_digitos else ""
        adicionar_linha(
            "Telefone",
            telefone,
            f'<a href="tel:{escape(telefone_href, quote=True)}">{escape(telefone)}</a>'
        )

    descricao = f"{nome_publico} — CNPJ {cnpj_formatado}"
    dados_html = "\n    ".join(linhas)

    return f'''<!DOCTYPE html>
<html lang="pt-BR"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(nome_publico)}</title>
<meta name="description" content="{escape(descricao, quote=True)}">
{meta_tag}
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{background:#f5f6f8;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;line-height:1.6;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1100px;margin:0 auto;padding:clamp(16px,4vw,40px)}}
.card{{background:#fff;border-radius:18px;box-shadow:0 1px 3px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.04);padding:clamp(20px,4vw,48px)}}
h1{{color:#1d4ed8;font-weight:800;font-size:clamp(1.35rem,3.2vw,2.1rem);letter-spacing:.2px;word-wrap:break-word;overflow-wrap:break-word;line-height:1.25;margin-bottom:clamp(18px,3vw,28px)}}
h3{{color:#1d4ed8;font-weight:700;font-size:clamp(1.05rem,2vw,1.25rem);padding-bottom:.6rem;border-bottom:1px solid #e5e7eb;margin-bottom:clamp(14px,2vw,22px)}}
p{{font-size:clamp(.95rem,1.6vw,1rem);color:#374151;margin-bottom:clamp(12px,1.6vw,18px);word-wrap:break-word;overflow-wrap:break-word}}
strong{{color:#111827;font-weight:700}}
a{{color:#1d4ed8;text-decoration:none;word-break:break-all}}
a:hover{{text-decoration:underline}}
@media (max-width:480px){{
  .wrap{{padding:12px}}
  .card{{padding:18px;border-radius:14px}}
}}
</style></head><body>
<div class="wrap">
  <article class="card">
    <h1>{escape(nome_publico)}</h1>
    <h3>Dados da Empresa</h3>
    {dados_html}
  </article>
</div>
</body></html>'''

def gerar_bundle_site_empresa(empresa, meta_tag, modelo_site, observacoes=""):
    html = gerar_html_site_empresa(empresa, meta_tag, modelo_site, observacoes)
    politica, termos = gerar_paginas_legais_site(empresa)
    return {"html": html, "politica": politica, "termos": termos}


def gerar_html_site_empresa(empresa, meta_tag, modelo_site, observacoes=""):
    modelo_site = modelo_site if modelo_site in MODELOS_SITE_DICT else "ia_2_0"
    meta_tag = normalizar_meta_tag_site(meta_tag)

    if modelo_site == "dinamico":
        return gerar_html_site_dinamico(empresa, meta_tag, observacoes)

    if modelo_site == "ia_2_0":
        return gerar_html_site_ia_2_0(empresa, meta_tag, observacoes)

    if modelo_site == "ia_3_0":
        return gerar_html_site_ia_3_0(empresa, meta_tag, observacoes)

    if modelo_site == "meta_waba_clean":
        return gerar_html_site_meta_waba_clean(empresa, meta_tag, observacoes)

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


def normalizar_nome_worker_cloudflare(texto):
    texto = valor_texto(texto, "").lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-z0-9-]+", "-", texto)
    texto = re.sub(r"-+", "-", texto).strip("-")

    if len(texto) > 63:
        texto = texto[:63].strip("-")

    return texto


def sugerir_nome_worker_site(empresa_ou_site):
    base = (
        valor_texto(empresa_ou_site.get("nome_exibicao", ""))
        or valor_texto(empresa_ou_site.get("nome_site", ""))
        or valor_texto(empresa_ou_site.get("nome_fantasia", ""))
        or valor_texto(empresa_ou_site.get("nome_empresarial", ""))
        or valor_texto(empresa_ou_site.get("razao_social", ""))
        or "site"
    )

    slug = normalizar_nome_worker_cloudflare(base)

    if not slug or slug == "empresa":
        slug = "site"

    return slug


def gerar_nome_worker_site(site, nome_personalizado=""):
    personalizado = (
        valor_texto(nome_personalizado, "")
        or valor_texto(site.get("cloudflare_slug_personalizado", ""))
    )

    if personalizado:
        nome = normalizar_nome_worker_cloudflare(personalizado)

        if not nome:
            raise RuntimeError("Nome do domínio Cloudflare inválido. Use letras, números e hífens.")

        if len(nome) < 3:
            raise RuntimeError("Nome do domínio Cloudflare precisa ter pelo menos 3 caracteres.")

        return nome

    base = (
        valor_texto(site.get("nome_exibicao", ""))
        or valor_texto(site.get("nome_fantasia", ""))
        or valor_texto(site.get("nome_empresarial", ""))
        or "site"
    )
    slug = normalizar_slug_site(base).replace("_", "-")
    cnpj = limpar_cnpj(site.get("cnpj", ""))
    sufixo_cnpj = cnpj[-4:] if cnpj else agora_brasilia().strftime("%H%M")
    sufixo_id = valor_texto(site.get("id", ""))

    partes = [slug, sufixo_cnpj]

    if sufixo_id:
        partes.append(sufixo_id)

    nome = "-".join(partes)
    nome = normalizar_nome_worker_cloudflare(nome)

    if not nome:
        nome = f"site-{sufixo_cnpj}"

    return nome or f"site-{sufixo_cnpj}"


def carregar_arquivos_verificacao_site(site):
    bruto = site.get("arquivos_verificacao", {}) if isinstance(site, dict) else {}
    if isinstance(bruto, dict):
        return bruto
    try:
        dados = json.loads(bruto or "{}")
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def gerar_nome_worker_interno(site, slug_publico, config):
    """Gera um nome técnico único sem alterar o subdomínio escolhido."""
    existente = normalizar_nome_worker_cloudflare(site.get("cloudflare_worker_name", ""))
    dominio_salvo = normalizar_dominio_cloudflare(site.get("cloudflare_dominio_base", ""))
    dominio_config = normalizar_dominio_cloudflare(config.get("custom_domain", ""))

    if existente and (not dominio_salvo or dominio_salvo == dominio_config):
        return existente

    cnpj = limpar_cnpj(site.get("cnpj", ""))
    assinatura = hashlib.sha256(
        f"{dominio_config}|{slug_publico}|{cnpj}".encode("utf-8")
    ).hexdigest()[:8]

    sufixo = f"-{assinatura}"
    base_maxima = max(3, 63 - len("ltdafinder-") - len(sufixo))
    base = normalizar_nome_worker_cloudflare(slug_publico)[:base_maxima].strip("-") or "site"
    return normalizar_nome_worker_cloudflare(f"ltdafinder-{base}{sufixo}")


def gerar_worker_js_site(html, politica_html="", termos_html="", arquivos_verificacao=None):
    paginas = {
        "/": html,
        "/index.html": html,
        "/politica-de-privacidade.html": politica_html or html,
        "/termos-de-uso.html": termos_html or html,
    }
    for caminho, conteudo in (arquivos_verificacao or {}).items():
        caminho = "/" + valor_texto(caminho, "").lstrip("/")
        if caminho != "/":
            paginas[caminho] = valor_texto(conteudo, "")

    pages_literal = json.dumps(paginas, ensure_ascii=False)
    robots_literal = json.dumps("User-agent: *\nAllow: /\n")

    return f"""const PAGES = {pages_literal};
const ROBOTS = {robots_literal};

async function handleRequest(request) {{
  const url = new URL(request.url);
  const path = url.pathname;

  if (path === "/robots.txt") {{
    return new Response(ROBOTS, {{
      headers: {{
        "content-type": "text/plain; charset=UTF-8",
        "cache-control": "public, max-age=3600"
      }}
    }});
  }}

  if (Object.prototype.hasOwnProperty.call(PAGES, path)) {{
    return new Response(PAGES[path], {{
      headers: {{
        "content-type": "text/html; charset=UTF-8",
        "cache-control": "public, max-age=300"
      }}
    }});
  }}

  return new Response("Página não encontrada", {{
    status: 404,
    headers: {{"content-type": "text/plain; charset=UTF-8"}}
  }});
}}

addEventListener("fetch", event => {{
  event.respondWith(handleRequest(event.request));
}});
"""


def gerar_worker_js_site_registro(site):
    return gerar_worker_js_site(
        site.get("html_gerado", ""),
        site.get("politica_html", ""),
        site.get("termos_html", ""),
        carregar_arquivos_verificacao_site(site),
    )


def normalizar_dominio_cloudflare(dominio):
    dominio = valor_texto(dominio, "").lower()
    dominio = dominio.replace("https://", "").replace("http://", "")
    dominio = dominio.split("/")[0].strip().strip(".")
    dominio = re.sub(r"[^a-z0-9.-]+", "", dominio)
    dominio = re.sub(r"\.{2,}", ".", dominio).strip(".")
    return dominio


def _config_cloudflare_legado():
    account_id = valor_texto(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    api_token = valor_texto(os.environ.get("CLOUDFLARE_API_TOKEN", ""))
    subdomain = valor_texto(os.environ.get("CLOUDFLARE_WORKERS_SUBDOMAIN", ""))
    subdomain = subdomain.replace("https://", "").replace("http://", "").strip().lower()
    subdomain = subdomain.replace(".workers.dev", "").strip("/")
    custom_domain = normalizar_dominio_cloudflare(os.environ.get("CLOUDFLARE_CUSTOM_DOMAIN", "espacoempresarial.com"))
    zone_id = valor_texto(os.environ.get("CLOUDFLARE_ZONE_ID", ""))
    zone_name = normalizar_dominio_cloudflare(os.environ.get("CLOUDFLARE_ZONE_NAME", custom_domain))
    return {
        "account_id": account_id,
        "api_token": api_token,
        "subdomain": subdomain,
        "custom_domain": custom_domain,
        "zone_id": zone_id,
        "zone_name": zone_name or custom_domain,
        "ativo": True,
    }


def _config_cloudflare_painelconectado():
    """Configuração isolada da nova conta Cloudflare.

    A configuração só entra na lista de domínios quando o token estiver
    cadastrado na Railway, evitando que o modo automático escolha um domínio
    ainda sem credencial.
    """
    api_token = valor_texto(os.environ.get(
        "CLOUDFLARE_PAINELCONECTADO_API_TOKEN", ""
    ))

    if not api_token:
        return None

    custom_domain = normalizar_dominio_cloudflare(
        os.environ.get(
            "CLOUDFLARE_PAINELCONECTADO_ZONE_NAME",
            "painelconectadobr.com"
        )
    ) or "painelconectadobr.com"

    return {
        "account_id": valor_texto(os.environ.get(
            "CLOUDFLARE_PAINELCONECTADO_ACCOUNT_ID",
            "bd343bda99621383fba856bd3476c6e6"
        )),
        "api_token": api_token,
        "subdomain": "",
        "custom_domain": custom_domain,
        "zone_id": valor_texto(os.environ.get(
            "CLOUDFLARE_PAINELCONECTADO_ZONE_ID",
            "dd8b7536528f5ca7479896d4d8771729"
        )),
        "zone_name": custom_domain,
        "ativo": valor_texto(os.environ.get(
            "CLOUDFLARE_PAINELCONECTADO_ATIVO", "1"
        )).lower() not in {"0", "false", "nao", "não", "off"},
        "publish_mode": valor_texto(os.environ.get(
            "CLOUDFLARE_PAINELCONECTADO_PUBLISH_MODE",
            "custom_strict"
        )).lower(),
        "rotulo": "Painel Conectado BR",
        "env_prefix": "CLOUDFLARE_PAINELCONECTADO",
    }



def _config_cloudflare_meupainelnegocios():
    # O token do Painel Conectado é reutilizado por padrão porque os domínios
    # estão na mesma conta. Um token específico ainda pode ser informado.
    api_token = valor_texto(
        os.environ.get("CLOUDFLARE_MEUPAINELNEGOCIOS_API_TOKEN", "")
        or os.environ.get("CLOUDFLARE_PAINELCONECTADO_API_TOKEN", "")
    )
    zone_id = valor_texto(os.environ.get("CLOUDFLARE_MEUPAINELNEGOCIOS_ZONE_ID", ""))

    # Sem Zone ID o domínio não aparece no seletor, evitando publicação na zona errada.
    if not api_token or not zone_id:
        return None

    custom_domain = normalizar_dominio_cloudflare(
        os.environ.get(
            "CLOUDFLARE_MEUPAINELNEGOCIOS_ZONE_NAME",
            "meupainelnegocios.com"
        )
    ) or "meupainelnegocios.com"

    return {
        "account_id": valor_texto(os.environ.get(
            "CLOUDFLARE_MEUPAINELNEGOCIOS_ACCOUNT_ID",
            "bd343bda99621383fba856bd3476c6e6"
        )),
        "api_token": api_token,
        "subdomain": "",
        "custom_domain": custom_domain,
        "zone_id": zone_id,
        "zone_name": custom_domain,
        "ativo": valor_texto(os.environ.get(
            "CLOUDFLARE_MEUPAINELNEGOCIOS_ATIVO", "1"
        )).lower() not in {"0", "false", "nao", "não", "off"},
        "publish_mode": valor_texto(os.environ.get(
            "CLOUDFLARE_MEUPAINELNEGOCIOS_PUBLISH_MODE",
            "custom_strict"
        )).lower(),
        "rotulo": "Meu Painel Negócios",
        "env_prefix": "CLOUDFLARE_MEUPAINELNEGOCIOS",
    }

def listar_configs_cloudflare():
    dominios_permitidos = {"painelconectadobr.com", "meupainelnegocios.com"}
    bruto = valor_texto(os.environ.get("CLOUDFLARE_SITES_CONFIG", ""))
    configs = []

    if bruto:
        try:
            carregado = json.loads(bruto)
            if isinstance(carregado, dict):
                carregado = carregado.get("domains", [])
            if isinstance(carregado, list):
                for item in carregado:
                    if not isinstance(item, dict):
                        continue
                    dominio = normalizar_dominio_cloudflare(
                        item.get("domain") or item.get("custom_domain") or item.get("zone_name")
                    )
                    if dominio not in dominios_permitidos:
                        continue
                    configs.append({
                        "account_id": valor_texto(item.get("account_id", "")),
                        "api_token": valor_texto(item.get("api_token", "")),
                        "subdomain": "",
                        "custom_domain": dominio,
                        "zone_id": valor_texto(item.get("zone_id", "")),
                        "zone_name": dominio,
                        "ativo": bool(item.get("active", item.get("ativo", True))),
                        "publish_mode": valor_texto(item.get("publish_mode", "custom_strict")),
                    })
        except Exception as erro:
            print("CLOUDFLARE_SITES_CONFIG inválido:", erro)

    for config_dedicada in [
        _config_cloudflare_painelconectado(),
        _config_cloudflare_meupainelnegocios(),
    ]:
        if not config_dedicada:
            continue
        dominio = config_dedicada["custom_domain"]
        configs = [
            config for config in configs
            if config.get("custom_domain") != dominio
        ]
        configs.append(config_dedicada)

    # Uma única opção por domínio. O domínio legado foi removido da lista.
    unicos = {}
    for config in configs:
        chave = dominio_publicacao_cloudflare(config)
        if chave in dominios_permitidos:
            unicos[chave] = config

    return list(unicos.values())

def obter_config_cloudflare(dominio=""):
    configs = listar_configs_cloudflare()
    dominio = normalizar_dominio_cloudflare(dominio)
    if dominio:
        for config in configs:
            if config.get("custom_domain") == dominio:
                return config
    ativos = [config for config in configs if config.get("ativo", True)]
    return (ativos or configs or [{}])[0]


def dominio_publicacao_cloudflare(config=None):
    config = config or obter_config_cloudflare()
    if config.get("custom_domain"):
        return config.get("custom_domain")
    subdomain = config.get("subdomain") or "portalempresarial"
    return f"{subdomain}.workers.dev"


def validar_config_cloudflare(dominio=""):
    config = obter_config_cloudflare(dominio)
    faltando = []
    if not config["account_id"]:
        faltando.append("CLOUDFLARE_ACCOUNT_ID")
    if not config["api_token"]:
        faltando.append("CLOUDFLARE_API_TOKEN")
    if config.get("custom_domain"):
        if not config.get("zone_id") and valor_texto(os.environ.get("CLOUDFLARE_PUBLISH_MODE", "route")).lower() not in ["custom", "custom_domain", "domain"]:
            faltando.append("CLOUDFLARE_ZONE_ID")
    elif not config.get("subdomain"):
        faltando.append("CLOUDFLARE_WORKERS_SUBDOMAIN")
    if faltando:
        raise RuntimeError("Configuração Cloudflare incompleta: " + ", ".join(faltando))
    return config

    if not config["account_id"]:
        faltando.append("CLOUDFLARE_ACCOUNT_ID")

    if not config["api_token"]:
        faltando.append("CLOUDFLARE_API_TOKEN")

    if config.get("custom_domain"):
        if not config.get("zone_id"):
            faltando.append("CLOUDFLARE_ZONE_ID")
    elif not config["subdomain"]:
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


def atualizar_publicacao_cloudflare_site(site_id, status, worker_name="", cloudflare_url="", erro="", slug_personalizado="", dominio_base="", hostname=""):
    criar_tabela_sites_gerados()

    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE sites_gerados
                SET
                    cloudflare_slug_personalizado = CASE
                        WHEN :slug_personalizado != '' THEN :slug_personalizado
                        ELSE cloudflare_slug_personalizado
                    END,
                    cloudflare_worker_name = :worker_name,
                    cloudflare_url = :cloudflare_url,
                    cloudflare_dominio_base = CASE
                        WHEN :dominio_base != '' THEN :dominio_base
                        ELSE cloudflare_dominio_base
                    END,
                    cloudflare_hostname = CASE
                        WHEN :hostname != '' THEN :hostname
                        ELSE cloudflare_hostname
                    END,
                    cloudflare_status = :status,
                    cloudflare_erro = :erro,
                    cloudflare_publicado_em = CASE
                        WHEN :status = 'Publicado' THEN CURRENT_TIMESTAMP
                        ELSE cloudflare_publicado_em
                    END
                WHERE id = :site_id
            """),
            {
                "slug_personalizado": slug_personalizado,
                "worker_name": worker_name,
                "cloudflare_url": cloudflare_url,
                "dominio_base": normalizar_dominio_cloudflare(dominio_base),
                "hostname": hostname or cloudflare_url.replace("https://", "").replace("http://", "").strip("/"),
                "status": status,
                "erro": erro,
                "site_id": site_id
            }
        )


def anexar_dominio_customizado_cloudflare(config, worker_name, hostname):
    url = f"https://api.cloudflare.com/client/v4/accounts/{config['account_id']}/workers/domains"
    resposta = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {config['api_token']}",
            "Content-Type": "application/json"
        },
        json={
            "hostname": hostname,
            "service": worker_name,
            "zone_id": config.get("zone_id", ""),
            "zone_name": config.get("zone_name", "") or config.get("custom_domain", "")
        },
        timeout=45
    )

    try:
        payload = resposta.json()
    except Exception:
        payload = {}

    if not resposta.ok or payload.get("success") is False:
        erro = resumir_erros_cloudflare(payload, resposta.text)
        raise RuntimeError(f"Worker enviado, mas falhou ao vincular o domínio {hostname}: {erro}")

    return payload




def cloudflare_headers_json(config):
    return {
        "Authorization": f"Bearer {config['api_token']}",
        "Content-Type": "application/json"
    }


def dns_record_ja_existe_cloudflare(mensagem):
    mensagem = valor_texto(mensagem, "").lower()
    termos = [
        "record already exists",
        "already exists",
        "an identical record already exists",
        "dns record already exists",
        "existing dns record"
    ]
    return any(termo in mensagem for termo in termos)


def criar_dns_proxied_para_worker_route(config, hostname):
    """
    Worker Routes precisam que exista um DNS record para o hostname.
    Criamos um A record proxied apontando para 192.0.2.1, IP reservado
    usado apenas como alvo neutro, porque o Worker Route intercepta a requisição.
    """
    url = f"https://api.cloudflare.com/client/v4/zones/{config['zone_id']}/dns_records"
    resposta = requests.post(
        url,
        headers=cloudflare_headers_json(config),
        json={
            "type": "A",
            "name": hostname,
            "content": "192.0.2.1",
            "ttl": 1,
            "proxied": True,
            "comment": "LTDAFinder Pro - DNS automático para Worker Route"
        },
        timeout=30
    )

    try:
        payload = resposta.json()
    except Exception:
        payload = {}

    if resposta.ok and payload.get("success") is not False:
        return payload

    erro = resumir_erros_cloudflare(payload, resposta.text)

    if dns_record_ja_existe_cloudflare(erro):
        return payload

    raise RuntimeError(f"Falhou ao criar DNS proxied para {hostname}: {erro}")


def buscar_rota_worker_cloudflare(config, pattern):
    url = f"https://api.cloudflare.com/client/v4/zones/{config['zone_id']}/workers/routes"
    resposta = requests.get(
        url,
        headers=cloudflare_headers_json(config),
        params={"per_page": 100},
        timeout=30
    )

    try:
        payload = resposta.json()
    except Exception:
        payload = {}

    if not resposta.ok or payload.get("success") is False:
        return None

    for rota in payload.get("result", []) or []:
        if rota.get("pattern") == pattern:
            return rota

    return None


def anexar_rota_worker_cloudflare(config, worker_name, hostname):
    """
    Fallback mais estável para muitos subdomínios:
    cria/atualiza uma Worker Route: subdominio.dominio.com/* -> worker_name.
    """
    criar_dns_proxied_para_worker_route(config, hostname)

    pattern = f"{hostname}/*"
    rota_existente = buscar_rota_worker_cloudflare(config, pattern)

    if rota_existente and rota_existente.get("id"):
        url = f"https://api.cloudflare.com/client/v4/zones/{config['zone_id']}/workers/routes/{rota_existente['id']}"
        metodo = requests.put
    else:
        url = f"https://api.cloudflare.com/client/v4/zones/{config['zone_id']}/workers/routes"
        metodo = requests.post

    resposta = metodo(
        url,
        headers=cloudflare_headers_json(config),
        json={
            "pattern": pattern,
            "script": worker_name
        },
        timeout=30
    )

    try:
        payload = resposta.json()
    except Exception:
        payload = {}

    if not resposta.ok or payload.get("success") is False:
        erro = resumir_erros_cloudflare(payload, resposta.text)
        raise RuntimeError(f"Worker enviado, mas falhou ao criar rota {pattern}: {erro}")

    return payload

def publicar_site_na_cloudflare(site, nome_personalizado="", dominio_publicacao=""):
    dominio_publicacao = dominio_publicacao or valor_texto(site.get("cloudflare_dominio_base", ""))
    config = validar_config_cloudflare(dominio_publicacao)
    slug_publico = gerar_nome_worker_site(site, nome_personalizado)
    worker_name = gerar_nome_worker_interno(site, slug_publico, config)
    worker_js = gerar_worker_js_site_registro(site)

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

    if config.get("custom_domain"):
        hostname = f"{slug_publico}.{config['custom_domain']}"
        modo_publicacao = valor_texto(
            config.get("publish_mode")
            or os.environ.get("CLOUDFLARE_PUBLISH_MODE", "route")
        ).lower()
        aviso_publicacao = ""

        if modo_publicacao in ["custom_strict", "custom_only", "custom-only"]:
            anexar_dominio_customizado_cloudflare(config, worker_name, hostname)
            modo_usado = "Custom Domain"
        elif modo_publicacao in ["custom", "custom_domain", "domain"]:
            try:
                anexar_dominio_customizado_cloudflare(config, worker_name, hostname)
                modo_usado = "Custom Domain"
            except Exception as erro_custom_domain:
                anexar_rota_worker_cloudflare(config, worker_name, hostname)
                modo_usado = "Worker Route"
                aviso_publicacao = f"Custom Domain falhou, então publiquei via Worker Route: {erro_custom_domain}"
        else:
            anexar_rota_worker_cloudflare(config, worker_name, hostname)
            modo_usado = "Worker Route"

        return {
            "worker_name": worker_name,
            "cloudflare_url": f"https://{hostname}",
            "aviso": aviso_publicacao or f"Publicado via {modo_usado}",
            "hostname": hostname,
            "dominio_base": config["custom_domain"]
        }

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
        "aviso": aviso_subdominio,
        "hostname": cloudflare_url.replace("https://", ""),
        "dominio_base": dominio_publicacao_cloudflare(config)
    }


def gerar_wrangler_toml_site(nome_worker):
    hoje = agora_brasilia().strftime("%Y-%m-%d")

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
        "cloudflare_slug_personalizado",
        "cloudflare_worker_name",
        "cloudflare_url",
        "cloudflare_dominio_base",
        "cloudflare_hostname",
        "cloudflare_status",
        "cloudflare_erro",
        "politica_html",
        "termos_html",
        "arquivos_verificacao",
        "dns_txt_nome",
        "dns_txt_valor",
        "versao_template"
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
                OR LOWER(COALESCE(cloudflare_worker_name, '')) LIKE :busca
                OR LOWER(COALESCE(cloudflare_slug_personalizado, '')) LIKE :busca
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
                    cloudflare_slug_personalizado,
                    cloudflare_worker_name,
                    cloudflare_url,
                    cloudflare_dominio_base,
                    cloudflare_hostname,
                    cloudflare_status,
                    cloudflare_publicado_em,
                    cloudflare_erro,
                    atualizado_em,
                    versao_template
                FROM sites_gerados
                {where_sql}
                ORDER BY id DESC
                LIMIT 200
            """),
            params
        ).mappings().fetchall()

    sites = []

    for row in resultado:
        item = dict(row)
        item["criado_em"] = converter_data_hora_sql_para_brasilia(item.get("criado_em"))
        item["cloudflare_publicado_em"] = converter_data_hora_sql_para_brasilia(item.get("cloudflare_publicado_em"))
        item["atualizado_em"] = converter_data_hora_sql_para_brasilia(item.get("atualizado_em"))
        sites.append(item)

    return sites


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

    if not resultado:
        return None

    site = dict(resultado)
    site["criado_em"] = converter_data_hora_sql_para_brasilia(site.get("criado_em"))
    site["cloudflare_publicado_em"] = converter_data_hora_sql_para_brasilia(site.get("cloudflare_publicado_em"))
    site["atualizado_em"] = converter_data_hora_sql_para_brasilia(site.get("atualizado_em"))
    return site


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



def contagem_publicacoes_por_dominio():
    criar_tabela_sites_gerados()
    with engine.connect() as conn:
        linhas = conn.execute(text("""
            SELECT COALESCE(cloudflare_dominio_base, '') AS dominio, COUNT(*) AS total
            FROM sites_gerados
            WHERE cloudflare_status = 'Publicado'
            GROUP BY COALESCE(cloudflare_dominio_base, '')
        """)).mappings().fetchall()
    return {normalizar_dominio_cloudflare(linha["dominio"]): int(linha["total"]) for linha in linhas if linha["dominio"]}


def opcoes_dominios_publicacao():
    contagem = contagem_publicacoes_por_dominio()
    opcoes = []
    for config in listar_configs_cloudflare():
        if not config.get("ativo", True):
            continue
        if not config.get("account_id") or not config.get("api_token"):
            continue
        if config.get("custom_domain") and not config.get("zone_id"):
            continue
        dominio = dominio_publicacao_cloudflare(config)
        opcoes.append({
            "dominio": dominio,
            "total": contagem.get(dominio, 0),
        })
    return sorted(opcoes, key=lambda item: (item["total"], item["dominio"]))


def resolver_dominio_publicacao(escolha="auto"):
    escolha = normalizar_dominio_cloudflare(escolha)
    opcoes = opcoes_dominios_publicacao()
    if not opcoes:
        config = obter_config_cloudflare()
        return dominio_publicacao_cloudflare(config), config
    if escolha and escolha not in {"auto", "automatico"}:
        for item in opcoes:
            if item["dominio"] == escolha:
                return item["dominio"], obter_config_cloudflare(item["dominio"])
    escolhido = opcoes[0]
    return escolhido["dominio"], obter_config_cloudflare(escolhido["dominio"])


def montar_hostname_publicacao(slug, dominio):
    slug = normalizar_nome_worker_cloudflare(slug)
    dominio = normalizar_dominio_cloudflare(dominio)
    return f"{slug}.{dominio}" if slug and dominio else ""


def buscar_site_por_hostname(hostname, excluir_site_id=None):
    criar_tabela_sites_gerados()
    hostname = normalizar_dominio_cloudflare(hostname)
    if not hostname:
        return None

    slug, _, dominio = hostname.partition(".")
    filtros = ["""
        (
            LOWER(COALESCE(cloudflare_hostname, '')) = :hostname
            OR (
                LOWER(COALESCE(cloudflare_slug_personalizado, '')) = :slug
                AND LOWER(COALESCE(cloudflare_dominio_base, '')) = :dominio
            )
        )
    """]
    params = {"hostname": hostname, "slug": slug, "dominio": dominio}

    if excluir_site_id:
        filtros.append("id != :excluir_site_id")
        params["excluir_site_id"] = int(excluir_site_id)

    with engine.connect() as conn:
        return conn.execute(
            text(f"""
                SELECT id, nome_exibicao, nome_empresarial, cloudflare_status
                FROM sites_gerados
                WHERE {' AND '.join(filtros)}
                LIMIT 1
            """),
            params
        ).mappings().fetchone()


def hostname_publicacao_disponivel(slug, dominio, excluir_site_id=None):
    hostname = montar_hostname_publicacao(slug, dominio)
    if not hostname:
        return False, hostname, None
    existente = buscar_site_por_hostname(hostname, excluir_site_id)
    return existente is None, hostname, dict(existente) if existente else None


def atualizar_site_conteudo(site_id, campos):
    permitidos = {
        "nome_exibicao", "nome_fantasia", "telefone_exibicao", "telefone",
        "whatsapp_exibicao", "email_exibicao", "email", "endereco_exibicao",
        "endereco", "meta_tag", "html_gerado", "politica_html", "termos_html",
        "arquivos_verificacao", "dns_txt_nome", "dns_txt_valor", "modelo_site",
        "versao_template", "cloudflare_dominio_base"
    }
    dados = {chave: valor for chave, valor in campos.items() if chave in permitidos}
    if not dados:
        return
    atribuicoes = [f"{chave} = :{chave}" for chave in dados]
    atribuicoes.append("atualizado_em = CURRENT_TIMESTAMP")
    dados["site_id"] = site_id
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE sites_gerados SET {', '.join(atribuicoes)} WHERE id = :site_id"), dados)


def empresa_site_a_partir_registro(site):
    empresa = buscar_empresa_por_cnpj(site.get("cnpj", "")) or {}
    return aplicar_personalizacao_site(
        empresa,
        site.get("nome_exibicao") or site.get("nome_fantasia") or site.get("nome_empresarial"),
        site.get("telefone_exibicao") or site.get("telefone"),
        site.get("email_exibicao") or site.get("email"),
        site.get("endereco_exibicao") or site.get("endereco") or montar_endereco_empresa(empresa),
        site.get("whatsapp_exibicao") or site.get("telefone_exibicao") or site.get("telefone"),
    )


def republicar_site_se_publicado(site_id):
    site = buscar_site_gerado(site_id)
    if not site or site.get("cloudflare_status") != "Publicado":
        return ""
    resultado = publicar_site_na_cloudflare(
        site,
        site.get("cloudflare_slug_personalizado") or site.get("cloudflare_worker_name"),
        site.get("cloudflare_dominio_base", ""),
    )
    atualizar_publicacao_cloudflare_site(
        site_id, "Publicado", resultado["worker_name"], resultado["cloudflare_url"],
        resultado.get("aviso", ""), site.get("cloudflare_slug_personalizado", ""),
        resultado.get("dominio_base", ""), resultado.get("hostname", "")
    )
    return resultado.get("cloudflare_url", "")


def criar_dns_txt_cloudflare(site, nome, valor):
    dominio = site.get("cloudflare_dominio_base") or dominio_publicacao_cloudflare()
    config = validar_config_cloudflare(dominio)
    if not config.get("zone_id"):
        raise RuntimeError("O Zone ID deste domínio não está configurado.")
    hostname = site.get("cloudflare_hostname") or f"{site.get('cloudflare_slug_personalizado')}.{dominio}"
    nome = valor_texto(nome, "").strip() or hostname
    if nome in {"@", dominio}:
        nome = dominio
    elif "." not in nome:
        nome = f"{nome}.{dominio}"
    url = f"https://api.cloudflare.com/client/v4/zones/{config['zone_id']}/dns_records"
    resposta = requests.post(url, headers=cloudflare_headers_json(config), json={
        "type": "TXT", "name": nome, "content": valor, "ttl": 1
    }, timeout=30)
    try:
        payload = resposta.json()
    except Exception:
        payload = {}
    if not resposta.ok or payload.get("success") is False:
        raise RuntimeError(resumir_erros_cloudflare(payload, resposta.text))
    return nome


def modelo_site_nome(slug):
    return MODELOS_SITE_DICT.get(slug, {}).get("nome", slug)


try:
    criar_tabela_sites_gerados()
except Exception:
    pass




# ============================================================
# CENTRAL DE VERIFICAÇÃO BM
# Implementação em SQL/PostgreSQL via database.engine.
# Não usa JSON como armazenamento ativo.
# ============================================================

STATUS_CENTRAL_BM = [
    "Preparando",
    "Pronto para verificar domínio",
    "Domínio verificado",
    "BM em Análise",
    "Verificou 250",
    "Verificou 2k",
    "Verificou 100k",
    "Precisa de mais informações",
    "Análise permanente",
    "Checkpoint",
    "WABA restrita",
    "Conta desabilitada",
    "Descartado"
]

STATUS_CENTRAL_BM_SUCESSO = ["Verificou 250", "Verificou 2k", "Verificou 100k"]
STATUS_CENTRAL_BM_PROBLEMAS = [
    "Precisa de mais informações",
    "Análise permanente",
    "Checkpoint",
    "WABA restrita",
    "Conta desabilitada",
    "Descartado"
]

CHECKLIST_CENTRAL_BM = [
    {"campo": "checklist_cnpj", "label": "CNPJ selecionado e conferido", "peso": 10, "etapa": "Preparação"},
    {"campo": "checklist_perfil_meta", "label": "Perfil Meta vinculado", "peso": 8, "etapa": "Preparação"},
    {"campo": "checklist_site_gerado", "label": "Site gerado e vinculado", "peso": 8, "etapa": "Preparação"},
    {"campo": "checklist_meta_tag", "label": "Meta tag adicionada", "peso": 9, "etapa": "Preparação"},

    {"campo": "checklist_site", "label": "Site publicado e abrindo", "peso": 8, "etapa": "Domínio"},
    {"campo": "checklist_dominio", "label": "Domínio/URL conferido", "peso": 8, "etapa": "Domínio"},
    {"campo": "checklist_https", "label": "HTTPS ativo", "peso": 6, "etapa": "Domínio"},
    {"campo": "checklist_cnpj_site", "label": "CNPJ encontrado no site", "peso": 4, "etapa": "Domínio"},
    {"campo": "checklist_razao_site", "label": "Razão social encontrada no site", "peso": 4, "etapa": "Domínio"},

    {"campo": "checklist_documento", "label": "Documento/cartão CNPJ separado", "peso": 7, "etapa": "Envio Meta"},
    {"campo": "checklist_2fa", "label": "2FA ativo no perfil/BM", "peso": 6, "etapa": "Envio Meta"},
    {"campo": "checklist_bm_enviada", "label": "BM enviada para análise", "peso": 7, "etapa": "Envio Meta"},
    {"campo": "checklist_waba", "label": "WABA preparada ou criada", "peso": 5, "etapa": "Envio Meta"},

    {"campo": "checklist_status_atualizado", "label": "Status atualizado no sistema", "peso": 4, "etapa": "Resultado"},
    {"campo": "checklist_print_salvo", "label": "Print/evidência salvo", "peso": 3, "etapa": "Resultado"},
    {"campo": "checklist_observacao", "label": "Observação operacional registrada", "peso": 3, "etapa": "Resultado"}
]

CHECKLIST_CAMPOS_CENTRAL_BM = [item["campo"] for item in CHECKLIST_CENTRAL_BM]


def checklist_central_bm_por_etapa():
    etapas = []
    mapa = {}
    for item in CHECKLIST_CENTRAL_BM:
        etapa = item.get("etapa", "Checklist")
        if etapa not in mapa:
            grupo = {"etapa": etapa, "itens": []}
            mapa[etapa] = grupo
            etapas.append(grupo)
        mapa[etapa]["itens"].append(item)
    return etapas


def bool_form(nome):
    return request.form.get(nome) in ["on", "1", "true", "True", "sim", "Sim"]


def bool_valor(valor):
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, int):
        return valor == 1
    return str(valor).strip().lower() in ["1", "true", "sim", "on", "yes"]


def criar_tabelas_central_bm():
    try:
        dialect = getattr(engine, "dialect", None)
        dialect_name = getattr(dialect, "name", "postgresql")

        if dialect_name == "postgresql":
            sql_verificacoes = """
            CREATE TABLE IF NOT EXISTS bm_verificacoes (
                id SERIAL PRIMARY KEY,
                usuario TEXT NOT NULL,
                cnpj TEXT NOT NULL,
                cnpj_formatado TEXT,
                razao_social TEXT,
                nome_fantasia TEXT,
                site_gerado_id INTEGER,
                site_nome_arquivo TEXT,
                site_modelo TEXT,
                perfil_meta_id TEXT,
                perfil_meta_nome TEXT,
                nome_bm TEXT,
                dominio TEXT,
                url_site TEXT,
                meta_tag TEXT,
                status TEXT DEFAULT 'Preparando',
                operador TEXT,
                telefone_operacional TEXT,
                email_operacional TEXT,
                numero_sms TEXT,
                checklist_cnpj BOOLEAN DEFAULT FALSE,
                checklist_site BOOLEAN DEFAULT FALSE,
                checklist_meta_tag BOOLEAN DEFAULT FALSE,
                checklist_dominio BOOLEAN DEFAULT FALSE,
                checklist_documento BOOLEAN DEFAULT FALSE,
                checklist_2fa BOOLEAN DEFAULT FALSE,
                checklist_waba BOOLEAN DEFAULT FALSE,
                checklist_perfil_meta BOOLEAN DEFAULT FALSE,
                checklist_site_gerado BOOLEAN DEFAULT FALSE,
                checklist_https BOOLEAN DEFAULT FALSE,
                checklist_cnpj_site BOOLEAN DEFAULT FALSE,
                checklist_razao_site BOOLEAN DEFAULT FALSE,
                checklist_bm_enviada BOOLEAN DEFAULT FALSE,
                checklist_status_atualizado BOOLEAN DEFAULT FALSE,
                checklist_print_salvo BOOLEAN DEFAULT FALSE,
                checklist_observacao BOOLEAN DEFAULT FALSE,
                score_prontidao INTEGER DEFAULT 0,
                risco TEXT DEFAULT 'Alto risco',
                ultimo_teste_status TEXT,
                ultimo_teste_mensagem TEXT,
                ultimo_teste_detalhes TEXT,
                ultimo_teste_em TIMESTAMP,
                observacoes TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
            sql_historico = """
            CREATE TABLE IF NOT EXISTS bm_verificacoes_historico (
                id SERIAL PRIMARY KEY,
                verificacao_id INTEGER REFERENCES bm_verificacoes(id) ON DELETE CASCADE,
                cnpj TEXT,
                acao TEXT,
                status_anterior TEXT,
                status_novo TEXT,
                observacao TEXT,
                operador TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        else:
            sql_verificacoes = """
            CREATE TABLE IF NOT EXISTS bm_verificacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL,
                cnpj TEXT NOT NULL,
                cnpj_formatado TEXT,
                razao_social TEXT,
                nome_fantasia TEXT,
                site_gerado_id INTEGER,
                site_nome_arquivo TEXT,
                site_modelo TEXT,
                perfil_meta_id TEXT,
                perfil_meta_nome TEXT,
                nome_bm TEXT,
                dominio TEXT,
                url_site TEXT,
                meta_tag TEXT,
                status TEXT DEFAULT 'Preparando',
                operador TEXT,
                telefone_operacional TEXT,
                email_operacional TEXT,
                numero_sms TEXT,
                checklist_cnpj BOOLEAN DEFAULT 0,
                checklist_site BOOLEAN DEFAULT 0,
                checklist_meta_tag BOOLEAN DEFAULT 0,
                checklist_dominio BOOLEAN DEFAULT 0,
                checklist_documento BOOLEAN DEFAULT 0,
                checklist_2fa BOOLEAN DEFAULT 0,
                checklist_waba BOOLEAN DEFAULT 0,
                checklist_perfil_meta BOOLEAN DEFAULT 0,
                checklist_site_gerado BOOLEAN DEFAULT 0,
                checklist_https BOOLEAN DEFAULT 0,
                checklist_cnpj_site BOOLEAN DEFAULT 0,
                checklist_razao_site BOOLEAN DEFAULT 0,
                checklist_bm_enviada BOOLEAN DEFAULT 0,
                checklist_status_atualizado BOOLEAN DEFAULT 0,
                checklist_print_salvo BOOLEAN DEFAULT 0,
                checklist_observacao BOOLEAN DEFAULT 0,
                score_prontidao INTEGER DEFAULT 0,
                risco TEXT DEFAULT 'Alto risco',
                ultimo_teste_status TEXT,
                ultimo_teste_mensagem TEXT,
                ultimo_teste_detalhes TEXT,
                ultimo_teste_em TIMESTAMP,
                observacoes TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
            sql_historico = """
            CREATE TABLE IF NOT EXISTS bm_verificacoes_historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verificacao_id INTEGER,
                cnpj TEXT,
                acao TEXT,
                status_anterior TEXT,
                status_novo TEXT,
                observacao TEXT,
                operador TEXT,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """

        colunas_verificacoes = {
            "usuario": "TEXT NOT NULL DEFAULT ''",
            "cnpj": "TEXT NOT NULL DEFAULT ''",
            "cnpj_formatado": "TEXT",
            "razao_social": "TEXT",
            "nome_fantasia": "TEXT",
            "site_gerado_id": "INTEGER",
            "site_nome_arquivo": "TEXT",
            "site_modelo": "TEXT",
            "perfil_meta_id": "TEXT",
            "perfil_meta_nome": "TEXT",
            "nome_bm": "TEXT",
            "dominio": "TEXT",
            "url_site": "TEXT",
            "meta_tag": "TEXT",
            "status": "TEXT DEFAULT 'Preparando'",
            "operador": "TEXT",
            "telefone_operacional": "TEXT",
            "email_operacional": "TEXT",
            "numero_sms": "TEXT",
            "checklist_cnpj": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_site": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_meta_tag": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_dominio": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_documento": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_2fa": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_waba": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_perfil_meta": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_site_gerado": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_https": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_cnpj_site": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_razao_site": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_bm_enviada": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_status_atualizado": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_print_salvo": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "checklist_observacao": "BOOLEAN DEFAULT FALSE" if dialect_name == "postgresql" else "BOOLEAN DEFAULT 0",
            "score_prontidao": "INTEGER DEFAULT 0",
            "risco": "TEXT DEFAULT 'Alto risco'",
            "ultimo_teste_status": "TEXT",
            "ultimo_teste_mensagem": "TEXT",
            "ultimo_teste_detalhes": "TEXT",
            "ultimo_teste_em": "TIMESTAMP",
            "observacoes": "TEXT",
            "criado_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "atualizado_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        }

        with engine.begin() as conn:
            conn.execute(text(sql_verificacoes))
            conn.execute(text(sql_historico))

            if dialect_name == "postgresql":
                for coluna, tipo in colunas_verificacoes.items():
                    conn.execute(text(f"ALTER TABLE bm_verificacoes ADD COLUMN IF NOT EXISTS {coluna} {tipo}"))
            else:
                existentes = conn.execute(text("PRAGMA table_info(bm_verificacoes)")).fetchall()
                colunas_existentes = {linha[1] for linha in existentes}

                for coluna, tipo in colunas_verificacoes.items():
                    if coluna not in colunas_existentes:
                        conn.execute(text(f"ALTER TABLE bm_verificacoes ADD COLUMN {coluna} {tipo}"))

    except Exception as erro:
        print("Erro ao criar tabelas da Central BM:", erro)


def linha_para_dict(linha):
    if not linha:
        return None
    try:
        return dict(linha._mapping)
    except Exception:
        return dict(linha)


def calcular_score_prontidao_bm(dados):
    pontos = 0
    total_peso = 0

    for item in CHECKLIST_CENTRAL_BM:
        peso = int(item.get("peso", 0) or 0)
        total_peso += peso
        if bool_valor(dados.get(item["campo"], False)):
            pontos += peso

    score = int(round((pontos / total_peso) * 100)) if total_peso else 0
    score = max(0, min(100, score))

    if score >= 80:
        risco = "Pronto"
    elif score >= 50:
        risco = "Atenção"
    else:
        risco = "Alto risco"

    return score, risco


def perfil_meta_por_id(perfil_id):
    perfil_id = str(perfil_id or "").strip()

    if not perfil_id:
        return None

    for perfil in carregar_perfis_meta():
        if str(perfil.get("id", "")).strip() == perfil_id:
            return perfil

    return None


def nome_perfil_meta(perfil):
    if not perfil:
        return ""

    partes = []

    for chave in ["nome", "login", "telefone"]:
        valor = valor_texto(perfil.get(chave, ""))
        if valor:
            partes.append(valor)

    return " | ".join(partes)


def perfis_meta_para_select():
    perfis = carregar_perfis_meta()
    itens = []

    for perfil in perfis:
        itens.append({
            "id": str(perfil.get("id", "")),
            "nome": nome_perfil_meta(perfil) or "Perfil sem identificação",
            "cnpj_limpo": str(perfil.get("cnpj_limpo", "")),
            "status_bm": str(perfil.get("status_bm", ""))
        })

    return sorted(itens, key=lambda item: item["nome"].lower())


def obter_site_recente_do_cnpj(cnpj):
    criar_tabela_sites_gerados()
    cnpj_limpo = limpar_cnpj(cnpj)

    try:
        with engine.connect() as conn:
            linha = conn.execute(
                text("""
                SELECT id, nome_arquivo, modelo_site, cloudflare_url, cloudflare_status, cloudflare_worker_name, cloudflare_slug_personalizado, meta_tag, nome_exibicao, telefone_exibicao, email_exibicao, endereco_exibicao
                FROM sites_gerados
                WHERE cnpj = :cnpj
                ORDER BY id DESC
                LIMIT 1
                """),
                {"cnpj": cnpj_limpo}
            ).fetchone()

        return linha_para_dict(linha)
    except Exception:
        return None


def montar_dados_verificacao_form(empresa=None, verificacao=None):
    dados = {}

    if empresa:
        cnpj_limpo = limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", "")))
        dados.update({
            "cnpj": cnpj_limpo,
            "cnpj_formatado": formatar_cnpj(cnpj_limpo),
            "razao_social": valor_texto(empresa.get("razao_social", "")),
            "nome_fantasia": valor_texto(empresa.get("nome_fantasia", "")),
            "telefone_operacional": valor_texto(empresa.get("telefone_formatado", "")),
            "email_operacional": valor_texto(empresa.get("email", "")),
            "checklist_cnpj": True
        })

        site = obter_site_recente_do_cnpj(cnpj_limpo)
        if site:
            dados["site_gerado_id"] = site.get("id")
            dados["site_nome_arquivo"] = valor_texto(site.get("nome_arquivo", ""))
            dados["site_modelo"] = valor_texto(site.get("modelo_site", ""))
            dados["url_site"] = valor_texto(site.get("cloudflare_url", ""))
            dados["meta_tag"] = valor_texto(site.get("meta_tag", ""))
            dados["checklist_site"] = bool(dados.get("url_site"))
            dados["checklist_meta_tag"] = bool(dados.get("meta_tag"))
            dados["checklist_dominio"] = bool(dados.get("url_site"))
            if dados.get("url_site"):
                try:
                    dados["dominio"] = urlparse(dados.get("url_site")).netloc or dados.get("url_site")
                except Exception:
                    dados["dominio"] = dados.get("url_site")

            if site.get("telefone_exibicao"):
                dados["telefone_operacional"] = valor_texto(site.get("telefone_exibicao", ""))
            if site.get("email_exibicao"):
                dados["email_operacional"] = valor_texto(site.get("email_exibicao", ""))

    if verificacao:
        dados.update(verificacao)

    for item in CHECKLIST_CENTRAL_BM:
        dados[item["campo"]] = bool_valor(dados.get(item["campo"], False))

    dados.setdefault("status", "Preparando")
    dados.setdefault("operador", usuario_atual())
    dados.setdefault("nome_bm", "")
    dados.setdefault("dominio", "")
    dados.setdefault("url_site", "")
    dados.setdefault("meta_tag", "")
    dados.setdefault("site_gerado_id", None)
    dados.setdefault("site_nome_arquivo", "")
    dados.setdefault("site_modelo", "")
    dados.setdefault("perfil_meta_id", "")
    dados.setdefault("perfil_meta_nome", "")
    dados.setdefault("telefone_operacional", "")
    dados.setdefault("email_operacional", "")
    dados.setdefault("numero_sms", "")
    dados.setdefault("observacoes", "")
    dados.setdefault("score_prontidao", 0)
    dados.setdefault("risco", "Alto risco")

    return dados


def dados_verificacao_do_form(empresa=None, verificacao_atual=None):
    cnpj_form = request.form.get("cnpj", "").strip()
    cnpj_base = cnpj_form or (empresa or {}).get("cnpj_limpo", "") or (verificacao_atual or {}).get("cnpj", "")
    cnpj_limpo = limpar_cnpj(cnpj_base)

    razao_social = valor_texto(request.form.get("razao_social", "")) or valor_texto((empresa or {}).get("razao_social", "")) or valor_texto((verificacao_atual or {}).get("razao_social", ""))
    nome_fantasia = valor_texto(request.form.get("nome_fantasia", "")) or valor_texto((empresa or {}).get("nome_fantasia", "")) or valor_texto((verificacao_atual or {}).get("nome_fantasia", ""))
    perfil_id = request.form.get("perfil_meta_id", "").strip()
    perfil = perfil_meta_por_id(perfil_id)
    perfil_nome = nome_perfil_meta(perfil) if perfil else request.form.get("perfil_meta_nome", "").strip()
    status = request.form.get("status", "Preparando").strip()

    if status not in STATUS_CENTRAL_BM:
        status = "Preparando"

    dados = {
        "usuario": usuario_atual(),
        "cnpj": cnpj_limpo,
        "cnpj_formatado": formatar_cnpj(cnpj_limpo),
        "razao_social": razao_social,
        "nome_fantasia": nome_fantasia,
        "site_gerado_id": int(request.form.get("site_gerado_id", "").strip()) if request.form.get("site_gerado_id", "").strip().isdigit() else ((verificacao_atual or {}).get("site_gerado_id") or None),
        "site_nome_arquivo": request.form.get("site_nome_arquivo", "").strip() or (verificacao_atual or {}).get("site_nome_arquivo", ""),
        "site_modelo": request.form.get("site_modelo", "").strip() or (verificacao_atual or {}).get("site_modelo", ""),
        "perfil_meta_id": perfil_id,
        "perfil_meta_nome": perfil_nome,
        "nome_bm": request.form.get("nome_bm", "").strip(),
        "dominio": request.form.get("dominio", "").strip(),
        "url_site": request.form.get("url_site", "").strip(),
        "meta_tag": request.form.get("meta_tag", "").strip(),
        "status": status,
        "operador": request.form.get("operador", usuario_atual()).strip() or usuario_atual(),
        "telefone_operacional": request.form.get("telefone_operacional", "").strip(),
        "email_operacional": request.form.get("email_operacional", "").strip(),
        "numero_sms": request.form.get("numero_sms", "").strip(),
        "observacoes": request.form.get("observacoes", "").strip(),
    }

    for item in CHECKLIST_CENTRAL_BM:
        dados[item["campo"]] = bool_form(item["campo"])

    if dados["meta_tag"] and "<meta" in dados["meta_tag"].lower():
        dados["checklist_meta_tag"] = True

    if dados["url_site"] or dados["dominio"]:
        dados["checklist_dominio"] = True

    if dados.get("perfil_meta_id"):
        dados["checklist_perfil_meta"] = True

    if dados.get("site_gerado_id"):
        dados["checklist_site_gerado"] = True

    if str(dados.get("url_site", "")).startswith("https://"):
        dados["checklist_https"] = True

    if dados.get("status") and dados.get("status") != "Preparando":
        dados["checklist_status_atualizado"] = True

    if dados.get("status") in ["BM em Análise"] + STATUS_CENTRAL_BM_SUCESSO + STATUS_CENTRAL_BM_PROBLEMAS:
        dados["checklist_bm_enviada"] = True

    if dados.get("observacoes"):
        dados["checklist_observacao"] = True

    dados["score_prontidao"], dados["risco"] = calcular_score_prontidao_bm(dados)
    return dados


def registrar_historico_verificacao(verificacao_id, acao, status_antigo="", status_novo="", observacao="", operador="", cnpj=""):
    criar_tabelas_central_bm()

    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO bm_verificacoes_historico
                (verificacao_id, cnpj, acao, status_anterior, status_novo, observacao, operador)
                VALUES
                (:verificacao_id, :cnpj, :acao, :status_anterior, :status_novo, :observacao, :operador)
                """),
                {
                    "verificacao_id": verificacao_id,
                    "cnpj": cnpj,
                    "acao": acao,
                    "status_anterior": status_antigo,
                    "status_novo": status_novo,
                    "observacao": observacao,
                    "operador": operador or usuario_atual()
                }
            )
    except Exception as erro:
        print("Erro ao registrar histórico da Central BM:", erro)


def salvar_status_operacional_por_central(verificacao, status_antigo, status_novo):
    if not verificacao or status_novo not in STATUS_OPCOES:
        return

    usuario = verificacao.get("operador") or verificacao.get("usuario") or usuario_atual()
    cnpj = limpar_cnpj(verificacao.get("cnpj", ""))

    if not cnpj:
        return

    try:
        if status_novo == STATUS_PADRAO:
            remover_status(usuario, cnpj)
            remover_data_uso_cnpj(usuario, cnpj)
        else:
            salvar_status(usuario, cnpj, status_novo)
            registrar_data_uso_cnpj(usuario, cnpj, status_novo)

        registrar_historico_producao(usuario, status_antigo or STATUS_PADRAO, status_novo)
        registrar_evento(usuario, formatar_cnpj(cnpj), status_antigo or STATUS_PADRAO, f"{status_novo} via Central BM")
    except Exception as erro:
        print("Erro ao sincronizar status operacional pela Central BM:", erro)


def criar_verificacao_bm(dados):
    criar_tabelas_central_bm()
    dialect_name = getattr(getattr(engine, "dialect", None), "name", "postgresql")

    campos = [
        "usuario", "cnpj", "cnpj_formatado", "razao_social", "nome_fantasia",
        "site_gerado_id", "site_nome_arquivo", "site_modelo",
        "perfil_meta_id", "perfil_meta_nome", "nome_bm", "dominio", "url_site", "meta_tag",
        "status", "operador", "telefone_operacional", "email_operacional", "numero_sms"
    ] + CHECKLIST_CAMPOS_CENTRAL_BM + ["score_prontidao", "risco", "observacoes"]

    params = {campo: dados.get(campo) for campo in campos}

    colunas_sql = ", ".join(campos)
    valores_sql = ", ".join([f":{campo}" for campo in campos])

    with engine.begin() as conn:
        if dialect_name == "postgresql":
            linha = conn.execute(
                text(f"""
                INSERT INTO bm_verificacoes ({colunas_sql})
                VALUES ({valores_sql})
                RETURNING id
                """),
                params
            ).fetchone()
            verificacao_id = int(linha[0])
        else:
            conn.execute(
                text(f"""
                INSERT INTO bm_verificacoes ({colunas_sql})
                VALUES ({valores_sql})
                """),
                params
            )
            verificacao_id = int(conn.execute(text("SELECT last_insert_rowid() AS id")).fetchone()[0])

    registrar_historico_verificacao(
        verificacao_id,
        "Criação",
        "",
        dados.get("status", "Preparando"),
        "Verificação BM criada",
        dados.get("operador", usuario_atual()),
        dados.get("cnpj", "")
    )

    salvar_status_operacional_por_central(dados, STATUS_PADRAO, dados.get("status", "Preparando"))
    return verificacao_id


def buscar_verificacao_bm(verificacao_id):
    criar_tabelas_central_bm()

    with engine.connect() as conn:
        linha = conn.execute(
            text("""
            SELECT *
            FROM bm_verificacoes
            WHERE id = :id
            """),
            {"id": verificacao_id}
        ).fetchone()

    verificacao = linha_para_dict(linha)

    if verificacao:
        for item in CHECKLIST_CENTRAL_BM:
            verificacao[item["campo"]] = bool_valor(verificacao.get(item["campo"], False))

    return verificacao


def atualizar_verificacao_bm(verificacao_id, dados):
    criar_tabelas_central_bm()
    atual = buscar_verificacao_bm(verificacao_id)

    if not atual:
        return False

    campos = [
        "cnpj", "cnpj_formatado", "razao_social", "nome_fantasia",
        "site_gerado_id", "site_nome_arquivo", "site_modelo",
        "perfil_meta_id", "perfil_meta_nome", "nome_bm", "dominio", "url_site", "meta_tag",
        "status", "operador", "telefone_operacional", "email_operacional", "numero_sms"
    ] + CHECKLIST_CAMPOS_CENTRAL_BM + ["score_prontidao", "risco", "observacoes"]

    params = {campo: dados.get(campo) for campo in campos}
    params["id"] = verificacao_id

    set_sql = ",\n                    ".join([f"{campo} = :{campo}" for campo in campos])

    with engine.begin() as conn:
        conn.execute(
            text(f"""
            UPDATE bm_verificacoes
            SET {set_sql},
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = :id
            """),
            params
        )

    status_antigo = atual.get("status", "Preparando")
    status_novo = dados.get("status", "Preparando")

    if status_antigo != status_novo:
        registrar_historico_verificacao(
            verificacao_id,
            "Status",
            status_antigo,
            status_novo,
            dados.get("observacoes", ""),
            dados.get("operador", usuario_atual()),
            dados.get("cnpj", "")
        )
        salvar_status_operacional_por_central(dados, status_antigo, status_novo)
    else:
        registrar_historico_verificacao(
            verificacao_id,
            "Atualização",
            status_antigo,
            status_novo,
            "Dados/checklist atualizados",
            dados.get("operador", usuario_atual()),
            dados.get("cnpj", "")
        )

    return True


def historico_verificacao_bm(verificacao_id):
    criar_tabelas_central_bm()

    with engine.connect() as conn:
        linhas = conn.execute(
            text("""
            SELECT *
            FROM bm_verificacoes_historico
            WHERE verificacao_id = :id
            ORDER BY id DESC
            LIMIT 100
            """),
            {"id": verificacao_id}
        ).fetchall()

    return [linha_para_dict(linha) for linha in linhas]


def listar_verificacoes_bm(filtros=None):
    criar_tabelas_central_bm()
    filtros = filtros or {}
    usuario = usuario_atual()
    params = {}
    where = []

    if tipo_usuario() != "admin":
        where.append("usuario = :usuario")
        params["usuario"] = usuario

    busca = filtros.get("busca", "").strip()
    status = filtros.get("status", "").strip()
    risco = filtros.get("risco", "").strip()
    operador = filtros.get("operador", "").strip()

    if busca:
        params["busca"] = f"%{busca.upper()}%"
        where.append("""
        (
            UPPER(COALESCE(cnpj, '')) LIKE :busca OR
            UPPER(COALESCE(cnpj_formatado, '')) LIKE :busca OR
            UPPER(COALESCE(razao_social, '')) LIKE :busca OR
            UPPER(COALESCE(nome_fantasia, '')) LIKE :busca OR
            UPPER(COALESCE(nome_bm, '')) LIKE :busca OR
            UPPER(COALESCE(dominio, '')) LIKE :busca OR
            UPPER(COALESCE(url_site, '')) LIKE :busca OR
            UPPER(COALESCE(perfil_meta_nome, '')) LIKE :busca
        )
        """)

    if status:
        where.append("status = :status")
        params["status"] = status

    if risco:
        where.append("risco = :risco")
        params["risco"] = risco

    if operador:
        where.append("operador = :operador")
        params["operador"] = operador

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    with engine.connect() as conn:
        linhas = conn.execute(
            text(f"""
            SELECT *
            FROM bm_verificacoes
            {where_sql}
            ORDER BY id DESC
            LIMIT 500
            """),
            params
        ).fetchall()

    itens = [linha_para_dict(linha) for linha in linhas]

    for item in itens:
        for checklist in CHECKLIST_CENTRAL_BM:
            item[checklist["campo"]] = bool_valor(item.get(checklist["campo"], False))

    return itens


def estatisticas_central_bm(verificacoes):
    total = len(verificacoes)
    sucesso = len([item for item in verificacoes if item.get("status") in STATUS_CENTRAL_BM_SUCESSO])
    problemas = len([item for item in verificacoes if item.get("status") in STATUS_CENTRAL_BM_PROBLEMAS])
    prontas = len([item for item in verificacoes if item.get("risco") == "Pronto"])
    atencao = len([item for item in verificacoes if item.get("risco") == "Atenção"])
    alto_risco = len([item for item in verificacoes if item.get("risco") == "Alto risco"])
    em_analise = len([item for item in verificacoes if item.get("status") == "BM em Análise"])
    taxa_sucesso = (sucesso / total * 100) if total else 0

    return {
        "total": total,
        "sucesso": sucesso,
        "problemas": problemas,
        "prontas": prontas,
        "atencao": atencao,
        "alto_risco": alto_risco,
        "em_analise": em_analise,
        "taxa_sucesso": taxa_sucesso
    }


def normalizar_url_teste(url):
    url = valor_texto(url, "")

    if not url:
        return ""

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return url


def extrair_codigo_meta(meta_tag):
    meta_tag = valor_texto(meta_tag, "")

    if not meta_tag:
        return ""

    match = re.search(r'content=["\']([^"\']+)["\']', meta_tag, flags=re.I)
    if match:
        return match.group(1).strip()

    return ""


def texto_normalizado_busca(texto):
    texto = valor_texto(texto, "").lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", texto).strip()


def testar_dominio_verificacao(verificacao):
    url_alvo = normalizar_url_teste(verificacao.get("url_site") or verificacao.get("dominio"))
    detalhes = []

    if not url_alvo:
        return {
            "status": "erro",
            "mensagem": "Informe uma URL ou domínio antes de testar.",
            "detalhes": "URL/domínio vazio."
        }

    try:
        resposta = requests.get(
            url_alvo,
            timeout=15,
            allow_redirects=True,
            headers={
                "User-Agent": "LTDAFinder-Pro-Domain-Checker/1.0"
            }
        )

        html = resposta.text or ""
        url_final = resposta.url or url_alvo
        codigo_http = resposta.status_code
        html_limpo = texto_normalizado_busca(html)
        html_digitos = re.sub(r"\D+", "", html)
        head_match = re.search(r"<head[^>]*>(.*?)</head>", html, flags=re.I | re.S)
        head_html = head_match.group(1) if head_match else ""

        online = codigo_http < 400
        https_ok = url_final.startswith("https://")
        meta_tag = valor_texto(verificacao.get("meta_tag", ""))
        codigo_meta = extrair_codigo_meta(meta_tag)
        meta_encontrada = False
        meta_no_head = False

        if meta_tag:
            meta_encontrada = meta_tag in html
            meta_no_head = meta_tag in head_html

        if codigo_meta:
            meta_encontrada = meta_encontrada or (codigo_meta in html)
            meta_no_head = meta_no_head or (codigo_meta in head_html)

        tem_facebook_meta = "facebook-domain-verification" in html.lower()
        meta_encontrada = meta_encontrada or tem_facebook_meta
        meta_no_head = meta_no_head or ("facebook-domain-verification" in head_html.lower())

        cnpj = limpar_cnpj(verificacao.get("cnpj", ""))
        cnpj_ok = bool(cnpj and cnpj in html_digitos)

        razao = texto_normalizado_busca(verificacao.get("razao_social", ""))
        nome_ok = False
        if razao:
            palavras = [palavra for palavra in razao.split() if len(palavra) >= 3]
            if palavras:
                acertos = sum(1 for palavra in palavras[:4] if palavra in html_limpo)
                nome_ok = acertos >= max(1, min(2, len(palavras)))

        detalhes.append(f"URL testada: {url_alvo}")
        detalhes.append(f"URL final: {url_final}")
        detalhes.append(f"HTTP: {codigo_http}")
        detalhes.append("HTTPS: OK" if https_ok else "HTTPS: atenção")
        detalhes.append("Meta tag: encontrada" if meta_encontrada else "Meta tag: não encontrada")
        detalhes.append("Meta tag no head: sim" if meta_no_head else "Meta tag no head: não")
        detalhes.append("CNPJ no HTML: sim" if cnpj_ok else "CNPJ no HTML: não encontrado")
        detalhes.append("Razão social no HTML: sim" if nome_ok else "Razão social no HTML: não encontrada")

        if not online:
            status = "erro"
            mensagem = f"Site respondeu HTTP {codigo_http}. Não tente verificar ainda."
        elif online and https_ok and meta_encontrada and meta_no_head:
            status = "pronto"
            mensagem = "Domínio tecnicamente pronto para tentativa de verificação na Meta."
        elif online and meta_encontrada:
            status = "atencao"
            mensagem = "Site online e meta tag encontrada, mas há pontos de atenção antes de tentar."
        else:
            status = "erro"
            mensagem = "Site online, mas a meta tag da Meta não foi encontrada."

        return {
            "status": status,
            "mensagem": mensagem,
            "detalhes": "\n".join(detalhes),
            "checklist_site": online,
            "checklist_meta_tag": meta_encontrada,
            "checklist_dominio": online and https_ok,
            "checklist_https": https_ok,
            "checklist_cnpj_site": cnpj_ok,
            "checklist_razao_site": nome_ok
        }

    except Exception as erro:
        detalhes.append(f"Erro: {erro}")
        return {
            "status": "erro",
            "mensagem": "Não consegui acessar o domínio/URL informado.",
            "detalhes": "\n".join(detalhes)
        }




def dominio_de_url_site(url):
    url = normalizar_url_teste(url)
    if not url:
        return ""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


def buscar_verificacao_por_site(site_id):
    criar_tabelas_central_bm()

    params = {"site_id": site_id}
    filtros = ["site_gerado_id = :site_id"]

    if tipo_usuario() != "admin":
        filtros.append("usuario = :usuario")
        params["usuario"] = usuario_atual()

    try:
        with engine.connect() as conn:
            linha = conn.execute(
                text(f"""
                SELECT *
                FROM bm_verificacoes
                WHERE {" AND ".join(filtros)}
                ORDER BY id DESC
                LIMIT 1
                """),
                params
            ).fetchone()
        return linha_para_dict(linha)
    except Exception:
        return None


def ids_verificacao_por_sites(site_ids):
    criar_tabelas_central_bm()
    ids_limpos = []

    for site_id in site_ids:
        try:
            ids_limpos.append(int(site_id))
        except Exception:
            pass

    if not ids_limpos:
        return {}

    placeholders = ", ".join([f":id_{idx}" for idx, _ in enumerate(ids_limpos)])
    params = {f"id_{idx}": site_id for idx, site_id in enumerate(ids_limpos)}
    where_usuario = ""

    if tipo_usuario() != "admin":
        where_usuario = "AND usuario = :usuario"
        params["usuario"] = usuario_atual()

    try:
        with engine.connect() as conn:
            linhas = conn.execute(
                text(f"""
                SELECT site_gerado_id, MAX(id) AS verificacao_id
                FROM bm_verificacoes
                WHERE site_gerado_id IN ({placeholders})
                {where_usuario}
                GROUP BY site_gerado_id
                """),
                params
            ).mappings().fetchall()

        return {str(linha["site_gerado_id"]): linha["verificacao_id"] for linha in linhas if linha.get("site_gerado_id")}
    except Exception:
        return {}


def enriquecer_sites_com_verificacao_bm(sites):
    mapa = ids_verificacao_por_sites([site.get("id") for site in sites])

    for site in sites:
        site["verificacao_bm_id"] = mapa.get(str(site.get("id")), "")

    return sites


def montar_dados_verificacao_por_site(site):
    empresa = buscar_empresa_por_cnpj(site.get("cnpj", ""))
    dados = montar_dados_verificacao_form(empresa)
    url_site = valor_texto(site.get("cloudflare_url", ""))
    meta_tag = valor_texto(site.get("meta_tag", ""))
    cnpj_limpo = limpar_cnpj(site.get("cnpj", "") or (empresa or {}).get("cnpj_limpo", ""))

    dados.update({
        "usuario": usuario_atual(),
        "cnpj": cnpj_limpo,
        "cnpj_formatado": formatar_cnpj(cnpj_limpo),
        "razao_social": valor_texto(site.get("nome_empresarial", "")) or valor_texto((empresa or {}).get("razao_social", "")),
        "nome_fantasia": valor_texto(site.get("nome_fantasia", "")) or valor_texto(site.get("nome_exibicao", "")) or valor_texto((empresa or {}).get("nome_fantasia", "")),
        "site_gerado_id": site.get("id"),
        "site_nome_arquivo": valor_texto(site.get("nome_arquivo", "")),
        "site_modelo": valor_texto(site.get("modelo_site", "")),
        "dominio": dominio_de_url_site(url_site),
        "url_site": url_site,
        "meta_tag": meta_tag,
        "status": "Pronto para verificar domínio" if url_site and meta_tag else "Preparando",
        "operador": usuario_atual(),
        "telefone_operacional": valor_texto(site.get("telefone_exibicao", "")) or valor_texto(site.get("telefone", "")),
        "email_operacional": valor_texto(site.get("email_exibicao", "")) or valor_texto(site.get("email", "")),
        "checklist_cnpj": True,
        "checklist_site_gerado": True,
        "checklist_site": bool(url_site),
        "checklist_meta_tag": bool(meta_tag),
        "checklist_dominio": bool(url_site),
        "checklist_https": bool(url_site and url_site.startswith("https://")),
    })

    dados["score_prontidao"], dados["risco"] = calcular_score_prontidao_bm(dados)
    return dados


def atualizar_verificacoes_do_site_publicado(site_id, cloudflare_url, slug_personalizado=""):
    criar_tabelas_central_bm()
    dominio = dominio_de_url_site(cloudflare_url)

    try:
        with engine.connect() as conn:
            linhas = conn.execute(
                text("""
                SELECT *
                FROM bm_verificacoes
                WHERE site_gerado_id = :site_id
                """),
                {"site_id": site_id}
            ).mappings().fetchall()

        for linha in linhas:
            dados = dict(linha)
            dados["url_site"] = cloudflare_url
            dados["dominio"] = dominio
            dados["checklist_site_gerado"] = True
            dados["checklist_site"] = True
            dados["checklist_dominio"] = True
            dados["checklist_https"] = str(cloudflare_url).startswith("https://")
            if dados.get("meta_tag"):
                dados["checklist_meta_tag"] = True
            dados["score_prontidao"], dados["risco"] = calcular_score_prontidao_bm(dados)

            with engine.begin() as conn:
                conn.execute(
                    text("""
                    UPDATE bm_verificacoes
                    SET url_site = :url_site,
                        dominio = :dominio,
                        checklist_site = :checklist_site,
                        checklist_dominio = :checklist_dominio,
                        checklist_meta_tag = :checklist_meta_tag,
                        checklist_site_gerado = :checklist_site_gerado,
                        checklist_https = :checklist_https,
                        score_prontidao = :score_prontidao,
                        risco = :risco,
                        atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """),
                    {
                        "id": dados.get("id"),
                        "url_site": cloudflare_url,
                        "dominio": dominio,
                        "checklist_site": dados.get("checklist_site"),
                        "checklist_dominio": dados.get("checklist_dominio"),
                        "checklist_meta_tag": dados.get("checklist_meta_tag"),
                        "checklist_site_gerado": dados.get("checklist_site_gerado"),
                        "checklist_https": dados.get("checklist_https"),
                        "score_prontidao": dados.get("score_prontidao"),
                        "risco": dados.get("risco")
                    }
                )

            registrar_historico_verificacao(
                dados.get("id"),
                "Site atualizado",
                dados.get("status", ""),
                dados.get("status", ""),
                f"URL publicada na Cloudflare vinculada: {cloudflare_url}",
                usuario_atual(),
                dados.get("cnpj", "")
            )
    except Exception as erro:
        print("Erro ao sincronizar site publicado com Central BM:", erro)



def parse_datetime_bm(valor):
    if not valor:
        return None

    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            return valor.replace(tzinfo=FUSO_UTC).astimezone(FUSO_BRASILIA)
        return valor.astimezone(FUSO_BRASILIA)

    valor = str(valor).strip()

    if not valor:
        return None

    formatos = [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d"
    ]

    valor_limpo = valor.replace("Z", "").split("+")[0].strip()

    for formato in formatos:
        try:
            data = datetime.strptime(valor_limpo[:26], formato)
            return data.replace(tzinfo=FUSO_UTC).astimezone(FUSO_BRASILIA)
        except Exception:
            pass

    return None


def horas_desde_bm(valor):
    data = parse_datetime_bm(valor)

    if not data:
        return 0

    try:
        return max(0, int((agora_brasilia() - data).total_seconds() // 3600))
    except Exception:
        return 0


def data_bm_eh_hoje(valor):
    data = parse_datetime_bm(valor)
    return bool(data and data.date() == agora_brasilia().date())


def primeiro_texto_bm(item, *chaves):
    for chave in chaves:
        valor = valor_texto(item.get(chave, ""))
        if valor:
            return valor
    return ""


def titulo_verificacao_radar(item):
    nome = primeiro_texto_bm(item, "razao_social", "nome_fantasia", "nome_bm")
    cnpj = primeiro_texto_bm(item, "cnpj_formatado", "cnpj")

    if nome and cnpj:
        return f"{nome} · {cnpj}"

    return nome or cnpj or "Verificação BM"


def montar_acao_radar(tipo, titulo, descricao, url, prioridade="media"):
    return {
        "tipo": tipo,
        "titulo": titulo,
        "descricao": descricao,
        "url": url,
        "prioridade": prioridade
    }


def montar_radar_bm():
    verificacoes = listar_verificacoes_bm({})
    agora = agora_brasilia()

    for item in verificacoes:
        data_ref = item.get("atualizado_em") or item.get("criado_em")
        item["horas_desde_atualizacao"] = horas_desde_bm(data_ref)
        item["atualizado_hoje"] = data_bm_eh_hoje(data_ref)
        item["titulo_radar"] = titulo_verificacao_radar(item)
        item["tem_url_ou_dominio"] = bool(valor_texto(item.get("url_site", "")) or valor_texto(item.get("dominio", "")))
        item["dominio_testado_pronto"] = str(item.get("ultimo_teste_status", "")).strip().lower() == "pronto"
        item["em_status_final"] = item.get("status") in (STATUS_CENTRAL_BM_SUCESSO + STATUS_CENTRAL_BM_PROBLEMAS)

    abertas = [item for item in verificacoes if not item.get("em_status_final")]

    prontas_para_analise = [
        item for item in abertas
        if item.get("risco") == "Pronto"
        and item.get("status") in ["Preparando", "Pronto para verificar domínio", "Domínio verificado"]
    ]

    precisam_teste = [
        item for item in abertas
        if item.get("tem_url_ou_dominio")
        and not item.get("dominio_testado_pronto")
        and item.get("status") not in ["BM em Análise"]
    ]

    pendentes = [
        item for item in abertas
        if item.get("status") != "BM em Análise"
        and (
            item.get("risco") != "Pronto"
            or not bool_valor(item.get("checklist_site"))
            or not bool_valor(item.get("checklist_meta_tag"))
            or not bool_valor(item.get("checklist_dominio"))
        )
    ]

    em_analise_48h = [
        item for item in abertas
        if item.get("status") == "BM em Análise"
        and item.get("horas_desde_atualizacao", 0) >= 48
    ]

    em_analise = [item for item in abertas if item.get("status") == "BM em Análise"]

    verificaram_hoje = [
        item for item in verificacoes
        if item.get("status") in STATUS_CENTRAL_BM_SUCESSO
        and item.get("atualizado_hoje")
    ]

    problemas = [item for item in verificacoes if item.get("status") in STATUS_CENTRAL_BM_PROBLEMAS]
    problemas_hoje = [item for item in problemas if item.get("atualizado_hoje")]

    meta_ausente = [
        item for item in abertas
        if not valor_texto(item.get("meta_tag", ""))
        or not bool_valor(item.get("checklist_meta_tag"))
    ]

    site_ausente = [
        item for item in abertas
        if not valor_texto(item.get("url_site", ""))
        and not valor_texto(item.get("dominio", ""))
    ]

    status_contagem = {}
    operador_contagem = {}

    for item in verificacoes:
        status = item.get("status") or "Sem status"
        status_contagem[status] = status_contagem.get(status, 0) + 1

        operador = item.get("operador") or item.get("usuario") or "Sem operador"
        if operador not in operador_contagem:
            operador_contagem[operador] = {"operador": operador, "total": 0, "sucesso": 0, "problemas": 0, "taxa": 0}

        operador_contagem[operador]["total"] += 1
        if item.get("status") in STATUS_CENTRAL_BM_SUCESSO:
            operador_contagem[operador]["sucesso"] += 1
        if item.get("status") in STATUS_CENTRAL_BM_PROBLEMAS:
            operador_contagem[operador]["problemas"] += 1

    operadores = []
    for item in operador_contagem.values():
        item["taxa"] = (item["sucesso"] / item["total"] * 100) if item["total"] else 0
        operadores.append(item)

    operadores = sorted(operadores, key=lambda item: (item["sucesso"], item["total"]), reverse=True)
    status_ranking = sorted([
        {"status": status, "total": total}
        for status, total in status_contagem.items()
    ], key=lambda item: item["total"], reverse=True)

    try:
        sites = enriquecer_sites_com_verificacao_bm(listar_sites_gerados())
    except Exception:
        sites = []

    sites_sem_bm = [
        site for site in sites
        if not site.get("verificacao_bm_id")
        and (site.get("cloudflare_url") or site.get("nome_arquivo"))
    ]

    acoes = []

    for site in sites_sem_bm[:5]:
        acoes.append(montar_acao_radar(
            "Site sem BM",
            site.get("nome_empresarial") or site.get("nome_fantasia") or "Site gerado",
            f"Site {site.get('nome_arquivo', '')} ainda não está vinculado a uma verificação BM.",
            f"/site-gerado/{site.get('id')}",
            "alta"
        ))

    for item in precisam_teste[:5]:
        acoes.append(montar_acao_radar(
            "Testar domínio",
            item.get("titulo_radar"),
            "URL/domínio informado, mas ainda sem teste pronto. Rode o pré-check antes de enviar para análise.",
            f"/central-bm/{item.get('id')}",
            "alta"
        ))

    for item in em_analise_48h[:5]:
        acoes.append(montar_acao_radar(
            "+48h em análise",
            item.get("titulo_radar"),
            f"Está em BM em Análise há aproximadamente {item.get('horas_desde_atualizacao', 0)}h.",
            f"/central-bm/{item.get('id')}",
            "media"
        ))

    for item in pendentes[:5]:
        faltando = []
        if not bool_valor(item.get("checklist_site")):
            faltando.append("site")
        if not bool_valor(item.get("checklist_meta_tag")):
            faltando.append("meta tag")
        if not bool_valor(item.get("checklist_dominio")):
            faltando.append("domínio")

        complemento = ", ".join(faltando) if faltando else "checklist/score"
        acoes.append(montar_acao_radar(
            "Pendência",
            item.get("titulo_radar"),
            f"Revisar {complemento} antes de seguir.",
            f"/central-bm/{item.get('id')}",
            "media"
        ))

    total = len(verificacoes)
    sucesso_total = len([item for item in verificacoes if item.get("status") in STATUS_CENTRAL_BM_SUCESSO])
    taxa_sucesso = (sucesso_total / total * 100) if total else 0

    alertas_criticos = gerar_alertas_gerais_bm(verificacoes, 10)

    return {
        "gerado_em": agora.strftime("%d/%m/%Y %H:%M"),
        "cards": {
            "total": total,
            "prontas_para_analise": len(prontas_para_analise),
            "pendentes": len(pendentes),
            "precisam_teste": len(precisam_teste),
            "em_analise": len(em_analise),
            "em_analise_48h": len(em_analise_48h),
            "verificaram_hoje": len(verificaram_hoje),
            "problemas_hoje": len(problemas_hoje),
            "sites_sem_bm": len(sites_sem_bm),
            "meta_ausente": len(meta_ausente),
            "site_ausente": len(site_ausente),
            "alertas_criticos": len([a for a in alertas_criticos if a.get("nivel") == "critico"]),
            "taxa_sucesso": taxa_sucesso
        },
        "listas": {
            "prontas_para_analise": prontas_para_analise[:12],
            "pendentes": pendentes[:12],
            "precisam_teste": precisam_teste[:12],
            "em_analise_48h": em_analise_48h[:12],
            "verificaram_hoje": verificaram_hoje[:12],
            "problemas": problemas[:12],
            "sites_sem_bm": sites_sem_bm[:12],
            "acoes": acoes[:14],
            "status_ranking": status_ranking[:10],
            "operadores": operadores[:10],
            "alertas": alertas_criticos
        }
    }



# ============================================================
# ALERTAS, FILA, RELATÓRIO DIÁRIO E DOSSIÊ BM
# ============================================================

STATUS_TAREFA_FILA_BM = ["Pendente", "Em andamento", "Concluída", "Problema", "Ignorada"]


def criar_tabela_fila_bm():
    dialect_name = getattr(getattr(engine, "dialect", None), "name", "postgresql")

    if dialect_name == "postgresql":
        sql = """
        CREATE TABLE IF NOT EXISTS fila_bm_tarefas (
            id SERIAL PRIMARY KEY,
            task_key TEXT UNIQUE NOT NULL,
            verificacao_id INTEGER,
            site_gerado_id INTEGER,
            cnpj TEXT,
            cnpj_formatado TEXT,
            razao_social TEXT,
            tipo_tarefa TEXT,
            prioridade TEXT,
            status_tarefa TEXT DEFAULT 'Pendente',
            operador TEXT,
            acao_url TEXT,
            observacao TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            concluido_em TIMESTAMP
        )
        """
        add_column = "ALTER TABLE fila_bm_tarefas ADD COLUMN IF NOT EXISTS {coluna} {tipo}"
    else:
        sql = """
        CREATE TABLE IF NOT EXISTS fila_bm_tarefas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_key TEXT UNIQUE NOT NULL,
            verificacao_id INTEGER,
            site_gerado_id INTEGER,
            cnpj TEXT,
            cnpj_formatado TEXT,
            razao_social TEXT,
            tipo_tarefa TEXT,
            prioridade TEXT,
            status_tarefa TEXT DEFAULT 'Pendente',
            operador TEXT,
            acao_url TEXT,
            observacao TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            concluido_em TIMESTAMP
        )
        """
        add_column = None

    colunas = {
        "task_key": "TEXT",
        "verificacao_id": "INTEGER",
        "site_gerado_id": "INTEGER",
        "cnpj": "TEXT",
        "cnpj_formatado": "TEXT",
        "razao_social": "TEXT",
        "tipo_tarefa": "TEXT",
        "prioridade": "TEXT",
        "status_tarefa": "TEXT DEFAULT 'Pendente'",
        "operador": "TEXT",
        "acao_url": "TEXT",
        "observacao": "TEXT",
        "criado_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "atualizado_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "concluido_em": "TIMESTAMP"
    }

    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
            if dialect_name == "postgresql":
                for coluna, tipo in colunas.items():
                    conn.execute(text(add_column.format(coluna=coluna, tipo=tipo)))
            else:
                existentes = conn.execute(text("PRAGMA table_info(fila_bm_tarefas)")).fetchall()
                colunas_existentes = {linha[1] for linha in existentes}
                for coluna, tipo in colunas.items():
                    if coluna not in colunas_existentes:
                        conn.execute(text(f"ALTER TABLE fila_bm_tarefas ADD COLUMN {coluna} {tipo}"))
    except Exception as erro:
        print("Erro ao criar tabela da Fila BM:", erro)


def prioridade_ordem_bm(prioridade):
    return {"alta": 1, "média": 2, "media": 2, "baixa": 3}.get(str(prioridade or "").lower(), 4)


def montar_tarefa_fila(task_key, tipo, prioridade, titulo, acao_url, verificacao=None, site=None, observacao=""):
    verificacao = verificacao or {}
    site = site or {}
    cnpj = limpar_cnpj(verificacao.get("cnpj") or site.get("cnpj") or "")
    return {
        "task_key": task_key,
        "verificacao_id": verificacao.get("id"),
        "site_gerado_id": site.get("id") or verificacao.get("site_gerado_id"),
        "cnpj": cnpj,
        "cnpj_formatado": formatar_cnpj(cnpj) if cnpj else (verificacao.get("cnpj_formatado") or site.get("cnpj_formatado") or ""),
        "razao_social": titulo,
        "tipo_tarefa": tipo,
        "prioridade": prioridade,
        "operador": verificacao.get("operador") or site.get("usuario") or usuario_atual(),
        "acao_url": acao_url,
        "observacao": observacao
    }


def gerar_tarefas_automaticas_fila_bm():
    verificacoes = listar_verificacoes_bm({})
    tarefas = []

    for item in verificacoes:
        item["tem_url_ou_dominio"] = bool(valor_texto(item.get("url_site", "")) or valor_texto(item.get("dominio", "")))
        item["dominio_testado_pronto"] = str(item.get("ultimo_teste_status", "")).strip().lower() == "pronto"
        item["em_status_final"] = item.get("status") in (STATUS_CENTRAL_BM_SUCESSO + STATUS_CENTRAL_BM_PROBLEMAS)
        item["horas_desde_atualizacao"] = horas_desde_bm(item.get("atualizado_em") or item.get("criado_em"))
        titulo = titulo_verificacao_radar(item)
        url = f"/central-bm/{item.get('id')}"

        if item.get("em_status_final"):
            continue

        if item.get("status") == "BM em Análise" and item.get("horas_desde_atualizacao", 0) >= 48:
            tarefas.append(montar_tarefa_fila(
                f"analise-48h-{item.get('id')}", "+48h em análise", "Alta", titulo, url, item,
                observacao=f"Está em análise há aproximadamente {item.get('horas_desde_atualizacao', 0)}h."
            ))

        if item.get("tem_url_ou_dominio") and not item.get("dominio_testado_pronto") and item.get("status") != "BM em Análise":
            tarefas.append(montar_tarefa_fila(
                f"testar-dominio-{item.get('id')}", "Testar domínio", "Alta", titulo, url, item,
                observacao="URL/domínio informado, mas sem pré-check pronto."
            ))

        if item.get("risco") == "Pronto" and item.get("status") in ["Preparando", "Pronto para verificar domínio", "Domínio verificado"]:
            tarefas.append(montar_tarefa_fila(
                f"pronta-analise-{item.get('id')}", "Pronta para análise", "Alta", titulo, url, item,
                observacao="Score pronto para seguir para análise, revise e avance o status."
            ))

        if not bool_valor(item.get("checklist_meta_tag")) or not valor_texto(item.get("meta_tag", "")):
            tarefas.append(montar_tarefa_fila(
                f"meta-ausente-{item.get('id')}", "Corrigir meta tag", "Média", titulo, url, item,
                observacao="Meta tag ausente ou checklist não marcado."
            ))

        if item.get("risco") == "Alto risco":
            tarefas.append(montar_tarefa_fila(
                f"score-baixo-{item.get('id')}", "Revisar score baixo", "Média", titulo, url, item,
                observacao="Score abaixo de 50. Revise checklist, domínio e dados antes de enviar."
            ))

        if not item.get("operador"):
            tarefas.append(montar_tarefa_fila(
                f"sem-operador-{item.get('id')}", "Definir operador", "Baixa", titulo, url, item,
                observacao="Verificação sem operador definido."
            ))

    try:
        sites = enriquecer_sites_com_verificacao_bm(listar_sites_gerados())
    except Exception:
        sites = []

    for site in sites:
        if site.get("verificacao_bm_id"):
            continue
        titulo = site.get("nome_empresarial") or site.get("nome_fantasia") or site.get("nome_arquivo") or "Site gerado"
        tarefas.append(montar_tarefa_fila(
            f"site-sem-bm-{site.get('id')}", "Criar BM do site", "Média", titulo, f"/site-gerado/{site.get('id')}", site=site,
            observacao="Site gerado ainda não vinculado à Central BM."
        ))

    tarefas = sorted(tarefas, key=lambda t: (prioridade_ordem_bm(t.get("prioridade")), t.get("tipo_tarefa", ""), t.get("razao_social", "")))
    return tarefas


def sincronizar_fila_bm():
    criar_tabela_fila_bm()
    tarefas = gerar_tarefas_automaticas_fila_bm()

    try:
        with engine.begin() as conn:
            for tarefa in tarefas:
                existente = conn.execute(
                    text("SELECT id, status_tarefa FROM fila_bm_tarefas WHERE task_key = :task_key"),
                    {"task_key": tarefa["task_key"]}
                ).fetchone()

                if existente:
                    status_atual = linha_para_dict(existente).get("status_tarefa")
                    if status_atual not in ["Concluída", "Ignorada"]:
                        conn.execute(
                            text("""
                            UPDATE fila_bm_tarefas
                            SET verificacao_id = :verificacao_id,
                                site_gerado_id = :site_gerado_id,
                                cnpj = :cnpj,
                                cnpj_formatado = :cnpj_formatado,
                                razao_social = :razao_social,
                                tipo_tarefa = :tipo_tarefa,
                                prioridade = :prioridade,
                                operador = :operador,
                                acao_url = :acao_url,
                                observacao = :observacao,
                                atualizado_em = CURRENT_TIMESTAMP
                            WHERE task_key = :task_key
                            """),
                            tarefa
                        )
                else:
                    params = dict(tarefa)
                    params["status_tarefa"] = "Pendente"
                    conn.execute(
                        text("""
                        INSERT INTO fila_bm_tarefas
                        (task_key, verificacao_id, site_gerado_id, cnpj, cnpj_formatado, razao_social, tipo_tarefa, prioridade, status_tarefa, operador, acao_url, observacao)
                        VALUES
                        (:task_key, :verificacao_id, :site_gerado_id, :cnpj, :cnpj_formatado, :razao_social, :tipo_tarefa, :prioridade, :status_tarefa, :operador, :acao_url, :observacao)
                        """),
                        params
                    )
    except Exception as erro:
        print("Erro ao sincronizar Fila BM:", erro)


def listar_tarefas_fila_bm(filtros=None):
    sincronizar_fila_bm()
    filtros = filtros or {}
    where = []
    params = {}

    if tipo_usuario() != "admin":
        where.append("(operador = :operador_usuario OR operador IS NULL OR operador = '')")
        params["operador_usuario"] = usuario_atual()

    busca = valor_texto(filtros.get("busca", ""))
    prioridade = valor_texto(filtros.get("prioridade", ""))
    status_tarefa = valor_texto(filtros.get("status_tarefa", "")) or "Pendente"
    operador = valor_texto(filtros.get("operador", ""))

    if busca:
        params["busca"] = f"%{busca.upper()}%"
        where.append("""
        (
            UPPER(COALESCE(cnpj, '')) LIKE :busca OR
            UPPER(COALESCE(cnpj_formatado, '')) LIKE :busca OR
            UPPER(COALESCE(razao_social, '')) LIKE :busca OR
            UPPER(COALESCE(tipo_tarefa, '')) LIKE :busca OR
            UPPER(COALESCE(observacao, '')) LIKE :busca
        )
        """)

    if prioridade:
        where.append("prioridade = :prioridade")
        params["prioridade"] = prioridade

    if status_tarefa:
        where.append("status_tarefa = :status_tarefa")
        params["status_tarefa"] = status_tarefa

    if operador:
        where.append("operador = :operador")
        params["operador"] = operador

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    with engine.connect() as conn:
        linhas = conn.execute(
            text(f"""
            SELECT *
            FROM fila_bm_tarefas
            {where_sql}
            ORDER BY
                CASE LOWER(COALESCE(prioridade,'')) WHEN 'alta' THEN 1 WHEN 'média' THEN 2 WHEN 'media' THEN 2 WHEN 'baixa' THEN 3 ELSE 4 END,
                id DESC
            LIMIT 500
            """),
            params
        ).fetchall()

    return [linha_para_dict(linha) for linha in linhas]


def estatisticas_fila_bm():
    criar_tabela_fila_bm()
    sincronizar_fila_bm()

    with engine.connect() as conn:
        linhas = conn.execute(text("SELECT status_tarefa, prioridade, operador, concluido_em FROM fila_bm_tarefas")).fetchall()

    hoje = agora_brasilia().date()
    stats = {"total": 0, "pendentes": 0, "alta": 0, "sem_responsavel": 0, "concluidas_hoje": 0, "problemas": 0}
    operadores = {}

    for linha in linhas:
        item = linha_para_dict(linha)
        stats["total"] += 1
        if item.get("status_tarefa") in ["Pendente", "Em andamento"]:
            stats["pendentes"] += 1
        if str(item.get("prioridade", "")).lower() == "alta" and item.get("status_tarefa") in ["Pendente", "Em andamento"]:
            stats["alta"] += 1
        if not item.get("operador") and item.get("status_tarefa") in ["Pendente", "Em andamento"]:
            stats["sem_responsavel"] += 1
        if item.get("status_tarefa") == "Problema":
            stats["problemas"] += 1
        data_concluida = parse_datetime_bm(item.get("concluido_em"))
        if item.get("status_tarefa") == "Concluída" and data_concluida and data_concluida.date() == hoje:
            stats["concluidas_hoje"] += 1

        operador = item.get("operador") or "Sem responsável"
        operadores[operador] = operadores.get(operador, 0) + 1

    stats["operadores"] = sorted([{"operador": k, "total": v} for k, v in operadores.items()], key=lambda x: x["total"], reverse=True)[:10]
    return stats


def atualizar_status_tarefa_fila(tarefa_id, status_tarefa, observacao=""):
    criar_tabela_fila_bm()
    if status_tarefa not in STATUS_TAREFA_FILA_BM:
        status_tarefa = "Pendente"

    complemento = ", concluido_em = CURRENT_TIMESTAMP" if status_tarefa == "Concluída" else ""

    with engine.begin() as conn:
        conn.execute(
            text(f"""
            UPDATE fila_bm_tarefas
            SET status_tarefa = :status_tarefa,
                observacao = CASE WHEN :observacao <> '' THEN :observacao ELSE observacao END,
                atualizado_em = CURRENT_TIMESTAMP
                {complemento}
            WHERE id = :id
            """),
            {"id": tarefa_id, "status_tarefa": status_tarefa, "observacao": observacao}
        )


def gerar_alertas_verificacao_bm(item):
    alertas = []
    if not item:
        return alertas

    finalizado = item.get("status") in (STATUS_CENTRAL_BM_SUCESSO + STATUS_CENTRAL_BM_PROBLEMAS)
    horas = horas_desde_bm(item.get("atualizado_em") or item.get("criado_em"))

    def add(nivel, titulo, texto):
        alertas.append({"nivel": nivel, "titulo": titulo, "texto": texto})

    if not finalizado and item.get("status") == "BM em Análise" and horas >= 48:
        add("critico", "+48h em análise", f"Está sem atualização há aproximadamente {horas}h.")
    if not finalizado and not valor_texto(item.get("url_site", "")) and not valor_texto(item.get("dominio", "")):
        add("critico", "Sem URL/domínio", "Informe a URL publicada ou domínio antes de seguir.")
    if not finalizado and (not item.get("meta_tag") or not bool_valor(item.get("checklist_meta_tag"))):
        add("critico", "Meta tag pendente", "Meta tag ausente ou ainda não validada no checklist.")
    if not finalizado and item.get("risco") == "Alto risco":
        add("atencao", "Score baixo", "Score de prontidão abaixo de 50.")
    if not finalizado and valor_texto(item.get("url_site", "")).endswith(".workers.dev"):
        add("atencao", "Workers.dev detectado", "URL de preview/Workers detectada. Revise se é o domínio esperado para a operação.")
    if not finalizado and not item.get("operador"):
        add("atencao", "Sem operador", "Defina o responsável pela verificação.")
    if finalizado and not bool_valor(item.get("checklist_observacao")):
        add("info", "Sem observação final", "Registre uma observação para fechar o histórico operacional.")

    return alertas


def gerar_alertas_gerais_bm(verificacoes=None, limite=12):
    verificacoes = verificacoes if verificacoes is not None else listar_verificacoes_bm({})
    alertas = []
    for item in verificacoes:
        for alerta in gerar_alertas_verificacao_bm(item):
            alerta = dict(alerta)
            alerta["verificacao_id"] = item.get("id")
            alerta["titulo_bm"] = titulo_verificacao_radar(item)
            alerta["url"] = f"/central-bm/{item.get('id')}"
            alertas.append(alerta)

    ordem = {"critico": 1, "atencao": 2, "info": 3}
    return sorted(alertas, key=lambda a: ordem.get(a.get("nivel"), 9))[:limite]


def texto_pdf_seguro(texto):
    texto = str(texto or "")
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def gerar_pdf_simples(titulo, linhas):
    largura, altura = 595, 842
    margem_x, y_inicial = 46, 792
    linhas_pdf = []
    y = y_inicial

    def add_linha(txt, tamanho=10, negrito=False):
        nonlocal y
        if y < 60:
            y = y_inicial
            linhas_pdf.append("__NOVA_PAGINA__")
        fonte = "F2" if negrito else "F1"
        linhas_pdf.append(f"BT /{fonte} {tamanho} Tf {margem_x} {y} Td ({texto_pdf_seguro(txt)}) Tj ET")
        y -= int(tamanho * 1.55)

    add_linha(titulo, 16, True)
    add_linha("LTDAFinder Pro", 10, False)
    add_linha("", 8, False)

    for linha in linhas:
        linha = str(linha or "")
        if len(linha) <= 92:
            add_linha(linha, 10, linha.isupper() and len(linha) < 60)
        else:
            atual = linha
            while len(atual) > 92:
                corte = atual.rfind(" ", 0, 92)
                if corte <= 0:
                    corte = 92
                add_linha(atual[:corte], 10, False)
                atual = atual[corte:].strip()
            if atual:
                add_linha(atual, 10, False)

    paginas = [[]]
    for cmd in linhas_pdf:
        if cmd == "__NOVA_PAGINA__":
            paginas.append([])
        else:
            paginas[-1].append(cmd)

    objetos = []
    objetos.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join([f"{3+i*2} 0 R" for i in range(len(paginas))])
    objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(paginas)} >>")

    for i, comandos in enumerate(paginas):
        page_obj_num = 3 + i * 2
        content_obj_num = page_obj_num + 1
        objetos.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {largura} {altura}] /Resources << /Font << /F1 1 0 R /F2 2 0 R >> >> /Contents {content_obj_num} 0 R >>")
        stream = "\n".join(comandos)
        objetos.append(f"<< /Length {len(stream.encode('latin1', errors='ignore'))} >>\nstream\n{stream}\nendstream")

    # Reorganiza fontes nos objetos 1 e 2 da forma correta para PDF simples.
    kids_pdf = " ".join([f"{5+i*2} 0 R" for i in range(len(paginas))])
    objetos_pdf = [
        "<< /Type /Catalog /Pages 4 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        f"<< /Type /Pages /Kids [{kids_pdf}] /Count {len(paginas)} >>"
    ]

    for i, comandos in enumerate(paginas):
        page_obj_num = 5 + i * 2
        content_obj_num = page_obj_num + 1
        objetos_pdf.append(f"<< /Type /Page /Parent 4 0 R /MediaBox [0 0 {largura} {altura}] /Resources << /Font << /F1 2 0 R /F2 3 0 R >> >> /Contents {content_obj_num} 0 R >>")
        stream = "\n".join(comandos)
        objetos_pdf.append(f"<< /Length {len(stream.encode('latin1', errors='ignore'))} >>\nstream\n{stream}\nendstream")

    pdf = "%PDF-1.4\n"
    offsets = []
    for idx, obj in enumerate(objetos_pdf, start=1):
        offsets.append(len(pdf.encode('latin1', errors='ignore')))
        pdf += f"{idx} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf.encode('latin1', errors='ignore'))
    pdf += f"xref\n0 {len(objetos_pdf)+1}\n0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n"
    pdf += f"trailer\n<< /Size {len(objetos_pdf)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode("latin1", errors="ignore")


def linhas_dossie_bm(verificacao, historico):
    linhas = [
        "Documento interno gerado pelo LTDAFinder Pro.",
        "Nao substitui documentos oficiais emitidos por orgaos publicos.",
        "",
        "DADOS DA EMPRESA",
        f"CNPJ: {verificacao.get('cnpj_formatado') or formatar_cnpj(verificacao.get('cnpj', ''))}",
        f"Razao social: {verificacao.get('razao_social', '')}",
        f"Nome fantasia: {verificacao.get('nome_fantasia', '')}",
        "",
        "OPERACAO BM",
        f"Status: {verificacao.get('status', '')}",
        f"Operador: {verificacao.get('operador', '')}",
        f"Perfil Meta: {verificacao.get('perfil_meta_nome', '')}",
        f"Nome BM: {verificacao.get('nome_bm', '')}",
        f"Score: {verificacao.get('score_prontidao', 0)}/100 - {verificacao.get('risco', '')}",
        "",
        "DOMINIO E SITE",
        f"URL: {verificacao.get('url_site', '')}",
        f"Dominio: {verificacao.get('dominio', '')}",
        f"Site vinculado: {verificacao.get('site_nome_arquivo', '')}",
        f"Modelo: {verificacao.get('site_modelo', '')}",
        "",
        "CONTATOS OPERACIONAIS",
        f"Telefone: {verificacao.get('telefone_operacional', '')}",
        f"E-mail: {verificacao.get('email_operacional', '')}",
        f"Numero SMS: {verificacao.get('numero_sms', '')}",
        "",
        "CHECKLIST"
    ]

    for item in CHECKLIST_CENTRAL_BM:
        marcado = "SIM" if bool_valor(verificacao.get(item['campo'])) else "NAO"
        linhas.append(f"[{marcado}] {item.get('etapa', '')} - {item.get('label', '')}")

    linhas.extend(["", "ULTIMO TESTE DE DOMINIO"])
    linhas.append(f"Status: {verificacao.get('ultimo_teste_status', '')}")
    linhas.append(f"Mensagem: {verificacao.get('ultimo_teste_mensagem', '')}")
    linhas.append("",)
    linhas.append("OBSERVACOES")
    linhas.append(verificacao.get('observacoes', '') or "Sem observacoes.")
    linhas.append("")
    linhas.append("HISTORICO")

    for item in historico[:40]:
        linhas.append(f"{item.get('criado_em', '')} - {item.get('acao', '')}: {item.get('status_anterior', '')} -> {item.get('status_novo', '')} | {item.get('observacao', '')}")

    return linhas


def resumo_relatorio_diario_central(data_iso=None, usuario_filtro=""):
    data_iso = data_iso or data_hoje()
    verificacoes = listar_verificacoes_bm({})
    itens_dia = []

    for item in verificacoes:
        data_ref = parse_datetime_bm(item.get("atualizado_em") or item.get("criado_em"))
        if not data_ref or data_ref.strftime("%Y-%m-%d") != data_iso:
            continue
        if usuario_filtro and (item.get("operador") or item.get("usuario")) != usuario_filtro:
            continue
        itens_dia.append(item)

    contagem = {status: 0 for status in STATUS_CENTRAL_BM}
    operadores = {}

    for item in itens_dia:
        status = item.get("status") or "Preparando"
        if status not in contagem:
            contagem[status] = 0
        contagem[status] += 1
        operador = item.get("operador") or item.get("usuario") or "Sem operador"
        if operador not in operadores:
            operadores[operador] = {"operador": operador, "total": 0, "sucesso": 0, "problemas": 0}
        operadores[operador]["total"] += 1
        if status in STATUS_CENTRAL_BM_SUCESSO:
            operadores[operador]["sucesso"] += 1
        if status in STATUS_CENTRAL_BM_PROBLEMAS:
            operadores[operador]["problemas"] += 1

    sucesso = sum(contagem.get(status, 0) for status in STATUS_CENTRAL_BM_SUCESSO)
    problemas = sum(contagem.get(status, 0) for status in STATUS_CENTRAL_BM_PROBLEMAS)
    total = len(itens_dia)

    operadores_lista = []
    for op in operadores.values():
        op["taxa_sucesso"] = (op["sucesso"] / op["total"] * 100) if op["total"] else 0
        operadores_lista.append(op)

    return {
        "data": data_iso,
        "total": total,
        "sucesso": sucesso,
        "problemas": problemas,
        "taxa_sucesso": (sucesso / total * 100) if total else 0,
        "contagem": contagem,
        "operadores": sorted(operadores_lista, key=lambda x: (x["sucesso"], x["total"]), reverse=True),
        "itens": itens_dia
    }


def gerar_texto_relatorio_diario_central(resumo):
    data_br = formatar_data_relatorio(resumo.get("data", data_hoje()))
    linhas = [
        "RELATORIO OPERACIONAL BM",
        f"Data: {data_br}",
        "",
        f"Total trabalhadas/atualizadas: {resumo.get('total', 0)}",
        f"Resultados positivos: {resumo.get('sucesso', 0)}",
        f"Problemas: {resumo.get('problemas', 0)}",
        f"Taxa de sucesso: {resumo.get('taxa_sucesso', 0):.1f}%",
        "",
        "RESULTADOS POR STATUS:"
    ]

    for status, qtd in resumo.get("contagem", {}).items():
        if qtd:
            linhas.append(f"- {status}: {qtd}")

    linhas.append("")
    linhas.append("POR OPERADOR:")
    for op in resumo.get("operadores", []):
        linhas.append(f"- {op['operador']}: {op['total']} trabalhadas | {op['sucesso']} positivas | {op['problemas']} problemas | {op['taxa_sucesso']:.1f}%")

    linhas.append("")
    linhas.append("ITENS DO DIA:")
    for item in resumo.get("itens", []):
        linhas.append(f"- {item.get('cnpj_formatado', '')} | {item.get('razao_social', '')} | {item.get('status', '')} | {item.get('operador', '')}")

    return "\n".join(linhas)


@app.route("/fila-bm", methods=["GET"])
@login_obrigatorio
def fila_bm():
    filtros = {
        "busca": request.args.get("busca", ""),
        "prioridade": request.args.get("prioridade", ""),
        "status_tarefa": request.args.get("status_tarefa", "Pendente"),
        "operador": request.args.get("operador", "")
    }
    tarefas = listar_tarefas_fila_bm(filtros)
    estatisticas = estatisticas_fila_bm()
    usuarios = sorted(carregar_usuarios().keys()) if tipo_usuario() == "admin" else [usuario_atual()]

    return render_template(
        "fila_bm.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        tarefas=tarefas,
        estatisticas=estatisticas,
        filtros=filtros,
        usuarios=usuarios,
        status_tarefa_fila=STATUS_TAREFA_FILA_BM
    )


@app.route("/fila-bm/<int:tarefa_id>/status", methods=["POST"])
@login_obrigatorio
def fila_bm_status(tarefa_id):
    status_tarefa = request.form.get("status_tarefa", "Pendente")
    observacao = request.form.get("observacao", "").strip()
    atualizar_status_tarefa_fila(tarefa_id, status_tarefa, observacao)
    return redirect(request.referrer or url_for("fila_bm"))


@app.route("/central-bm/<int:verificacao_id>/dossie-pdf", methods=["GET"])
@login_obrigatorio
def dossie_bm_pdf(verificacao_id):
    verificacao = buscar_verificacao_bm(verificacao_id)
    if not verificacao:
        abort(404)
    if tipo_usuario() != "admin" and verificacao.get("usuario") != usuario_atual():
        abort(403)

    historico = historico_verificacao_bm(verificacao_id)
    linhas = linhas_dossie_bm(verificacao, historico)
    pdf = gerar_pdf_simples("Dossie BM", linhas)
    nome = f"dossie_bm_{limpar_cnpj(verificacao.get('cnpj', ''))}.pdf"
    return send_file(io.BytesIO(pdf), download_name=nome, as_attachment=True, mimetype="application/pdf")


@app.route("/relatorio-diario-bm", methods=["GET"])
@login_obrigatorio
def relatorio_diario_bm():
    data = request.args.get("data", data_hoje())
    usuario_filtro = request.args.get("usuario", "").strip().lower()
    if tipo_usuario() != "admin":
        usuario_filtro = usuario_atual()
    resumo = resumo_relatorio_diario_central(data, usuario_filtro)
    texto_relatorio = gerar_texto_relatorio_diario_central(resumo)
    usuarios = sorted(carregar_usuarios().keys()) if tipo_usuario() == "admin" else [usuario_atual()]
    return render_template(
        "relatorio_diario_bm.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        usuarios=usuarios,
        filtros={"data": data, "usuario": usuario_filtro},
        resumo=resumo,
        texto_relatorio=texto_relatorio
    )


@app.route("/relatorio-diario-bm/txt", methods=["POST"])
@login_obrigatorio
def relatorio_diario_bm_txt():
    data = request.form.get("data", data_hoje())
    usuario_filtro = request.form.get("usuario", "").strip().lower()
    if tipo_usuario() != "admin":
        usuario_filtro = usuario_atual()
    resumo = resumo_relatorio_diario_central(data, usuario_filtro)
    texto_relatorio = gerar_texto_relatorio_diario_central(resumo)
    return send_file(io.BytesIO(texto_relatorio.encode("utf-8")), download_name=f"relatorio_diario_bm_{data}.txt", as_attachment=True, mimetype="text/plain; charset=utf-8")


@app.route("/relatorio-diario-bm/csv", methods=["POST"])
@login_obrigatorio
def relatorio_diario_bm_csv():
    data = request.form.get("data", data_hoje())
    usuario_filtro = request.form.get("usuario", "").strip().lower()
    if tipo_usuario() != "admin":
        usuario_filtro = usuario_atual()
    resumo = resumo_relatorio_diario_central(data, usuario_filtro)
    linhas = ["cnpj;razao_social;status;operador;score;risco"]
    for item in resumo.get("itens", []):
        valores = [
            item.get("cnpj_formatado", ""),
            item.get("razao_social", ""),
            item.get("status", ""),
            item.get("operador", ""),
            str(item.get("score_prontidao", "")),
            item.get("risco", "")
        ]
        linhas.append(";".join([str(v).replace(";", ",") for v in valores]))
    conteudo = "\n".join(linhas)
    return send_file(io.BytesIO(conteudo.encode("utf-8-sig")), download_name=f"relatorio_diario_bm_{data}.csv", as_attachment=True, mimetype="text/csv; charset=utf-8")


@app.route("/relatorio-diario-bm/pdf", methods=["POST"])
@login_obrigatorio
def relatorio_diario_bm_pdf():
    data = request.form.get("data", data_hoje())
    usuario_filtro = request.form.get("usuario", "").strip().lower()
    if tipo_usuario() != "admin":
        usuario_filtro = usuario_atual()
    resumo = resumo_relatorio_diario_central(data, usuario_filtro)
    linhas = gerar_texto_relatorio_diario_central(resumo).splitlines()
    pdf = gerar_pdf_simples("Relatorio Diario BM", linhas)
    return send_file(io.BytesIO(pdf), download_name=f"relatorio_diario_bm_{data}.pdf", as_attachment=True, mimetype="application/pdf")


@app.route("/radar-bm", methods=["GET"])
@login_obrigatorio
def radar_bm():
    radar = montar_radar_bm()

    return render_template(
        "radar_bm.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        radar=radar
    )


@app.route("/central-bm", methods=["GET"])
@login_obrigatorio
def central_bm():
    filtros = {
        "busca": request.args.get("busca", ""),
        "status": request.args.get("status", ""),
        "risco": request.args.get("risco", ""),
        "operador": request.args.get("operador", "")
    }

    verificacoes = listar_verificacoes_bm(filtros)
    estatisticas = estatisticas_central_bm(verificacoes)
    usuarios = sorted(carregar_usuarios().keys()) if tipo_usuario() == "admin" else [usuario_atual()]

    return render_template(
        "central_bm.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        verificacoes=verificacoes,
        estatisticas=estatisticas,
        status_central_bm=STATUS_CENTRAL_BM,
        filtros=filtros,
        usuarios=usuarios,
        alertas=gerar_alertas_gerais_bm(verificacoes, 10)
    )


@app.route("/central-bm/nova", methods=["GET", "POST"])
@login_obrigatorio
def nova_verificacao_bm():
    erro = ""
    cnpj = request.values.get("cnpj", "").strip()
    empresa = buscar_empresa_por_cnpj(cnpj) if cnpj else None
    dados = montar_dados_verificacao_form(empresa)

    if request.method == "POST":
        cnpj_post = request.form.get("cnpj", "").strip()
        empresa = buscar_empresa_por_cnpj(cnpj_post)

        if not empresa:
            erro = "CNPJ não encontrado na base do LTDAFinder. Confira o número e tente novamente."
            dados = dados_verificacao_do_form(None, None)
        else:
            dados = dados_verificacao_do_form(empresa, None)
            verificacao_id = criar_verificacao_bm(dados)
            return redirect(url_for("detalhe_verificacao_bm", verificacao_id=verificacao_id))

    return render_template(
        "nova_verificacao_bm.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        empresa=empresa,
        dados=dados,
        perfis_meta=perfis_meta_para_select(),
        status_central_bm=STATUS_CENTRAL_BM,
        checklist_central_bm=CHECKLIST_CENTRAL_BM,
        checklist_etapas=checklist_central_bm_por_etapa(),
        erro=erro
    )


@app.route("/central-bm/<int:verificacao_id>", methods=["GET", "POST"])
@login_obrigatorio
def detalhe_verificacao_bm(verificacao_id):
    verificacao = buscar_verificacao_bm(verificacao_id)

    if not verificacao:
        abort(404)

    if tipo_usuario() != "admin" and verificacao.get("usuario") != usuario_atual():
        abort(403)

    erro = ""
    sucesso = request.args.get("sucesso", "")

    if request.method == "POST":
        empresa = buscar_empresa_por_cnpj(verificacao.get("cnpj", ""))
        dados = dados_verificacao_do_form(empresa, verificacao)
        atualizar_verificacao_bm(verificacao_id, dados)
        return redirect(url_for("detalhe_verificacao_bm", verificacao_id=verificacao_id, sucesso="Dados salvos com sucesso."))

    historico = historico_verificacao_bm(verificacao_id)
    alertas = gerar_alertas_verificacao_bm(verificacao)

    return render_template(
        "bm_verificacao.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        verificacao=verificacao,
        historico=historico,
        perfis_meta=perfis_meta_para_select(),
        status_central_bm=STATUS_CENTRAL_BM,
        checklist_central_bm=CHECKLIST_CENTRAL_BM,
        checklist_etapas=checklist_central_bm_por_etapa(),
        alertas=alertas,
        erro=erro,
        sucesso=sucesso
    )


@app.route("/central-bm/<int:verificacao_id>/status", methods=["POST"])
@login_obrigatorio
def central_bm_status(verificacao_id):
    verificacao = buscar_verificacao_bm(verificacao_id)

    if not verificacao:
        abort(404)

    if tipo_usuario() != "admin" and verificacao.get("usuario") != usuario_atual():
        abort(403)

    novo_status = request.form.get("status", "Preparando").strip()

    if novo_status not in STATUS_CENTRAL_BM:
        novo_status = "Preparando"

    dados = montar_dados_verificacao_form(verificacao=verificacao)
    dados["status"] = novo_status
    dados["score_prontidao"], dados["risco"] = calcular_score_prontidao_bm(dados)
    atualizar_verificacao_bm(verificacao_id, dados)

    return redirect(request.referrer or url_for("central_bm"))


@app.route("/central-bm/<int:verificacao_id>/testar-dominio", methods=["POST"])
@login_obrigatorio
def central_bm_testar_dominio(verificacao_id):
    verificacao = buscar_verificacao_bm(verificacao_id)

    if not verificacao:
        abort(404)

    if tipo_usuario() != "admin" and verificacao.get("usuario") != usuario_atual():
        abort(403)

    resultado = testar_dominio_verificacao(verificacao)

    verificacao_atualizada = dict(verificacao)
    verificacao_atualizada["checklist_site"] = resultado.get("checklist_site", verificacao.get("checklist_site", False))
    verificacao_atualizada["checklist_meta_tag"] = resultado.get("checklist_meta_tag", verificacao.get("checklist_meta_tag", False))
    verificacao_atualizada["checklist_dominio"] = resultado.get("checklist_dominio", verificacao.get("checklist_dominio", False))
    verificacao_atualizada["checklist_https"] = resultado.get("checklist_https", verificacao.get("checklist_https", False))
    verificacao_atualizada["checklist_cnpj_site"] = resultado.get("checklist_cnpj_site", verificacao.get("checklist_cnpj_site", False))
    verificacao_atualizada["checklist_razao_site"] = resultado.get("checklist_razao_site", verificacao.get("checklist_razao_site", False))
    verificacao_atualizada["score_prontidao"], verificacao_atualizada["risco"] = calcular_score_prontidao_bm(verificacao_atualizada)

    with engine.begin() as conn:
        conn.execute(
            text("""
            UPDATE bm_verificacoes
            SET checklist_site = :checklist_site,
                checklist_meta_tag = :checklist_meta_tag,
                checklist_dominio = :checklist_dominio,
                checklist_https = :checklist_https,
                checklist_cnpj_site = :checklist_cnpj_site,
                checklist_razao_site = :checklist_razao_site,
                score_prontidao = :score_prontidao,
                risco = :risco,
                ultimo_teste_status = :ultimo_teste_status,
                ultimo_teste_mensagem = :ultimo_teste_mensagem,
                ultimo_teste_detalhes = :ultimo_teste_detalhes,
                ultimo_teste_em = CURRENT_TIMESTAMP,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = :id
            """),
            {
                "id": verificacao_id,
                "checklist_site": verificacao_atualizada["checklist_site"],
                "checklist_meta_tag": verificacao_atualizada["checklist_meta_tag"],
                "checklist_dominio": verificacao_atualizada["checklist_dominio"],
                "checklist_https": verificacao_atualizada["checklist_https"],
                "checklist_cnpj_site": verificacao_atualizada["checklist_cnpj_site"],
                "checklist_razao_site": verificacao_atualizada["checklist_razao_site"],
                "score_prontidao": verificacao_atualizada["score_prontidao"],
                "risco": verificacao_atualizada["risco"],
                "ultimo_teste_status": resultado.get("status", "erro"),
                "ultimo_teste_mensagem": resultado.get("mensagem", ""),
                "ultimo_teste_detalhes": resultado.get("detalhes", "")
            }
        )

    registrar_historico_verificacao(
        verificacao_id,
        "Teste de domínio",
        verificacao.get("status", ""),
        verificacao.get("status", ""),
        resultado.get("mensagem", ""),
        usuario_atual(),
        verificacao.get("cnpj", "")
    )

    return redirect(url_for("detalhe_verificacao_bm", verificacao_id=verificacao_id))


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

    perfis_meta = carregar_perfis_meta()
    estatisticas_meta = estatisticas_gerais_perfis_meta(perfis_meta)

    contexto = montar_contexto(df, df)
    contexto["estatisticas_gerais"] = estatisticas_meta
    contexto["historico_hoje"] = resumo_perfis_meta_do_dia(perfis=perfis_meta)
    contexto["dashboard_master"] = calcular_dashboard_master(df, estatisticas_meta, perfis_meta)
    contexto["evolucao_diaria"] = resumo_evolucao_diaria_perfis_meta(15, perfis_meta)
    contexto["top_ufs"] = calcular_top_ufs(df, 10)

    return render_template("master.html", **contexto)


@app.route("/ia-operacional")
@login_obrigatorio
def painel_ia_operacional():
    forcar = request.args.get("recalcular", "").strip() == "1"
    modelo = obter_modelo_ia_operacional(forcar=forcar)

    return render_template(
        "ia_operacional.html",
        usuario_logado=usuario_atual(),
        tipo_usuario=tipo_usuario(),
        modelo=modelo,
        status_sucesso=STATUS_SUCESSO,
        status_negativos=STATUS_NEGATIVOS,
    )


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

    empresa_dados = encontrado.iloc[0].to_dict()
    empresa_dados.update(
        avaliar_ia_empresa(
            empresa_dados,
            obter_modelo_ia_operacional(),
            detalhado=True,
        )
    )

    return render_template(
        "empresa.html",
        empresa=empresa_dados,
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

    sincronizar_status_perfis_meta(cnpj_limpo, novo_status, usuario)

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
                    agora = agora_brasilia()

                    novo_perfil = {
                        "id": gerar_id_perfil(),
                        "usuario": usuario_atual(),
                        "nome": nome,
                        "cnpj_limpo": cnpj if empresa else "",
                        "razao_social": empresa.get("razao_social", "") if empresa else "",
                        "status_bm": empresa.get("status_bm", STATUS_PADRAO) if empresa else STATUS_PADRAO,
                        "status_bm_usuario": usuario_atual(),
                        "status_bm_atualizado_em": agora.strftime("%d/%m/%Y %H:%M"),
                        "proxy": proxy,
                        "navegador": navegador,
                        "conta_facebook": conta_facebook,
                        "senha_facebook": senha_facebook,
                        "observacoes": observacoes,
                        "criado_em": agora.strftime("%d/%m/%Y %H:%M"),
                        "criado_em_iso": agora.strftime("%Y-%m-%d"),
                        "atualizado_em": agora.strftime("%d/%m/%Y %H:%M"),
                        "fuso_horario": NOME_FUSO_BRASILIA
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
                        perfil["atualizado_em"] = agora_brasilia().strftime("%d/%m/%Y %H:%M")
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
                        perfil["status_bm"] = empresa.get("status_bm", STATUS_PADRAO)
                        perfil["status_bm_usuario"] = usuario_atual()
                        perfil["status_bm_atualizado_em"] = agora_brasilia().strftime("%d/%m/%Y %H:%M")
                        perfil["atualizado_em"] = agora_brasilia().strftime("%d/%m/%Y %H:%M")
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

    agora_status_perfil = agora_brasilia().strftime("%d/%m/%Y %H:%M")
    perfil_encontrado["status_bm"] = novo_status
    perfil_encontrado["status_bm_usuario"] = usuario
    perfil_encontrado["status_bm_atualizado_em"] = agora_status_perfil
    perfil_encontrado["atualizado_em"] = agora_status_perfil
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
                    "endereco_completo": "Endereço não informado",
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

    sincronizar_status_perfis_meta(cnpj_limpo, novo_status, usuario)

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
    hoje = agora_brasilia()
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
    return render_template(
        "sites.html",
        usuario_logado=usuario_atual(), tipo_usuario=tipo_usuario(), sites=sites,
        modelos_site=MODELOS_SITE, modelo_site_nome=modelo_site_nome,
        estatisticas_sites=estatisticas_sites_gerados(),
        filtros={"modelo_site": modelo_site, "busca": busca}
    )


@app.route("/api/empresa-cnpj/<cnpj>", methods=["GET"])
@login_obrigatorio
def api_empresa_cnpj(cnpj):
    empresa = buscar_empresa_por_cnpj(cnpj)
    if not empresa:
        return jsonify({"ok": False, "erro": "CNPJ não encontrado na base."}), 404
    return jsonify({
        "ok": True,
        "empresa": {
            "cnpj": limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", cnpj))),
            "cnpj_formatado": empresa.get("cnpj_formatado") or formatar_cnpj(limpar_cnpj(cnpj)),
            "razao_social": valor_texto(empresa.get("razao_social", "")),
            "nome_fantasia": valor_texto(empresa.get("nome_fantasia", "")),
            "email": valor_texto(empresa.get("email", "")),
            "telefone": valor_texto(empresa.get("telefone_formatado", "")),
            "cnae": atividade_site_sem_codigo(empresa),
            "cnae_completo": valor_texto(empresa.get("cnae_principal", "")),
            "endereco": montar_endereco_empresa(empresa),
            "slug": sugerir_nome_worker_site(empresa),
        }
    })


@app.route("/api/subdominio-disponivel", methods=["GET"])
@login_obrigatorio
def api_subdominio_disponivel():
    slug = normalizar_nome_worker_cloudflare(request.args.get("slug", ""))
    escolha = request.args.get("dominio", "auto")
    dominio, _ = resolver_dominio_publicacao(escolha)

    if len(slug) < 3:
        return jsonify({
            "ok": False,
            "disponivel": False,
            "hostname": montar_hostname_publicacao(slug, dominio),
            "erro": "Use pelo menos 3 caracteres no nome do site."
        }), 400

    disponivel, hostname, existente = hostname_publicacao_disponivel(slug, dominio)
    return jsonify({
        "ok": True,
        "disponivel": disponivel,
        "hostname": hostname,
        "ocupado_por": existente.get("nome_exibicao") or existente.get("nome_empresarial")
                       if existente else ""
    })


@app.route("/gerador-site", defaults={"cnpj": ""}, methods=["GET", "POST"])
@app.route("/gerador-site/<cnpj>", methods=["GET", "POST"])
@login_obrigatorio
def gerador_site(cnpj):
    erro = ""
    cnpj_form = request.values.get("cnpj", cnpj).strip()
    empresa = buscar_empresa_por_cnpj(cnpj_form) if cnpj_form else None
    modelo_site = request.values.get("modelo_site", "ia_2_0").strip()
    if modelo_site not in MODELOS_SITE_DICT:
        modelo_site = "ia_2_0"

    dominios = opcoes_dominios_publicacao()
    dominio_escolhido = request.values.get("dominio_publicacao", "auto").strip() or "auto"
    dominio_auto = dominios[0]["dominio"] if dominios else ""

    if empresa:
        nome_site = request.values.get("nome_site", "").strip() or nome_exibicao_empresa(empresa)
        telefone_site = request.values.get("telefone_site", "").strip() or valor_texto(empresa.get("telefone_formatado", ""))
        email_site = request.values.get("email_site", "").strip() or valor_texto(empresa.get("email", ""))
        endereco_site = request.values.get("endereco_site", "").strip() or montar_endereco_empresa(empresa)
        cloudflare_slug = normalizar_nome_worker_cloudflare(
            request.values.get("cloudflare_slug", "")
        ) or sugerir_nome_worker_site({**empresa, "nome_site": nome_site})
    else:
        nome_site = request.values.get("nome_site", "").strip()
        telefone_site = request.values.get("telefone_site", "").strip()
        email_site = request.values.get("email_site", "").strip()
        endereco_site = request.values.get("endereco_site", "").strip()
        cloudflare_slug = normalizar_nome_worker_cloudflare(request.values.get("cloudflare_slug", ""))

    dominio_previsto, _ = resolver_dominio_publicacao(dominio_escolhido)
    hostname_previsto = montar_hostname_publicacao(cloudflare_slug, dominio_previsto)

    if request.method == "POST":
        if not empresa:
            erro = "CNPJ não encontrado na base do LTDAFinder."
        elif len(cloudflare_slug) < 3:
            erro = "O nome do site/subdomínio precisa ter pelo menos 3 caracteres."
        else:
            disponivel, hostname_previsto, existente = hostname_publicacao_disponivel(
                cloudflare_slug,
                dominio_previsto
            )

            if not disponivel:
                nome_existente = existente.get("nome_exibicao") or existente.get("nome_empresarial") or "outro site"
                erro = (
                    f"O endereço {hostname_previsto} já está vinculado a {nome_existente}. "
                    "Escolha outro nome para o site."
                )
            else:
                empresa_site = aplicar_personalizacao_site(
                    empresa, nome_site, telefone_site, email_site, endereco_site, telefone_site
                )
                bundle = gerar_bundle_site_empresa(empresa_site, "", modelo_site, "")
                cnpj_limpo = limpar_cnpj(empresa.get("cnpj_limpo", empresa.get("cnpj", cnpj_form)))
                site_id = salvar_site_gerado({
                    "usuario": usuario_atual(), "cnpj": cnpj_limpo,
                    "cnpj_formatado": formatar_cnpj(cnpj_limpo),
                    "nome_empresarial": valor_texto(empresa.get("razao_social", "")),
                    "nome_fantasia": valor_texto(empresa.get("nome_fantasia", "")),
                    "cnae_principal": valor_texto(empresa.get("cnae_principal", "")),
                    "categoria_cnae": valor_texto(empresa.get("categoria_cnae", "")),
                    "endereco": endereco_site, "telefone": telefone_site, "email": email_site,
                    "nome_exibicao": nome_site, "telefone_exibicao": telefone_site,
                    "whatsapp_exibicao": telefone_site, "email_exibicao": email_site,
                    "endereco_exibicao": endereco_site,
                    "cloudflare_slug_personalizado": cloudflare_slug,
                    "cloudflare_dominio_base": dominio_previsto,
                    "cloudflare_hostname": hostname_previsto,
                    "cloudflare_status": "Não publicado", "meta_tag": "",
                    "modelo_site": modelo_site, "nome_arquivo": gerar_nome_arquivo_site(empresa_site),
                    "html_gerado": bundle["html"], "politica_html": bundle["politica"],
                    "termos_html": bundle["termos"], "arquivos_verificacao": "{}",
                    "status": "Gerado", "observacoes": "",
                    "versao_template": "3.0" if modelo_site == "ia_3_0" else "2.1"
                })
                site = buscar_site_gerado(site_id)
                try:
                    resultado = publicar_site_na_cloudflare(site, cloudflare_slug, dominio_previsto)
                    atualizar_publicacao_cloudflare_site(
                        site_id, "Publicado", resultado["worker_name"], resultado["cloudflare_url"],
                        resultado.get("aviso", ""), cloudflare_slug,
                        resultado.get("dominio_base", dominio_previsto), resultado.get("hostname", "")
                    )
                    registrar_evento(
                        usuario_atual(), formatar_cnpj(cnpj_limpo), "-",
                        f"Site publicado: {resultado['cloudflare_url']}"
                    )
                    return redirect(url_for(
                        "site_gerado_preview", site_id=site_id,
                        cloudflare_msg="Site publicado com sucesso."
                    ))
                except Exception as exc:
                    atualizar_publicacao_cloudflare_site(
                        site_id, "Erro", "", "", str(exc), cloudflare_slug,
                        dominio_previsto, hostname_previsto
                    )
                    return redirect(url_for(
                        "site_gerado_preview", site_id=site_id,
                        cloudflare_erro=str(exc)
                    ))

    return render_template(
        "gerador_site.html", usuario_logado=usuario_atual(), tipo_usuario=tipo_usuario(),
        empresa=empresa, modelos_site=MODELOS_SITE, modelo_site=modelo_site,
        nome_site=nome_site, telefone_site=telefone_site, email_site=email_site,
        endereco_site=endereco_site, cloudflare_slug=cloudflare_slug,
        dominios_publicacao=dominios, dominio_escolhido=dominio_escolhido,
        dominio_auto=dominio_auto, hostname_previsto=hostname_previsto,
        atividade_site=atividade_site_sem_codigo(empresa) if empresa else "",
        erro=erro
    )


@app.route("/site-gerado/<int:site_id>", methods=["GET"])
@login_obrigatorio
def site_gerado_preview(site_id):
    site = buscar_site_gerado(site_id)
    if not site:
        abort(404)
    aba = request.args.get("aba", "informacoes")
    if aba not in {"informacoes", "editar", "verificacao"}:
        aba = "informacoes"
    return render_template(
        "site_preview.html", usuario_logado=usuario_atual(), tipo_usuario=tipo_usuario(),
        site=site, empresa=buscar_empresa_por_cnpj(site.get("cnpj", "")) or {},
        modelo_site_nome=modelo_site_nome, aba=aba,
        metodo=request.args.get("metodo", "meta"),
        cloudflare_msg=request.args.get("cloudflare_msg", ""),
        cloudflare_erro=request.args.get("cloudflare_erro", "")
    )


@app.route("/site-gerado/<int:site_id>/editar", methods=["POST"])
@login_obrigatorio
def site_gerado_editar(site_id):
    site = buscar_site_gerado(site_id)
    if not site:
        abort(404)
    nome = request.form.get("nome_exibicao", "").strip() or site.get("nome_exibicao")
    telefone = request.form.get("telefone_exibicao", "").strip()
    email = request.form.get("email_exibicao", "").strip()
    empresa = buscar_empresa_por_cnpj(site.get("cnpj", "")) or {}
    endereco = site.get("endereco_exibicao") or site.get("endereco") or montar_endereco_empresa(empresa)
    empresa_site = aplicar_personalizacao_site(empresa, nome, telefone, email, endereco, telefone)
    bundle = gerar_bundle_site_empresa(empresa_site, site.get("meta_tag", ""), site.get("modelo_site", "ia_2_0"), site.get("observacoes", ""))
    atualizar_site_conteudo(site_id, {
        "nome_exibicao": nome, "nome_fantasia": nome,
        "telefone_exibicao": telefone, "telefone": telefone, "whatsapp_exibicao": telefone,
        "email_exibicao": email, "email": email,
        "html_gerado": bundle["html"], "politica_html": bundle["politica"], "termos_html": bundle["termos"]
    })
    try:
        if site.get("cloudflare_status") == "Publicado":
            republicar_site_se_publicado(site_id)
            mensagem = "Alterações salvas e site republicado."
        else:
            mensagem = "Alterações salvas."
    except Exception as exc:
        mensagem = f"Alterações salvas, mas a republicação falhou: {exc}"
    return redirect(url_for("site_gerado_preview", site_id=site_id, aba="editar", cloudflare_msg=mensagem))


@app.route("/site-gerado/<int:site_id>/verificacao", methods=["POST"])
@login_obrigatorio
def site_gerado_verificacao(site_id):
    site = buscar_site_gerado(site_id)
    if not site:
        abort(404)
    metodo = request.form.get("metodo", "meta")
    try:
        if metodo == "meta":
            meta_tag = normalizar_meta_tag_site(request.form.get("meta_tag", ""))
            if not meta_tag:
                raise RuntimeError("Informe o código da Meta Tag.")
            empresa_site = empresa_site_a_partir_registro(site)
            bundle = gerar_bundle_site_empresa(empresa_site, meta_tag, site.get("modelo_site", "ia_2_0"), site.get("observacoes", ""))
            atualizar_site_conteudo(site_id, {"meta_tag": meta_tag, "html_gerado": bundle["html"], "politica_html": bundle["politica"], "termos_html": bundle["termos"]})
            republicar_site_se_publicado(site_id)
            mensagem = "Meta Tag aplicada e site atualizado."
        elif metodo == "dns":
            nome = request.form.get("dns_txt_nome", "").strip()
            valor = request.form.get("dns_txt_valor", "").strip()
            if not valor:
                raise RuntimeError("Informe o valor do registro TXT.")
            nome_final = criar_dns_txt_cloudflare(site, nome, valor)
            atualizar_site_conteudo(site_id, {"dns_txt_nome": nome_final, "dns_txt_valor": valor})
            mensagem = "Registro DNS TXT criado com sucesso."
        elif metodo == "arquivo":
            nome = request.form.get("arquivo_nome", "").strip()
            conteudo = request.form.get("arquivo_conteudo", "").strip()
            if not nome or not conteudo:
                raise RuntimeError("Informe o nome e o conteúdo do arquivo HTML.")
            if not nome.lower().endswith(".html"):
                nome += ".html"
            arquivos = carregar_arquivos_verificacao_site(site)
            arquivos[nome] = conteudo
            atualizar_site_conteudo(site_id, {"arquivos_verificacao": json.dumps(arquivos, ensure_ascii=False)})
            republicar_site_se_publicado(site_id)
            mensagem = f"Arquivo {nome} publicado na raiz do site."
        else:
            raise RuntimeError("Método de verificação inválido.")
        return redirect(url_for("site_gerado_preview", site_id=site_id, aba="verificacao", metodo=metodo, cloudflare_msg=mensagem))
    except Exception as exc:
        return redirect(url_for("site_gerado_preview", site_id=site_id, aba="verificacao", metodo=metodo, cloudflare_erro=str(exc)))


@app.route("/site-gerado/<int:site_id>/criar-verificacao-bm", methods=["POST"])
@login_obrigatorio
def site_gerado_criar_verificacao_bm(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    existente = buscar_verificacao_por_site(site_id)
    if existente:
        return redirect(url_for("detalhe_verificacao_bm", verificacao_id=existente["id"], sucesso="Este site já estava vinculado a esta verificação BM."))

    dados = montar_dados_verificacao_por_site(site)
    verificacao_id = criar_verificacao_bm(dados)

    registrar_historico_verificacao(
        verificacao_id,
        "Site vinculado",
        "",
        dados.get("status", "Preparando"),
        f"Verificação criada a partir do site gerado #{site_id}: {site.get('nome_arquivo', '')}",
        usuario_atual(),
        dados.get("cnpj", "")
    )

    try:
        registrar_evento(usuario_atual(), site.get("cnpj_formatado", site.get("cnpj", "")), "Site gerado", f"Verificação BM criada a partir do site #{site_id}")
    except Exception:
        pass

    return redirect(url_for("detalhe_verificacao_bm", verificacao_id=verificacao_id, sucesso="Verificação BM criada com os dados do site gerado."))

@app.route("/site-gerado/<int:site_id>/publicar-cloudflare", methods=["POST"])
@login_obrigatorio
def site_gerado_publicar_cloudflare(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    slug_personalizado = normalizar_nome_worker_cloudflare(request.form.get("cloudflare_slug", ""))

    if not slug_personalizado:
        slug_personalizado = normalizar_nome_worker_cloudflare(site.get("cloudflare_slug_personalizado", ""))

    worker_name = gerar_nome_worker_site(site, slug_personalizado) if slug_personalizado else (site.get("cloudflare_worker_name", "") or gerar_nome_worker_site(site))
    cloudflare_url = "" if slug_personalizado else site.get("cloudflare_url", "")

    try:
        resultado = publicar_site_na_cloudflare(site, slug_personalizado, request.form.get("dominio_publicacao", "") or site.get("cloudflare_dominio_base", ""))
        worker_name = resultado["worker_name"]
        cloudflare_url = resultado["cloudflare_url"]
        aviso = resultado.get("aviso", "")

        atualizar_publicacao_cloudflare_site(
            site_id,
            "Publicado",
            worker_name,
            cloudflare_url,
            aviso,
            slug_personalizado,
            resultado.get("dominio_base", ""),
            resultado.get("hostname", "")
        )

        atualizar_verificacoes_do_site_publicado(site_id, cloudflare_url, slug_personalizado)

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
            mensagem,
            slug_personalizado,
            site.get("cloudflare_dominio_base", ""),
            site.get("cloudflare_hostname", "")
        )

        return redirect(url_for("site_gerado_preview", site_id=site_id, cloudflare_erro=mensagem))


@app.route("/site-gerado/<int:site_id>/download", methods=["GET"])
@login_obrigatorio
def site_gerado_download(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    html = site.get("html_gerado", "")

    if not html:
        abort(404)

    output = io.BytesIO(html.encode("utf-8"))
    output.seek(0)

    return send_file(
        output,
        download_name="index.html",
        as_attachment=True,
        mimetype="text/html"
    )



@app.route("/site-gerado/<int:site_id>/worker-download", methods=["GET"])
@login_obrigatorio
def site_gerado_worker_download(site_id):
    site = buscar_site_gerado(site_id)

    if not site:
        abort(404)

    worker_js = gerar_worker_js_site_registro(site)
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
    worker_js = gerar_worker_js_site_registro(site)
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
        "cnpj_formatado", "razao_social", "endereco_completo", "capital_formatado", "uf",
        "municipio_nome", "telefone_formatado", "email", "nome_socio",
        "sexo_provavel", "categoria_cnae", "status_bm", "score_ia",
        "score_ia_regras", "score_ia_historico", "ia_peso_regras",
        "ia_peso_historico", "ia_confianca_historica", "ia_taxa_estimada",
        "ia_amostras_semelhantes", "ia_resultado_mais_comum", "ia_recomendacao",
        "usado_por", "data_uso_bm", "data_inicio_formatada", "cnae_principal"
    ]

    df = df[[col for col in colunas if col in df.columns]].copy()

    df.rename(columns={
        "cnpj_formatado": "CNPJ",
        "razao_social": "Razão Social",
        "endereco_completo": "Endereço Completo",
        "capital_formatado": "Capital Social",
        "uf": "UF",
        "municipio_nome": "Município",
        "telefone_formatado": "Telefone",
        "email": "Email",
        "nome_socio": "Sócio",
        "sexo_provavel": "Sexo pela Razão Social",
        "categoria_cnae": "Categoria CNAE",
        "status_bm": "Meu Status BM",
        "score_ia": "Score IA Final",
        "score_ia_regras": "Score IA Regras",
        "score_ia_historico": "Score IA Histórico",
        "ia_peso_regras": "Peso Regras (%)",
        "ia_peso_historico": "Peso Histórico (%)",
        "ia_confianca_historica": "Confiança Histórica (%)",
        "ia_taxa_estimada": "Taxa Histórica Estimada (%)",
        "ia_amostras_semelhantes": "Amostras Semelhantes",
        "ia_resultado_mais_comum": "Resultado Histórico Mais Comum",
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
