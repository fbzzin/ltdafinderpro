import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf_com_cnae.xlsx"

DATAS_CORTE = {
    "2020": "20200101",
    "2022": "20220101",
    "2024": "20240101",
    "2025": "20250101"
}

print("Lendo base...")

df = pd.read_excel(ARQUIVO_ENTRADA, dtype=str)

df["data_inicio_limpa"] = (
    df["data_inicio"]
    .astype(str)
    .str.replace(".0", "", regex=False)
    .str.zfill(8)
)

for ano, data_corte in DATAS_CORTE.items():
    filtrado = df[df["data_inicio_limpa"] >= data_corte].copy()

    caminho_saida = (
        PASTA_RESULTADOS /
        f"cnpjs_ltda_socio_unico_pf_abertas_apos_{ano}.xlsx"
    )

    filtrado.drop(columns=["data_inicio_limpa"], inplace=True)

    filtrado.to_excel(caminho_saida, index=False)

    print(f"Abertas após {ano}: {len(filtrado)} empresas")
    print(f"Arquivo salvo: {caminho_saida}")

df.drop(columns=["data_inicio_limpa"], inplace=True)

ARQUIVO_GERAL = PASTA_RESULTADOS / "base_final_minerador_cnpj.xlsx"
df.to_excel(ARQUIVO_GERAL, index=False)

print()
print("Base final salva em:")
print(ARQUIVO_GERAL)
print()
print("Finalizado.")