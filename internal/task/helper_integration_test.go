package task

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/xiabee/game-scheduler/internal/config"
	"github.com/xiabee/game-scheduler/internal/events"
	"github.com/xiabee/game-scheduler/internal/game"
	"github.com/xiabee/game-scheduler/internal/game/genshin"
	"github.com/xiabee/game-scheduler/internal/game/hsr"
	"github.com/xiabee/game-scheduler/internal/game/r1999"
	"github.com/xiabee/game-scheduler/internal/game/wuwa"
	"github.com/xiabee/game-scheduler/internal/helper"
	"github.com/xiabee/game-scheduler/internal/store"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func TestExactArgvChild(t *testing.T) {
	if os.Getenv("GS_ARGV_CHILD") != "1" {
		return
	}
	for i, a := range os.Args {
		if a == "--" {
			_ = json.NewEncoder(os.Stdout).Encode(os.Args[i+1:])
			os.Exit(0)
		}
	}
	os.Exit(2)
}
func helperService(t *testing.T) (*Service, *store.Store) {
	t.Helper()
	root := t.TempDir()
	st, e := store.Open(filepath.Join(root, "db"))
	if e != nil {
		t.Fatal(e)
	}
	reg := game.NewRegistry(genshin.New(), hsr.New(), wuwa.New(), r1999.New())
	cfg := config.Config{Root: root, DataDir: filepath.Join(root, "Data"), MaxConcurrent: 1}
	hub, e := helper.New(st, reg, cfg)
	if e != nil {
		t.Fatal(e)
	}
	svc := NewService(st, reg, cfg, events.New(), nil)
	svc.Helpers = hub
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		svc.Shutdown(ctx)
		st.Close()
	})
	return svc, st
}
func TestHelperPreflightExecutionExactArgvDiagnostics(t *testing.T) {
	svc, st := helperService(t)
	t.Setenv("GS_ARGV_CHILD", "1")
	exe, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	dir := filepath.Join(t.TempDir(), "space 中文")
	os.MkdirAll(dir, 0755)
	h := store.HelperInstance{ID: "main", HelperID: "ok-nte", Name: "Main", LocationMode: "external", Executable: exe, WorkingDir: dir, Enabled: true}
	if e = st.SaveHelper(h, true); e != nil {
		t.Fatal(e)
	}
	args := []string{"-test.run=TestExactArgvChild", "--", "a b", "literal; echo NO", "中文", "${ROOT}", "", `quote"slash\`}
	params, _ := json.Marshal(map[string]any{"helper_instance_id": h.ID, "raw_args": args})
	g, e := st.CreateGame(store.Game{ID: "nte", Name: "NTE", Adapter: "ok-nte", Enabled: true})
	if e != nil {
		t.Fatal(e)
	}
	tk, e := st.CreateTask(store.Task{GameID: g.ID, Name: "fake", Type: "raw", Params: string(params), Enabled: true, TimeoutSec: 5})
	if e != nil {
		t.Fatal(e)
	}
	pf, e := svc.Preflight(tk.ID)
	if e != nil || !pf.Ready || pf.Executable != exe || pf.WorkingDir != dir || !reflect.DeepEqual(pf.Args, args) {
		t.Fatalf("preflight %+v %v", pf, e)
	}
	run, _, e := svc.Enqueue(tk.ID, store.TriggerManual, nil, false)
	if e != nil {
		t.Fatal(e)
	}
	waitStatus(t, st, run.ID, store.StatusSuccess, 5*time.Second)
	result, _ := st.GetExecution(run.ID)
	var got []string
	if e = json.Unmarshal([]byte(result.Stdout), &got); e != nil || !reflect.DeepEqual(got, args[2:]) {
		t.Fatal(result.Stdout, e)
	}
	diag, e := st.ExecutionDiagnostics(run.ID)
	if e != nil {
		t.Fatal(e)
	}
	var data map[string]any
	json.Unmarshal(diag, &data)
	if data["executable"] != exe || data["working_dir"] != dir {
		t.Fatal(string(diag))
	}
	h.Enabled = false
	st.SaveHelper(h, false)
	pf, e = svc.Preflight(tk.ID)
	if e != nil || pf.Ready || pf.ValidationError == "" {
		t.Fatal(pf, e)
	}
}
func TestNTEPackagedWorkerPreflight(t *testing.T) {
	svc, st := helperService(t)
	root := t.TempDir()
	launcher := filepath.Join(root, "ok-nte.exe")
	workerExe := filepath.Join(root, "data", "apps", "ok-nte", "python", "python.exe")
	workerDir := filepath.Join(root, "data", "apps", "ok-nte", "working")
	entry := filepath.Join(workerDir, "main.py")
	if e := os.MkdirAll(filepath.Dir(workerExe), 0755); e != nil {
		t.Fatal(e)
	}
	if e := os.MkdirAll(workerDir, 0755); e != nil {
		t.Fatal(e)
	}
	for _, path := range []string{launcher, workerExe, entry} {
		if e := os.WriteFile(path, []byte("fixture"), 0600); e != nil {
			t.Fatal(e)
		}
	}
	h := store.HelperInstance{ID: "nte-worker", HelperID: "ok-nte", Name: "NTE", LocationMode: "external", Executable: launcher, WorkingDir: root, Enabled: true}
	if e := st.SaveHelper(h, true); e != nil {
		t.Fatal(e)
	}
	pf, e := svc.PreflightHelper(h.ID, "task", map[string]any{"task_index": float64(2)})
	if e != nil || !pf.Ready || pf.Executable != workerExe || pf.WorkingDir != workerDir || !reflect.DeepEqual(pf.Args, []string{entry, "-t", "2", "-e"}) {
		t.Fatalf("worker preflight %+v err=%v", pf, e)
	}
	foundEntry := false
	for _, check := range pf.Checks {
		if check.Key == "worker_entry" && check.Path == entry && check.Exists {
			foundEntry = true
		}
	}
	if !foundEntry {
		t.Fatalf("worker entry check missing: %+v", pf.Checks)
	}
	if e := os.Remove(entry); e != nil {
		t.Fatal(e)
	}
	pf, e = svc.PreflightHelper(h.ID, "task", map[string]any{"task_index": float64(2)})
	if e != nil || pf.Ready || len(pf.Missing) == 0 {
		t.Fatalf("missing worker entry was accepted: %+v err=%v", pf, e)
	}
}

func TestMissingPathsAndOverrides(t *testing.T) {
	svc, st := helperService(t)
	exe, _ := os.Executable()
	dir := t.TempDir()
	h := store.HelperInstance{ID: "main", HelperID: "ok-nte", Name: "main", LocationMode: "external", Executable: filepath.Join(dir, "missing.exe"), Enabled: true}
	st.SaveHelper(h, true)
	pf, e := svc.PreflightHelper("main", "task", map[string]any{"task_index": float64(2)})
	if e != nil || pf.Ready || len(pf.Missing) == 0 {
		t.Fatal(pf, e)
	}
	pf, e = svc.PreflightHelper("main", "task", map[string]any{"task_index": float64(2), "exe": exe, "working_dir": dir})
	if e != nil || !pf.Ready || pf.Executable != exe {
		t.Fatal(pf, e)
	}
	pf, e = svc.PreflightHelper("main", "task", map[string]any{"task_index": float64(2), "exe": exe, "working_dir": filepath.Join(dir, "missing")})
	if e != nil || pf.Ready {
		t.Fatal(pf, e)
	}
	for _, adapter := range []string{"genshin", "wuwa", "r1999"} {
		g := store.Game{Adapter: adapter, ToolPath: exe, WorkingDir: dir}
		params := `{"raw_args":["literal","a b"]}`
		tk := store.Task{Type: "raw", Params: params}
		a, _ := svc.reg.Get(adapter)
		expected, e := a.BuildCommand(g, tk)
		if e != nil {
			t.Fatal(e)
		}
		pf, e := svc.preflightExternal(g, tk)
		if e != nil || !pf.Ready || !reflect.DeepEqual(pf.Args, expected.Args) || pf.Executable != expected.Path || pf.WorkingDir != expected.Dir {
			t.Fatal(adapter, pf, e)
		}
	}
}
func TestPythonEntryChecksWithExternalInterpreter(t *testing.T) {
	svc, st := helperService(t)
	exe, _ := os.Executable()
	dir := t.TempDir()
	entry := filepath.Join(dir, "main.py")
	h := store.HelperInstance{ID: "m7", HelperID: "march7th", Name: "m7", LocationMode: "external", Executable: entry, WorkingDir: dir, RuntimePath: exe, Enabled: true}
	st.SaveHelper(h, true)
	pf, e := svc.PreflightHelper(h.ID, "march7th_daily", nil)
	if e != nil || pf.Ready {
		t.Fatal(pf, e)
	}
	os.WriteFile(entry, []byte("# not executed"), 0600)
	pf, e = svc.PreflightHelper(h.ID, "march7th_daily", nil)
	if e != nil || !pf.Ready || pf.Executable != exe || !reflect.DeepEqual(pf.Args, []string{entry}) {
		t.Fatal(fmt.Sprintf("%+v", pf), e)
	}
}
