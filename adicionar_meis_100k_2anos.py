from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd


PASTA_BASE = Path(__file__).resolve().parent
PASTA_DOWNLOADS = PASTA_BASE / "downloads"
PASTA_RESULTADOS = PASTA_BASE / "resultados"
BASE_FINAL = PASTA_RESULTADOS / "base_final_minerador_cnpj.csv"
MUNICIPIOS_ZIP = PASTA_DOWNLOADS / "Municipios.zip"
SIMPLES_ZIP = PASTA_DOWNLOADS / "Simples.zip"

CAPITAL_MINIMO = 100_000.00
IDADE_MINIMA_ANOS = 2
CHUNK_SIZE = 250_000

COLUNAS_EMPRESAS = [
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte",
    "ente_federativo",
]

COLUNAS_SIMPLES = [
    "cnpj_basico",
    "opcao_simples",
    "data_opcao_simples",
    "data_exclusao_simples",
    "opcao_mei",
    "data_opcao_mei",
    "data_exclusao_mei",
]

COLUNAS_ESTABELECIMENTOS = [
    "cnpj_basico",
    "cnpj_ordem",
    "cnpj_dv",
    "matriz_filial",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao",
    "motivo_situacao",
    "cidade_exterior",
    "pais",
    "data_inicio",
    "cnae_principal",
    "cnae_secundaria",
    "tipo_logradouro",
    "logradouro_receita",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "ddd1",
    "telefone1",
    "ddd2",
    "telefone2",
    "ddd_fax",
    "fax",
    "email",
    "situacao_especial",
    "data_situacao_especial",
]

COLUNAS_BASE_ESPERADAS = [
    "cnpj",
    "razao_social",
    "capital_social",
    "natureza_juridica",
    "nome_fantasia",
    "situacao_cadastral",
    "data_inicio",
    "cnae_principal",
    "ddd1",
    "telefone1",
    "email",
    "quantidade_socios",
    "nome_socio",
    "cpf_cnpj_socio",
    "tipo_socio",
    "qualificacao_socio",
    "data_entrada_sociedade",
    "faixa_etaria",
    "primeiro_nome_socio",
    "sexo_provavel",
    "categoria_cnae",
    "logradouro",
    "numero",
    "complemento",
    "cep",
    "bairro",
    "municipio",
    "uf",
    "municipio_nome",
]


def texto(valor: object) -> str:
    if valor is None or pd.isna(valor):
        return ""
    return str(valor).strip()


def somente_digitos(serie: pd.Series, tamanho: int | None = None) -> pd.Series:
    resultado = serie.fillna("").astype(str).str.replace(r"\D", "", regex=True)
    if tamanho is not None:
        resultado = resultado.str.zfill(tamanho)
    return resultado


def data_limite_anos(anos: int) -> date:
    hoje = date.today()
    try:
        return hoje.replace(year=hoje.year - anos)
    except ValueError:
        return hoje.replace(month=2, day=28, year=hoje.year - anos)


def primeiro_arquivo_csv_no_zip(caminho_zip: Path) -> str:
    with zipfile.ZipFile(caminho_zip, "r") as arquivo_zip:
        candidatos = [
            nome
            for nome in arquivo_zip.namelist()
            if not nome.endswith("/") and not nome.startswith("__MACOSX/")
        ]
        if not candidatos:
            raise RuntimeError(f"ZIP sem arquivo interno: {caminho_zip}")
        return candidatos[0]


def ler_zip_em_chunks(
    caminho_zip: Path,
    colunas: list[str],
    *,
    usecols: list[int] | None = None,
):
    nome_interno = primeiro_arquivo_csv_no_zip(caminho_zip)
    with zipfile.ZipFile(caminho_zip, "r") as arquivo_zip:
        with arquivo_zip.open(nome_interno) as arquivo:
            nomes = colunas if usecols is None else [colunas[indice] for indice in usecols]
            yield from pd.read_csv(
                arquivo,
                sep=";",
                header=None,
                names=nomes,
                usecols=usecols,
                dtype=str,
                encoding="latin1",
                keep_default_na=False,
                chunksize=CHUNK_SIZE,
                low_memory=False,
            )


def validar_arquivos() -> list[Path]:
    faltando: list[Path] = []

    if not BASE_FINAL.exists():
        faltando.append(BASE_FINAL)
    if not SIMPLES_ZIP.exists():
        faltando.append(SIMPLES_ZIP)
    if not MUNICIPIOS_ZIP.exists():
        faltando.append(MUNICIPIOS_ZIP)

    for indice in range(10):
        empresas_zip = PASTA_DOWNLOADS / f"Empresas{indice}.zip"
        estabelecimentos_zip = PASTA_DOWNLOADS / f"Estabelecimentos{indice}.zip"
        if not empresas_zip.exists():
            faltando.append(empresas_zip)
        if not estabelecimentos_zip.exists():
            faltando.append(estabelecimentos_zip)

    return faltando


def carregar_municipios() -> dict[str, str]:
    nome_interno = primeiro_arquivo_csv_no_zip(MUNICIPIOS_ZIP)
    with zipfile.ZipFile(MUNICIPIOS_ZIP, "r") as arquivo_zip:
        with arquivo_zip.open(nome_interno) as arquivo:
            municipios = pd.read_csv(
                arquivo,
                sep=";",
                header=None,
                names=["codigo", "nome"],
                dtype=str,
                encoding="latin1",
                keep_default_na=False,
            )

    municipios["codigo"] = somente_digitos(municipios["codigo"])
    municipios["nome"] = municipios["nome"].map(texto)
    return dict(zip(municipios["codigo"], municipios["nome"]))


def buscar_empresas_com_capital() -> pd.DataFrame:
    encontrados: list[pd.DataFrame] = []

    print(f"\n1/4 Procurando empresas com capital social >= R$ {CAPITAL_MINIMO:,.2f}...")

    for indice in range(10):
        caminho_zip = PASTA_DOWNLOADS / f"Empresas{indice}.zip"
        total_parte = 0

        for chunk in ler_zip_em_chunks(caminho_zip, COLUNAS_EMPRESAS):
            chunk["cnpj_basico"] = somente_digitos(chunk["cnpj_basico"], 8)
            capital = pd.to_numeric(
                chunk["capital_social"].str.replace(",", ".", regex=False),
                errors="coerce",
            )

            # MEI normalmente possui natureza 213-5 (Empresário Individual).
            # A confirmação definitiva, porém, será feita pelo arquivo Simples.zip.
            filtro = (capital >= CAPITAL_MINIMO) & (chunk["natureza_juridica"] == "2135")
            parte = chunk.loc[
                filtro,
                ["cnpj_basico", "razao_social", "natureza_juridica", "capital_social"],
            ].copy()

            if not parte.empty:
                parte["capital_social_num"] = capital.loc[parte.index]
                encontrados.append(parte)
                total_parte += len(parte)

        print(f"   Empresas{indice}.zip: {total_parte:,} candidatas")

    if not encontrados:
        return pd.DataFrame()

    empresas = pd.concat(encontrados, ignore_index=True)
    empresas = empresas.drop_duplicates(subset=["cnpj_basico"], keep="first")
    print(f"   Total após capital + natureza 2135: {len(empresas):,}")
    return empresas


def confirmar_opcao_mei(empresas: pd.DataFrame) -> pd.DataFrame:
    print("\n2/4 Confirmando opção atual pelo MEI no Simples.zip...")

    candidatos = set(empresas["cnpj_basico"])
    ids_mei: set[str] = set()

    for chunk in ler_zip_em_chunks(
        SIMPLES_ZIP,
        COLUNAS_SIMPLES,
        usecols=[0, 4],
    ):
        chunk["cnpj_basico"] = somente_digitos(chunk["cnpj_basico"], 8)
        chunk["opcao_mei"] = chunk["opcao_mei"].astype(str).str.strip().str.upper()

        filtro = chunk["cnpj_basico"].isin(candidatos) & chunk["opcao_mei"].eq("S")
        if filtro.any():
            ids_mei.update(chunk.loc[filtro, "cnpj_basico"].tolist())

    confirmadas = empresas[empresas["cnpj_basico"].isin(ids_mei)].copy()
    print(f"   MEIs confirmados: {len(confirmadas):,}")
    return confirmadas


def buscar_estabelecimentos_aptos(empresas_mei: pd.DataFrame) -> pd.DataFrame:
    limite = data_limite_anos(IDADE_MINIMA_ANOS)
    limite_texto = limite.strftime("%Y%m%d")
    ids_mei = set(empresas_mei["cnpj_basico"])
    resultados: list[pd.DataFrame] = []

    print(
        "\n3/4 Buscando matrizes ativas abertas até "
        f"{limite.strftime('%d/%m/%Y')}..."
    )

    for indice in range(10):
        caminho_zip = PASTA_DOWNLOADS / f"Estabelecimentos{indice}.zip"
        total_parte = 0

        for chunk in ler_zip_em_chunks(caminho_zip, COLUNAS_ESTABELECIMENTOS):
            chunk["cnpj_basico"] = somente_digitos(chunk["cnpj_basico"], 8)

            pre_filtro = chunk["cnpj_basico"].isin(ids_mei)
            if not pre_filtro.any():
                continue

            parte = chunk.loc[pre_filtro].copy()
            parte["data_inicio"] = somente_digitos(parte["data_inicio"])

            filtro = (
                parte["situacao_cadastral"].eq("02")
                & parte["matriz_filial"].eq("1")
                & parte["data_inicio"].str.fullmatch(r"\d{8}", na=False)
                & parte["data_inicio"].le(limite_texto)
            )
            parte = parte.loc[filtro].copy()

            if parte.empty:
                continue

            parte["cnpj_ordem"] = somente_digitos(parte["cnpj_ordem"], 4)
            parte["cnpj_dv"] = somente_digitos(parte["cnpj_dv"], 2)
            parte["cnpj"] = parte["cnpj_basico"] + parte["cnpj_ordem"] + parte["cnpj_dv"]

            resultados.append(parte)
            total_parte += len(parte)

        print(f"   Estabelecimentos{indice}.zip: {total_parte:,} aptos")

    if not resultados:
        return pd.DataFrame()

    estabelecimentos = pd.concat(resultados, ignore_index=True)
    estabelecimentos = estabelecimentos.drop_duplicates(subset=["cnpj"], keep="first")
    print(f"   Total de matrizes ativas com 2 anos ou mais: {len(estabelecimentos):,}")
    return estabelecimentos


def montar_linhas_base(
    empresas_mei: pd.DataFrame,
    estabelecimentos: pd.DataFrame,
    colunas_base: list[str],
) -> pd.DataFrame:
    municipios = carregar_municipios()

    dados = estabelecimentos.merge(
        empresas_mei,
        on="cnpj_basico",
        how="inner",
        validate="many_to_one",
    )

    tipo = dados["tipo_logradouro"].map(texto)
    nome = dados["logradouro_receita"].map(texto)
    dados["logradouro"] = (tipo + " " + nome).str.replace(r"\s+", " ", regex=True).str.strip()
    dados["municipio"] = somente_digitos(dados["municipio"])
    dados["municipio_nome"] = dados["municipio"].map(municipios).fillna("")
    dados["capital_social"] = dados["capital_social_num"].map(lambda valor: f"{valor:.2f}")

    novas = pd.DataFrame(index=dados.index)
    for coluna in colunas_base:
        novas[coluna] = ""

    mapa_direto = {
        "cnpj": "cnpj",
        "razao_social": "razao_social",
        "capital_social": "capital_social",
        "natureza_juridica": "natureza_juridica",
        "nome_fantasia": "nome_fantasia",
        "situacao_cadastral": "situacao_cadastral",
        "data_inicio": "data_inicio",
        "cnae_principal": "cnae_principal",
        "ddd1": "ddd1",
        "telefone1": "telefone1",
        "email": "email",
        "logradouro": "logradouro",
        "numero": "numero",
        "complemento": "complemento",
        "cep": "cep",
        "bairro": "bairro",
        "municipio": "municipio",
        "uf": "uf",
        "municipio_nome": "municipio_nome",
    }

    for destino, origem in mapa_direto.items():
        if destino in novas.columns and origem in dados.columns:
            novas[destino] = dados[origem].map(texto)

    # MEI é empresário individual. Não inventamos dados pessoais nos campos de sócio.
    # Esses campos ficam vazios para preservar a origem oficial dos dados.
    if "quantidade_socios" in novas.columns:
        novas["quantidade_socios"] = "0"

    return novas


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Localiza MEIs ativos com capital social mínimo de R$ 100 mil e "
            "pelo menos 2 anos de abertura. Por padrão, executa somente uma prévia."
        )
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Cria backup e acrescenta os novos CNPJs à base final.",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("ATUALIZAÇÃO DE MEIs | CAPITAL >= R$ 100 MIL | ABERTURA >= 2 ANOS")
    print("Modo:", "APLICAR NA BASE" if args.aplicar else "PRÉVIA SEGURA")
    print("=" * 72)

    faltando = validar_arquivos()
    if faltando:
        print("\nERRO: arquivos obrigatórios não encontrados:")
        for caminho in faltando:
            print(f" - {caminho}")
        return 1

    base = pd.read_csv(BASE_FINAL, dtype=str, keep_default_na=False, low_memory=False)
    if "cnpj" not in base.columns:
        print("ERRO: a base final não contém a coluna 'cnpj'.")
        return 1

    colunas_base = list(base.columns)
    faltando_na_base = [coluna for coluna in COLUNAS_BASE_ESPERADAS if coluna not in colunas_base]
    if faltando_na_base:
        print("Aviso: a base não possui algumas colunas esperadas:")
        print(" - " + "\n - ".join(faltando_na_base))
        print("O script continuará usando exatamente o esquema atual da base.")

    base["cnpj"] = somente_digitos(base["cnpj"], 14)
    cnpjs_existentes = set(base["cnpj"])
    print(f"\nBase atual carregada: {len(base):,} linhas")

    empresas = buscar_empresas_com_capital()
    if empresas.empty:
        print("\nNenhuma empresa candidata encontrada.")
        return 0

    empresas_mei = confirmar_opcao_mei(empresas)
    if empresas_mei.empty:
        print("\nNenhuma candidata possui opção atual pelo MEI.")
        return 0

    estabelecimentos = buscar_estabelecimentos_aptos(empresas_mei)
    if estabelecimentos.empty:
        print("\nNenhuma matriz ativa com pelo menos 2 anos foi encontrada.")
        return 0

    novas = montar_linhas_base(empresas_mei, estabelecimentos, colunas_base)
    novas["cnpj"] = somente_digitos(novas["cnpj"], 14)
    novas = novas[~novas["cnpj"].isin(cnpjs_existentes)].copy()
    novas = novas.drop_duplicates(subset=["cnpj"], keep="first")

    print("\n4/4 Comparando com a base existente...")
    print(f"   Novos CNPJs que ainda não estavam na base: {len(novas):,}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefixo = "meis_adicionados" if args.aplicar else "preview_meis"
    auditoria = PASTA_RESULTADOS / f"{prefixo}_100k_2anos_{timestamp}.csv"
    novas.to_csv(auditoria, index=False, encoding="utf-8-sig")
    print(f"   Arquivo de conferência: {auditoria}")

    if novas.empty:
        print("\nNenhuma alteração necessária.")
        return 0

    if not args.aplicar:
        print("\nPRÉVIA CONCLUÍDA. A base final NÃO foi alterada.")
        print("Confira o arquivo de preview e, quando estiver correto, execute:")
        print("python adicionar_meis_100k_2anos.py --aplicar")
        return 0

    backup = BASE_FINAL.with_name(f"base_final_minerador_cnpj_backup_antes_mei_{timestamp}.csv")
    temporario = BASE_FINAL.with_suffix(".csv.tmp")

    print(f"\nCriando backup: {backup}")
    shutil.copy2(BASE_FINAL, backup)

    atualizada = pd.concat([base, novas], ignore_index=True)
    atualizada = atualizada.drop_duplicates(subset=["cnpj"], keep="first")

    print("Gravando base atualizada...")
    atualizada.to_csv(temporario, index=False, encoding="utf-8")
    temporario.replace(BASE_FINAL)

    print("\n✅ Atualização concluída.")
    print(f"✅ Linhas antes: {len(base):,}")
    print(f"✅ MEIs adicionados: {len(novas):,}")
    print(f"✅ Linhas depois: {len(atualizada):,}")
    print(f"✅ Backup: {backup}")
    print(f"✅ Auditoria: {auditoria}")
    print(f"✅ Base: {BASE_FINAL}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        raise SystemExit(130)
    except Exception as erro:
        print(f"\nERRO inesperado: {erro}")
        raise
