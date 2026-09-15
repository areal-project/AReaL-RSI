"""Reproducible pool-only selection using public-dev language statistics.

No hidden evaluation data is read. Token metadata is the official tokenization
of pool.jsonl; column 2 (unused metadata) is deliberately ignored.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np

V = 50257
H = 2**22


def near_groups(root, output):
    """Cluster documents sharing >=8 of their bottom-16 token 5-gram hashes."""
    tokens = np.load(root/'pool/pool_tokens.npy',mmap_mode='r')
    meta = np.load(root/'pool/pool_meta.npy')
    parent = np.arange(len(meta))
    inverted = {}
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = int(parent[i])
        return i
    for i, row in enumerate(meta):
        start, length = map(int,row[:2])
        if length<20: continue
        t = tokens[start:start+length].astype(np.uint64)
        h = np.zeros(length-4,dtype=np.uint64)
        for j in range(5): h = h*np.uint64(65599)+t[j:j+length-4]
        h ^= h >> np.uint64(30); h *= np.uint64(0xbf58476d1ce4e5b9)
        h ^= h >> np.uint64(27); h *= np.uint64(0x94d049bb133111eb)
        h ^= h >> np.uint64(31)
        h = np.unique(h)
        if len(h)<16: continue
        sketch = np.partition(h,15)[:16]
        hits = {}
        for v in sketch:
            for other in inverted.get(int(v),[]): hits[other] = hits.get(other,0)+1
        for other, count in hits.items():
            if count>=8: parent[find(i)] = find(other)
        for v in sketch:
            bucket = inverted.setdefault(int(v),[])
            # A bounded index prevents ubiquitous boilerplate from quadratic work.
            if len(bucket)<100: bucket.append(i)
        if i%20000==0: print('near duplicates',i,flush=True)
    groups = np.array([find(i) for i in range(len(meta))])
    np.save(output,groups)
    print('near groups',len(set(groups)),'documents',len(groups),flush=True)


def hashed(a, b):
    return (a.astype(np.int64) * 50021 + b) % H


def features(root, output):
    tokens = np.load(root / 'pool/pool_tokens.npy', mmap_mode='r')
    meta = np.load(root / 'pool/pool_meta.npy')
    dev = np.load(root / 'workspace/data/multi_dev.npy')
    lengths = meta[:, 1].astype(np.int64)
    bg = np.bincount(tokens, minlength=V).astype(float) + 1
    bg /= bg.sum()
    bgpair = np.zeros(H)
    for start in range(0, len(tokens)-1, 2000000):
        end = min(start+2000000, len(tokens)-1)
        bgpair += np.bincount(hashed(tokens[start:end], tokens[start+1:end+1]), minlength=H)
    bgprev = np.bincount(tokens[:-1], minlength=V).astype(float)
    uni, bi, ratio, biratio = [np.zeros((len(meta), 4), dtype=np.float32) for _ in range(4)]
    for k in range(4):
        d = dev[k*250000:(k+1)*250000]
        counts = np.bincount(d, minlength=V).astype(float)
        p = (counts + 1000*bg)/(len(d)+1000)
        pairs = np.bincount(hashed(d[:-1], d[1:]), minlength=H).astype(float)
        prev = np.bincount(d[:-1], minlength=V)
        for first in range(0, len(meta), 1000):
            last = min(first+1000, len(meta))
            lo = int(meta[first,0]); hi = int(meta[last-1,0]+meta[last-1,1]+1)
            t = tokens[lo:hi].astype(np.int64)
            a = np.roll(t,1); h = hashed(a,t)
            logu = np.log(p[t]); logb = np.log((pairs[h]+20*p[t])/(prev[a]+20))
            logbg = np.log((bgpair[h]+20*bg[t])/(bgprev[a]+20))
            starts = meta[first:last,0].astype(np.int64)-lo
            sizes = lengths[first:last]+1
            for dest, val in [(uni,logu),(bi,logb),(ratio,logu-np.log(bg[t])),(biratio,logb-logbg)]:
                # EOT and document-initial cross-document bigrams carry no quality signal.
                val[t == 50256] = 0
                val[starts] = 0
                dest[first:last,k] = np.add.reduceat(val, starts)/np.maximum(sizes-2,1)
        print('domain',k,'done',flush=True)
    quality = np.zeros((len(meta), 6), dtype=np.float32)
    duplicate = np.zeros(len(meta), dtype=bool)
    seen = set()
    with (root/'pool/pool.jsonl').open() as f:
        for line in f:
            doc = json.loads(line); i = doc['id']; text = doc['text']
            words = re.findall(r"[A-Za-z]+", text.lower())
            n = max(len(words),1)
            lines = [s.strip() for s in text.splitlines() if s.strip()]
            digest = hashlib.sha256(' '.join(words).encode()).digest()
            duplicate[i] = digest in seen; seen.add(digest)
            stop = {'the','of','and','to','a','in','is','that','for','it','as','was','with','be','on','are','by','this','an','from','or','at','which','have','not'}
            quality[i] = [sum(w in stop for w in words)/n,
                          sum(len(s) for s in lines if len(s)>100)/max(len(text),1),
                          1-len(set(lines))/max(len(lines),1),
                          sum(c.isalpha() and c.isascii() for c in text)/max(len(text),1),
                          len(set(words))/n,
                          len(text)/max(int(lengths[i]),1)]
    np.savez_compressed(output, lengths=lengths, uni=uni, bi=bi,
                        ratio=ratio, biratio=biratio, quality=quality, duplicate=duplicate)
    print('saved',output,flush=True)


def select(cache, config, output, root):
    f = np.load(cache)
    c = json.loads(Path(config).read_text())
    lens = f['lengths']; q = f['quality']
    score = sum(c.get(key,0)*f[key] for key in ['uni','bi','ratio','biratio'])
    score += c.get('prose',0)*q[:,1,None] + c.get('stop',0)*q[:,0,None]
    score -= c.get('repeat',0)*q[:,2,None]
    score += c.get('length_bonus',0)*np.log(np.maximum(lens,1)/1000)[:,None]
    valid = (~f['duplicate']) & (lens >= c.get('min_len',200)) & (lens <= c.get('max_len',6000))
    valid &= (q[:,0]>=c.get('min_stop',0.12)) & (q[:,2]<=c.get('max_repeat',0.25))
    valid &= (q[:,3]>=c.get('min_alpha',0.55)) & (q[:,5]>=c.get('min_chars',3))
    valid &= q[:,1]>=c.get('min_prose',0)
    if c.get('technical_relax_only'):
        technical_path = Path(cache).with_name('technical.npy')
        if not technical_path.exists():
            technical = np.zeros(len(lens),dtype=bool)
            specific = re.compile(r'\b(?:python|javascript|java|sql|linux|ubuntu|github|compiler|debugging|exception|stdout|stderr|malloc|kubernetes|bash)\b',re.I)
            general = re.compile(r'\b(?:code|function|variable|array|server|error|database|command|script|library|package|kernel)\b',re.I)
            with (root/'pool/pool.jsonl').open() as stream:
                for line in stream:
                    doc = json.loads(line); text = doc['text']
                    terms = {s.lower() for s in general.findall(text)}
                    technical[doc['id']] = bool(specific.search(text)) and len(terms)>=2
            np.save(technical_path,technical)
        technical = np.load(technical_path)
        ordinary = (q[:,0]>=.12)&(q[:,3]>=.55)&(q[:,5]>=3)
        valid &= ordinary|technical
    rng = np.random.default_rng(c.get('seed',42))
    score += c.get('noise',0)*rng.gumbel(size=score.shape)
    score[~valid] = -np.inf
    groups = np.load(Path(cache).with_name('near_groups.npy')) if c.get('near_dedup') else np.arange(len(lens))
    weights = np.array(c.get('weights',[.25]*4)); weights /= weights.sum()
    coverage = c.get('coverage',0)
    if coverage:
        tok = np.load(root/'pool/pool_tokens.npy',mmap_mode='r')
        meta = np.load(root/'pool/pool_meta.npy')
        bg = np.bincount(tok,minlength=V).astype(float)+1
        bg /= bg.sum()
        observed = bg*c.get('coverage_prior',2000000)
        base_score = score.copy()
    if c.get('mode','balanced') == 'global':
        scalar = score @ weights
        order = np.argsort(-scalar, kind='stable')
        order = order[valid[order]]
        seen = set(); deduped = []
        for i in order:
            if groups[i] not in seen:
                seen.add(groups[i]); deduped.append(i)
        order = deduped
    else:
        ranks = np.argsort(-score,axis=0,kind='stable')
        ptr = [0]*4; spent = np.zeros(4); used = set(); order = []
        interval = c.get('coverage_interval',500000)
        next_update = interval; since_update = []
        while spent.sum() < 12500000:
            if coverage and spent.sum()>=next_update:
                for docid in since_update:
                    start = int(meta[docid,0]); length = int(lens[docid])
                    observed += np.bincount(tok[start:start+length],minlength=V)
                correction = np.log(bg/(observed/observed.sum()))
                correction[50256] = 0
                avg = np.add.reduceat(correction[tok],meta[:,0].astype(np.int64))/(lens+1)
                score = base_score+coverage*avg[:,None]
                ranks = np.argsort(-score,axis=0,kind='stable'); ptr=[0]*4
                since_update=[]; next_update+=interval
            k = int(np.argmin(np.where(weights>0,spent/np.maximum(weights,1e-10),np.inf)))
            while ptr[k]<len(lens) and (int(groups[ranks[ptr[k],k]]) in used or not valid[ranks[ptr[k],k]]):
                ptr[k]+=1
            if ptr[k]>=len(lens):
                raise ValueError('Not enough eligible tokens')
            i = int(ranks[ptr[k],k]); ptr[k]+=1
            used.add(int(groups[i])); order.append(i); spent[k]+=lens[i]+1
            since_update.append(i)
    order = [int(i) for i in order]
    cumulative = np.cumsum(lens[order]+1)
    if cumulative[-1]<12000000: raise ValueError('Not enough tokens')
    order = order[:int(np.searchsorted(cumulative,12500000))+1]
    # Shuffle the chosen documents so register/quality order cannot create long
    # artificial distribution shifts across the packed sequence.
    if c.get('shuffle',True): rng.shuffle(order)
    Path(output).write_text(json.dumps(order)+'\n')
    consumed = np.array(order[:int(np.searchsorted(np.cumsum(lens[order]+1),12000000))+1])
    summary = dict(config=c, documents=len(order), available_tokens=int(sum(lens[order]+1)),
                   consumed_documents=len(consumed), mean_length=float(lens[consumed].mean()),
                   quality_mean=np.average(q[consumed],axis=0,weights=lens[consumed]).tolist())
    Path(str(output)+'.summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(os.environ.get('RSI_ROOT',Path(__file__).resolve().parents[2])))
    p.add_argument('--features',type=Path)
    p.add_argument('--build',action='store_true')
    p.add_argument('--build-near',action='store_true')
    p.add_argument('--config',type=Path)
    p.add_argument('--output',type=Path)
    a = p.parse_args()
    if a.features is None: a.features = a.root/'workspace/research/features.npz'
    if not a.build and not a.build_near and a.config is None:
        a.config = Path(__file__).with_name('config.json')
    if a.config and a.output is None: a.output = Path(__file__).with_name('selection.json')
    a.features.parent.mkdir(parents=True,exist_ok=True)
    if a.config and not a.features.exists(): a.build = True
    if a.build: features(a.root,a.features)
    if a.config and json.loads(a.config.read_text()).get('near_dedup') and not a.features.with_name('near_groups.npy').exists():
        a.build_near = True
    if a.build_near: near_groups(a.root,a.features.with_name('near_groups.npy'))
    if a.config: select(a.features,a.config,a.output,a.root)
