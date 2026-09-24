// Package chains runs persistent daily occurrences on one shared desktop.
package chains

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"sync"
	"time"
	_ "time/tzdata" // named zones also work on fresh portable Windows devices

	"github.com/xiabee/game-scheduler/internal/store"
	"github.com/xiabee/game-scheduler/internal/task"
)

type Engine struct {
	mu        sync.Mutex
	st        *store.Store
	svc       *task.Service
	log       *slog.Logger
	Ready     func() bool
	done      chan struct{}
	cancel    context.CancelFunc
	startedAt time.Time
}

func New(st *store.Store, svc *task.Service, log *slog.Logger) *Engine {
	if log == nil {
		log = slog.Default()
	}
	return &Engine{st: st, svc: svc, log: log, Ready: DesktopReady, startedAt: time.Now().UTC()}
}
func (e *Engine) Start() {
	ctx, cancel := context.WithCancel(context.Background())
	e.cancel = cancel
	e.done = make(chan struct{})
	go func() {
		defer close(e.done)
		t := time.NewTicker(time.Second)
		defer t.Stop()
		for {
			if err := e.Tick(time.Now()); err != nil {
				e.log.Error("daily chains", "error", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-t.C:
			}
		}
	}()
}
func (e *Engine) Stop() {
	if e.cancel != nil {
		e.cancel()
		<-e.done
	}
}
func Location(c store.Chain) (*time.Location, error) {
	if c.Zone == "" || c.Zone == "Local" {
		return time.Local, nil
	}
	return time.LoadLocation(c.Zone)
}
func Validate(c store.Chain) error {
	if strings.TrimSpace(c.Name) == "" || len(c.Name) > 200 {
		return fmt.Errorf("name is required (maximum 200 characters)")
	}
	if parsed, err := time.Parse("15:04", c.Time); err != nil || len(c.Time) != 5 || parsed.Format("15:04") != c.Time {
		return fmt.Errorf("time must be HH:MM")
	}
	if _, err := Location(c); err != nil {
		return fmt.Errorf("unknown time zone: %s", c.Zone)
	}
	if len(c.Days) == 0 {
		return fmt.Errorf("select at least one day")
	}
	days := map[int]bool{}
	for _, d := range c.Days {
		if d < 0 || d > 6 || days[d] {
			return fmt.Errorf("days must be unique values 0–6")
		}
		days[d] = true
	}
	if len(c.TaskIDs) == 0 || len(c.TaskIDs) > 32 {
		return fmt.Errorf("select 1–32 tasks")
	}
	ids := map[int64]bool{}
	for _, id := range c.TaskIDs {
		if id <= 0 || ids[id] {
			return fmt.Errorf("select each task only once")
		}
		ids[id] = true
	}
	if c.FailurePolicy != "stop" && c.FailurePolicy != "continue" {
		return fmt.Errorf("failure policy must be stop or continue")
	}
	return nil
}
func dayEnabled(c store.Chain, t time.Time) bool {
	for _, d := range c.Days {
		if d == int(t.Weekday()) {
			return true
		}
	}
	return false
}

// Resolve nonexistent DST wall times to the first available minute after them;
// repeated wall times have just one occurrence, protected by the calendar key.
func slot(c store.Chain, t time.Time) time.Time {
	start := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, t.Location())
	end := start.AddDate(0, 0, 1)
	for p := start; p.Before(end); p = p.Add(time.Minute) {
		if p.Format("15:04") >= c.Time {
			return p
		}
	}
	return end
}
func Next(c store.Chain, now time.Time) time.Time {
	loc, err := Location(c)
	if err != nil {
		return time.Time{}
	}
	now = now.In(loc)
	for i := 0; i < 8; i++ {
		d := now.AddDate(0, 0, i)
		if dayEnabled(c, d) {
			p := slot(c, d)
			if p.After(now) {
				return p
			}
		}
	}
	return time.Time{}
}
func Due(c store.Chain, now time.Time) (string, bool) {
	loc, err := Location(c)
	if err != nil {
		return "", false
	}
	t := now.In(loc)
	p := slot(c, t)
	return t.Format("2006-01-02"), c.Enabled && dayEnabled(c, t) && !t.Before(p) && (c.CatchUp || t.Sub(p) < time.Minute)
}
func (e *Engine) Save(c store.Chain, disablePlans bool) (store.Chain, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if err := Validate(c); err != nil {
		return c, err
	}
	for _, id := range c.TaskIDs {
		if _, err := e.st.GetTask(id); err != nil {
			return c, fmt.Errorf("task %d is missing", id)
		}
	}
	runs, err := e.st.ListChainRuns()
	if err != nil {
		return c, err
	}
	for _, r := range runs {
		if r.ChainID == c.ID && (r.Status == "running" || r.Status == "paused" || r.Status == "interrupted") {
			return c, fmt.Errorf("finish or cancel this chain's unfinished run before editing")
		}
	}
	return e.st.SaveChain(c, disablePlans)
}
func (e *Engine) Enable(id int64, enabled bool) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	c, err := e.st.GetChain(id)
	if err != nil {
		return err
	}
	c.Enabled = enabled
	_, err = e.st.SaveChain(c, false)
	return err
}
func (e *Engine) Tick(now time.Time) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	defs, err := e.st.ListChains()
	if err != nil {
		return err
	}
	byID := map[int64]store.Chain{}
	for _, c := range defs {
		byID[c.ID] = c
		if day, due := Due(c, now); due {
			if _, err = e.st.CreateChainRun(c, day); err != nil {
				return err
			}
		}
	}
	runs, err := e.st.ListChainRuns()
	if err != nil {
		return err
	}
	sort.Slice(runs, func(i, j int) bool { return runs[i].ID < runs[j].ID })
	for _, r := range runs {
		// A terminal run is eligible for automatic startup recovery only when
		// it predates this daemon session. This prevents an operator cancelling
		// a broken helper from having it immediately restart in the same session.
		preexistingAtStartup := r.UpdatedAt.Before(e.startedAt)
		changed := false
		interrupted := false
		for i, s := range r.Steps {
			if s.Status != "running" {
				continue
			}
			x, xerr := e.st.GetExecution(s.ExecutionID)
			if xerr != nil {
				if !errors.Is(xerr, store.ErrNotFound) {
					return xerr
				}
				r.Steps[i].Status = "failed"
				r.Steps[i].Error = "execution was removed; completion cannot be verified"
				changed = true
				interrupted = true
				continue
			}
			if e.svc.ExecutionActive(x.ID) {
				continue
			}
			if x.Status == store.StatusPending || x.Status == store.StatusRunning {
				return fmt.Errorf("orphan execution %d needs recovery", x.ID)
			}
			r.Steps[i].Status = x.Status
			r.Steps[i].Error = x.ErrorMsg
			changed = true
			if strings.Contains(x.ErrorMsg, "interrupted") || strings.Contains(x.ErrorMsg, "restart") || x.Status == store.StatusCancelled {
				r.Steps[i].Status = "interrupted"
				interrupted = true
			}
		}
		if interrupted && r.Status != "cancelled" {
			r.Status = "interrupted"
		}
		c := byID[r.ChainID]
		if changed {
			if err = e.st.SaveChainRun(r); err != nil {
				return err
			}
		}
		if r.Status != "running" && preexistingAtStartup {
			_, due := Due(c, now)
			resume := due && ((c.ResumeIncompleteOnStartup && (r.Status == "failed" || r.Status == "interrupted")) ||
				(c.ResumeCancelledOnStartup && r.Status == "cancelled"))
			if resume {
				for i := range r.Steps {
					if r.Steps[i].Status == "success" {
						continue
					}
					r.Steps[i].Status = "pending"
					r.Steps[i].ExecutionID = 0
					r.Steps[i].Error = ""
				}
				r.Status = "running"
				if err = e.st.SaveChainRun(r); err != nil {
					return err
				}
				e.log.Info("daily chain recovered after daemon startup", "run_id", r.ID, "chain_id", r.ChainID)
			}
		}
		if r.Status != "running" {
			e.svc.ReleaseChain(r.ID)
			continue
		}
		loc, lerr := Location(c)
		if lerr != nil {
			return lerr
		}
		active := false
		for _, s := range r.Steps {
			if s.Status == "running" {
				active = true
			}
		}
		allSuccess := true
		for _, step := range r.Steps {
			if step.Status != "success" {
				allSuccess = false
			}
		}
		if !active && !allSuccess && r.Day != now.In(loc).Format("2006-01-02") {
			r.Status = "expired"
			if err = e.st.SaveChainRun(r); err != nil {
				return err
			}
			e.svc.ReleaseChain(r.ID)
			continue
		}
		if !c.Enabled {
			e.svc.ReleaseChain(r.ID)
			continue
		}
		if active {
			continue
		}
		next := -1
		failed := false
		for i, s := range r.Steps {
			if s.Status == "failed" || s.Status == "interrupted" {
				failed = true
				if r.FailurePolicy == "stop" {
					break
				}
			}
			if s.Status == "pending" {
				next = i
				break
			}
		}
		if next < 0 {
			r.Status = "success"
			if failed {
				r.Status = "failed"
			}
			if err = e.st.SaveChainRun(r); err != nil {
				return err
			}
			e.svc.ReleaseChain(r.ID)
			continue
		}
		if e.Ready != nil && !e.Ready() {
			e.svc.ReleaseChain(r.ID)
			continue
		}
		if !e.svc.ReserveChain(r.ID) {
			continue
		}
		t, terr := e.st.GetTask(r.Steps[next].TaskID)
		var problem error = terr
		if problem == nil {
			g, gerr := e.st.GetGame(t.GameID)
			problem = gerr
			if problem == nil && (!t.Enabled || !g.Enabled) {
				problem = fmt.Errorf("task or game is disabled")
			}
		}
		if problem == nil {
			_, _, problem = e.svc.EnqueueChain(t.ID, r.ID, next)
		}
		if problem != nil {
			r.Steps[next].Status = "failed"
			r.Steps[next].Error = problem.Error()
			if err = e.st.SaveChainRun(r); err != nil {
				return err
			}
		}
	}
	return nil
}

// Run now uses today's occurrence too, so double-clicks never duplicate work.
func (e *Engine) RunNow(id int64, now time.Time) (store.ChainRun, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	c, err := e.st.GetChain(id)
	if err != nil {
		return store.ChainRun{}, err
	}
	if !c.Enabled {
		return store.ChainRun{}, fmt.Errorf("enable the chain first")
	}
	loc, err := Location(c)
	if err != nil {
		return store.ChainRun{}, err
	}
	return e.st.CreateChainRun(c, now.In(loc).Format("2006-01-02"))
}
func (e *Engine) Control(id int64, action string) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	r, err := e.st.GetChainRun(id)
	if err != nil {
		return err
	}
	switch action {
	case "pause":
		if r.Status != "running" {
			return fmt.Errorf("only a running chain can be paused")
		}
		r.Status = "paused"
	case "resume":
		if r.Status != "paused" && r.Status != "failed" && r.Status != "interrupted" {
			return fmt.Errorf("this run cannot be resumed")
		}
		c, err := e.st.GetChain(r.ChainID)
		if err != nil {
			return err
		}
		loc, err := Location(c)
		if err != nil {
			return err
		}
		if r.Day != time.Now().In(loc).Format("2006-01-02") {
			return fmt.Errorf("past-day run is retained as history; run today's chain instead")
		}
		for i, s := range r.Steps {
			if s.ExecutionID != 0 && e.svc.ExecutionActive(s.ExecutionID) {
				return fmt.Errorf("wait for the current step to finish")
			}
			if s.Status != "success" {
				r.Steps[i].Status = "pending"
				r.Steps[i].ExecutionID = 0
				r.Steps[i].Error = ""
			}
		}
		r.Status = "running"
	case "cancel":
		if r.Status != "running" && r.Status != "paused" && r.Status != "interrupted" {
			return fmt.Errorf("run is already finished")
		}
		r.Status = "cancelled"
	default:
		return errors.New("unknown action")
	}
	if err = e.st.SaveChainRun(r); err != nil {
		return err
	}
	if action == "cancel" {
		for _, s := range r.Steps {
			if s.ExecutionID != 0 && e.svc.ExecutionActive(s.ExecutionID) {
				e.svc.Cancel(s.ExecutionID)
			}
		}
	}
	e.svc.ReleaseChain(r.ID)
	return nil
}

// DeleteRun removes a finished chain occurrence. Task execution rows are kept
// in the ordinary execution history; only the chain occurrence/progress record
// is removed, allowing an explicit same-day retest to create a fresh run.
func (e *Engine) DeleteRun(id int64) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	r, err := e.st.GetChainRun(id)
	if err != nil {
		return err
	}
	if r.Status == "running" || r.Status == "paused" {
		return fmt.Errorf("stop or cancel the chain before deleting its record")
	}
	for _, step := range r.Steps {
		if step.ExecutionID != 0 && e.svc.ExecutionActive(step.ExecutionID) {
			return fmt.Errorf("execution %d is still active", step.ExecutionID)
		}
	}
	e.svc.ReleaseChain(r.ID)
	return e.st.DeleteChainRun(id)
}
