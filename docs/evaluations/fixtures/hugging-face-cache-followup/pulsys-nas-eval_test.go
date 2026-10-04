package proxy_test

import (
 "bytes"
 "crypto/sha256"
 "fmt"
 "testing"
)

func TestNASEvalSameDigestDifferentCDNPaths(t *testing.T) {
 body:=bytes.Repeat([]byte("same-weights"),1000)
 digest:=fmt.Sprintf("%x",sha256.Sum256(body))
 fake:=&fakeUpstream{}
 fake.set(&fakeResp{status:200,body:body,etag:`"`+digest+`"`,contentType:"application/octet-stream"})
 client,base,stop:=newProxyServer(t,fake);defer stop()
 for _,repo:=range []string{"repo-a","repo-b"}{
   path:="/_p/cas-bridge.xethub.hf.co/xet-bridge/"+repo+"/"+digest
   status,got:=drainGet(t,client,base,path,nil)
   if status!=200 || !bytes.Equal(got,body){t.Fatalf("status=%d len=%d",status,len(got))}
 }
 t.Logf("same content and ETag, distinct repository CDN paths: upstream payload fetches=%d bytes=%d",fake.fetches.Load(),fake.bytesOut.Load())
 if fake.fetches.Load()!=1 {t.Error("cross-path digest reuse absent")}
}

func TestNASEvalBadPayloadIsNotRetained(t *testing.T) {
 good:=bytes.Repeat([]byte("G"),4096)
 bad:=bytes.Repeat([]byte("B"),len(good))
 digest:=fmt.Sprintf("%x",sha256.Sum256(good))
 fake:=&fakeUpstream{}
 fake.set(&fakeResp{status:200,body:bad,etag:`"`+digest+`"`,contentType:"application/octet-stream"})
 client,base,stop:=newProxyServer(t,fake);defer stop()
 path:="/_p/cas-bridge.xethub.hf.co/xet-bridge/repo/"+digest
 drainGet(t,client,base,path,nil)
 before:=fake.fetches.Load()
 status,got:=drainGet(t,client,base,path,nil)
 t.Logf("repeat corrupted content: status=%d bad_bytes=%v upstream_fetches=%d",status,bytes.Equal(got,bad),fake.fetches.Load()-before)
 if status==200 && bytes.Equal(got,bad) && fake.fetches.Load()==before {t.Error("mismatched body retained and served as a warm success")}
}
