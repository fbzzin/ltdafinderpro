import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import text
from database import engine

try:
    FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
except Exception:
    # Fallback para ambientes Windows sem o pacote tzdata instalado.
    FUSO_BRASILIA = timezone(timedelta(hours=-3), name="America/Sao_Paulo")

FUSO_UTC = timezone.utc
NOME_FUSO_BRASILIA = "America/Sao_Paulo"


def agora_brasilia():
    return datetime.now(FUSO_BRASILIA)


def converter_data_legada(valor):
    texto_data = str(valor or "").strip()

    if not texto_data:
        return texto_data

    for formato in ["%d/%m/%Y %H:%M", "%d/%m/%Y"]:
        try:
            data = datetime.strptime(texto_data, formato).replace(tzinfo=FUSO_UTC)
            formato_saida = "%d/%m/%Y %H:%M" if "%H" in formato else "%d/%m/%Y"
            return data.astimezone(FUSO_BRASILIA).strftime(formato_saida)
        except Exception:
            pass

    return texto_data


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

        if not isinstance(dados, list):
            return []

        alterou = False

        for item in dados:
            if not isinstance(item, dict) or item.get("fuso_horario") == NOME_FUSO_BRASILIA:
                continue

            item["data"] = converter_data_legada(item.get("data", ""))
            item["fuso_horario"] = NOME_FUSO_BRASILIA
            alterou = True

        if alterou:
            salvar_historico(dados)

        return dados

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
            "data": agora_brasilia().strftime("%d/%m/%Y %H:%M"),
            "usuario": usuario,
            "cnpj": cnpj,
            "status_antigo": status_antigo,
            "status_novo": status_novo,
            "fuso_horario": NOME_FUSO_BRASILIA
        }
    )

    salvar_historico(historico)
