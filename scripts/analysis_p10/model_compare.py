# Local model comparison run on the v2 dump (cells_ratios.npz, cells_cv.csv.gz, rgb_deglint_median.tif from analysis-data/project10/sdb).
# Paths are relative to a local working folder holding those files under sdb/v2_* ; results in analysis/project10/model_compare.csv.
import sys, numpy as np, pandas as pd, rasterio, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0,'/home/claude/palau-dive-brief/scripts/analysis_p10'); import sdb
z=np.load('sdb/v2_cells_ratios.npz'); c=pd.read_csv('sdb/v2_cells_cv.csv.gz')
rgb=rasterio.open('sdb/rgb_deglint_median.tif').read().astype(float)/1e4
c['G']=rgb[1][c.iy,c.ix]
bad=(c.depth<3)&(c.G<0.05)
print('filtered cells', bad.sum())
PG=z['pg'].astype(np.float32); PR=z['pr'].astype(np.float32); V=z['valid']; Hh=z['h']
def run(deg, weighted, filt, grange=(1,22), label=''):
    sdb.GREEN_DEG=deg; sdb.GREEN_RANGE=grange
    use=~bad.values if filt else np.ones(len(c),bool)
    depth=c.depth.values; fold=c.fold.values
    preds=np.full(PG.shape,np.nan,np.float32); nacc=0
    for s in range(PG.shape[0]):
        t=depth+Hh[s]; cm=V[s]&use
        if weighted is False:
            orig=sdb.depth_weights; sdb.depth_weights=lambda y,**k: np.ones(len(y))
        fg,fr=sdb.fit_scene(PG[s],PR[s],t,cm)
        if fg is None or fg['n']<300 or fg['r2']<0.6 or fg['m1']<=0:
            if weighted is False: sdb.depth_weights=orig
            continue
        nacc+=1
        cap=np.percentile(t[cm&np.isfinite(PG[s])],99)+1
        for f in range(4):
            fgf,frf=sdb.fit_scene(PG[s],PR[s],t,cm&(fold!=f))
            if fgf is None or fgf['m1']<=0: continue
            te=(fold==f)&V[s]
            pi=sdb.predict(PG[s][te],PR[s][te],fgf,frf)
            preds[s,np.where(te)[0]]=np.where(pi<=cap,pi-Hh[s],np.nan)
        if weighted is False: sdb.depth_weights=orig
    med=np.nanmedian(preds,0)
    ev=~bad.values & np.isfinite(med)
    r=med[ev]-depth[ev]; dd=depth[ev]
    out={'label':label,'scenes':nacc}
    for a,b in [(0,2),(2,5),(5,10),(10,15),(15,20),(20,25),(0,15),(0,25)]:
        m=(dd>=a)&(dd<b); out[f'{a}-{b}']=f"{r[m].mean():+.2f}/{np.sqrt((r[m]**2).mean()):.2f}"
    return out, med
res=[]
for args in [(2,True,False,(1,22),'v2 quad w, no filter'),(2,True,True,(1,22),'quad w + filter'),(1,True,True,(1,22),'lin w + filter'),(1,False,True,(1,22),'lin unweighted + filter'),(2,True,True,(1,25),'quad w + filter, to 25'),(3,True,True,(1,25),'cubic w + filter, to 25')]:
    o,_=run(*args); res.append(o); print(o, flush=True)
pd.DataFrame(res).to_csv('exp_results.csv',index=False)

if __name__=='__main__' and len(sys.argv)>1:
    pass
