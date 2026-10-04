import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

root=Path(tempfile.mkdtemp(prefix="muninn-v2-eval.",dir="/tmp"))
os.environ.update(HF_HOME=str(root/"hf-home"),HF_HUB_CACHE=str(root/"cache"),HF_XET_CACHE=str(root/"xet"),HF_TOKEN="",HF_TOKEN_PATH=str(root/"no-token"),HF_HUB_DISABLE_TELEMETRY="1",HF_HUB_DISABLE_PROGRESS_BARS="1",XHC_CACHE_PATH=str(root/"cache"))
for key in ["HF_HUB_DISABLE_XET","HF_HUB_OFFLINE","HF_ENDPOINT"]:
    os.environ.pop(key,None)
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
try:
    from app import jobs
except Exception as e:
    print(json.dumps({"phase":"import","error":type(e).__name__,"detail":str(e)}),flush=True)
    raise
from huggingface_hub import hf_hub_download, get_hf_file_metadata, hf_hub_url
repo="hf-internal-testing/tiny-random-gpt2"
filename="pytorch_model.bin"
meta=get_hf_file_metadata(hf_hub_url(repo,filename),token=False)
path=Path(hf_hub_download(repo,filename,cache_dir=root/"cache",token=False))
actual=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps({"phase":"download","bytes":path.stat().st_size,"resolved_blob_name":path.resolve().name,"upstream_sha256":meta.etag,"xet_hash":meta.xet_file_data.file_hash,"bytes_match_upstream_sha256":actual==meta.etag,"artifact_dir":str(root)}),flush=True)
try:
    result=jobs.verify_ingested(path)
    print(json.dumps({"phase":"muninn-verifier","result":result}),flush=True)
except Exception as e:
    print(json.dumps({"phase":"muninn-verifier","error":type(e).__name__,"detail":str(e),"blob_survives":path.exists()}),flush=True)
    raise
