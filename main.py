"""CLI entry point; keep dependencies and business logic in study_agent/."""
import sys

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        from study_agent.cli import main
        raise SystemExit(main())
    except ModuleNotFoundError as exc:
        print(f"缺少依赖 {exc.name}。请先执行：python -m pip install -r requirements.txt", file=sys.stderr)
        raise SystemExit(2)
