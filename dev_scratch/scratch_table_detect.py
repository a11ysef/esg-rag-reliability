import json, re

def num_line_ratio(content):
    lines = [l.strip() for l in content.split(chr(10)) if l.strip()]
    if not lines:
        return 0.0, 0, 0
    num_re = re.compile(r'^[-+]?[\d,]+(\.\d+)?%?$')
    n_num = sum(1 for l in lines if num_re.match(l))
    return n_num/len(lines), n_num, len(lines)

CHUNK_CACHE = {}

def get_chunk(company, chunk_id):
    data = CHUNK_CACHE.setdefault(company, json.loads(open(f'analysis/cloud_chunks/{company}.json').read()))
    for c in data:
        if c['chunk_id'] == chunk_id:
            return c
    return None

def adjacent_id(chunk_id, delta):
    m = re.match(r'(.+-p\d+-c)(\d+)$', chunk_id)
    if not m:
        return None
    prefix, num = m.group(1), int(m.group(2))
    newnum = num + delta
    if newnum < 0:
        return None
    return f'{prefix}{newnum:02d}'

result_data = json.loads(open('analysis/outputs/esg_comparison.json').read())
for company_block in result_data:
    company = company_block['company']
    for ind in company_block['indicators']:
        if ind['disclosed']:
            continue
        top3 = sorted(ind['attempts'], key=lambda a: -a['similarity'])[:3]
        verdict = 'NARRATIVE_MISS'
        best_ratio = 0
        best_chunk = None
        for a in top3:
            cid = a['chunk_id']
            for delta in (0, 1, -1):
                aid = adjacent_id(cid, delta)
                if not aid:
                    continue
                c = get_chunk(company, aid)
                if not c:
                    continue
                ratio, n_num, n_total = num_line_ratio(c['content'])
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_chunk = aid
                if ratio >= 0.3 and n_num >= 5:
                    verdict = 'FLATTENED_TABLE'
        print(f"{company:16s} {ind['indicator_id']:22s} verdict={verdict:16s} best_ratio={best_ratio:.2f} best_chunk={best_chunk}")
