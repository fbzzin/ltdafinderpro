import zipfile
import requests
import pandas as pd
from pathlib import Path

BASE_URL = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/2026-05-10"

PASTA_BASE = Path("C:/Users/Dutra/Desktop/minerador-cnpj")
PASTA_DOWNLOADS = PASTA_BASE / "downloads"
PASTA_RESULTADOS = PASTA_BASE / "resultados"

ARQUIVO_EMPRESAS_FILTRADAS = PASTA_RESULTADOS / "cnpjs_ltda_nome_pessoa_capital_1_milhao.csv"

ARQUIVO_SAIDA_CSV = PASTA_RESULTADOS / "cnpjs_ltda_com_socios.csv"
ARQUIVO_SAIDA_EXCEL = PASTA_RESULTADOS / "cnpjs_ltda_com_socios.xlsx"

COLUNAS_SOCIOS = [
    "cnpj_basico",
    "identificador_socio",
    "nome_socio",
    "cpf_cnpj_socio",
    "qualificacao_socio",
    "data_entrada_sociedade",
    "pais",
    "representante_legal",
    "nome_representante",
    "qualificacao_representante",
    "faixa_etaria"
]

def baixar_arquivo(nome):
    destino = PASTA_DOWNLOADS / nome

    if destino.exists() and destino.stat().st_size > 0:
        print(f"Arquivo já existe: {nome}")
        return destino

    url = f"{BASE_URL}/{nome}"
    print(f"Baixando: {nome}")

    resposta = requests.get(url, stream=True)
    resposta.raise_for_status()

    with open(destino, "wb") as arquivo:
        for parte in resposta.iter_content(chunk_size=1024 * 1024):
            if parte:
                arquivo.write(parte)

    return destino

def ler_zip_csv(caminho_zip, colunas):
    with zipfile.ZipFile(caminho_zip, "r") as zip_ref:
        nome_csv = zip_ref.namelist()[0]
        with zip_ref.open(nome_csv) as arquivo:
            return pd.read_csv(
                arquivo,
                sep=";",
                header=None,
                names=colunas,
                dtype=str,
                encoding="latin1"
            )

def identificar_tipo_socio(valor):
    if valor == "1":
        return "Pessoa Jurídica"
    if valor == "2":
        return "Pessoa Física"
    if valor == "3":
        return "Estrangeiro"
    return "Não informado"

def iniciar():
    print("Lendo empresas filtradas...")

    if not ARQUIVO_EMPRESAS_FILTRADAS.exists():
        print("Arquivo base não encontrado.")
        print(f"Esperado em: {ARQUIVO_EMPRESAS_FILTRADAS}")
        return

    empresas = pd.read_csv(
        ARQUIVO_EMPRESAS_FILTRADAS,
        sep=";",
        dtype=str,
        encoding="utf-8-sig"
    )

    empresas["cnpj_basico"] = empresas["cnpj"].str[:8]

    cnpjs_basicos = set(empresas["cnpj_basico"].dropna().unique())

    print(f"Empresas carregadas: {len(empresas)}")
    print(f"CNPJs básicos únicos: {len(cnpjs_basicos)}")

    socios_encontrados = []

    for i in range(10):
        nome_zip = f"Socios{i}.zip"
        caminho = baixar_arquivo(nome_zip)

        print(f"Processando {nome_zip}")

        socios = ler_zip_csv(caminho, COLUNAS_SOCIOS)

        socios = socios[socios["cnpj_basico"].isin(cnpjs_basicos)].copy()

        if not socios.empty:
            socios["tipo_socio"] = socios["identificador_socio"].apply(identificar_tipo_socio)
            socios_encontrados.append(socios)
            print(f"Sócios encontrados nessa parte: {len(socios)}")
        else:
            print("Nenhum sócio encontrado nessa parte.")

    if not socios_encontrados:
        print("Nenhum sócio encontrado.")
        return

    socios_final = pd.concat(socios_encontrados, ignore_index=True)

    qtd_socios = (
        socios_final
        .groupby("cnpj_basico")
        .size()
        .reset_index(name="quantidade_socios")
    )

    socios_agrupados = (
        socios_final
        .groupby("cnpj_basico")
        .agg({
            "nome_socio": lambda x: " | ".join(x.dropna().astype(str).unique()),
            "cpf_cnpj_socio": lambda x: " | ".join(x.dropna().astype(str).unique()),
            "tipo_socio": lambda x: " | ".join(x.dropna().astype(str).unique()),
            "qualificacao_socio": lambda x: " | ".join(x.dropna().astype(str).unique()),
            "data_entrada_sociedade": lambda x: " | ".join(x.dropna().astype(str).unique()),
            "faixa_etaria": lambda x: " | ".join(x.dropna().astype(str).unique())
        })
        .reset_index()
    )

    final = empresas.merge(qtd_socios, on="cnpj_basico", how="left")
    final = final.merge(socios_agrupados, on="cnpj_basico", how="left")

    final["quantidade_socios"] = final["quantidade_socios"].fillna(0).astype(int)

    colunas_saida = [
        "cnpj",
        "razao_social",
        "capital_social",
        "natureza_juridica",
        "nome_fantasia",
        "situacao_cadastral",
        "data_inicio",
        "cnae_principal",
        "uf",
        "municipio",
        "ddd1",
        "telefone1",
        "email",
        "quantidade_socios",
        "nome_socio",
        "cpf_cnpj_socio",
        "tipo_socio",
        "qualificacao_socio",
        "data_entrada_sociedade",
        "faixa_etaria"
    ]

    final = final[colunas_saida]

    final.to_csv(
        ARQUIVO_SAIDA_CSV,
        index=False,
        sep=";",
        encoding="utf-8-sig"
    )

    final.to_excel(
        ARQUIVO_SAIDA_EXCEL,
        index=False
    )

    print("Finalizado.")
    print(f"Total de empresas no arquivo final: {len(final)}")
    print(f"CSV salvo em: {ARQUIVO_SAIDA_CSV}")
    print(f"Excel salvo em: {ARQUIVO_SAIDA_EXCEL}")

if __name__ == "__main__":
    iniciar()