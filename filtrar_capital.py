import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf.xlsx"

FAIXAS = [
    5_000_000,
    10_000_000,
    50_000_000,
    100_000_000
]

print("Lendo arquivo base...")

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

for capital in FAIXAS:
    filtrado = df[df["capital_social_num"] >= capital].copy()

    nome_arquivo = f"cnpjs_socio_unico_pf_capital_{capital}.xlsx"
    caminho_saida = PASTA_RESULTADOS / nome_arquivo

    filtrado = filtrado.sort_values(
        by="capital_social_num",
        ascending=False
    )

    filtrado.drop(columns=["capital_social_num"], inplace=True)

    filtrado.to_excel(caminho_saida, index=False)

    print(f"Capital acima de R$ {capital:,.0f}: {len(filtrado)} empresas")
    print(f"Arquivo salvo: {caminho_saida}")

print()
print("Finalizado.")