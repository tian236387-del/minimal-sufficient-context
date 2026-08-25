import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  BookOpen,
  Braces,
  Check,
  ChevronDown,
  ChevronRight,
  Columns2,
  Download,
  FileJson,
  GitBranch,
  GitFork,
  GitMerge,
  Network,
  ExternalLink,
  LoaderCircle,
  MessageSquarePlus,
  MoreHorizontal,
  PanelLeft,
  PanelRight,
  Pencil,
  RotateCcw,
  RefreshCw,
  Send,
  ShieldAlert,
  Square,
  Trash2,
  X,
} from "lucide-react";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function responseError(response) {
  let detail = response.statusText || `HTTP ${response.status}`;
  let payload = null;
  try {
    payload = await response.json();
    detail = payload.detail || JSON.stringify(payload);
  } catch {}
  const error = new Error(detail);
  error.status = response.status;
  error.payload = payload;
  return error;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

function parseSseBlock(block) {
  let eventName = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) eventName = line.slice(7);
    if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!dataLines.length) return null;
  return { eventName, payload: JSON.parse(dataLines.join("\n")) };
}

async function readEventStream(response, onEvent) {
  if (!response.body) throw new Error("浏览器未返回可读取的流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    buffer = buffer.replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseSseBlock(block);
      if (parsed) await onEvent(parsed.eventName, parsed.payload);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }

  if (buffer.trim()) {
    const parsed = parseSseBlock(buffer);
    if (parsed) await onEvent(parsed.eventName, parsed.payload);
  }
}

function buildChildren(messages) {
  const children = new Map();
  for (const message of messages) {
    const parentKey = message.parent_id ?? "root";
    if (!children.has(parentKey)) children.set(parentKey, []);
    children.get(parentKey).push(message);
  }
  return children;
}

function ancestorPath(messages, leafId) {
  if (!leafId) return [];
  const byId = new Map(messages.map((message) => [message.id, message]));
  const path = [];
  const seen = new Set();
  let current = byId.get(leafId);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    path.push(current);
    current = current.parent_id ? byId.get(current.parent_id) : null;
  }
  return path.reverse();
}

function branchHead(branch) {
  return branch?.head_message_id ?? branch?.forked_from_message_id ?? null;
}

function formatTokens(value) {
  return typeof value === "number" ? value.toLocaleString() : "-";
}

function userFacingError(error) {
  const message = error?.message || "发生了未知错误";
  if (error?.status === 503 || /cannot connect to ollama|ollama.*start/i.test(message)) {
    return "Ollama 未连接。请启动 Ollama，然后点击状态标记重试。";
  }
  if (error?.status === 504 || /timed out|timeout/i.test(message)) {
    return "模型响应超时。可以稍后重试，或暂时换用更小的模型。";
  }
  if (error?.status === 422 && /token budget|预算|prompt/i.test(message)) {
    return "当前问题超过 Token 预算。请提高 Budget，或缩短问题。";
  }
  if (error?.status === 409 && /stale|preview/i.test(message)) {
    return "上下文已经变化，请重新运行一次 Preview。";
  }
  return message;
}

function TreeNode({
  node,
  childrenMap,
  selectedId,
  activePathIds,
  onSelect,
  onFork,
  depth = 0,
}) {
  const [expanded, setExpanded] = useState(true);
  const children = childrenMap.get(node.id) || [];
  const selected = selectedId === node.id;
  const active = activePathIds.has(node.id);

  return (
    <div className="tree-branch">
      <div
        className={`tree-node ${selected ? "selected" : ""} ${active ? "on-path" : ""}`}
        style={{ paddingLeft: `${6 + depth * 13}px` }}
      >
        <button
          className="tree-toggle icon-button"
          onClick={() => setExpanded((value) => !value)}
          aria-label={expanded ? "折叠节点" : "展开节点"}
          disabled={!children.length}
        >
          {children.length ? (
            expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />
          ) : (
            <span className="tree-dot" />
          )}
        </button>
        <button className="tree-main" onClick={() => onSelect(node.id)}>
          <span className={`role-mark ${node.role}`}>
            {node.role === "user" ? "U" : "A"}
          </span>
          <span className="tree-copy">{node.content}</span>
        </button>
        <button
          className="icon-button tree-action"
          onClick={() => onFork(node.id)}
          title="从此消息创建命名分支"
          aria-label="创建分支"
        >
          <GitFork size={13} />
        </button>
      </div>
      {expanded &&
        children.map((child) => (
          <TreeNode
            key={child.id}
            node={child}
            childrenMap={childrenMap}
            selectedId={selectedId}
            activePathIds={activePathIds}
            onSelect={onSelect}
            onFork={onFork}
            depth={depth + 1}
          />
        ))}
    </div>
  );
}

function ConversationTree(props) {
  const childrenMap = useMemo(() => buildChildren(props.messages), [props.messages]);
  const roots = childrenMap.get("root") || [];
  if (!roots.length) return <div className="empty-note">发送第一条消息后，这里会生成对话树。</div>;
  return (
    <div className="tree-scroll">
      {roots.map((root) => (
        <TreeNode key={root.id} node={root} childrenMap={childrenMap} {...props} />
      ))}
    </div>
  );
}

function ProviderStatus({ available, loading, model, onRetry }) {
  const state = loading ? "checking" : available ? "online" : "offline";
  const label = loading ? "检查模型服务" : available ? `Ollama · ${model}` : "Ollama 未连接";
  return (
    <div className={`provider-status ${state}`} title={available ? `当前模型：${model}` : "点击重试模型服务连接"}>
      <span className="provider-dot" />
      <span className="provider-label">{label}</span>
      {!loading && !available && (
        <button className="provider-retry" onClick={onRetry} aria-label="重试模型服务" title="重试模型服务">
          <RefreshCw size={12} />
        </button>
      )}
    </div>
  );
}

const STARTER_PROMPTS = [
  "比较 PostgreSQL 和 SQLite，给出当前项目的选择建议",
  "帮我定位这个 API 设计里最可能的并发问题",
  "把这组研究资料整理成结论、证据和待确认问题",
];

function StarterPanel({ hasConversation, onCreate, onPrompt, onDismiss }) {
  return (
    <section className="starter-panel">
      <div className="starter-header">
        <div>
          <span className="starter-kicker">FIRST SESSION</span>
          <strong>{hasConversation ? "从一个具体问题开始" : "先试一次分支对比"}</strong>
        </div>
        {onDismiss && (
          <button className="icon-button" onClick={onDismiss} aria-label="关闭首次提示" title="关闭首次提示">
            <X size={14} />
          </button>
        )}
      </div>
      <p>
        {hasConversation
          ? "先得到一个答案，再从任意消息 Fork 出另一条思路。"
          : "同一个问题可以沿不同思路继续，互不污染上下文。"}
      </p>
      <div className="starter-prompts">
        {STARTER_PROMPTS.map((prompt) => (
          <button className="starter-prompt" key={prompt} onClick={() => (hasConversation ? onPrompt(prompt) : onCreate(prompt))}>
            {prompt}
          </button>
        ))}
      </div>
      {!hasConversation && (
        <button className="button primary starter-create" onClick={() => onCreate("")}>
          <MessageSquarePlus size={15} /> 新建空白对话
        </button>
      )}
    </section>
  );
}

function BranchDiscovery({ onFork, onDismiss }) {
  return (
    <div className="branch-discovery">
      <GitFork size={14} />
      <span>想试另一条方案？从当前消息创建一个命名分支。</span>
      <button className="text-command" onClick={onFork}>试试 Fork</button>
      <button className="icon-button" onClick={onDismiss} aria-label="关闭提示" title="关闭提示"><X size={13} /></button>
    </div>
  );
}

function IdList({ ids, empty = "无" }) {
  if (!ids?.length) return <span className="muted-inline">{empty}</span>;
  return (
    <div className="id-list">
      {ids.map((id) => (
        <span className="id-chip" key={id}>#{id}</span>
      ))}
    </div>
  );
}

function ContextDiff({ diff }) {
  if (!diff) return <div className="empty-note compact">尚无 Context Diff。</div>;
  return (
    <section className="inspector-section">
      <div className="section-heading">Context Diff</div>
      <div className="diff-row">
        <span>Linear 独有</span>
        <IdList ids={diff.linear_only_message_ids} />
      </div>
      <div className="diff-row">
        <span>Branch 独有</span>
        <IdList ids={diff.branch_only_message_ids} />
      </div>
      <div className="diff-row">
        <span>共同上下文</span>
        <IdList ids={diff.shared_message_ids} />
      </div>
      <div className="diff-row">
        <span>Linear 裁剪</span>
        <IdList ids={diff.linear_truncated_message_ids} />
      </div>
      <div className="diff-row">
        <span>Branch 裁剪</span>
        <IdList ids={diff.branch_truncated_message_ids} />
      </div>
    </section>
  );
}

function ContextInspector({ contextInfo, liveContext, activeBranch, onOpenMessage }) {
  const branchContext = liveContext || contextInfo?.branch_context;
  if (!branchContext && !contextInfo) {
    return (
      <section className="context-empty">
        <GitBranch size={22} />
        <strong>Context 会在第一条回答后显示</strong>
        <span>这里会说明当前分支包含了什么，以及哪些 sibling 被排除。</span>
      </section>
    );
  }

  const budget = branchContext?.token_budget || contextInfo?.token_budget || 0;
  const estimate = branchContext?.estimated_tokens || contextInfo?.estimated_prompt_tokens || 0;
  const usage = budget ? Math.min(100, Math.round((estimate / budget) * 100)) : 0;
  const includedCount = branchContext?.included_message_count ?? contextInfo?.active_message_count ?? 0;
  const truncatedCount = branchContext?.truncated_message_count ?? contextInfo?.truncated_path?.length ?? 0;
  const excludedCount = contextInfo?.excluded_message_count ?? 0;
  const actualTokens = contextInfo?.last_prompt_tokens;

  const messageRow = (message, Icon, className = "") => {
    const content = (
      <>
        <Icon size={13} />
        <span>#{message.id}</span>
        <span className={`mini-role ${message.role}`}>{message.role}</span>
        <span className="context-copy">{message.content}</span>
      </>
    );
    if (!onOpenMessage) return <div className={`context-row ${className}`} key={message.id}>{content}</div>;
    return (
      <button className={`context-row context-row-button ${className}`} key={message.id} onClick={() => onOpenMessage(message.id)} title="打开原始消息">
        {content}
      </button>
    );
  };

  return (
    <>
      <section className="context-explainer">
        <div className="context-explainer-heading">
          <GitBranch size={15} />
          <div>
            <strong>{activeBranch?.name || contextInfo?.active_branch_name || "当前分支"}</strong>
            <span>只沿当前消息路径编译回答</span>
          </div>
        </div>
        <div className="context-stats">
          <span><b>{includedCount}</b> 条纳入</span>
          <span><b>{excludedCount}</b> 条 sibling 排除</span>
          <span><b>{truncatedCount}</b> 条预算裁剪</span>
        </div>
      </section>

      <section className="budget-overview">
        <div className="budget-line">
          <span>本次 Branch 上下文</span>
          <strong>{formatTokens(estimate)} / {formatTokens(budget)}</strong>
        </div>
        <div className="usage-track"><span style={{ width: `${usage}%` }} /></div>
        <div className="comparison-metrics">
          <span>Linear {formatTokens(contextInfo?.linear_context?.estimated_tokens)}</span>
          <span>Branch 节省 {formatTokens(contextInfo?.context_diff?.estimated_tokens_saved)}</span>
          <span>实际 {formatTokens(actualTokens)} tokens</span>
        </div>
      </section>

      <ContextDiff diff={contextInfo?.context_diff} />

      <section className="inspector-section context-source-section">
        <div className="section-heading">回答来源</div>
        <div className="context-rule"><Check size={13} /> 当前分支祖先消息</div>
        <div className="context-rule"><X size={13} /> 其他 sibling 分支</div>
        <div className="context-rule"><BookOpen size={13} /> 已注入摘要 {branchContext?.summary_ids?.length ? `#${branchContext.summary_ids.join(", #")}` : "无"}</div>
      </section>

      <section className="inspector-section">
        <div className="section-heading">Included Path · 点击查看原文</div>
        <div className="context-row system-row"><Check size={13} /> System prompt</div>
        {(contextInfo?.active_path || []).map((message) => messageRow(message, Check))}
        {!contextInfo?.active_path?.length && <div className="empty-note compact">当前分支还没有可展开的消息。</div>}
      </section>

      {!!contextInfo?.truncated_path?.length && (
        <section className="inspector-section warning-section">
          <div className="section-heading">Budget Truncated</div>
          {contextInfo.truncated_path.map((message) => messageRow(message, AlertTriangle))}
        </section>
      )}

      <section className="inspector-section">
        <div className="section-heading">Excluded Siblings</div>
        {(contextInfo?.excluded_siblings || []).map((message) => messageRow(message, X, "excluded"))}
        {!contextInfo?.excluded_siblings?.length && <div className="empty-note compact">没有 sibling 消息。</div>}
      </section>
    </>
  );
}

function ComparisonInspector({ comparison, loading }) {
  if (loading) {
    return <div className="inspector-loading"><LoaderCircle className="spin" size={20} /> 正在运行两组上下文</div>;
  }
  if (!comparison) {
    return <div className="empty-note inspector-empty">在输入框写好同一个问题，然后点击 A/B 对比。</div>;
  }
  return (
    <>
      <div className="comparison-question">{comparison.question}</div>
      <section className="answer-section linear-answer">
        <div className="answer-heading">
          <span>Linear</span>
          <small>估算 {formatTokens(comparison.linear.context.estimated_tokens)} · 实际 {formatTokens(comparison.linear.prompt_tokens)}</small>
        </div>
        <div className="answer-copy">{comparison.linear.answer}</div>
      </section>
      <section className="answer-section branch-answer">
        <div className="answer-heading">
          <span>Branch</span>
          <small>估算 {formatTokens(comparison.branch.context.estimated_tokens)} · 实际 {formatTokens(comparison.branch.prompt_tokens)}</small>
        </div>
        <div className="answer-copy">{comparison.branch.answer}</div>
      </section>
      <ContextDiff diff={comparison.context_diff} />
      <div className="non-persisted-note">本次对比未写入对话历史</div>
    </>
  );
}

function CitationList({ sources, onOpenSource }) {
  if (!sources?.length) return <span className="muted-inline">No citations</span>;
  return (
    <div className="citation-list">
      {sources.map((source) => (
        <button
          className="citation-chip"
          key={`${source.message_id}-${source.source_order}`}
          onClick={() => onOpenSource(source.message_id)}
          title="Open original message"
        >
          <ExternalLink size={10} /> {source.citation}
        </button>
      ))}
    </div>
  );
}

function SummaryInspector({ summaries, onCreate, onOpenSource, busy }) {
  return (
    <>
      <section className="knowledge-toolbar">
        <div>
          <div className="section-heading">Citable summaries</div>
          <div className="knowledge-note">Every summary keeps its original evidence.</div>
        </div>
        <button
          className="icon-button"
          onClick={onCreate}
          disabled={busy}
          title="Create summary for active branch"
          aria-label="Create summary"
        >
          {busy ? <LoaderCircle className="spin" size={15} /> : <BookOpen size={15} />}
        </button>
      </section>
      {!summaries?.length && <div className="empty-note compact">No summaries yet.</div>}
      {(summaries || []).map((summary) => (
        <section className="knowledge-item" key={summary.id}>
          <div className="knowledge-item-heading">
            <strong>{summary.title}</strong>
            <span>v{summary.version} · {summary.citation_count} citations</span>
          </div>
          <div className="knowledge-copy">{summary.content}</div>
          <CitationList sources={summary.sources} onOpenSource={onOpenSource} />
          {!summary.is_citable && (
            <div className="knowledge-warning"><ShieldAlert size={12} /> Original evidence is unavailable.</div>
          )}
        </section>
      ))}
    </>
  );
}

function MergeInspector({
  branches,
  summaries,
  targetId,
  sourceId,
  onTargetChange,
  onSourceChange,
  onPreview,
  preview,
  resolutions,
  onResolutionChange,
  onExecute,
  merges,
  onRollback,
  onOpenSource,
  busy,
}) {
  const targetBranch = (branches || []).find((branch) => branch.id === Number(targetId));
  const sourceBranch = (branches || []).find((branch) => branch.id === Number(sourceId));
  const targetSummaries = (summaries || []).filter(
    (summary) => summary.branch_id === Number(targetId)
      && summary.anchor_message_id === branchHead(targetBranch)
      && (targetBranch?.path_message_ids || []).every((id) => summary.source_message_ids.includes(id))
      && summary.is_citable
  ).slice(0, 1);
  const sourceSummaries = (summaries || []).filter(
    (summary) => summary.branch_id === Number(sourceId)
      && summary.anchor_message_id === branchHead(sourceBranch)
      && (sourceBranch?.path_message_ids || []).every((id) => summary.source_message_ids.includes(id))
      && summary.is_citable
  ).slice(0, 1);
  const unresolved = (preview?.conflicts || []).filter(
    (conflict) => !resolutions[conflict.key]
  );
  const blockers = preview?.blockers || [];
  const canExecute = Boolean(preview) && !blockers.length && !unresolved.length;

  return (
    <>
      <section className="knowledge-toolbar">
        <div>
          <div className="section-heading">Explicit merge</div>
          <div className="knowledge-note">Preview first. Raw messages remain untouched.</div>
        </div>
        <GitMerge size={16} className="knowledge-icon" />
      </section>
      <section className="inspector-section merge-controls">
        <label className="field-label" htmlFor="merge-target">Target branch</label>
        <select id="merge-target" className="select-control wide" value={targetId || ""} onChange={(event) => onTargetChange(event.target.value)}>
          <option value="">Choose target</option>
          {(branches || []).map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
        </select>
        <label className="field-label" htmlFor="merge-source">Source branch</label>
        <select id="merge-source" className="select-control wide" value={sourceId || ""} onChange={(event) => onSourceChange(event.target.value)}>
          <option value="">Choose source</option>
          {(branches || []).map((branch) => <option key={branch.id} value={branch.id}>{branch.name}</option>)}
        </select>
        <button className="button ghost full" onClick={() => onPreview(targetSummaries, sourceSummaries)} disabled={busy || !targetId || !sourceId || targetId === sourceId}>
          {busy ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} Preview conflicts
        </button>
      </section>
      {preview && (
        <section className="inspector-section merge-preview">
          <div className="section-heading">Merge preview</div>
          <div className={`merge-verdict ${preview.has_conflicts || blockers.length ? "warning" : "ready"}`}>
            {preview.has_conflicts || blockers.length ? <ShieldAlert size={13} /> : <Check size={13} />}
            <span>{blockers.length ? `${blockers.length} current summary requirement(s)` : preview.has_conflicts ? `${preview.conflicts.length} conflict(s) need a decision` : "No detected conflicts"}</span>
          </div>
          {blockers.map((blocker) => (
            <div className="knowledge-warning" key={blocker.code}>
              <ShieldAlert size={12} /> {blocker.detail}
            </div>
          ))}
          {(preview.conflicts || []).map((conflict) => (
            <div className="conflict-item" key={conflict.key}>
              <div className="conflict-heading"><strong>{conflict.subject}</strong><span>{conflict.severity}</span></div>
              <div className="conflict-values">
                <span>Target: {conflict.target_values.join(" / ")}</span>
                <span>Source: {conflict.source_values.join(" / ")}</span>
              </div>
              <select className="select-control wide" value={resolutions[conflict.key] || ""} onChange={(event) => onResolutionChange(conflict.key, event.target.value)}>
                <option value="">Choose resolution</option>
                <option value="target">Keep target</option>
                <option value="source">Keep source</option>
                {Array.from(new Set([...conflict.target_values, ...conflict.source_values])).map((value) => <option key={value} value={value}>{value}</option>)}
                <option value="ignore">Keep both as evidence</option>
              </select>
              <CitationList
                sources={[
                  ...conflict.target_source_message_ids.map((message_id) => ({ message_id, source_order: `target-${message_id}`, citation: `[m:${message_id}]` })),
                  ...conflict.source_source_message_ids.map((message_id) => ({ message_id, source_order: `source-${message_id}`, citation: `[m:${message_id}]` })),
                ]}
                onOpenSource={onOpenSource}
              />
            </div>
          ))}
          <button className="button primary full" onClick={onExecute} disabled={busy || !canExecute}>
            {busy ? <LoaderCircle className="spin" size={14} /> : <GitMerge size={14} />} Create reversible merge
          </button>
          {!canExecute && <div className="knowledge-note">Complete summaries and resolve every conflict before merging.</div>}
        </section>
      )}
      <section className="inspector-section">
        <div className="section-heading">Merge history</div>
        {!merges?.length && <div className="empty-note compact">No merge operations.</div>}
        {(merges || []).map((merge) => (
          <div className="history-item" key={merge.id}>
            <div><strong>v{merge.version} · {merge.result_branch_name}</strong><span>{merge.status}</span></div>
            {merge.status === "completed" && <button className="icon-button" onClick={() => onRollback(merge.id)} title="Rollback merge" aria-label="Rollback merge"><RotateCcw size={13} /></button>}
          </div>
        ))}
      </section>
    </>
  );
}

function DagInspector({ dag }) {
  if (!dag) return <div className="empty-note inspector-empty">No DAG loaded.</div>;
  return (
    <>
      <section className="knowledge-toolbar">
        <div>
          <div className="section-heading">DAG / version history</div>
          <div className="knowledge-note">Derived relationships; no raw message rewriting.</div>
        </div>
        <span className={`dag-health ${dag.is_acyclic ? "ready" : "warning"}`}><Network size={13} /> {dag.is_acyclic ? "Acyclic" : "Cycle detected"}</span>
      </section>
      <section className="inspector-section dag-node-list">
        {(dag.nodes || []).map((node) => (
          <div className={`dag-node ${node.type}`} key={node.id}>
            <span className="dag-node-type">{node.type}</span>
            <span className="dag-node-label">{node.label}</span>
            {node.type === "summary" && <small>{node.citation_count} citations</small>}
            {node.type === "merge" && <small>v{node.version} · {node.status}</small>}
          </div>
        ))}
      </section>
      <section className="inspector-section">
        <div className="section-heading">Relationships</div>
        {(dag.edges || []).map((edge) => (
          <div className="dag-edge" key={edge.id}><span>{edge.source}</span><GitMerge size={11} /><span>{edge.target}</span><small>{edge.relation}</small></div>
        ))}
      </section>
    </>
  );
}

function ForkDialog({ state, onChange, onClose, onSubmit, busy }) {
  if (!state) return null;
  return (
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <form className="dialog" onSubmit={onSubmit} onMouseDown={(event) => event.stopPropagation()}>
        <div className="dialog-header">
          <div>
            <strong>创建分支</strong>
            <span>从消息 #{state.messageId} 开始</span>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X size={16} /></button>
        </div>
        <label className="field-label" htmlFor="branch-name">分支名称</label>
        <input
          id="branch-name"
          className="text-input"
          value={state.name}
          onChange={(event) => onChange({ ...state, name: event.target.value })}
          autoFocus
          maxLength={120}
        />
        <div className="dialog-actions">
          <button type="button" className="button ghost" onClick={onClose}>取消</button>
          <button className="button primary" disabled={busy || !state.name.trim()}>
            {busy ? <LoaderCircle className="spin" size={15} /> : <GitBranch size={15} />}
            创建
          </button>
        </div>
      </form>
    </div>
  );
}

function App() {
  const [conversations, setConversations] = useState([]);
  const [conversation, setConversation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [branches, setBranches] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [models, setModels] = useState(["qwen3:4b"]);
  const [providerAvailable, setProviderAvailable] = useState(null);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [selectedModel, setSelectedModel] = useState("qwen3:4b");
  const [budgetDraft, setBudgetDraft] = useState("8192");
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pendingUser, setPendingUser] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [liveContext, setLiveContext] = useState(null);
  const [contextInfo, setContextInfo] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [summaries, setSummaries] = useState([]);
  const [dag, setDag] = useState(null);
  const [merges, setMerges] = useState([]);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [mergePreview, setMergePreview] = useState(null);
  const [mergeResolutions, setMergeResolutions] = useState({});
  const [mergeTargetId, setMergeTargetId] = useState("");
  const [mergeSourceId, setMergeSourceId] = useState("");
  const [mergeBusy, setMergeBusy] = useState(false);
  const [inspectorTab, setInspectorTab] = useState("context");
  const [inspectorOpen, setInspectorOpen] = useState(
    () => window.innerWidth > 940
  );
  const [advancedToolsOpen, setAdvancedToolsOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [error, setError] = useState("");
  const [welcomeVisible, setWelcomeVisible] = useState(() => {
    try {
      return window.localStorage.getItem("msc-v021-welcome-dismissed") !== "1";
    } catch {
      return true;
    }
  });
  const [branchHintVisible, setBranchHintVisible] = useState(true);
  const [forkDialog, setForkDialog] = useState(null);
  const [forkBusy, setForkBusy] = useState(false);
  const [renamingBranchId, setRenamingBranchId] = useState(null);
  const [renameDraft, setRenameDraft] = useState("");
  const abortControllerRef = useRef(null);

  const activeBranch = useMemo(
    () => branches.find((branch) => branch.id === conversation?.active_branch_id) || null,
    [branches, conversation]
  );
  const activePathIds = useMemo(
    () => new Set(activeBranch?.path_message_ids || []),
    [activeBranch]
  );
  const selectedPath = useMemo(
    () => ancestorPath(messages, selectedId),
    [messages, selectedId]
  );
  const expectedHead = branchHead(activeBranch);
  const historicalSelection = selectedId !== expectedHead;
  const showBranchDiscovery = Boolean(
    conversation &&
    messages.length >= 2 &&
    branches.length === 1 &&
    branchHintVisible &&
    !streaming
  );

  function dismissWelcome() {
    setWelcomeVisible(false);
    try {
      window.localStorage.setItem("msc-v021-welcome-dismissed", "1");
    } catch {
      // Local storage is optional; the in-memory dismissal still works.
    }
  }

  function chooseInspectorTab(tab) {
    setInspectorTab(tab);
    setAdvancedToolsOpen(false);
  }

  function focusComposer() {
    window.setTimeout(() => document.getElementById("composer")?.focus(), 40);
  }

  function titleFromPrompt(content) {
    const compact = content.replace(/\s+/g, " ").trim();
    if (!compact) return "新对话";
    return compact.length > 28 ? `${compact.slice(0, 28).trim()}…` : compact;
  }

  async function refreshModels(silent = false) {
    setModelsLoading(true);
    try {
      const data = await api("/api/models");
      const availableModels = data.models?.length ? data.models : ["qwen3:4b"];
      setModels(availableModels);
      setProviderAvailable(Boolean(data.provider_available));
      if (!availableModels.includes(selectedModel)) setSelectedModel(availableModels[0]);
      return data;
    } catch (requestError) {
      setProviderAvailable(false);
      if (!silent) setError(userFacingError(requestError));
      return { models: ["qwen3:4b"], provider_available: false };
    } finally {
      setModelsLoading(false);
    }
  }

  function applyConversationData(data, preferredMessageId) {
    setConversation(data.conversation);
    setMessages(data.messages || []);
    setBranches(data.branches || []);
    setSelectedModel(data.conversation.model || "qwen3:4b");
    setBudgetDraft(String(data.conversation.token_budget || 8192));
    const branch = (data.branches || []).find(
      (item) => item.id === data.conversation.active_branch_id
    );
    const nextMessageId =
      preferredMessageId ?? data.conversation.active_message_id ?? branchHead(branch);
    setSelectedId(nextMessageId ?? null);
    return nextMessageId ?? null;
  }

  async function refreshConversations() {
    const data = await api("/api/conversations");
    setConversations(data.conversations || []);
    return data.conversations || [];
  }

  async function refreshKnowledge(conversationId, branchList = [], activeBranchId = null) {
    const [summaryData, mergeData, dagData] = await Promise.all([
      api(`/api/conversations/${conversationId}/summaries`),
      api(`/api/conversations/${conversationId}/merges`),
      api(`/api/conversations/${conversationId}/dag`),
    ]);
    const nextSummaries = summaryData.summaries || [];
    const nextBranches = branchList.length ? branchList : branches;
    const target = nextBranches.find((branch) => branch.id === activeBranchId) || nextBranches[0];
    const source = nextBranches.find((branch) => branch.id !== target?.id);
    setSummaries(nextSummaries);
    setMerges(mergeData.merges || []);
    setDag(dagData);
    setMergeTargetId((current) => nextBranches.some((branch) => String(branch.id) === String(current)) ? current : String(target?.id || ""));
    setMergeSourceId((current) => nextBranches.some((branch) => String(branch.id) === String(current) && branch.id !== target?.id) ? current : String(source?.id || ""));
    setMergePreview(null);
    setMergeResolutions({});
  }

  async function inspectContext(messageId) {
    if (!messageId) {
      setContextInfo(null);
      return;
    }
    const data = await api(`/api/messages/${messageId}/context`);
    setContextInfo(data);
  }

  async function loadConversation(conversationId, preferredMessageId) {
    const data = await api(`/api/conversations/${conversationId}`);
    const nextMessageId = applyConversationData(data, preferredMessageId);
    setComparison(null);
    setLiveContext(null);
    const knowledgePromise = refreshKnowledge(
      conversationId,
      data.branches || [],
      data.conversation.active_branch_id,
    );
    if (nextMessageId) await Promise.all([inspectContext(nextMessageId), knowledgePromise]);
    else {
      setContextInfo(null);
      await knowledgePromise;
    }
    return data;
  }

  async function createActiveSummary() {
    if (!conversation || !activeBranch || summaryBusy) return;
    setSummaryBusy(true);
    setError("");
    try {
      await api(`/api/branches/${activeBranch.id}/summaries`, {
        method: "POST",
        body: JSON.stringify({ anchor_message_id: selectedId || branchHead(activeBranch) }),
      });
      await refreshKnowledge(conversation.id, branches, conversation.active_branch_id);
      chooseInspectorTab("summary");
    } catch (requestError) {
      setError(userFacingError(requestError));
    } finally {
      setSummaryBusy(false);
    }
  }

  async function openOriginalMessage(messageId) {
    try {
      await selectNode(messageId);
      chooseInspectorTab("context");
      setInspectorOpen(true);
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function previewMerge(targetSummaryList, sourceSummaryList) {
    if (!conversation || !mergeTargetId || !mergeSourceId || mergeTargetId === mergeSourceId || mergeBusy) return;
    setMergeBusy(true);
    setError("");
    try {
      const result = await api(`/api/conversations/${conversation.id}/merges/preview`, {
        method: "POST",
        body: JSON.stringify({
          target_branch_id: Number(mergeTargetId),
          source_branch_id: Number(mergeSourceId),
          target_summary_ids: targetSummaryList.map((summary) => summary.id),
          source_summary_ids: sourceSummaryList.map((summary) => summary.id),
        }),
      });
      setMergePreview(result);
      setMergeResolutions({});
      chooseInspectorTab("merge");
    } catch (requestError) {
      setError(userFacingError(requestError));
    } finally {
      setMergeBusy(false);
    }
  }

  async function executePreviewedMerge() {
    if (!conversation || !mergePreview || mergeBusy) return;
    if (!window.confirm("创建新的可回滚 Merge 分支？原始消息不会被修改。")) return;
    setMergeBusy(true);
    setError("");
    try {
      await api(`/api/conversations/${conversation.id}/merges`, {
        method: "POST",
        body: JSON.stringify({
          target_branch_id: Number(mergePreview.target_branch_id),
          source_branch_id: Number(mergePreview.source_branch_id),
          target_summary_ids: mergePreview.target_summary_ids,
          source_summary_ids: mergePreview.source_summary_ids,
          preview_token: mergePreview.preview_token,
          resolutions: mergeResolutions,
          activate: false,
        }),
      });
      await loadConversation(conversation.id);
      chooseInspectorTab("merge");
    } catch (requestError) {
      setError(userFacingError(requestError));
    } finally {
      setMergeBusy(false);
    }
  }

  async function rollbackMerge(mergeId) {
    if (!conversation || mergeBusy || !window.confirm("Rollback this derived merge? Original messages will remain.")) return;
    setMergeBusy(true);
    setError("");
    try {
      await api(`/api/merges/${mergeId}/rollback`, {
        method: "POST",
        body: JSON.stringify({ reason: "User requested rollback" }),
      });
      await loadConversation(conversation.id);
      chooseInspectorTab("merge");
    } catch (requestError) {
      setError(userFacingError(requestError));
    } finally {
      setMergeBusy(false);
    }
  }

  useEffect(() => {
    (async () => {
      try {
        const [conversationData] = await Promise.all([
          api("/api/conversations"),
          refreshModels(true),
        ]);
        setConversations(conversationData.conversations || []);
        if (conversationData.conversations?.length) {
          await loadConversation(conversationData.conversations[0].id);
        }
      } catch (requestError) {
        setError(userFacingError(requestError));
      }
    })();
  }, []);

  async function createConversation(initialPrompt = "") {
    setError("");
    try {
      const created = await api("/api/conversations", {
        method: "POST",
        body: JSON.stringify({
          title: initialPrompt ? titleFromPrompt(initialPrompt) : "新对话",
          model: selectedModel,
          token_budget: 8192,
        }),
      });
      await refreshConversations();
      await loadConversation(created.id);
      setMobileNavOpen(false);
      if (initialPrompt) {
        setDraft(initialPrompt);
        focusComposer();
      }
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function deleteConversation() {
    if (!conversation || !window.confirm(`删除「${conversation.title}」及其全部分支？`)) return;
    try {
      await api(`/api/conversations/${conversation.id}`, { method: "DELETE" });
      const remaining = await refreshConversations();
      if (remaining.length) await loadConversation(remaining[0].id);
      else {
        setConversation(null);
        setMessages([]);
        setBranches([]);
        setSelectedId(null);
        setContextInfo(null);
        setSummaries([]);
        setMerges([]);
        setDag(null);
        setMergePreview(null);
        setMergeResolutions({});
      }
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function chooseConversation(conversationId) {
    if (streaming) return;
    try {
      await loadConversation(conversationId);
      setMobileNavOpen(false);
      setError("");
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function selectBranch(branch) {
    if (!conversation || streaming) return;
    const activeMessageId = branchHead(branch);
    try {
      const data = await api(`/api/conversations/${conversation.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          active_branch_id: branch.id,
          active_message_id: activeMessageId,
        }),
      });
      applyConversationData(data, activeMessageId);
      if (activeMessageId) await inspectContext(activeMessageId);
      else setContextInfo(null);
      setError("");
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function selectNode(messageId) {
    if (!conversation || streaming) return;
    const currentBranch = branches.find((branch) => branch.id === conversation.active_branch_id);
    const targetBranch = currentBranch?.path_message_ids.includes(messageId)
      ? currentBranch
      : branches.find((branch) => branch.path_message_ids.includes(messageId));
    if (!targetBranch) {
      setError("找不到包含该消息的分支");
      return;
    }
    try {
      const data = await api(`/api/conversations/${conversation.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          active_branch_id: targetBranch.id,
          active_message_id: messageId,
        }),
      });
      applyConversationData(data, messageId);
      await inspectContext(messageId);
      setError("");
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  function openForkDialog(messageId) {
    setForkDialog({ messageId, name: `Branch ${branches.length + 1}` });
  }

  async function createFork(event) {
    event.preventDefault();
    if (!conversation || !forkDialog?.name.trim()) return;
    setForkBusy(true);
    try {
      const created = await api(`/api/conversations/${conversation.id}/branches`, {
        method: "POST",
        body: JSON.stringify({
          name: forkDialog.name.trim(),
          forked_from_message_id: forkDialog.messageId,
        }),
      });
      setForkDialog(null);
      await loadConversation(conversation.id, created.forked_from_message_id);
      await refreshConversations();
      setTimeout(() => document.getElementById("composer")?.focus(), 40);
    } catch (requestError) {
      setError(userFacingError(requestError));
    } finally {
      setForkBusy(false);
    }
  }

  function beginRename(branch) {
    setRenamingBranchId(branch.id);
    setRenameDraft(branch.name);
  }

  async function saveRename(branchId) {
    if (!renameDraft.trim()) return;
    try {
      const updated = await api(`/api/branches/${branchId}`, {
        method: "PATCH",
        body: JSON.stringify({ name: renameDraft.trim() }),
      });
      setBranches((current) => current.map((branch) => (branch.id === branchId ? updated : branch)));
      setRenamingBranchId(null);
      await refreshConversations();
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function deleteBranch(branch) {
    if (!conversation || !window.confirm(`删除分支「${branch.name}」及其独有消息？`)) return;
    try {
      await api(`/api/branches/${branch.id}`, { method: "DELETE" });
      await loadConversation(conversation.id);
      await refreshConversations();
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function downloadBranch(branch, format) {
    try {
      const response = await fetch(`${API_BASE}/api/branches/${branch.id}/export?format=${format}`);
      if (!response.ok) throw await responseError(response);
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `branch-${branch.id}`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      setError(userFacingError(requestError));
    }
  }

  async function saveBudget() {
    if (!conversation) return;
    const tokenBudget = Number(budgetDraft);
    if (!Number.isInteger(tokenBudget) || tokenBudget < 256 || tokenBudget > 262144) {
      setError("Token 预算必须是 256 到 262144 之间的整数");
      setBudgetDraft(String(conversation.token_budget));
      return;
    }
    if (tokenBudget === conversation.token_budget) return;
    try {
      const data = await api(`/api/conversations/${conversation.id}`, {
        method: "PATCH",
        body: JSON.stringify({ token_budget: tokenBudget }),
      });
      const activeMessageId = applyConversationData(data, selectedId);
      if (activeMessageId) await inspectContext(activeMessageId);
      setError("");
    } catch (requestError) {
      setError(userFacingError(requestError));
      setBudgetDraft(String(conversation.token_budget));
    }
  }

  async function sendMessage() {
    const content = draft.trim();
    if (!content || !conversation || !activeBranch || streaming) return;
    if (historicalSelection) {
      openForkDialog(selectedId);
      return;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    setStreaming(true);
    setPendingUser(content);
    setStreamingText("");
    setLiveContext(null);
    setDraft("");
    setError("");
    let donePayload = null;
    let receivedText = "";

    try {
      const response = await fetch(`${API_BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          conversation_id: conversation.id,
          branch_id: activeBranch.id,
          parent_id: selectedId,
          content,
          model: selectedModel,
        }),
      });
      if (!response.ok) throw await responseError(response);
      await readEventStream(response, async (eventName, payload) => {
        if (eventName === "context") setLiveContext(payload.context);
        if (eventName === "delta") {
          receivedText += payload.content;
          setStreamingText(receivedText);
        }
        if (eventName === "done") donePayload = payload;
        if (eventName === "error") throw new Error(payload.detail || "流式生成失败");
      });
      if (!donePayload) throw new Error("流式响应未正常结束");
      await loadConversation(conversation.id, donePayload.assistant_message.id);
      await refreshConversations();
    } catch (requestError) {
      if (requestError.name === "AbortError") setError("已停止生成，未写入对话历史");
      else setError(userFacingError(requestError));
      setDraft(content);
    } finally {
      abortControllerRef.current = null;
      setStreaming(false);
      setPendingUser("");
      setStreamingText("");
      setLiveContext(null);
    }
  }

  function stopStreaming() {
    abortControllerRef.current?.abort();
  }

  async function runComparison() {
    const content = draft.trim();
    if (!content || !conversation || comparisonLoading) return;
    setComparisonLoading(true);
    setComparison(null);
    chooseInspectorTab("compare");
    setInspectorOpen(true);
    setError("");
    try {
      const result = await api("/api/context/compare", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: conversation.id,
          branch_id: activeBranch?.id,
          parent_id: selectedId,
          content,
          model: selectedModel,
          token_budget: Number(budgetDraft),
        }),
      });
      setComparison(result);
    } catch (requestError) {
      setError(userFacingError(requestError));
    } finally {
      setComparisonLoading(false);
    }
  }

  return (
    <div id="workspace-view" className={`app-shell ${inspectorOpen ? "" : "inspector-closed"}`}>
      <aside className={`left-panel ${mobileNavOpen ? "mobile-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark"><Braces size={18} /></div>
          <div className="brand-copy">
            <strong>Minimal Sufficient Context</strong>
            <span>Local Workbench V0.2.1</span>
          </div>
          <button className="icon-button mobile-only" onClick={() => setMobileNavOpen(false)} aria-label="关闭导航"><X size={17} /></button>
        </div>

          <button className="button primary full" onClick={() => createConversation()}>
          <MessageSquarePlus size={15} /> 新建对话
        </button>

        <div className="panel-label">Conversations</div>
        <div className="conversation-list">
          {conversations.map((item) => (
            <button
              className={`conversation-item ${conversation?.id === item.id ? "active" : ""}`}
              key={item.id}
              onClick={() => chooseConversation(item.id)}
            >
              <span>
                <strong>{item.title}</strong>
                <small>{item.branch_count || 1} branches</small>
              </span>
              <b>{item.message_count}</b>
            </button>
          ))}
        </div>

        <div className="panel-label label-actions">
          <span>Branches</span>
          {conversation && (
            <button className="icon-button" onClick={deleteConversation} title="删除整个对话" aria-label="删除对话"><Trash2 size={14} /></button>
          )}
        </div>
        <div className="branch-list">
          {branches.map((branch) => (
            <div className={`branch-item ${activeBranch?.id === branch.id ? "active" : ""}`} key={branch.id}>
              {renamingBranchId === branch.id ? (
                <div className="rename-row">
                  <input
                    className="inline-input"
                    value={renameDraft}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") saveRename(branch.id);
                      if (event.key === "Escape") setRenamingBranchId(null);
                    }}
                    autoFocus
                  />
                  <button className="icon-button" onClick={() => saveRename(branch.id)} aria-label="保存名称"><Check size={14} /></button>
                  <button className="icon-button" onClick={() => setRenamingBranchId(null)} aria-label="取消"><X size={14} /></button>
                </div>
              ) : (
                <>
                  <button className="branch-main" onClick={() => selectBranch(branch)}>
                    <GitBranch size={14} />
                    <span>{branch.name}</span>
                    {branch.is_main && <small>MAIN</small>}
                  </button>
                  <div className="branch-actions">
                    <button className="icon-button" onClick={() => beginRename(branch)} title="重命名" aria-label="重命名"><Pencil size={13} /></button>
                    <button className="icon-button" onClick={() => downloadBranch(branch, "markdown")} title="导出 Markdown" aria-label="导出 Markdown"><Download size={13} /></button>
                    <button className="icon-button" onClick={() => downloadBranch(branch, "json")} title="导出 JSON" aria-label="导出 JSON"><FileJson size={13} /></button>
                    {!branch.is_main && <button className="icon-button danger" onClick={() => deleteBranch(branch)} title="删除分支" aria-label="删除分支"><Trash2 size={13} /></button>}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>

        <div className="panel-label">Message Tree</div>
        <ConversationTree
          messages={messages}
          selectedId={selectedId}
          activePathIds={activePathIds}
          onSelect={selectNode}
          onFork={openForkDialog}
        />
      </aside>

      <main className="center-panel">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setMobileNavOpen(true)} aria-label="打开导航"><PanelLeft size={18} /></button>
          <div className="title-block">
            <strong>{conversation?.title || "Minimal Sufficient Context"}</strong>
            <span>{activeBranch ? `${activeBranch.name} · ${selectedId ? `#${selectedId}` : "empty"}` : "选择或新建一个对话"}</span>
          </div>
          <div className="topbar-controls">
            <ProviderStatus
              available={providerAvailable}
              loading={modelsLoading}
              model={selectedModel}
              onRetry={() => refreshModels(false)}
            />
            <select className="select-control" value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} aria-label="模型">
              {models.map((model) => <option key={model} value={model}>{model}</option>)}
            </select>
            <label className="budget-control" title="输入上下文 Token 预算">
              <span>Budget</span>
              <input
                type="number"
                min="256"
                max="262144"
                step="256"
                value={budgetDraft}
                onChange={(event) => setBudgetDraft(event.target.value)}
                onBlur={saveBudget}
                onKeyDown={(event) => event.key === "Enter" && event.currentTarget.blur()}
              />
            </label>
            <button className="icon-button inspector-button" onClick={() => setInspectorOpen((value) => !value)} title="切换上下文面板" aria-label="切换上下文面板"><PanelRight size={17} /></button>
          </div>
        </header>

        <section className="chat-scroll">
          {!conversation && (
            <div className="workspace-empty workspace-welcome">
              <div className="welcome-mark"><Braces size={24} /></div>
              <strong>从一个问题开始</strong>
              <span>先保留一条主线，再从关键消息 Fork 出另一种思路。</span>
              {welcomeVisible ? (
                <StarterPanel hasConversation={false} onCreate={createConversation} onDismiss={dismissWelcome} />
              ) : (
                <button className="button primary" onClick={() => createConversation()}><MessageSquarePlus size={15} /> 新建对话</button>
              )}
            </div>
          )}
          {conversation && !selectedPath.length && !pendingUser && (
            <div className="workspace-empty workspace-welcome">
              <div className="welcome-mark"><GitBranch size={24} /></div>
              <strong>{activeBranch?.name || "Main"}</strong>
              {welcomeVisible ? (
                <StarterPanel
                  hasConversation
                  onPrompt={(prompt) => { setDraft(prompt); focusComposer(); }}
                  onDismiss={dismissWelcome}
                />
              ) : (
                <span>输入一个问题，回答会留在这条主线上。</span>
              )}
            </div>
          )}
          {selectedPath.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <div className="message-meta">
                <span className={`role-name ${message.role}`}>{message.role === "user" ? "You" : "Assistant"}</span>
                <span>#{message.id}</span>
                {message.prompt_tokens != null && <span>{formatTokens(message.prompt_tokens)} prompt tokens</span>}
                <button className="message-fork" onClick={() => openForkDialog(message.id)}><GitFork size={13} /> Fork</button>
              </div>
              <div className="message-content">{message.content}</div>
            </article>
          ))}
          {pendingUser && (
            <article className="message user pending">
              <div className="message-meta"><span className="role-name user">You</span><span>pending</span></div>
              <div className="message-content">{pendingUser}</div>
            </article>
          )}
          {pendingUser && (
            <article className="message assistant pending">
              <div className="message-meta"><span className="role-name assistant">Assistant</span><span>streaming</span></div>
              <div className="message-content streaming-content">
                {streamingText || <span className="typing-indicator"><i /><i /><i /></span>}
              </div>
            </article>
          )}
        </section>

        <div className="composer-area">
          {error && <div className="error-banner"><AlertTriangle size={15} /><span>{error}</span><button className="icon-button" onClick={() => setError("")} aria-label="关闭错误"><X size={14} /></button></div>}
          {showBranchDiscovery && (
            <BranchDiscovery
              onFork={() => openForkDialog(expectedHead)}
              onDismiss={() => setBranchHintVisible(false)}
            />
          )}
          {conversation && activeBranch && (
            <div className={`branch-status ${historicalSelection ? "warning" : ""}`}>
              <GitBranch size={14} />
              <span>
                {historicalSelection
                  ? `消息 #${selectedId} 不是 ${activeBranch.name} 的 Head`
                  : `将在 ${activeBranch.name}${selectedId ? ` · #${selectedId}` : ""} 继续`}
              </span>
              {historicalSelection && <button className="text-command" onClick={() => openForkDialog(selectedId)}>创建分支</button>}
            </div>
          )}
          <div className="composer">
            <textarea
              id="composer"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={conversation ? "输入消息，或用同一问题运行 Linear / Branch 对比" : "请先新建对话"}
              disabled={!conversation || streaming}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendMessage();
                }
              }}
            />
            <div className="composer-actions">
              <button className="button compare-button" onClick={runComparison} disabled={!draft.trim() || comparisonLoading || streaming} title="同一问题分别使用 Linear 与 Branch 上下文">
                {comparisonLoading ? <LoaderCircle className="spin" size={16} /> : <Columns2 size={16} />}
                A/B 对比
              </button>
              {streaming ? (
                <button className="icon-command stop" onClick={stopStreaming} title="停止生成" aria-label="停止生成"><Square size={15} fill="currentColor" /></button>
              ) : (
                <button className="icon-command send" onClick={sendMessage} disabled={!draft.trim() || !conversation} title="发送" aria-label="发送"><Send size={17} /></button>
              )}
            </div>
          </div>
        </div>
      </main>

      <aside className={`debug-panel ${inspectorOpen ? "open" : "closed"}`}>
        <div className="inspector-header">
          <div className="segmented-control inspector-tabs">
            <button className={inspectorTab === "context" ? "active" : ""} onClick={() => chooseInspectorTab("context")}>Context</button>
            <button className={inspectorTab === "compare" ? "active" : ""} onClick={() => chooseInspectorTab("compare")}><Columns2 size={12} /> A/B</button>
            <div className="advanced-tools">
              <button
                className={`advanced-toggle ${["summary", "merge", "dag"].includes(inspectorTab) ? "active" : ""}`}
                onClick={() => setAdvancedToolsOpen((value) => !value)}
                aria-expanded={advancedToolsOpen}
              >
                <MoreHorizontal size={14} /> 更多
              </button>
              {advancedToolsOpen && (
                <div className="advanced-menu">
                  <button className={inspectorTab === "summary" ? "active" : ""} onClick={() => chooseInspectorTab("summary")}><BookOpen size={13} /> Summary</button>
                  <button className={inspectorTab === "merge" ? "active" : ""} onClick={() => chooseInspectorTab("merge")}><GitMerge size={13} /> Merge</button>
                  <button className={inspectorTab === "dag" ? "active" : ""} onClick={() => chooseInspectorTab("dag")}><Network size={13} /> DAG</button>
                </div>
              )}
            </div>
          </div>
          <button className="icon-button mobile-only" onClick={() => setInspectorOpen(false)} aria-label="关闭面板"><X size={17} /></button>
        </div>
        <div className="inspector-scroll">
          {inspectorTab === "context" && <ContextInspector contextInfo={contextInfo} liveContext={liveContext} activeBranch={activeBranch} onOpenMessage={selectNode} />}
          {inspectorTab === "compare" && <ComparisonInspector comparison={comparison} loading={comparisonLoading} />}
          {inspectorTab === "summary" && <SummaryInspector summaries={summaries} onCreate={createActiveSummary} onOpenSource={openOriginalMessage} busy={summaryBusy} />}
          {inspectorTab === "merge" && (
            <MergeInspector
              branches={branches}
              summaries={summaries}
              targetId={mergeTargetId}
              sourceId={mergeSourceId}
              onTargetChange={(value) => { setMergeTargetId(value); setMergePreview(null); setMergeResolutions({}); }}
              onSourceChange={(value) => { setMergeSourceId(value); setMergePreview(null); setMergeResolutions({}); }}
              onPreview={previewMerge}
              preview={mergePreview}
              resolutions={mergeResolutions}
              onResolutionChange={(key, value) => setMergeResolutions((current) => ({ ...current, [key]: value }))}
              onExecute={executePreviewedMerge}
              merges={merges}
              onRollback={rollbackMerge}
              onOpenSource={openOriginalMessage}
              busy={mergeBusy}
            />
          )}
          {inspectorTab === "dag" && <DagInspector dag={dag} />}
        </div>
      </aside>

      <ForkDialog
        state={forkDialog}
        onChange={setForkDialog}
        onClose={() => !forkBusy && setForkDialog(null)}
        onSubmit={createFork}
        busy={forkBusy}
      />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
