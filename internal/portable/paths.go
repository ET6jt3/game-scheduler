// Package portable resolves persisted paths without changing their stored form.
package portable

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

type Paths struct{ Root, Data, Helpers, Runtime string }

var variable = regexp.MustCompile(`\$\{([A-Z_]+)\}`)

func New(root, data, helpers, runtime string) (Paths, error) {
	root, err := filepath.Abs(root)
	if err != nil {
		return Paths{}, err
	}
	p := Paths{Root: root, Data: filepath.Join(root, "data"), Helpers: filepath.Join(root, "Helpers"), Runtime: filepath.Join(root, "Runtime")}
	for _, item := range []struct {
		in  string
		out *string
	}{{data, &p.Data}, {helpers, &p.Helpers}, {runtime, &p.Runtime}} {
		if item.in != "" {
			v, e := p.Resolve(item.in)
			if e != nil {
				return p, e
			}
			*item.out = v
		}
	}
	return p, nil
}
func (p Paths) Expand(value string) (string, error) {
	values := map[string]string{"ROOT": p.Root, "DATA": p.Data, "HELPERS": p.Helpers, "RUNTIME": p.Runtime}
	for _, k := range []string{"USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES"} {
		values[k] = os.Getenv(k)
	}
	var err error
	out := variable.ReplaceAllStringFunc(value, func(s string) string {
		k := variable.FindStringSubmatch(s)[1]
		v, ok := values[k]
		if !ok || v == "" {
			err = fmt.Errorf("path variable %s is not available", s)
		}
		return v
	})
	if strings.Contains(out, "${") {
		err = fmt.Errorf("malformed or recursive path variable in %q", value)
	}
	return out, err
}
func (p Paths) Resolve(value string) (string, error) {
	if value == "" {
		return "", nil
	}
	value, err := p.Expand(value)
	if err != nil {
		return "", err
	}
	if !filepath.IsAbs(value) {
		value = filepath.Join(p.Root, value)
	}
	return filepath.Clean(value), nil
}
