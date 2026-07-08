"""
generar_graficos.py — Gráficos matplotlib embebidos en el Word del informe
SINTIA. Extraído de generar.py (Fase 3 de profesionalización).
"""
import io

try:
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    MPL_OK = True
except ImportError:
    MPL_OK = False

from generar_utils import C_TRANS, C_NO_TRANS, C_TARDIO, C_CARGADO, C_LASTRE, fmt, n, pct_f, mes_label, mes_label_largo

def fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig); buf.seek(0)
    return buf
def grafico_torta(gT, gN, gTd):
    fig, ax = plt.subplots(figsize=(7,4), facecolor='white')
    valores=[gT,gN,gTd]; total=gT+gN+gTd
    labels=[f'Transmitidos\n{fmt(gT)}',f'No transmitidos\n{fmt(gN)}',f'Tard\u00edos\n{fmt(gTd)}']
    colors=[C_TRANS,C_NO_TRANS,C_TARDIO]
    # Si alguna porción es 0, se excluye del pie (si no, matplotlib le dibuja
    # una etiqueta y un "0,0%" que se superponen con las porciones vecinas).
    idx = [i for i,v in enumerate(valores) if v>0]
    valores=[valores[i] for i in idx]; labels=[labels[i] for i in idx]; colors=[colors[i] for i in idx]
    wedges,texts,autotexts = ax.pie(valores,labels=labels,colors=colors,
        autopct=lambda v: f"{v:.1f}%".replace(".",",") if total>0 else "",
        startangle=90,pctdistance=0.75,wedgeprops=dict(edgecolor='white',linewidth=2))
    for t in texts: t.set_fontsize(9)
    for t in autotexts: t.set_fontsize(9); t.set_fontweight('bold'); t.set_color('white')
    ax.set_title("Distribuci\u00f3n MICs por estado de transmisi\u00f3n",fontsize=11,fontweight='bold',pad=12)
    fig.tight_layout()
    return fig_to_bytes(fig)
def grafico_barras_apiladas(ev_total):
    meses=[mes_label(r["MES"]) for r in ev_total]
    carg=[n(r.get("CARGADO",0)) for r in ev_total]
    lastre=[n(r.get("LASTRE",0)) for r in ev_total]
    x=range(len(meses))
    fig,ax=plt.subplots(figsize=(7,4),facecolor='white')
    ax.bar(x,carg,color=C_CARGADO,label='Cargado',edgecolor='white',linewidth=0.5)
    ax.bar(x,lastre,bottom=carg,color=C_LASTRE,label='Lastre',edgecolor='white',linewidth=0.5)
    ax.set_xticks(list(x)); ax.set_xticklabels(meses,fontsize=9)
    ax.set_ylabel("Operaciones",fontsize=9)
    ax.set_title("Evoluci\u00f3n mensual de ingreso de camiones",fontsize=11,fontweight='bold')
    ax.legend(fontsize=9); ax.yaxis.grid(True,alpha=0.3); ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    for i,(c,l) in enumerate(zip(carg,lastre)):
        t=c+l; ax.text(i,t+t*0.01,fmt(t),ha='center',va='bottom',fontsize=8,fontweight='bold')
    fig.tight_layout(); return fig_to_bytes(fig)
def grafico_lineas_pct(ev_total,ev_trans,ev_tardio,ev_no_trans):
    mt={r["MES"]:n(r.get("CARGADO",0))+n(r.get("LASTRE",0)) for r in ev_total}
    mtr={r["MES"]:n(r.get("CARGADOS",0))+n(r.get("LASTRE",0)) for r in ev_trans}
    mtd={r["MES"]:n(r.get("CARGADOS",0))+n(r.get("LASTRE",0)) for r in ev_tardio}
    mnt={r["MES"]:n(r.get("CARGADOS",0))+n(r.get("LASTRE",0)) for r in ev_no_trans}
    periodos=sorted(mt.keys()); labels=[mes_label(p) for p in periodos]
    pt=[pct_f(mtr.get(p,0),mt.get(p,0)) for p in periodos]
    ptd=[pct_f(mtd.get(p,0),mt.get(p,0)) for p in periodos]
    pnt=[pct_f(mnt.get(p,0),mt.get(p,0)) for p in periodos]
    fig,ax=plt.subplots(figsize=(7,4),facecolor='white')
    x=range(len(labels))
    ax.plot(list(x),pt, marker='o',color=C_TRANS,   linewidth=2,label='Transmitidos',markersize=5)
    ax.plot(list(x),pnt,marker='s',color=C_NO_TRANS,linewidth=2,label='No transmitidos',markersize=5)
    ax.plot(list(x),ptd,marker='^',color=C_TARDIO,  linewidth=2,label='Tard\u00edos',markersize=5)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels,fontsize=9)
    ax.set_ylabel("%",fontsize=9); ax.set_ylim(0,110)
    ax.set_title("Evoluci\u00f3n mensual del estado de transmisi\u00f3n (%)",fontsize=11,fontweight='bold')
    ax.legend(fontsize=9); ax.yaxis.grid(True,alpha=0.3); ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    for i,t in enumerate(pt):
        ax.annotate(f"{t:.0f}%",(i,t),textcoords="offset points",xytext=(0,7),ha='center',fontsize=7,color=C_TRANS)
    for i,t in enumerate(pnt):
        ax.annotate(f"{t:.0f}%",(i,t),textcoords="offset points",xytext=(0,-11),ha='center',fontsize=7,color=C_NO_TRANS)
    for i,t in enumerate(ptd):
        ax.annotate(f"{t:.0f}%",(i,t),textcoords="offset points",xytext=(0,7),ha='center',fontsize=7,color=C_TARDIO)
    fig.tight_layout(); return fig_to_bytes(fig)
def grafico_rechazos_cat(rechazos_cat):
    cats=[r for r in rechazos_cat if r["Categoria"]!="TOTAL"][:10]
    if not cats: return None
    labels=[r["Categoria"] for r in reversed(cats)]
    valores=[n(r.get("Rechazos",0)) for r in reversed(cats)]
    fig,ax=plt.subplots(figsize=(7,max(3,len(cats)*0.5+1)),facecolor='white')
    bars=ax.barh(labels,valores,color=C_NO_TRANS,edgecolor='white',linewidth=0.5)
    for bar,val in zip(bars,valores):
        ax.text(bar.get_width()+bar.get_width()*0.02,bar.get_y()+bar.get_height()/2,
                fmt(val),va='center',fontsize=9,fontweight='bold')
    ax.set_xlabel("Cantidad",fontsize=9)
    ax.set_title("Rechazos por categor\u00eda",fontsize=11,fontweight='bold')
    ax.xaxis.grid(True,alpha=0.3); ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.tick_params(axis='y',labelsize=8); fig.tight_layout()
    return fig_to_bytes(fig)
def grafico_rechazos_mes(rechazos_mes):
    if not rechazos_mes: return None
    labels=[mes_label(r["periodo"]) for r in rechazos_mes]
    valores=[n(r.get("MIC_RECHAZOS",0)) for r in rechazos_mes]
    fig,ax=plt.subplots(figsize=(7,4),facecolor='white')
    ax.bar(range(len(labels)),valores,color=C_NO_TRANS,edgecolor='white',linewidth=0.5)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels,fontsize=9)
    ax.set_ylabel("MICs rechazados",fontsize=9)
    ax.set_title("Rechazos por mes",fontsize=11,fontweight='bold')
    for i,v in enumerate(valores):
        ax.text(i,v+v*0.02,fmt(v),ha='center',va='bottom',fontsize=9,fontweight='bold')
    ax.yaxis.grid(True,alpha=0.3); ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    fig.tight_layout(); return fig_to_bytes(fig)
def grafico_comparativo_meses(datos_ult, datos_ant, per_ult, per_ant):
    if not datos_ult or not datos_ant: return None
    categorias=['Transmitidos','No transmitidos','Tard\u00edos']
    ult=[n(datos_ult.get("trans",0)),n(datos_ult.get("no_trans",0)),n(datos_ult.get("tardio",0))]
    ant=[n(datos_ant.get("trans",0)),n(datos_ant.get("no_trans",0)),n(datos_ant.get("tardio",0))]
    x=range(len(categorias)); w=0.35
    fig,ax=plt.subplots(figsize=(7,4),facecolor='white')
    b1=ax.bar([i-w/2 for i in x],ant,w,label=mes_label_largo(per_ant),color='#ADB5BD',edgecolor='white')
    b2=ax.bar([i+w/2 for i in x],ult,w,label=mes_label_largo(per_ult),color=C_TRANS,edgecolor='white')
    ax.set_xticks(list(x)); ax.set_xticklabels(categorias,fontsize=10)
    ax.set_title(f"Comparativo: {mes_label_largo(per_ant)} vs {mes_label_largo(per_ult)}",fontsize=11,fontweight='bold')
    ax.legend(fontsize=9); ax.yaxis.grid(True,alpha=0.3); ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    for bar in list(b1)+list(b2):
        v=int(bar.get_height())
        if v>0: ax.text(bar.get_x()+bar.get_width()/2,v+v*0.02,fmt(v),ha='center',va='bottom',fontsize=8)
    fig.tight_layout(); return fig_to_bytes(fig)
