"""`buddy` command-line entrypoint: run the daemon, or send it one-shot
commands (status, rename, push a character, etc.)."""
import argparse
import json
import sys

from . import daemon as daemon_mod
from . import ipc
from .ble_link import find_device_sync


def _print(obj):
    print(json.dumps(obj, indent=2))


def cmd_daemon(args):
    daemon_mod.run(address=args.address)


def cmd_scan(args):
    try:
        address = find_device_sync(timeout=args.timeout)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(address)


def cmd_status(args):
    resp = ipc.request("device_cmd", {"cmd": "status"})
    if resp is None:
        print("daemon not reachable (is `buddy daemon` running?)", file=sys.stderr)
        sys.exit(1)
    _print(resp)


def cmd_name(args):
    resp = ipc.request("device_cmd", {"cmd": "name", "args": {"name": args.name}})
    _print(resp)


def cmd_owner(args):
    resp = ipc.request("device_cmd", {"cmd": "owner", "args": {"name": args.name}})
    _print(resp)


def cmd_species(args):
    resp = ipc.request("device_cmd", {"cmd": "species", "args": {"idx": args.idx}})
    _print(resp)


def cmd_unpair(args):
    resp = ipc.request("device_cmd", {"cmd": "unpair"})
    _print(resp)


def cmd_push_character(args):
    resp = ipc.request("push_character", {"dir": args.dir}, timeout=120)
    if resp is None:
        print("daemon not reachable", file=sys.stderr)
        sys.exit(1)
    _print(resp)
    if not resp.get("ok"):
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="buddy")
    sub = parser.add_subparsers(dest="command", required=True)

    p_daemon = sub.add_parser("daemon", help="run the bridge daemon (foreground)")
    p_daemon.add_argument("--address", help="BLE address, e.g. AA:BB:CC:DD:EE:FF")
    p_daemon.set_defaults(func=cmd_daemon)

    p_scan = sub.add_parser("scan", help="scan for a paired 'Claude-XXXX' BLE device")
    p_scan.add_argument("--timeout", type=float, default=10.0)
    p_scan.set_defaults(func=cmd_scan)

    p_status = sub.add_parser("status", help="query device status")
    p_status.set_defaults(func=cmd_status)

    p_name = sub.add_parser("name", help="set the pet's name")
    p_name.add_argument("name")
    p_name.set_defaults(func=cmd_name)

    p_owner = sub.add_parser("owner", help="set the owner's name")
    p_owner.add_argument("name")
    p_owner.set_defaults(func=cmd_owner)

    p_species = sub.add_parser("species", help="set built-in species index")
    p_species.add_argument("idx", type=int)
    p_species.set_defaults(func=cmd_species)

    p_unpair = sub.add_parser("unpair", help="clear BLE bonds on the device")
    p_unpair.set_defaults(func=cmd_unpair)

    p_push = sub.add_parser("push-character", help="upload a character folder")
    p_push.add_argument("dir")
    p_push.set_defaults(func=cmd_push_character)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
