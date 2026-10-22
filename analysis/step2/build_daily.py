import pandas as pd, numpy as np
from prep import *
h=load()
cols=['HW','HE','HS','HW_fromW','HW_fromE','u','v','wind','p','TW']
L={c:godin(h[c]) for c in cols}
out=pd.DataFrame({'setup':h.setup})
for c in cols:
    out[c]=L[c]; out[c+'_a']=anom(L[c])
out['rain72']=h.rain.rolling(72,min_periods=60).sum(); out['rain72_a']=anom(out.rain72)
out['ib']=-0.00993*out.p_a
out['s_noib']=out.setup-out.ib
out['DW']=h.DW
d=out.resample('1D').mean()
d['fromW_share']=((h.DW>=180)&(h.DW<360)).resample('1D').mean()
d.to_csv('/tmp/claude-0/-home-claude/b47c3945-135e-5a09-b47f-d3b816dc4e1f/scratchpad/s2/daily.csv.gz')
print(len(d.dropna(subset=['setup','HW'])))
