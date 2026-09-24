package helper

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

func TestOKNTEHeadlessBootstrapLifecycle(t *testing.T) {
	for _, needle := range []string{"--headless", "communicate.start_success.emit()", "GS_OK_NTE_RUNTIME_READY=1", "GS_OK_NTE_STATUS=", "class Proof", "FOREGROUND_DENIED"} {
		if !strings.Contains(okNTEHeadlessBootstrap, needle) {
			t.Fatalf("embedded bootstrap missing %q", needle)
		}
	}
}

func TestOKNTEBootstrapBehavior(t *testing.T) {
	var python string
	for _, name := range []string{"python3", "python"} {
		candidate, err := exec.LookPath(name)
		if err != nil {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		err = exec.CommandContext(ctx, candidate, "-c", "import sys; assert sys.version_info >= (3, 10)").Run()
		cancel()
		if err == nil {
			python = candidate
			break
		}
	}
	if python == "" {
		t.Skip("Python >=3.10 absent: behavioral fixtures need a test interpreter, not a production dependency")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-B", "test_ok_nte_bootstrap.py")
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8", "PYTHONUTF8=1")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("bootstrap behavior tests: %v\n%s", err, out)
	}
	t.Log(string(out))
}
