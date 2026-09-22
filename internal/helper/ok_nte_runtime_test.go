package helper

import (
	"strings"
	"testing"
)

func TestOKNTEHeadlessBootstrapLifecycle(t *testing.T) {
	requiredInOrder := []string{
		"instance = ok.OK(config)",
		"task = instance.get_onetime_task(2)",
		"communicate.start_success.emit()",
		"_ = my_app.openvino_model_async",
		"instance.run_onetime_task(task, exit_after=True)",
		"GS_OK_NTE_STATUS=",
		"if failed or pending:",
		"sys.exit(20)",
	}
	last := -1
	for _, needle := range requiredInOrder {
		idx := strings.Index(okNTEHeadlessBootstrap, needle)
		if idx < 0 {
			t.Fatalf("bootstrap missing %q", needle)
		}
		if idx <= last {
			t.Fatalf("bootstrap lifecycle out of order at %q", needle)
		}
		last = idx
	}
	if strings.Contains(okNTEHeadlessBootstrap, "open(") || strings.Contains(okNTEHeadlessBootstrap, "write(") {
		t.Fatal("bootstrap must not modify the external ok-nte installation")
	}
}
