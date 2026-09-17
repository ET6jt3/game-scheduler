package api

import (
	"context"
	"encoding/json"
	"github.com/xiabee/game-scheduler/internal/chains"
	"github.com/xiabee/game-scheduler/internal/config"
	"github.com/xiabee/game-scheduler/internal/events"
	"github.com/xiabee/game-scheduler/internal/game"
	"github.com/xiabee/game-scheduler/internal/store"
	"github.com/xiabee/game-scheduler/internal/task"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func TestChainAPIValidationAndOrigin(t *testing.T) {
	st, err := store.Open(filepath.Join(t.TempDir(), "api.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	svc := task.NewService(st, game.NewRegistry(), config.Config{}, events.New(), nil)
	defer svc.Shutdown(context.Background())
	server := New(st, svc, nil, game.NewRegistry(), events.New(), nil, config.Config{}, nil)
	server.Chains = chains.New(st, svc, nil)
	h := server.Handler()
	_, err = st.CreateGame(store.Game{ID: "a", Name: "a", Adapter: "raw", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	tsk, err := st.CreateTask(store.Task{GameID: "a", Name: "a", Type: "raw", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	v := store.Chain{Name: "Daily", Time: "06:00", Zone: "Local", Days: []int{0, 1, 2, 3, 4, 5, 6}, TaskIDs: []int64{tsk.ID}, FailurePolicy: "stop", Enabled: false, CatchUp: true}
	b, _ := json.Marshal(v)
	for _, tc := range []struct {
		body, origin string
		want         int
	}{{string(b), "https://evil.example", 403}, {`{"name":"broken","time":"25:00"}`, "", 400}, {string(b), "", 200}} {
		r := httptest.NewRequest("POST", "http://localhost/api/chains", strings.NewReader(tc.body))
		if tc.origin != "" {
			r.Header.Set("Origin", tc.origin)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.want {
			t.Fatalf("%d: %s", w.Code, w.Body.String())
		}
	}
	r := httptest.NewRequest("GET", "http://localhost/automation", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 200 || !strings.Contains(w.Body.String(), "type=\"time\"") {
		t.Fatal("missing schedule editor")
	}
}


func TestChainRunDeleteEndpoint(t *testing.T) {
	st, err := store.Open(filepath.Join(t.TempDir(), "delete.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	svc := task.NewService(st, game.NewRegistry(), config.Config{}, events.New(), nil)
	defer svc.Shutdown(context.Background())
	server := New(st, svc, nil, game.NewRegistry(), events.New(), nil, config.Config{}, nil)
	server.Chains = chains.New(st, svc, nil)
	h := server.Handler()
	_, err = st.CreateGame(store.Game{ID: "a", Name: "a", Adapter: "raw", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	tsk, err := st.CreateTask(store.Task{GameID: "a", Name: "a", Type: "raw", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	c, err := st.SaveChain(store.Chain{Name: "Daily", Time: "00:00", Zone: "Local", Days: []int{0, 1, 2, 3, 4, 5, 6}, TaskIDs: []int64{tsk.ID}, FailurePolicy: "stop", Enabled: true, CatchUp: true}, false)
	if err != nil {
		t.Fatal(err)
	}
	run, err := st.CreateChainRun(c, "2026-09-17")
	if err != nil {
		t.Fatal(err)
	}
	run.Status = "cancelled"
	if err = st.SaveChainRun(run); err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("DELETE", "http://localhost/api/chain-runs/"+strconv.FormatInt(run.ID, 10), nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != 204 {
		t.Fatalf("delete status=%d body=%s", w.Code, w.Body.String())
	}
	if _, err = st.GetChainRun(run.ID); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("run still exists: %v", err)
	}
}
