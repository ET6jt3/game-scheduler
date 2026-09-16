package task

import (
	"encoding/json"
	"fmt"
	"github.com/xiabee/game-scheduler/internal/runner"
	"github.com/xiabee/game-scheduler/internal/store"
)

func (s *Service) PreflightHelper(id string, kind string, params map[string]any) (Preflight, error) {
	if s.Helpers == nil {
		return Preflight{}, fmt.Errorf("helpers unavailable")
	}
	h, e := s.store.GetHelper(id)
	if e != nil {
		return Preflight{}, e
	}
	d, e := s.Helpers.Definition(h.HelperID)
	if e != nil {
		return Preflight{}, e
	}
	adapter := d.Adapter
	if adapter == "" || adapter == "manifest" {
		adapter = d.ID
	}
	if params == nil {
		params = map[string]any{}
	}
	params["helper_instance_id"] = id
	b, _ := json.Marshal(params)
	if kind == "" {
		if h.HelperID == "ok-nte" {
			kind = "task"
		} else {
			kind = "raw"
		}
	}
	return s.preflightExternal(store.Game{Adapter: adapter, Enabled: true}, store.Task{Name: h.Name, Type: kind, Params: string(b)})
}
func (s *Service) addHelperChecks(pf *Preflight, g store.Game, t store.Task, spec runner.Spec) {
	if pf.HelperInstance != nil {
		for k, path := range map[string]string{"config_dir": pf.HelperInstance.ConfigDir, "data_dir": pf.HelperInstance.DataDir} {
			if path != "" {
				resolved, e := s.Helpers.Paths.Resolve(path)
				if e != nil {
					pf.Missing = append(pf.Missing, e.Error())
				} else {
					pf.addDirCheck(k, resolved)
				}
			}
		}
	}
	// Resolution makes non-PATH-enabled executables absolute before this point.
	if d, e := s.Helpers.Definition(g.Adapter); e == nil {
		if d.Requirements.Foreground {
			pf.Warnings = append(pf.Warnings, "Helper may require the interactive desktop; keep execution concurrency at 1.")
		}
		if tt, ok := d.TaskTypesMap[t.Type]; ok {
			p, _ := t.ParamsMap()
			for k, f := range tt.Fields {
				v, ok := p[k]
				if !ok {
					v = f.Default
				}
				if path, ok := v.(string); ok && path != "" {
					if f.Type == "file" {
						pf.addFileCheck(k, path)
					}
					if f.Type == "directory" {
						pf.addDirCheck(k, path)
					}
				}
			}
		}
	}
}
