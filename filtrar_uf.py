import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf.xlsx"

UFS = [
    "SP",
    "RJ",
    "MG",
    "SC",
    "PR",
    "RS",
    "ES",
    "GO",
    "BA",
    "PE"
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

for uf in UFS:
    filtrado = df[df["uf"] == uf].copy()

    filtrado = filtrado.sort_values(
        by="capital_social_num",
        ascending=False
    )

    filtrado.drop(columns=["capital_social_num"], inplace=True)

    caminho_saida = PASTA_RESULTADOS / f"cnpjs_socio_unico_pf_{uf}.xlsx"

    filtrado.to_excel(caminho_saida, index=False)

    print(f"{uf}: {len(filtrado)} empresas")
    print(f"Arquivo salvo: {caminho_saida}")

print()
print("Finalizado.")