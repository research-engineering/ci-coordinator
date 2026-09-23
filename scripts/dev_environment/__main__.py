import sys

if sys.argv[1:2] == ["doctor"]:
    from scripts.dev_environment.doctor import main

    raise SystemExit(main(sys.argv[2:]))
if sys.argv[1:2] == ["cancel-watch"]:
    from scripts.dev_environment.watch_session import main

    raise SystemExit(main(sys.argv[2:]))

try:
    from scripts.dev_environment.cli import main
except ImportError:
    from scripts.dev_environment.diagnostics import Diagnostic, Reason, write_diagnostic

    write_diagnostic(sys.stderr, Diagnostic(Reason.DEPENDENCIES_MISSING, "command"), "json")
    raise SystemExit(2) from None

raise SystemExit(main())
