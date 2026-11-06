
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO
import csv
import re
import unicodedata
import warnings

import pandas as pd
import numpy as np


# ============================================================
# 1. CONFIGURAÇÕES
# ============================================================

ROOT = Path(__file__).resolve().parents[2]

PASTA_RAW = ROOT / "data" / "raw" / "meteorologia"
PASTA_PROCESSED = ROOT / "data" / "processed" / "meteorologia"
PASTA_QUALITY = ROOT / "data" / "quality" / "meteorologia"

ANOS_ANALISE = range(2021, 2027)
TAMANHO_LOTE = 100_000

MUNICIPIOS_BAIXADA = {
    "BERTIOGA",
    "CUBATAO",
    "GUARUJA",
    "ITANHAEM",
    "MONGAGUA",
    "PERUIBE",
    "PRAIA GRANDE",
    "SANTOS",
    "SAO VICENTE"
}

for pasta in [PASTA_PROCESSED, PASTA_QUALITY]:
    pasta.mkdir(parents=True, exist_ok=True)


# ============================================================
# 2. NORMALIZAÇÃO
# ============================================================

def normalizar_texto(valor):

    valor = str(valor).strip().upper()

    valor = unicodedata.normalize("NFKD", valor)

    valor = "".join(
        c for c in valor
        if not unicodedata.combining(c)
    )

    return valor


def normalizar_coluna(coluna):

    coluna = normalizar_texto(coluna)

    coluna = coluna.replace("°", " ")
    coluna = coluna.replace("²", "2")
    coluna = coluna.replace("(", " ")
    coluna = coluna.replace(")", " ")

    coluna = re.sub(r"[^A-Z0-9]+", "_", coluna)

    return coluna.strip("_").lower()


def numero(valor):

    if pd.isna(valor):
        return np.nan

    valor = str(valor).strip()

    if valor in ["", "-9999", "-9999,0", "-9999.0"]:
        return np.nan

    valor = valor.replace(",", ".")

    try:
        resultado = float(valor)

        if not np.isfinite(resultado):
            return np.nan

        return resultado

    except ValueError:
        return np.nan


# ============================================================
# 3. LEITURA DOS METADADOS
# ============================================================

def ler_metadados(zip_ref, nome_csv):

    with zip_ref.open(nome_csv) as arquivo:

        linhas = []

        for _ in range(8):

            linha = arquivo.readline()

            if not linha:
                break

            linhas.append(
                linha.decode("latin1").strip()
            )

    metadados = {}

    for linha in linhas:

        campos = next(
            csv.reader([linha], delimiter=";")
        )

        if len(campos) >= 2:

            chave = normalizar_texto(
                campos[0].replace(":", "")
            )

            valor = campos[1].strip()

            metadados[chave] = valor

    def obter(chave):
        return metadados.get(normalizar_texto(chave))

    return {
        "regiao": obter("REGIAO"),
        "uf": obter("UF"),
        "estacao": obter("ESTACAO"),
        "codigo_estacao": obter("CODIGO (WMO)"),
        "latitude": numero(obter("LATITUDE")),
        "longitude": numero(obter("LONGITUDE")),
        "altitude": numero(obter("ALTITUDE")),
        "data_fundacao": obter("DATA DE FUNDACAO"),
        "arquivo_origem": nome_csv
    }


# ============================================================
# 4. CATÁLOGO DE TODAS AS ESTAÇÕES
# ============================================================

def construir_catalogo():

    arquivos_zip = sorted(
        PASTA_RAW.glob("*.zip")
    )

    if not arquivos_zip:
        raise FileNotFoundError(
            f"Nenhum ZIP encontrado em {PASTA_RAW}"
        )

    registros = []

    for arquivo_zip in arquivos_zip:

        try:
            ano_zip = int(arquivo_zip.stem)
        except ValueError:
            continue

        if ano_zip not in ANOS_ANALISE:
            continue

        print(f"\nLendo catálogo de {ano_zip}...")

        with ZipFile(arquivo_zip, "r") as zip_ref:

            arquivos_csv = [
                nome for nome in zip_ref.namelist()
                if nome.lower().endswith(".csv")
                and not nome.startswith("__MACOSX/")
            ]

            for nome_csv in arquivos_csv:

                try:

                    metadados = ler_metadados(
                        zip_ref,
                        nome_csv
                    )

                    metadados["ano_zip"] = ano_zip

                    estacao = normalizar_texto(
                        metadados["estacao"]
                    )

                    uf = str(
                        metadados["uf"] or ""
                    ).strip().upper()

                    metadados["municipio_candidato"] = (
                        estacao
                        if estacao in MUNICIPIOS_BAIXADA
                        else None
                    )

                    metadados["dentro_abrangencia"] = (
                        uf == "SP"
                        and estacao in MUNICIPIOS_BAIXADA
                    )

                    registros.append(metadados)

                except Exception as erro:

                    print(
                        f"Erro nos metadados de {nome_csv}: {erro}"
                    )

    catalogo = pd.DataFrame(registros)

    if catalogo.empty:
        raise RuntimeError(
            "Não foi possível construir o catálogo."
        )

    catalogo.to_csv(
        PASTA_QUALITY / "catalogo_estacoes_completo.csv",
        index=False,
        encoding="utf-8-sig"
    )

    catalogo_baixada = catalogo[
        catalogo["dentro_abrangencia"]
    ].copy()

    catalogo_baixada.to_csv(
        PASTA_QUALITY / "catalogo_estacoes_baixada.csv",
        index=False,
        encoding="utf-8-sig"
    )

    return catalogo, catalogo_baixada


# ============================================================
# 5. LEITURA DAS MEDIÇÕES EM LOTES
# ============================================================

def ler_medicoes(zip_ref, nome_csv, metadados, ano_zip):

    with zip_ref.open(nome_csv) as arquivo:

        dados = pd.read_csv(
            arquivo,
            sep=";",
            skiprows=8,
            decimal=",",
            encoding="latin1",
            dtype=str,
            chunksize=TAMANHO_LOTE,
            on_bad_lines="warn"
        )

        for lote in dados:

            # Remover colunas vazias do final do CSV
            lote = lote.loc[
                :,
                ~lote.columns.astype(str).str.match(
                    r"^\s*(Unnamed:.*)?$"
                )
            ]

            lote.columns = [
                normalizar_coluna(c)
                for c in lote.columns
            ]

            coluna_data = "data"
            coluna_hora = "hora_utc"

            if coluna_data not in lote.columns:
                raise ValueError("Coluna DATA não encontrada")

            if coluna_hora not in lote.columns:
                raise ValueError("Coluna HORA UTC não encontrada")

            # Normalizar data
            data = pd.to_datetime(
                lote[coluna_data].str.strip(),
                format="%Y/%m/%d",
                errors="coerce"
            )

            # Normalizar horário
            horario = (
                lote[coluna_hora]
                .fillna("")
                .str.upper()
                .str.replace("UTC", "", regex=False)
                .str.strip()
            )

            horario = horario.apply(
                lambda x: re.sub(r"\D", "", x)
            )

            horario = horario.where(
                horario.str.fullmatch(r"\d{4}")
            )

            horario_valido = horario.str.match(
                r"^(?:[01]\d|2[0-3])[0-5]\d$"
            )

            data_hora = pd.to_datetime(
                data.dt.strftime("%Y-%m-%d")
                + " "
                + horario.where(horario_valido),
                format="%Y-%m-%d %H%M",
                errors="coerce",
                utc=True
            )

            lote["data_hora_utc"] = data_hora

            lote = lote.drop(
                columns=[coluna_data, coluna_hora],
                errors="ignore"
            )

            # Converter variáveis meteorológicas
            for coluna in lote.columns:
                if coluna != "data_hora_utc":
                    lote[coluna] = lote[coluna].apply(numero)

            # Adicionar metadados
            lote["ano_zip"] = ano_zip
            lote["estacao"] = metadados["estacao"]
            lote["codigo_estacao"] = metadados["codigo_estacao"]
            lote["uf"] = metadados["uf"]

            lote["latitude"] = metadados["latitude"]
            lote["longitude"] = metadados["longitude"]
            lote["altitude"] = metadados["altitude"]

            lote["arquivo_origem"] = nome_csv

            yield lote


# ============================================================
# 6. EXTRAÇÃO SOMENTE DA BAIXADA SANTISTA
# ============================================================

def extrair_baixada(catalogo_baixada):

    bases = []
    erros = []

    arquivos_zip = sorted(
        PASTA_RAW.glob("*.zip")
    )

    for arquivo_zip in arquivos_zip:

        try:
            ano_zip = int(arquivo_zip.stem)
        except ValueError:
            continue

        if ano_zip not in ANOS_ANALISE:
            continue

        print(f"\nProcessando medições de {ano_zip}...")

        with ZipFile(arquivo_zip, "r") as zip_ref:

            arquivos_csv = [
                nome for nome in zip_ref.namelist()
                if nome.lower().endswith(".csv")
                and not nome.startswith("__MACOSX/")
            ]

            for nome_csv in arquivos_csv:

                try:

                    metadados = ler_metadados(
                        zip_ref,
                        nome_csv
                    )

                    estacao = normalizar_texto(
                        metadados["estacao"]
                    )

                    uf = str(
                        metadados["uf"] or ""
                    ).strip().upper()

                    if (
                        uf != "SP"
                        or estacao not in MUNICIPIOS_BAIXADA
                    ):
                        continue

                    print(
                        f"  Estação selecionada: {estacao}"
                    )

                    for lote in ler_medicoes(
                        zip_ref,
                        nome_csv,
                        metadados,
                        ano_zip
                    ):

                        bases.append(lote)

                except Exception as erro:

                    erros.append({
                        "ano_zip": ano_zip,
                        "arquivo": nome_csv,
                        "erro": str(erro)
                    })

                    print(
                        f"  ERRO: {nome_csv}: {erro}"
                    )

    if not bases:
        raise RuntimeError(
            "Nenhuma medição foi extraída para a Baixada. "
            "Verifique catalogo_estacoes_baixada.csv."
        )

    base = pd.concat(
        bases,
        ignore_index=True
    )

    pd.DataFrame(erros).to_csv(
        PASTA_QUALITY / "erros_leitura.csv",
        index=False,
        encoding="utf-8-sig"
    )

    return base


# ============================================================
# 7. DIAGNÓSTICO DE QUALIDADE E LIMPEZA
# ============================================================

def limpar_e_diagnosticar(base):

    base = base.copy()

    # Registros sem data/hora válida
    invalidos_tempo = base[
        base["data_hora_utc"].isna()
    ].copy()

    base = base[
        base["data_hora_utc"].notna()
    ].copy()

    # Período solicitado
    base = base[
        (base["data_hora_utc"].dt.year >= 2021)
        & (base["data_hora_utc"].dt.year <= 2026)
    ].copy()

    # Identificação das duplicatas
    chave = [
        "codigo_estacao",
        "data_hora_utc"
    ]

    duplicatas = base[
        base.duplicated(
            subset=chave,
            keep=False
        )
    ].copy()

    # Mantém uma ocorrência e documenta as demais.
    base = base.drop_duplicates(
        subset=chave,
        keep="first"
    ).copy()

    # Identificação das variáveis meteorológicas
    colunas_meteorologicas = [
        c for c in base.columns
        if c not in {
            "data_hora_utc",
            "ano_zip",
            "estacao",
            "codigo_estacao",
            "uf",
            "latitude",
            "longitude",
            "altitude",
            "arquivo_origem"
        }
    ]

    # Diagnóstico de dados ausentes
    ausentes = pd.DataFrame([
        {
            "variavel": coluna,
            "quantidade_ausentes": int(base[coluna].isna().sum()),
            "percentual_ausentes": (
                base[coluna].isna().mean() * 100
            )
        }
        for coluna in colunas_meteorologicas
    ])

    # Valores suspeitos: regras conservadoras
    regras = {
        "precipitacao": lambda s: s < 0,
        "radiacao": lambda s: s < 0,
        "umidade": lambda s: (s < 0) | (s > 100),
        "vento_direcao": lambda s: (s < 0) | (s > 360),
        "vento_velocidade": lambda s: s < 0,
        "vento_rajada": lambda s: s < 0,
        "temperatura": lambda s: (s < -90) | (s > 60),
        "pressao": lambda s: s <= 0
    }

    suspeitos = []

    for coluna in colunas_meteorologicas:

        nome = coluna.lower()

        regra = None

        for termo, funcao in regras.items():

            if termo in nome:
                regra = funcao
                break

        if regra is None:
            continue

        mascara = regra(base[coluna]) & base[coluna].notna()

        if mascara.any():

            encontrados = base.loc[
                mascara,
                [
                    "data_hora_utc",
                    "estacao",
                    "codigo_estacao",
                    coluna,
                    "arquivo_origem"
                ]
            ].copy()

            encontrados = encontrados.rename(
                columns={coluna: "valor"}
            )

            encontrados["variavel"] = coluna

            suspeitos.append(encontrados)

    if suspeitos:
        valores_suspeitos = pd.concat(
            suspeitos,
            ignore_index=True
        )
    else:
        valores_suspeitos = pd.DataFrame()

    # Resumo estatístico
    resumo = base[colunas_meteorologicas].describe().T

    resumo.index.name = "variavel"
    resumo = resumo.reset_index()

    # Cobertura mensal
    cobertura = (
        base.groupby(
            [
                "estacao",
                "codigo_estacao",
                pd.Grouper(
                    key="data_hora_utc",
                    freq="MS"
                )
            ]
        )
        .size()
        .reset_index(name="quantidade_registros")
    )

    return {
        "base": base,
        "invalidos_tempo": invalidos_tempo,
        "duplicatas": duplicatas,
        "ausentes": ausentes,
        "suspeitos": valores_suspeitos,
        "resumo": resumo,
        "cobertura": cobertura
    }


# ============================================================
# 8. EXPORTAÇÃO
# ============================================================

def exportar_resultados(resultados):

    base = resultados["base"]

    base.to_csv(
        PASTA_PROCESSED / "meteorologia_baixada_limpa.csv",
        index=False,
        encoding="utf-8-sig"
    )

    base.to_parquet(
        PASTA_PROCESSED / "meteorologia_baixada_limpa.parquet",
        index=False
    )

    arquivos = {
        "registros_temporais_invalidos.csv":
            resultados["invalidos_tempo"],

        "duplicatas_identificadas.csv":
            resultados["duplicatas"],

        "dados_ausentes.csv":
            resultados["ausentes"],

        "valores_suspeitos.csv":
            resultados["suspeitos"],

        "resumo_estatistico.csv":
            resultados["resumo"],

        "cobertura_mensal.csv":
            resultados["cobertura"]
    }

    for nome, dataframe in arquivos.items():

        dataframe.to_csv(
            PASTA_QUALITY / nome,
            index=False,
            encoding="utf-8-sig"
        )

    print("\nARQUIVOS EXPORTADOS!")


# ============================================================
# 9. EXECUÇÃO
# ============================================================

def executar_pipeline():

    print("=" * 65)
    print("ETL METEOROLOGIA - PORTO DE SANTOS")
    print("=" * 65)

    print("\nETAPA 1 - CATÁLOGO")

    catalogo, catalogo_baixada = construir_catalogo()

    print("\nEstações encontradas na Baixada:")

    if catalogo_baixada.empty:
        print("Nenhuma estação identificada.")
    else:
        print(
            catalogo_baixada[
                [
                    "ano_zip",
                    "estacao",
                    "uf",
                    "codigo_estacao",
                    "latitude",
                    "longitude"
                ]
            ].to_string(index=False)
        )

    print("\nETAPA 2 - EXTRAÇÃO SELETIVA")

    base = extrair_baixada(catalogo_baixada)

    print("\nRegistros extraídos:", len(base))

    print("\nETAPA 3 - LIMPEZA E QUALIDADE")

    resultados = limpar_e_diagnosticar(base)

    print(
        "Registros finais:",
        len(resultados["base"])
    )

    print(
        "Duplicatas identificadas:",
        len(resultados["duplicatas"])
    )

    print(
        "Datas/horários inválidos:",
        len(resultados["invalidos_tempo"])
    )

    print("\nETAPA 4 - EXPORTAÇÃO")

    exportar_resultados(resultados)

    print("\nPROCESSAMENTO FINALIZADO!")


if __name__ == "__main__":
    executar_pipeline()
