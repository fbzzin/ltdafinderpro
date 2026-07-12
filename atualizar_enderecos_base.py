"""Enriquece a base final do LTDAFinder Pro com endereço completo da Receita Federal.

Uso, na raiz do projeto:
    python atualizar_enderecos_base.py

O script lê apenas as colunas necessárias dos arquivos Estabelecimentos*.zip,
filtra pelos CNPJs que já existem na base final e atualiza o CSV sem alterar
as demais colunas do sistema.
"""

from __future__ import annotations

import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd


PASTA_BASE = Path(__file__).resolve().parent
PASTA_DOWNLOADS = PASTA_BASE / "downloads"
PASTA_RESULTADOS = PASTA_BASE / "resultados"
BASE_FINAL = PASTA_RESULTADOS / "base_final_minerador_cnpj.csv"
MUNICIPIOS_ZIP = PASTA_DOWNLOADS / "Municipios.zip"

CHUNK_SIZE = 250_000

# Layout oficial dos arquivos de Estabelecimentos da Receita Federal.
COLUNAS_ESTABELECIMENTOS = {
    0: "cnpj_basico",
    1: "cnpj_ordem",
    2: "cnpj_dv",
    13: "tipo_logradouro",
    14: "logradouro_nome",
    15: "numero",
    16: "complemento",
    17: "bairro",
    18: "cep",
    19: "uf",
    20: "municipio",
}

COLUNAS_ENDERECO = [
    "logradouro",
    "numero",
    "complemento",
    "cep",
    "bairro",
    "municipio",
    "municipio_nome",
    "uf",
]


def texto_limpo(valor: object) -> str:
    if valor is None or pd.isna(valor):
        return ""

    texto = str(valor).strip()
    if texto.lower() in {"nan", "none", "null", "nat"}:
        return ""

    return texto


def somente_digitos(valor: object, tamanho: int = 0) -> str:
    digitos = re.sub(r"\D", "", texto_limpo(valor))
    return digitos.zfill(tamanho) if tamanho else digitos


def combinar_logradouro(tipo: object, nome: object) -> str:
    tipo_limpo = texto_limpo(tipo)
    nome_limpo = texto_limpo(nome)

    if tipo_limpo and nome_limpo:
        return f"{tipo_limpo} {nome_limpo}".strip()

    return nome_limpo or tipo_limpo


def localizar_csv_no_zip(caminho_zip: Path) -> str:
    with zipfile.ZipFile(caminho_zip, "r") as arquivo_zip:
        candidatos = [
            nome
            for nome in arquivo_zip.namelist()
            if not nome.endswith("/") and not nome.startswith("__MACOSX/")
        ]

    if not candidatos:
        raise RuntimeError(f"Nenhum arquivo encontrado dentro de {caminho_zip.name}")

    return candidatos[0]


def carregar_municipios() -> dict[str, str]:
    if not MUNICIPIOS_ZIP.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {MUNICIPIOS_ZIP}. "
            "Ele é necessário para trocar códigos como 7107 pelo nome do município."
        )

    nome_csv = localizar_csv_no_zip(MUNICIPIOS_ZIP)

    with zipfile.ZipFile(MUNICIPIOS_ZIP, "r") as arquivo_zip:
        with arquivo_zip.open(nome_csv) as arquivo:
            municipios = pd.read_csv(
                arquivo,
                sep=";",
                header=None,
                names=["codigo", "nome"],
                dtype=str,
                encoding="latin1",
                keep_default_na=False,
            )

    municipios["codigo"] = municipios["codigo"].map(texto_limpo)
    municipios["nome"] = municipios["nome"].map(texto_limpo)
    return dict(zip(municipios["codigo"], municipios["nome"]))


def ler_enderecos_alvo(cnpjs_alvo: set[str], arquivos_zip: list[Path]) -> pd.DataFrame:
    encontrados: list[pd.DataFrame] = []
    restantes = set(cnpjs_alvo)
    usecols = list(COLUNAS_ESTABELECIMENTOS.keys())
    names = [COLUNAS_ESTABELECIMENTOS[indice] for indice in usecols]

    for indice_zip, caminho_zip in enumerate(arquivos_zip, start=1):
        if not restantes:
            break

        print(
            f"[{indice_zip}/{len(arquivos_zip)}] Lendo {caminho_zip.name} "
            f"| faltam {len(restantes):,} CNPJs..."
        )

        nome_csv = localizar_csv_no_zip(caminho_zip)

        with zipfile.ZipFile(caminho_zip, "r") as arquivo_zip:
            with arquivo_zip.open(nome_csv) as arquivo:
                leitor = pd.read_csv(
                    arquivo,
                    sep=";",
                    header=None,
                    usecols=usecols,
                    names=names,
                    dtype=str,
                    encoding="latin1",
                    chunksize=CHUNK_SIZE,
                    keep_default_na=False,
                    low_memory=False,
                )

                for chunk in leitor:
                    chunk["cnpj"] = (
                        chunk["cnpj_basico"].map(lambda v: somente_digitos(v, 8))
                        + chunk["cnpj_ordem"].map(lambda v: somente_digitos(v, 4))
                        + chunk["cnpj_dv"].map(lambda v: somente_digitos(v, 2))
                    )

                    selecionados = chunk[chunk["cnpj"].isin(restantes)].copy()
                    if selecionados.empty:
                        continue

                    selecionados["logradouro"] = selecionados.apply(
                        lambda linha: combinar_logradouro(
                            linha["tipo_logradouro"], linha["logradouro_nome"]
                        ),
                        axis=1,
                    )

                    encontrados.append(
                        selecionados[
                            [
                                "cnpj",
                                "logradouro",
                                "numero",
                                "complemento",
                                "cep",
                                "bairro",
                                "municipio",
                                "uf",
                            ]
                        ]
                    )

                    restantes.difference_update(selecionados["cnpj"].tolist())

    if not encontrados:
        return pd.DataFrame(
            columns=[
                "cnpj",
                "logradouro",
                "numero",
                "complemento",
                "cep",
                "bairro",
                "municipio",
                "uf",
            ]
        )

    enderecos = pd.concat(encontrados, ignore_index=True)
    enderecos = enderecos.drop_duplicates(subset=["cnpj"], keep="first")

    print(f"Endereços encontrados: {len(enderecos):,}/{len(cnpjs_alvo):,}")
    if restantes:
        print(f"Aviso: {len(restantes):,} CNPJs não foram encontrados nos ZIPs atuais.")

    return enderecos


def main() -> int:
    if not BASE_FINAL.exists():
        print(f"ERRO: base final não encontrada em {BASE_FINAL}")
        return 1

    arquivos_zip = sorted(PASTA_DOWNLOADS.glob("Estabelecimentos*.zip"))
    if not arquivos_zip:
        print(
            "ERRO: nenhum Estabelecimentos*.zip foi encontrado em "
            f"{PASTA_DOWNLOADS}"
        )
        return 1

    print("Carregando base final...")
    base = pd.read_csv(BASE_FINAL, dtype=str, keep_default_na=False)

    if "cnpj" not in base.columns:
        print("ERRO: a coluna 'cnpj' não existe na base final.")
        return 1

    base["cnpj"] = base["cnpj"].map(lambda valor: somente_digitos(valor, 14))
    cnpjs_alvo = set(base["cnpj"].dropna().astype(str))

    print(f"CNPJs na base: {len(cnpjs_alvo):,}")
    print(f"ZIPs de estabelecimentos encontrados: {len(arquivos_zip)}")

    municipios = carregar_municipios()
    enderecos = ler_enderecos_alvo(cnpjs_alvo, arquivos_zip)

    if enderecos.empty:
        print("ERRO: nenhum endereço foi localizado. A base não foi modificada.")
        return 1

    enderecos["municipio_nome"] = (
        enderecos["municipio"].map(municipios).fillna("")
    )

    for coluna in COLUNAS_ENDERECO:
        if coluna in base.columns:
            base = base.drop(columns=[coluna])

    base_atualizada = base.merge(enderecos, on="cnpj", how="left")

    for coluna in COLUNAS_ENDERECO:
        if coluna not in base_atualizada.columns:
            base_atualizada[coluna] = ""
        base_atualizada[coluna] = base_atualizada[coluna].fillna("").map(texto_limpo)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BASE_FINAL.with_name(f"base_final_minerador_cnpj_backup_{timestamp}.csv")
    temporario = BASE_FINAL.with_suffix(".csv.tmp")

    print(f"Criando backup em: {backup}")
    shutil.copy2(BASE_FINAL, backup)

    print("Gravando base enriquecida...")
    base_atualizada.to_csv(temporario, index=False, encoding="utf-8")
    temporario.replace(BASE_FINAL)

    preenchidos = base_atualizada["logradouro"].astype(str).str.strip().ne("").sum()
    municipios_nomeados = base_atualizada["municipio_nome"].astype(str).str.strip().ne("").sum()

    print("\n✅ Atualização concluída.")
    print(f"✅ Linhas totais: {len(base_atualizada):,}")
    print(f"✅ Endereços com logradouro: {preenchidos:,}")
    print(f"✅ Municípios com nome: {municipios_nomeados:,}")
    print(f"✅ Base atualizada: {BASE_FINAL}")
    print(f"✅ Backup local: {backup}")
    print("\nAgora valide com:")
    print(
        'python -c "import pandas as pd; '
        "df=pd.read_csv(r\'resultados\\base_final_minerador_cnpj.csv\',dtype=str); "
        "print(df[df[\'municipio\'].astype(str).eq(\'7107\')]["
        "[\'cnpj\',\'logradouro\',\'numero\',\'complemento\',\'cep\',"
        "\'bairro\',\'municipio_nome\',\'uf\']].head(3).to_string(index=False))\""
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        raise SystemExit(130)
    except Exception as erro:
        print(f"\nERRO inesperado: {erro}")
        raise SystemExit(1)
