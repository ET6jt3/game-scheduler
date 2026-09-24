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
	for _, needle := range []string{"--headless", "communicate.start_success.emit()", "GS_OK_NTE_RUNTIME_READY=1", "GS_OK_NTE_STATUS=", "class Proof", "FOREGROUND_DENIED", "strict-no-mouse", "cursor-compatible", "INPUT_AUDIT"} {
		if !strings.Contains(okNTECoreSource, needle) && !strings.Contains(okNTELauncherSource, needle) {
			t.Fatalf("readable embedded sources missing %q", needle)
		}
	}
	for _, needle := range []string{"zlib.decompress", "<gs-nte-launcher>", "<gs-nte-core>", "GS_OK_NTE_EVENT_DIR"} {
		if !strings.Contains(okNTEHeadlessBootstrap, needle) {
			t.Fatalf("assembled compressed transport missing %q", needle)
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

func TestOKNTENativeLifecycleSource(t *testing.T) {
	for _, needle := range []string{
		"native-lifecycle-v1",
		"select_native_tasks",
		"enable_after_start",
		"instance.run_onetime_task(task, exit_after=True)",
		"communicate.start_success.emit()",
		"launcher_owned_by",
		"TASK_ITEMS_FAILED",
		"LauncherCaptureObserver",
		"LAUNCHER_CAPTURE_SNAPSHOT",
		"LAUNCHER_CAPTURE_STALE",
		"capture_target_signature",
		"launcher_button_ready_percentage",
	} {
		if !strings.Contains(okNTENativeLifecycleSource, needle) {
			t.Fatalf("native lifecycle source missing %q", needle)
		}
	}
	for _, forbidden := range []string{
		"patch_launcher(",
		"patch_input(",
		"from src.interaction.NTEInteraction",
		"SetCursorPos(",
		"SendInput(",
		"def uia_invoke",
		"capture.get_frame(",
		"ensure_capture(",
		"bring_to_front(",
		"resize_window(",
	} {
		if strings.Contains(okNTENativeLifecycleSource, forbidden) {
			t.Fatalf("native lifecycle source unexpectedly owns input/launcher behavior: %q", forbidden)
		}
	}
	for _, needle := range []string{"zlib.decompress", "<gs-nte-native>", "GS_OK_NTE_EVENT_DIR"} {
		if !strings.Contains(okNTENativeLifecycleBootstrap, needle) {
			t.Fatalf("native lifecycle transport missing %q", needle)
		}
	}
}

func TestOKNTENativeLifecycleBehavior(t *testing.T) {
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
		t.Skip("Python >=3.10 absent: native lifecycle fixtures need a test interpreter")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-B", "test_ok_nte_native_lifecycle.py")
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8", "PYTHONUTF8=1")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("native lifecycle behavior tests: %v\n%s", err, out)
	}
	t.Log(string(out))
}
