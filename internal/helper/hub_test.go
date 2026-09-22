package helper

import (
	"context"
	"encoding/json"
	"github.com/xiabee/game-scheduler/internal/config"
	"github.com/xiabee/game-scheduler/internal/game"
	"github.com/xiabee/game-scheduler/internal/game/genshin"
	"github.com/xiabee/game-scheduler/internal/game/hsr"
	"github.com/xiabee/game-scheduler/internal/store"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func testHub(t *testing.T) (*Hub, *game.Registry) {
	t.Helper()
	root := t.TempDir()
	s, e := store.Open(filepath.Join(root, "db"))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	r := game.NewRegistry(genshin.New(), hsr.New())
	h, e := New(s, r, config.Config{Root: root, DataDir: filepath.Join(root, "Data")})
	if e != nil {
		t.Fatal(e)
	}
	return h, r
}
func TestInstanceResolutionPriorityAndPersistence(t *testing.T) {
	h, _ := testHub(t)
	ext := filepath.Join(t.TempDir(), "external space 游戏.exe")
	legacy := filepath.Join(t.TempDir(), "legacy.exe")
	over := filepath.Join(t.TempDir(), "override.exe")
	for _, id := range []string{"one", "two"} {
		v := store.HelperInstance{ID: id, HelperID: "ok-nte", Name: id, LocationMode: "external", Executable: ext, Enabled: id == "one"}
		if e := h.Store.SaveHelper(v, true); e != nil {
			t.Fatal(e)
		}
	}
	p := map[string]any{"helper_instance_id": "one", "task_index": 2}
	b, _ := json.Marshal(p)
	g, task, instance, e := h.Resolve(store.Game{Adapter: "ok-nte", ToolPath: legacy}, store.Task{Type: "task", Params: string(b)})
	if e != nil || g.ToolPath != ext || g.WorkingDir != filepath.Dir(ext) || instance.ID != "one" {
		t.Fatal(g, instance, e)
	}
	_ = task
	p["exe"] = over
	p["working_dir"] = t.TempDir()
	b, _ = json.Marshal(p)
	g, _, _, e = h.Resolve(g, store.Task{Type: "task", Params: string(b)})
	if e != nil || g.ToolPath != over || g.WorkingDir != p["working_dir"] {
		t.Fatal(g, e)
	}
	p["helper_instance_id"] = "two"
	b, _ = json.Marshal(p)
	if _, _, _, e = h.Resolve(g, store.Task{Type: "task", Params: string(b)}); e == nil {
		t.Fatal("disabled accepted")
	}
	if e = h.Store.DeleteHelper("one"); e != nil {
		t.Fatal(e)
	}
	if _, e = h.Store.GetHelper("two"); e != nil {
		t.Fatal(e)
	}
	if _, e = h.Store.GetHelper("one"); e != store.ErrNotFound {
		t.Fatal(e)
	}
	if e = h.Store.SaveHelperSetting("proof", map[string]bool{"ok": true}); e != nil {
		t.Fatal(e)
	}
}
func TestLegacyPythonAndNoPATHFallback(t *testing.T) {
	h, _ := testHub(t)
	root := t.TempDir()
	python := filepath.Join(root, "python.exe")
	project := filepath.Join(root, "March7th")
	ec, _ := json.Marshal(map[string]string{"python_path": python, "march7th_dir": project})
	g, task, _, e := h.Resolve(store.Game{Adapter: "hsr", ExtraConfig: string(ec)}, store.Task{Type: "march7th_daily"})
	if e != nil {
		t.Fatal(e)
	}
	a := hsr.New()
	spec, e := a.BuildCommand(g, task)
	if e != nil || spec.Path != python || spec.Dir != project || spec.Args[0] != filepath.Join(project, "main.py") {
		t.Fatal(spec, e)
	}
	g, _, _, e = h.Resolve(store.Game{Adapter: "genshin", ToolPath: "same-name.exe"}, store.Task{})
	if e != nil || g.ToolPath != filepath.Join(h.Paths.Root, "same-name.exe") {
		t.Fatal(g, e)
	}
}
func TestManifestReloadAtomicAndDiscovery(t *testing.T) {
	h, r := testHub(t)
	dir := filepath.Join(h.Paths.Data, "helpers")
	os.MkdirAll(dir, 0755)
	file := filepath.Join(dir, "future.json")
	good := `{"schema_version":1,"id":"future","display_name":"Future","discovery":{"executable_names":["future.exe"]},"task_types":{"raw":{"raw_args":true}}}`
	os.WriteFile(file, []byte(good), 0600)
	if e := h.Reload(); e != nil {
		t.Fatal(e)
	}
	if _, e := r.Get("future"); e != nil {
		t.Fatal(e)
	}
	os.WriteFile(file, []byte(`{"schema_version":99}`), 0600)
	if e := h.Reload(); e == nil {
		t.Fatal("invalid reload accepted")
	}
	if _, e := r.Get("future"); e != nil {
		t.Fatal("lost prior manifest")
	}
	os.WriteFile(file, []byte(good), 0600)
	search := t.TempDir()
	os.WriteFile(filepath.Join(search, "future.exe"), []byte("never executed"), 0600)
	if e := h.SaveDiscovery(config.DiscoveryConfig{Roots: []string{search}, MaxDepth: 4}); e != nil {
		t.Fatal(e)
	}
	result, e := h.Discover(context.Background(), nil, 0)
	if e != nil || len(result.Candidates) != 1 {
		t.Fatal(result, e)
	}
	if result.Candidates[0].Adapter != "future" {
		t.Fatal(result)
	}
	if e := os.Remove(file); e != nil {
		t.Fatal(e)
	}
	if e := h.Reload(); e != nil {
		t.Fatal(e)
	}
	if _, e := r.Get("future"); e == nil {
		t.Fatal("removed manifest stayed registered")
	}
}
func TestManifestSymlinkEscape(t *testing.T) {
	h, _ := testHub(t)
	dir := filepath.Join(h.Paths.Data, "helpers")
	os.MkdirAll(dir, 0755)
	external := filepath.Join(t.TempDir(), "external.json")
	os.WriteFile(external, nte, 0600)
	if e := os.Symlink(external, filepath.Join(dir, "bad.json")); e != nil {
		t.Skip(e)
	}
	if e := h.Reload(); e == nil {
		t.Fatal("escape accepted")
	}
}

func TestBuiltInNTEIgnoresPreservedManifestOverride(t *testing.T) {
	h, _ := testHub(t)
	dir := filepath.Join(h.Paths.Root, "Config", "helpers")
	if e := os.MkdirAll(dir, 0755); e != nil {
		t.Fatal(e)
	}
	stale := []byte(`{
	  "schema_version":1,
	  "id":"ok-nte",
	  "display_name":"stale",
	  "discovery":{"executable_names":["ok-nte.exe"]},
	  "launch":{"default_executable":"stale.exe"},
	  "task_types":{"task":{"fields":{},"args":["--stale"]}}
	}`)
	if e := os.WriteFile(filepath.Join(dir, "ok-nte.json"), stale, 0600); e != nil {
		t.Fatal(e)
	}
	if e := h.Reload(); e != nil {
		t.Fatal(e)
	}
	d, e := h.Definition("ok-nte")
	if e != nil {
		t.Fatal(e)
	}
	if d.DisplayName != "ok-nte" || d.Launch.Worker.Entry != "main.py" || d.Launch.Worker.DefaultTimeoutSec != 21600 {
		t.Fatalf("stale manifest replaced built-in: %+v", d)
	}
	args, e := d.Arguments("task", map[string]any{"task_index": float64(2)})
	if e != nil || !reflect.DeepEqual(args, []string{"-t", "2", "-e", "--headless"}) {
		t.Fatalf("built-in args=%v err=%v", args, e)
	}
}
