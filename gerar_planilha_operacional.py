import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVOS_ENTRADA = [
    "top_brasil_socio_unico_pf_5M.xlsx",
    "top_brasil_socio_unico_pf_10M.xlsx",
    "top_brasil_socio_unico_pf_50M.xlsx",
    "top_brasil_socio_unico_pf_100M.xlsx"
]

COLUNAS_FINAIS = [
    "cnpj",
    "razao_social",
    "capital_social",
    "uf",
    "municipio",
    "ddd1",
    "telefone1",
    "email",
    "nome_socio",
    "cpf_cnpj_socio",
    "faixa_etaria",
    "data_inicio",
    "cnae_principal"
]

for nome_arquivo in ARQUIVOS_ENTRADA:
    caminho = PASTA_RESULTADOS / nome_arquivo

    print(f"Lendo {nome_arquivo}...")

    df = pd.read_excel(caminho, dtype=str)

    colunas_existentes = [col for col in COLUNAS_FINAIS if col in df.columns]

    df = df[colunas_existentes].copy()

    df["telefone_formatado"] = (
        df["ddd1"].fillna("").astype(str).str.replace(".0", "", regex=False)
        + df["telefone1"].fillna("").astype(str).str.replace(".0", "", regex=False)
    )

    df["telefone_formatado"] = df["telefone_formatado"].str.strip()

    colunas_ordenadas = [
        "cnpj",
        "razao_social",
        "capital_social",
        "uf",
        "municipio",
        "telefone_formatado",
        "email",
        "nome_socio",
        "cpf_cnpj_socio",
        "faixa_etaria",
        "data_inicio",
        "cnae_principal"
    ]

    df = df[[col for col in colunas_ordenadas if col in df.columns]]

    nome_saida = nome_arquivo.replace(".xlsx", "_OPERACIONAL.xlsx")
    caminho_saida = PASTA_RESULTADOS / nome_saida

    df.to_excel(caminho_saida, index=False)

    print(f"Salvo: {caminho_saida}")
    print(f"Total: {len(df)} empresas")
    print()

print("Finalizado.")