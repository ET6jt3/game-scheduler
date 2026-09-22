// Package runner executes external automation tools as ordinary child
// processes and captures their output. It contains no game-specific logic and
// performs no injection, memory access, packet manipulation or anti-detection
// behaviour — it only spawns an executable, waits for it, and records the
// result. This boundary is intentional: every supported tool is treated as an
// opaque local process.
package runner

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// Spec fully describes how to launch one external tool invocation.
type Spec struct {
	RequireCompleteTree    bool          // Windows chains reject launcher-only exits with live descendants
	PreserveTimeoutInChain bool          // lifecycle-sensitive workers keep their timeout inside daily chains
	Path                   string        // absolute or PATH-resolvable executable
	Args                []string      // command-line arguments
	Dir                 string        // working directory (optional)
	Env                 []string      // extra environment, appended to os.Environ()
	Timeout             time.Duration // 0 means no timeout
	CompletionMarker    string        // optional stdout marker proving managed work completed
	CompletionGrace     time.Duration // grace period for the launcher to exit after the marker
}

// CommandLine renders the spec for logging/storage. It is informational only
// and is not re-parsed.
func (s Spec) CommandLine() string {
	parts := append([]string{s.Path}, s.Args...)
	for i, p := range parts {
		if strings.ContainsAny(p, " \t\"") {
			parts[i] = fmt.Sprintf("%q", p)
		}
	}
	return strings.Join(parts, " ")
}

// Result is the outcome of running a Spec.
type Result struct {
	Command   string
	Stdout    string
	Stderr    string
	ExitCode  int  // -1 if the process never started or was killed by signal
	Started   bool // true once the child process actually launched
	Err       error
	StartTime time.Time
	EndTime   time.Time
	TimedOut  bool
}

// maxCapture bounds how much stdout/stderr is retained per stream so a chatty
// tool cannot exhaust memory. The tail is kept because errors usually surface
// at the end of output.
const maxCapture = 1 << 20 // 1 MiB

// Run launches the process described by spec, waits for it to exit (or for the
// timeout / ctx cancellation), and returns a populated Result. A non-zero exit
// code is reported via Result.ExitCode and Result.Err; the error is never
// silently dropped.
func Run(ctx context.Context, spec Spec) Result {
	res := Result{Command: spec.CommandLine(), ExitCode: -1, StartTime: time.Now()}

	if strings.TrimSpace(spec.Path) == "" {
		res.EndTime = time.Now()
		res.Err = errors.New("runner: empty executable path")
		return res
	}

	runCtx := ctx
	var cancel context.CancelFunc
	if spec.Timeout > 0 {
		runCtx, cancel = context.WithTimeout(ctx, spec.Timeout)
		defer cancel()
	}

	cmd := exec.CommandContext(runCtx, spec.Path, spec.Args...)
	cmd.Dir = spec.Dir
	if len(spec.Env) > 0 {
		cmd.Env = append(cmd.Environ(), spec.Env...)
	}
	// On cancel or timeout, kill the whole child process tree — the automation
	// tools spawn grandchildren (python, helper exes) that would otherwise keep
	// controlling the game after the parent is signalled. WaitDelay forces Wait
	// to return even if a lingering grandchild holds the stdout/stderr pipes.
	cmd.Cancel = func() error { return killProcessTree(cmd.Process) }
	cmd.WaitDelay = 5 * time.Second

	var stdout, stderr cappedBuffer
	stdout.limit = maxCapture
	stderr.limit = maxCapture
	var completion <-chan struct{}
	if spec.CompletionMarker != "" {
		hit := make(chan struct{})
		cmd.Stdout = &markerBuffer{dst: &stdout, marker: spec.CompletionMarker, hit: hit}
		completion = hit
	} else {
		cmd.Stdout = &stdout
	}
	cmd.Stderr = &stderr

	err := cmd.Start()
	if err == nil {
		res.Started = true
		// From this point the child (and its whole future tree) lives in a
		// kill-on-close job, so a scheduler hard-exit cannot orphan it. The
		// close after Wait also finishes off anything still dying.
		release, remaining, jerr := trackJob(cmd.Process)
		if jerr == nil {
			defer release()
		}
		if jerr != nil && spec.RequireCompleteTree {
			_ = killProcessTree(cmd.Process)
			_ = cmd.Wait()
			err = fmt.Errorf("cannot track helper process tree: %w", jerr)
		} else {
			waitCh := make(chan error, 1)
			go func() { waitCh <- cmd.Wait() }()
			markerSuccess := false
			if completion == nil {
				err = <-waitCh
			} else {
				select {
				case err = <-waitCh:
				case <-completion:
					markerSuccess = true
					grace := spec.CompletionGrace
					if grace <= 0 {
						grace = 5 * time.Second
					}
					timer := time.NewTimer(grace)
					select {
					case err = <-waitCh:
						if !timer.Stop() {
							<-timer.C
						}
					case <-timer.C:
						_ = killProcessTree(cmd.Process)
						err = <-waitCh
					case <-runCtx.Done():
						_ = killProcessTree(cmd.Process)
						err = <-waitCh
					}
				}
			}
			if markerSuccess {
				res.EndTime = time.Now()
				res.Stdout = stdout.String()
				res.Stderr = stderr.String()
				res.ExitCode = 0
				return res
			}
			if spec.RequireCompleteTree && remaining != nil {
				alive, checkErr := waitForTreeDrain(remaining, time.Second)
				if checkErr != nil {
					err = fmt.Errorf("cannot verify helper completion: %w", checkErr)
				} else if alive {
					err = errors.New("helper launcher exited with child processes still running; completion is unknown. Use the worker command or disable automatic launcher exit before chaining")
				}
			}
		}
	}
	res.EndTime = time.Now()
	res.Stdout = stdout.String()
	res.Stderr = stderr.String()

	if runCtx.Err() == context.DeadlineExceeded {
		res.TimedOut = true
		res.Err = fmt.Errorf("runner: timed out after %s", spec.Timeout)
		return res
	}

	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			res.ExitCode = ee.ExitCode()
			res.Err = fmt.Errorf("runner: exit code %d", res.ExitCode)
		} else if res.Started {
			res.Err = fmt.Errorf("runner: %w", err)
		} else {
			res.Err = fmt.Errorf("runner: failed to start: %w", err)
		}
		return res
	}

	res.ExitCode = 0
	return res
}

// cappedBuffer keeps only the last `limit` bytes written to it.
type cappedBuffer struct {
	buf   bytes.Buffer
	limit int
}

func (c *cappedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if c.limit > 0 && len(p) > c.limit {
		p = p[len(p)-c.limit:]
		c.buf.Reset()
	}
	c.buf.Write(p)
	if c.limit > 0 && c.buf.Len() > c.limit {
		over := c.buf.Len() - c.limit
		c.buf.Next(over)
	}
	return n, nil
}

func (c *cappedBuffer) String() string { return c.buf.String() }

// KillProcessTree terminates the process and all of its descendants. It is
// the exported seam of the runner's cancel path for consumers that manage
// their own child processes (e.g. the native controller session) so the
// taskkill /T + PPID-sweep + job-object guarantees stay in one place.
func KillProcessTree(p *os.Process) error {
	if p == nil {
		return nil
	}
	return killProcessTree(p)
}

// AssignJob puts a freshly started child into a kill-on-close job so a
// scheduler hard-exit cannot orphan it (no-op where unsupported). Returns
// the release func the caller must invoke once the child is done.
func AssignJob(p *os.Process) (release func(), err error) {
	return assignJob(p)
}

// markerBuffer forwards stdout while detecting a completion marker across
// arbitrary write boundaries. The marker is data, never shell syntax.
type markerBuffer struct {
	dst    *cappedBuffer
	marker string
	hit    chan struct{}
	once   sync.Once
	tail   string
}

func (m *markerBuffer) Write(p []byte) (int, error) {
	n, err := m.dst.Write(p)
	if m.marker == "" {
		return n, err
	}
	combined := m.tail + string(p)
	if strings.Contains(combined, m.marker) {
		m.once.Do(func() { close(m.hit) })
	}
	keep := len(m.marker) - 1
	if keep < 0 {
		keep = 0
	}
	if len(combined) > keep {
		m.tail = combined[len(combined)-keep:]
	} else {
		m.tail = combined
	}
	return n, err
}

func waitForTreeDrain(remaining func() (bool, error), grace time.Duration) (bool, error) {
	deadline := time.Now().Add(grace)
	for {
		alive, err := remaining()
		if err != nil || !alive {
			return alive, err
		}
		if time.Now().After(deadline) {
			return true, nil
		}
		time.Sleep(50 * time.Millisecond)
	}
}
