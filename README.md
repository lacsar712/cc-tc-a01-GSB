# 隧道收敛测缝台

测量员先把桩号钉进**断面坐标册**，再报送收敛毫米值。接口进程内后台线程认领待判行（不另起 worker 容器），按绝对值是否不超过 3.0 mm 给出合格或超限。页面是 Svelte。

## 钉桩规矩

1. 页眉分两页：**收敛总表** 与 **钉桩专页**。坐标只进坐标册，不挂总表末列。
2. 钉桩专页三块：**① 待钉清单 / ② 已钉档案 / ③ 退单原因**。
3. 报送前必须已钉桩；没钉就报送，整份退回（HTTP 422），退单留档，原因写明"还没钉桩"。
4. 钉桩瞬间坐标**抄进单据**（`coord_snapshot`）；此后在专页改坐标只动册子，不牵动任何已办结/在办单据的坐标与结论。
5. 钉桩登记与报送**必须同一次提交、同一事务**：钉桩时必须同时填本次收敛值，缺一边视为没做完（400，册保持待钉、不生成单据）。钉桩成功后坐标立刻抄进该单据。
6. 同一桩号：册记唯一、同时只允许一张在办单。两人抢同一根待钉桩或同一在办坑位时只留一单，另一单立即 409 失败、整笔回滚不半钉（部分唯一索引 `uq_open_log_per_stake` + 行锁）。
7. 巡检员（inspector）能看册和单据坐标，但不能钉桩、不能改坐标、不能报送（403）。

## 技术栈

- 后端：Flask、Gunicorn（单进程多线程）、SQLAlchemy、进程内认领线程
- 前端：Svelte、Vite、nginx 反代 `/api`
- 数据库：PostgreSQL 16

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3201 |
| 接口 | http://localhost:8201 |
| PostgreSQL | localhost:54401（库名 `tunnelconv`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| surveyor | surv123456 | 可钉桩、可报送 |
| inspector | insp123456 | 只读（看册、看单据坐标） |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8201/api/health`

## 接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/stakes` | 登录 | 断面坐标册 |
| POST | `/api/stakes` | 测量员 | 登记断面号/桩号/坐标，进待钉清单 |
| POST | `/api/stakes/{id}/drive` | 测量员 | 钉桩，**必须同次带 `delta_mm` 报送**，同一事务原子完成 |
| PATCH | `/api/stakes/{id}` | 测量员 | 专页改册坐标（不回写单据快照） |
| GET | `/api/logs` | 登录 | 收敛单据（含 `coord_snapshot` 坐标快照） |
| POST | `/api/logs` | 测量员 | 挂已钉 `stake_id` 报送；未钉 422 整份退回 |

## 验收脚本（无 Docker 时用 sqlite 等价验证）

```bash
# 34 项端到端断言（权限、退回、快照、并发抢单、办结不被改册牵动）
DATABASE_URL=sqlite:////tmp/t.db python3 backend/acceptance_test.py
# 真实 HTTP + 认领线程冒烟：先起服务再跑
DATABASE_URL=sqlite:////tmp/t2.db python3 -c "from api import app; app.run(port=8201)"
python3 backend/smoke_http.py
```

## 种子

| 桩号 | 断面号 | 坐标 | 收敛 | 结论 |
|------|--------|------|------|------|
| K12+180 | S-01 | 3120.5 | 1.2 mm | 合格 |
| K18+040 | S-02 | 3180.0 | 5.6 mm | 超限 |

A01 老库启动时自动幂等迁移：补列、补部分唯一索引，并为无册记的旧单据回填已钉册记。
