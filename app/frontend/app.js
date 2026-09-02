const $ = (id) => document.getElementById(id);
let parsed = {};
let lastResult = {};
let loginReady = false;
let loginFailCount = 0;
let lastJobStage = "";
let lastJobStageAt = 0;
let appJobRunning = false;
let fullWorkflowRunning = false;

const WORKFLOW_STORAGE_KEY = "codex_dev_app_listing_workflow_v1";
const WORKFLOW_BLOCKED_STATES = [
  "SUCCESS", "FAILED", "CANCELLED", "CHANNEL_RUNNING", "CHANNEL_UNKNOWN",
  "APP_SUBMITTING", "APP_SUBMIT_UNKNOWN",
];

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
  const busy = appJobRunning || fullWorkflowRunning;
  $("createChannelBtn").disabled = !loginReady || busy;
  $("createAppBtn").disabled = !loginReady || busy;
  $("locateAppBtn").disabled = !loginReady || busy;
  renderWorkflow();
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
  data.app_name_preview = data.business_object;
  return data;
}

function workflowFormData() {
  return { ...readForm(), base_platform: $("base_platform").value || "" };
}

function workflowFingerprint(data) {
  return JSON.stringify({
    channel_base_name: data.channel_base_name,
    base_platform: data.base_platform,
    business_object: data.business_object,
    activity_name: data.activity_name,
    application_type: data.application_type,
    settlement_type: data.settlement_type,
    group_name: data.group_name,
    jump_address: data.jump_address,
    resource_fallback_page: data.resource_fallback_page,
  });
}

function loadWorkflowCheckpoint() {
  try {
    const raw = localStorage.getItem(WORKFLOW_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function saveWorkflowCheckpoint(checkpoint) {
  localStorage.setItem(WORKFLOW_STORAGE_KEY, JSON.stringify(checkpoint));
  renderWorkflow(checkpoint);
  return checkpoint;
}

function restoreWorkflowForm() {
  const checkpoint = loadWorkflowCheckpoint();
  if (!checkpoint?.input) return;
  for (const id of fields) {
    if ($(id) && checkpoint.input[id] !== undefined) $(id).value = checkpoint.input[id] || "";
  }
  $("base_platform").value = checkpoint.input.base_platform || "";
  if (checkpoint.actual_channel_name) $("actual_channel_name").value = checkpoint.actual_channel_name;
}

function renderWorkflow(checkpoint = loadWorkflowCheckpoint()) {
  const button = $("runWorkflowBtn");
  const blocked = checkpoint && WORKFLOW_BLOCKED_STATES.includes(checkpoint.state);
  button.disabled = appJobRunning || fullWorkflowRunning;
  if (fullWorkflowRunning) button.textContent = "上架自测运行中";
  else if (checkpoint?.app_job_id && !blocked) button.textContent = "继续查询原任务";
  else if (checkpoint?.actual_channel_name && !blocked) button.textContent = "从创建应用继续";
  else if (checkpoint?.state === "SUCCESS") button.textContent = "开始下一次自测";
  else if (blocked) button.textContent = "核对后重新自测";
  else button.textContent = "完整上架自测";

  const defaultMessage = loginReady ? "已就绪" : "点击后自动检查登录状态";
  const status = $("workflowMessage");
  const stepText = checkpoint?.steps
    ? [
        ["渠道", checkpoint.steps.channel],
        ["保存", checkpoint.steps.save],
        ["开启", checkpoint.steps.enable],
        ["分组", checkpoint.steps.group],
      ].map(([name, value]) => name + "：" + (value?.[1] || "待执行")).join("；")
    : "";
  status.textContent = [checkpoint?.message || defaultMessage, stepText].filter(Boolean).join("\n");
  status.classList.remove("ok", "err", "running", "muted");
  if (!checkpoint) status.classList.add("muted");
  else if (checkpoint.state === "SUCCESS") status.classList.add("ok");
  else if (blocked) status.classList.add("err");
  else status.classList.add("running");
}

function workflowStepsFromJob(job) {
  const steps = {
    channel: ["success", "已完成"],
    save: ["pending", "待执行"],
    enable: ["pending", "待执行"],
    group: ["pending", "待执行"],
  };
  const stage = String(job.stage || "");
  const detail = job.result || job.error || {};
  const completed = Array.isArray(detail.completed_stages) ? detail.completed_stages : [];
  const failedStage = detail.failed_stage || stage;

  if (job.status === "SUCCESS") {
    steps.save = ["success", "已完成"];
    steps.enable = ["success", "已开启"];
    steps.group = completed.length && !completed.includes("SET_GROUP")
      ? ["success", "已跳过"]
      : ["success", "已设置"];
    return steps;
  }

  if (job.status === "FAILED" || job.status === "CANCELLED") {
    const failed = job.status === "CANCELLED" ? "已取消" : "失败";
    if (completed.includes("CREATE_SAVE")) steps.save = ["success", "已完成"];
    if (completed.includes("ENABLE")) steps.enable = ["success", "已开启"];
    if (completed.includes("SET_GROUP")) steps.group = ["success", "已设置"];
    if (failedStage === "ENABLE" || failedStage === "PUBLISH") {
      steps.save = ["success", "已完成"];
      steps.enable = ["failed", failed];
    } else if (failedStage === "SET_GROUP" || failedStage === "GROUP") {
      steps.save = ["success", "已完成"];
      steps.enable = ["success", "已开启"];
      steps.group = ["failed", failed];
    } else if (failedStage === "VERIFY") {
      steps.group = ["failed", "终态校验失败"];
    } else {
      steps.save = ["failed", failed];
    }
    return steps;
  }

  if (stage.includes("发布")) {
    steps.save = ["success", "已完成"];
    steps.enable = ["running", "执行中"];
  } else if (stage.includes("分组")) {
    steps.save = ["success", "已完成"];
    steps.enable = ["success", "已开启"];
    steps.group = ["running", "执行中"];
  } else if (stage.includes("校验终态")) {
    steps.save = ["success", "已完成"];
    steps.enable = ["success", "已开启"];
    steps.group = ["running", "校验中"];
  } else {
    steps.save = ["running", stage || "等待执行"];
  }
  return steps;
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

function formatFailedJob(job, elapsed) {
  const detail = job.result || job.error || {};
  const lines = [
    "任务状态：" + jobStatusLabel(job.status),
    "失败阶段：" + (job.stage || detail.error_stage || "未知"),
    "状态说明：" + (job.message || detail.message || "任务执行失败"),
    "已用时间：" + elapsed + " 秒",
    "任务编号：" + job.job_id,
    detail.operator_message || "本任务未自动重试。请先在平台按应用ID或实际渠道名检查是否已保存，再决定是否重新提交。",
  ];
  if (job.execution_id) lines.push("执行编号：" + job.execution_id);
  lines.push("", "执行结果：", JSON.stringify(detail, null, 2));
  return lines.join(String.fromCharCode(10));
}

async function pollAppJob(jobId, startedAt, onUpdate = null) {
  let queryFailures = 0;
  while (true) {
    const elapsed = Math.round((Date.now() - startedAt) / 1000);
    let job;
    try {
      job = await api("/api/jobs/" + jobId);
      queryFailures = 0;
      if (onUpdate) onUpdate(job);
    } catch (err) {
      queryFailures += 1;
      const reason = err?.message || err?.detail || JSON.stringify(err);
      setResult(
        $("appResult"),
        [
          "任务状态：查询暂时中断",
          "任务编号：" + jobId,
          "连续查询失败：" + queryFailures + " 次",
          "状态说明：" + reason,
          "已用时间：" + elapsed + " 秒",
          "创建任务可能仍在执行，页面会继续查询，请勿重新提交。",
        ].join(String.fromCharCode(10)),
        false,
      );
      $("appResult").classList.add("running");
      await sleep(3000);
      continue;
    }

    if (job.status === "SUCCESS") {
      lastResult = job.result || {};
      setResult($("appResult"), lastResult, true);
      $("appResult").classList.remove("running");
      try {
        await refreshLedger(lastResult);
      } catch (err) {
        $("ledgerPreview").value = "应用已创建成功，但台账行生成失败：" + (err?.message || JSON.stringify(err));
      }
      appJobRunning = false;
      setActionAvailability();
      return job;
    }

    if (job.status === "FAILED") {
      lastResult = job.result || job.error || job;
      setResult($("appResult"), formatFailedJob(job, elapsed), false);
      $("appResult").classList.remove("running");
      appJobRunning = false;
      setActionAvailability();
      return job;
    }

    if (job.status === "CANCELLED") {
      lastResult = job;
      setResult(
        $("appResult"),
        [
          "任务状态：已取消",
          "任务编号：" + job.job_id,
          "状态说明：" + (job.message || "任务未开始，已取消。"),
        ].join(String.fromCharCode(10)),
        false,
      );
      $("appResult").classList.remove("running");
      appJobRunning = false;
      setActionAvailability();
      return job;
    }

    setResult($("appResult"), formatJob(job, elapsed), true);
    $("appResult").classList.add("running");
    await sleep(1500);
  }
}

function validateAppData(data, requireGroup = false) {
  const required = [
    ["business_object", "业务对象"],
    ["actual_channel_name", "实际渠道名"],
    ["jump_address", "调起路径"],
    ["resource_fallback_page", "资源不足兜底页"],
    ["settlement_type", "结算类型"],
  ];
  if (requireGroup) required.push(["group_name", "分组"]);
  return required.filter(([key]) => !data[key]).map(([, label]) => label);
}

function buildAppRequest(data) {
  return {
    business_object: data.business_object,
    actual_channel_name: data.actual_channel_name,
    application_type: data.application_type,
    jump_address: data.jump_address,
    resource_fallback_page: data.resource_fallback_page,
    settlement_type: data.settlement_type,
    group_name: data.group_name,
  };
}

$("createAppBtn").onclick = async () => {
  try {
    parsed = { ...parsed, ...readForm() };
    const missing = validateAppData(parsed);
    if (missing.length) {
      setResult($("appResult"), `请先填写：${missing.join("、")}。已有渠道可以直接填写渠道名，无需重新创建渠道。`, false);
      return;
    }

    const body = buildAppRequest(parsed);

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
    const message = err?.message || err?.detail || JSON.stringify(err);
    setResult(
      $("appResult"),
      "任务提交或查询异常：" + message + String.fromCharCode(10) +
        "如果已经点击创建，请先检查任务列表和平台，不要立即重复提交。",
      false,
    );
  }
};

$("runWorkflowBtn").onclick = async () => {
  const input = workflowFormData();
  const missing = [];
  if (!input.channel_base_name) missing.push("渠道基础名");
  missing.push(...validateAppData({ ...input, actual_channel_name: "由流程生成" }, true));
  if (missing.length) {
    setResult($("appResult"), "完整流程还缺少：" + missing.join("、") + "。", false);
    return;
  }

  const fingerprint = workflowFingerprint(input);
  let checkpoint = loadWorkflowCheckpoint();
  if (checkpoint && WORKFLOW_BLOCKED_STATES.includes(checkpoint.state)) {
    const confirmed = confirm(
      "本地保存着上一次流程结果。这里只会清除本地检查点，不会删除后台任务或平台数据。\n\n确认已经核对上一次任务，继续开始新的唯一数据测试？",
    );
    if (!confirmed) return;
    localStorage.removeItem(WORKFLOW_STORAGE_KEY);
    checkpoint = null;
  }
  if (checkpoint && checkpoint.fingerprint !== fingerprint) {
    setResult(
      $("appResult"),
      "当前表单与本地检查点不一致。请先核对旧任务；确认无需继续后，再点击“重置本地检查点”。",
      false,
    );
    return;
  }

  fullWorkflowRunning = true;
  setActionAvailability();
  const startedAt = checkpoint?.started_at || Date.now();

  try {
    if (!loginReady) {
      $("workflowMessage").textContent = "正在检查浏览器和登录状态";
      $("workflowMessage").classList.remove("muted", "ok", "err");
      $("workflowMessage").classList.add("running");
      let login;
      try {
        login = await api("/api/login", { method: "POST", body: "{}" });
      } catch (err) {
        const message = err?.message || err?.detail || JSON.stringify(err);
        setLoginState("err", "登录检查失败", message);
        setResult($("appResult"), "完整自测未开始：" + message, false);
        return;
      }
      if (!login.success) {
        setLoginState("err", "登录失败", login.message || "无法登录执行平台");
        setResult($("appResult"), "完整自测未开始：" + (login.message || "登录失败"), false);
        return;
      }
      loginReady = true;
      setLoginState("ok", "登录成功", "完整上架自测继续执行。" );
    }

    if (!checkpoint) {
      checkpoint = {
        version: 1,
        fingerprint,
        input,
        started_at: startedAt,
        state: "CHANNEL_RUNNING",
        actual_channel_name: "",
        app_job_id: "",
        message: "正在创建渠道",
        steps: {
          channel: ["running", "执行中"],
          save: ["pending", "待执行"],
          enable: ["pending", "待执行"],
          group: ["pending", "待执行"],
        },
      };
      saveWorkflowCheckpoint(checkpoint);
      setResult($("channelResult"), "完整流程正在创建渠道，请勿重复提交。", true);

      let channel;
      try {
        channel = await api("/api/channels", {
          method: "POST",
          body: JSON.stringify({
            channel_base_name: input.channel_base_name,
            base_platform: input.base_platform || null,
          }),
        });
      } catch (err) {
        checkpoint.state = "CHANNEL_UNKNOWN";
        checkpoint.steps.channel = ["unknown", "结果未知"];
        checkpoint.message = "渠道请求中断，请先到平台核对，前端不会自动重建";
        saveWorkflowCheckpoint(checkpoint);
        setResult($("channelResult"), checkpoint.message + "。\n" + (err?.message || JSON.stringify(err)), false);
        return;
      }

      if (!channel.success) {
        checkpoint.state = "FAILED";
        checkpoint.steps.channel = ["failed", "失败"];
        checkpoint.message = channel.message || "创建渠道失败";
        saveWorkflowCheckpoint(checkpoint);
        setResult($("channelResult"), channel, false);
        return;
      }

      checkpoint.state = "CHANNEL_SUCCESS";
      checkpoint.actual_channel_name = channel.actual_channel_name || input.channel_base_name;
      checkpoint.steps.channel = ["success", "已完成"];
      checkpoint.message = "渠道已完成，准备提交应用任务";
      saveWorkflowCheckpoint(checkpoint);
      $("actual_channel_name").value = checkpoint.actual_channel_name;
      setResult($("channelResult"), "渠道创建成功：" + checkpoint.actual_channel_name, true);
    } else if (checkpoint.actual_channel_name) {
      $("actual_channel_name").value = checkpoint.actual_channel_name;
    }

    if (!checkpoint.app_job_id) {
      checkpoint.state = "APP_SUBMITTING";
      checkpoint.steps.save = ["running", "提交中"];
      checkpoint.message = "正在提交应用三阶段任务";
      saveWorkflowCheckpoint(checkpoint);
      appJobRunning = true;
      setActionAvailability();
      const body = buildAppRequest({ ...input, actual_channel_name: checkpoint.actual_channel_name });

      let submitted;
      try {
        submitted = await api("/api/apps/async", { method: "POST", body: JSON.stringify(body) });
      } catch (err) {
        checkpoint.state = "APP_SUBMIT_UNKNOWN";
        checkpoint.steps.save = ["unknown", "提交未知"];
        checkpoint.message = "应用任务提交中断，请先查任务列表和平台，前端不会再次提交";
        saveWorkflowCheckpoint(checkpoint);
        setResult($("appResult"), checkpoint.message + "。\n" + (err?.message || JSON.stringify(err)), false);
        return;
      }

      checkpoint.app_job_id = submitted.job_id;
      checkpoint.state = submitted.status || "QUEUED";
      checkpoint.message = "应用任务已提交：" + submitted.job_id;
      saveWorkflowCheckpoint(checkpoint);
      setResult($("appResult"), checkpoint.message, true);
    } else {
      appJobRunning = true;
      setActionAvailability();
    }

    const terminal = await pollAppJob(checkpoint.app_job_id, startedAt, (job) => {
      checkpoint.state = job.status;
      checkpoint.steps = workflowStepsFromJob(job);
      checkpoint.message = "应用任务 " + jobStatusLabel(job.status) + "：" + (job.stage || "等待执行");
      saveWorkflowCheckpoint(checkpoint);
    });
    checkpoint.state = terminal.status;
    checkpoint.steps = workflowStepsFromJob(terminal);
    checkpoint.message = terminal.status === "SUCCESS"
      ? "完整上架流程已通过"
      : "流程已停止，请按任务编号核对后再重置检查点";
    saveWorkflowCheckpoint(checkpoint);
  } finally {
    appJobRunning = false;
    fullWorkflowRunning = false;
    setActionAvailability();
  }
};

$("locateAppBtn").onclick = async () => {
  const data = readForm();
  if (!data.actual_channel_name || !data.business_object || !data.activity_name) {
    setResult($("appResult"), "定位应用需要填写业务对象、活动名称和实际渠道名。", false);
    return;
  }
  appJobRunning = true;
  setActionAvailability();
  setResult($("appResult"), "正在平台中定位现有应用。", true);
  $("appResult").classList.add("running");
  try {
    const result = await api("/api/apps/locate", {
      method: "POST",
      body: JSON.stringify({
        app_name: data.business_object + "-" + data.activity_name,
        channel_name: data.actual_channel_name,
      }),
    });
    setResult($("appResult"), result, Boolean(result.success));
  } catch (err) {
    setResult($("appResult"), err, false);
  } finally {
    $("appResult").classList.remove("running");
    appJobRunning = false;
    setActionAvailability();
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

restoreWorkflowForm();
setActionAvailability();









