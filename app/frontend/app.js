const $ = (id) => document.getElementById(id);
let parsed = {};
let lastResult = {};
let loginReady = false;
let loginFailCount = 0;
let lastJobStage = "";
let lastJobStageAt = 0;
let appJobRunning = false;

const fields = [
  "business_object", "activity_name", "actual_channel_name", "application_type",
  "settlement_type", "group_name", "jump_address", "resource_fallback_page", "channel_base_name"
];

function setResult(el, data, ok = true) {
  el.classList.remove("ok", "err", "muted");
  el.classList.add(ok ? "ok" : "err");
  el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

function setLoginState(state, title, message) {
  const card = $("loginCard");
  const dot = $("loginDot");
  card.classList.remove("checking", "ok", "err", "idle");
  dot.classList.remove("checking", "ok", "err", "idle");
  card.classList.add(state);
  dot.classList.add(state);
  $("loginTitle").textContent = title;
  $("loginMessage").textContent = message;
}

function setActionAvailability() {
  $("createChannelBtn").disabled = !loginReady;
  $("createAppBtn").disabled = !loginReady || appJobRunning;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) throw data;
  return data;
}

function readForm() {
  const data = {};
  for (const id of fields) data[id] = $(id)?.value?.trim() || "";
  data.app_name_preview = `${data.business_object}-${data.activity_name}`;
  return data;
}

function fillForm(data) {
  parsed = { ...data };
  for (const id of fields) if ($(id) && data[id] !== undefined) $(id).value = data[id] || "";
  $("template_hint").textContent = data.template_hint || "可按解析结果选择复制模板，执行前仍可调整。";
}

async function refreshLedger(result = {}) {
  parsed = { ...parsed, ...readForm() };
  const row = await api("/api/ledger-row", {
    method: "POST",
    body: JSON.stringify({ parsed, result }),
  });
  $("ledgerPreview").value = row.tsv || "";
  $("copyLedgerBtn").disabled = !row.tsv;
}

$("parseBtn").onclick = async () => {
  try {
    const raw_text = $("rawText").value.trim();
    const data = await api("/api/parse-email", { method: "POST", body: JSON.stringify({ raw_text }) });
    fillForm(data.parsed);
    $("ledgerPreview").value = data.ledger_row.tsv;
    $("copyLedgerBtn").disabled = false;
    setResult($("channelResult"), loginReady
      ? "已解析。可创建新渠道；如渠道已存在，直接在右侧填写实际渠道名并创建应用。"
      : "已解析。请先检查浏览器和登录状态。", true);
  } catch (err) { setResult($("channelResult"), err, false); }
};

$("statusBtn").onclick = async () => {
  const button = $("statusBtn");
  button.disabled = true;
  loginReady = false;
  
async function refreshExecutorLock() {
  const data = await api("/api/executor/lock");
  if (!data.locked) {
    setLoginState("ok", "执行端空闲", data.message || "当前没有任务占用执行端。");
    return data;
  }
  const age = data.age_seconds == null ? "未知" : `${data.age_seconds} 秒`;
  const text = [
    data.message,
    `任务类型：${data.operation || "未知"}`,
    `当前阶段：${data.stage || "未知"}`,
    `占用时长：${age}`,
    data.stale_candidate ? "提示：占用超过 10 分钟，可确认浏览器无动作后释放。" : "提示：如果浏览器仍在自动操作，请继续等待。",
  ].join("\n");
  setLoginState(data.stale_candidate ? "err" : "checking", data.stale_candidate ? "可能卡住" : "执行端忙碌中", text);
  return data;
}

$("lockBtn").onclick = async () => {
  try { await refreshExecutorLock(); }
  catch (err) { setLoginState("err", "锁状态检查失败", err?.message || JSON.stringify(err)); }
};

$("releaseLockBtn").onclick = async () => {
  try {
    const lock = await refreshExecutorLock();
    if (!lock.locked) return;
    const ok = confirm(`确认释放执行端锁？\n\n占用执行：${lock.holder}\n当前阶段：${lock.stage || "未知"}\n\n只有确认浏览器已经卡住、没有继续自动操作时才释放。`);
    if (!ok) return;
    const data = await api("/api/executor/lock/release", { method: "POST", body: "{}" });
    setLoginState("ok", "已释放卡住任务", data.message || "执行端锁已释放，可以重试或继续队列。");
  } catch (err) {
    setLoginState("err", "释放失败", err?.message || JSON.stringify(err));
  }
};

setActionAvailability();
  setLoginState("checking", loginFailCount > 0 ? "重试中" : "启动浏览器中", "正在启动或检查 Chrome/Edge 调试端口 9222。");
  try {
    setTimeout(() => {
      if (button.disabled) setLoginState("checking", "登录中", "浏览器已启动，正在检查登录状态；如需验证码，会自动识别并重试。");
    }, 800);

    const data = await api("/api/login", { method: "POST", body: "{}" });
    if (data.success) {
      loginReady = true;
      loginFailCount = 0;
      setLoginState("ok", "登录成功", data.already_logged_in ? "当前会话已登录，可以创建渠道或应用。" : "已自动登录成功，可以创建渠道或应用。");
      setResult($("channelResult"), "浏览器和登录状态正常。可创建新渠道，也可使用已有渠道直接创建应用。", true);
    } else {
      loginFailCount += 1;
      const retryText = loginFailCount >= 3
        ? "连续 3 次验证码解析失败，请点击重试，或联系管理员手动登录。"
        : "登录失败，可能是验证码解析失败。可以点击重试。";
      setLoginState("err", "登录失败", `${retryText} ${data.message || ""}`.trim());
    }
  } catch (err) {
    loginFailCount += 1;
    const message = err?.message || err?.detail || JSON.stringify(err);
    const retryText = loginFailCount >= 3
      ? "已连续 3 次检查失败，请点击重试或联系管理员手动登录。"
      : "检查失败，请点击重试。";
    setLoginState("err", "登录失败", `${retryText} ${message}`.trim());
  } finally {
    button.disabled = false;
    
async function refreshExecutorLock() {
  const data = await api("/api/executor/lock");
  if (!data.locked) {
    setLoginState("ok", "执行端空闲", data.message || "当前没有任务占用执行端。");
    return data;
  }
  const age = data.age_seconds == null ? "未知" : `${data.age_seconds} 秒`;
  const text = [
    data.message,
    `任务类型：${data.operation || "未知"}`,
    `当前阶段：${data.stage || "未知"}`,
    `占用时长：${age}`,
    data.stale_candidate ? "提示：占用超过 10 分钟，可确认浏览器无动作后释放。" : "提示：如果浏览器仍在自动操作，请继续等待。",
  ].join("\n");
  setLoginState(data.stale_candidate ? "err" : "checking", data.stale_candidate ? "可能卡住" : "执行端忙碌中", text);
  return data;
}

$("lockBtn").onclick = async () => {
  try { await refreshExecutorLock(); }
  catch (err) { setLoginState("err", "锁状态检查失败", err?.message || JSON.stringify(err)); }
};

$("releaseLockBtn").onclick = async () => {
  try {
    const lock = await refreshExecutorLock();
    if (!lock.locked) return;
    const ok = confirm(`确认释放执行端锁？\n\n占用执行：${lock.holder}\n当前阶段：${lock.stage || "未知"}\n\n只有确认浏览器已经卡住、没有继续自动操作时才释放。`);
    if (!ok) return;
    const data = await api("/api/executor/lock/release", { method: "POST", body: "{}" });
    setLoginState("ok", "已释放卡住任务", data.message || "执行端锁已释放，可以重试或继续队列。");
  } catch (err) {
    setLoginState("err", "释放失败", err?.message || JSON.stringify(err));
  }
};

setActionAvailability();
  }
};

$("createChannelBtn").onclick = async () => {
  const button = $("createChannelBtn");
  button.disabled = true;
  setResult($("channelResult"), "创建渠道进行中，请不要重复点击。", true);
  try {
    const body = { channel_base_name: $("channel_base_name").value.trim(), base_platform: $("base_platform").value || null };
    const data = await api("/api/channels", { method: "POST", body: JSON.stringify(body) });
    lastResult = data;
    if (data.success) {
      $("actual_channel_name").value = data.actual_channel_name || body.channel_base_name;
      parsed = { ...parsed, ...readForm(), actual_channel_name: $("actual_channel_name").value };
      setActionAvailability();
      setResult($("channelResult"), `渠道创建成功：${$("actual_channel_name").value}\n请确认应用表单，确认后创建应用。`, true);
      await refreshLedger(data);
    } else {
      setResult($("channelResult"), data, false);
    }
  } catch (err) {
    setResult($("channelResult"), err, false);
  } finally {
    button.disabled = !loginReady;
  }
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jobStatusLabel(status) {
  return {
    QUEUED: "排队中",
    WAITING: "等待执行端空闲",
    PENDING: "等待开始",
    RUNNING: "执行中",
    SUCCESS: "成功",
    FAILED: "失败",
    CANCELLED: "已取消",
  }[status] || status;
}

function formatJob(job, elapsed) {
  const currentStage = job.stage || "执行中";
  if (currentStage !== lastJobStage) {
    lastJobStage = currentStage;
    lastJobStageAt = Date.now();
  }
  const stageSeconds = Math.round((Date.now() - lastJobStageAt) / 1000);
  const lines = [
    `任务状态：${jobStatusLabel(job.status)}`,
    `当前阶段：${currentStage}`,
    `本阶段停留：${stageSeconds} 秒`,
    `状态说明：${job.message || "正在执行"}`,
    `已用时间：${elapsed} 秒`,
  ];
  if (job.queue_position && ["QUEUED", "WAITING"].includes(job.status)) {
    lines.push(`排队位置：第 ${job.queue_position} 位`);
  }
  if (job.attempt) lines.push(`当前尝试：第 ${job.attempt} 次 / 最多 ${Number(job.max_retries || 0) + 1} 次`);
  if (stageSeconds >= 120 && job.status === "RUNNING") lines.push("提示：当前阶段超过 2 分钟未变化，可能卡住。可先观察浏览器页面，必要时联系管理员处理后重试。");
  if (job.execution_id) lines.push(`执行编号：${job.execution_id}`);
  return lines.join("\n");
}

async function pollAppJob(jobId, startedAt) {
  while (true) {
    const job = await api(`/api/jobs/${jobId}`);
    const elapsed = Math.round((Date.now() - startedAt) / 1000);

    if (job.status === "SUCCESS") {
      lastResult = job.result || {};
      setResult($("appResult"), lastResult, true);
      $("appResult").classList.remove("running");
      await refreshLedger(lastResult);
      appJobRunning = false;
      setActionAvailability();
      return;
    }

    if (job.status === "FAILED") {
      lastResult = job.result || job.error || job;
      setResult($("appResult"), lastResult, false);
      $("appResult").classList.remove("running");
      appJobRunning = false;
      setActionAvailability();
      return;
    }

    setResult($("appResult"), formatJob(job, elapsed), true);
    $("appResult").classList.add("running");
    await sleep(1500);
  }
}

$("createAppBtn").onclick = async () => {
  try {
    parsed = { ...parsed, ...readForm() };
    const missing = [
      ["business_object", "业务对象"],
      ["activity_name", "活动名称"],
      ["actual_channel_name", "实际渠道名"],
    ].filter(([key]) => !parsed[key]).map(([, label]) => label);
    if (missing.length) {
      setResult($("appResult"), `请先填写：${missing.join("、")}。已有渠道可以直接填写渠道名，无需重新创建渠道。`, false);
      return;
    }

    const body = {
      business_object: parsed.business_object,
      activity_name: parsed.activity_name,
      actual_channel_name: parsed.actual_channel_name,
      application_type: parsed.application_type,
      jump_address: parsed.jump_address,
      resource_fallback_page: parsed.resource_fallback_page,
      settlement_type: parsed.settlement_type,
      group_name: parsed.group_name,
    };

    appJobRunning = true;
    setActionAvailability();
    $("copyLedgerBtn").disabled = true;
    $("ledgerPreview").value = "应用尚未创建完成，成功后会生成可复制的台账行。";
    const startedAt = Date.now();
    setResult($("appResult"), "任务状态：正在提交\n当前阶段：创建应用任务准备中\n已用时间：0 秒", true);
    $("appResult").classList.add("running");

    const job = await api("/api/apps/async", { method: "POST", body: JSON.stringify(body) });
    setResult($("appResult"), `任务状态：${jobStatusLabel(job.status)}\n当前阶段：任务已提交\n状态说明：${job.message}\n任务编号：${job.job_id}`, true);
    $("appResult").classList.add("running");
    await pollAppJob(job.job_id, startedAt);
  } catch (err) {
    appJobRunning = false;
    setActionAvailability();
    setResult($("appResult"), err, false);
  }
};

$("copyLedgerBtn").onclick = async () => {
  const text = $("ledgerPreview").value;
  await navigator.clipboard.writeText(text);
  $("copyLedgerBtn").textContent = "已复制";
  setTimeout(() => $("copyLedgerBtn").textContent = "复制台账行", 1200);
};


async function refreshExecutorLock() {
  const data = await api("/api/executor/lock");
  if (!data.locked) {
    setLoginState("ok", "执行端空闲", data.message || "当前没有任务占用执行端。");
    return data;
  }
  const age = data.age_seconds == null ? "未知" : `${data.age_seconds} 秒`;
  const text = [
    data.message,
    `任务类型：${data.operation || "未知"}`,
    `当前阶段：${data.stage || "未知"}`,
    `占用时长：${age}`,
    data.stale_candidate ? "提示：占用超过 10 分钟，可确认浏览器无动作后释放。" : "提示：如果浏览器仍在自动操作，请继续等待。",
  ].join("\n");
  setLoginState(data.stale_candidate ? "err" : "checking", data.stale_candidate ? "可能卡住" : "执行端忙碌中", text);
  return data;
}

$("lockBtn").onclick = async () => {
  try { await refreshExecutorLock(); }
  catch (err) { setLoginState("err", "锁状态检查失败", err?.message || JSON.stringify(err)); }
};

$("releaseLockBtn").onclick = async () => {
  try {
    const lock = await refreshExecutorLock();
    if (!lock.locked) return;
    const ok = confirm(`确认释放执行端锁？\n\n占用执行：${lock.holder}\n当前阶段：${lock.stage || "未知"}\n\n只有确认浏览器已经卡住、没有继续自动操作时才释放。`);
    if (!ok) return;
    const data = await api("/api/executor/lock/release", { method: "POST", body: "{}" });
    setLoginState("ok", "已释放卡住任务", data.message || "执行端锁已释放，可以重试或继续队列。");
  } catch (err) {
    setLoginState("err", "释放失败", err?.message || JSON.stringify(err));
  }
};

setActionAvailability();









