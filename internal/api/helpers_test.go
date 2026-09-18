package api

import (
	"encoding/json"
	"github.com/xiabee/game-scheduler/internal/config"
	"github.com/xiabee/game-scheduler/internal/events"
	"github.com/xiabee/game-scheduler/internal/game"
	"github.com/xiabee/game-scheduler/internal/helper"
	"github.com/xiabee/game-scheduler/internal/store"
	"github.com/xiabee/game-scheduler/internal/task"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHelpersAPIPersistenceAuthAndPreflight(t *testing.T) {
	root := t.TempDir()
	st, e := store.Open(filepath.Join(root, "db"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.Close()
	reg := game.NewRegistry()
	cfg := config.Config{Root: root, DataDir: root, AuthToken: "test-token"}
	hub, e := helper.New(st, reg, cfg)
	if e != nil {
		t.Fatal(e)
	}
	svc := task.NewService(st, reg, cfg, events.New(), nil)
	svc.Helpers = hub
	s := New(st, svc, nil, reg, events.New(), nil, cfg, nil)
	handler := s.Handler()
	call := func(method, path, body, token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		if token != "" {
			r.Header.Set("Authorization", "Bearer "+token)
		}
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		return w
	}
	if w := call("GET", "/api/helpers", "", ""); w.Code != 401 {
		t.Fatal(w.Code)
	}
	exe, _ := os.Executable()
	body, _ := json.Marshal(store.HelperInstance{ID: "one", HelperID: "ok-nte", Name: "Main", Executable: exe, LocationMode: "external", Enabled: true})
	if w := call("POST", "/api/helpers", string(body), "test-token"); w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := call("POST", "/api/helpers", string(body), "test-token"); w.Code != 409 {
		t.Fatal(w.Code)
	}
	w := call("POST", "/api/helpers/one/preflight", `{"type":"task","params":{"task_index":2}}`, "test-token")
	var pf task.Preflight
	json.Unmarshal(w.Body.Bytes(), &pf)
	if w.Code != http.StatusOK || !pf.Ready || pf.Executable != exe || len(pf.Args) != 3 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := call("GET", "/api/helper-definitions", "", "test-token"); w.Code != 200 || !strings.Contains(w.Body.String(), "ok-nte") {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := call("POST", "/api/helper-definitions/reload", "{}", "test-token"); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := call("DELETE", "/api/helpers/one", "", "test-token"); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := call("GET", "/api/helpers/one", "", "test-token"); w.Code != 404 {
		t.Fatal(w.Code)
	}
}
