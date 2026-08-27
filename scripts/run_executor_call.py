import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.executor_client import call_executor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        kwargs = json.loads(input_path.read_text(encoding="utf-8"))
        result = call_executor(args.function, **kwargs)
        output_path.write_text(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception as exc:
        payload = {
            "ok": False,
            "error": {
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
