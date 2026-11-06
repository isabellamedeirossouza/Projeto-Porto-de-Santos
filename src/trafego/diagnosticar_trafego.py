from pathlib import Path
import pandas as pd
import csv

ROOT = Path(__file__).resolve().parents[2]

PASTA_RAW = ROOT / "data" / "raw" / "trafego"
PASTA_REPORTS = ROOT / "reports" / "trafego"

PASTA_REPORTS.mkdir(parents=True, exist_ok=True)

arquivos = sorted(PASTA_RAW.rglob("*.csv"))

print(f"Arquivos encontrados: {len(arquivos)}")

resultados = []

for arquivo in arquivos:

    print(f"\nAnalisando: {arquivo.name}")

    try:
        # Ler somente uma amostra para não carregar arquivos gigantes
        amostra = None

        for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                with arquivo.open(
                    "r",
                    encoding=encoding,
                    newline=""
                ) as f:
                    trecho = f.read(8192)
                    f.seek(0)

                    try:
                        separador = csv.Sniffer().sniff(
                            trecho,
                            delimiters=";,\t|"
                        ).delimiter
                    except csv.Error:
                        separador = ";"

                amostra = pd.read_csv(
                    arquivo,
                    sep=separador,
                    encoding=encoding,
                    nrows=5,
                    dtype="string"
                )
                break

            except (UnicodeDecodeError, pd.errors.ParserError):
                continue

        if amostra is None:
            raise ValueError("Não foi possível interpretar o CSV.")

        resultados.append({
            "arquivo": arquivo.name,
            "pasta": str(arquivo.parent.relative_to(PASTA_RAW)),
            "tamanho_mb": round(
                arquivo.stat().st_size / (1024 ** 2), 2
            ),
            "quantidade_colunas": len(amostra.columns),
            "colunas": " | ".join(amostra.columns)
        })

        print("Colunas:", list(amostra.columns))

    except Exception as erro:
        print("ERRO:", erro)

        resultados.append({
            "arquivo": arquivo.name,
            "pasta": str(arquivo.parent.relative_to(PASTA_RAW)),
            "tamanho_mb": round(
                arquivo.stat().st_size / (1024 ** 2), 2
            ),
            "quantidade_colunas": None,
            "colunas": f"ERRO: {erro}"
        })

diagnostico = pd.DataFrame(resultados)

saida = PASTA_REPORTS / "diagnostico_arquivos_trafego.csv"

diagnostico.to_csv(
    saida,
    index=False,
    encoding="utf-8-sig"
)

print("\nDiagnóstico concluído!")
print("Relatório salvo em:", saida)