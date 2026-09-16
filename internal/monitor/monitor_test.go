package monitor

import (
	"bytes"
	"errors"
	"log/slog"
	"testing"
	"time"

	"github.com/xiabee/game-scheduler/internal/events"
)

func newMon(policy string) *Monitor {
	return New(Config{
		Enabled: true, CPUThreshold: 90, MemThreshold: 90, Policy: policy,
	}, func() (Reading, error) { return Reading{}, nil }, events.New(), nil)
}

func TestStaleMarkingIsFailSafeAndHeals(t *testing.T) {
	m := newMon(PolicyPause)

	// Precondition: overload latched (pause gate closed).
	m.update(Reading{CPUPercent: 99, MemPercent: 10})
	m.update(Reading{CPUPercent: 99, MemPercent: 10})
	if !m.ShouldPause() {
		t.Fatal("precondition: pause gate must be closed while overloaded")
	}

	// Sampler starts failing. Below the threshold the snapshot stays live.
	for i := 1; i < staleAfterFailures; i++ {
		m.recordError(errors.New("wmi dead"), time.Now())
	}
	if m.Current().Stale {
		t.Fatal("a transient hiccup must not mark the snapshot stale")
	}

	// At the threshold: stale marker on, error surfaced, and — fail-safe —
	// the overload latch STAYS held: an unmeasurable machine must not invite
	// new load by silently releasing the pause gate.
	m.recordError(errors.New("wmi dead"), time.Now())
	s := m.Current()
	if !s.Stale || s.LastError == "" {
		t.Fatalf("snapshot must be marked stale with the last error: %+v", s)
	}
	if !m.ShouldPause() {
		t.Fatal("fail-safe: stale data must not release the pause gate")
	}

	// A good sample heals staleness (and the under-threshold reading clears
	// the overload latch through the normal hysteresis path).
	m.update(Reading{CPUPercent: 10, MemPercent: 10})
	s = m.Current()
	if s.Stale || s.LastError != "" {
		t.Fatalf("a good sample must clear staleness: %+v", s)
	}
	if m.Overloaded() || m.ShouldPause() {
		t.Fatal("recovered sampler + normal load must release the pause gate")
	}
}

func TestStaleWarnLogIsRateLimited(t *testing.T) {
	m := newMon(PolicyAlert)
	var buf bytes.Buffer
	m.log = slog.New(slog.NewTextHandler(&buf, nil))

	err := errors.New("sampler gone")
	m.recordError(err, time.Now())
	if buf.Len() == 0 {
		t.Fatal("the first failure must log")
	}

	// Failures inside the rate-limit window are silent.
	buf.Reset()
	m.recordError(err, time.Now().Add(5*time.Second))
	m.recordError(err, time.Now().Add(30*time.Second))
	if buf.Len() != 0 {
		t.Fatalf("warnings inside the window must be suppressed: %s", buf.String())
	}

	// Past the window it warns again (one line per minute, not per interval).
	m.recordError(err, time.Now().Add(2*time.Minute))
	if buf.Len() == 0 {
		t.Fatal("a persisting failure must warn again after the window")
	}
}

func TestOverloadHysteresis(t *testing.T) {
	m := newMon(PolicyAlert)

	// One over-threshold sample is not enough (needs breachesToTrip=2).
	m.update(Reading{CPUPercent: 95, MemPercent: 50})
	if m.Overloaded() {
		t.Fatal("should not trip after a single breach")
	}
	// Second consecutive breach trips it.
	m.update(Reading{CPUPercent: 96, MemPercent: 50})
	if !m.Overloaded() {
		t.Fatal("should trip after two consecutive breaches")
	}
	if r := m.Current().Reason; r == "" {
		t.Error("expected an overload reason")
	}
	// A sample under threshold clears it immediately.
	m.update(Reading{CPUPercent: 10, MemPercent: 50})
	if m.Overloaded() {
		t.Fatal("should clear once usage drops")
	}
	if m.Current().Reason != "" {
		t.Error("reason should clear")
	}
}

func TestMemoryTrips(t *testing.T) {
	m := newMon(PolicyAlert)
	m.update(Reading{CPUPercent: 5, MemPercent: 92})
	m.update(Reading{CPUPercent: 5, MemPercent: 93})
	if !m.Overloaded() {
		t.Fatal("memory over threshold should trip overload")
	}
}

func TestShouldPausePolicy(t *testing.T) {
	// alert policy never pauses even when overloaded
	alert := newMon(PolicyAlert)
	alert.update(Reading{CPUPercent: 99, MemPercent: 10})
	alert.update(Reading{CPUPercent: 99, MemPercent: 10})
	if !alert.Overloaded() || alert.ShouldPause() {
		t.Errorf("alert policy: overloaded=%v shouldPause=%v (want true/false)", alert.Overloaded(), alert.ShouldPause())
	}

	// pause policy pauses while overloaded
	pause := newMon(PolicyPause)
	if pause.ShouldPause() {
		t.Error("should not pause when not overloaded")
	}
	pause.update(Reading{CPUPercent: 99, MemPercent: 10})
	pause.update(Reading{CPUPercent: 99, MemPercent: 10})
	if !pause.ShouldPause() {
		t.Error("pause policy should pause while overloaded")
	}
}

func TestDisabledMonitorNeverPauses(t *testing.T) {
	m := New(Config{Enabled: false, Policy: PolicyPause, CPUThreshold: 90}, func() (Reading, error) { return Reading{}, nil }, events.New(), nil)
	m.update(Reading{CPUPercent: 99})
	m.update(Reading{CPUPercent: 99})
	if m.ShouldPause() {
		t.Error("disabled monitor must never pause")
	}
}

func TestHistoryAndDisk(t *testing.T) {
	m := newMon(PolicyAlert)
	for i := 0; i < historyLen+10; i++ {
		m.update(Reading{CPUPercent: float64(i % 100), MemPercent: 40, DiskPercent: 55, DiskUsedMB: 100, DiskTotalMB: 200})
	}
	s := m.Current()
	if len(s.CPUHistory) != historyLen {
		t.Errorf("cpu history len=%d want %d (capped)", len(s.CPUHistory), historyLen)
	}
	if len(s.MemHistory) != historyLen || len(s.DiskHistory) != historyLen {
		t.Errorf("mem/disk history not tracked: %d/%d", len(s.MemHistory), len(s.DiskHistory))
	}
	if s.DiskPercent != 55 || s.DiskTotalMB != 200 {
		t.Errorf("disk not recorded: %+v", s)
	}
	// Current returns copies — mutating them must not affect the monitor.
	s.CPUHistory[0] = -1
	if m.Current().CPUHistory[0] == -1 {
		t.Error("Current() must return a copy of history")
	}
}

func TestThresholdDisabled(t *testing.T) {
	// CPUThreshold<=0 disables the CPU dimension.
	m := New(Config{Enabled: true, CPUThreshold: 0, MemThreshold: 90}, func() (Reading, error) { return Reading{}, nil }, events.New(), nil)
	m.update(Reading{CPUPercent: 100, MemPercent: 10})
	m.update(Reading{CPUPercent: 100, MemPercent: 10})
	if m.Overloaded() {
		t.Error("CPUThreshold<=0 should not trip on CPU")
	}
}
