package config

import (
	"github.com/xiabee/game-scheduler/internal/portable"
	"os"
	"path/filepath"
)

type DiscoveryConfig struct {
	Roots              []string `json:"roots"`
	ScanAllLocalDrives bool     `json:"scan_all_local_drives"`
	MaxDepth           int      `json:"max_depth"`
}

// ResolvePortable is called by the server after Load. ROOT always means the binary directory.
func (c *Config) ResolvePortable() error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	if resolved, e := filepath.EvalSymlinks(exe); e == nil {
		exe = resolved
	}
	c.Root = filepath.Dir(exe)
	p, err := portable.New(c.Root, c.DataDir, c.HelpersDir, c.RuntimeDir)
	if err != nil {
		return err
	}
	c.DataDir = p.Data
	c.HelpersDir = p.Helpers
	c.RuntimeDir = p.Runtime
	c.DBPath, err = p.Resolve(c.DBPath)
	if err != nil {
		return err
	}
	if c.NativeControllerPath != "" {
		c.NativeControllerPath, err = p.Resolve(c.NativeControllerPath)
		if err != nil {
			return err
		}
	}
	if len(c.ManifestDirs) == 0 {
		c.ManifestDirs = []string{"${ROOT}/Config/helpers", "${DATA}/helpers"}
	}
	for i, d := range c.ManifestDirs {
		c.ManifestDirs[i], err = p.Resolve(d)
		if err != nil {
			return err
		}
	}
	return nil
}
