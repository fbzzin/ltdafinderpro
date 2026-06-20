import pandas as pd
from pathlib import Path

PASTA_RESULTADOS = Path(r"C:\Users\Dutra\Desktop\minerador-cnpj\resultados")

ARQUIVO_ENTRADA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf.xlsx"

ARQUIVO_SAIDA = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf_com_sexo.xlsx"
ARQUIVO_HOMENS = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf_homens.xlsx"
ARQUIVO_MULHERES = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf_mulheres.xlsx"
ARQUIVO_INDEFINIDO = PASTA_RESULTADOS / "cnpjs_ltda_socio_unico_pf_sexo_indefinido.xlsx"

NOMES_MASCULINOS = {
    "JOSE", "JOAO", "ANTONIO", "FRANCISCO", "CARLOS", "PAULO", "PEDRO",
    "LUCAS", "LUIZ", "LUIS", "MARCOS", "MARCO", "MARCELO", "RAFAEL",
    "GABRIEL", "DANIEL", "ROBERTO", "FERNANDO", "RICARDO", "EDUARDO",
    "FELIPE", "BRUNO", "ANDRE", "ALEXANDRE", "GUSTAVO", "RODRIGO",
    "LEONARDO", "LEANDRO", "THIAGO", "TIAGO", "FABIO", "FABIANO",
    "CLAUDIO", "SERGIO", "MARIO", "ALBERTO", "JORGE", "WILLIAM",
    "WESLEY", "DIEGO", "VINICIUS", "HENRIQUE", "AUGUSTO", "MATEUS",
    "MATHEUS", "MIGUEL", "ARTHUR", "DAVI", "MURILO", "RENATO",
    "NELSON", "ADRIANO", "EMERSON", "EVANDRO", "VALTER", "WALTER"
}

NOMES_FEMININOS = {
    "MARIA", "ANA", "FRANCISCA", "ANTONIA", "ADRIANA", "JULIANA",
    "MARCIA", "FERNANDA", "PATRICIA", "ALINE", "SANDRA", "CAMILA",
    "AMANDA", "BRUNA", "JESSICA", "LETICIA", "JULIA", "LUCIANA",
    "VANESSA", "CRISTINA", "CLAUDIA", "SIMONE", "MONICA", "RENATA",
    "ROBERTA", "CARLA", "DANIELA", "LUANA", "LARISSA", "BEATRIZ",
    "GABRIELA", "RAFAELA", "ISABELA", "ISABELLA", "PAULA", "VIVIANE",
    "TATIANA", "PRISCILA", "ELIANE", "LUZIA", "TEREZA", "TERESA",
    "HELENA", "ALICE", "LAURA", "MANUELA", "LUCIA", "REGINA",
    "ROSA", "SUELI", "SILVIA", "ELISANGELA", "APARECIDA"
}

def primeiro_nome(nome):
    if pd.isna(nome):
        return ""

    nome = str(nome).upper().strip()
    partes = nome.split()

    if not partes:
        return ""

    return partes[0]

def classificar_sexo(nome):
    p_nome = primeiro_nome(nome)

    if p_nome in NOMES_MASCULINOS:
        return "Masculino"

    if p_nome in NOMES_FEMININOS:
        return "Feminino"

    return "Indefinido"

print("Lendo base...")

df = pd.read_excel(ARQUIVO_ENTRADA, dtype=str)

print("Classificando sexo provável...")

df["primeiro_nome_socio"] = df["nome_socio"].apply(primeiro_nome)
df["sexo_provavel"] = df["nome_socio"].apply(classificar_sexo)

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

df_final = df.drop(columns=["capital_social_num"])

homens = df_final[df_final["sexo_provavel"] == "Masculino"].copy()
mulheres = df_final[df_final["sexo_provavel"] == "Feminino"].copy()
indefinido = df_final[df_final["sexo_provavel"] == "Indefinido"].copy()

df_final.to_excel(ARQUIVO_SAIDA, index=False)
homens.to_excel(ARQUIVO_HOMENS, index=False)
mulheres.to_excel(ARQUIVO_MULHERES, index=False)
indefinido.to_excel(ARQUIVO_INDEFINIDO, index=False)

print("Finalizado.")
print(f"Total geral: {len(df_final)}")
print(f"Masculino: {len(homens)}")
print(f"Feminino: {len(mulheres)}")
print(f"Indefinido: {len(indefinido)}")
print()
print(f"Arquivo geral: {ARQUIVO_SAIDA}")
print(f"Homens: {ARQUIVO_HOMENS}")
print(f"Mulheres: {ARQUIVO_MULHERES}")
print(f"Indefinido: {ARQUIVO_INDEFINIDO}")