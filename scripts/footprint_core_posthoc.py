"""POST-HOC diagnostic (written after seeing footprint_ms.py's result): core-conserved families.
For each non-generic fragment bin, take the features whose consensus fragments contain it, link
them by the same biotransformation deltas, and score the largest connected group sharing that
one core fragment. Everything else as in footprint_ms.py."""
import sys
from collections import defaultdict
sys.argv=[sys.argv[0], sys.argv[1]]
import footprint_ms_check as fm

def run(path):
    scans=list(fm.spectra(path)); feats=[]
    for s in sorted(scans,key=lambda s:s["prec"]):
        if feats and abs(s["prec"]-feats[-1]["mz"])<=fm.PPM*s["prec"]: feats[-1]["scans"].append(s)
        else: feats.append({"mz":s["prec"],"scans":[s]})
    for f in feats:
        f["mz"]=sum(s["prec"] for s in f["scans"])/len(f["scans"]); f["pint"]=max(s["pint"] for s in f["scans"])
        c=defaultdict(int)
        for s in f["scans"]:
            if not s["it"]: continue
            b=max(s["it"])
            for x in {round(m/0.003) for m,i in zip(s["mz"],s["it"]) if i>=0.05*b and m>=100}: c[x]+=1
        f["frags"]={x for x,n in c.items() if n>=0.5*len(f["scans"])}
    holders=defaultdict(set)
    for i,f in enumerate(feats):
        for x in f["frags"]: holders[x].add(i)
    res=[]
    for core in list(holders):
        mem=set().union(*(holders.get(core+d,set()) for d in (-1,0,1)))
        if len(mem)<2 or len(mem)>0.10*len(feats): continue
        mem=sorted(mem,key=lambda i:feats[i]["mz"]); par={i:i for i in mem}
        def find(i):
            while par[i]!=i: i=par[i]
            return i
        edges=[]
        for a in mem:
            for b in mem:
                d=feats[b]["mz"]-feats[a]["mz"]
                if d<=0: continue
                for n,v in fm.BIOTRANSFORMATIONS.items():
                    if abs(d-v)<=fm.PPM*feats[b]["mz"]: edges.append((a,b,n)); par[find(b)]=find(a)
        comp=defaultdict(set)
        for a,b,_ in edges: comp[find(a)]|={a,b}
        for m in comp.values(): res.append((len(m),round(core*0.003,3),tuple(sorted(m)),[e for e in edges if e[0] in m]))
    best={}
    for r in sorted(res,reverse=True):
        best.setdefault(r[2],r)
    ranked=sorted(best.values(),key=lambda r:(r[0],len(r[3])),reverse=True)
    print(f"{len(ranked)} core-conserved families")
    for k,(n,core,m,edges) in enumerate(ranked[:6],1):
        print(f"#{k} core fragment {core}: {n} members: "+", ".join(f"{feats[i]['mz']:.4f}" for i in m))
        print("    edges: "+"; ".join(f"{feats[a]['mz']:.2f}->{feats[b]['mz']:.2f} {e.split(' ')[0]}" for a,b,e in edges[:6]))
run(sys.argv[1])
