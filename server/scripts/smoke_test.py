from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
import time
import urllib.request
import base64

from app.core.config import settings


def request(url, method="GET", data=None, headers=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8085")
    p.add_argument("--user", default="TEST01")
    p.add_argument("--password", required=True)
    p.add_argument("--admin-token", default=settings.admin_token)
    args=p.parse_args()

    print("HEALTH", request(args.base+"/health"))
    token=base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    now=int(time.time())
    payload={
        "_type":"location","_id":f"smoke{now}","tst":now,"created_at":now,
        "lat":39.999,"lon":116.326,"acc":8,"batt":80,"m":2,"source":"smoke-test"
    }
    print("INGEST", request(
        args.base+"/api/v1/gps/owntracks", "POST", payload,
        {"Content-Type":"application/json","Authorization":"Basic "+token,
         "X-Limit-U":args.user,"X-Limit-D":"smoke"}
    ))
    print("STATUS", request(
        args.base+f"/api/v1/admin/gps/status/{args.user}",
        headers={"Authorization":"Bearer "+args.admin_token}
    ))

if __name__=="__main__":
    main()
