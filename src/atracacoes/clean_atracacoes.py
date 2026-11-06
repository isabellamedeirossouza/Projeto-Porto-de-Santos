from pathlib import Path
import json
import pandas as pd


# ============================================================
# CONFIGURAÇÕES
# ============================================================

DATA_INICIAL = pd.Timestamp("2021-01-01")
DATA_FINAL_EXCLUSIVA = pd.Timestamp("2027-01-01")

COLUNAS_DATA = ["ATRACACAO", "DESATRACACAO"]
COLUNAS_NUMERICAS = [
    "ANO",
    "MES",
    "NUMERO_VIAGEM",
    "QUANTIDADE_ATRACACAO",
]
COLUNAS_TEXTO_CATEGORICO = [
    "MERCADORIA",
    "PERFIL_EMBARCACAO",
    "NATUREZA_CARGA",
    "TIPO_VIAGEM",
]


# ============================================================
# LEITURA DA BASE
# ============================================================

def ler_csv(caminho):
    """Lê o CSV tentando as codificações mais comuns."""
    erros = []

    for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
        try:
            return pd.read_csv(
                caminho,
                encoding=encoding,
                low_memory=False
            )
        except UnicodeDecodeError as erro:
            erros.append(str(erro))

    raise ValueError(
        "Não foi possível ler o CSV com as codificações testadas."
    )


# ============================================================
# DIAGNÓSTICO DA BASE
# ============================================================

def diagnosticar(df):
    """Gera indicadores de qualidade por coluna."""
    linhas = []

    for coluna in df.columns:
        linhas.append({
            "coluna": coluna,
            "tipo": str(df[coluna].dtype),
            "total_registros": len(df),
            "valores_ausentes": int(df[coluna].isna().sum()),
            "percentual_ausente": round(
                df[coluna].isna().mean() * 100, 2
            ),
            "valores_distintos": int(df[coluna].nunique(dropna=True)),
        })

    return pd.DataFrame(linhas)


# ============================================================
# LIMPEZA PRINCIPAL
# ============================================================

def clean_data(df_original):
    """
    Limpa e valida os dados sem eliminar automaticamente
    registros que apenas apresentam campos suspeitos.

    Retorna:
        df_limpo: base filtrada e padronizada.
        df_revisao: registros que precisam de investigação.
        resumo: indicadores do processo.
        comparacao: indicadores antes e depois.
        diagnostico_antes: diagnóstico por coluna da base original.
        diagnostico_depois: diagnóstico por coluna da base limpa.
    """

    df = df_original.copy()

    colunas_obrigatorias = [
        "ATRACACAO",
        "DESATRACACAO",
        "ANO",
        "MES",
        "ANO_MES",
        "NUMERO_VIAGEM",
        "QUANTIDADE_ATRACACAO",
        "MERCADORIA",
        "PERFIL_EMBARCACAO",
        "NATUREZA_CARGA",
        "TIPO_VIAGEM",
    ]

    faltantes = [
        coluna for coluna in colunas_obrigatorias
        if coluna not in df.columns
    ]

    if faltantes:
        raise ValueError(
            f"Colunas esperadas não encontradas: {faltantes}"
        )

    total_original = len(df)
    colunas_originais = list(df.columns)

    # Guarda a posição original da linha no arquivo CSV.
    df["_LINHA_ARQUIVO"] = range(2, total_original + 2)

    # Guarda os valores originais para auditoria.
    df["_ANO_FONTE"] = df["ANO"].copy()
    df["_MES_FONTE"] = df["MES"].copy()
    df["_ANO_MES_FONTE"] = df["ANO_MES"].copy()
    df["_DESATRACACAO_FONTE"] = df["DESATRACACAO"].copy()

    # 1. Padronização dos textos.
    colunas_texto = df.select_dtypes(
        include=["object", "string"]
    ).columns.tolist()

    for coluna in colunas_texto:
        df[coluna] = (
            df[coluna]
            .astype("string")
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )

        df[coluna] = df[coluna].replace("", pd.NA)

    # Padroniza o uso de maiúsculas nas categorias.
    for coluna in COLUNAS_TEXTO_CATEGORICO:
        df[coluna] = df[coluna].str.upper()

    # 2. Conversão das datas.
    for coluna in COLUNAS_DATA:
        df[coluna] = pd.to_datetime(
            df[coluna],
            format="%d/%m/%Y %H:%M:%S",
            errors="coerce"
        )

    # 3. Conversão dos campos numéricos.
    for coluna in COLUNAS_NUMERICAS:
        df[coluna] = pd.to_numeric(
            df[coluna],
            errors="coerce"
        ).astype("Int64")

    # 4. Diagnóstico das inconsistências de ano, mês e ANO_MES.
    ano_fonte = pd.to_numeric(
        df["_ANO_FONTE"], errors="coerce"
    )
    mes_fonte = pd.to_numeric(
        df["_MES_FONTE"], errors="coerce"
    )
    ano_mes_fonte = pd.to_datetime(
        df["_ANO_MES_FONTE"], errors="coerce"
    )

    data_atracacao = df["ATRACACAO"]

    df["_ANO_INCONSISTENTE"] = (
        data_atracacao.notna()
        & ano_fonte.notna()
        & ano_fonte.ne(data_atracacao.dt.year)
    )

    df["_MES_INCONSISTENTE"] = (
        data_atracacao.notna()
        & mes_fonte.notna()
        & mes_fonte.ne(data_atracacao.dt.month)
    )

    df["_ANO_MES_INCONSISTENTE"] = (
        data_atracacao.notna()
        & ano_mes_fonte.notna()
        & (
            ano_mes_fonte.dt.to_period("M")
            != data_atracacao.dt.to_period("M")
        )
    )

    # Verifica datas que não puderam ser interpretadas.
    df["_ATRACACAO_INVALIDA"] = (
        df["_LINHA_ARQUIVO"].notna()
        & data_atracacao.isna()
    )

    df["_DESATRACACAO_INVALIDA"] = (
        df["_DESATRACACAO_FONTE"].notna()
        & df["DESATRACACAO"].isna()
    )

    # 5. Identificação de duplicatas exatas.
    # A linha técnica não entra na comparação.
    colunas_comparacao = colunas_originais
    duplicata_exata = df[colunas_comparacao].duplicated(keep="first")
    df["_DUPLICATA_EXATA"] = duplicata_exata

    total_duplicatas = int(duplicata_exata.sum())

    # Remove somente duplicatas exatas, depois de normalizar os dados.
    df = df.loc[~df["_DUPLICATA_EXATA"]].copy()

    # 6. Regras de validação.
    df["_DURACAO_NEGATIVA"] = (
        df["ATRACACAO"].notna()
        & df["DESATRACACAO"].notna()
        & (df["DESATRACACAO"] < df["ATRACACAO"])
    )

    df["_MERCADORIA_AUSENTE"] = df["MERCADORIA"].isna()

    df["_NATUREZA_VERIFICAR"] = (
        df["NATUREZA_CARGA"].eq("VERIFICAR").fillna(False)
    )

    df["_QUANTIDADE_INVALIDA"] = (
        df["QUANTIDADE_ATRACACAO"].isna()
        | df["QUANTIDADE_ATRACACAO"].le(0).fillna(False)
    )

    df["_VIAGEM_INVALIDA"] = (
        df["NUMERO_VIAGEM"].isna()
        | df["NUMERO_VIAGEM"].le(0).fillna(False)
    )

    # Reúne os motivos de revisão de cada registro.
    motivos = []

    regras_revisao = [
        ("ATRACACAO inválida ou ausente", "_ATRACACAO_INVALIDA"),
        ("DESATRACACAO inválida", "_DESATRACACAO_INVALIDA"),
        ("DESATRACACAO anterior à ATRACACAO", "_DURACAO_NEGATIVA"),
        ("ANO divergente da data de ATRACACAO", "_ANO_INCONSISTENTE"),
        ("MES divergente da data de ATRACACAO", "_MES_INCONSISTENTE"),
        ("ANO_MES divergente da data de ATRACACAO", "_ANO_MES_INCONSISTENTE"),
        ("MERCADORIA ausente", "_MERCADORIA_AUSENTE"),
        ("NATUREZA_CARGA marcada como VERIFICAR", "_NATUREZA_VERIFICAR"),
        ("QUANTIDADE_ATRACACAO ausente ou não positiva", "_QUANTIDADE_INVALIDA"),
        ("NUMERO_VIAGEM ausente ou não positivo", "_VIAGEM_INVALIDA"),
    ]

    for _, linha in df.iterrows():
        motivos_linha = [
            descricao
            for descricao, flag in regras_revisao
            if bool(linha[flag])
        ]
        motivos.append("; ".join(motivos_linha))

    df["_MOTIVO_REVISAO"] = motivos

    # 7. Define o período usando ATRACACAO.
    # Datas ausentes/inválidas não entram na base temporal,
    # mas continuam documentadas no relatório de revisão.
    dentro_periodo = (
        df["ATRACACAO"].ge(DATA_INICIAL)
        & df["ATRACACAO"].lt(DATA_FINAL_EXCLUSIVA)
    )

    data_invalida = df["ATRACACAO"].isna()

    df_periodo = df.loc[dentro_periodo].copy()

    # 8. Corrige ANO, MES e ANO_MES usando ATRACACAO como referência.
    # Os valores originais são preservados nas evidências.
    df_periodo["ANO"] = df_periodo["ATRACACAO"].dt.year.astype("Int64")
    df_periodo["MES"] = df_periodo["ATRACACAO"].dt.month.astype("Int64")
    df_periodo["ANO_MES"] = (
        df_periodo["ATRACACAO"].dt.to_period("M").dt.to_timestamp()
    )

    # 9. Uma duração negativa é inválida.
    # Preserva-se a atracação e torna-se a desatracação ausente
    # na base analítica; o valor da fonte fica no relatório de revisão.
    duracao_negativa_periodo = (
        df_periodo["DESATRACACAO"].notna()
        & df_periodo["ATRACACAO"].notna()
        & (df_periodo["DESATRACACAO"] < df_periodo["ATRACACAO"])
    )

    df_periodo.loc[duracao_negativa_periodo, "DESATRACACAO"] = pd.NaT

    # 10. Gera a tabela de revisão.
    # Inclui registros no período com problemas e registros cuja data
    # de atracação não pôde ser interpretada.
    df_revisao_origem = df.loc[
        dentro_periodo | data_invalida
    ].copy()

    df_revisao = df_revisao_origem.loc[
        df_revisao_origem["_MOTIVO_REVISAO"].ne("")
    ].copy()

    df_revisao["DESATRACACAO_ORIGINAL"] = (
        df_revisao["_DESATRACACAO_FONTE"]
    )
    df_revisao["ANO_ORIGINAL"] = df_revisao["_ANO_FONTE"]
    df_revisao["MES_ORIGINAL"] = df_revisao["_MES_FONTE"]
    df_revisao["ANO_MES_ORIGINAL"] = df_revisao["_ANO_MES_FONTE"]

    colunas_revisao = (
        ["_LINHA_ARQUIVO", "_MOTIVO_REVISAO"]
        + colunas_originais
        + [
            "ANO_ORIGINAL",
            "MES_ORIGINAL",
            "ANO_MES_ORIGINAL",
            "DESATRACACAO_ORIGINAL",
        ]
    )

    df_revisao = df_revisao[colunas_revisao].rename(columns={
        "_LINHA_ARQUIVO": "LINHA_ARQUIVO",
        "_MOTIVO_REVISAO": "MOTIVO_REVISAO",
    })

    # 11. Finaliza a base limpa.
    colunas_internas = [
        coluna for coluna in df_periodo.columns
        if coluna.startswith("_")
    ]

    df_limpo = df_periodo.drop(
        columns=colunas_internas,
        errors="ignore"
    ).copy()

    df_limpo = df_limpo[colunas_originais]

    # 12. Evidências antes/depois.
    diagnostico_antes = diagnosticar(df_original)
    diagnostico_depois = diagnosticar(df_limpo)

    comparacao = pd.DataFrame([
        {
            "indicador": "Registros",
            "antes": len(df_original),
            "depois": len(df_limpo),
        },
        {
            "indicador": "Colunas",
            "antes": df_original.shape[1],
            "depois": df_limpo.shape[1],
        },
        {
            "indicador": "Duplicatas exatas identificadas",
            "antes": int(df_original.duplicated().sum()),
            "depois": int(df_limpo.duplicated().sum()),
        },
        {
            "indicador": "MERCADORIA ausente",
            "antes": int(df_original["MERCADORIA"].isna().sum()),
            "depois": int(df_limpo["MERCADORIA"].isna().sum()),
        },
        {
            "indicador": "ATRACACAO ausente/inválida",
            "antes": int(
                pd.to_datetime(
                    df_original["ATRACACAO"],
                    format="%d/%m/%Y %H:%M:%S",
                    errors="coerce"
                ).isna().sum()
            ),
            "depois": int(df_limpo["ATRACACAO"].isna().sum()),
        },
        {
            "indicador": "DESATRACACAO ausente/inválida",
            "antes": int(
                pd.to_datetime(
                    df_original["DESATRACACAO"],
                    format="%d/%m/%Y %H:%M:%S",
                    errors="coerce"
                ).isna().sum()
            ),
            "depois": int(df_limpo["DESATRACACAO"].isna().sum()),
        },
    ])

    resumo = {
        "arquivo_origem": "exportacao_atracacao.csv",
        "periodo_inicio": str(DATA_INICIAL.date()),
        "periodo_fim_exclusivo": str(DATA_FINAL_EXCLUSIVA.date()),
        "registros_originais": int(total_original),
        "registros_apos_remocao_de_duplicatas": int(len(df)),
        "duplicatas_exatas_removidas": int(total_duplicatas),
        "registros_no_periodo": int(len(df_limpo)),
        "registros_para_revisao": int(len(df_revisao)),
        "registros_com_ano_inconsistente": int(
            df["_ANO_INCONSISTENTE"].sum()
        ),
        "registros_com_mes_inconsistente": int(
            df["_MES_INCONSISTENTE"].sum()
        ),
        "registros_com_ano_mes_inconsistente": int(
            df["_ANO_MES_INCONSISTENTE"].sum()
        ),
        "registros_com_duracao_negativa_no_periodo": int(
            duracao_negativa_periodo.sum()
        ),
        "registros_com_mercadoria_ausente_no_periodo": int(
            df_periodo["_MERCADORIA_AUSENTE"].sum()
        ),
        "observacao": (
            "Registros suspeitos são documentados para revisão. "
            "ANO, MES e ANO_MES são derivados de ATRACACAO na base final. "
            "DESATRACACAO anterior à ATRACACAO é anulada na base analítica, "
            "preservando o valor original no relatório de revisão."
        ),
    }

    return (
        df_limpo,
        df_revisao,
        resumo,
        comparacao,
        diagnostico_antes,
        diagnostico_depois,
    )


# ============================================================
# EXPORTAÇÃO DOS RESULTADOS
# ============================================================

def salvar_resultados(
    df_limpo,
    df_revisao,
    resumo,
    comparacao,
    diagnostico_antes,
    diagnostico_depois,
    root,
):
    processed_dir = root / "data" / "processed"
    reports_dir = root / "reports"

    processed_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    df_limpo.to_csv(
        processed_dir / "atracacoes_limpo_2021_2026.csv",
        index=False,
        encoding="utf-8-sig",
        date_format="%Y-%m-%d %H:%M:%S",
    )

    df_revisao.to_csv(
        reports_dir / "atracacoes_para_revisao.csv",
        index=False,
        encoding="utf-8-sig",
    )

    comparacao.to_csv(
        reports_dir / "comparacao_qualidade.csv",
        index=False,
        encoding="utf-8-sig",
    )

    diagnostico_antes.to_csv(
        reports_dir / "diagnostico_original.csv",
        index=False,
        encoding="utf-8-sig",
    )

    diagnostico_depois.to_csv(
        reports_dir / "diagnostico_limpo.csv",
        index=False,
        encoding="utf-8-sig",
    )

    (reports_dir / "resumo_qualidade.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Limpeza concluída.")
    print("Base limpa:", processed_dir / "atracacoes_limpo_2021_2026.csv")
    print("Registros para revisão:", reports_dir / "atracacoes_para_revisao.csv")
    print("Relatórios:", reports_dir)


# ============================================================
# EXECUÇÃO DIRETA DO SCRIPT
# ============================================================

if __name__ == "__main__":

    # Localiza a raiz do projeto porto-santos
    root = Path(__file__).resolve().parents[2]

    # Caminho do CSV original
    arquivo_entrada = (
        root
        / "data"
        / "raw"
        / "atracacoes"
        / "exportacao_atracacao.csv"
    )

    # Verifica se o arquivo existe
    if not arquivo_entrada.exists():
        raise FileNotFoundError(
            f"Arquivo não encontrado: {arquivo_entrada}"
        )

    print("Arquivo encontrado:", arquivo_entrada)

    # Leitura dos dados
    df_original = ler_csv(arquivo_entrada)

    print("Registros originais:", len(df_original))

    # Executa a limpeza
    resultados = clean_data(df_original)

    # Salva os arquivos tratados e relatórios
    salvar_resultados(
        *resultados,
        root=root
    )