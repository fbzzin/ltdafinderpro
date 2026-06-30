from pathlib import Path
from datetime import datetime
import shutil
import re

APP_PATH = Path("app.py")
DASHBOARD_PATH = Path("templates") / "dashboard.html"

HELPERS_RAZAO = r'''

TERMOS_EMPRESA_RAZAO = {
    "COMERCIO", "COMERCIAL", "SERVICO", "SERVICOS", "SOLUCOES", "TECNOLOGIA",
    "DIGITAL", "MARKETING", "CONSULTORIA", "ASSESSORIA", "TRANSPORTES",
    "TRANSPORTE", "LOGISTICA", "CONSTRUCAO", "CONSTRUCOES", "ENGENHARIA",
    "INDUSTRIA", "INDUSTRIAL", "DISTRIBUIDORA", "DISTRIBUICAO", "IMPORTACAO",
    "EXPORTACAO", "MERCADO", "MINIMERCADO", "MERCEARIA", "ARMAZEM", "LOJA",
    "RESTAURANTE", "LANCHONETE", "PIZZARIA", "PADARIA", "DOCERIA", "DOCES",
    "HAMBURGUERIA", "BAR", "LOUNGE", "CLINICA", "LABORATORIO", "HOSPITAL",
    "ESCOLA", "COLEGIO", "CURSO", "CURSOS", "ACADEMIA", "AUTO", "PECAS",
    "AUTOPECAS", "OFICINA", "MECANICA", "ESTETICA", "BELEZA", "COSMETICOS",
    "FARMACIA", "DROGARIA", "IMOBILIARIA", "IMOVEIS", "PARTICIPACOES",
    "HOLDING", "INVESTIMENTOS", "ADMINISTRADORA", "EMPREENDIMENTOS",
    "INCORPORADORA", "AGRO", "AGROPECUARIA", "FAZENDA", "SITIO", "POSTO",
    "HOTEL", "POUSADA", "TURISMO", "VIAGENS", "EVENTOS", "PRODUCOES",
    "COMUNICACAO", "PUBLICIDADE", "PROPAGANDA", "ALIMENTOS", "BEBIDAS",
    "VAREJISTA", "ATACADISTA", "MATERIAIS", "ELETRICA", "ELETRICO",
    "INFORMATICA", "TELECOM", "GAMING", "BET", "BRASIL", "NACIONAL",
    "CENTER", "CENTRO", "SHOP", "STORE", "GROUP", "GRUPO", "MASTER",
    "PRIME", "ALPHA", "OMEGA", "NOVA", "NOVO", "IDEAL", "REAL", "TOP",
    "PLUS", "MAX", "VIP", "EXPRESS", "DELIVERY", "COMPANY", "EMPRESA",
    "CASA", "CASAS", "ATELIE", "ATELIER", "MODAS", "CONFECCOES", "MOVEIS",
    "MOVEIS", "EQUIPAMENTOS", "SUPRIMENTOS", "REPRESENTACOES", "REPRESENTACAO"
}

CONECTORES_NOME_RAZAO = {"DA", "DE", "DO", "DAS", "DOS", "E"}

NOMES_MASCULINOS_RAZAO = {
    "JOAO", "JOSE", "ANTONIO", "FRANCISCO", "CARLOS", "PAULO", "PEDRO",
    "LUCAS", "LUIZ", "LUIS", "MARCOS", "MARCO", "MATEUS", "MATHEUS",
    "GABRIEL", "RAFAEL", "DANIEL", "BRUNO", "FELIPE", "ANDRE", "RODRIGO",
    "RICARDO", "MARCELO", "ALEXANDRE", "GUSTAVO", "LEANDRO", "EDUARDO",
    "FERNANDO", "ROBERTO", "ROGERIO", "CLAUDIO", "FABIO", "FABIANO",
    "DIEGO", "THIAGO", "TIAGO", "VINICIUS", "VITOR", "VICTOR", "HUGO",
    "MIGUEL", "ARTHUR", "DAVI", "BERNARDO", "HEITOR", "ENZO", "NICOLAS",
    "NICOLAU", "SAMUEL", "BENJAMIN", "LORENZO", "MURILO", "CAIO",
    "ARMANDO", "ALBERTO", "ALFREDO", "ALCIR", "ALCIDES", "ALAN", "ALEX",
    "ADRIANO", "ADILSON", "ANDERSON", "WAGNER", "WELLINGTON", "WASHINGTON",
    "WILLIAM", "ROBSON", "RONALDO", "RENATO", "MAURICIO", "NELSON",
    "OSVALDO", "SEBASTIAO", "GERALDO", "MANOEL", "MANUEL", "EDSON",
    "ELIAS", "EVANDRO", "IVAN", "JULIO", "JULIANO", "LEONARDO", "ALESSANDRO",
    "AUGUSTO", "ADALBERTO", "ADEMIR", "AMILTON", "HAMILTON", "SIDNEY", "SIDNEI"
}

NOMES_FEMININOS_RAZAO = {
    "MARIA", "ANA", "ANTONIA", "FRANCISCA", "ADRIANA", "JULIANA",
    "MARCIA", "FERNANDA", "PATRICIA", "ALINE", "SANDRA", "CAMILA",
    "AMANDA", "BRUNA", "JESSICA", "LETICIA", "JULIA", "LUCIA", "LUCIANA",
    "VANESSA", "CRISTIANE", "CRISTINA", "CLAUDIA", "ANDREA", "RAQUEL",
    "CARLA", "CAROLINA", "DANIELA", "PRISCILA", "ROBERTA", "RENATA",
    "SIMONE", "TALITA", "TATIANE", "TATIANA", "ELIANE", "ELAINE",
    "ELISANGELA", "FABIANA", "GABRIELA", "ISABELA", "ISABELLA",
    "LARISSA", "MARIANA", "NATALIA", "PAULA", "VITORIA", "VANUSA",
    "ALICE", "HELENA", "LAURA", "VALENTINA", "HELOISA", "LORENA",
    "MANUELA", "LUIZA", "MELISSA", "LIVIA", "CECILIA", "BEATRIZ",
    "CLARA", "SOPHIA", "SOFIA", "YASMIN", "EMANUELA", "MIRELLA",
    "ALESSANDRA", "ALEXANDRA", "APARECIDA", "ROSA", "TEREZINHA",
    "TERESA", "VERA", "MARTA", "SUELI", "REGINA", "DENISE", "MONICA",
    "ANGELA", "ANGELICA", "SILVIA", "SILVANA", "SOLANGE", "VALERIA", "VIVIANE"
}


def normalizar_texto_razao(texto):
    texto = str(texto or "").strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("ASCII", "ignore").decode("ASCII")
    texto = texto.upper()
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def limpar_razao_social_para_nome(razao_social):
    texto = normalizar_texto_razao(razao_social)

    termos_juridicos = [
        "SOCIEDADE EMPRESARIA LIMITADA",
        "SOCIEDADE LIMITADA UNIPESSOAL",
        "SOCIEDADE UNIPESSOAL",
        "EMPRESA INDIVIDUAL DE RESPONSABILIDADE LIMITADA",
        "EMPRESARIO INDIVIDUAL",
        "MICROEMPREENDEDOR INDIVIDUAL",
        "LIMITADA",
        "LTDA",
        "EIRELI",
        "SLU",
        "MEI",
        "ME",
        "EPP"
    ]

    for termo in termos_juridicos:
        texto = re.sub(rf"\b{re.escape(termo)}\b", " ", texto)

    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def classificar_razao_social_nome_pessoa(razao_social):
    razao_limpa = limpar_razao_social_para_nome(razao_social)

    if not razao_limpa:
        return {"razao_limpa": "", "eh_nome_pessoa": False, "sexo": "Indefinido"}

    partes_brutas = razao_limpa.split()
    partes_validas = [parte for parte in partes_brutas if parte not in CONECTORES_NOME_RAZAO]

    if len(partes_validas) < 2:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    if len(partes_validas) > 7:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    if any(parte in TERMOS_EMPRESA_RAZAO for parte in partes_validas):
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    primeiro = partes_validas[0]

    if primeiro.isdigit():
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}

    if primeiro in NOMES_MASCULINOS_RAZAO:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": True, "sexo": "Masculino"}

    if primeiro in NOMES_FEMININOS_RAZAO:
        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": True, "sexo": "Feminino"}

    # Aceita razões sociais com iniciais, exemplo:
    # A ALBUQUERQUE MARTINS CORREA LTDA
    # A K DA SILVA VIEIRA LTDA
    if len(primeiro) == 1 and primeiro.isalpha() and len(partes_validas) >= 3:
        segundo_nome_real = ""
        for parte in partes_validas[1:]:
            if len(parte) > 1:
                segundo_nome_real = parte
                break

        sexo = "Indefinido"

        if segundo_nome_real in NOMES_MASCULINOS_RAZAO:
            sexo = "Masculino"
        elif segundo_nome_real in NOMES_FEMININOS_RAZAO:
            sexo = "Feminino"

        return {"razao_limpa": razao_limpa, "eh_nome_pessoa": True, "sexo": sexo}

    return {"razao_limpa": razao_limpa, "eh_nome_pessoa": False, "sexo": "Indefinido"}
'''

NOVO_BLOCO_CLASSIFICACAO = r'''    if "razao_social" not in df.columns:
        df["razao_social"] = ""

    if "sexo_provavel" in df.columns:
        df["sexo_socio"] = df["sexo_provavel"].replace("", "Indefinido")
    else:
        df["sexo_socio"] = "Indefinido"

    classificacao_razao = df["razao_social"].apply(classificar_razao_social_nome_pessoa)

    df["razao_limpa_nome"] = classificacao_razao.apply(lambda item: item["razao_limpa"])
    df["razao_nome_pessoa"] = classificacao_razao.apply(lambda item: item["eh_nome_pessoa"])
    df["sexo_razao"] = classificacao_razao.apply(lambda item: item["sexo"])
    df["sexo_provavel"] = df["sexo_razao"].replace("", "Indefinido")

    # Regra nova do minerador:
    # nome de pessoa agora é definido pela razão social, não pelo nome do sócio.
    df = df[df["razao_nome_pessoa"] == True].copy()

    df["categoria_cnae"] = df["categoria_cnae"].replace("", "Outros")
'''


def fazer_backup(caminho: Path, pasta_backup: Path):
    if not caminho.exists():
        return None
    destino = pasta_backup / caminho.name
    shutil.copy2(caminho, destino)
    return destino


def garantir_imports(texto: str) -> str:
    if "import zipfile, io, math, json, re, unicodedata" in texto:
        return texto

    if "import zipfile, io, math, json" in texto:
        return texto.replace(
            "import zipfile, io, math, json",
            "import zipfile, io, math, json, re, unicodedata",
            1
        )

    return texto


def inserir_helpers(texto: str) -> str:
    if "def classificar_razao_social_nome_pessoa" in texto:
        return texto

    marcador_preferido = "\ndef carregar_base():"
    if marcador_preferido in texto:
        return texto.replace(marcador_preferido, HELPERS_RAZAO + marcador_preferido, 1)

    marcador_alternativo = "\ndef calcular_idade_empresa"
    if marcador_alternativo in texto:
        return texto.replace(marcador_alternativo, HELPERS_RAZAO + marcador_alternativo, 1)

    raise RuntimeError("Não encontrei um ponto seguro para inserir as funções de razão social no app.py.")


def atualizar_classificacao_carregar_base(texto: str) -> str:
    if "razao_nome_pessoa" in texto and "df = df[df[\"razao_nome_pessoa\"] == True].copy()" in texto:
        return texto

    padrao = re.compile(
        r"(?m)^    df\[\"sexo_provavel\"\] = df\[\"sexo_provavel\"\]\.replace\(\"\", \"Indefinido\"\)\n"
        r"^    df\[\"categoria_cnae\"\] = df\[\"categoria_cnae\"\]\.replace\(\"\", \"Outros\"\)\n"
    )

    novo_texto, qtd = padrao.subn(NOVO_BLOCO_CLASSIFICACAO, texto, count=1)
    if qtd == 0:
        raise RuntimeError("Não encontrei o bloco sexo_provavel/categoria_cnae dentro do carregar_base().")

    return novo_texto


def remover_nome_socio_da_busca(texto: str) -> str:
    linhas = texto.splitlines()
    novas = []
    removidas = 0

    for linha in linhas:
        if '+ " " + df["nome_socio"].astype(str).str.upper()' in linha:
            removidas += 1
            continue
        novas.append(linha)

    texto = "\n".join(novas) + "\n"
    return texto


def atualizar_labels_python(texto: str) -> str:
    texto = texto.replace('"sexo_provavel": "Sexo Provável"', '"sexo_provavel": "Sexo pela Razão Social"')
    texto = texto.replace('"sexo_provavel": "Sexo"', '"sexo_provavel": "Sexo pela Razão Social"')
    return texto


def atualizar_app():
    if not APP_PATH.exists():
        raise FileNotFoundError("app.py não encontrado na raiz do projeto.")

    texto = APP_PATH.read_text(encoding="utf-8")
    texto = garantir_imports(texto)
    texto = inserir_helpers(texto)
    texto = atualizar_classificacao_carregar_base(texto)
    texto = remover_nome_socio_da_busca(texto)
    texto = atualizar_labels_python(texto)
    APP_PATH.write_text(texto, encoding="utf-8")


def atualizar_dashboard():
    if not DASHBOARD_PATH.exists():
        print("⚠ templates/dashboard.html não encontrado. Pulei ajuste visual do painel.")
        return

    texto = DASHBOARD_PATH.read_text(encoding="utf-8")
    texto = texto.replace(
        "Busque por CNPJ, razão social, sócio, telefone, email, município ou status",
        "Busque por CNPJ, razão social, telefone, email, município ou status"
    )
    texto = texto.replace("<label>Sexo</label>", "<label>Sexo pela razão social</label>")
    texto = texto.replace("<label>Sexo provável</label>", "<label>Sexo pela razão social</label>")
    DASHBOARD_PATH.write_text(texto, encoding="utf-8")


def main():
    agora = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_backup = Path(f"backup_razao_social_{agora}")
    pasta_backup.mkdir(exist_ok=True)

    backup_app = fazer_backup(APP_PATH, pasta_backup)
    backup_dashboard = fazer_backup(DASHBOARD_PATH, pasta_backup)

    atualizar_app()
    atualizar_dashboard()

    print("✅ Atualização concluída com sucesso.")
    print("✅ O minerador agora identifica nome de pessoa pela RAZÃO SOCIAL.")
    print("✅ A busca inteligente não usa mais o nome do sócio como campo de busca.")
    print("✅ O filtro de sexo agora usa a razão social.")
    print(f"✅ Backup criado em: {pasta_backup}")
    if backup_app:
        print(f"   - {backup_app}")
    if backup_dashboard:
        print(f"   - {backup_dashboard}")
    print("\nAgora rode:")
    print("python -m py_compile app.py")


if __name__ == "__main__":
    main()
