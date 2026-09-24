// Package helper implements data-only helper definitions and installation resolution.
package helper

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/xiabee/game-scheduler/internal/game"
	"github.com/xiabee/game-scheduler/internal/game/cmdutil"
	"github.com/xiabee/game-scheduler/internal/runner"
	"github.com/xiabee/game-scheduler/internal/store"
)

type Definition struct {
	SchemaVersion int    `json:"schema_version"`
	ID            string `json:"id"`
	DisplayName   string `json:"display_name"`
	GameName      string `json:"game_name,omitempty"`
	Adapter       string `json:"adapter,omitempty"`
	ManifestPath  string `json:"manifest_path,omitempty"`
	Discovery     struct {
		ExecutableNames []string `json:"executable_names"`
		DirectoryHints  []string `json:"directory_hints"`
	} `json:"discovery"`
	Launch struct {
		DefaultExecutable string       `json:"default_executable"`
		WorkingDir        string       `json:"working_dir"`
		AllowPATH         bool         `json:"allow_path_lookup"`
		Worker            WorkerLaunch `json:"worker,omitempty"`
	} `json:"launch"`
	TaskTypesMap map[string]TaskType `json:"task_types"`
	Requirements struct {
		Foreground bool `json:"foreground"`
		WorkingDir bool `json:"working_dir"`
	} `json:"requirements"`
	Completion struct {
		Marker   string `json:"marker,omitempty"`
		GraceSec int    `json:"grace_sec,omitempty"`
	} `json:"completion,omitempty"`
}
type WorkerLaunch struct {
	Executable             string   `json:"executable,omitempty"`
	WorkingDir             string   `json:"working_dir,omitempty"`
	Entry                  string   `json:"entry,omitempty"`
	Bootstrap              string   `json:"bootstrap,omitempty"`
	TaskTypes              []string `json:"task_types,omitempty"`
	DefaultTimeoutSec      int      `json:"default_timeout_sec,omitempty"`
	PreserveTimeoutInChain bool     `json:"preserve_timeout_in_chain,omitempty"`
}

type Field struct {
	Type     string   `json:"type"`
	Required bool     `json:"required"`
	Default  any      `json:"default,omitempty"`
	Min      *float64 `json:"min,omitempty"`
	Max      *float64 `json:"max,omitempty"`
	Enum     []string `json:"enum,omitempty"`
	Repeated bool     `json:"repeated,omitempty"`
}
type TaskType struct {
	Fields  map[string]Field  `json:"fields"`
	Args    []json.RawMessage `json:"args"`
	RawArgs bool              `json:"raw_args"`
}
type argument struct {
	If     string `json:"if,omitempty"`
	Value  string `json:"value,omitempty"`
	Repeat string `json:"repeat,omitempty"`
	Flag   string `json:"flag,omitempty"`
}

var identifier = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]*$`)
var placeholder = regexp.MustCompile(`\{\{([a-zA-Z0-9_]+)\}\}`)

func decodeStrict(b []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(bytes.TrimPrefix(b, []byte{239, 187, 191})))
	d.DisallowUnknownFields()
	if e := d.Decode(v); e != nil {
		return e
	}
	var extra any
	if e := d.Decode(&extra); e != io.EOF {
		return fmt.Errorf("trailing JSON data")
	}
	return nil
}
func Parse(b []byte) (*Definition, error) {
	var d Definition
	if e := decodeStrict(b, &d); e != nil {
		return nil, e
	}
	if e := d.validate(); e != nil {
		return nil, e
	}
	return &d, nil
}
func (d *Definition) validate() error {
	if d.SchemaVersion != 1 {
		return fmt.Errorf("unknown manifest schema_version %d", d.SchemaVersion)
	}
	if !identifier.MatchString(d.ID) || d.DisplayName == "" || len(d.TaskTypesMap) == 0 {
		return fmt.Errorf("manifest requires valid id, display_name and task_types")
	}
	if d.Adapter != "" && d.Adapter != "manifest" {
		return fmt.Errorf("manifest adapter must be manifest")
	}
	for _, name := range append(append([]string{}, d.Discovery.ExecutableNames...), d.Discovery.DirectoryHints...) {
		if name == "" || name == "." || name == ".." || strings.ContainsAny(name, `/\`) {
			return fmt.Errorf("discovery names must be basenames")
		}
	}
	w := d.Launch.Worker
	if w.Executable != "" || w.WorkingDir != "" || w.Entry != "" || len(w.TaskTypes) > 0 {
		if !validWorkerRelativePath(w.Executable) || !validWorkerRelativePath(w.WorkingDir) || !validWorkerRelativePath(w.Entry) || len(w.TaskTypes) == 0 {
			return fmt.Errorf("launch.worker requires relative executable, working_dir, entry and task_types")
		}
		if w.DefaultTimeoutSec < 0 {
			return fmt.Errorf("launch.worker default_timeout_sec must be >= 0")
		}
		if w.Bootstrap != "" && (d.ID != "ok-nte" || w.Bootstrap != "runtime-services") {
			return fmt.Errorf("launch.worker bootstrap is reserved for built-in ok-nte runtime-services")
		}
		seen := map[string]bool{}
		for _, kind := range w.TaskTypes {
			if _, ok := d.TaskTypesMap[kind]; !ok {
				return fmt.Errorf("launch.worker references unknown task type %q", kind)
			}
			if seen[kind] {
				return fmt.Errorf("launch.worker duplicates task type %q", kind)
			}
			seen[kind] = true
		}
	}
	for name, t := range d.TaskTypesMap {
		if !identifier.MatchString(name) {
			return fmt.Errorf("invalid task type")
		}
		for key, f := range t.Fields {
			if !identifier.MatchString(key) {
				return fmt.Errorf("invalid field %s", key)
			}
			switch f.Type {
			case "string", "integer", "number", "boolean", "enum", "file", "directory":
			default:
				return fmt.Errorf("invalid field type %s", f.Type)
			}
			if f.Type == "enum" && len(f.Enum) == 0 {
				return fmt.Errorf("empty enum %s", key)
			}
			if f.Min != nil && f.Max != nil && *f.Min > *f.Max {
				return fmt.Errorf("invalid bounds %s", key)
			}
			if f.Default != nil {
				if _, e := fieldValue(key, f, f.Default); e != nil {
					return e
				}
			}
		}
		for _, raw := range t.Args {
			var literal string
			if json.Unmarshal(raw, &literal) == nil {
				if e := validateTemplate(literal, t.Fields); e != nil {
					return e
				}
				continue
			}
			var a argument
			if e := decodeStrict(raw, &a); e != nil {
				return e
			}
			if a.Repeat != "" {
				f, ok := t.Fields[a.Repeat]
				if !ok || !f.Repeated || a.If != "" || a.Value != "" {
					return fmt.Errorf("invalid repeat %s", a.Repeat)
				}
				continue
			}
			f, ok := t.Fields[a.If]
			if !ok || f.Type != "boolean" || f.Repeated || a.Flag != "" {
				return fmt.Errorf("invalid conditional %s", a.If)
			}
			if e := validateTemplate(a.Value, t.Fields); e != nil {
				return e
			}
		}
	}
	return nil
}
func validWorkerRelativePath(v string) bool {
	v = strings.ReplaceAll(strings.TrimSpace(v), `\`, "/")
	if v == "" || strings.HasPrefix(v, "/") || strings.IndexByte(v, 0) >= 0 || (len(v) >= 2 && v[1] == ':') {
		return false
	}
	for _, part := range strings.Split(v, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

func validateTemplate(v string, fields map[string]Field) error {
	for _, match := range placeholder.FindAllStringSubmatch(v, -1) {
		f, ok := fields[match[1]]
		if !ok || f.Repeated {
			return fmt.Errorf("unknown or repeated placeholder %s", match[1])
		}
	}
	rest := placeholder.ReplaceAllString(v, "")
	if strings.Contains(rest, "{{") || strings.Contains(rest, "}}") || strings.IndexByte(v, 0) >= 0 {
		return fmt.Errorf("malformed placeholder")
	}
	return nil
}
func fieldValue(k string, f Field, v any) (any, error) {
	if f.Repeated {
		arr, ok := v.([]any)
		if !ok {
			return nil, fmt.Errorf("%s requires an array", k)
		}
		f.Repeated = false
		out := []any{}
		for _, item := range arr {
			x, e := fieldValue(k, f, item)
			if e != nil {
				return nil, e
			}
			out = append(out, x)
		}
		return out, nil
	}
	bad := func() (any, error) { return nil, fmt.Errorf("%s must be %s", k, f.Type) }
	switch f.Type {
	case "boolean":
		if _, ok := v.(bool); !ok {
			return bad()
		}
	case "integer", "number":
		n, ok := v.(float64)
		if !ok || math.IsNaN(n) || math.IsInf(n, 0) || (f.Type == "integer" && (math.Trunc(n) != n || math.Abs(n) > 9007199254740991)) {
			return bad()
		}
		if (f.Min != nil && n < *f.Min) || (f.Max != nil && n > *f.Max) {
			return nil, fmt.Errorf("%s outside allowed range", k)
		}
	default:
		s, ok := v.(string)
		if !ok || strings.IndexByte(s, 0) >= 0 {
			return bad()
		}
		if f.Required && s == "" {
			return nil, fmt.Errorf("%s required", k)
		}
		if f.Type == "enum" {
			found := false
			for _, choice := range f.Enum {
				if s == choice {
					found = true
				}
			}
			if !found {
				return nil, fmt.Errorf("invalid enum %s", k)
			}
		}
	}
	return v, nil
}
func scalar(v any) string {
	if n, ok := v.(float64); ok {
		return strconv.FormatFloat(n, 'f', -1, 64)
	}
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func (d *Definition) Arguments(kind string, params map[string]any) ([]string, error) {
	t, ok := d.TaskTypesMap[kind]
	if !ok {
		return nil, fmt.Errorf("unknown task type %q", kind)
	}
	if t.RawArgs {
		v, ok := params["raw_args"].([]any)
		if !ok {
			return nil, fmt.Errorf("raw_args must be a string array")
		}
		out := []string{}
		for _, item := range v {
			s, ok := item.(string)
			if !ok || strings.IndexByte(s, 0) >= 0 {
				return nil, fmt.Errorf("raw_args must contain strings without NUL")
			}
			out = append(out, s)
		}
		return out, nil
	}
	values := map[string]any{}
	for k, f := range t.Fields {
		v, ok := params[k]
		if !ok {
			v = f.Default
		}
		if v == nil {
			if f.Required {
				return nil, fmt.Errorf("%s required", k)
			}
			continue
		}
		v, e := fieldValue(k, f, v)
		if e != nil {
			return nil, e
		}
		values[k] = v
	}
	expand := func(s string) string {
		return placeholder.ReplaceAllStringFunc(s, func(m string) string { return scalar(values[placeholder.FindStringSubmatch(m)[1]]) })
	}
	out := []string{}
	for _, raw := range t.Args {
		var literal string
		if json.Unmarshal(raw, &literal) == nil {
			out = append(out, expand(literal))
			continue
		}
		var a argument
		_ = json.Unmarshal(raw, &a)
		if a.Repeat != "" {
			arr, _ := values[a.Repeat].([]any)
			for _, v := range arr {
				if a.Flag != "" {
					out = append(out, a.Flag)
				}
				out = append(out, scalar(v))
			}
		} else if values[a.If] == true {
			out = append(out, expand(a.Value))
		}
	}
	return out, nil
}
func (d *Definition) Key() string { return d.ID }
func (d *Definition) Validate(g store.Game) error {
	if g.ToolPath == "" {
		return fmt.Errorf("%s executable is not configured", d.ID)
	}
	return nil
}
func (d *Definition) TaskTypes() []string {
	out := []string{}
	for k := range d.TaskTypesMap {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func (d *Definition) workerEnabled(g store.Game, t store.Task, params map[string]any) bool {
	w := d.Launch.Worker
	if w.Executable == "" {
		return false
	}
	if override, ok := params["exe"].(string); ok && strings.TrimSpace(override) != "" {
		return false
	}
	enabledType := false
	for _, kind := range w.TaskTypes {
		if kind == t.Type {
			enabledType = true
			break
		}
	}
	if !enabledType {
		return false
	}
	base := filepath.Base(g.ToolPath)
	for _, name := range d.Discovery.ExecutableNames {
		if strings.EqualFold(base, name) {
			return true
		}
	}
	return false
}

func workerJoin(root, rel string) string {
	rel = strings.ReplaceAll(rel, `\`, "/")
	return filepath.Join(root, filepath.FromSlash(rel))
}

// WorkerEntryPath reports the real worker entry file when this invocation uses
// a manifest-declared worker instead of the visible/updater launcher.
func (d *Definition) WorkerEntryPath(g store.Game, t store.Task, spec runner.Spec) (string, bool) {
	p, e := t.ParamsMap()
	if e != nil || !d.workerEnabled(g, t, p) {
		return "", false
	}
	root := filepath.Dir(g.ToolPath)
	workerDir := workerJoin(root, d.Launch.Worker.WorkingDir)
	return workerJoin(workerDir, d.Launch.Worker.Entry), true
}

func (d *Definition) BuildCommand(g store.Game, t store.Task) (runner.Spec, error) {
	p, e := t.ParamsMap()
	if e != nil {
		return runner.Spec{}, e
	}
	args, e := d.Arguments(t.Type, p)
	if e != nil {
		return runner.Spec{}, e
	}
	if d.workerEnabled(g, t, p) {
		root := filepath.Dir(g.ToolPath)
		workerDir := workerJoin(root, d.Launch.Worker.WorkingDir)
		workerExe := workerJoin(root, d.Launch.Worker.Executable)
		entry := workerJoin(workerDir, d.Launch.Worker.Entry)
		timeout := cmdutil.Timeout(t)
		if timeout <= 0 && d.Launch.Worker.DefaultTimeoutSec > 0 {
			timeout = time.Duration(d.Launch.Worker.DefaultTimeoutSec) * time.Second
		}
		spec := runner.Spec{
			Path:                   workerExe,
			Args:                   append([]string{entry}, args...),
			Dir:                    workerDir,
			Timeout:                timeout,
			PreserveTimeoutInChain: d.Launch.Worker.PreserveTimeoutInChain,
		}
		if d.Launch.Worker.Bootstrap == "runtime-services" {
			spec.Args = []string{"-c", okNTEHeadlessBootstrap}
			spec.Env = append(spec.Env, "PYTHONIOENCODING=utf-8", "PYTHONUTF8=1")
		}
		if d.Completion.Marker != "" {
			spec.CompletionMarker = d.Completion.Marker
			if d.Completion.GraceSec > 0 {
				spec.CompletionGrace = time.Duration(d.Completion.GraceSec) * time.Second
			}
		}
		return spec, nil
	}
	spec := cmdutil.BaseSpec(g, t, p, args)
	if d.Completion.Marker != "" {
		spec.CompletionMarker = d.Completion.Marker
		if d.Completion.GraceSec > 0 {
			spec.CompletionGrace = time.Duration(d.Completion.GraceSec) * time.Second
		}
	}
	return spec, nil
}
func (d *Definition) TaskSchema() []game.TaskTypeInfo {
	out := []game.TaskTypeInfo{}
	for _, name := range d.TaskTypes() {
		t := d.TaskTypesMap[name]
		item := game.TaskTypeInfo{Type: name, Label: name, Fields: []game.Field{}}
		keys := []string{}
		for k := range t.Fields {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			f := t.Fields[k]
			typ := "text"
			switch f.Type {
			case "boolean":
				typ = "bool"
			case "integer", "number":
				typ = "number"
			}
			if f.Repeated {
				typ = "json"
			}
			item.Fields = append(item.Fields, game.Field{Key: k, Label: k, Type: typ, Required: f.Required, Default: f.Default, Help: strings.Join(f.Enum, ", ")})
		}
		if t.RawArgs {
			item.Fields = append(item.Fields, game.Field{Key: "raw_args", Label: "Exact argv (JSON array)", Type: "json", Required: true})
		}
		out = append(out, item)
	}
	return out
}
