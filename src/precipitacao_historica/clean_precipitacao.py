from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

ENTRADA = (
    ROOT / "data" / "raw" / "precipitacao_historica"
    / "planilha_media_30anos_psite-alt.csv"
)

SAIDA = ROOT / "data" / "processed" / "precipitacao_historica"
REPORTS = ROOT / "reports" / "precipitacao_historica"

SAIDA.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)

MESES = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARÇO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}


def converter_numero(serie):
    valores = (
        serie.astype("string")
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )

    return pd.to_numeric(valores, errors="coerce")


def main():
    if not ENTRADA.exists():
        raise FileNotFoundError(
            f"Arquivo original não encontrado: {ENTRADA}"
        )

    original = pd.read_csv(
        ENTRADA,
        sep=";",
        encoding="utf-8-sig",
        dtype="string"
    )

    original.columns = original.columns.str.strip()

    total_original = len(original)

    # Separar os meses da linha TOTAL/ANO
    meses = original[
        original["MÊS"].isin(MESES)
    ].copy()

    total_fonte = original[
        original["MÊS"] == "TOTAL/ANO"
    ].copy()

    # Converter a tabela de formato largo para longo
    colunas_anos = [
        coluna
        for coluna in original.columns
        if coluna.isdigit() and len(coluna) == 4
    ]

    df = meses.melt(
        id_vars=["MÊS"],
        value_vars=colunas_anos,
        var_name="ano",
        value_name="precipitacao_original"
    )

    df["ano"] = df["ano"].astype(int)
    df["mes"] = df["MÊS"].map(MESES)

    df["precipitacao_mensal"] = converter_numero(
        df["precipitacao_original"]
    )

    # Referência histórica fornecida na planilha
    referencia = meses[["MÊS", "30 anos"]].copy()

    referencia["referencia_30_anos"] = converter_numero(
        referencia["30 anos"]
    )

    df = df.merge(
        referencia[["MÊS", "referencia_30_anos"]],
        on="MÊS",
        how="left",
        validate="many_to_one"
    )

    # Criar data mensal
    df["data_mes"] = pd.to_datetime(
        dict(
            year=df["ano"],
            month=df["mes"],
            day=1
        )
    )

    # Diferença absoluta em relação à referência
    df["anomalia_absoluta"] = (
        df["precipitacao_mensal"]
        - df["referencia_30_anos"]
    )

    # Diferença percentual
    df["anomalia_percentual"] = (
        df["anomalia_absoluta"]
        / df["referencia_30_anos"].replace(0, float("nan"))
        * 100
    ).round(2)

    # Identificar registros suspeitos
    df["valor_negativo"] = df["precipitacao_mensal"] < 0

    df["valor_ausente"] = df["precipitacao_mensal"].isna()

    revisao = df[
        df["valor_negativo"] | df["valor_ausente"]
    ].copy()

    # Não inventar valores para meses ausentes
    df.loc[
        df["valor_negativo"],
        ["precipitacao_mensal", "anomalia_absoluta",
         "anomalia_percentual"]
    ] = float("nan")

    # Ordenação
    df = df.sort_values(
        ["ano", "mes"]
    ).reset_index(drop=True)

    # Base final mensal
    colunas_finais = [
        "data_mes",
        "ano",
        "mes",
        "MÊS",
        "precipitacao_mensal",
        "referencia_30_anos",
        "anomalia_absoluta",
        "anomalia_percentual"
    ]

    df_final = df[colunas_finais].rename(
        columns={"MÊS": "nome_mes"}
    )

    # Criar resumo anual
    resumo_anual = (
        df_final.groupby("ano")
        .agg(
            precipitacao_acumulada=(
                "precipitacao_mensal",
                lambda x: x.sum(min_count=1)
            ),
            meses_validos=("precipitacao_mensal", "count")
        )
        .reset_index()
    )

    resumo_anual["ano_completo"] = (
        resumo_anual["meses_validos"] == 12
    )

    # Validar totais anuais informados na fonte
    totais_fonte = []

    if not total_fonte.empty:
        for ano in colunas_anos:
            valor = converter_numero(
                total_fonte[ano]
            ).iloc[0]

            totais_fonte.append({
                "ano": int(ano),
                "total_informado_fonte": valor
            })

    conferencia = resumo_anual.merge(
        pd.DataFrame(totais_fonte),
        on="ano",
        how="left"
    )

    # Garantir que as duas colunas sejam numéricas
    conferencia["precipitacao_acumulada"] = pd.to_numeric(
        conferencia["precipitacao_acumulada"],
        errors="coerce"
    ).astype("Float64")

    conferencia["total_informado_fonte"] = pd.to_numeric(
        conferencia["total_informado_fonte"],
        errors="coerce"
    ).astype("Float64")

    # Calcular diferença entre o total calculado e o total da fonte
    conferencia["diferenca_total"] = (
        conferencia["precipitacao_acumulada"]
        - conferencia["total_informado_fonte"]
    ).round(2)

    # Exportações
    df_final.to_csv(
        SAIDA / "precipitacao_mensal_limpa.csv",
        index=False,
        encoding="utf-8-sig"
    )

    resumo_anual.to_csv(
        SAIDA / "precipitacao_resumo_anual.csv",
        index=False,
        encoding="utf-8-sig"
    )

    conferencia.to_csv(
        REPORTS / "conferencia_totais_anuais.csv",
        index=False,
        encoding="utf-8-sig"
    )

    revisao.to_csv(
        REPORTS / "precipitacao_para_revisao.csv",
        index=False,
        encoding="utf-8-sig"
    )

    relatorio = {
        "registros_originais": total_original,
        "registros_mensais": len(df_final),
        "anos_identificados": sorted(df_final["ano"].unique().tolist()),
        "valores_ausentes": int(df_final["precipitacao_mensal"].isna().sum()),
        "valores_negativos": int(df["valor_negativo"].sum()),
        "duplicatas_ano_mes": int(
            df_final.duplicated(["ano", "mes"]).sum()
        ),
        "observacao": (
            "A coluna 30 anos foi preservada como referência "
            "informada na fonte. O ano de 2026 é parcial."
        )
    }

    (REPORTS / "resumo_qualidade.json").write_text(
        json.dumps(relatorio, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print("Limpeza concluída!")
    print("Registros mensais:", len(df_final))
    print("Registros para revisão:", len(revisao))
    print("Arquivos salvos em:", SAIDA)

    print("\nConferência dos totais:")
    print(conferencia.tail(10).to_string(index=False))


if __name__ == "__main__":
    main()