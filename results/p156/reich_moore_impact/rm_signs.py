import sys, os
def num(s):
    s=s.strip()
    if not s: return 0.0
    for i in range(1,len(s)):
        if s[i] in '+-' and s[i-1] not in 'eE':
            return float(s[:i]+'e'+s[i:])
    return float(s)
def recs(lines):
    return [[num(l[11*k:11*k+11]) for k in range(6)] for l in lines]
d=sys.argv[1]; out=[]
for f in sorted(os.listdir(d)):
    lines=[l for l in open(os.path.join(d,f),errors='replace') if l[70:72]==' 2' and l[72:75]=='151']
    if not lines: continue
    r=recs(lines); i=1
    try:
        nis=int(r[0][4]); 
        za,abn,_,lfw,ner,_=r[i]; i+=1
        for _ in range(int(ner)):
            el,eh,lru,lrf,nro,naps=r[i]; i+=1
            if nro: break
            if not(int(lru)==1 and int(lrf)==3): break
            spi,ap,lad,_,nls,nlsc=r[i]; i+=1
            neg=pos=0
            for _ in range(int(nls)):
                awri,apl,l,_,n6,nrs=r[i]; i+=1
                vals=[]; 
                nl=(int(n6)+5)//6
                for k in range(nl): vals+=r[i+k]
                i+=nl
                for k in range(int(nrs)):
                    aj=vals[6*k+1]
                    if aj<0: neg+=1
                    else: pos+=1
            if neg: out.append((f,spi,el,eh,neg,pos))
    except Exception as e:
        pass
for o in out: print(*o)
print('files with negative AJ in an LRF=3 range:',len(out))
