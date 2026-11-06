"""ETL de volume pedagiado horário e diário (2021-2026).
Execute: python src/trafego/clean_trafego.py
Passo 1 gera reports/trafego/inventario_pracas_lotes.csv.
Preencha data/config/trafego_pracas_acesso.csv com PRACA e/ou LOTE verificados.
Passo 2 execute novamente, para gerar os recortes de acesso ao porto.
NÃO mescla diário + horário (seria dupla contagem).
"""
from pathlib import Path
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data/raw/trafego'
OUT = ROOT / 'data/processed/trafego'
REPORT = ROOT / 'reports/trafego'
CONFIG = ROOT / 'data/config/trafego_pracas_acesso.csv'
CHUNKSIZE = 40_000
for p in [OUT,REPORT,CONFIG.parent]: p.mkdir(parents=True, exist_ok=True)

def norm(v):
    if pd.isna(v): return ''
    v = unicodedata.normalize('NFKD',str(v)).encode('ascii','ignore').decode().upper().strip()
    return re.sub(r'\s+',' ',v)

def parse_num(s):
    # aceita 1234, 1234.0, 1.234 e 1.234,00
    a=s.astype('string').str.strip().str.replace(' ','',regex=False)
    comma=a.str.contains(',',regex=False,na=False)
    a=a.where(~comma,a.str.replace('.','',regex=False).str.replace(',','.',regex=False))
    return pd.to_numeric(a,errors='coerce')

def read_chunks(path):
    # primeiro verifica separador e codificacao pelo cabecalho
    encoding=None; sep=None
    for enc in ('utf-8-sig','utf-8','latin-1'):
        try:
            with path.open(encoding=enc) as f: sample=f.read(8192)
            try: sep=csv.Sniffer().sniff(sample,delimiters=';,|\t').delimiter
            except csv.Error: sep=';' if sample.splitlines()[0].count(';')>sample.splitlines()[0].count(',') else ','
            encoding=enc;break
        except UnicodeDecodeError:pass
    if encoding is None:raise ValueError('Codificacao nao reconhecida')
    return pd.read_csv(path,sep=sep,encoding=encoding,dtype='string',chunksize=CHUNKSIZE,on_bad_lines='warn',low_memory=False)

def load_filter():
    if not CONFIG.exists():
        CONFIG.write_text('PRACA,LOTE,JUSTIFICATIVA\n',encoding='utf-8-sig')
        return set(),set()
    c=pd.read_csv(CONFIG,dtype='string').fillna('')
    for k in ('PRACA','LOTE'):
        if k not in c.columns:raise ValueError(f'Config deve conter coluna {k}')
    return {norm(x) for x in c.PRACA if norm(x)},{norm(x) for x in c.LOTE if norm(x)}

def converter_datas(serie):
    """
    Converte datas sem confundir dia e mês.

    Formatos aceitos:
    YYYY-MM-DD
    YYYY/MM/DD
    DD/MM/YYYY
    DD-MM-YYYY
    """

    valores = serie.astype("string").str.strip()

    resultado = pd.Series(
        pd.NaT,
        index=serie.index,
        dtype="datetime64[ns]"
    )

    formatos = [
        (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d"),
        (r"^\d{4}/\d{2}/\d{2}$", "%Y/%m/%d"),
        (r"^\d{2}/\d{2}/\d{4}$", "%d/%m/%Y"),
        (r"^\d{2}-\d{2}-\d{4}$", "%d-%m-%Y"),
    ]

    for padrao, formato in formatos:
        mascara = valores.str.fullmatch(padrao).fillna(False)

        if mascara.any():
            resultado.loc[mascara] = pd.to_datetime(
                valores.loc[mascara],
                format=formato,
                errors="coerce"
            )

    return resultado

def main():
    files=sorted(RAW.rglob('*.csv'))
    files=[p for p in files if ('volume_pedagiado' in str(p.parent).lower() or 'volume_trafego_diario' in str(p.parent).lower())]
    if not files:raise FileNotFoundError(f'Nao ha CSV em {RAW}')
    plazas,lots=load_filter()
    print(f'Arquivos: {len(files)} | filtro de pracas: {len(plazas)} | filtro de lotes: {len(lots)}',flush=True)
    inventories=defaultdict(lambda: {'linhas':0,'arquivos':set()})
    quality=Counter(); file_report=[]; regional_rows=Counter()
    output_paths={'horario':OUT/'volume_pedagiado_acesso_horario.csv','diario':OUT/'volume_trafego_acesso_diario.csv'}
    for p in output_paths.values():
        if p.exists():p.unlink()
    for i,path in enumerate(files,1):
        rel=str(path.relative_to(RAW)).replace('\\','/')
        kind='diario' if 'volume_trafego_diario' in rel.lower() else 'horario'
        year_match=re.search(r'20(?:2[1-6])',rel)
        year=int(year_match.group(0)) if year_match else None
        rows_in=0; selected=0; bad_date=0
        try:
            for chunk in read_chunks(path):
                chunk.columns=[norm(x).replace(' ','_') for x in chunk.columns]
                needed={'DATA','LOTE','PRACA','SENTIDO'}
                if not needed.issubset(chunk.columns):raise ValueError(f'Colunas obrigatorias ausentes: {needed-set(chunk.columns)}')
                rows_in+=len(chunk)
                # Inventário de valores observados sem tentar adivinhar geografia.
                pairs=chunk.groupby(['LOTE','PRACA'],dropna=False).size()
                for (lot,plaza),n in pairs.items():
                    key=(kind,year,norm(lot),norm(plaza));inventories[key]['linhas']+=int(n);inventories[key]['arquivos'].add(path.name)
                if not plazas and not lots:continue
                match=pd.Series(False,index=chunk.index)
                if plazas:match|=chunk.PRACA.map(norm).isin(plazas)
                if lots:match|=chunk.LOTE.map(norm).isin(lots)
                sub=chunk.loc[match].copy()
                if sub.empty:continue
                sub['DATA'] = converter_datas(sub['DATA']).dt.strftime('%Y-%m-%d')
                bad=sub['DATA'].isna().sum();bad_date+=int(bad)
                sub=sub.loc[sub.DATA.notna()].copy()
                if sub.empty:continue
                for cat in ['LOTE','PRACA','SENTIDO','TIPO_CABINE','TIPO_PASSAGEM','TP_PAGAMENTO']:
                    if cat in sub:sub[cat]=sub[cat].astype('string').str.strip()
                value_cols=[c for c in sub if c.startswith(('LEVE-EIXOS_','MOTO-EIXOS_','PESADO-EIXOS_')) or c=='ESPECIAL']
                for col in value_cols:
                    sub[col]=parse_num(sub[col])
                # Preserva valores indefinidos; nao converte ausente em zero.
                sub['TOTAL_VEICULOS_INFORMADOS']=sub[value_cols].sum(axis=1,min_count=1)
                sub['VEICULOS_PESADOS_INFORMADOS']=sub[[c for c in value_cols if c.startswith('PESADO-')]].sum(axis=1,min_count=1)
                sub['VEICULOS_LEVES_INFORMADOS']=sub[[c for c in value_cols if c.startswith('LEVE-')]].sum(axis=1,min_count=1)
                sub['MOTOS_INFORMADAS']=sub[[c for c in value_cols if c.startswith('MOTO-')]].sum(axis=1,min_count=1)
                sub['ANO_ARQUIVO']=year;sub['TIPO_BASE']=kind;sub['ARQUIVO_ORIGEM']=path.name
                # As fontes diferem em quantidade de categorias; os indicadores comuns ficam no output.
                common=['DATA','HORA','LOTE','PRACA','SENTIDO','TIPO_CABINE','TIPO_PASSAGEM','TP_PAGAMENTO','TOTAL_VEICULOS_INFORMADOS','VEICULOS_PESADOS_INFORMADOS','VEICULOS_LEVES_INFORMADOS','MOTOS_INFORMADAS','ANO_ARQUIVO','TIPO_BASE','ARQUIVO_ORIGEM']
                if kind=='diario':sub['HORA']=pd.NA
                for col in common:
                    if col not in sub:sub[col]=pd.NA
                output=sub[common]
                target=output_paths[kind]
                output.to_csv(target,mode='a',index=False,encoding='utf-8-sig' if not target.exists() else 'utf-8',header=not target.exists())
                selected+=len(output);regional_rows[kind]+=len(output)
        except Exception as exc:
            quality['arquivos_com_erro']+=1;print('ERRO',path.name,str(exc),flush=True)
            file_report.append({'arquivo':rel,'tipo':kind,'ano':year,'linhas_lidas':rows_in,'selecionadas':selected,'datas_invalidas_no_recorte':bad_date,'erro':str(exc)})
            continue
        file_report.append({'arquivo':rel,'tipo':kind,'ano':year,'linhas_lidas':rows_in,'selecionadas':selected,'datas_invalidas_no_recorte':bad_date,'erro':''})
        print(f'[{i}/{len(files)}] {path.name}: {rows_in:,} linhas; {selected:,} selecionadas',flush=True)
    inv=pd.DataFrame([{'tipo_base':kind,'ano':year,'lote':lot,'praca':plaza,'registros':v['linhas'],'arquivos':'; '.join(sorted(v['arquivos']))} for (kind,year,lot,plaza),v in inventories.items()])
    inv.to_csv(REPORT/'inventario_pracas_lotes.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(file_report).to_csv(REPORT/'relatorio_arquivos.csv',index=False,encoding='utf-8-sig')
    summary={'arquivos_encontrados':len(files),'arquivos_com_erro':quality['arquivos_com_erro'],'filtro_pracas':sorted(plazas),'filtro_lotes':sorted(lots),'registros_por_base':dict(regional_rows),'alertas':['Filtro por PRACA/LOTE precisa de validacao geografica externa.','Nao somar fontes diaria e horaria: podem retratar o mesmo trafego.','Total de categorias em 2021-2023 e 2024+ nao e totalmente equivalente.']}
    (REPORT/'resumo_qualidade.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('\nInventario:',REPORT/'inventario_pracas_lotes.csv')
    if not plazas and not lots:print('SEM FILTRO VALIDADO: configure data/config/trafego_pracas_acesso.csv e execute novamente. Nenhum dado foi indevidamente rotulado como Baixada.')
    else:print('Saidas:',*output_paths.values(),sep='\n')

if __name__=='__main__':main()
