#!/usr/bin/env python3
"""provision: join a fresh MikroTik router to the network.

Run it on a PC in the new router's LAN:

  python3 provision.py --password ROUTER_ADMIN_PASSWORD --token INVITE --name hEX-D

What it does:
  1. connects to the router (192.168.88.1 by default) as admin;
  2. creates the WireGuard interfaces; the keys are generated on the router itself;
  3. registers the router with the controller (invite + public keys);
  4. applies what the controller returns: name, controller account, tunnel
     addresses, relay peers, LAN address, API access limited to the relays.
The controller then finishes the routes on every router by itself.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

from librouteros import connect

TAG = "netctl"


def http(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"controller refused: {e.code} {e.read().decode()}")
    except OSError as e:
        sys.exit(f"cannot reach controller at {url}: {e}")


def norm(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def ensure(api, path, match, want):
    p = api.path(*path)
    found = [i for i in p if not i.get("dynamic")
             and all(norm(i.get(k)) == norm(v) for k, v in match.items())]
    full = {**match, **want, "comment": TAG}
    if not found:
        p.add(**full)
        print(f"    + /{'/'.join(path)} {match}")
        return
    diff = {k: v for k, v in full.items() if norm(found[0].get(k)) != norm(v)}
    if diff:
        p.update(**{".id": found[0][".id"], **diff})
        print(f"    ~ /{'/'.join(path)} {match}")


def ensure_wg(api, name, port):
    ensure(api, ("interface", "wireguard"), {"name": name}, {"listen-port": port})
    for i in api.path("interface", "wireguard"):
        if i["name"] == name:
            return i["public-key"]


def main():
    ap = argparse.ArgumentParser(description="Join a fresh MikroTik router to the network")
    ap.add_argument("--router", default="192.168.88.1", help="router address in this LAN")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", required=True, help="router admin password")
    ap.add_argument("--controller", default="http://203.0.113.200:8080")
    ap.add_argument("--token", required=True, help="one-time invite code")
    ap.add_argument("--name", required=True, help="name for the router, e.g. hEX-D")
    ap.add_argument("--lan-if", default="ether2", help="LAN interface (bridge on a real hEX)")
    a = ap.parse_args()

    print(f"1/5 connecting to the router at {a.router}")
    try:
        api = connect(host=a.router, username=a.user, password=a.password, port=8728, timeout=10)
    except Exception as e:
        sys.exit(f"cannot log into the router: {e}")

    print("2/5 asking the controller which tunnels to create")
    info = http("GET", a.controller + "/relays")

    print("3/5 creating WireGuard interfaces (keys are generated on the router)")
    keys = {r: ensure_wg(api, rc["site_if"], rc["site_port"]) for r, rc in info["relays"].items()}

    print("4/5 registering with the controller")
    res = http("POST", a.controller + "/enroll", {"token": a.token, "name": a.name, "keys": keys})
    print(f"    accepted: {res['name']}, LAN {res['lan']}")

    print("5/5 applying the configuration")
    tuple(api("/system/identity/set", name=res["name"]))
    users = api.path("user")
    acc = res["api"]
    existing = [u for u in users if u.get("name") == acc["user"]]
    if existing:
        users.update(**{".id": existing[0][".id"], "group": "full", "password": acc["password"]})
    else:
        users.add(name=acc["user"], group="full", password=acc["password"])
    print(f"    controller account '{acc['user']}' ready")

    for r, rc in res["relays"].items():
        ensure(api, ("ip", "address"), {"address": rc["tunnel"], "interface": rc["site_if"]}, {})
        ensure(api, ("interface", "wireguard", "peers"), {"public-key": rc["key"]},
               {"interface": rc["site_if"], "endpoint-address": rc["public"],
                "endpoint-port": rc["port"], "allowed-address": "10.0.0.0/8",
                "persistent-keepalive": "25s"})

    lan_prefix = res["lan"].split("/")[0].rsplit(".", 1)[0]
    ensure(api, ("ip", "address"), {"address": f"{lan_prefix}.1/24", "interface": a.lan_if}, {})

    # API only from the relays' tunnel addresses (the controller's path) and from this LAN
    local_lan = a.router.rsplit(".", 1)[0] + ".0/24"
    allowed = ",".join([f'{rc["net"]}.1/32' for rc in res["relays"].values()] + [local_lan])
    svc = api.path("ip", "service")
    api_svc = [s for s in svc if s.get("name") == "api"][0]
    svc.update(**{".id": api_svc[".id"], "address": allowed, "disabled": False})
    print(f"    API allowed only from {allowed}")
    api.close()

    print(f"""
Done. {res['name']} is registered:
  LAN          {res['lan']} (router {lan_prefix}.1)
  tunnels      {', '.join(rc['tunnel'] for rc in res['relays'].values())}
The controller is now adding routes to {res['lan']} on every router (about 10-30 s).
""")


if __name__ == "__main__":
    main()
