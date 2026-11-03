#!/usr/bin/env bash
# End-to-end smoke test against a running server (default localhost:8090)
URL=${URL:-http://localhost:8090}
for q in "Flag likely privileged documents" "Build a timeline and check for back-dated documents" "Find near-duplicate documents" "Who discussed the price change before March 3?" "Summarize the vendor note DOC-033 (containment drill)"; do
curl -s $URL/api/ask -H 'content-type: application/json' -d "{\"question\":\"$q\"}" | python3 -c "
import json,sys;r=json.load(sys.stdin);print('\n==',r['question']);[print(' ',s['msg']) for s in r['steps']];res=r['result'];print('SUMMARY:',res and res['summary'])
[print('  ',f['doc_id'],'verified' if f['verified'] else 'UNVERIFIED','|',f['note'][:100]) for f in (res or {}).get('findings',[])[:6]]
[print('  ERR',a['stderr'][-300:]) for a in r['attempts'] if not a['ok']]
print('  containment_flag:',r['containment_flag'],[a['blocked'] for a in r['attempts']])"
done
