// Package game defines the adapter contract that turns a stored Task into an
// external-process invocation, plus a registry of the available adapters.
//
// Adapters NEVER inject code, read/write game memory, manipulate packets, or
// implement anti-detection logic. They only translate configuration into a
// command line for an already-installed local tool, which runner then spawns
// as a child process.
package game

import (
	"fmt"
	"sort"
	"sync"

	"github.com/xiabee/game-scheduler/internal/runner"
	"github.com/xiabee/game-scheduler/internal/store"
)

// Adapter builds a runnable command for one game's automation tool.
type Adapter interface {
	// Key is the stable identifier stored in Game.Adapter.
	Key() string
	// Validate checks that the game configuration is usable by this adapter.
	Validate(g store.Game) error
	// BuildCommand translates a task (in the context of its game) into a spec
	// that runner can execute.
	BuildCommand(g store.Game, t store.Task) (runner.Spec, error)
	// TaskTypes returns the task types this adapter understands, for docs/UX.
	TaskTypes() []string
}

// Registry maps adapter keys to implementations.
type Registry struct {
	mu sync.RWMutex
	m  map[string]Adapter
}

// NewRegistry builds a registry from the given adapters.
func NewRegistry(adapters ...Adapter) *Registry {
	r := &Registry{m: map[string]Adapter{}}
	for _, a := range adapters {
		r.m[a.Key()] = a
	}
	return r
}

// Get returns the adapter for key.
func (r *Registry) Get(key string) (Adapter, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	a, ok := r.m[key]
	if !ok {
		return nil, fmt.Errorf("game: no adapter registered for %q", key)
	}
	return a, nil
}

// Keys returns the registered adapter keys, sorted.
func (r *Registry) Keys() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	keys := make([]string, 0, len(r.m))
	for k := range r.m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// AdapterInfo describes an adapter for UI/metadata purposes. TaskTypes carries
// the full form schema so the dashboard can render graphical settings per task
// type instead of raw JSON.
type AdapterInfo struct {
	Key       string         `json:"key"`
	TaskTypes []TaskTypeInfo `json:"task_types"`
}

// Meta returns metadata for all adapters, sorted by key — used to populate the
// dashboard's add-game / add-task forms. Adapters without a UI schema fall back
// to bare type names.
func (r *Registry) Meta() []AdapterInfo {
	out := []AdapterInfo{}
	for _, k := range r.Keys() {
		a, err := r.Get(k)
		if err != nil {
			continue
		}
		tts := Schema(k)
		if provider, ok := a.(interface{ TaskSchema() []TaskTypeInfo }); ok {
			tts = provider.TaskSchema()
		}
		if tts == nil {
			for _, t := range a.TaskTypes() {
				tts = append(tts, TaskTypeInfo{Type: t, Label: t, Fields: []Field{}})
			}
		}
		out = append(out, AdapterInfo{Key: k, TaskTypes: tts})
	}
	return out
}

// Replace installs an atomic manifest snapshot without disturbing built-in adapters.
func (r *Registry) Replace(remove []string, adapters []Adapter) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, k := range remove {
		delete(r.m, k)
	}
	for _, a := range adapters {
		r.m[a.Key()] = a
	}
}
