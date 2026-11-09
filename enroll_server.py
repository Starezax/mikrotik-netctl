#!/usr/bin/env python3
"""enroll_server: the controller's registration service for new routers.

  python3 enroll_server.py

Listens on port 8080:
  GET  /relays   which WireGuard interfaces a new router must create (no secrets)
  POST /enroll   register a router: {"token", "name", "keys": {relay: public_key}}

On a valid one-time invite it adds the router on every relay, writes it into
network.yaml, and then keeps running `apply` until the new router is reachable,
so every other router gets a route to the new LAN.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

import netctl

CFG_PATH = "network.yaml"
LOCK = threading.Lock()


def load_cfg():
    return netctl.load(CFG_PATH)


def save_cfg(cfg):
    with open(CFG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


def relay_public_key(api):
    for i in api.path("interface", "wireguard"):
        if i["name"] == "wg0":
            return i["public-key"]
    raise RuntimeError("relay has no wg0 interface")


def enroll(req):
    with LOCK:
        cfg = load_cfg()
        token = req.get("token")
        if token not in (cfg.get("invites") or []):
            return 403, {"error": "invalid or already used invite"}
        keys = req.get("keys") or {}
        if set(keys) != set(cfg["relays"]):
            return 400, {"error": f"need one key per relay: {list(cfg['relays'])}"}
        name = req.get("name")
        if not name or name in cfg["sites"]:
            return 400, {"error": f"name missing or already used: {name}"}

        new_id = max(s["id"] for s in cfg["sites"].values()) + 1
        lan = f"10.10.{new_id - 10}.0/24"
        print(f"[enroll] {name}: id {new_id}, LAN {lan}")

        relays_out = {}
        for r, rc in cfg["relays"].items():
            api, _ = netctl.api_connect(r, [rc["mgmt"]], cfg)
            log = netctl.logger(r)
            tun = f'{rc["net"]}.{new_id}'
            netctl.ensure(api, ("interface", "wireguard", "peers"), {"public-key": keys[r]},
                          {"interface": "wg0", "allowed-address": f"{tun}/32,{lan}"}, log)
            netctl.ensure(api, ("ip", "route"), {"dst-address": lan, "gateway": tun}, {}, log)
            relays_out[r] = {"public": rc["public"], "port": rc["port"], "net": rc["net"],
                             "site_if": rc["site_if"], "site_port": rc["site_port"],
                             "key": relay_public_key(api), "tunnel": f"{tun}/24"}
            api.close()

        cfg["sites"][name] = {"id": new_id, "lan": lan}
        cfg["invites"].remove(token)
        save_cfg(cfg)

    threading.Thread(target=finish, args=(name,), daemon=True).start()
    return 200, {"name": name, "id": new_id, "lan": lan, "api": cfg["api"], "relays": relays_out}


def finish(name):
    """Wait until the new router's tunnel is up, then run apply for the whole network."""
    for attempt in range(1, 13):
        time.sleep(10)
        with LOCK:
            try:
                netctl.CHANGES = 0
                netctl.apply(load_cfg())
                print(f"[enroll] {name} is in the network")
                return
            except Exception as e:
                print(f"[enroll] waiting for {name} (attempt {attempt}/12): {e}")
    print(f"[enroll] gave up on {name}: run `python3 netctl.py apply` by hand")


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/relays":
            cfg = load_cfg()
            self.reply(200, {"relays": {r: {"site_if": rc["site_if"], "site_port": rc["site_port"]}
                                        for r, rc in cfg["relays"].items()}})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/enroll":
            return self.reply(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            code, data = enroll(req)
        except Exception as e:
            code, data = 500, {"error": str(e)}
        self.reply(code, data)


if __name__ == "__main__":
    print("enroll server listening on :8080")
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
