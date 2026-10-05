"""真实 HTTP 冒烟：对运行中的 Flask(threaded)+sqlite+认领线程 走一遍。"""
import json
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8201"


def call(method, path, token=None, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as r:
            return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


def login(u, p):
    _, b = call("POST", "/api/auth/login", body={"username": u, "password": p})
    return b["access_token"]


s, i = login("surveyor", "surv123456"), login("inspector", "insp123456")
results = []

# 甲：先钉 0.3 再报 1.2
_, stake = call("POST", "/api/stakes", s,
                {"section_no": "甲", "chainage": "K50+000", "coordinate": 0.3})
code, b = call("POST", "/api/logs", s, {"stake_id": stake["id"], "delta_mm": 1.2})
results.append(("甲未钉先报挡 422", code == 422 and "还没钉桩" in b["detail"]))

code, b = call("POST", f"/api/stakes/{stake['id']}/drive", s, {"delta_mm": 1.2})
results.append(("钉桩并报送 201", code == 201 and b["log"]["coord_snapshot"] == 0.3))
log_id = b["log"]["id"]

# 等认领线程办结
final = None
for _ in range(50):
    _, logs = call("GET", "/api/logs", s)
    final = next(l for l in logs if l["id"] == log_id)
    if final["status"] == "done":
        break
    time.sleep(0.2)
results.append(("后台线程办结且合格", final["status"] == "done" and final["verdict"] == "合格"))
results.append(("办结时坐标 0.3", final["coord_snapshot"] == 0.3))

# 改册不牵动单据
call("PATCH", f"/api/stakes/{stake['id']}", s, {"coordinate": 9.9})
_, logs = call("GET", "/api/logs", s)
again = next(l for l in logs if l["id"] == log_id)
results.append(("改册后单据仍 0.3", again["coord_snapshot"] == 0.3 and again["verdict"] == "合格"))

# 巡检员
results.append(("巡检员不能钉桩", call("POST", f"/api/stakes/{stake['id']}/drive", i)[0] == 403))
results.append(("巡检员不能改钉", call("PATCH", f"/api/stakes/{stake['id']}", i, {"coordinate": 1})[0] == 403))
results.append(("巡检员不能报送", call("POST", "/api/logs", i, {"stake_id": stake["id"], "delta_mm": 1})[0] == 403))
_, logs = call("GET", "/api/logs", i)
results.append(("巡检员能看单据坐标", any(l["coord_snapshot"] == 0.3 for l in logs)))

# 并发抢乙：乙原子钉桩报送并办结，两人再同时报送
_, yi = call("POST", "/api/stakes", s, {"section_no": "乙", "chainage": "K50+200", "coordinate": 12.0})
code, b = call("POST", f"/api/stakes/{yi['id']}/drive", s, {"delta_mm": 2.0})
assert code == 201, (code, b)
for _ in range(50):
    _, logs = call("GET", "/api/logs", s)
    if all(l["status"] == "done" for l in logs if l["stake_id"] == yi["id"]):
        break
    time.sleep(0.2)
barrier = threading.Barrier(2)
codes = []


def race():
    barrier.wait()
    codes.append(call("POST", "/api/logs", s, {"stake_id": yi["id"], "delta_mm": 2.2})[0])


threading.Thread(target=race).start()
threading.Thread(target=race).start()
time.sleep(1)
results.append(("抢单一胜一败", sorted(codes) == [201, 409]))

ok = True
for name, passed in results:
    print(("PASS  " if passed else "FAIL  ") + name)
    ok = ok and passed
print("\nHTTP 冒烟：", "全部通过" if ok else "存在失败")
raise SystemExit(0 if ok else 1)
