package store

import (
	"path/filepath"
	"testing"
)

func TestHelperMigrationIdempotentPreservesLegacy(t *testing.T) {
	path := filepath.Join(t.TempDir(), "scheduler.db")
	s, e := Open(path)
	if e != nil {
		t.Fatal(e)
	}
	g, e := s.CreateGame(Game{ID: "legacy", Name: "old", Adapter: "genshin", ToolPath: "D:/External/BetterGI.exe", Enabled: true})
	if e != nil {
		t.Fatal(e)
	}
	task, e := s.CreateTask(Task{GameID: g.ID, Name: "old task", Type: "raw", Enabled: true})
	if e != nil {
		t.Fatal(e)
	}
	h := HelperInstance{ID: "one", HelperID: "ok-nte", Name: "main", Executable: "${HELPERS}/ok-nte.exe", Enabled: true, LocationMode: "managed"}
	if e = s.SaveHelper(h, true); e != nil {
		t.Fatal(e)
	}
	s.Close()
	for i := 0; i < 2; i++ {
		s, e = Open(path)
		if e != nil {
			t.Fatal(e)
		}
		old, e := s.GetGame(g.ID)
		if e != nil || old.ToolPath != g.ToolPath {
			t.Fatal(old, e)
		}
		if _, e = s.GetTask(task.ID); e != nil {
			t.Fatal(e)
		}
		got, e := s.GetHelper(h.ID)
		if e != nil || got.Executable != h.Executable {
			t.Fatal(got, e)
		}
		s.Close()
	}
}
