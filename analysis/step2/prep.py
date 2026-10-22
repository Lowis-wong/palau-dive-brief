import pandas as pd, numpy as np
D='/tmp/claude-0/-home-claude/b47c3945-135e-5a09-b47f-d3b816dc4e1f/scratchpad/s2/'
def load():
    wl=pd.read_csv(D+'wl_hourly.csv.gz',index_col=0,parse_dates=True)
    F=pd.read_csv(D+'forcing_all.csv.gz',index_col=0,parse_dates=True)
    h=pd.DataFrame(index=wl.index)
    h['setup']=wl.setup_m; h['resid']=wl.resid_m; h['r30']=wl.resid_30d_m
    F=F.reindex(h.index)
    for p in 'WES':
        h[f'H{p}']=F[f'wave_{p}_wave_height']; h[f'D{p}']=F[f'wave_{p}_wave_direction']; h[f'T{p}']=F[f'wave_{p}_wave_period']
    spd=F.wx_koror_wind_speed_10m; dr=np.deg2rad(F.wx_koror_wind_direction_10m)
    h['wind']=spd; h['u']=-spd*np.sin(dr); h['v']=-spd*np.cos(dr)  # toward east / toward north, kn
    h['p']=F.wx_koror_pressure_msl; h['rain']=F.wx_koror_precipitation
    # west-point waves split by where they come from
    fromW=(h.DW>=180)&(h.DW<360)
    h['HW_fromW']=h.HW.where(fromW,0.0); h['HW_fromE']=h.HW.where(~fromW,0.0)
    h['PW']=h.HW**2*h.TW; h['PE']=h.HE**2*h.TE
    return h
def godin(s):
    return s.rolling(24,center=True,min_periods=18).mean().rolling(24,center=True,min_periods=18).mean().rolling(25,center=True,min_periods=19).mean()
def anom(s, days=30):
    return s - s.rolling(24*days,center=True,min_periods=24*10).mean()
