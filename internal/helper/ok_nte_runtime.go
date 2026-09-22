package helper

// okNTEHeadlessBootstrap repairs a lifecycle gap in the packaged ok-nte v1.3.x
// headless entry path. That path calls OK.run_task directly and therefore
// bypasses OK.start_runtime(), which is normally responsible for emitting
// start_success. ok-nte's Globals starts RuntimeServices (including OpenVINO)
// from that event. Without it, the first task that waits on
// openvino_model_async can block until shutdown cancels the Future.
//
// The bootstrap deliberately runs inside ok-nte's own packaged interpreter and
// working directory. It does not modify the external installation.
const okNTEHeadlessBootstrap = `import json
import sys

from src.config import config
from src.patches.startup_patches import install_startup_patches

install_startup_patches(config)

import ok
from ok.core.events import communicate

instance = ok.OK(config)
task = instance.get_onetime_task(2)
headless = instance.headless_app
headless.initialize_overlay()

# Packaged --headless task execution skips OK.start_runtime(), so explicitly
# emit the same runtime-ready event before starting the one-time task.
communicate.start_success.emit()

# Fail early if the background OpenVINO service could not initialize. This also
# guarantees that DailyRoutineTask cannot later hang on an unstarted Future.
my_app = getattr(ok.og, "my_app", None)
if my_app is None:
    raise RuntimeError("ok-nte Globals service is unavailable")
_ = my_app.openvino_model_async
print("GS_OK_NTE_RUNTIME_READY=1", flush=True)

# Keep ok-nte's own exit-after cleanup (including closing the game), then map
# DailyRoutineTask's internal per-item status to the process exit code.
instance.run_onetime_task(task, exit_after=True)
status = getattr(task, "task_status", {}) or {}
print("GS_OK_NTE_STATUS=" + json.dumps(status, ensure_ascii=True), flush=True)
failed = status.get("failed") or []
pending = status.get("pending") or []
if failed or pending:
    sys.exit(20)
`
