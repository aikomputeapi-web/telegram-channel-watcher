#!/bin/bash
# login comparison via bash tool (real bash semantics)
for pw in 'Dirty2020!Passwordcameron123' 'BO49D2LIPH' 'wrongpass'; do
  code=$(curl -s --max-time 20 -X POST https://cred-search.nowrouter.store/ \
    --data-urlencode "username=admin" --data-urlencode "password=$pw" \
    -o /dev/null -w "%{http_code} redirect=%{redirect_url}")
  echo "pw[${pw:0:8}...] => $code"
done
