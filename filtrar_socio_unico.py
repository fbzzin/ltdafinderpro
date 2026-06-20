import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = PASTA_RESULTADOS / "cnpjs_ltda_com_socios.xlsx"

ARQUIVO_SAIDA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf.xlsx"

print("Lendo arquivo...")

df = pd.read_excel(ARQUIVO_ENTRADA, dtype=str)

df["quantidade_socios"] = pd.to_numeric(
    df["quantidade_socios"],
    errors="coerce"
).fillna(0)

print("Filtrando sócio único...")

df = df[
    (df["quantidade_socios"] == 1)
]

print("Filtrando Pessoa Física...")

df = df[
    df["tipo_socio"].str.contains(
        "Pessoa Física",
        na=False
    )
]

print("Ordenando por capital social...")

df["capital_social"] = (
    df["capital_social"]
    .str.replace(",", ".", regex=False)
)

df["capital_social_num"] = pd.to_numeric(
    df["capital_social"],
    errors="coerce"
)

df = df.sort_values(
    by="capital_social_num",
    ascending=False
)

df.drop(
    columns=["capital_social_num"],
    inplace=True
)

df.to_excel(
    ARQUIVO_SAIDA,
    index=False
)

print()
print("Finalizado.")
print(f"Empresas encontradas: {len(df)}")
print(f"Arquivo salvo em:")
print(ARQUIVO_SAIDA)