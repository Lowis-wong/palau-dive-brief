import pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt, matplotlib.dates as mdates
plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.color':'#e6e4de','grid.linewidth':.6,'figure.dpi':150,
 'axes.edgecolor':'#8e8d86','xtick.color':'#52514e','ytick.color':'#52514e','axes.labelcolor':'#3d3c38'})
BLUE='#2a78d6'; ORANGE='#eb6834'; INK='#0b0b0b'; GREY='#9a9891'; TEAL='#1baf7a'
D='/tmp/claude-0/-home-claude/b47c3945-135e-5a09-b47f-d3b816dc4e1f/scratchpad/p1/'
wl=pd.read_csv(D+'wl.csv.gz',index_col=0,parse_dates=True)['2023':'2025']
L=(wl.tide_m-wl.tide_m.mean())
rng=(wl.tide_m.resample('1D').max()-wl.tide_m.resample('1D').min())
r25=rng['2025']; neap=r25[(r25>0.85)&(r25<0.95)].index[0]; spring=r25[r25>1.9].index[0]
models={'EOT20':'EOT20','GOT4_10':'GOT4.10c','GOT5_6':'GOT5.6'}
O={k:pd.read_csv(D+f'{k}_sp.csv.gz',index_col=0,parse_dates=True)['W_ulong'] for k in models}; O={k:v-v.mean() for k,v in O.items()}
fig,ax=plt.subplots(4,1,figsize=(7.2,13),gridspec_kw={'height_ratios':[1,1,1.1,1]})
for i,(day,name) in enumerate(((spring,f'Spring tide ({spring:%d %b %Y}, range {rng[spring]:.2f} m)'),(neap,f'Neap tide ({neap:%d %b %Y}, range {rng[neap]:.2f} m)'))):
    a=day-pd.Timedelta(hours=3); b=a+pd.Timedelta(hours=30)
    l=L[a:b].resample('6min').interpolate('cubic'); o=O['EOT20'][a:b].resample('6min').interpolate('cubic')
    t=l.index+pd.Timedelta(hours=9); hd=(l-o)*100
    ax[i].fill_between(t,0,hd.clip(upper=0),color=BLUE,alpha=.25,lw=0,label='ocean higher → water flows IN')
    ax[i].fill_between(t,0,hd.clip(lower=0),color=ORANGE,alpha=.25,lw=0,label='lagoon higher → water flows OUT')
    ax[i].plot(t,hd,color=INK,lw=1.6,label='lagoon − ocean')
    ax[i].axhline(2.8,color=TEAL,lw=1.2,ls='--'); ax[i].text(t[-1],3.4,'strong west swell ≈ +2.8 cm',color=TEAL,fontsize=7.8,ha='right')
    ax[i].axhline(0,color='#8e8d86',lw=.8); ax[i].set_ylim(-22,22); ax[i].set_ylabel('cm')
    ax[i].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M')); ax[i].set_title(f'{i+1}  {name}: lagoon minus open ocean',loc='left',fontweight='bold')
    if i==0: ax[i].legend(frameon=False,fontsize=7.8,loc='lower left')
ax[1].set_xlabel('Palau time')
bins=[0,1.0,1.3,1.6,1.9,3]; labs=['<1.0\n(neap)','1.0–1.3','1.3–1.6','1.6–1.9','>1.9\n(spring)']; x=np.arange(5)
for k,c,mk in zip(models,(BLUE,ORANGE,GREY),('o','s','^')):
    hd=(L-O[k]).dropna().resample('6min').interpolate('cubic'); s=np.sign(hd.values); zc=np.where(np.diff(s)!=0)[0]
    pk=[(hd.index[a+1+np.argmax(np.abs(hd.values[a+1:b+1]))],np.max(np.abs(hd.values[a+1:b+1]))) for a,b in zip(zc[:-1],zc[1:]) if b-a>20]
    P=pd.DataFrame(pk,columns=['t','pk']).set_index('t'); P['rng']=rng.reindex(P.index.floor('D')).values
    g=P.groupby(pd.cut(P.rng,bins),observed=True).pk.median()*100
    ax[2].plot(x,g.values,color=c,marker=mk,lw=1.6,ms=5,label=models[k])
for v,lab in ((0.7,'weak +0.7'),(1.7,'moderate +1.7'),(2.8,'strong +2.8')):
    ax[2].axhline(v,color=TEAL,lw=1,ls='--')
ax[2].text(4.3,3.2,'west swell pump: +0.7 / +1.7 / +2.8 cm',color=TEAL,fontsize=7.5,ha='right')
ax[2].set_xticks(x,labs); ax[2].set_ylim(0,20); ax[2].set_ylabel('peak difference, cm'); ax[2].set_xlabel('daily tidal range at Malakal (m)')
ax[2].legend(frameon=False,fontsize=8,loc='upper left',title='ocean tide model',title_fontsize=8)
ax[2].set_title('3  How hard the tide pushes vs. how much the swell lifts',loc='left',fontweight='bold')
hd=(L-O['EOT20']).dropna().resample('6min').interpolate('cubic'); zc=np.where(np.diff(np.sign(hd.values))!=0)[0]; zc=zc[(zc>2)&(zc<len(hd)-3)]
S=pd.DataFrame({'sl':np.abs((hd.values[zc+2]-hd.values[zc-2])/0.4)*100},index=hd.index[zc]); S['rng']=rng.reindex(S.index.floor('D')).values
sl=S.groupby(pd.cut(S.rng,bins),observed=True).sl.median(); w=.36
for off,v,c,lab in ((-w/2,1.7,'#8fb5e8','moderate swell (+1.7 cm)'),(w/2,2.8,BLUE,'strong swell (+2.8 cm)')):
    vals=v/sl.values*60; ax[3].bar(x+off,vals,width=w,color=c,label=lab)
    for xi,vv in zip(x+off,vals): ax[3].text(xi,vv+1.5,f'{vv:.0f}',ha='center',fontsize=8)
ax[3].set_xticks(x,labs); ax[3].set_ylabel('minutes'); ax[3].set_xlabel('daily tidal range at Malakal (m)'); ax[3].legend(frameon=False,fontsize=8); ax[3].set_ylim(0,80)
ax[3].set_title('4  Incoming starts later and ends earlier, each by about',loc='left',fontweight='bold')
fig.tight_layout(h_pad=1.8); fig.savefig(D+'fig/project1_lagoon_vs_ocean.png',facecolor='white'); print('ok')
