import argparse
import json
import sys

from . import core


def main():
    parser = argparse.ArgumentParser(description="Clear Dictation: local cleanup and transcript recovery")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("process", help="Clean text from stdin; writes only text to stdout")
    sub.add_parser("ui", help="Open the desktop control panel")
    sub.add_parser("status", help="Show settings and local model availability")
    sub.add_parser("warmup", help="Prime the local model without saving a transcript")
    mode = sub.add_parser("mode", help="Choose a writing mode")
    mode.add_argument("name", choices=core.MODES)
    history = sub.add_parser("history", help="List recent local transcripts")
    history.add_argument("--limit", type=int, default=10)
    dictionary = sub.add_parser("dictionary", help="Add a personal spelling")
    dictionary.add_argument("heard")
    dictionary.add_argument("preferred")
    args = parser.parse_args()
    if args.command == "process":
        text = sys.stdin.read()
        try:
            output = core.process(text)
        except Exception:
            output = text
        sys.stdout.write(output)
    elif args.command == "warmup":
        try:
            core.warmup()
        except Exception:
            # Keep the model service alive if optional cache priming fails.
            print("Cleanup cache warmup unavailable; the next request will retry.", file=sys.stderr)
            raise SystemExit(1)
    elif args.command == "status":
        print(json.dumps({"model_ready": core.health(), "settings": core.load_config()}, indent=2))
    elif args.command == "mode":
        config = core.load_config()
        config["mode"] = args.name
        core.save_config(config)
        print("Writing mode:", args.name)
    elif args.command == "dictionary":
        if not args.heard.strip() or not args.preferred.strip():
            parser.error("Both dictionary phrases must be nonempty")
        config = core.load_config()
        config["dictionary"][args.heard.strip()] = args.preferred.strip()
        core.save_config(config)
    elif args.command == "history":
        print(json.dumps(core.recent_history(args.limit), indent=2, ensure_ascii=False))
    else:
        from .ui import run
        run()


if __name__ == "__main__":
    main()
