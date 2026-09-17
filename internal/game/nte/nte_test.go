package nte

import (
	"reflect"
	"testing"

	"github.com/xiabee/game-scheduler/internal/store"
)

func TestTaskCommand(t *testing.T) {
	a := New()
	g := store.Game{ToolPath: `D:\Tools\ok-nte\ok-nte.exe`, WorkingDir: `D:\Tools\ok-nte`}
	spec, err := a.BuildCommand(g, store.Task{Type: "task", Params: `{"task_index":2,"exit":true}`})
	if err != nil { t.Fatal(err) }
	if spec.Path != g.ToolPath { t.Fatalf("path=%q", spec.Path) }
	if !reflect.DeepEqual(spec.Args, []string{"-t", "2", "-e"}) { t.Fatalf("args=%v", spec.Args) }
	if spec.Dir != g.WorkingDir { t.Fatalf("dir=%q", spec.Dir) }
}

func TestTaskWithoutExit(t *testing.T) {
	spec, err := New().BuildCommand(store.Game{ToolPath: "ok-nte.exe"}, store.Task{Type: "task", Params: `{"task_index":3,"exit":false}`})
	if err != nil { t.Fatal(err) }
	if !reflect.DeepEqual(spec.Args, []string{"-t", "3"}) { t.Fatalf("args=%v", spec.Args) }
}

func TestTaskRejectsInvalidIndex(t *testing.T) {
	for _, params := range []string{`{}`, `{"task_index":0}`, `{"task_index":"bad"}`} {
		if _, err := New().BuildCommand(store.Game{ToolPath: "ok-nte.exe"}, store.Task{Type: "task", Params: params}); err == nil {
			t.Fatalf("expected error for %s", params)
		}
	}
}

func TestRawArgsAndOverrides(t *testing.T) {
	spec, err := New().BuildCommand(
		store.Game{ToolPath: "default.exe", WorkingDir: "default"},
		store.Task{Type: "raw", Params: `{"raw_args":["--x","1"],"exe":"E:/NTE/ok-nte.exe","working_dir":"E:/NTE"}`},
	)
	if err != nil { t.Fatal(err) }
	if spec.Path != "E:/NTE/ok-nte.exe" || spec.Dir != "E:/NTE" { t.Fatalf("spec=%+v", spec) }
	if !reflect.DeepEqual(spec.Args, []string{"--x", "1"}) { t.Fatalf("args=%v", spec.Args) }
}
