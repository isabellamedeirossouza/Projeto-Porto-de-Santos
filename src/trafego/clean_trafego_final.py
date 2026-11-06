"""Audita os CSVs de tráfego pré-filtrados L22 e exporta tabelas analíticas.
Execute na raiz do repo: python src/trafego/clean_trafego_final.py
Não agrega bases diária e horária entre si.
"""
from pathlib import Path
import json
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
IN_DIR = ROOT / 'data' / 'processed' / 'trafego'
OUT_DIR = IN_DIR
REPORT_DIR = ROOT / 'reports' / 'trafego'
REPORT_DIR.mkdir(parents=True, exist_ok=True)
TODAY = pd.Timestamp('2026-11-05')
NUMS = ['TOTAL_VEICULOS_INFORMADOS','VEICULOS_PESADOS_INFORMADOS','VEICULOS_LEVES_INFORMADOS','MOTOS_INFORMADAS']
GROUP = ['DATA','PRACA','SENTIDO']
FILES = {'diario':'volume_trafego_acesso_diario.csv','horario':'volume_pedagiado_acesso_horario.csv'}
AGGS = {n:'sum' for n in NUMS}


def process(kind, filename):
    path = IN_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f'Arquivo não encontrado: {path}; execute clean_trafego.py primeiro.')
    annual = {}
    chunk_size = 100_000
    frames=[]
    counts={'linhas_lidas':0,'datas_invalidas':0,'datas_futuras':0,'ano_divergente':0,'volumes_invalidos':0,'diferenca_classes':0}
    flagged=[]
    for part in pd.read_csv(path, encoding='utf-8-sig',chunksize=chunk_size,low_memory=False):
        counts['linhas_lidas']+=len(part)
        part['DATA']=pd.to_datetime(part['DATA'], errors='coerce',format='%Y-%m-%d')
        for col in NUMS: part[col]=pd.to_numeric(part[col], errors='coerce')
        part['ANO_ARQUIVO']=pd.to_numeric(part['ANO_ARQUIVO'],errors='coerce')
        if kind=='horario':part['HORA']=pd.to_numeric(part['HORA'],errors='coerce')
        invalid_date=part['DATA'].isna()
        future=part['DATA'].gt(TODAY)
        year_bad=part['DATA'].notna() & part['ANO_ARQUIVO'].ne(part['DATA'].dt.year)
        invalid_volume=part[NUMS].isna().any(axis=1) | part[NUMS].lt(0).any(axis=1)
        diff=part['TOTAL_VEICULOS_INFORMADOS'].ne(part['VEICULOS_PESADOS_INFORMADOS']+part['VEICULOS_LEVES_INFORMADOS']+part['MOTOS_INFORMADAS'])
        for key,mask in [('datas_invalidas',invalid_date),('datas_futuras',future),('ano_divergente',year_bad),('volumes_invalidos',invalid_volume),('diferenca_classes',diff)]:counts[key]+=int(mask.sum())
        # Note: data source may legitimately define total differently across formats. Do not modify.
        issues=invalid_date|future|year_bad|invalid_volume
        if issues.any():
            flagged.append(part.loc[issues].assign(FLAG_DATA_INVALIDA=invalid_date.loc[issues],FLAG_FUTURO=future.loc[issues],FLAG_ANO_DIVERGENTE=year_bad.loc[issues],FLAG_VOLUME_INVALIDO=invalid_volume.loc[issues]))
        part=part.loc[~(invalid_date|future|invalid_volume)].copy()
        # Aggregate to date, plaza, direction, passage type, for more insight while limiting memory
        part['DATA']=part['DATA'].dt.strftime('%Y-%m-%d')
        keys=GROUP+['TIPO_PASSAGEM']+(['HORA'] if kind=='horario' else [])
        grouped=part.groupby(keys,dropna=False,as_index=False)[NUMS].sum(min_count=1)
        frames.append(grouped)
    if not frames:raise ValueError('Nenhum dado válido encontrado')
    keys=GROUP+['TIPO_PASSAGEM']+(['HORA'] if kind=='horario' else [])
    compact=pd.concat(frames,ignore_index=True).groupby(keys,dropna=False,as_index=False)[NUMS].sum(min_count=1)
    compact['ANO']=pd.to_datetime(compact['DATA']).dt.year
    compact['MES']=pd.to_datetime(compact['DATA']).dt.month
    compact['TOTAL_CATEGORIAS_CALCULADO']=compact['VEICULOS_PESADOS_INFORMADOS']+compact['VEICULOS_LEVES_INFORMADOS']+compact['MOTOS_INFORMADAS']
    compact['DIFERENCA_TOTAL_CLASSES']=compact['TOTAL_VEICULOS_INFORMADOS']-compact['TOTAL_CATEGORIAS_CALCULADO']
    compact.to_csv(OUT_DIR/f'trafego_{kind}_analitico_2021_2026.csv',index=False,encoding='utf-8-sig')
    # All pass types; for dashboard user may filter 'VALIDAS' as chargeable flows
    day=compact.groupby(GROUP,as_index=False)[NUMS].sum()
    day['ANO']=pd.to_datetime(day.DATA).dt.year
    day['MES']=pd.to_datetime(day.DATA).dt.month
    day.to_csv(OUT_DIR/f'trafego_{kind}_resumo_diario.csv',index=False,encoding='utf-8-sig')
    month=day.groupby(['ANO','MES','PRACA','SENTIDO'],as_index=False)[NUMS].sum()
    month.to_csv(OUT_DIR/f'trafego_{kind}_resumo_mensal.csv',index=False,encoding='utf-8-sig')
    if kind=='diario':
        summary=day.groupby(['ANO','MES'],as_index=False)[NUMS].sum()
        summary.to_csv(OUT_DIR/'trafego_serie_mensal_powerbi.csv',index=False,encoding='utf-8-sig')
    if flagged:
        pd.concat(flagged,ignore_index=True).to_csv(REPORT_DIR/f'trafego_{kind}_registros_sinalizados.csv',index=False,encoding='utf-8-sig')
    return counts, {'agregadas':len(compact),'dias_praca_sentido':len(day),'datas':int(day.DATA.nunique()),'primeira':str(day.DATA.min()),'ultima':str(day.DATA.max()),'pracas':sorted(day.PRACA.dropna().unique().tolist())}


def main():
    report={}
    for kind,fn in FILES.items():
        print(f'Processando {kind}: {fn}',flush=True)
        counts,detail=process(kind,fn)
        report[kind]={'contagens':counts,'resultado':detail}
        print(kind,counts,detail,flush=True)
    (REPORT_DIR/'auditoria_trafego_final.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Arquivos processados em:',OUT_DIR)

if __name__=='__main__':main()
