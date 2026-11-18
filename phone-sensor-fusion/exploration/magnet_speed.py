"""How fast the magnetic field changes, as a speed sensor for low speeds.

The steady field inside the car changes from metre to metre along the track (rails,
platform steel; exploration/magnet_gaps.py), so the faster the train moves the faster the
reading changes. Tested here, phone resting, against labelled speed:

 1. the field's rate of change by speed band: the full vector (needs a phone that isn't
    turning) and the field's strength alone (unaffected by turning the phone);
 2. telling slow movement from standing, one second at a time;
 3. how far along the track the field's slope stays the same (if it were smooth over a
    whole approach, speed could be recovered exactly; it isn't).

    python -m exploration.magnet_speed
"""

import numpy as np
from scipy import signal

from common.recording import Recording
from exploration.newdata import NEW, truth
from features.frames import uniform_imu
from labels import load


def auc(pos, neg):
    x=np.r_[pos,neg]; r=x.argsort().argsort()+1
    return (r[:len(pos)].sum()-len(pos)*(len(pos)+1)/2)/(len(pos)*len(neg))
for rid in NEW:
    rec=Recording(rid); lab,pos,direction=truth(rid); T=len(lab.v)
    tm,m=rec.mag; mu=uniform_imu(tm,m)[:T*100]
    lp=signal.sosfiltfilt(signal.butter(2,2.0,fs=100,output='sos'),mu,axis=0)
    rate_vec=np.linalg.norm(np.gradient(lp,0.01,axis=0),axis=1)           # µT/s, needs a phone that isn't turning
    size=np.linalg.norm(lp-np.median(lp,axis=0)*0,axis=1)
    dm=rec.device_motion; Bc=uniform_imu(dm['t'],dm['mag'])[:T*100]
    sz=signal.sosfiltfilt(signal.butter(2,2.0,fs=100,output='sos'),np.linalg.norm(Bc,axis=1))
    rate_mag=np.abs(np.gradient(sz,0.01))                                  # µT/s of |B|: unaffected by turning the phone
    sec=lambda x: x[:T*100].reshape(T,100).mean(1)
    rv,rm=sec(rate_vec),sec(rate_mag)
    ok=(lab.phone==0)&(lab.on_train==1)&lab.confident
    print("##",rid)
    bins=[(0,0.001),(0.2,1),(1,2),(2,4),(4,7),(7,10),(10,20),(20,40)]
    print("   speed band (m/s):        "+"  ".join(f"{a:g}-{b:g}".rjust(8) for a,b in bins))
    for name,x in (("|dB/dt| vector, µT/s",rv),("d|B|/dt, µT/s",rm)):
        print(f"   {name:22s} med: "+"  ".join(f"{np.median(x[ok&(lab.v>=a)&(lab.v<b)]):8.2f}" for a,b in bins))
    still=ok&(lab.v==0); 
    for a,b in ((0.2,1),(1,3),(3,7)):
        mv=ok&(lab.v>=a)&(lab.v<b)
        print(f"   telling {a:g}-{b:g} m/s from standing, one second at a time (AUC, 0.5 = chance): vector {auc(rv[mv],rv[still]):.2f}, |B| {auc(rm[mv],rm[still]):.2f}   (n={mv.sum()})")
    slow=ok&(lab.v>0.2)&(lab.v<10)
    print(f"   within 0.2-10 m/s: correlation of log rate with log speed: vector {np.corrcoef(np.log(rv[slow]),np.log(lab.v[slow]))[0,1]:+.2f}, |B| {np.corrcoef(np.log(rm[slow]+1e-3),np.log(lab.v[slow]))[0,1]:+.2f};"
          f" scatter of log10(rate/speed), i.e. of the unknown gradient: sd {np.std(np.log10(rv[slow]/lab.v[slow])):.2f} decades (a factor of {10**np.std(np.log10(rv[slow]/lab.v[slow])):.1f})")
    # how far does the spatial gradient stay the same? field against labelled position, 1 m grid, approaches to stops
    corr={k:[] for k in (2,5,10,20,50)}
    for st in load.stops(rid):
        for t0,sign in ((st.t_arrive,-1),(st.t_depart,1)):
            if not np.isfinite(t0): continue
            i=int(t0); sl=slice(i-40,i) if sign<0 else slice(i,i+40)
            if not (ok[sl].all()): continue
            # distance from the standing point at 100 Hz
            v100=np.repeat(lab.v[sl],100); d=np.cumsum(v100)*0.01
            if sign<0: d=d[-1]-d
            B=lp[sl.start*100:sl.stop*100]
            grid=np.arange(2,min(150,d.max()-1),1.0)
            if len(grid)<60: continue
            order=np.argsort(d); Bg=np.stack([np.interp(grid,d[order],B[order,k]) for k in range(3)],axis=1)
            g=np.gradient(Bg,1.0,axis=0)                                  # µT per m, vector
            for lag in corr:
                a_,b_=g[:-lag],g[lag:]
                corr[lag].append(np.sum(a_*b_)/np.sqrt(np.sum(a_*a_)*np.sum(b_*b_)))
    print("   does the spatial gradient persist? correlation of dB/ds with itself this far along (first 150 m from a stop): "+"  ".join(f"{k} m: {np.mean(v):+.2f}" for k,v in corr.items())+f"   ({len(corr[2])} approaches)")
