package helper

import (
	"encoding/json"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/xiabee/game-scheduler/internal/store"
)

func TestNTEArguments(t *testing.T) {
	d, e := Parse(nte)
	if e != nil {
		t.Fatal(e)
	}
	for _, tc := range []struct {
		p    string
		want []string
		bad  bool
	}{{`{}`, []string{"-t", "2", "-e", "--headless"}, false}, {`{"task_index":2}`, []string{"-t", "2", "-e", "--headless"}, false}} {
		var p map[string]any
		_ = json.Unmarshal([]byte(tc.p), &p)
		got, e := d.Arguments("task", p)
		if (e != nil) != tc.bad || (!tc.bad && !reflect.DeepEqual(got, tc.want)) {
			t.Fatalf("%s: %v %v", tc.p, got, e)
		}
	}
	var p map[string]any
	_ = json.Unmarshal([]byte(`{"raw_args":["space value","$(touch SHOULD_NOT_EXIST)","${ROOT}","中文","", "a\"b"]}`), &p)
	got, e := d.Arguments("raw", p)
	if e != nil || len(got) != 6 || got[2] != "${ROOT}" {
		t.Fatal(got, e)
	}
	if _, e = d.Arguments("raw", map[string]any{"raw_args": []any{1.0}}); e == nil {
		t.Fatal("non-string raw accepted")
	}
}
func TestNTEWorkerCommand(t *testing.T) {
	d, e := Parse(nte)
	if e != nil {
		t.Fatal(e)
	}
	root := t.TempDir()
	launcher := filepath.Join(root, "ok-nte.exe")
	g := store.Game{Adapter: "ok-nte", ToolPath: launcher, WorkingDir: root}
	task := store.Task{Type: "task", Params: `{"task_index":2}`, TimeoutSec: 37}
	spec, e := d.BuildCommand(g, task)
	if e != nil {
		t.Fatal(e)
	}
	workerDir := filepath.Join(root, "data", "apps", "ok-nte", "working")
	workerExe := filepath.Join(root, "data", "apps", "ok-nte", "python", "python.exe")
	entry := filepath.Join(workerDir, "main.py")
	if spec.Path != workerExe || spec.Dir != workerDir || len(spec.Args) != 2 || spec.Args[0] != "-c" || spec.Args[1] != okNTEHeadlessBootstrap || spec.Timeout.Seconds() != 37 || !spec.PreserveTimeoutInChain || spec.CompletionMarker != "" {
		t.Fatalf("worker spec=%+v", spec)
	}
	if !reflect.DeepEqual(spec.Env, []string{"PYTHONIOENCODING=utf-8", "PYTHONUTF8=1"}) {
		t.Fatalf("worker env=%v", spec.Env)
	}
	entryPath, ok := d.WorkerEntryPath(g, task, spec)
	if !ok || entryPath != entry {
		t.Fatalf("worker entry=%q ok=%v", entryPath, ok)
	}
	defaultSpec, e := d.BuildCommand(g, store.Task{Type: "task", Params: `{}`})
	if e != nil || defaultSpec.Timeout != 6*time.Hour {
		t.Fatalf("default worker timeout=%s err=%v", defaultSpec.Timeout, e)
	}

	raw := store.Task{Type: "raw", Params: `{"raw_args":["--diagnose"]}`}
	rawSpec, e := d.BuildCommand(g, raw)
	if e != nil || rawSpec.Path != launcher || rawSpec.Dir != root || !reflect.DeepEqual(rawSpec.Args, []string{"--diagnose"}) {
		t.Fatalf("raw spec=%+v err=%v", rawSpec, e)
	}
}

func TestManifestValidationAndRepeated(t *testing.T) {
	valid := `{"schema_version":1,"id":"future","display_name":"Future","task_types":{"daily":{"fields":{"profile":{"type":"enum","enum":["main","alt"],"required":true},"tags":{"type":"string","repeated":true},"count":{"type":"number","min":0}},"args":["--profile","{{profile}}",{"repeat":"tags","flag":"--tag"}]}}}`
	d, e := Parse([]byte(valid))
	if e != nil {
		t.Fatal(e)
	}
	args, e := d.Arguments("daily", map[string]any{"profile": "main", "tags": []any{"a b", ";echo x"}})
	if e != nil || !reflect.DeepEqual(args, []string{"--profile", "main", "--tag", "a b", "--tag", ";echo x"}) {
		t.Fatal(args, e)
	}
	if _, e = d.Arguments("daily", map[string]any{"profile": "bad"}); e == nil {
		t.Fatal("enum accepted")
	}
	for _, s := range []string{strings.Replace(valid, `"schema_version":1`, `"schema_version":2`, 1), strings.Replace(valid, `{{profile}}`, `{{unknown}}`, 1), strings.Replace(valid, `{{profile}}`, `{{profile}`, 1), strings.Replace(valid, `"type":"number"`, `"type":"shell"`, 1), valid + `{}`, strings.Replace(valid, `"schema_version":1`, `"shell":"bash","schema_version":1`, 1)} {
		if _, e := Parse([]byte(s)); e == nil {
			t.Fatal("invalid accepted", s)
		}
	}
}
