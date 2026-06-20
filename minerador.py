import os
import re
import zipfile
import requests
import pandas as pd
from pathlib import Path

BASE_URL = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/2026-05-10"

PASTA_BASE = Path("C:/Users/Dutra/Desktop/minerador-cnpj")
PASTA_DOWNLOADS = PASTA_BASE / "downloads"
PASTA_RESULTADOS = PASTA_BASE / "resultados"

PASTA_DOWNLOADS.mkdir(parents=True, exist_ok=True)
PASTA_RESULTADOS.mkdir(parents=True, exist_ok=True)

CAPITAL_MINIMO = 1000000

PALAVRAS_EMPRESA = [
    "COMERCIO", "COMÉRCIO", "SERVICOS", "SERVIÇOS", "SERVICO", "SERVIÇO",
    "HOLDING", "PARTICIPACOES", "PARTICIPAÇÕES", "ADMINISTRADORA",
    "CONSTRUTORA", "DISTRIBUIDORA", "TRANSPORTES", "INDUSTRIA", "INDÚSTRIA",
    "TECNOLOGIA", "CONSULTORIA", "EMPREENDIMENTOS", "LOCACOES", "LOCAÇÕES",
    "INVESTIMENTOS", "ALIMENTOS", "MERCADO", "RESTAURANTE", "LANCHONETE",
    "IMOVEIS", "IMÓVEIS", "AGRO", "FAZENDA", "CLINICA", "CLÍNICA"
]

def baixar_arquivo(nome):
    destino = PASTA_DOWNLOADS / nome

    if destino.exists():
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

def parece_nome_pessoa(razao):
    if not razao:
        return False

    nome = str(razao).upper()
    nome = nome.replace(" LTDA", "")
    nome = nome.replace(" LIMITADA", "")
    nome = re.sub(r"[^A-ZÀ-Ú\s]", " ", nome)
    nome = re.sub(r"\s+", " ", nome).strip()

    palavras = nome.split()

    if len(palavras) < 2 or len(palavras) > 5:
        return False

    for termo in PALAVRAS_EMPRESA:
        if termo in nome:
            return False

    return True

COLUNAS_EMPRESAS = [
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte",
    "ente_federativo"
]

COLUNAS_ESTABELECIMENTOS = [
    "cnpj_basico",
    "cnpj_ordem",
    "cnpj_dv",
    "matriz_filial",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao",
    "motivo_situacao",
    "cidade_exterior",
    "pais",
    "data_inicio",
    "cnae_principal",
    "cnae_secundaria",
    "tipo_logradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "ddd1",
    "telefone1",
    "ddd2",
    "telefone2",
    "ddd_fax",
    "fax",
    "email",
    "situacao_especial",
    "data_situacao_especial"
]

def iniciar():
    print("Iniciando mineração...")

    empresas_filtradas = []

    for i in range(10):
        nome_zip = f"Empresas{i}.zip"
        caminho = baixar_arquivo(nome_zip)

        print(f"Processando {nome_zip}")

        df = ler_zip_csv(caminho, COLUNAS_EMPRESAS)

        df["capital_social_num"] = (
            df["capital_social"]
            .str.replace(",", ".", regex=False)
            .astype(float)
        )

        df = df[
            (df["natureza_juridica"] == "2062") &
            (df["capital_social_num"] >= CAPITAL_MINIMO) &
            (df["razao_social"].str.upper().str.contains("LTDA", na=False))
        ].copy()

        df = df[df["razao_social"].apply(parece_nome_pessoa)]

        empresas_filtradas.append(df)

        print(f"Encontradas nessa parte: {len(df)}")

    empresas = pd.concat(empresas_filtradas, ignore_index=True)

    print(f"Total pré-filtrado: {len(empresas)}")

    if empresas.empty:
        print("Nenhuma empresa encontrada.")
        return

    resultados = []

    for i in range(10):
        nome_zip = f"Estabelecimentos{i}.zip"
        caminho = baixar_arquivo(nome_zip)

        print(f"Processando {nome_zip}")

        est = ler_zip_csv(caminho, COLUNAS_ESTABELECIMENTOS)

        est = est[
            (est["situacao_cadastral"] == "02") &
            (est["matriz_filial"] == "1")
        ].copy()

        est["cnpj"] = est["cnpj_basico"] + est["cnpj_ordem"] + est["cnpj_dv"]

        cruzado = est.merge(empresas, on="cnpj_basico", how="inner")

        if not cruzado.empty:
            resultados.append(cruzado)
            print(f"Encontradas ativas nessa parte: {len(cruzado)}")

    if not resultados:
        print("Nenhum resultado final encontrado.")
        return

    final = pd.concat(resultados, ignore_index=True)

    final = final[[
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
        "email"
    ]]

    caminho_csv = PASTA_RESULTADOS / "cnpjs_ltda_nome_pessoa_capital_1_milhao.csv"
    caminho_excel = PASTA_RESULTADOS / "cnpjs_ltda_nome_pessoa_capital_1_milhao.xlsx"

    final.to_csv(caminho_csv, index=False, sep=";", encoding="utf-8-sig")
    final.to_excel(caminho_excel, index=False)

    print("Finalizado.")
    print(f"Total encontrado: {len(final)}")
    print(f"CSV salvo em: {caminho_csv}")
    print(f"Excel salvo em: {caminho_excel}")

if __name__ == "__main__":
    iniciar()