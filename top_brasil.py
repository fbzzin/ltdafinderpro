import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf.xlsx"

ARQUIVO_SAIDA_GERAL = PASTA_RESULTADOS / "top_brasil_socio_unico_pf.xlsx"
ARQUIVO_SAIDA_5M = PASTA_RESULTADOS / "top_brasil_socio_unico_pf_5M.xlsx"
ARQUIVO_SAIDA_10M = PASTA_RESULTADOS / "top_brasil_socio_unico_pf_10M.xlsx"
ARQUIVO_SAIDA_50M = PASTA_RESULTADOS / "top_brasil_socio_unico_pf_50M.xlsx"
ARQUIVO_SAIDA_100M = PASTA_RESULTADOS / "top_brasil_socio_unico_pf_100M.xlsx"

print("Lendo base...")

df = pd.read_excel(ARQUIVO_ENTRADA, dtype=str)

df["capital_social_num"] = (
    df["capital_social"]
    .astype(str)
    .str.replace(",", ".", regex=False)
)

df["capital_social_num"] = pd.to_numeric(
    df["capital_social_num"],
    errors="coerce"
).fillna(0)

df = df.sort_values(
    by="capital_social_num",
    ascending=False
)

def salvar(nome_arquivo, base):
    saida = base.copy()
    saida.drop(columns=["capital_social_num"], inplace=True)
    saida.to_excel(nome_arquivo, index=False)
    print(f"{len(saida)} empresas salvas em: {nome_arquivo}")

salvar(ARQUIVO_SAIDA_GERAL, df)
salvar(ARQUIVO_SAIDA_5M, df[df["capital_social_num"] >= 5_000_000])
salvar(ARQUIVO_SAIDA_10M, df[df["capital_social_num"] >= 10_000_000])
salvar(ARQUIVO_SAIDA_50M, df[df["capital_social_num"] >= 50_000_000])
salvar(ARQUIVO_SAIDA_100M, df[df["capital_social_num"] >= 100_000_000])

print()
print("Finalizado.")