from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

STATUS_SUCESSO = {"Verificou 250", "Verificou 2k", "Verificou 100k"}
STATUS_NEGATIVOS = {
    "Precisa de mais informações",
    "Análise permanente",
    "Restrito",
    "WABA restrita",
    "Conta desabilitada",
    "Checkpoint",
    "Descartado",
}
STATUS_CONCLUIDOS = STATUS_SUCESSO | STATUS_NEGATIVOS

FATORES = {
    "faixa_capital": {"nome": "Capital social", "peso": 0.25},
    "faixa_idade": {"nome": "Idade da empresa", "peso": 0.20},
    "categoria_cnae": {"nome": "Categoria CNAE", "peso": 0.25},
    "uf": {"nome": "UF", "peso": 0.10},
    "tem_telefone": {"nome": "Telefone cadastrado", "peso": 0.10},
    "tem_email": {"nome": "E-mail cadastrado", "peso": 0.10},
}


ORDEM_VALORES = {
    "faixa_capital": [
        "Sem informação",
        "Até R$ 50 mil",
        "R$ 50 mil a R$ 100 mil",
        "R$ 100 mil a R$ 500 mil",
        "R$ 500 mil a R$ 1 milhão",
        "Acima de R$ 1 milhão",
    ],
    "faixa_idade": [
        "Menos de 1 ano",
        "1 a 2 anos",
        "3 a 5 anos",
        "6 a 10 anos",
        "Mais de 10 anos",
    ],
    "tem_telefone": ["Sim", "Não"],
    "tem_email": ["Sim", "Não"],
}

COLUNAS_BASE_IA = [
    "cnpj",
    "razao_social",
    "capital_social",
    "data_inicio",
    "categoria_cnae",
    "uf",
    "ddd1",
    "telefone1",
    "email",
]


def limpar_cnpj(valor: Any) -> str:
    texto = "" if valor is None else str(valor).strip()
    if texto.endswith(".0"):
        texto = texto[:-2]
    digitos = "".join(caractere for caractere in texto if caractere.isdigit())
    return digitos.zfill(14)[-14:] if digitos else ""


def numero_float(valor: Any) -> float:
    texto = texto_limpo(valor, "0")

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except (TypeError, ValueError):
        return 0.0


def texto_limpo(valor: Any, padrao: str = "") -> str:
    if valor is None:
        return padrao
    texto = str(valor).strip()
    if not texto or texto.lower() in {"nan", "none", "null"}:
        return padrao
    return texto


def tem_valor(valor: Any) -> bool:
    texto = texto_limpo(valor)
    return bool(texto and any(caractere.isalnum() for caractere in texto))


def calcular_idade(data_inicio: Any, hoje: datetime | None = None) -> int:
    texto = texto_limpo(data_inicio).replace(".0", "")
    if not texto:
        return 0

    formatos = ["%Y%m%d", "%Y-%m-%d", "%d/%m/%Y"]
    abertura = None
    for formato in formatos:
        try:
            abertura = datetime.strptime(texto, formato)
            break
        except ValueError:
            continue

    if abertura is None:
        return 0

    referencia = hoje or datetime.now()
    idade = referencia.year - abertura.year
    if (referencia.month, referencia.day) < (abertura.month, abertura.day):
        idade -= 1
    return max(0, idade)


def faixa_capital(valor: Any) -> str:
    capital = numero_float(valor)
    if capital <= 0:
        return "Sem informação"
    if capital < 50_000:
        return "Até R$ 50 mil"
    if capital < 100_000:
        return "R$ 50 mil a R$ 100 mil"
    if capital < 500_000:
        return "R$ 100 mil a R$ 500 mil"
    if capital < 1_000_000:
        return "R$ 500 mil a R$ 1 milhão"
    return "Acima de R$ 1 milhão"


def faixa_idade(idade: Any) -> str:
    try:
        valor = int(float(idade or 0))
    except (TypeError, ValueError):
        valor = 0

    if valor < 1:
        return "Menos de 1 ano"
    if valor <= 2:
        return "1 a 2 anos"
    if valor <= 5:
        return "3 a 5 anos"
    if valor <= 10:
        return "6 a 10 anos"
    return "Mais de 10 anos"


def rotulo_booleano(valor: bool) -> str:
    return "Sim" if valor else "Não"


def extrair_caracteristicas(empresa: dict[str, Any]) -> dict[str, str]:
    capital = empresa.get("capital_social_num", empresa.get("capital_social", 0))
    idade = empresa.get("idade_empresa")
    if idade is None or str(idade).strip() == "":
        idade = calcular_idade(empresa.get("data_inicio", ""))

    telefone = empresa.get("telefone_formatado", "")
    if not tem_valor(telefone):
        telefone = f"{texto_limpo(empresa.get('ddd1'))}{texto_limpo(empresa.get('telefone1'))}"

    categoria = texto_limpo(empresa.get("categoria_cnae"), "Outros")
    uf = texto_limpo(empresa.get("uf"), "Sem UF").upper()

    return {
        "faixa_capital": faixa_capital(capital),
        "faixa_idade": faixa_idade(idade),
        "categoria_cnae": categoria,
        "uf": uf,
        "tem_telefone": rotulo_booleano(tem_valor(telefone)),
        "tem_email": rotulo_booleano(tem_valor(empresa.get("email", ""))),
    }


def fase_por_amostras(total_concluidas: int) -> dict[str, Any]:
    if total_concluidas < 50:
        return {
            "codigo": "fase_1",
            "nome": "Fase 1 · Regras fixas",
            "peso_historico": 0.0,
            "peso_regras": 1.0,
            "proximo_marco": 50,
        }
    if total_concluidas < 150:
        return {
            "codigo": "fase_2_inicial",
            "nome": "Fase 2 · Aprendizado inicial",
            "peso_historico": 0.40,
            "peso_regras": 0.60,
            "proximo_marco": 150,
        }
    if total_concluidas < 300:
        return {
            "codigo": "fase_2_consolidada",
            "nome": "Fase 2 · Aprendizado consolidado",
            "peso_historico": 0.55,
            "peso_regras": 0.45,
            "proximo_marco": 300,
        }
    if total_concluidas < 500:
        return {
            "codigo": "preparacao_fase_3",
            "nome": "Preparação para a Fase 3",
            "peso_historico": 0.65,
            "peso_regras": 0.35,
            "proximo_marco": 500,
        }
    return {
        "codigo": "fase_3",
        "nome": "Fase 3 · Modelo histórico avançado",
        "peso_historico": 0.75,
        "peso_regras": 0.25,
        "proximo_marco": None,
    }


def carregar_empresas_ia(caminho_base: Path | str, cnpjs: set[str]) -> dict[str, dict[str, Any]]:
    if not cnpjs:
        return {}

    caminho = Path(caminho_base)
    if not caminho.exists():
        return {}

    try:
        cabecalho = pd.read_csv(caminho, dtype=str, nrows=0).columns.tolist()
    except Exception:
        return {}

    colunas = [coluna for coluna in COLUNAS_BASE_IA if coluna in cabecalho]
    if "cnpj" not in colunas:
        return {}

    try:
        df = pd.read_csv(caminho, dtype=str, usecols=colunas)
    except Exception:
        return {}

    df["cnpj_limpo"] = df["cnpj"].apply(limpar_cnpj)
    df = df[df["cnpj_limpo"].isin(cnpjs)].copy()

    for coluna in COLUNAS_BASE_IA:
        if coluna not in df.columns:
            df[coluna] = ""
        else:
            df[coluna] = df[coluna].fillna("")

    empresas: dict[str, dict[str, Any]] = {}
    for registro in df.to_dict(orient="records"):
        registro["capital_social_num"] = numero_float(registro.get("capital_social", 0))
        registro["idade_empresa"] = calcular_idade(registro.get("data_inicio", ""))
        registro["telefone_formatado"] = (
            texto_limpo(registro.get("ddd1", "")) + texto_limpo(registro.get("telefone1", ""))
        )
        empresas[registro["cnpj_limpo"]] = registro

    return empresas


def _estatistica_bucket(registros: list[dict[str, Any]], taxa_global: float, alpha: float = 8.0) -> dict[str, Any]:
    total = len(registros)
    sucessos = sum(1 for registro in registros if registro["sucesso"])
    taxa_bruta = (sucessos / total) if total else 0.0
    taxa_ajustada = ((sucessos + alpha * taxa_global) / (total + alpha)) if total else taxa_global
    resultados = Counter(registro["status"] for registro in registros)
    resultado_comum = resultados.most_common(1)[0][0] if resultados else "Sem dados"

    return {
        "total": total,
        "sucessos": sucessos,
        "falhas": total - sucessos,
        "taxa": round(taxa_bruta * 100, 2),
        "taxa_ajustada": round(taxa_ajustada * 100, 2),
        "resultado_comum": resultado_comum,
        "resultados": dict(resultados),
    }


def construir_modelo_ia(perfis: list[dict[str, Any]], caminho_base: Path | str) -> dict[str, Any]:
    perfis = perfis if isinstance(perfis, list) else []
    perfis_vinculados = [perfil for perfil in perfis if limpar_cnpj(perfil.get("cnpj_limpo", ""))]
    cnpjs = {limpar_cnpj(perfil.get("cnpj_limpo", "")) for perfil in perfis_vinculados}
    empresas = carregar_empresas_ia(caminho_base, cnpjs)

    registros: list[dict[str, Any]] = []
    pendentes = 0
    sem_empresa = 0

    for perfil in perfis_vinculados:
        cnpj = limpar_cnpj(perfil.get("cnpj_limpo", ""))
        empresa = empresas.get(cnpj)
        status = texto_limpo(perfil.get("status_bm", "Disponível"), "Disponível")

        if empresa is None:
            sem_empresa += 1
            continue
        if status not in STATUS_CONCLUIDOS:
            pendentes += 1
            continue

        caracteristicas = extrair_caracteristicas(empresa)
        registros.append(
            {
                "cnpj_limpo": cnpj,
                "status": status,
                "sucesso": status in STATUS_SUCESSO,
                "caracteristicas": caracteristicas,
                "empresa": empresa,
            }
        )

    total_concluidas = len(registros)
    total_sucessos = sum(1 for registro in registros if registro["sucesso"])
    taxa_global = (total_sucessos / total_concluidas) if total_concluidas else 0.5
    taxa_global_exibicao = (total_sucessos / total_concluidas) if total_concluidas else 0.0
    fase = fase_por_amostras(total_concluidas)

    por_fator: dict[str, dict[str, dict[str, Any]]] = {}
    for fator in FATORES:
        agrupado: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for registro in registros:
            agrupado[registro["caracteristicas"].get(fator, "Sem informação")].append(registro)

        por_fator[fator] = {
            valor: _estatistica_bucket(lista, taxa_global)
            for valor, lista in agrupado.items()
        }

    distribuicao_contagem = Counter(registro["status"] for registro in registros)
    ordem_resultados = [
        "Verificou 250",
        "Verificou 2k",
        "Verificou 100k",
        "Precisa de mais informações",
        "Análise permanente",
        "Restrito",
        "WABA restrita",
        "Conta desabilitada",
        "Checkpoint",
        "Descartado",
    ]
    distribuicao = {
        status: distribuicao_contagem.get(status, 0)
        for status in ordem_resultados
        if distribuicao_contagem.get(status, 0) > 0
    }

    insights: list[dict[str, Any]] = []
    for fator, valores in por_fator.items():
        nome_fator = FATORES[fator]["nome"]
        for valor, estatistica in valores.items():
            if estatistica["total"] < 3:
                continue
            diferenca = estatistica["taxa_ajustada"] - (taxa_global * 100)
            insights.append(
                {
                    "fator": fator,
                    "nome_fator": nome_fator,
                    "valor": valor,
                    "total": estatistica["total"],
                    "sucessos": estatistica["sucessos"],
                    "taxa": estatistica["taxa"],
                    "taxa_ajustada": estatistica["taxa_ajustada"],
                    "diferenca": round(diferenca, 2),
                    "resultado_comum": estatistica["resultado_comum"],
                }
            )

    positivos = sorted(
        [item for item in insights if item["diferenca"] >= 3],
        key=lambda item: (item["diferenca"], item["total"]),
        reverse=True,
    )[:8]
    negativos = sorted(
        [item for item in insights if item["diferenca"] <= -3],
        key=lambda item: (item["diferenca"], -item["total"]),
    )[:8]

    def ranking(fator: str, limite: int | None = None) -> list[dict[str, Any]]:
        itens = [
            {"valor": valor, **estatistica}
            for valor, estatistica in por_fator.get(fator, {}).items()
        ]

        ordem = ORDEM_VALORES.get(fator)
        if ordem:
            posicoes = {valor: indice for indice, valor in enumerate(ordem)}
            itens.sort(key=lambda item: (posicoes.get(item["valor"], 999), item["valor"]))
        else:
            itens.sort(key=lambda item: (-item["total"], -item["taxa_ajustada"], item["valor"]))

        return itens[:limite] if limite else itens

    proximo_marco = fase.get("proximo_marco")
    faltam_para_marco = max(0, proximo_marco - total_concluidas) if proximo_marco else 0
    confianca_geral = min(100, round((total_concluidas / 150) * 100))

    return {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_perfis": len(perfis),
        "total_vinculados": len(perfis_vinculados),
        "total_concluidas": total_concluidas,
        "total_sucessos": total_sucessos,
        "total_falhas": total_concluidas - total_sucessos,
        "total_pendentes": pendentes,
        "total_sem_empresa": sem_empresa,
        "taxa_global": round(taxa_global_exibicao * 100, 2),
        "fase": fase,
        "faltam_para_marco": faltam_para_marco,
        "confianca_geral": confianca_geral,
        "por_fator": por_fator,
        "distribuicao_resultados": dict(distribuicao),
        "insights_positivos": positivos,
        "insights_negativos": negativos,
        "ranking_capital": ranking("faixa_capital"),
        "ranking_idade": ranking("faixa_idade"),
        "ranking_categoria": ranking("categoria_cnae", 15),
        "ranking_uf": ranking("uf", 15),
        "ranking_telefone": ranking("tem_telefone"),
        "ranking_email": ranking("tem_email"),
        "registros": registros,
    }


def avaliar_historico_empresa(
    empresa: dict[str, Any],
    modelo: dict[str, Any] | None,
    detalhado: bool = False,
) -> dict[str, Any]:
    if not modelo:
        return {
            "score_historico": 50,
            "taxa_estimada": 50.0,
            "confianca": 0,
            "amostras_semelhantes": 0,
            "resultado_mais_comum": "Sem dados",
            "motivos": [],
            "pontos_atencao": [],
            "fatores": [],
        }

    total_concluidas = int(modelo.get("total_concluidas", 0) or 0)
    taxa_global = float(modelo.get("taxa_global", 50.0) or 50.0)
    caracteristicas = extrair_caracteristicas(empresa)
    fatores_avaliados: list[dict[str, Any]] = []
    soma_taxas = 0.0
    soma_pesos = 0.0
    tamanhos = []
    resultados_agregados: Counter[str] = Counter()

    for fator, configuracao in FATORES.items():
        valor = caracteristicas.get(fator, "Sem informação")
        estatistica = modelo.get("por_fator", {}).get(fator, {}).get(valor)
        if not estatistica or estatistica.get("total", 0) <= 0:
            continue

        total = int(estatistica["total"])
        taxa_ajustada = float(estatistica["taxa_ajustada"])
        confianca_fator = min(1.0, total / 15.0)
        peso = float(configuracao["peso"]) * (0.35 + 0.65 * confianca_fator)

        soma_taxas += taxa_ajustada * peso
        soma_pesos += peso
        tamanhos.append(total)
        resultados_agregados.update(estatistica.get("resultados", {}))

        if detalhado:
            fatores_avaliados.append(
                {
                    "fator": fator,
                    "nome": configuracao["nome"],
                    "valor": valor,
                    "total": total,
                    "taxa": float(estatistica["taxa"]),
                    "taxa_ajustada": taxa_ajustada,
                    "diferenca": round(taxa_ajustada - taxa_global, 2),
                    "resultado_comum": estatistica.get("resultado_comum", "Sem dados"),
                }
            )

    taxa_estimada = (soma_taxas / soma_pesos) if soma_pesos else taxa_global
    amostras_semelhantes = int(round(median(tamanhos))) if tamanhos else 0
    confianca_amostra = min(100, round((amostras_semelhantes / 15) * 100))
    confianca_geral = int(modelo.get("confianca_geral", 0) or 0)
    confianca = round((confianca_amostra * 0.65) + (confianca_geral * 0.35))

    motivos = []
    pontos_atencao = []

    if detalhado:
        for fator in sorted(fatores_avaliados, key=lambda item: abs(item["diferenca"]), reverse=True):
            if fator["total"] < 3:
                continue
            texto = (
                f"{fator['nome']} · {fator['valor']}: "
                f"{fator['taxa']:.1f}% de sucesso em {fator['total']} caso(s)"
            )
            if fator["diferenca"] >= 4:
                motivos.append(texto)
            elif fator["diferenca"] <= -4:
                pontos_atencao.append(texto)

        if not motivos and total_concluidas >= 50:
            motivos.append(f"Base histórica geral com {taxa_global:.1f}% de sucesso")

    resultado_mais_comum = (
        resultados_agregados.most_common(1)[0][0]
        if resultados_agregados
        else "Sem dados"
    )

    return {
        "score_historico": max(0, min(100, round(taxa_estimada))),
        "taxa_estimada": round(taxa_estimada, 2),
        "confianca": max(0, min(100, confianca)),
        "amostras_semelhantes": amostras_semelhantes,
        "resultado_mais_comum": resultado_mais_comum,
        "motivos": motivos[:3],
        "pontos_atencao": pontos_atencao[:3],
        "fatores": fatores_avaliados if detalhado else [],
    }
