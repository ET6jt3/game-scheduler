package helper

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"github.com/xiabee/game-scheduler/internal/config"
	"github.com/xiabee/game-scheduler/internal/discover"
	"github.com/xiabee/game-scheduler/internal/game"
	"github.com/xiabee/game-scheduler/internal/portable"
	"github.com/xiabee/game-scheduler/internal/store"
)

//go:embed ok-nte.json
var nte []byte

type Hub struct {
	Store       *store.Store
	Paths       portable.Paths
	reg         *game.Registry
	dirs        []string
	mu          sync.RWMutex
	definitions map[string]*Definition
	manifestIDs []string
	discovery   config.DiscoveryConfig
}

func New(st *store.Store, reg *game.Registry, cfg config.Config) (*Hub, error) {
	root := cfg.Root
	if root == "" {
		var e error
		root, e = os.Getwd()
		if e != nil {
			return nil, e
		}
	}
	paths, e := portable.New(root, cfg.DataDir, cfg.HelpersDir, cfg.RuntimeDir)
	if e != nil {
		return nil, e
	}
	dirs := cfg.ManifestDirs
	if len(dirs) == 0 {
		dirs = []string{filepath.Join(root, "Config", "helpers"), filepath.Join(paths.Data, "helpers")}
	}
	h := &Hub{Store: st, Paths: paths, reg: reg, dirs: dirs, discovery: cfg.Discovery}
	if h.discovery.MaxDepth == 0 {
		h.discovery.MaxDepth = 4
	}
	if e = h.Reload(); e != nil {
		return nil, e
	}
	return h, nil
}
func (h *Hub) Reload() error {
	h.mu.Lock()
	defer h.mu.Unlock()
	defs := map[string]*Definition{}
	for _, b := range []struct{ id, name, adapter string }{{"bettergi", "BetterGI", "genshin"}, {"march7th", "March7thAssistant / Fhoe-Rail", "hsr"}, {"ok-wuwa", "ok-wuthering-waves", "wuwa"}, {"m9a", "M9A", "r1999"}} {
		defs[b.id] = &Definition{SchemaVersion: 1, ID: b.id, DisplayName: b.name, Adapter: b.adapter}
	}
	d, e := Parse(nte)
	if e != nil {
		return e
	}
	defs[d.ID] = d
	// DATA has precedence over ROOT. A bad file rejects the whole reload.
	for _, dir := range h.dirs {
		entries, err := os.ReadDir(dir)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return err
		}
		for _, entry := range entries {
			if entry.IsDir() || !strings.HasSuffix(strings.ToLower(entry.Name()), ".json") {
				continue
			}
			path := filepath.Join(dir, entry.Name())
			resolved, err := filepath.EvalSymlinks(path)
			if err != nil {
				return err
			}
			base, err := filepath.EvalSymlinks(dir)
			if err != nil {
				return err
			}
			rel, err := filepath.Rel(base, resolved)
			if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
				return fmt.Errorf("manifest escapes its directory: %s", path)
			}
			info, err := os.Stat(path)
			if err != nil {
				return err
			}
			if info.Size() > 1024*1024 {
				return fmt.Errorf("manifest too large: %s", path)
			}
			b, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			d, err := Parse(b)
			if err != nil {
				return fmt.Errorf("%s: %w", path, err)
			}
			if existing, ok := defs[d.ID]; ok && existing.Adapter != "" && existing.Adapter != "manifest" {
				return fmt.Errorf("cannot replace built-in %s", d.ID)
			}
			for _, key := range []string{"genshin", "hsr", "wuwa", "r1999"} {
				if d.ID == key {
					return fmt.Errorf("reserved adapter id %s", key)
				}
			}
			d.ManifestPath = path
			defs[d.ID] = d
		}
	}
	adapters := []game.Adapter{}
	ids := []string{}
	for _, d := range defs {
		if len(d.TaskTypesMap) > 0 {
			adapters = append(adapters, d)
			ids = append(ids, d.ID)
		}
	}
	h.reg.Replace(h.manifestIDs, adapters)
	h.manifestIDs = ids
	h.definitions = defs
	return nil
}
func (h *Hub) Definitions() []Definition {
	h.mu.RLock()
	defer h.mu.RUnlock()
	out := []Definition{}
	for _, d := range h.definitions {
		out = append(out, *d)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}
func (h *Hub) Definition(id string) (*Definition, error) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	d, ok := h.definitions[id]
	if !ok {
		return nil, fmt.Errorf("unknown helper definition %s", id)
	}
	return d, nil
}
func (h *Hub) ValidateInstance(v store.HelperInstance) error {
	if !identifier.MatchString(v.ID) || len(v.ID) > 100 || v.Name == "" {
		return fmt.Errorf("valid id and name required")
	}
	if _, err := h.Definition(v.HelperID); err != nil {
		return err
	}
	switch v.LocationMode {
	case "managed", "external", "discovered":
	default:
		return fmt.Errorf("location_mode must be managed, external or discovered")
	}
	for _, p := range []string{v.Executable, v.WorkingDir, v.RuntimePath, v.ConfigDir, v.DataDir} {
		if _, e := h.Paths.Resolve(p); e != nil {
			return e
		}
	}
	return nil
}
func (h *Hub) Discovery() (config.DiscoveryConfig, error) {
	v := h.discovery
	e := h.Store.HelperSetting("discovery", &v)
	if errors.Is(e, store.ErrNotFound) {
		e = nil
	}
	return v, e
}
func (h *Hub) SaveDiscovery(v config.DiscoveryConfig) error {
	if v.MaxDepth < 1 || v.MaxDepth > 20 {
		return fmt.Errorf("max_depth must be 1..20")
	}
	for _, p := range v.Roots {
		if p == "" {
			return fmt.Errorf("empty discovery root")
		}
		if _, e := h.Paths.Resolve(p); e != nil {
			return e
		}
	}
	return h.Store.SaveHelperSetting("discovery", v)
}
func (h *Hub) Discover(ctx context.Context, paths []string, depth int) (discover.Result, error) {
	cfg, e := h.Discovery()
	if e != nil {
		return discover.Result{}, e
	}
	if len(paths) == 0 {
		paths = cfg.Roots
		if cfg.ScanAllLocalDrives {
			paths = append(append([]string{}, paths...), discover.DefaultRoots()...)
		}
		if len(paths) == 0 {
			paths = []string{h.Paths.Helpers}
		}
	}
	if depth <= 0 {
		depth = cfg.MaxDepth
	}
	if depth > 20 {
		return discover.Result{}, fmt.Errorf("max_depth must be <=20")
	}
	roots := []string{}
	for _, p := range paths {
		v, e := h.Paths.Resolve(p)
		if e != nil {
			return discover.Result{}, e
		}
		roots = append(roots, v)
	}
	signatures := append([]discover.Tool{}, discover.Tools...)
	for _, d := range h.Definitions() {
		if len(d.TaskTypesMap) > 0 {
			signatures = append(signatures, discover.Tool{Adapter: d.ID, Name: d.DisplayName, Exe: d.Discovery.ExecutableNames, Dirs: d.Discovery.DirectoryHints})
		}
	}
	return discover.Scan(ctx, discover.Options{Paths: roots, MaxDepth: depth, Tools: signatures}), nil
}
func str(m map[string]any, k string) string { s, _ := m[k].(string); return s }

// Resolve returns copies. Persisted variable strings and external paths are never rewritten.
func (h *Hub) Resolve(g store.Game, t store.Task) (store.Game, store.Task, *store.HelperInstance, error) {
	p, e := t.ParamsMap()
	if e != nil {
		return g, t, nil, e
	}
	if p == nil {
		p = map[string]any{}
	}
	ec, e := g.ExtraConfigMap()
	if e != nil {
		return g, t, nil, e
	}
	if ec == nil {
		ec = map[string]any{}
	}
	id := str(p, "helper_instance_id")
	if id == "" {
		id = str(ec, "helper_instance_id")
	}
	var instance *store.HelperInstance
	var def *Definition
	if id != "" {
		v, e := h.Store.GetHelper(id)
		if e != nil {
			return g, t, nil, fmt.Errorf("helper instance %s: %w", id, e)
		}
		instance = &v
		if !v.Enabled {
			return g, t, instance, fmt.Errorf("helper instance %s is disabled", id)
		}
		def, e = h.Definition(v.HelperID)
		if e != nil {
			return g, t, instance, e
		}
		g.Adapter = def.Adapter
		if g.Adapter == "" || g.Adapter == "manifest" {
			g.Adapter = def.ID
		}
		if v.Executable != "" {
			g.ToolPath = v.Executable
		}
		if v.WorkingDir != "" {
			g.WorkingDir = v.WorkingDir
		}
		if v.RuntimePath != "" {
			ec["python_path"] = v.RuntimePath
		}
		if g.Adapter == "hsr" {
			key := "march7th_dir"
			entry := "march7th_entry"
			if t.Type == "fhoe_route" {
				key = "fhoe_dir"
				entry = "fhoe_entry"
			}
			if g.WorkingDir != "" {
				ec[key] = g.WorkingDir
			}
			if strings.HasSuffix(strings.ToLower(v.Executable), ".py") {
				ec[entry] = v.Executable
				if str(ec, key) == "" {
					ec[key] = filepath.Dir(v.Executable)
				}
			}
		}
	}
	if def == nil {
		def, _ = h.Definition(g.Adapter)
	}
	if str(p, "exe") != "" {
		g.ToolPath = str(p, "exe")
	}
	if g.ToolPath == "" && def != nil {
		g.ToolPath = def.Launch.DefaultExecutable
	}
	// Discovery is explicit: stored discovered instances supply their path. There is no ambiguous automatic choice.
	resolve := func(v *string) error { var e error; *v, e = h.Paths.Resolve(*v); return e }
	allowPATH := def != nil && def.Launch.AllowPATH
	if g.ToolPath != "" {
		if allowPATH && !strings.ContainsAny(g.ToolPath, `/\$`) {
			g.ToolPath, e = exec.LookPath(g.ToolPath)
		} else {
			e = resolve(&g.ToolPath)
		}
		if e != nil {
			return g, t, instance, e
		}
	}
	if str(p, "exe") != "" {
		p["exe"] = g.ToolPath
	}
	if str(p, "working_dir") != "" {
		g.WorkingDir = str(p, "working_dir")
	}
	if g.WorkingDir == "" && g.Adapter == "hsr" {
		key := "march7th_dir"
		if t.Type == "fhoe_route" {
			key = "fhoe_dir"
		}
		g.WorkingDir = str(ec, key)
	}
	if g.WorkingDir == "" && def != nil {
		g.WorkingDir = strings.ReplaceAll(def.Launch.WorkingDir, "${EXECUTABLE_DIR}", filepath.Dir(g.ToolPath))
	}
	if g.WorkingDir == "" && g.ToolPath != "" {
		g.WorkingDir = filepath.Dir(g.ToolPath)
	}
	if e = resolve(&g.WorkingDir); e != nil {
		return g, t, instance, e
	}
	if g.WorkingDir != "" {
		p["working_dir"] = g.WorkingDir
	}
	for k, v := range ec {
		s, ok := v.(string)
		if !ok || s == "" {
			continue
		}
		if strings.HasSuffix(k, "_dir") || k == "python_path" || strings.Contains(s, "${") {
			if k == "python_path" && s == "python" {
				continue
			}
			if strings.HasSuffix(k, "_entry") && !strings.Contains(s, "${") && !filepath.IsAbs(s) {
				continue
			}
			s, e = h.Paths.Resolve(s)
			if e != nil {
				return g, t, instance, e
			}
			ec[k] = s
		}
	}
	// The legacy hsr definition explicitly retains its documented system-Python default.
	if g.Adapter == "hsr" {
		if str(p, "exe") == "" {
			py := str(ec, "python_path")
			if py == "" || py == "python" {
				py, e = exec.LookPath("python")
				if e == nil {
					ec["python_path"] = py
				}
			}
		}
		if g.WorkingDir != "" {
			key := "march7th_dir"
			if t.Type == "fhoe_route" {
				key = "fhoe_dir"
			}
			ec[key] = g.WorkingDir
		}
	}
	// Expand only variable-bearing task data, preserving literal raw argv and semantic strings.
	var expand func(any) (any, error)
	expand = func(v any) (any, error) {
		switch x := v.(type) {
		case string:
			if strings.Contains(x, "${") {
				return h.Paths.Expand(x)
			}
		case []any:
			for i, item := range x {
				y, e := expand(item)
				if e != nil {
					return nil, e
				}
				x[i] = y
			}
		case map[string]any:
			for k, item := range x {
				if k == "raw_args" {
					continue
				}
				y, e := expand(item)
				if e != nil {
					return nil, e
				}
				x[k] = y
			}
		}
		return v, nil
	}
	if _, e = expand(p); e != nil {
		return g, t, instance, e
	}
	if def != nil {
		if tt, ok := def.TaskTypesMap[t.Type]; ok {
			for k, f := range tt.Fields {
				if f.Type != "file" && f.Type != "directory" {
					continue
				}
				if _, ok := p[k]; !ok && f.Default != nil {
					p[k] = f.Default
				}
				if value, ok := p[k].(string); ok && value != "" {
					v, e := h.Paths.Resolve(value)
					if e != nil {
						return g, t, instance, e
					}
					p[k] = v
				}
			}
		}
	}
	b, _ := json.Marshal(ec)
	g.ExtraConfig = string(b)
	b, _ = json.Marshal(p)
	t.Params = string(b)
	return g, t, instance, nil
}
