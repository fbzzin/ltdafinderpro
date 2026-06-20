import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = (
    PASTA_RESULTADOS /
    "cnpjs_ltda_socio_unico_pf_com_sexo.xlsx"
)

ARQUIVO_SAIDA = (
    PASTA_RESULTADOS /
    "cnpjs_ltda_socio_unico_pf_com_cnae.xlsx"
)

print("Lendo base...")

df = pd.read_excel(
    ARQUIVO_ENTRADA,
    dtype=str
)

def classificar_cnae(cnae):

    if pd.isna(cnae):
        return "Outros"

    cnae = str(cnae)

    if cnae.startswith("41"):
        return "Construção"

    if cnae.startswith("42"):
        return "Construção"

    if cnae.startswith("43"):
        return "Construção"

    if cnae.startswith("45"):
        return "Comércio"

    if cnae.startswith("46"):
        return "Comércio"

    if cnae.startswith("47"):
        return "Comércio"

    if cnae.startswith("49"):
        return "Transporte"

    if cnae.startswith("50"):
        return "Transporte"

    if cnae.startswith("51"):
        return "Transporte"

    if cnae.startswith("52"):
        return "Transporte"

    if cnae.startswith("53"):
        return "Transporte"

    if cnae.startswith("55"):
        return "Serviços"

    if cnae.startswith("56"):
        return "Serviços"

    if cnae.startswith("58"):
        return "Tecnologia"

    if cnae.startswith("61"):
        return "Tecnologia"

    if cnae.startswith("62"):
        return "Tecnologia"

    if cnae.startswith("63"):
        return "Tecnologia"

    if cnae.startswith("68"):
        return "Imobiliário"

    if cnae.startswith("85"):
        return "Educação"

    if cnae.startswith("86"):
        return "Saúde"

    return "Outros"


print("Classificando CNAEs...")

df["categoria_cnae"] = (
    df["cnae_principal"]
    .apply(classificar_cnae)
)

df.to_excel(
    ARQUIVO_SAIDA,
    index=False
)

print()
print("Finalizado.")
print(f"Empresas: {len(df)}")
print(f"Arquivo salvo em:")
print(ARQUIVO_SAIDA)

print()
print(df["categoria_cnae"].value_counts())