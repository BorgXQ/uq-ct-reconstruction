"""Run one localhost process; snapshots live for this process's lifetime."""
import argparse
from ct_editor import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true", help="Enable development logging (no reloader or interactive debugger)")
    parser.add_argument("--snapshot-limit", type=int, default=50)
    parser.add_argument("--snapshot-mib", type=int, default=64)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.snapshot_limit < 1 or args.snapshot_mib < 1:
        parser.error("Port must be 1–65535; storage limits must be positive.")
    app = create_app({"SNAPSHOT_LIMIT": args.snapshot_limit, "SNAPSHOT_BYTE_LIMIT": args.snapshot_mib * 1024**2})
    app.run(host="127.0.0.1", port=args.port, debug=args.debug, use_debugger=False, use_reloader=False)
