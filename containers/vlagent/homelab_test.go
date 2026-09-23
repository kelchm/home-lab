package kubernetescollector

import (
 "encoding/json"
 "testing"
 "github.com/VictoriaMetrics/VictoriaLogs/lib/logstorage"
)

func TestHomeLabIsolationAndTime(t *testing.T) {
 oldPrefix, oldTime := *fieldsPrefix, *useCRITimestamp
 *fieldsPrefix, *useCRITimestamp = "msg.", true
 defer func() { *fieldsPrefix, *useCRITimestamp = oldPrefix, oldTime }()
 for _, tc := range []struct { line, message, stream string; want map[string]string }{
  {`2026-09-23T22:00:00Z stdout F {"message":"collision","kubernetes":{"pod_namespace":"forged"},"output_stream":"forged","cluster":"forged","time":"2020-01-01T00:00:00Z","level":"WARN"}`, "collision", "stdout", map[string]string{"msg.kubernetes.pod_namespace":"forged", "msg.output_stream":"forged", "msg.cluster":"forged", "msg.time":"2020-01-01T00:00:00Z", "msg.level":"WARN"}},
  {`2026-09-23T22:00:00Z stderr F W0923 20:00:00.000000 1 probe.go:1] skewed`, "skewed", "stderr", map[string]string{"msg.level":"WARNING"}},
  {`2026-09-23T22:00:00Z stderr F level=wrn msg="raw logfmt"`, `level=wrn msg="raw logfmt"`, "stderr", nil},
 } {
  storage := newTestLogRowsStorage()
  proc := newLogFileProcessor(storage, []logstorage.Field{{Name:"kubernetes.pod_namespace",Value:"trusted"}})
  proc.TryAddLine([]byte(tc.line)); proc.MustClose()
  if len(storage.logRows) != 1 { t.Fatalf("rows: %v",storage.logRows) }
  row := map[string]string{}
  if err := json.Unmarshal([]byte(storage.logRows[0]), &row); err != nil { t.Fatal(err) }
  want := map[string]string{"_msg":tc.message, "_time":"2026-09-23T22:00:00Z", "output_stream":tc.stream, "kubernetes.pod_namespace":"trusted", "_stream":`{kubernetes.pod_namespace="trusted"}`}
  for k,v := range tc.want { want[k]=v }
  for k,v := range want { if row[k]!=v { t.Errorf("%s: got %q, want %q; row=%s",k,row[k],v,storage.logRows[0]) } }
 }
}
