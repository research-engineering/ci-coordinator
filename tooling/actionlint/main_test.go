package main

import (
	"bytes"
	"fmt"
	"go/parser"
	"go/token"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/rhysd/actionlint"
)

func TestEmbeddedScheduleTimezones(t *testing.T) {
	source, err := parser.ParseFile(token.NewFileSet(), "main.go", nil, parser.ImportsOnly)
	if err != nil {
		t.Fatal(err)
	}
	embedded := false
	for _, spec := range source.Imports {
		path, err := strconv.Unquote(spec.Path.Value)
		if err != nil {
			t.Fatal(err)
		}
		if path == "time/tzdata" && spec.Name != nil && spec.Name.Name == "_" {
			embedded = true
		}
	}
	if !embedded {
		t.Fatal("actionlint must embed the schedule.timezone database")
	}
	for _, name := range []string{"Europe/Berlin", "America/New_York", "Asia/Kathmandu"} {
		if _, err := time.LoadLocation(name); err != nil {
			t.Errorf("valid schedule zone %q: %v", name, err)
		}
	}
	if _, err := time.LoadLocation("Invalid/Not_A_Zone"); err == nil {
		t.Fatal("invalid timezone was admitted")
	}
}

func TestProviderJobIdentityRemainsStrict(t *testing.T) {
	original := actionlint.BuiltinGlobalVariableTypes["job"]
	actionlint.BuiltinGlobalVariableTypes["job"] = original.DeepCopy()
	t.Cleanup(func() { actionlint.BuiltinGlobalVariableTypes["job"] = original })
	if err := configureJobIdentity(); err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		name, expression string
		valid            bool
	}{
		{"ref", "job.workflow_ref", true},
		{"sha", "job.workflow_sha", true},
		{"repository", "job.workflow_repository", true},
		{"file", "job.workflow_file_path", true},
		{"typo", "job.workflow_shaa", false},
		{"string-not-object", "job.workflow_sha.value", false},
		{"existing-property", "job.status", true},
	} {
		t.Run(test.name, func(t *testing.T) {
			source := fmt.Sprintf("name: Probe\non: push\njobs:\n  probe:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo ok\n        env:\n          VALUE: ${{ %s }}\n", test.expression)
			var output bytes.Buffer
			cmd := actionlint.Command{Stdin: strings.NewReader(source), Stdout: &output, Stderr: &output}
			code := cmd.Main([]string{"actionlint", "-shellcheck=", "-pyflakes=", "-"})
			if (code == 0) != test.valid {
				t.Fatalf("code=%d valid=%v: %s", code, test.valid, output.String())
			}
		})
	}
	var output bytes.Buffer
	source := "name: Probe\non: push\njobs:\n  probe:\n    if: job.workflow_ref != ''\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo ok\n"
	cmd := actionlint.Command{Stdin: strings.NewReader(source), Stdout: &output, Stderr: &output}
	if code := cmd.Main([]string{"actionlint", "-shellcheck=", "-pyflakes=", "-"}); code == 0 {
		t.Fatal("job context unexpectedly admitted at job-level if")
	}
	if err := configureJobIdentity(); err == nil {
		t.Fatal("existing upstream field did not trigger compatibility review")
	}
}
