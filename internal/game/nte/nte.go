// Package nte adapts ok-nte.exe for Neverness to Everness.
//
// ok-nte exposes a numbered-task CLI: `ok-nte.exe -t N -e`. The adapter only
// builds and launches that command; it does not inspect or modify the game.
package nte

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/xiabee/game-scheduler/internal/game/cmdutil"
	"github.com/xiabee/game-scheduler/internal/runner"
	"github.com/xiabee/game-scheduler/internal/store"
)

// Key is the adapter identifier persisted in games.adapter.
const Key = "nte"

// Adapter implements game.Adapter for ok-nte.exe.
type Adapter struct{}

func New() *Adapter                    { return &Adapter{} }
func (a *Adapter) Key() string         { return Key }
func (a *Adapter) TaskTypes() []string { return []string{"task", "raw"} }

func (a *Adapter) Validate(g store.Game) error {
	if strings.TrimSpace(g.ToolPath) == "" {
		return fmt.Errorf("nte: tool_path (ok-nte.exe) must be set")
	}
	return nil
}

// BuildCommand maps:
//   - task: {"task_index": N, "exit": true} => ok-nte.exe -t N [-e]
//   - raw:  {"raw_args": [...]}            => exact argv
func (a *Adapter) BuildCommand(g store.Game, t store.Task) (runner.Spec, error) {
	params, err := t.ParamsMap()
	if err != nil {
		return runner.Spec{}, fmt.Errorf("nte: bad params: %w", err)
	}
	if raw, ok := cmdutil.RawArgs(params); ok {
		return cmdutil.BaseSpec(g, t, params, raw), nil
	}

	switch t.Type {
	case "task":
		idxText := cmdutil.IntStr(params, "task_index")
		if idxText == "" {
			return runner.Spec{}, fmt.Errorf("nte: task requires params.task_index")
		}
		idx, err := strconv.Atoi(idxText)
		if err != nil || idx < 1 {
			return runner.Spec{}, fmt.Errorf("nte: params.task_index must be an integer >= 1")
		}
		args := []string{"-t", strconv.Itoa(idx)}
		if cmdutil.Bool(params, "exit", true) {
			args = append(args, "-e")
		}
		return cmdutil.BaseSpec(g, t, params, args), nil
	case "raw":
		return runner.Spec{}, fmt.Errorf("nte: type 'raw' requires params.raw_args")
	default:
		return runner.Spec{}, fmt.Errorf("nte: unknown task type %q", t.Type)
	}
}
