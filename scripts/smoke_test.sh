#!/usr/bin/env bash
# End-to-end smoke test against a running server (default localhost:8090). Saves chart PNGs to shots/.
URL=${URL:-http://localhost:8090}; cd "$(dirname "$0")/.."; mkdir -p shots
for q in "Flag likely privileged documents" "Build a timeline and check for back-dated documents" "Find near-duplicate documents" \
         "Who discussed the price change before March 3?" "Chart communications by week" "Chart documents by sender" \
         "Summarize the vendor note DOC-033 (containment drill)"; do
curl -s $URL/api/ask -H 'content-type: application/json' -d "{\"question\":\"$q\"}" | python3 -c "
import json,sys,base64;r=json.load(sys.stdin);res=r['result'];A=r['attempts']
print('\n==',r['question']);print('  attempts:',' -> '.join(a['status'] for a in A),'| diffs:',[len(a['diff'].splitlines()) for a in A])
print('  SUMMARY:',res and res['summary'][:160])
if res: print('  citations verified: %d/%d'%(sum(f['verified'] for f in res['findings']),len(res['findings'])))
for a in A:
  for c in a.get('charts',[]):
    p='shots/sample_'+c['name'];open(p,'wb').write(base64.b64decode(c['png_b64']));print('  chart saved:',p)
print('  containment_flag:',r['containment_flag'],[b['kind'] for a in A for b in a['blocked']])
ok = res is not None; sys.exit(0 if ok else 1)" || echo "  !! FAILED"
done
