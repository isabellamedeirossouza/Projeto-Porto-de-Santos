"""ETL em lotes das panes veiculares SP 2021–2026.
Execução, a partir da raiz do projeto:
  python src/panes_veiculares/clean_panes_veiculares.py
Entrada: data/raw/panes_veiculares/panes_veiculares_YYYY.csv
Saídas: data/processed/panes_veiculares e reports/panes_veiculares
Não exclui casos suspeitos: marca QUALIDADE_STATUS e conserva valores originais em colunas *_ORIGINAL quando corrigidos.
"""
from pathlib import Path
import pandas as pd
import numpy as np
import json, re, unicodedata
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data/raw/panes_veiculares'
PROCESSED = ROOT / 'data/processed/panes_veiculares'
REPORTS = ROOT / 'reports/panes_veiculares'
YEARS = range(2021, 2027)
CHUNK_SIZE = 150_000
EXPORTAR_ESTADUAL_COMPLETO = False  # Ative apenas se precisar de ~3 milhões de linhas
BAIXADA = {'BERTIOGA','CUBATAO','GUARUJA','ITANHAEM','MONGAGUA','PERUIBE','PRAIA GRANDE','SANTOS','SAO VICENTE'}
COLS = ['ID','CONCESSIONARIA','RODOVIA','KM','SENTIDO','PERIMETRO','PANE_TIPO','MUNICIPIO','REGIAO_ADMINISTRATIVA','DATA','HORA','GRUPO_VEICULO','LATITUDE','LONGITUDE']
TEXT_COLS = ['ID','CONCESSIONARIA','RODOVIA','SENTIDO','PERIMETRO','PANE_TIPO','MUNICIPIO','REGIAO_ADMINISTRATIVA','GRUPO_VEICULO']

def normalizar(texto):
    if pd.isna(texto): return ''
    s = unicodedata.normalize('NFKD',str(texto))
    return ''.join(c for c in s if not unicodedata.combining(c)).strip().upper()

def limpar_lote(df, year, ids_vistos):
    df.columns = df.columns.str.strip().str.upper()
    if list(df.columns) != COLS:
        if set(df.columns) != set(COLS): raise ValueError(f'Colunas inesperadas no ano {year}: {df.columns.tolist()}')
        df = df[COLS]
    df = df.copy()
    for c in TEXT_COLS:
        df[c] = df[c].astype('string').str.strip().str.replace(r'\s+',' ',regex=True).replace('',pd.NA)
    df['ANO_ARQUIVO'] = year
    df['DATA_ORIGINAL'] = df['DATA'].astype('string')
    df['HORA_ORIGINAL'] = df['HORA'].astype('string')
    df['DATA'] = pd.to_datetime(df['DATA'].astype('string'),format='%Y-%m-%d',errors='coerce')
    t = pd.to_datetime(df['HORA'].astype('string'),format='%H:%M:%S',errors='coerce')
    df['DATA_HORA'] = pd.to_datetime(df['DATA_ORIGINAL'] + ' ' + df['HORA_ORIGINAL'],format='%Y-%m-%d %H:%M:%S',errors='coerce')
    df['KM'] = pd.to_numeric(df['KM'].astype('string').str.replace(',','.',regex=False),errors='coerce')
    for c in ['LATITUDE','LONGITUDE']:
        df[c] = pd.to_numeric(df[c].astype('string').str.replace(',','.',regex=False),errors='coerce')
    # Duplicação é avaliada pelo identificador do evento, sem descartar registros silenciosamente.
    duplicado_id=[]
    for val in df['ID']:
        if pd.isna(val): duplicado_id.append(False)
        else:
            duplicado_id.append(val in ids_vistos)
            ids_vistos.add(val)
    df['FLAG_ID_DUPLICADO'] = duplicado_id
    df['FLAG_DATA_INVALIDA'] = df['DATA'].isna()
    df['FLAG_HORA_INVALIDA'] = t.isna()
    df['FLAG_ANO_DIVERGENTE'] = df['DATA'].notna() & df['DATA'].dt.year.ne(year)
    df['FLAG_KM_INVALIDO'] = df['KM'].isna() | df['KM'].lt(0)
    df['FLAG_COORDENADA_INVALIDA'] = (df['LATITUDE'].isna() | df['LONGITUDE'].isna() | ~df['LATITUDE'].between(-90,90) | ~df['LONGITUDE'].between(-180,180))
    # Possível divergência geográfica: limites aproximados de SP; sinalizar apenas, não excluir.
    df['FLAG_COORDENADA_FORA_SP_APROX'] = (~df['FLAG_COORDENADA_INVALIDA'] & ~(df['LATITUDE'].between(-26,-19) & df['LONGITUDE'].between(-54,-44)))
    df['FLAG_ID_AUSENTE'] = df['ID'].isna()
    df['FLAG_MUNICIPIO_AUSENTE'] = df['MUNICIPIO'].isna()
    df['FLAG_RODOVIA_AUSENTE'] = df['RODOVIA'].isna()
    flags = [c for c in df if c.startswith('FLAG_')]
    df['QUALIDADE_STATUS'] = np.where(df[flags].any(axis=1),'REVISAR','OK')
    df['ANO'] = df['DATA'].dt.year.astype('Int64')
    df['MES'] = df['DATA'].dt.month.astype('Int64')
    df['DIA'] = df['DATA'].dt.day.astype('Int64')
    df['HORA_NUM'] = df['DATA_HORA'].dt.hour.astype('Int64')
    df['MUNICIPIO_NORMALIZADO'] = df['MUNICIPIO'].map(normalizar)
    df['RECORTE_BAIXADA'] = df['MUNICIPIO_NORMALIZADO'].isin(BAIXADA)
    # Corredores principais de acesso rodoviário ao Porto de Santos (Anchieta-Imigrantes).
    # SP-055 e demais rodovias permanecem na base regional, mas não são rotuladas como acesso principal.
    df['CORREDOR_PRINCIPAL_ACESSO'] = df['RODOVIA'].astype('string').str.upper().isin(['SP150', 'SP160'])
    return df

def main():
    PROCESSED.mkdir(parents=True,exist_ok=True)
    REPORTS.mkdir(parents=True,exist_ok=True)
    expected = [RAW / f'panes_veiculares_{y}.csv' for y in YEARS]
    faltam = [str(f) for f in expected if not f.exists()]
    if faltam: raise FileNotFoundError('Coloque os seis arquivos CSV em data/raw/panes_veiculares/:\n'+'\n'.join(faltam))
    # Limpa saídas de execução anterior para evitar duplicação ao acrescentar linhas.
    all_path = PROCESSED/'panes_veiculares_sp_limpo_2021_2026.csv.gz'
    reg_path = PROCESSED/'panes_veiculares_baixada_limpo_2021_2026.csv'
    review_path = REPORTS/'panes_para_revisao_baixada.csv'
    for f in [all_path,reg_path,review_path]:
        if f.exists(): f.unlink()
    yearly=[]; flags_total=Counter(); flags_region=Counter(); ids=set(); total=0; regional=0; review=0
    regional_parts=[]
    for year,path in zip(YEARS,expected):
        anual=0; regional_ano=0
        for raw in pd.read_csv(path,encoding='utf-8-sig',dtype='string',chunksize=CHUNK_SIZE,low_memory=False):
            total+=len(raw); anual+=len(raw)
            # Limpeza regional em memória: evita processar milhões de linhas fora do escopo.
            bruto_regional = raw.loc[raw['MUNICIPIO'].map(normalizar).isin(BAIXADA)].copy()
            if bruto_regional.empty: continue
            out=limpar_lote(bruto_regional,year,ids)
            flagcols=[c for c in out if c.startswith('FLAG_')]
            flags_total.update({c:int(out[c].sum()) for c in flagcols})  # flags da amostra regional
            if EXPORTAR_ESTADUAL_COMPLETO:
                out.to_csv(all_path,mode='a',index=False,header=not all_path.exists(),encoding='utf-8-sig',compression='gzip',date_format='%Y-%m-%d %H:%M:%S')
            reg=out.loc[out['RECORTE_BAIXADA']].copy()
            regional+=len(reg);regional_ano+=len(reg)
            flags_region.update({c:int(reg[c].sum()) for c in flagcols})
            if len(reg):
                reg.to_csv(reg_path,mode='a',index=False,header=not reg_path.exists(),encoding='utf-8-sig',date_format='%Y-%m-%d %H:%M:%S')
                rev=reg.loc[reg['QUALIDADE_STATUS'].eq('REVISAR')]
                review+=len(rev)
                if len(rev): rev.to_csv(review_path,mode='a',index=False,header=not review_path.exists(),encoding='utf-8-sig',date_format='%Y-%m-%d %H:%M:%S')
        yearly.append({'ano_arquivo':year,'registros_sp':anual,'registros_baixada':regional_ano})
        print(f'{year}: SP={anual:,} | Baixada={regional_ano:,}',flush=True)
    # Gera também a base de corredores principais usando apenas o recorte regional já tratado.
    # Leitura em lotes para não duplicar o consumo de memória.
    acesso_path = PROCESSED/'panes_corredores_principais_acesso_2021_2026.csv'
    if acesso_path.exists(): acesso_path.unlink()
    for parte in pd.read_csv(reg_path,encoding='utf-8-sig',chunksize=100_000,low_memory=False):
        filtro = parte['CORREDOR_PRINCIPAL_ACESSO'].astype('string').str.lower().eq('true')
        parte.loc[filtro].to_csv(acesso_path,mode='a',header=not acesso_path.exists(),index=False,encoding='utf-8-sig')
    pd.DataFrame(yearly).to_csv(REPORTS/'contagem_por_ano.csv',index=False,encoding='utf-8-sig')
    resumo={'total_estadual':total,'total_baixada':regional,'revisao_baixada':review,'flags_estadual':'nao_avaliadas: somente recorte regional validado','flags_baixada':dict(flags_region),'periodo':'2021–2026; ano de 2026 pode estar incompleto','regra_recorte':'MUNICIPIO normalizado em nove municípios da Baixada Santista','deduplicacao':'IDs repetidos no recorte regional sinalizados, não removidos; preservar auditoria','horario':'DATA e HORA sem informação inequívoca de fuso; não assumir UTC','interpretacao':'Panes de veículos em rodovias, não restrições de acesso marítimo ao porto.'}
    (REPORTS/'resumo_qualidade_panes.json').write_text(json.dumps(resumo,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Concluído. Total:',total,'Baixada:',regional,'Revisão Baixada:',review)

if __name__=='__main__': main()
