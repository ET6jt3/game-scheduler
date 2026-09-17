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
	if err != nil {\n\t\tt.Fatal(err)\n\t}
	if spec.Path != g.ToolPath {\n\t\tt.Fatalf("path=%q", spec.Path)\n\t}
	if !reflect.DeepEqual(spec.Args, []string{"-t", "2", "-e"}) {\n\t\tt.Fatalf("args=%v", spec.Args)\n\t}
	if spec.Dir != g.WorkingDir {\n\t\tt.Fatalf("dir=%q", spec.Dir)\n\t}
}

func TestTaskWithoutExit(t *testing.T) {
	spec, err := New().BuildCommand(store.Game{ToolPath: "ok-nte.exe"}, store.Task{Type: "task", Params: `{"task_index":3,"exit":false}`})
	if err != nil { t.Fatal(err) }
	if !reflect.DeepEqual(spec.Args, []string{"-t", "3"}) {\n\t\tt.Fatalf("args=%v", spec.Args)\n\t}
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
	if spec.Path != "E:/NTE/ok-nte.exe" || spec.Dir != "E:/NTE" {\n\t\tt.Fatalf("spec=%+v", spec)\n\t}
	if !reflect.DeepEqual(spec.Args, []string{"--x", "1"}) {\n\t\tt.Fatalf("args=%v", spec.Args)\n\t}
}
