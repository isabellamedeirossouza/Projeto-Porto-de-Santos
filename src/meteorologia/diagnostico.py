
from pathlib import Path
from zipfile import ZipFile
import csv

ROOT = Path(__file__).resolve().parents[2]
PASTA_RAW = ROOT / "data" / "raw" / "meteorologia"

# Vamos inspecionar somente um CSV por enquanto.
zip_path = PASTA_RAW / "2021.zip"

with ZipFile(zip_path, "r") as z:

    arquivos = [
        nome for nome in z.namelist()
        if nome.lower().endswith(".csv")
        and not nome.startswith("__MACOSX/")
    ]

    print("TOTAL DE CSVs:", len(arquivos))

    # Procurar nomes que possam corresponder à Baixada.
    print("\nARQUIVOS COM NOMES RELEVANTES:")

    termos = [
        "BERTIOGA",
        "SANTOS",
        "GUARUJA",
        "CUBATAO",
        "ITANHAEM",
        "MONGAGUA",
        "PERUIBE",
        "PRAIA GRANDE",
        "SAO VICENTE"
    ]

    encontrados = [
        nome for nome in arquivos
        if any(termo in nome.upper() for termo in termos)
    ]

    for nome in encontrados[:30]:
        print(nome)

    # Escolher o primeiro CSV para inspecionar.
    nome_csv = arquivos[0]

    print("\n" + "=" * 80)
    print("ARQUIVO INSPECIONADO:", nome_csv)
    print("=" * 80)

    conteudo = z.read(nome_csv).decode(
        "latin1",
        errors="replace"
    )

    linhas = conteudo.splitlines()

    print("\nPRIMEIRAS 20 LINHAS DO ARQUIVO:\n")

    for i, linha in enumerate(linhas[:20]):
        print(f"Linha {i}: {linha}")

    print("\n" + "=" * 80)
    print("LINHAS QUE CONTÊM METADADOS:")
    print("=" * 80)

    for i, linha in enumerate(linhas[:10]):
        campos = next(csv.reader([linha], delimiter=";"))
        print(f"\nLinha {i}:")
        print(campos)
