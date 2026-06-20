import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf.xlsx"

UFS = [
    "SP", "RJ", "MG", "SC", "PR",
    "RS", "ES", "GO", "BA", "PE"
]

CAPITAIS = [
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

for uf in UFS:
    for capital in CAPITAIS:
        filtrado = df[
            (df["uf"] == uf) &
            (df["capital_social_num"] >= capital)
        ].copy()

        filtrado = filtrado.sort_values(
            by="capital_social_num",
            ascending=False
        )

        filtrado.drop(columns=["capital_social_num"], inplace=True)

        capital_nome = str(int(capital / 1_000_000)) + "M"
        nome_arquivo = f"cnpjs_{uf}_socio_unico_pf_capital_{capital_nome}.xlsx"

        caminho_saida = PASTA_RESULTADOS / nome_arquivo

        filtrado.to_excel(caminho_saida, index=False)

        print(f"{uf} capital acima de R$ {capital:,.0f}: {len(filtrado)} empresas")
        print(f"Arquivo salvo: {caminho_saida}")

print()
print("Finalizado.")