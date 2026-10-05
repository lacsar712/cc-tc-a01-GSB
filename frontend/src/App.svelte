<script>
  let session = null;
  let page = "stakes"; // stakes=钉桩专页  logs=总表
  let logs = [];
  let stakes = [];
  let loginUser = "surveyor";
  let loginPass = "surv123456";
  let chainage = "";
  let coordinate = "";
  let deltaMm = "";
  let coordEdits = {}; // stakeId -> 草稿坐标
  let error = "";
  let notice = "";
  let loading = false;
  let timer;

  $: isWriter = session?.role === "writer";
  $: stakedSet = new Set(stakes.map((s) => s.chainage));
  $: returnedLogs = logs.filter((r) => r.status === "returned");
  // 待钉清单：被退回、且断面仍未进册的桩号（去重）
  $: pendingStakes = [...new Set(returnedLogs.map((r) => r.chainage))]
    .filter((c) => !stakedSet.has(c))
    .map((c) => ({ chainage: c, latest: returnedLogs.filter((r) => r.chainage === c).at(-1) }));

  function headers() {
    return session ? { Authorization: "Bearer " + session.token } : {};
  }

  async function refresh() {
    if (!session) return;
    const [lr, sr] = await Promise.all([
      fetch("/api/logs", { headers: headers() }),
      fetch("/api/stakes", { headers: headers() }),
    ]);
    if (lr.status === 401 || sr.status === 401) {
      logout();
      return;
    }
    if (lr.ok) logs = await lr.json();
    if (sr.ok) stakes = await sr.json();
  }

  async function login() {
    error = "";
    loading = true;
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginUser, password: loginPass }),
      });
      const data = await res.json();
      if (!res.ok) {
        error = data.detail || "登录失败";
        return;
      }
      session = { token: data.access_token, username: data.username, role: data.role };
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
    localStorage.removeItem("tunnel_session");
  }

  // 钉桩登记 + 报送同一次提交。缺坐标会被整份退回。
  async function submit() {
    error = "";
    notice = "";
    loading = true;
    try {
      const res = await fetch("/api/submissions", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers() },
        body: JSON.stringify({ chainage, coordinate, delta_mm: Number(deltaMm) }),
      });
      const data = await res.json();
      if (!res.ok) {
        error = data.detail || "提交失败";
        return;
      }
      if (res.status === 202) {
        notice = `整份退回：${data.reason}`;
      } else {
        notice = `钉桩成功，坐标 ${data.coordinate_snapshot} 已抄进单据 #${data.id}，进入待认领。`;
        chainage = "";
        coordinate = "";
        deltaMm = "";
      }
      await refresh();
    } catch {
      error = "提交时网络异常";
    } finally {
      loading = false;
    }
  }

  async function saveCoordinate(stake) {
    error = "";
    notice = "";
    const next = (coordEdits[stake.id] ?? "").trim();
    if (!next) {
      error = "坐标不能为空";
      return;
    }
    const res = await fetch(`/api/stakes/${stake.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ coordinate: next }),
    });
    const data = await res.json();
    if (!res.ok) {
      error = data.detail || "改坐标失败";
      return;
    }
    notice = `档案坐标已改为 ${next}；已办结单据的坐标快照与结论不变。`;
    coordEdits = { ...coordEdits, [stake.id]: "" };
    await refresh();
  }

  function goStake(c) {
    chainage = c;
    page = "stakes";
    notice = "该断面还没钉桩，请填坐标后重新钉桩报送。";
  }

  function statusTag(s) {
    if (s === "pending") return ["待认领", "pending"];
    if (s === "done") return ["已办结", "ok"];
    return ["已退回", "bad"];
  }

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
  main { max-width: 1000px; margin: 0 auto; padding: 1.5rem; }
  h1 { color: #fbbf24; margin: 0; font-size: 1.4rem; }
  .sub { color: #a8a29e; margin: 0.4rem 0 1rem; }
  header.bar {
    display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
    border-bottom: 1px solid #44403c; padding-bottom: 0.75rem; margin-bottom: 1.1rem;
  }
  nav { display: flex; gap: 0.5rem; }
  nav button { background: #44403c; }
  nav button.active { background: #d97706; }
  .spacer { flex: 1; }
  section {
    background: #292524; border: 1px solid #44403c; border-radius: 8px;
    padding: 1rem 1.25rem; margin-bottom: 1rem;
  }
  h2 { font-size: 1.05rem; margin: 0 0 0.75rem; color: #fde68a; }
  h3 { font-size: 0.95rem; margin: 0 0 0.6rem; color: #d6d3d1; }
  label { display: block; font-size: 0.85rem; color: #d6d3d1; margin-bottom: 0.25rem; }
  input {
    width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px;
    border: 1px solid #57534e; background: #0c0a09; color: #fafaf9; margin-bottom: 0.75rem;
  }
  button {
    cursor: pointer; padding: 0.45rem 0.9rem; border: none; border-radius: 6px;
    background: #d97706; color: #fff; font-weight: 600;
  }
  button.secondary { background: #57534e; }
  button.mini { padding: 0.25rem 0.6rem; font-size: 0.8rem; }
  .err { color: #fb7185; }
  .ok-note { color: #86efac; }
  table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #44403c; vertical-align: top; }
  .tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; white-space: nowrap; }
  .ok { background: #14532d; color: #86efac; }
  .bad { background: #7f1d1d; color: #fca5a5; }
  .pending { background: #713f12; color: #fde68a; }
  .muted { color: #78716c; }
  .coord-edit { display: flex; gap: 0.5rem; align-items: center; }
  .coord-edit input { margin: 0; min-width: 220px; }
  .grid3 { display: grid; grid-template-columns: 1fr; gap: 1rem; }
</style>

<main>
  {#if !session}
    <h1>隧道收敛测缝台</h1>
    <p class="sub">进队先钉桩：断面坐标必须钉进坐标册后才能报送收敛。登录框已预填可写账号 surveyor / surv123456。</p>
    <section>
      <label>用户名</label>
      <input bind:value={loginUser} autocomplete="off" />
      <label>密码</label>
      <input type="password" bind:value={loginPass} autocomplete="off" />
      <button disabled={loading} on:click={login}>登录</button>
      {#if error}<p class="err">{error}</p>{/if}
    </section>
  {:else}
    <header class="bar">
      <h1>隧道收敛测缝台</h1>
      <nav>
        <button class:active={page === "stakes"} on:click={() => (page = "stakes")}>钉桩专页</button>
        <button class:active={page === "logs"} on:click={() => (page = "logs")}>断面总表</button>
      </nav>
      <span class="spacer"></span>
      <span class="sub" style="margin:0">{session.username}（{isWriter ? "测量员·可写" : "巡检员·只读"}）</span>
      <button class="secondary mini" on:click={logout}>退出</button>
    </header>

    {#if error}<p class="err">{error}</p>{/if}
    {#if notice}<p class="ok-note">{notice}</p>{/if}

    {#if page === "stakes"}
      <!-- 钉桩登记 + 报送：同一次提交 -->
      {#if isWriter}
        <section>
          <h2>钉桩并报送</h2>
          <label>断面号（桩号）</label>
          <input placeholder="例如 甲 / K20+050" bind:value={chainage} />
          <label>断面坐标（钉进坐标册；留空报送将被整份退回）</label>
          <input placeholder="例如 X2005.000 / Y1010.300" bind:value={coordinate} />
          <label>收敛（毫米，可正可负）</label>
          <input type="number" step="0.1" bind:value={deltaMm} />
          <button disabled={loading} on:click={submit}>钉桩并报送（同一次提交）</button>
        </section>
      {:else}
        <section><p class="muted" style="margin:0">巡检员可查看坐标册与单据坐标，但不能钉桩、改坐标或报送。</p></section>
      {/if}

      <div class="grid3">
        <!-- 待钉清单 -->
        <section>
          <h3>① 待钉清单</h3>
          {#if pendingStakes.length === 0}
            <p class="muted" style="margin:0">暂无待钉断面。</p>
          {:else}
            <table>
              <thead><tr><th>断面号</th><th>退回原因</th>{#if isWriter}<th></th>{/if}</tr></thead>
              <tbody>
                {#each pendingStakes as item}
                  <tr>
                    <td>{item.chainage}</td>
                    <td class="err">还没钉桩</td>
                    {#if isWriter}<td><button class="mini" on:click={() => goStake(item.chainage)}>去钉</button></td>{/if}
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>

        <!-- 已钉档案 -->
        <section>
          <h3>② 已钉档案（断面坐标册）</h3>
          {#if stakes.length === 0}
            <p class="muted" style="margin:0">册上还没有任何桩号。</p>
          {:else}
            <table>
              <thead><tr><th>断面号</th><th>坐标（册上现值）</th><th>钉桩人</th>{#if isWriter}<th>改坐标（不动已办结单）</th>{/if}</tr></thead>
              <tbody>
                {#each stakes as s}
                  <tr>
                    <td>{s.chainage}</td>
                    <td>{s.coordinate}</td>
                    <td>{s.staked_by}</td>
                    {#if isWriter}
                      <td>
                        <div class="coord-edit">
                          <input placeholder="新坐标" value={coordEdits[s.id] ?? ""}
                            on:input={(e) => (coordEdits = { ...coordEdits, [s.id]: e.target.value })} />
                          <button class="mini secondary" on:click={() => saveCoordinate(s)}>存档</button>
                        </div>
                      </td>
                    {/if}
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>

        <!-- 退单原因 -->
        <section>
          <h3>③ 退单原因</h3>
          {#if returnedLogs.length === 0}
            <p class="muted" style="margin:0">暂无退单。</p>
          {:else}
            <table>
              <thead><tr><th>单号</th><th>断面号</th><th>收敛mm</th><th>退单原因</th></tr></thead>
              <tbody>
                {#each returnedLogs as r}
                  <tr>
                    <td>#{r.id}</td>
                    <td>{r.chainage}</td>
                    <td>{r.delta_mm ?? "—"}</td>
                    <td class="err">{r.reason}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>
      </div>
    {/if}

    {#if page === "logs"}
      <section>
        <h2>断面总表（单据坐标为钉桩时快照，只读）</h2>
        <table>
          <thead>
            <tr><th>单号</th><th>断面号</th><th>单据坐标</th><th>收敛mm</th><th>状态</th><th>结论</th><th>说明</th><th>提交人</th></tr>
          </thead>
          <tbody>
            {#each logs as row}
              <tr>
                <td>#{row.id}</td>
                <td>{row.chainage}</td>
                <td>{row.coordinate_snapshot ?? "—"}</td>
                <td>{row.delta_mm ?? "—"}</td>
                <td><span class="tag {statusTag(row.status)[1]}">{statusTag(row.status)[0]}</span></td>
                <td>
                  {#if row.verdict}<span class="tag {row.verdict === "合格" ? "ok" : "bad"}">{row.verdict}</span>{:else}—{/if}
                </td>
                <td>{row.reason ?? "—"}</td>
                <td>{row.created_by}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}
</main>
