"""端到端验收脚本：sqlite + Flask test_client，手动触发认领。

覆盖：
甲断面先钉 0.3 再报 1.2 → 待认领 → 办结后改册坐标，单据仍 0.3；
乙断面没钉就报被挡（422，原因含“还没钉桩”，退单留痕）；
两人抢同一桩号只留一单；
巡检员只读；退单原因留档。
"""
import os
import sys
import threading

DB_PATH = "/tmp/tunnelconv_accept.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["CLAIMER_ENABLED"] = "0"

sys.path.insert(0, os.path.dirname(__file__))

from api import app  # noqa: E402
from claimer import claim_once  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


client = app.test_client()


def login(username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


H_S = login("surveyor", "surv123456")
H_I = login("inspector", "insp123456")

print("== 1. 巡检员只读 ==")
check("inspector 看册 200", client.get("/api/stakes", headers=H_I).status_code == 200)
check("inspector 看单 200", client.get("/api/logs", headers=H_I).status_code == 200)
check("inspector 不能登记 403", client.post("/api/stakes", headers=H_I, json={
    "section_no": "x", "chainage": "x", "coordinate": 1}).status_code == 403)
check("inspector 不能钉桩 403", client.post("/api/stakes/1/drive", headers=H_I).status_code == 403)
check("inspector 不能改坐标 403", client.patch("/api/stakes/1", headers=H_I, json={
    "coordinate": 1}).status_code == 403)
check("inspector 不能报送 403", client.post("/api/logs", headers=H_I, json={
    "stake_id": 1, "delta_mm": 1}).status_code == 403)

print("== 2. 甲断面：登记待钉，未钉先报应整份退回 ==")
r = client.post("/api/stakes", headers=H_S, json={
    "section_no": "甲", "chainage": "K30+100", "coordinate": 0.3})
check("登记甲 201", r.status_code == 201, r.get_json())
jia = r.get_json()
check("甲进待钉清单 draft", jia["status"] == "draft")

r = client.post("/api/logs", headers=H_S, json={"stake_id": jia["id"], "delta_mm": 1.2})
body = r.get_json()
check("未钉报送被挡 422", r.status_code == 422, body)
check("退回原因写明还没钉桩", "还没钉桩" in (body.get("detail") or ""), body)
rejected_log = body["log"]
check("退回单无坐标快照", rejected_log["coord_snapshot"] is None)

rejected_rows = [l for l in client.get("/api/logs", headers=H_S).get_json()
                 if l["status"] == "rejected"]
check("退单原因留档可查", len(rejected_rows) == 1 and "还没钉桩" in rejected_rows[0]["reason"])

print("== 3. 甲断面：钉桩 + 报送同一次提交，坐标 0.3 抄进单据 ==")
r = client.post(f"/api/stakes/{jia['id']}/drive", headers=H_S, json={"delta_mm": 1.2})
check("钉桩并报送 201", r.status_code == 201, r.get_json())
out = r.get_json()
check("册已钉", out["stake"]["status"] == "staked" and out["stake"]["coordinate"] == 0.3)
log = out["log"]
check("单据进待认领", log["status"] == "pending")
check("单据坐标抄成 0.3", log["coord_snapshot"] == 0.3, log)
check("单据带断面号甲", log["section_no"] == "甲")

print("== 4. 两人抢同一个桩号，只留一单 ==")
r = client.post("/api/logs", headers=H_S, json={"stake_id": jia["id"], "delta_mm": 0.8})
check("甲已有在办单再报立刻失败 409", r.status_code == 409, r.get_json())

# 丙：两人同时对同一根待钉桩做“钉桩并报送”，只成一单，输家整笔回滚不半钉
r = client.post("/api/stakes", headers=H_S, json={
    "section_no": "丙", "chainage": "K30+300", "coordinate": 7.7})
bing = r.get_json()
barrier_stake = threading.Barrier(2)
stake_codes = []


def drive_race(delta):
    barrier_stake.wait()
    stake_codes.append(client.post(
        f"/api/stakes/{bing['id']}/drive", headers=H_S,
        json={"delta_mm": delta}).status_code)


t3 = threading.Thread(target=drive_race, args=(0.5,))
t4 = threading.Thread(target=drive_race, args=(0.6,))
t3.start(); t4.start(); t3.join(); t4.join()
check("并发钉桩返回 201+409", sorted(stake_codes) == [201, 409], stake_codes)
logs_all = client.get("/api/logs", headers=H_S).get_json()
check("丙只生成一张单", len([l for l in logs_all if l["stake_id"] == bing["id"]]) == 1)
stakes_all = {s["id"]: s for s in client.get("/api/stakes", headers=H_S).get_json()}
check("输家整笔回滚不半钉", stakes_all[bing["id"]]["status"] == "staked")

# 乙也原子钉桩报送（此时甲、乙、丙三张在办）
r = client.post("/api/stakes", headers=H_S, json={
    "section_no": "乙", "chainage": "K30+200", "coordinate": 12.0})
yi = r.get_json()
r = client.post(f"/api/stakes/{yi['id']}/drive", headers=H_S, json={"delta_mm": 2.0})
check("乙钉桩报送一次成型 201", r.status_code == 201, r.get_json())

print("== 5. 认领线程按序办结（丙/乙/甲），甲 1.2 mm 合格 ==")
drained = 0
while claim_once():
    drained += 1
check("三张在办全部认领", drained == 3, drained)
logs = {l["id"]: l for l in client.get("/api/logs", headers=H_S).get_json()}
done = logs[log["id"]]
check("甲办结", done["status"] == "done")
check("结论合格", done["verdict"] == "合格", done)
check("办结单据坐标仍是 0.3", done["coord_snapshot"] == 0.3)

# 乙已办结，两人再同时报送：仍只留一单（部分唯一索引只挡 pending 重复）
barrier_log = threading.Barrier(2)
log_codes = []


def report_race(delta):
    barrier_log.wait()
    log_codes.append(client.post(
        "/api/logs", headers=H_S,
        json={"stake_id": yi["id"], "delta_mm": delta}).status_code)


t1 = threading.Thread(target=report_race, args=(2.2,))
t2 = threading.Thread(target=report_race, args=(2.4,))
t1.start(); t2.start(); t1.join(); t2.join()
check("办结后并发报送乙返回 201+409", sorted(log_codes) == [201, 409], log_codes)
while claim_once():
    pass

print("== 6. 办结后专页改册坐标，不牵动已办结结论 ==")
r = client.patch(f"/api/stakes/{jia['id']}", headers=H_S, json={"coordinate": 9.9})
check("改册坐标 200", r.status_code == 200 and r.get_json()["coordinate"] == 9.9)
logs = {l["id"]: l for l in client.get("/api/logs", headers=H_S).get_json()}
again = logs[log["id"]]
check("再打开那份单坐标仍 0.3", again["coord_snapshot"] == 0.3, again)
check("结论仍合格不重判", again["verdict"] == "合格" and again["delta_mm"] == 1.2)
# 乙、丙单据快照同样不被改册牵动
check("乙单据快照仍是 12.0", all(l["coord_snapshot"] == 12.0
      for l in logs.values() if l["stake_id"] == yi["id"]))
check("丙单据快照仍是 7.7", all(l["coord_snapshot"] == 7.7
      for l in logs.values() if l["stake_id"] == bing["id"]))

print("== 7. 其他边界 ==")
# 钉桩必须同次报送，缺收敛值视为没做完，册保持待钉
r = client.post("/api/stakes", headers=H_S, json={
    "section_no": "戊", "chainage": "K60+000", "coordinate": 3.0})
wu = r.get_json()
r = client.post(f"/api/stakes/{wu['id']}/drive", headers=H_S, json={})
check("只钉不报缺一边 400", r.status_code == 400, r.get_json())
stakes_all = {s["id"]: s for s in client.get("/api/stakes", headers=H_S).get_json()}
check("缺一边册仍待钉、无单据", stakes_all[wu["id"]]["status"] == "draft"
      and not [l for l in client.get("/api/logs", headers=H_S).get_json()
               if l["stake_id"] == wu["id"]])
check("已钉不能重复钉", client.post(f"/api/stakes/{jia['id']}/drive", headers=H_S,
                                     json={"delta_mm": 1}).status_code == 409)
r = client.post("/api/stakes", headers=H_S, json={
    "section_no": "己", "chainage": "K30+100", "coordinate": 1})
check("同桩号只留一条册记 409", r.status_code == 409, r.get_json())
r = client.post("/api/logs", headers=H_S, json={"stake_id": 99999, "delta_mm": 1})
check("不存在的桩号 400", r.status_code == 400)
r = client.post("/api/logs", headers=H_S, json={"delta_mm": 1})
check("报送缺 stake_id 400", r.status_code == 400)
seed_done = [l for l in client.get("/api/logs", headers=H_S).get_json()
             if l["chainage"] in ("K12+180", "K18+040")]
check("种子两单已办结且带坐标快照", len(seed_done) == 2
      and all(l["status"] == "done" and l["coord_snapshot"] is not None for l in seed_done))

# 丁断面：登记进待钉但没钉就报，被挡住
r = client.post("/api/stakes", headers=H_S, json={
    "section_no": "丁", "chainage": "K40+000", "coordinate": 5.0})
ding = r.get_json()
r = client.post("/api/logs", headers=H_S, json={"stake_id": ding["id"], "delta_mm": 4.0})
check("丁没钉就报被挡", r.status_code == 422 and "还没钉桩" in r.get_json()["detail"])
stakes_all = {s["id"]: s for s in client.get("/api/stakes", headers=H_S).get_json()}
check("丁被挡后仍是待钉，可重新钉桩报送",
      stakes_all[ding["id"]]["status"] == "draft"
      and client.post(f"/api/stakes/{ding['id']}/drive", headers=H_S,
                      json={"delta_mm": 4.0}).status_code == 201)

print(f"\n结果：{PASS} 通过，{FAIL} 失败")
sys.exit(1 if FAIL else 0)
