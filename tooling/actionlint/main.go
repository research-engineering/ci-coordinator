package main

import (
	"fmt"
	"os"
	_ "time/tzdata" // Resolve schedule.timezone even on runners without system zoneinfo.

	"github.com/rhysd/actionlint"
)

func configureJobIdentity() error {
	job, ok := actionlint.BuiltinGlobalVariableTypes["job"].(*actionlint.ObjectType)
	if !ok || !job.IsStrict() {
		return fmt.Errorf("actionlint job type is not the admitted strict object")
	}
	fields := []string{"workflow_ref", "workflow_sha", "workflow_repository", "workflow_file_path"}
	for _, field := range fields {
		if _, exists := job.Props[field]; exists {
			return fmt.Errorf("upstream defines %s; review and retire the compatibility extension", field)
		}
	}
	for _, field := range fields {
		job.Props[field] = actionlint.StringType{}
	}
	return nil
}

func main() {
	if err := configureJobIdentity(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	cmd := actionlint.Command{Stdin: os.Stdin, Stdout: os.Stdout, Stderr: os.Stderr}
	os.Exit(cmd.Main(os.Args))
}
