package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadDefaults(t *testing.T) {
	t.Setenv("GS_ADDR", "")
	cfg, err := Load(filepath.Join(t.TempDir(), "missing.json"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Addr == "" || cfg.MaxConcurrent < 1 {
		t.Fatalf("bad defaults: %+v", cfg)
	}
}

// Invalid env overrides must be ignored (with a log warning) instead of
// silently or unexpectedly changing behavior.
func TestLoadEnvOverrides(t *testing.T) {
	t.Setenv("GS_MAX_CONCURRENT", "3")
	t.Setenv("GS_OVERLOAD_POLICY", "pause")
	t.Setenv("GS_EXECUTION_RETENTION_DAYS", "7")
	cfg, err := Load(filepath.Join(t.TempDir(), "missing.json"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.MaxConcurrent != 3 || cfg.OverloadPolicy != "pause" || cfg.ExecutionRetentionDays != 7 {
		t.Fatalf("valid overrides not applied: %+v", cfg)
	}
}

func TestLoadEnvInvalidValuesIgnored(t *testing.T) {
	t.Setenv("GS_MAX_CONCURRENT", "abc")
	t.Setenv("GS_OVERLOAD_POLICY", "yolo")
	t.Setenv("GS_MONITOR_ENABLED", "not-a-bool")
	t.Setenv("GS_EXECUTION_RETENTION_DAYS", "-")
	cfg, err := Load(filepath.Join(t.TempDir(), "missing.json"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.OverloadPolicy != "alert" {
		t.Errorf("invalid overload policy should keep the default, got %q", cfg.OverloadPolicy)
	}
	if cfg.ExecutionRetentionDays != 30 {
		t.Errorf("invalid retention days should keep the default, got %d", cfg.ExecutionRetentionDays)
	}
	if cfg.MaxConcurrent < 1 {
		t.Errorf("max concurrent should fall back to >=1, got %d", cfg.MaxConcurrent)
	}
}

// A config-file policy the env path would have rejected used to slip through
// unvalidated: "Pause"/" pause " silently degraded the pause gate to
// alert-only. Case/whitespace now normalize; unknown values fail the startup.
func TestLoadFilePolicyNormalizedOrRejected(t *testing.T) {
	dir := t.TempDir()

	write := func(content string) string {
		p := filepath.Join(dir, "cfg.json")
		if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
		return p
	}

	cfg, err := Load(write(`{"overload_policy":" Pause "}`))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.OverloadPolicy != "pause" {
		t.Fatalf("policy must normalize, got %q", cfg.OverloadPolicy)
	}

	if _, err := Load(write(`{"overload_policy":"bogus"}`)); err == nil {
		t.Fatal("an unknown file policy must fail the load, not silently run as alert")
	}

	// env still wins over file, and an invalid env value still warns+ignores
	t.Setenv("GS_OVERLOAD_POLICY", "alert")
	cfg, err = Load(write(`{"overload_policy":"pause"}`))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.OverloadPolicy != "alert" {
		t.Fatalf("env override must beat the file, got %q", cfg.OverloadPolicy)
	}
}

// Windows `setx` appends a trailing newline to env values; a token captured
// that way must not break every Bearer comparison.
func TestLoadAuthTokenTrimmed(t *testing.T) {
	t.Setenv("GS_AUTH_TOKEN", "sekrit\n")
	cfg, err := Load(filepath.Join(t.TempDir(), "missing.json"))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.AuthToken != "sekrit" {
		t.Fatalf("token must be trimmed, got %q", cfg.AuthToken)
	}
}
