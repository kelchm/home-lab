import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

root=Path(tempfile.mkdtemp(prefix="muninn-offline-eval.",dir="/tmp"))
cache=root/"cache"
os.environ.update(HF_HOME=str(root/"hf-home"),HF_HUB_CACHE=str(cache),HF_XET_CACHE=str(root/"xet"),HF_TOKEN="",HF_TOKEN_PATH=str(root/"no-token"),HF_HUB_DISABLE_TELEMETRY="1",XHC_UPSTREAM="https://upstream.invalid",XHC_STATE_DIR=str(root/"state"))
for key in ["HF_ENDPOINT","HF_HUB_OFFLINE","XHC_TIER2"]:
    os.environ.pop(key,None)
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
commit="a"*40
data=b'{"model_type":"bert"}'
etag=hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
base=cache/"models--acme--tiny"
(base/"blobs").mkdir(parents=True)
(base/"blobs"/etag).write_bytes(data)
(base/"refs").mkdir()
(base/"refs"/"main").write_text(commit)
snap=base/"snapshots"/commit
snap.mkdir(parents=True)
(snap/"config.json").symlink_to("../../blobs/"+etag)

import httpx
from app.main import app
from app import hfcompat, cachefs

upstream_requests=[]
def unavailable(request):
    upstream_requests.append(str(request.url.path))
    raise httpx.ConnectError("evaluation: upstream unavailable",request=request)

async def main():
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as upstream:
        hfcompat.get_client=lambda:upstream
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://nas.local") as client:
            results=[]
            for path in [f"/acme/tiny/resolve/{commit}/config.json",f"/api/models/acme/tiny/revision/{commit}",f"/api/models/acme/tiny/tree/{commit}"]:
                response=await client.get(path)
                results.append({"path":path,"status":response.status_code,"cached_file_matches":response.content==data})
            print(json.dumps({"local_file_present":cachefs.resolve_local("model","acme/tiny",commit,"config.json") is not None,"results":results,"upstream_requests":upstream_requests,"artifact_dir":str(root)}),flush=True)
asyncio.run(main())
