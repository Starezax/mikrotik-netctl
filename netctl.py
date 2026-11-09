#!/usr/bin/env python3
"""netctl: a minimal controller for a MikroTik WireGuard overlay.

Reads the desired state from network.yaml and brings every relay and site router
to that state through the RouterOS API.

  python3 netctl.py status   show tunnels and active routes
  python3 netctl.py apply    configure everything (safe to run again and again)

Everything netctl creates or adopts is marked with comment=netctl. Only items with
that mark are ever removed, so manual settings without it are left alone.
"""
import sys

import yaml
from librouteros import connect

TAG = "netctl"
CHANGES = 0


# ---------- helpers ----------

def load(path="network.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def api_connect(name, hosts, cfg):
    """Connect to the first reachable address from the list."""
    last = None
    for host in hosts:
        try:
            api = connect(host=host, username=cfg["api"]["user"],
                          password=cfg["api"]["password"], port=8728, timeout=5)
            return api, host
        except Exception as e:  # unreachable, refused, wrong password...
            last = e
    raise RuntimeError(f"{name}: not reachable via {', '.join(hosts)} ({last})")


def norm(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def show(d):
    return " ".join(f"{k}={v}" for k, v in d.items())


def ensure(api, path, match, want, log):
    """Make sure an item matching `match` exists with fields `want`.

    Adds it if missing, fixes it if different, does nothing if already correct.
    """
    global CHANGES
    p = api.path(*path)
    found = [i for i in p
             if not i.get("dynamic")
             and all(norm(i.get(k)) == norm(v) for k, v in match.items())]
    full = {**match, **want, "comment": TAG}
    where = "/" + "/".join(path)
    if not found:
        p.add(**full)
        CHANGES += 1
        log(f"+ {where} {show(match)}")
        return
    item = found[0]
    diff = {k: v for k, v in full.items() if norm(item.get(k)) != norm(v)}
    if diff:
        p.update(**{".id": item[".id"], **diff})
        CHANGES += 1
        log(f"~ {where} {show(match)} -> {show(diff)}")


def prune(api, path, keep, key, log):
    """Remove netctl-marked items that are no longer in the desired state."""
    global CHANGES
    p = api.path(*path)
    for i in list(p):
        if i.get("comment") == TAG and key(i) not in keep:
            p.remove(i[".id"])
            CHANGES += 1
            log(f"- /{'/'.join(path)} {key(i)}")


def ensure_wg(api, name, port, log):
    """Make sure a WireGuard interface exists; return its public key."""
    ensure(api, ("interface", "wireguard"), {"name": name}, {"listen-port": port}, log)
    for i in api.path("interface", "wireguard"):
        if i["name"] == name:
            return i["public-key"]


def logger(name):
    return lambda msg: print(f"[{name}] {msg}")


def connect_all(cfg):
    relays, sites = cfg["relays"], cfg["sites"]
    conns = {}
    for r, rc in relays.items():
        conns[r], _ = api_connect(r, [rc["mgmt"]], cfg)
    for s, sc in sites.items():
        # a site is reachable through any relay: try them in order
        hosts = [f'{rc["net"]}.{sc["id"]}' for rc in relays.values()]
        conns[s], via = api_connect(s, hosts, cfg)
        print(f"[{s}] connected via {via}")
    return conns


# ---------- commands ----------

def apply(cfg):
    relays, sites = cfg["relays"], cfg["sites"]
    conns = connect_all(cfg)
    relay_key, site_key = {}, {}

    # Phase 1: WireGuard interfaces and tunnel addresses. Keys are collected
    # automatically, so nobody has to copy them by hand.
    for r, rc in relays.items():
        api, log = conns[r], logger(r)
        relay_key[r] = ensure_wg(api, "wg0", rc["port"], log)
        ensure(api, ("ip", "address"),
               {"address": f'{rc["net"]}.1/24', "interface": "wg0"}, {}, log)
    for s, sc in sites.items():
        api, log = conns[s], logger(s)
        for r, rc in relays.items():
            site_key[(s, r)] = ensure_wg(api, rc["site_if"], rc["site_port"], log)
            ensure(api, ("ip", "address"),
                   {"address": f'{rc["net"]}.{sc["id"]}/24', "interface": rc["site_if"]}, {}, log)

    # Phase 2: relays know every site and route to its LAN.
    for r, rc in relays.items():
        api, log = conns[r], logger(r)
        keep_peers, keep_routes = set(), set()
        for s, sc in sites.items():
            key = site_key[(s, r)]
            tun = f'{rc["net"]}.{sc["id"]}'
            ensure(api, ("interface", "wireguard", "peers"), {"public-key": key},
                   {"interface": "wg0", "allowed-address": f'{tun}/32,{sc["lan"]}'}, log)
            ensure(api, ("ip", "route"), {"dst-address": sc["lan"], "gateway": tun}, {}, log)
            keep_peers.add(key)
            keep_routes.add((sc["lan"], tun))
        prune(api, ("interface", "wireguard", "peers"), keep_peers,
              lambda i: i.get("public-key"), log)
        prune(api, ("ip", "route"), keep_routes,
              lambda i: (i.get("dst-address"), i.get("gateway")), log)

    # Phase 3: sites know every relay and have main + backup routes to the other LANs.
    for s, sc in sites.items():
        api, log = conns[s], logger(s)
        keep_routes = set()
        for distance, (r, rc) in enumerate(relays.items(), start=1):
            ensure(api, ("interface", "wireguard", "peers"), {"public-key": relay_key[r]},
                   {"interface": rc["site_if"], "endpoint-address": rc["public"],
                    "endpoint-port": rc["port"], "allowed-address": "10.0.0.0/8",
                    "persistent-keepalive": "25s"}, log)
            gw = f'{rc["net"]}.1'
            for o, oc in sites.items():
                if o == s:
                    continue
                ensure(api, ("ip", "route"), {"dst-address": oc["lan"], "gateway": gw},
                       {"distance": distance, "check-gateway": "ping"}, log)
                keep_routes.add((oc["lan"], gw))
        prune(api, ("interface", "wireguard", "peers"), set(relay_key.values()),
              lambda i: i.get("public-key"), log)
        prune(api, ("ip", "route"), keep_routes,
              lambda i: (i.get("dst-address"), i.get("gateway")), log)

    for api in conns.values():
        api.close()
    print(f"Done: {CHANGES} change(s)." if CHANGES else "Done: everything already matches network.yaml.")


def status(cfg):
    conns = connect_all(cfg)
    for r in cfg["relays"]:
        print(f"\n=== {r} ===")
        for p in conns[r].path("interface", "wireguard", "peers"):
            print(f'  {p.get("allowed-address", ""):32} from {p.get("current-endpoint-address") or "-":15}'
                  f'  handshake {p.get("last-handshake") or "never"}')
    lans = {sc["lan"] for sc in cfg["sites"].values()}
    for s in cfg["sites"]:
        print(f"\n=== {s} ===")
        for rt in conns[s].path("ip", "route"):
            if rt.get("dst-address") in lans and not rt.get("dynamic"):
                state = "ACTIVE  " if rt.get("active") else "standby "
                print(f'  {state} {rt["dst-address"]:15} via {rt.get("gateway")}  distance {rt.get("distance")}')
    for api in conns.values():
        api.close()


if __name__ == "__main__":
    commands = {"apply": apply, "status": status}
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in commands:
        print("usage: python3 netctl.py [status|apply] [network.yaml]")
        sys.exit(1)
    cfg = load(sys.argv[2] if len(sys.argv) > 2 else "network.yaml")
    try:
        commands[cmd](cfg)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(2)
