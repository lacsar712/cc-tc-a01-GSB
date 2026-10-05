<script>
  let session = null;
  let page = "logs"; // logs | stakes
  let logs = [];
  let stakes = [];
  let loginUser = "surveyor";
  let loginPass = "surv123456";

  // 待钉登记
  let nSection = "";
  let nChainage = "";
  let nCoordinate = "";
  // 总表报送（挂已钉桩号）
  let rStakeId = "";
  let rDelta = "";
  // 行内动作
  let driveDelta = {};
  let editCoord = {};
  let detail = null;

  let error = "";
  let notice = "";
  let loading = false;
  let timer;

  $: isWriter = session?.role === "writer";
  $: drafts = stakes.filter((s) => s.status !== "staked");
  $: staked = stakes.filter((s) => s.status === "staked");
  $: rejected = logs.filter((l) => l.status === "rejected");
  $: openStakeIds = new Set(logs.filter((l) => l.status === "pending").map((l) => l.stake_id));

  function headers(json) {
    const h = {};
    if (session) h.Authorization = "Bearer " + session.token;
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* 无响应体 */
    }
    return { ok: res.ok, status: res.status, data };
  }

  async function refresh() {
    if (!session) return;
    const [lr, sr] = await Promise.all([
      api("/api/logs", { headers: headers() }),
      api("/api/stakes", { headers: headers() }),
    ]);
    if (lr.status === 401 || sr.status === 401) {
      logout();
      return;
    }
    if (lr.ok) logs = lr.data;
    if (sr.ok) stakes = sr.data;
    if (detail) {
      const d = logs.find((l) => l.id === detail.id);
      if (d) detail = d;
    }
  }

  function flash(msg, isErr) {
    if (isErr) {
      error = msg;
      notice = "";
    } else {
      notice = msg;
      error = "";
    }
    setTimeout(() => {
      if (!isErr) notice = "";
    }, 3500);
  }

  async function login() {
    error = "";
    loading = true;
    try {
      const r = await api("/api/auth/login", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({ username: loginUser, password: loginPass }),
      });
      if (!r.ok) {
        error = r.data?.detail || "登录失败";
        return;
      }
      session = {
        token: r.data.access_token,
        username: r.data.username,
        role: r.data.role,
      };
      localStorage.setItem("tunnel_session", JSON.stringify(session));
      await refresh();
      timer = setInterval(refresh, 2000);
    } catch {
      error = "无法连接接口";
    } finally {
      loading = false;
    }
  }

  function logout() {
    if (timer) clearInterval(timer);
    session = null;
    logs = [];
    stakes = [];
    detail = null;
    localStorage.removeItem("tunnel_session");
  }

  // 登记进待钉清单（还没钉）
  async function registerDraft() {
    error = "";
    if (!nSection || !nChainage || nCoordinate === "") {
      flash("断面号、桩号、坐标缺一边，登记不算完成", true);
      return;
    }
    loading = true;
    try {
      const r = await api("/api/stakes", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({
          section_no: nSection,
          chainage: nChainage,
          coordinate: Number(nCoordinate),
        }),
      });
      if (!r.ok) {
        flash(r.data?.detail || "登记失败", true);
        return;
      }
      nSection = "";
      nChainage = "";
      nCoordinate = "";
      await refresh();
    } catch {
      flash("登记时网络异常", true);
    } finally {
      loading = false;
    }
  }

  // 钉桩登记和报送必须同一次提交：收敛值缺一边视为没做完
  async function drive(s) {
    error = "";
    const v = driveDelta[s.id];
    if (v === undefined || v === "") {
      flash("钉桩登记和报送必须同一次提交：请填本次收敛值，缺一边视为没做完", true);
      return;
    }
    loading = true;
    try {
      const r = await api(`/api/stakes/${s.id}/drive`, {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({ delta_mm: Number(v) }),
      });
      if (!r.ok) {
        flash(r.data?.detail || "钉桩失败", true);
        return;
      }
      delete driveDelta[s.id];
      await refresh();
      flash(`桩号 ${s.chainage} 钉桩并报送成功：坐标已抄进单据，进入待认领`);
    } catch {
      flash("钉桩时网络异常", true);
    } finally {
      loading = false;
    }
  }

  // 已钉档案里改坐标：只动册子，不动已办结单据
  async function saveCoord(s) {
    const v = editCoord[s.id];
    if (v === undefined || v === "") return;
    loading = true;
    try {
      const r = await api(`/api/stakes/${s.id}`, {
        method: "PATCH",
        headers: headers(true),
        body: JSON.stringify({ coordinate: Number(v) }),
      });
      if (!r.ok) {
        flash(r.data?.detail || "改坐标失败", true);
        return;
      }
      delete editCoord[s.id];
      await refresh();
      flash("坐标册已更新；已办结单据仍保留钉桩时抄录的坐标");
    } catch {
      flash("改坐标时网络异常", true);
    } finally {
      loading = false;
    }
  }

  // 总表报送：必须挂已钉桩号，没钉的整份退回
  async function submitReport() {
    error = "";
    if (!rStakeId) {
      flash("请选择已钉桩号再报送", true);
      return;
    }
    if (rDelta === "") {
      flash("收敛值不能为空", true);
      return;
    }
    loading = true;
    try {
      const r = await api("/api/logs", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({ stake_id: Number(rStakeId), delta_mm: Number(rDelta) }),
      });
      if (!r.ok) {
        await refresh();
        flash(r.data?.detail || "报送失败", true);
        return;
      }
      rStakeId = "";
      rDelta = "";
      await refresh();
      flash("报送成功，进入待认领");
    } catch {
      flash("报送时网络异常", true);
    } finally {
      loading = false;
    }
  }

  function statusTag(s) {
    if (s === "pending") return ["待认领", "pending"];
    if (s === "done") return ["已办结", "ok"];
    if (s === "rejected") return ["已退回", "bad"];
    return [s, "pending"];
  }
  const statusLabel = (s) => statusTag(s)[0];
  const statusClass = (s) => statusTag(s)[1];

  const raw = localStorage.getItem("tunnel_session");
  if (raw) {
    try {
      session = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch {
      localStorage.removeItem("tunnel_session");
    }
  }
</script>

<style>
  :global(body) {
    margin: 0;
    font-family: "Segoe UI", system-ui, sans-serif;
    background: #1c1917;
    color: #f5f5f4;
  }
  main { max-width: 1020px; margin: 0 auto; padding: 1.5rem; }
  header {
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid #44403c; padding-bottom: 0.75rem; margin-bottom: 1rem;
  }
  h1 { color: #fbbf24; margin: 0; font-size: 1.25rem; }
  nav { display: flex; gap: 0.5rem; }
  nav button { background: transparent; color: #d6d3d1; border: 1px solid #57534e; }
  nav button.active { background: #d97706; border-color: #d97706; color: #fff; }
  .sub { color: #a8a29e; margin-bottom: 1.25rem; }
  section {
    background: #292524; border: 1px solid #44403c; border-radius: 8px;
    padding: 1rem 1.25rem; margin-bottom: 1rem;
  }
  h2 { font-size: 1rem; margin: 0 0 0.75rem; color: #fbbf24; }
  label { display: block; font-size: 0.85rem; color: #d6d3d1; margin-bottom: 0.25rem; }
  input, select {
    width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px;
    border: 1px solid #57534e; background: #0c0a09; color: #fafaf9; margin-bottom: 0.75rem;
  }
  .row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.6rem; }
  .row2 { display: grid; grid-template-columns: 2fr 1fr; gap: 0.6rem; }
  button {
    cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px;
    background: #d97706; color: #fff; font-weight: 600;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.secondary { background: #57534e; }
  button.small { padding: 0.3rem 0.7rem; font-size: 0.82rem; }
  .err { color: #fb7185; }
  .note { color: #86efac; font-size: 0.88rem; }
  .hint { color: #a8a29e; font-size: 0.82rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #44403c; vertical-align: middle; }
  tr.clickable { cursor: pointer; }
  tr.clickable:hover { background: #322e2b; }
  .tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
  .ok { background: #14532d; color: #86efac; }
  .bad { background: #7f1d1d; color: #fca5a5; }
  .pending { background: #713f12; color: #fde68a; }
  .inline { display: flex; gap: 0.4rem; align-items: center; }
  .inline input { margin: 0; width: 7rem; }
  .empty { color: #78716c; font-size: 0.88rem; padding: 0.4rem 0; }
  .modal-mask {
    position: fixed; inset: 0; background: rgba(0,0,0,0.55);
    display: flex; align-items: center; justify-content: center; z-index: 10;
  }
  .modal {
    background: #292524; border: 1px solid #57534e; border-radius: 8px;
    padding: 1.25rem 1.5rem; width: 420px; max-width: 92vw;
  }
  .modal dl { display: grid; grid-template-columns: 7rem 1fr; gap: 0.35rem 0.75rem; margin: 0.5rem 0; }
  .modal dt { color: #a8a29e; font-size: 0.85rem; }
  .modal dd { margin: 0; }
</style>

<main>
  {#if !session}
    <h1>隧道收敛测缝台</h1>
    <p class="sub">测缝进队前先钉桩。登录框已预填测量员账号 surveyor / surv123456；巡检员 inspector / insp123456 只读。</p>
    <section>
      <label>用户名</label>
      <input bind:value={loginUser} autocomplete="off" />
      <label>密码</label>
      <input type="password" bind:value={loginPass} autocomplete="off" />
      <button disabled={loading} on:click={login}>登录</button>
      {#if error}<p class="err">{error}</p>{/if}
    </section>
  {:else}
    <header>
      <h1>隧道收敛测缝台</h1>
      <nav>
        <button class={page === "logs" ? "active" : ""} on:click={() => (page = "logs")}>收敛总表</button>
        <button class={page === "stakes" ? "active" : ""} on:click={() => (page = "stakes")}>钉桩专页</button>
        <button class="secondary small" on:click={logout}>退出 {session.username}</button>
      </nav>
    </header>
    <p class="sub">
      已登录：{session.username}（{isWriter ? "测量员·可钉桩可报送" : "巡检员·只看册和单据，不能改钉不能报送"}）
    </p>
    {#if error}<p class="err">{error}</p>{/if}
    {#if notice}<p class="note">{notice}</p>{/if}

    {#if page === "logs"}
      {#if isWriter}
        <section>
          <h2>报送收敛（必须挂已钉桩号）</h2>
          <div class="row2">
            <div>
              <label>桩号（断面坐标册）</label>
              <select bind:value={rStakeId}>
                <option value="">请选择桩号…</option>
                <optgroup label="已钉档案">
                  {#each staked as s}
                    <option value={s.id} disabled={openStakeIds.has(s.id)}>
                      {s.section_no} · {s.chainage}{openStakeIds.has(s.id) ? "（在办中）" : ""}
                    </option>
                  {/each}
                </optgroup>
                <optgroup label="待钉清单（没钉就报会整份退回）">
                  {#each drafts as s}
                    <option value={s.id}>{s.section_no} · {s.chainage}（待钉）</option>
                  {/each}
                </optgroup>
              </select>
            </div>
            <div>
              <label>收敛（mm，可正可负）</label>
              <input type="number" step="0.1" bind:value={rDelta} />
            </div>
          </div>
          <button disabled={loading} on:click={submitReport}>报送（进入待认领）</button>
          <p class="hint">没钉过的桩号不能报送：整份退回，退单原因见钉桩专页。</p>
        </section>
      {/if}
      <section>
        <h2>收敛单据总表</h2>
        <table>
          <thead>
            <tr><th>编号</th><th>断面号</th><th>桩号</th><th>收敛mm</th><th>状态</th><th>结论</th><th>说明</th><th></th></tr>
          </thead>
          <tbody>
            {#each logs as row}
              <tr class="clickable" on:click={() => (detail = row)}>
                <td>{row.id}</td>
                <td>{row.section_no ?? "—"}</td>
                <td>{row.chainage}</td>
                <td>{row.delta_mm}</td>
                <td><span class="tag {statusClass(row.status)}">{statusLabel(row.status)}</span></td>
                <td>
                  {#if row.verdict}
                    <span class="tag {row.verdict === '合格' ? 'ok' : 'bad'}">{row.verdict}</span>
                  {:else}—{/if}
                </td>
                <td>{row.reason ?? "—"}</td>
                <td class="hint">点开看单据坐标</td>
              </tr>
            {/each}
          </tbody>
        </table>
        <p class="hint">坐标不挂总表末列，只随单据快照保存，点单据行查看。</p>
      </section>
    {/if}

    {#if page === "stakes"}
      <section>
        <h2>① 待钉清单</h2>
        {#if isWriter}
          <div class="row3">
            <div>
              <label>断面号</label>
              <input placeholder="例如 甲 / S-03" bind:value={nSection} />
            </div>
            <div>
              <label>桩号</label>
              <input placeholder="例如 K20+050" bind:value={nChainage} />
            </div>
            <div>
              <label>断面坐标</label>
              <input type="number" step="0.001" placeholder="例如 0.3" bind:value={nCoordinate} />
            </div>
          </div>
          <button class="secondary" disabled={loading} on:click={registerDraft}>登记进待钉清单</button>
          <p class="hint">登记只进待钉清单；钉桩时必须同时填本次收敛值，登记和报送同一次提交，缺一边视为没做完。</p>
        {/if}
        {#if drafts.length === 0}
          <p class="empty">暂无待钉桩号。</p>
        {:else}
          <table>
            <thead>
              <tr><th>断面号</th><th>桩号</th><th>坐标</th><th>收敛mm</th><th>钉桩动作</th></tr>
            </thead>
            <tbody>
              {#each drafts as s}
                <tr>
                  <td>{s.section_no}</td>
                  <td>{s.chainage}</td>
                  <td>{s.coordinate}</td>
                  <td>
                    {#if isWriter}
                      <input
                        type="number" step="0.1" placeholder="本次报送收敛"
                        value={driveDelta[s.id] ?? ""}
                        on:input={(e) => (driveDelta = { ...driveDelta, [s.id]: e.target.value })}
                      />
                    {:else}—{/if}
                  </td>
                  <td>
                    {#if isWriter}
                      <button class="small" disabled={loading} on:click={() => drive(s)}>钉桩并报送</button>
                    {:else}<span class="hint">只读</span>{/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>

      <section>
        <h2>② 已钉档案（断面坐标册）</h2>
        {#if staked.length === 0}
          <p class="empty">暂无已钉桩号。</p>
        {:else}
          <table>
            <thead>
              <tr><th>断面号</th><th>桩号</th><th>在册坐标</th><th>钉桩人</th><th>{isWriter ? "改坐标（不触动已办结结论）" : "钉桩时间"}</th></tr>
            </thead>
            <tbody>
              {#each staked as s}
                <tr>
                  <td>{s.section_no}</td>
                  <td>{s.chainage}</td>
                  <td>{s.coordinate}</td>
                  <td>{s.staked_by}</td>
                  <td>
                    {#if isWriter}
                      <div class="inline">
                        <input
                          type="number" step="0.001" placeholder="新坐标"
                          value={editCoord[s.id] ?? ""}
                          on:input={(e) => (editCoord = { ...editCoord, [s.id]: e.target.value })}
                        />
                        <button class="small" disabled={loading} on:click={() => saveCoord(s)}>保存到册</button>
                      </div>
                    {:else}
                      {s.staked_at ? new Date(s.staked_at).toLocaleString() : "—"}
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
        {#if isWriter}<p class="hint">改坐标只更新坐标册；已经办结的单据保留钉桩瞬间抄录的坐标与结论，不被牵动。</p>{/if}
      </section>

      <section>
        <h2>③ 退单原因</h2>
        {#if rejected.length === 0}
          <p class="empty">暂无退单。</p>
        {:else}
          <table>
            <thead>
              <tr><th>编号</th><th>断面号</th><th>桩号</th><th>收敛mm</th><th>退单原因</th><th>时间</th></tr>
            </thead>
            <tbody>
              {#each rejected as r}
                <tr>
                  <td>{r.id}</td>
                  <td>{r.section_no ?? "—"}</td>
                  <td>{r.chainage}</td>
                  <td>{r.delta_mm}</td>
                  <td class="err">{r.reason}</td>
                  <td>{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/if}

    {#if detail}
      <div class="modal-mask" on:click={() => (detail = null)}>
        <div class="modal" on:click={(e) => e.stopPropagation()}>
          <h2>单据 #{detail.id}</h2>
          <dl>
            <dt>断面号</dt><dd>{detail.section_no ?? "—"}</dd>
            <dt>桩号</dt><dd>{detail.chainage}</dd>
            <dt>收敛</dt><dd>{detail.delta_mm} mm</dd>
            <dt>单据坐标</dt>
            <dd>
              {#if detail.coord_snapshot !== null && detail.coord_snapshot !== undefined}
                <strong>{detail.coord_snapshot}</strong>（钉桩时抄入，不随后续改册变化）
              {:else}<span class="err">未钉桩，无坐标</span>{/if}
            </dd>
            <dt>状态</dt><dd><span class="tag {statusClass(detail.status)}">{statusLabel(detail.status)}</span></dd>
            <dt>结论</dt><dd>{detail.verdict ?? "—"}</dd>
            <dt>说明</dt><dd>{detail.reason ?? "—"}</dd>
          </dl>
          <button class="secondary small" on:click={() => (detail = null)}>关闭</button>
        </div>
      </div>
    {/if}
  {/if}
</main>
