package helper

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestNTEArguments(t *testing.T) {
	d, e := Parse(nte)
	if e != nil {
		t.Fatal(e)
	}
	for _, tc := range []struct {
		p    string
		want []string
		bad  bool
	}{{`{"task_index":2}`, []string{"-t", "2", "-e"}, false}, {`{"task_index":3,"exit":false}`, []string{"-t", "3"}, false}, {`{"task_index":0}`, nil, true}, {`{"task_index":1.2}`, nil, true}, {`{"task_index":"2"}`, nil, true}, {`{}`, nil, true}, {`{"task_index":2,"exit":"true"}`, nil, true}} {
		var p map[string]any
		_ = json.Unmarshal([]byte(tc.p), &p)
		got, e := d.Arguments("task", p)
		if (e != nil) != tc.bad || (!tc.bad && !reflect.DeepEqual(got, tc.want)) {
			t.Fatalf("%s: %v %v", tc.p, got, e)
		}
	}
	var p map[string]any
	_ = json.Unmarshal([]byte(`{"raw_args":["space value","$(touch SHOULD_NOT_EXIST)","${ROOT}","中文","", "a\"b"]}`), &p)
	got, e := d.Arguments("raw", p)
	if e != nil || len(got) != 6 || got[2] != "${ROOT}" {
		t.Fatal(got, e)
	}
	if _, e = d.Arguments("raw", map[string]any{"raw_args": []any{1.0}}); e == nil {
		t.Fatal("non-string raw accepted")
	}
}
func TestManifestValidationAndRepeated(t *testing.T) {
	valid := `{"schema_version":1,"id":"future","display_name":"Future","task_types":{"daily":{"fields":{"profile":{"type":"enum","enum":["main","alt"],"required":true},"tags":{"type":"string","repeated":true},"count":{"type":"number","min":0}},"args":["--profile","{{profile}}",{"repeat":"tags","flag":"--tag"}]}}}`
	d, e := Parse([]byte(valid))
	if e != nil {
		t.Fatal(e)
	}
	args, e := d.Arguments("daily", map[string]any{"profile": "main", "tags": []any{"a b", ";echo x"}})
	if e != nil || !reflect.DeepEqual(args, []string{"--profile", "main", "--tag", "a b", "--tag", ";echo x"}) {
		t.Fatal(args, e)
	}
	if _, e = d.Arguments("daily", map[string]any{"profile": "bad"}); e == nil {
		t.Fatal("enum accepted")
	}
	for _, s := range []string{strings.Replace(valid, `"schema_version":1`, `"schema_version":2`, 1), strings.Replace(valid, `{{profile}}`, `{{unknown}}`, 1), strings.Replace(valid, `{{profile}}`, `{{profile}`, 1), strings.Replace(valid, `"type":"number"`, `"type":"shell"`, 1), valid + `{}`, strings.Replace(valid, `"schema_version":1`, `"shell":"bash","schema_version":1`, 1)} {
		if _, e := Parse([]byte(s)); e == nil {
			t.Fatal("invalid accepted", s)
		}
	}
}
