package portable

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPathsRelocationExternalAndUnicode(t *testing.T) {
	a := t.TempDir()
	b := filepath.Join(t.TempDir(), "Moved space 游戏")
	external := filepath.Join(t.TempDir(), "external tool.exe")
	for _, root := range []string{a, b} {
		p, e := New(root, "${ROOT}/../Data", "${ROOT}/../Helpers", "")
		if e != nil {
			t.Fatal(e)
		}
		for input, want := range map[string]string{"${ROOT}/asset": filepath.Join(root, "asset"), "${HELPERS}/ok-nte/ok-nte.exe": filepath.Clean(filepath.Join(root, "../Helpers/ok-nte/ok-nte.exe")), external: external, "relative/file": filepath.Join(root, "relative/file")} {
			got, e := p.Resolve(input)
			if e != nil || got != want {
				t.Fatalf("%s: %s %v want %s", input, got, e, want)
			}
		}
		for _, invalid := range []string{"${UNKNOWN}/x", "${root}/x", "${ROOT/x"} {
			if _, e := p.Resolve(invalid); e == nil {
				t.Fatalf("accepted %s", invalid)
			}
		}
	}
	t.Setenv("USERPROFILE", a)
	p, _ := New(a, "", "", "")
	got, e := p.Resolve("${USERPROFILE}/x")
	if e != nil || got != filepath.Join(a, "x") {
		t.Fatal(got, e)
	}
	_ = os.PathSeparator
}
