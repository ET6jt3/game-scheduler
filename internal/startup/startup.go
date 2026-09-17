// Package startup manages the single per-user portable logon registration.
package startup

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"
)

func Apply(root, action string, elevated bool) (map[string]any, error) {
	if runtime.GOOS != "windows" {
		if action == "Status" {
			return map[string]any{"supported": false, "enabled": false}, nil
		}
		return nil, fmt.Errorf("Windows startup setup is available in the Windows portable package")
	}
	if action != "Status" && action != "Enable" && action != "Disable" {
		return nil, fmt.Errorf("unknown startup action")
	}
	script := filepath.Join(root, "Startup.ps1")
	if _, err := os.Stat(script); err != nil {
		return nil, fmt.Errorf("build the portable package first: %w", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	args := []string{"-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-Action", action}
	if elevated {
		args = append(args, "-Elevated")
	}
	powershell := filepath.Join(os.Getenv("SystemRoot"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	out, err := exec.CommandContext(ctx, powershell, args...).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("startup setup: %s (%w)", out, err)
	}
	var result map[string]any
	if err = json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("startup status: %w", err)
	}
	return result, nil
}
