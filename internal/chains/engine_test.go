package chains

import (
	"context"
	"encoding/json"
	"github.com/xiabee/game-scheduler/internal/config"
	"github.com/xiabee/game-scheduler/internal/events"
	"github.com/xiabee/game-scheduler/internal/game"
	"github.com/xiabee/game-scheduler/internal/runner"
	"github.com/xiabee/game-scheduler/internal/store"
	"github.com/xiabee/game-scheduler/internal/task"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

func TestChainWorker(t *testing.T) {
	if os.Getenv("GS_CHAIN_WORKER") != "1" {
		return
	}
	time.Sleep(30 * time.Millisecond)
	code, _ := strconv.Atoi(os.Getenv("GS_CHAIN_EXIT"))
	os.Exit(code)
}

type adapter struct{}

func (adapter) Key() string               { return "chain-test" }
func (adapter) TaskTypes() []string       { return []string{"ok", "fail"} }
func (adapter) Validate(store.Game) error { return nil }
func (adapter) BuildCommand(_ store.Game, t store.Task) (runner.Spec, error) {
	code := "0"
	if t.Type == "fail" {
		code = "7"
	}
	return runner.Spec{Path: os.Args[0], Args: []string{"-test.run=^TestChainWorker$"}, Env: []string{"GS_CHAIN_WORKER=1", "GS_CHAIN_EXIT=" + code}, Timeout: time.Duration(t.TimeoutSec) * time.Second}, nil
}
func setup(t *testing.T) (*Engine, *store.Store, *task.Service, store.Chain) {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "state.db"))
	if err != nil {
		t.Fatal(err)
	}
	svc := task.NewService(st, game.NewRegistry(adapter{}), config.Config{MaxConcurrent: 3}, events.New(), nil)
	e := New(st, svc, nil)
	e.Ready = func() bool { return true }
	t.Cleanup(func() { svc.Shutdown(context.Background()); st.Close() })
	ids := []int64{}
	for _, name := range []string{"BetterGI", "HSR", "NTE"} {
		_, err = st.CreateGame(store.Game{ID: name, Name: name, Adapter: "chain-test", Enabled: true})
		if err != nil {
			t.Fatal(err)
		}
		v, err := st.CreateTask(store.Task{GameID: name, Name: name, Type: "ok", Params: "{}", Enabled: true})
		if err != nil {
			t.Fatal(err)
		}
		ids = append(ids, v.ID)
	}
	c, err := e.Save(store.Chain{Name: "Daily", Time: "00:00", Zone: "UTC", Days: []int{0, 1, 2, 3, 4, 5, 6}, TaskIDs: ids, Enabled: true, CatchUp: true, FailurePolicy: "stop"}, false)
	if err != nil {
		t.Fatal(err)
	}
	return e, st, svc, c
}
func tick(t *testing.T, e *Engine) {
	t.Helper()
	if err := e.Tick(time.Now()); err != nil {
		t.Fatal(err)
	}
}
func waitRun(t *testing.T, e *Engine, st *store.Store, c store.Chain, want string) store.ChainRun {
	t.Helper()
	until := time.Now().Add(10 * time.Second)
	for time.Now().Before(until) {
		tick(t, e)
		r, err := st.ChainRunForDay(c.ID, time.Now().UTC().Format("2006-01-02"))
		if err == nil && r.Status == want {
			return r
		}
		time.Sleep(10 * time.Millisecond)
	}
	r, _ := st.ChainRunForDay(c.ID, time.Now().UTC().Format("2006-01-02"))
	t.Fatalf("want %s got %+v", want, r)
	return r
}
func TestOrderCatchUpAndDailyDedup(t *testing.T) {
	e, st, svc, c := setup(t)
	tick(t, e)
	if _, _, err := svc.Enqueue(c.TaskIDs[1], "manual", nil, false); err == nil {
		t.Fatal("manual run entered reserved chain")
	}
	r := waitRun(t, e, st, c, "success")
	for i, s := range r.Steps {
		if s.Status != "success" {
			t.Fatal(r)
		}
		if i > 0 {
			prev, _ := st.GetExecution(r.Steps[i-1].ExecutionID)
			cur, _ := st.GetExecution(s.ExecutionID)
			if cur.StartTime.Before(*prev.EndTime) {
				t.Fatal("overlap")
			}
		}
	}
	e2 := New(st, svc, nil)
	e2.Ready = func() bool { return true }
	tick(t, e2)
	if _, err := e2.RunNow(c.ID, time.Now()); err != nil {
		t.Fatal(err)
	}
	tick(t, e2)
	n, _ := st.CountExecutions()
	if n != 3 {
		t.Fatalf("reran completed daily chain: %d", n)
	}
}
func TestInterruptedRetainsSuccessAndManualResume(t *testing.T) {
	e, st, _, c := setup(t)
	r, err := st.CreateChainRun(c, time.Now().UTC().Format("2006-01-02"))
	if err != nil {
		t.Fatal(err)
	}
	r.Steps[0].Status = "success"
	if err = st.SaveChainRun(r); err != nil {
		t.Fatal(err)
	}
	_, err = st.CreateChainExecution(r.ID, 1, store.Execution{TaskID: c.TaskIDs[1], Trigger: "chain", Status: "pending"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = st.RecoverOrphans(); err != nil {
		t.Fatal(err)
	}
	tick(t, e)
	r, _ = st.GetChainRun(r.ID)
	if r.Status != "interrupted" || r.Steps[0].Status != "success" {
		t.Fatal(r)
	}
	for i := 0; i < 3; i++ {
		tick(t, e)
	}
	n, _ := st.CountExecutions()
	if n != 1 {
		t.Fatal("automatically retried unknown outcome")
	}
	if err = e.Control(r.ID, "resume"); err != nil {
		t.Fatal(err)
	}
	r = waitRun(t, e, st, c, "success")
	n, _ = st.CountExecutions()
	if n != 3 || r.Steps[0].ExecutionID != 0 {
		t.Fatal("did not retain success")
	}
}
func TestFailurePoliciesAndRetry(t *testing.T) {
	for _, policy := range []string{"stop", "continue"} {
		t.Run(policy, func(t *testing.T) {
			e, st, _, c := setup(t)
			c.FailurePolicy = policy
			c, err := e.Save(c, false)
			if err != nil {
				t.Fatal(err)
			}
			v, _ := st.GetTask(c.TaskIDs[1])
			v.Type = "fail"
			if _, err = st.UpdateTask(v); err != nil {
				t.Fatal(err)
			}
			r := waitRun(t, e, st, c, "failed")
			want := "pending"
			if policy == "continue" {
				want = "success"
			}
			if r.Steps[2].Status != want {
				t.Fatal(r)
			}
			v.Type = "ok"
			_, _ = st.UpdateTask(v)
			if err = e.Control(r.ID, "resume"); err != nil {
				t.Fatal(err)
			}
			r = waitRun(t, e, st, c, "success")
			if r.Steps[0].ExecutionID == 0 {
				t.Fatal(r)
			}
		})
	}
}
func TestPausedAndLockedDesktop(t *testing.T) {
	e, st, _, c := setup(t)
	e.Ready = func() bool { return false }
	tick(t, e)
	n, _ := st.CountExecutions()
	if n != 0 {
		t.Fatal("ran on locked desktop")
	}
	r, _ := st.ChainRunForDay(c.ID, time.Now().UTC().Format("2006-01-02"))
	if err := e.Control(r.ID, "pause"); err != nil {
		t.Fatal(err)
	}
	e.Ready = func() bool { return true }
	tick(t, e)
	n, _ = st.CountExecutions()
	if n != 0 {
		t.Fatal("ran paused chain")
	}
	if err := e.Control(r.ID, "resume"); err != nil {
		t.Fatal(err)
	}
	waitRun(t, e, st, c, "success")
}
func TestDatesDSTAndMissedPolicy(t *testing.T) {
	c := store.Chain{Enabled: true, CatchUp: true, Time: "02:30", Zone: "America/New_York", Days: []int{0, 1, 2, 3, 4, 5, 6}}
	loc, _ := Location(c)
	gap := time.Date(2026, 3, 8, 3, 0, 0, 0, loc)
	day, due := Due(c, gap)
	if !due || day != "2026-03-08" {
		t.Fatal(day, due)
	}
	c.Time = "01:30"
	for _, stamp := range []string{"2026-11-01T05:31:00Z", "2026-11-01T06:31:00Z"} {
		now, _ := time.Parse(time.RFC3339, stamp)
		day, due = Due(c, now)
		if !due || day != "2026-11-01" {
			t.Fatal(day, due)
		}
	}
	c.CatchUp = false
	now := time.Date(2026, 9, 17, 9, 0, 0, 0, loc)
	if _, due = Due(c, now); due {
		t.Fatal("late run without catchup")
	}
	c.CatchUp = true
	if _, due = Due(c, now); !due {
		t.Fatal("catchup missed")
	}
	c.Days = []int{int(time.Friday)}
	if _, due = Due(c, now); due {
		t.Fatal("wrong weekday")
	}
}
func TestAtomicStepLinkAndDisabledPlans(t *testing.T) {
	e, st, _, c := setup(t)
	_, err := st.CreatePlan(store.Plan{Name: "old", TaskID: c.TaskIDs[0], CronExpr: "* * * * *", Enabled: true})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = e.Save(c, true); err != nil {
		t.Fatal(err)
	}
	plans, _ := st.ListPlans(true)
	if len(plans) != 0 {
		t.Fatal("old schedules not disabled")
	}
	r, err := st.CreateChainRun(c, "2026-09-17")
	if err != nil {
		t.Fatal(err)
	}
	x, err := st.CreateChainExecution(r.ID, 0, store.Execution{TaskID: c.TaskIDs[0], Trigger: "chain", Status: "pending"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = st.CreateChainExecution(r.ID, 0, x); err == nil {
		t.Fatal("duplicate step accepted")
	}
	r, _ = st.GetChainRun(r.ID)
	b, _ := json.Marshal(r)
	if r.Steps[0].ExecutionID != x.ID {
		t.Fatal(string(b))
	}
}

func TestStartupRecoversPreexistingInterruptedRun(t *testing.T) {
	_, st, svc, c := setup(t)
	day := time.Now().UTC().Format("2006-01-02")
	r, err := st.CreateChainRun(c, day)
	if err != nil {
		t.Fatal(err)
	}
	r.Steps[0].Status = "success"
	r.Steps[1].Status = "interrupted"
	r.Steps[1].Error = "interrupted: server stopped"
	r.Status = "interrupted"
	if err = st.SaveChainRun(r); err != nil {
		t.Fatal(err)
	}
	time.Sleep(time.Millisecond)
	restarted := New(st, svc, nil)
	restarted.Ready = func() bool { return true }
	r = waitRun(t, restarted, st, c, "success")
	if r.Steps[0].Status != "success" || r.Steps[0].ExecutionID != 0 {
		t.Fatalf("startup recovery rewrote a completed step: %+v", r.Steps[0])
	}
	n, _ := st.CountExecutions()
	if n != 2 {
		t.Fatalf("startup recovery should only rerun unfinished steps; executions=%d", n)
	}
}

func TestStartupCancelledRecoveryIsOptIn(t *testing.T) {
	_, st, svc, c := setup(t)
	day := time.Now().UTC().Format("2006-01-02")
	r, err := st.CreateChainRun(c, day)
	if err != nil {
		t.Fatal(err)
	}
	r.Status = "cancelled"
	if err = st.SaveChainRun(r); err != nil {
		t.Fatal(err)
	}
	time.Sleep(time.Millisecond)
	restarted := New(st, svc, nil)
	restarted.Ready = func() bool { return true }
	tick(t, restarted)
	n, _ := st.CountExecutions()
	if n != 0 {
		t.Fatal("operator-cancelled run restarted without opt-in")
	}
	c.ResumeCancelledOnStartup = true
	c.PolicyVersion = 1
	if _, err = st.SaveChain(c, false); err != nil {
		t.Fatal(err)
	}
	time.Sleep(time.Millisecond)
	restarted = New(st, svc, nil)
	restarted.Ready = func() bool { return true }
	waitRun(t, restarted, st, c, "success")
}

func TestDeleteFinishedRunAllowsExplicitSameDayRetest(t *testing.T) {
	e, st, _, c := setup(t)
	r, err := st.CreateChainRun(c, time.Now().UTC().Format("2006-01-02"))
	if err != nil {
		t.Fatal(err)
	}
	r.Status = "cancelled"
	if err = st.SaveChainRun(r); err != nil {
		t.Fatal(err)
	}
	if err = e.DeleteRun(r.ID); err != nil {
		t.Fatal(err)
	}
	fresh, err := e.RunNow(c.ID, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if fresh.ID == r.ID || fresh.Status != "running" {
		t.Fatalf("expected a fresh same-day occurrence, got %+v", fresh)
	}
}
