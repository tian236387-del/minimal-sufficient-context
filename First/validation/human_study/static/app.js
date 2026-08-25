const SESSION_KEY = "msc-human-study-session";
const DEFAULT_PROMPT = "请生成一份可编辑的完整草稿，逐项核对当前分支事实并满足交付要求。";
const DOMAIN_LABELS = {
  programming: "编程任务",
  research: "研究任务",
  writing: "写作任务",
};

const state = {
  session: null,
  assignmentIndex: -1,
  chatTurns: [],
  latestDraft: "",
  modelReady: false,
  timerInterval: null,
  timerBaseElapsed: 0,
  timerBaseActive: 0,
  timerWallStarted: 0,
  timerActiveStarted: 0,
  answerEventTimer: null,
};

const elements = {
  onboardingView: document.querySelector("#onboarding-view"),
  workspaceView: document.querySelector("#workspace-view"),
  completionView: document.querySelector("#completion-view"),
  doneView: document.querySelector("#done-view"),
  onboardingForm: document.querySelector("#onboarding-form"),
  resultForm: document.querySelector("#result-form"),
  completionForm: document.querySelector("#completion-form"),
  modelStatus: document.querySelector("#model-status"),
  sessionProgress: document.querySelector("#session-progress"),
  taskDomain: document.querySelector("#task-domain"),
  taskTitle: document.querySelector("#task-title"),
  conditionBadge: document.querySelector("#condition-badge"),
  progressFill: document.querySelector("#progress-fill"),
  taskBrief: document.querySelector("#task-brief"),
  taskDeliverable: document.querySelector("#task-deliverable"),
  activeBranchName: document.querySelector("#active-branch-name"),
  sharedContext: document.querySelector("#shared-context"),
  branchContext: document.querySelector("#branch-context"),
  taskTimer: document.querySelector("#task-timer"),
  draftCount: document.querySelector("#draft-count"),
  promptInput: document.querySelector("#prompt-input"),
  generateButton: document.querySelector("#generate-button"),
  generateStatus: document.querySelector("#generate-status"),
  contextMetrics: document.querySelector("#context-metrics"),
  draftOutput: document.querySelector("#draft-output"),
  draftText: document.querySelector("#draft-text"),
  useDraftButton: document.querySelector("#use-draft-button"),
  finalAnswer: document.querySelector("#final-answer"),
  answerCount: document.querySelector("#answer-count"),
  submitTaskButton: document.querySelector("#submit-task-button"),
  differenceRating: document.querySelector("#difference-rating"),
  differenceOutput: document.querySelector("#difference-output"),
  completionComments: document.querySelector("#completion-comments"),
  toast: document.querySelector("#toast"),
};

function setView(active) {
  for (const [name, element] of Object.entries({
    onboarding: elements.onboardingView,
    workspace: elements.workspaceView,
    completion: elements.completionView,
    done: elements.doneView,
  })) {
    element.hidden = name !== active;
  }
  elements.sessionProgress.hidden = active === "onboarding";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败：HTTP ${response.status}`);
  }
  return payload;
}

let toastTimer = null;
function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 3200);
}

function currentAssignment() {
  return state.session?.assignment[state.assignmentIndex] || null;
}

function currentTask() {
  return currentAssignment()?.task || null;
}

function storageKey(taskId) {
  return `msc-human-task:${state.session.session_id}:${taskId}`;
}

function readTaskState(taskId) {
  try {
    return JSON.parse(localStorage.getItem(storageKey(taskId))) || {};
  } catch {
    return {};
  }
}

function taskElapsedMs() {
  if (!state.timerWallStarted) return state.timerBaseElapsed;
  return state.timerBaseElapsed + (Date.now() - state.timerWallStarted);
}

function taskActiveMs() {
  if (!state.timerActiveStarted) return state.timerBaseActive;
  return state.timerBaseActive + (performance.now() - state.timerActiveStarted);
}

function saveTaskState() {
  const assignment = currentAssignment();
  if (!assignment || assignment.completed) return;
  const ratings = {};
  for (const field of ["confidence", "workload", "usability", "trust"]) {
    ratings[field] = Number(document.querySelector(`#${field}-rating`).value);
  }
  localStorage.setItem(
    storageKey(assignment.task_id),
    JSON.stringify({
      answer: elements.finalAnswer.value,
      ratings,
      elapsed_ms: Math.round(taskElapsedMs()),
      active_ms: Math.round(taskActiveMs()),
      draft_count: Number(elements.draftCount.dataset.count || 0),
    }),
  );
}

function resetTimer(taskState) {
  window.clearInterval(state.timerInterval);
  state.timerBaseElapsed = Number(taskState.elapsed_ms || 0);
  state.timerBaseActive = Number(taskState.active_ms || 0);
  state.timerWallStarted = Date.now();
  state.timerActiveStarted = document.hidden ? 0 : performance.now();
  updateTimer();
  state.timerInterval = window.setInterval(() => {
    updateTimer();
    saveTaskState();
  }, 1000);
}

function updateTimer() {
  const totalSeconds = Math.floor(taskElapsedMs() / 1000);
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  elements.taskTimer.textContent = `${minutes}:${seconds}`;
}

function renderMessage(message) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${message.role}`;
  const role = document.createElement("span");
  role.className = "message-role";
  role.textContent = message.role === "assistant" ? "Assistant" : "User";
  const content = document.createElement("p");
  content.textContent = message.content;
  wrapper.append(role, content);
  return wrapper;
}

function renderContext(task) {
  elements.sharedContext.replaceChildren();
  for (const message of task.shared_context) {
    elements.sharedContext.append(renderMessage(message));
  }

  elements.branchContext.replaceChildren();
  for (const branch of task.branches) {
    const block = document.createElement("section");
    const active = branch.id === task.active_branch_id;
    block.className = `branch-block${active ? " active" : ""}`;
    const label = document.createElement("div");
    label.className = "branch-label";
    const name = document.createElement("strong");
    name.textContent = branch.name;
    label.append(name);
    if (active) {
      const marker = document.createElement("span");
      marker.textContent = "ACTIVE";
      label.append(marker);
    }
    block.append(label);
    for (const message of branch.history) {
      block.append(renderMessage(message));
    }
    elements.branchContext.append(block);
  }
}

function updateDraftCount(count) {
  elements.draftCount.dataset.count = String(count);
  elements.draftCount.textContent = `AI 草稿 ${count} / 4`;
  elements.generateButton.disabled = count >= 4 || !state.modelReady;
}

function postEvent(eventType, payload = {}) {
  const assignment = currentAssignment();
  if (!state.session || !assignment) return Promise.resolve();
  return api("/api/event", {
    method: "POST",
    body: JSON.stringify({
      session_id: state.session.session_id,
      task_id: assignment.task_id,
      event_type: eventType,
      elapsed_ms: Math.round(taskElapsedMs()),
      payload,
    }),
  }).catch(() => undefined);
}

function showTask(index) {
  saveTaskState();
  state.assignmentIndex = index;
  state.chatTurns = [];
  state.latestDraft = "";
  const assignment = currentAssignment();
  const task = assignment.task;
  const taskState = readTaskState(assignment.task_id);
  const activeBranch = task.branches.find((branch) => branch.id === task.active_branch_id);

  setView("workspace");
  elements.taskDomain.textContent = DOMAIN_LABELS[task.domain];
  elements.taskTitle.textContent = task.title;
  elements.conditionBadge.textContent = assignment.condition === "branch" ? "Branch" : "Linear";
  elements.conditionBadge.className = `condition-badge ${assignment.condition}`;
  elements.taskBrief.textContent = task.brief;
  elements.taskDeliverable.textContent = task.deliverable;
  elements.activeBranchName.textContent = activeBranch?.name || "活动分支";
  elements.progressFill.style.width = `${(assignment.order / state.session.assignment.length) * 100}%`;
  elements.sessionProgress.textContent = `任务 ${assignment.order} / ${state.session.assignment.length}`;
  elements.promptInput.value = DEFAULT_PROMPT;
  elements.generateStatus.textContent = "";
  elements.generateStatus.className = "";
  elements.contextMetrics.textContent = "";
  elements.draftOutput.hidden = true;
  elements.draftText.textContent = "";
  elements.finalAnswer.value = taskState.answer || "";
  elements.answerCount.textContent = String(elements.finalAnswer.value.length);

  for (const field of ["confidence", "workload", "usability", "trust"]) {
    const input = document.querySelector(`#${field}-rating`);
    input.value = String(taskState.ratings?.[field] || 4);
    document.querySelector(`#${field}-output`).value = input.value;
  }
  updateDraftCount(Number(taskState.draft_count || 0));
  renderContext(task);
  resetTimer(taskState);
  postEvent("task_opened", {
    order: assignment.order,
    condition: assignment.condition,
    domain: assignment.domain,
  });
  window.scrollTo({ top: 0, behavior: "instant" });
}

function routeSession() {
  if (state.session.completed) {
    window.clearInterval(state.timerInterval);
    setView("done");
    elements.sessionProgress.textContent = "已完成";
    return;
  }
  const nextIndex = state.session.assignment.findIndex((assignment) => !assignment.completed);
  if (nextIndex === -1) {
    saveTaskState();
    window.clearInterval(state.timerInterval);
    setView("completion");
    elements.sessionProgress.textContent = "任务 6 / 6";
    return;
  }
  showTask(nextIndex);
}

async function checkHealth() {
  try {
    const health = await api("/api/health");
    state.modelReady = health.ollama.available && health.ollama.model_installed;
    elements.modelStatus.className = `status-pill ${state.modelReady ? "ready" : "error"}`;
    elements.modelStatus.textContent = state.modelReady
      ? `${health.model} 已就绪`
      : health.ollama.available
        ? `${health.model} 未安装`
        : "Ollama 未连接";
  } catch {
    state.modelReady = false;
    elements.modelStatus.className = "status-pill error";
    elements.modelStatus.textContent = "实验服务异常";
  }
  const count = Number(elements.draftCount.dataset.count || 0);
  if (!elements.workspaceView.hidden) updateDraftCount(count);
}

async function restoreSession() {
  const sessionId = localStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    setView("onboarding");
    return;
  }
  try {
    state.session = await api(`/api/session/${encodeURIComponent(sessionId)}`);
    routeSession();
  } catch {
    localStorage.removeItem(SESSION_KEY);
    state.session = null;
    setView("onboarding");
  }
}

elements.onboardingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.modelReady) {
    showToast("模型尚未就绪，请联系实验组织者。");
    return;
  }
  const button = elements.onboardingForm.querySelector("button[type='submit']");
  button.disabled = true;
  const form = new FormData(elements.onboardingForm);
  try {
    state.session = await api("/api/session", {
      method: "POST",
      body: JSON.stringify({
        consent: form.get("consent") === "on",
        profile: {
          experience: form.get("experience"),
          primary_domain: form.get("primary_domain"),
          ai_frequency: form.get("ai_frequency"),
        },
      }),
    });
    localStorage.setItem(SESSION_KEY, state.session.session_id);
    routeSession();
    await postEvent("session_started", {
      counterbalance_cell: state.session.counterbalance_cell,
    });
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

elements.generateButton.addEventListener("click", async () => {
  const assignment = currentAssignment();
  const prompt = elements.promptInput.value.trim();
  if (!prompt) {
    showToast("请输入要交给 AI 的请求。");
    return;
  }
  elements.generateButton.disabled = true;
  elements.generateStatus.className = "";
  elements.generateStatus.textContent = "生成中…";
  try {
    const result = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.session.session_id,
        task_id: assignment.task_id,
        prompt,
        prior_turns: state.chatTurns,
      }),
    });
    state.chatTurns.push(
      { role: "user", content: prompt },
      { role: "assistant", content: result.answer },
    );
    state.latestDraft = result.answer;
    elements.draftText.textContent = result.answer;
    elements.draftOutput.hidden = false;
    elements.generateStatus.textContent = `完成 · ${Math.round(result.latency_ms / 100) / 10}s`;
    elements.contextMetrics.textContent = `${result.context_messages} 条消息 · 约 ${result.estimated_tokens} tokens`;
    const count = Number(elements.draftCount.dataset.count || 0) + 1;
    updateDraftCount(count);
    saveTaskState();
  } catch (error) {
    elements.generateStatus.className = "error";
    elements.generateStatus.textContent = error.message;
    updateDraftCount(Number(elements.draftCount.dataset.count || 0));
  }
});

elements.useDraftButton.addEventListener("click", () => {
  if (!state.latestDraft) return;
  elements.finalAnswer.value = state.latestDraft;
  elements.answerCount.textContent = String(state.latestDraft.length);
  saveTaskState();
  elements.finalAnswer.focus();
  showToast("草稿已放入最终答案，可继续编辑。");
});

elements.finalAnswer.addEventListener("input", () => {
  elements.answerCount.textContent = String(elements.finalAnswer.value.length);
  saveTaskState();
  window.clearTimeout(state.answerEventTimer);
  state.answerEventTimer = window.setTimeout(() => {
    postEvent("answer_edited", { answer_chars: elements.finalAnswer.value.length });
  }, 1800);
});

for (const field of ["confidence", "workload", "usability", "trust"]) {
  const input = document.querySelector(`#${field}-rating`);
  const output = document.querySelector(`#${field}-output`);
  input.addEventListener("input", () => {
    output.value = input.value;
    saveTaskState();
  });
}

elements.resultForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const answer = elements.finalAnswer.value.trim();
  if (answer.length < 40) {
    showToast("最终答案至少需要 40 个字符。");
    elements.finalAnswer.focus();
    return;
  }
  const assignment = currentAssignment();
  const ratings = {};
  for (const field of ["confidence", "workload", "usability", "trust"]) {
    ratings[field] = Number(document.querySelector(`#${field}-rating`).value);
  }
  elements.submitTaskButton.disabled = true;
  try {
    await api("/api/task-result", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.session.session_id,
        task_id: assignment.task_id,
        final_answer: answer,
        ratings,
        elapsed_ms: Math.round(taskElapsedMs()),
        active_ms: Math.round(taskActiveMs()),
      }),
    });
    await postEvent("task_submitted", {
      answer_chars: answer.length,
      draft_count: Number(elements.draftCount.dataset.count || 0),
    });
    assignment.completed = true;
    localStorage.removeItem(storageKey(assignment.task_id));
    showToast("任务已提交。");
    routeSession();
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.submitTaskButton.disabled = false;
  }
});

elements.differenceRating.addEventListener("input", () => {
  elements.differenceOutput.value = elements.differenceRating.value;
});

elements.completionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(elements.completionForm);
  const button = elements.completionForm.querySelector("button[type='submit']");
  button.disabled = true;
  try {
    await api("/api/session-complete", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.session.session_id,
        preference: form.get("preference"),
        perceived_difference: Number(elements.differenceRating.value),
        comments: elements.completionComments.value,
      }),
    });
    state.session.completed = true;
    routeSession();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("visibilitychange", () => {
  if (!currentAssignment()) return;
  if (document.hidden) {
    if (state.timerActiveStarted) {
      state.timerBaseActive += performance.now() - state.timerActiveStarted;
      state.timerActiveStarted = 0;
    }
    saveTaskState();
    postEvent("page_hidden", { visibility: "hidden" });
  } else if (!state.timerActiveStarted) {
    state.timerActiveStarted = performance.now();
  }
});

window.addEventListener("beforeunload", saveTaskState);

Promise.all([checkHealth(), restoreSession()]).catch(() => {
  setView("onboarding");
});
