"""Command line: ``python -m live_clipper serve|watch|add <login>``."""
import argparse
import asyncio

import uvicorn

from . import pipeline, server, store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="live_clipper", description="Clip live streams for review.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="review GUI on http://127.0.0.1:8765")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    sub.add_parser("watch", help="record channels on the watch list when they go live")
    add = sub.add_parser("add", help="add a Twitch channel to the watch list")
    add.add_argument("login")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        uvicorn.run(server.app, host=args.host, port=args.port)
    elif args.cmd == "watch":
        asyncio.run(pipeline.run_watch())
    elif args.cmd == "add":
        print(", ".join(store.add_channel(args.login)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
