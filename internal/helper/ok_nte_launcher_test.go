package helper

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf16"
)

func nteTestPython(t *testing.T) string {
	t.Helper()
	for _, name := range []string{"python3", "python"} {
		path, err := exec.LookPath(name)
		if err != nil {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		err = exec.CommandContext(ctx, path, "-c", "import sys; assert sys.version_info >= (3,10)").Run()
		cancel()
		if err == nil {
			return path
		}
	}
	t.Skip("Python >=3.10 unavailable for fixture tests")
	return ""
}

func TestNTELauncherBehavior(t *testing.T) {
	python := nteTestPython(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, python, "-B", "test_ok_nte_launcher.py").CombinedOutput()
	if err != nil {
		t.Fatalf("launcher regressions: %v\n%s", err, out)
	}
	t.Log(string(out))
}

func TestNTEEmbeddedTransport(t *testing.T) {
	python := nteTestPython(t)
	// Conservatively include escaped command and 1,000 units of executable path.
	units := len(utf16.Encode([]rune(strconv.Quote(okNTEHeadlessBootstrap)))) + 1000
	if units >= 32767 {
		t.Fatalf("NTE adapter exceeds Windows command-line budget: %d", units)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	logDir := t.TempDir()
	cmd := exec.CommandContext(ctx, python, "-c", okNTEHeadlessBootstrap)
	cmd.Env = append(os.Environ(), "GS_OK_NTE_DOCTOR=1", "GS_OK_NTE_EVENT_DIR="+logDir)
	out, err := cmd.CombinedOutput()
	exit, ok := err.(*exec.ExitError)
	if !ok || (exit.ExitCode() != 21 && exit.ExitCode() != 24 && exit.ExitCode() != 28) {
		t.Fatalf("doctor must report desktop unavailable/unsupported/check-only, not execute game code: %v\n%s", err, out)
	}
	if !strings.Contains(string(out), "WORKER_STARTED") || !strings.Contains(string(out), "GS_OK_NTE_STATUS=") {
		t.Fatalf("embedded transport produced no diagnostic outcome: %s", out)
	}
	files, err := filepath.Glob(filepath.Join(logDir, "nte-*.jsonl"))
	if err != nil || len(files) != 1 {
		t.Fatalf("live event file missing: %v %v", files, err)
	}
}
