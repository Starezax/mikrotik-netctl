#!/usr/bin/env python3
"""netctl: a minimal controller for a MikroTik WireGuard overlay.

Reads the desired state from network.yaml and brings every relay and site router
to that state through the RouterOS API.

  python3 netctl.py status     show tunnels, delays and active routes
  python3 netctl.py apply      configure everything (safe to run again and again)
  python3 netctl.py optimize   measure delays, pick the fastest relay for every pair of sites, apply
  python3 netctl.py watch [s]  run optimize every s seconds (default 60)

Everything netctl creates or adopts is marked with comment=netctl. Only items with
that mark are ever removed, so manual settings without it are left alone.
"""
import json
import os
import re
import sys
import time

import yaml
from librouteros import connect

TAG = "netctl"
CHANGES = 0
METRICS = "metrics.json"   # last measured delays and chosen relays
HYSTERESIS = (1.2, 5.0)    # switch only if the new relay is >20% and >5 ms better


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


# ---------- delays and relay choice ----------

def load_metrics():
    try:
        with open(METRICS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_metrics(m):
    with open(METRICS, "w") as f:
        json.dump(m, f, indent=2)


def parse_time(s):
    """RouterOS durations: '82ms141us', '966us', '1m6s' -> milliseconds."""
    unit = {"w": 604800000.0, "d": 86400000.0, "h": 3600000.0, "m": 60000.0,
            "s": 1000.0, "ms": 1.0, "us": 0.001}
    return sum(float(n) * unit[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|us|w|d|h|m|s)", s or ""))


def ping(api, address, count=3):
    """Ping from the router itself; average RTT in ms, or None if nothing came back."""
    last = {}
    for r in api("/ping", address=address, count=count, interval="200ms"):
        last = r
    if not last or int(last.get("received", 0)) == 0:
        return None
    return round(parse_time(last.get("avg-rtt")), 1)


def cost(rtt, a, b, r):
    """Path a -> relay r -> b costs RTT(a, r) + RTT(b, r)."""
    x, y = rtt.get(a, {}).get(r), rtt.get(b, {}).get(r)
    return float("inf") if x is None or y is None else x + y


def pair(a, b):
    return "|".join(sorted([a, b]))


def relay_order(cfg, metrics, a, b):
    """Relays for traffic between sites a and b, best first."""
    relays = list(cfg["relays"])
    rtt = metrics.get("rtt", {})
    if a not in rtt or b not in rtt:
        return relays                      # not measured yet: order from network.yaml
    order = sorted(relays, key=lambda r: cost(rtt, a, b, r))
    chosen = metrics.get("choice", {}).get(pair(a, b))
    if chosen in relays and cost(rtt, a, b, chosen) != float("inf"):
        order.remove(chosen)
        order.insert(0, chosen)
    return order


# ---------- direct P2P tunnels ----------

P2P_PEERS = {}   # (site, other site) -> public key of the other site's wg-p2p


def public_addresses(cfg, conns, site_key):
    """Each site's public IP as the relays see it (the address of its ISP's NAT)."""
    seen = {}
    for r in cfg["relays"]:
        for p in conns[r].path("interface", "wireguard", "peers"):
            seen[p.get("public-key")] = (p.get("current-endpoint-address"), p.get("current-endpoint-port"))
    out = {}
    for s in cfg["sites"]:
        for r, rc in cfg["relays"].items():
            ip, port = seen.get(site_key[(s, r)], (None, None))
            if ip:
                out[s] = ip
                if str(port) != str(rc["site_port"]):
                    print(f"[{s}] note: its NAT changed port {rc['site_port']} -> {port}; P2P may not work")
                break
    return out


def handshake_age(peer):
    """Seconds since the last handshake, or None if there never was one."""
    hs = peer.get("last-handshake")
    return parse_time(hs) / 1000 if hs else None


def setup_p2p(cfg, conns, site_key, wait=30):
    """Create wg-p2p on every site with a peer for every other site, then see which pairs connect.

    Both sides of a pair send keepalives to each other's public address at the same time,
    so each NAT lets the other side in (UDP hole punching). Pairs that never handshake stay
    on the relays.
    """
    p2p, sites = cfg["p2p"], cfg["sites"]
    ifname, port, net = p2p["interface"], p2p["port"], p2p["net"]
    public = public_addresses(cfg, conns, site_key)
    key = {}
    for s, sc in sites.items():
        api, log = conns[s], logger(s)
        key[s] = ensure_wg(api, ifname, port, log)
        ensure(api, ("ip", "address"), {"address": f'{net}.{sc["id"]}/24', "interface": ifname}, {}, log)

    before = CHANGES
    P2P_PEERS.clear()
    for s in sites:
        api, log = conns[s], logger(s)
        for o, oc in sites.items():
            if o == s or o not in public:
                continue
            ensure(api, ("interface", "wireguard", "peers"), {"public-key": key[o]},
                   {"interface": ifname, "endpoint-address": public[o], "endpoint-port": port,
                    "allowed-address": f'{net}.{oc["id"]}/32,{oc["lan"]}',
                    "persistent-keepalive": "10s"}, log)
            P2P_PEERS[(s, o)] = key[o]

    names = list(sites)
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]

    def check():
        ok = {}
        ages = {}
        for s in names:
            for p in conns[s].path("interface", "wireguard", "peers"):
                if p.get("interface") == ifname:
                    ages[(s, p.get("public-key"))] = handshake_age(p)
        for a, b in pairs:
            ab, ba = ages.get((a, key.get(b))), ages.get((b, key.get(a)))
            ok[pair(a, b)] = any(x is not None and x < 180 for x in (ab, ba))
        return ok

    direct = check()
    if CHANGES > before and not all(direct.values()):
        print(f"\nWaiting up to {wait} s for the new direct tunnels to handshake...")
        for _ in range(wait // 5):
            time.sleep(5)
            direct = check()
            if all(direct.values()):
                break
    return direct


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

    # Phase 3 (optional): direct P2P tunnels between sites, through their NATs.
    p2p = cfg.get("p2p") or {}
    direct = setup_p2p(cfg, conns, site_key) if p2p.get("enabled") else {}

    # Phase 4: routes to the other LANs. Distance 1 = direct tunnel if it works,
    # then the relays, best first; check-gateway falls back to the next one on failure.
    metrics = load_metrics()
    for s, sc in sites.items():
        api, log = conns[s], logger(s)
        keep_routes, keep_peers = set(), set()
        for r, rc in relays.items():
            ensure(api, ("interface", "wireguard", "peers"), {"public-key": relay_key[r]},
                   {"interface": rc["site_if"], "endpoint-address": rc["public"],
                    "endpoint-port": rc["port"], "allowed-address": "10.0.0.0/8",
                    "persistent-keepalive": "25s"}, log)
            keep_peers.add((rc["site_if"], relay_key[r]))
        for o, oc in sites.items():
            if o == s:
                continue
            gateways = []
            if direct.get(pair(s, o)):
                gateways.append(f'{p2p["net"]}.{oc["id"]}')
            gateways += [f'{relays[r]["net"]}.1' for r in relay_order(cfg, metrics, s, o)]
            for distance, gw in enumerate(gateways, start=1):
                ensure(api, ("ip", "route"), {"dst-address": oc["lan"], "gateway": gw},
                       {"distance": distance, "check-gateway": "ping"}, log)
                keep_routes.add((oc["lan"], gw))
            if p2p.get("enabled") and (s, o) in P2P_PEERS:
                keep_peers.add((p2p["interface"], P2P_PEERS[(s, o)]))
        prune(api, ("interface", "wireguard", "peers"), keep_peers,
              lambda i: (i.get("interface"), i.get("public-key")), log)
        prune(api, ("ip", "route"), keep_routes,
              lambda i: (i.get("dst-address"), i.get("gateway")), log)

    if p2p.get("enabled"):
        print("\nP2P between sites:")
        names = list(sites)
        for a_i, a in enumerate(names):
            for b in names[a_i + 1:]:
                print(f"  {a} <-> {b}: " + ("direct" if direct.get(pair(a, b)) else "via relay (no handshake)"))

    for api in conns.values():
        api.close()
    print(f"Done: {CHANGES} change(s)." if CHANGES else "Done: everything already matches network.yaml.")


def optimize(cfg):
    """Measure RTT from every site to every relay, choose a relay per pair of sites, apply."""
    relays, sites = cfg["relays"], cfg["sites"]
    conns = connect_all(cfg)
    rtt = {}
    print("\nRTT from each site to each relay (ms):")
    print("  " + " " * 8 + "".join(f"{r:>10}" for r in relays))
    for s in sites:
        rtt[s] = {r: ping(conns[s], f'{rc["net"]}.1') for r, rc in relays.items()}
        print(f"  {s:8}" + "".join(f"{('-' if v is None else v):>10}" for v in rtt[s].values()))
    for api in conns.values():
        api.close()

    old = load_metrics().get("choice", {})
    choice = {}
    print("\nRelay for each pair of sites:")
    names = list(sites)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            costs = {r: cost(rtt, a, b, r) for r in relays}
            best = min(costs, key=costs.get)
            prev = old.get(pair(a, b))
            keep = (prev in costs and prev != best and costs[prev] != float("inf")
                    and costs[prev] <= costs[best] * HYSTERESIS[0] + HYSTERESIS[1])
            choice[pair(a, b)] = prev if keep else best
            detail = ", ".join(f"{r} {c:.1f}" if c != float("inf") else f"{r} down" for r, c in costs.items())
            note = "" if prev in (None, choice[pair(a, b)]) else f"  (was {prev})"
            if keep:
                note = f"  (kept {prev}: not enough better)"
            print(f"  {a} <-> {b}: {detail} -> {choice[pair(a, b)]}{note}")

    save_metrics({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rtt": rtt, "choice": choice})
    print()
    apply(cfg)


def watch(cfg, every=60):
    global CHANGES
    while True:
        print(f"\n===== {time.strftime('%H:%M:%S')} =====")
        try:
            CHANGES = 0
            optimize(load())
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(every)


def status(cfg):
    conns = connect_all(cfg)
    for r in cfg["relays"]:
        print(f"\n=== {r} ===")
        for p in conns[r].path("interface", "wireguard", "peers"):
            print(f'  {p.get("allowed-address", ""):32} from {p.get("current-endpoint-address") or "-":15}'
                  f'  handshake {p.get("last-handshake") or "never"}')
    if (cfg.get("p2p") or {}).get("enabled"):
        ifname = cfg["p2p"]["interface"]
        print("\n=== Direct P2P tunnels ===")
        for s in cfg["sites"]:
            for p in conns[s].path("interface", "wireguard", "peers"):
                if p.get("interface") == ifname:
                    print(f'  {s:8} -> {p.get("allowed-address", ""):30} '
                          f'handshake {p.get("last-handshake") or "never"}')
    m = load_metrics()
    if m.get("rtt"):
        print(f"\n=== Delays measured {m.get('time')} (ms) ===")
        for s, row in m["rtt"].items():
            print(f"  {s:8} " + "  ".join(f"{r} {('-' if v is None else v)}" for r, v in row.items()))
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
    commands = {"apply": apply, "status": status, "optimize": optimize, "watch": watch}
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in commands:
        print("usage: python3 netctl.py [status|apply|optimize|watch [seconds]]")
        sys.exit(1)
    cfg = load()
    try:
        if cmd == "watch":
            watch(cfg, int(sys.argv[2]) if len(sys.argv) > 2 else 60)
        else:
            commands[cmd](cfg)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(2)
