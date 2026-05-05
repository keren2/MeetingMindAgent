import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import {
  Activity,
  BarChart3,
  Bot,
  Edit3,
  FileDown,
  FileText,
  FolderDown,
  LogOut,
  MessageSquare,
  Plus,
  Settings,
  Shield,
  Sparkles,
  Trash2,
  Upload,
  Users,
  UsersRound,
  X,
} from "lucide-react";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

type User = {
  id: number;
  username: string;
  is_admin: boolean;
  usage: { daily_limit: number; used_today: number; remaining: number; last_reset_date: string };
};

type Session = { id: number; title: string; created_at: string };
type Message = { id?: number; role: "user" | "assistant"; content: string; created_at?: string };
type KnowledgeFile = { id: number; filename: string; created_at: string; chunks: number };
type RagDoc = { id: number; filename: string; content: string; score: number };
type Trace = { agent: string; output: string };
type Persona = {
  id: number;
  name: string;
  background: string;
  avatar: string;
  tone: string;
  created_at: string;
  matched_skill?: { id: string; label: string; path: string } | null;
};
type GroupChat = { id: number; title: string; event: string; created_at: string };
type GroupMessage = { id: number; group_id: number; persona_id?: number; speaker: string; avatar: string; content: string; created_at: string };
type AdminUser = {
  id: number;
  username: string;
  created_at: string;
  is_active: number;
  is_admin: number;
  daily_limit: number;
  used_today: number;
};
type UsageLog = { id: number; username: string; type: string; created_at: string };

function token() {
  return localStorage.getItem("meetingmind_token") || "";
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (token()) headers.set("Authorization", `Bearer ${token()}`);
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(error.detail || "请求失败");
  }
  return res.json();
}

async function downloadWithAuth(url: string, filename: string) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token()}` } });
  if (!res.ok) throw new Error("下载失败");
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(objectUrl);
}

function hasCjkText(value: string) {
  return /[\u3400-\u9fff]/.test(value);
}

function cleanUnreadableText(value: string) {
  return value
    .replace(/\uFFFD/g, " ")
    .replace(/\?{2,}/g, " ")
    .replace(/[^\w\u3400-\u9fff]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function readableGroupTitle(group: GroupChat) {
  const candidates = Array.from(new Set([group.title, group.event].map((value) => value.trim()).filter(Boolean)));
  const readable = candidates.find((value) => hasCjkText(value) && cleanUnreadableText(value).length >= 4);
  if (readable) return readable;
  const cleaned = cleanUnreadableText(candidates.join(" "));
  if (cleaned) return `${cleaned} \u8ba8\u8bba`;
  return `\u5386\u53f2\u7fa4\u804a #${group.id}`;
}

function AuthGate({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin123");
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const data = await api<{ token: string }>(mode === "login" ? "/api/login" : "/api/register", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      localStorage.setItem("meetingmind_token", data.token);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel">
        <div className="brand-row">
          <div className="brand-mark">
            <Bot size={24} />
          </div>
          <div>
            <h1>MeetingMind Agent Pro</h1>
            <p>AI 需求会议助手</p>
          </div>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label>
            用户名
            <input value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label>
            密码
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="primary" type="submit">
            <Shield size={18} />
            {mode === "login" ? "登录" : "注册"}
          </button>
        </form>
        <button className="ghost wide" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "创建新账号" : "使用已有账号登录"}
        </button>
        <p className="hint">内置管理员：admin / admin123</p>
      </section>
    </main>
  );
}

function AvatarView({ value, className }: { value: string; className: string }) {
  if (value?.startsWith("http://") || value?.startsWith("https://")) {
    return <img className={className} src={value} alt="" referrerPolicy="no-referrer" />;
  }
  return <span className={className}>{value || "🙂"}</span>;
}

function DemandChatBoard({ user, refreshUser }: { user: User; refreshUser: () => void }) {
  const [importText, setImportText] = useState("");
  const [summaries, setSummaries] = useState<{
    quick: { id: number; markdown: string } | null;
    detailed: { id: number; markdown: string } | null;
  }>({ quick: null, detailed: null });
  const [activePreview, setActivePreview] = useState<"quick" | "detailed">("quick");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const importFileRef = useRef<HTMLInputElement | null>(null);
  const activeSummary = summaries[activePreview];

  async function analyzeImported(mode: "quick" | "detailed", content = importText) {
    if (!content.trim()) {
      setError("请先导入或粘贴聊天记录");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const data = await api<{ id: number; markdown: string }>("/api/group_chat/import_analyze", {
        method: "POST",
        body: JSON.stringify({
          title: mode === "quick" ? "聊天记录快速总结" : "聊天记录详细总结",
          content,
          mode,
        }),
      });
      setSummaries((prev) => ({ ...prev, [mode]: { id: data.id, markdown: data.markdown } }));
      setActivePreview(mode);
      refreshUser();
    } catch (err) {
      setError(err instanceof Error ? err.message : "分析失败");
    } finally {
      setBusy(false);
    }
  }

  async function importChatFile(file: File) {
    const content = await file.text();
    setImportText(content);
  }

  function clearAnalysis() {
    setImportText("");
    setSummaries({ quick: null, detailed: null });
    setActivePreview("quick");
    setError("");
    if (importFileRef.current) importFileRef.current.value = "";
  }

  async function downloadSummary(format: "md" | "pdf") {
    if (!activeSummary) return;
    setBusy(true);
    setError("");
    try {
      const filename = `${activePreview === "quick" ? "quick-summary" : "detailed-summary"}-${activeSummary.id}.${format}`;
      const res = await fetch(`${API_BASE}/api/export/${format}?report_id=${activeSummary.id}`, {
        headers: { Authorization: `Bearer ${token()}` },
      });
      if (!res.ok) throw new Error("下载总结失败");
      const blob = await res.blob();
      const picker = (window as Window & {
        showSaveFilePicker?: (options: {
          suggestedName: string;
          types: { description: string; accept: Record<string, string[]> }[];
        }) => Promise<{
          createWritable: () => Promise<{ write: (data: Blob) => Promise<void>; close: () => Promise<void> }>;
        }>;
      }).showSaveFilePicker;
      if (picker) {
        const handle = await picker({
          suggestedName: filename,
          types: [
            format === "md"
              ? { description: "Markdown", accept: { "text/markdown": [".md"] } }
              : { description: "PDF", accept: { "application/pdf": [".pdf"] } },
          ],
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
      } else {
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(objectUrl);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "下载总结失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="analysis-workspace">
      <section className="analysis-panel record-panel">
        <div className="analysis-panel-head">
          <div>
            <span>需求分析</span>
            <h2>导入记录</h2>
            <p>粘贴或上传会议聊天记录，系统会生成结构化总结报告。</p>
          </div>
          <small>剩余 {user.usage.remaining} / {user.usage.daily_limit} 次</small>
        </div>
        <textarea
          className="analysis-record-textarea"
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          placeholder="粘贴群聊记录、会议纪要或访谈文字..."
        />
        <div className="analysis-actions">
          <button onClick={clearAnalysis} disabled={busy || (!importText && !summaries.quick && !summaries.detailed)}>
            <Trash2 size={16} />
            清除
          </button>
          <button onClick={() => importFileRef.current?.click()} disabled={busy}>
            <Upload size={16} />
            导入文件
          </button>
          <input
            ref={importFileRef}
            type="file"
            hidden
            accept=".md,.txt,.json"
            onChange={(e) => e.target.files?.[0] && importChatFile(e.target.files[0])}
          />
          <button onClick={() => analyzeImported("quick")} disabled={busy}>
            <Sparkles size={16} />
            快速总结
          </button>
          <button className="primary" onClick={() => analyzeImported("detailed")} disabled={busy}>
            <Sparkles size={16} />
            详细总结
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </section>

      <section className="analysis-panel report-panel">
        <div className="analysis-panel-head">
          <div>
            <span>输出</span>
            <h2>报告预览</h2>
            <p>切换查看快速总结或详细总结，并保存到指定目录。</p>
          </div>
        </div>
        <div className="preview-tabs">
          <button className={activePreview === "quick" ? "selected" : ""} onClick={() => setActivePreview("quick")}>
            简单预览
          </button>
          <button className={activePreview === "detailed" ? "selected" : ""} onClick={() => setActivePreview("detailed")}>
            详细预览
          </button>
        </div>
        <div className="report-actions report-download-actions">
          <button title="下载 Markdown" onClick={() => downloadSummary("md")} disabled={!activeSummary || busy}>
            <FileText size={16} />
          </button>
          <button title="下载 PDF" onClick={() => downloadSummary("pdf")} disabled={!activeSummary || busy}>
            <FileDown size={16} />
          </button>
        </div>
        <div className="analysis-report-body">
          {activeSummary ? (
            <ReactMarkdown>{activeSummary.markdown}</ReactMarkdown>
          ) : (
            <div className="empty-state">
              <FileText size={34} />
              <h2>暂无报告</h2>
              <p>{activePreview === "quick" ? "点击左侧快速总结后，简单预览会显示在这里。" : "点击左侧详细总结后，详细预览会显示在这里。"}</p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function RoleGroupBoard({ user, refreshUser }: { user: User; refreshUser: () => void }) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [groups, setGroups] = useState<GroupChat[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [form, setForm] = useState({ name: "", background: "", avatar: "", tone: "" });
  const [eventText, setEventText] = useState("讨论：为一个 AI 会议助手设计可落地的 MVP 方案，包括核心功能、风险和销售切入点。");
  const [groupId, setGroupId] = useState<number | null>(null);
  const [groupMessages, setGroupMessages] = useState<GroupMessage[]>([]);
  const [exportDir, setExportDir] = useState("E:\\AgentDemo\\MeetingMindAgent\\exports");
  const [savedPath, setSavedPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const toastTimer = useRef<ReturnType<typeof window.setTimeout> | null>(null);

  function showToast(type: "success" | "error", message: string) {
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    setToast({ type, message });
    toastTimer.current = window.setTimeout(() => setToast(null), 2600);
  }

  async function loadPersonas() {
    const rows = await api<Persona[]>("/api/personas");
    setPersonas(rows);
    if (selectedIds.length === 0) setSelectedIds(rows.slice(0, 3).map((row) => row.id));
  }

  async function loadGroups() {
    setGroups(await api<GroupChat[]>("/api/group_chats"));
  }

  useEffect(() => {
    loadPersonas();
    loadGroups();
    return () => {
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
    };
  }, []);

  async function savePersona() {
    if (!form.name.trim() || !form.background.trim()) {
      setError("请填写角色名称和人物背景");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = JSON.stringify(form);
      if (editingId) {
        await api(`/api/personas/${editingId}`, { method: "PUT", body: payload });
      } else {
        await api("/api/personas", { method: "POST", body: payload });
      }
      setEditingId(null);
      setForm({ name: "", background: "", avatar: "", tone: "" });
      setEditorOpen(false);
      await loadPersonas();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存角色失败");
    } finally {
      setBusy(false);
    }
  }

  async function removePersona(id: number) {
    setBusy(true);
    setError("");
    try {
      await api(`/api/personas/${id}`, { method: "DELETE" });
      setSelectedIds((prev) => prev.filter((roleId) => roleId !== id));
      await loadPersonas();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除角色失败");
    } finally {
      setBusy(false);
    }
  }

  function editPersona(persona: Persona) {
    setEditingId(persona.id);
    setForm({
      name: persona.name,
      background: persona.background,
      avatar: persona.avatar,
      tone: persona.tone || "",
    });
    setEditorOpen(true);
  }

  function openNewPersonaEditor() {
    setEditingId(null);
    setForm({ name: "", background: "", avatar: "", tone: "" });
    setEditorOpen(true);
  }

  function cancelPersonaEdit() {
    setEditingId(null);
    setForm({ name: "", background: "", avatar: "", tone: "" });
    setEditorOpen(false);
  }

  async function startDiscussion() {
    if (!eventText.trim() || selectedIds.length === 0) {
      setError("请填写讨论事件，并至少选择一个角色");
      return;
    }
    setBusy(true);
    setError("");
    setSavedPath("");
    setGroupMessages([]);
    try {
      const headers = new Headers();
      headers.set("Content-Type", "application/json");
      if (token()) headers.set("Authorization", `Bearer ${token()}`);
      const res = await fetch(`${API_BASE}/api/group_chat/start_stream`, {
        method: "POST",
        headers,
        body: JSON.stringify({ event: eventText, role_ids: selectedIds, rounds: 1 }),
      });
      if (!res.ok || !res.body) {
        const error = await res.json().catch(() => ({ detail: "发起讨论失败" }));
        throw new Error(error.detail || "发起讨论失败");
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.type === "group") {
            setGroupId(event.group_id);
          }
          if (event.type === "message_start") {
            setGroupMessages((prev) => [...prev, event.message]);
          }
          if (event.type === "message_delta") {
            setGroupMessages((prev) =>
              prev.map((message, index) => index === prev.length - 1 ? { ...message, content: message.content + event.content } : message),
            );
          }
          if (event.type === "message_done") {
            if (event.message.speaker !== "老板") {
              setGroupMessages((prev) => prev.map((message, index) => index === prev.length - 1 ? event.message : message));
            } else {
              setGroupMessages((prev) => [...prev, event.message]);
            }
          }
        }
      }
      await loadGroups();
      refreshUser();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发起讨论失败");
    } finally {
      setBusy(false);
    }
  }

  async function openGroup(id: number) {
    setGroupId(id);
    setGroupMessages(await api<GroupMessage[]>(`/api/group_chat/${id}/messages`));
  }

  async function clearMessages() {
    if (!groupId) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/group_chat/${groupId}/messages`, { method: "DELETE" });
      setGroupMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "清除记录失败");
    } finally {
      setBusy(false);
    }
  }

  async function exportDownload() {
    if (!groupId) return;
    try {
      await downloadWithAuth(`${API_BASE}/api/group_chat/${groupId}/export`, `group-chat-${groupId}.md`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败");
    }
  }

  async function exportToPath() {
    if (!groupId) return;
    setBusy(true);
    setError("");
    setSavedPath("");
    try {
      const data = await api<{ path: string }>(`/api/group_chat/${groupId}/export_to_path`, {
        method: "POST",
        body: JSON.stringify({ directory: exportDir, filename: `group-chat-${groupId}.md` }),
      });
      setSavedPath(data.path);
      showToast("success", "导出成功");
    } catch (err) {
      setError(err instanceof Error ? `导出失败：${err.message}` : "导出失败");
      showToast("error", "导出失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="group-workspace">
      {toast && <div className={`toast ${toast.type}`}>{toast.message}</div>}
      <aside className="role-pane">
        <div className="pane-header">
          <UsersRound size={18} />
          <span>角色 Agent</span>
          <button title="新增角色" onClick={openNewPersonaEditor}>
            <Plus size={16} />
          </button>
        </div>
        <div className="role-list">
          {personas.map((persona) => (
            <div className={selectedIds.includes(persona.id) ? "role-card selected" : "role-card"} key={persona.id}>
              <label className="role-check">
                <input
                  type="checkbox"
                  checked={selectedIds.includes(persona.id)}
                  onChange={(event) =>
                    setSelectedIds((prev) => event.target.checked ? [...prev, persona.id] : prev.filter((id) => id !== persona.id))
                  }
                />
                <AvatarView className="avatar" value={persona.avatar} />
                <strong>{persona.name}</strong>
              </label>
              <p>{persona.background}</p>
              {persona.tone && <small>{persona.tone}</small>}
              {persona.matched_skill && <span className="skill-badge">Skill: {persona.matched_skill.label}</span>}
              <div className="row-actions">
                <button title="编辑角色" onClick={() => editPersona(persona)}>
                  <Edit3 size={15} />
                </button>
                <button title="删除角色" onClick={() => removePersona(persona.id)}>
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </aside>

      {editorOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={cancelPersonaEdit}>
          <section className="persona-modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div>
                <strong>{editingId ? "编辑角色" : "新增角色"}</strong>
                <span>设置人物背景、头像和发言风格</span>
              </div>
              <button title="取消编辑" onClick={cancelPersonaEdit}>
                <X size={16} />
              </button>
            </div>
            <div className="form-grid">
              <label>名称<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
              <div className="avatar-preview-box">
                <AvatarView className="avatar preview-avatar" value={form.avatar} />
                <span>{form.avatar ? "当前头像预览" : "留空保存后会自动分配随机真人头像"}</span>
              </div>
              <label className="wide-field">头像图片地址<input value={form.avatar} onChange={(e) => setForm({ ...form, avatar: e.target.value })} placeholder="https://example.com/avatar.jpg" /></label>
              <label className="wide-field">人物背景<textarea value={form.background} onChange={(e) => setForm({ ...form, background: e.target.value })} /></label>
              <label className="wide-field">表达风格<textarea value={form.tone} onChange={(e) => setForm({ ...form, tone: e.target.value })} /></label>
            </div>
            <div className="modal-actions">
              <button onClick={cancelPersonaEdit}>取消编辑</button>
              <button className="primary" onClick={savePersona} disabled={busy}>
                <Plus size={16} />
                保存角色
              </button>
            </div>
          </section>
        </div>
      )}

      <section className="group-chat-pane">
        <div className="chat-top">
          <div>
            <strong>需求讨论</strong>
            <span>剩余 {user.usage.remaining} / {user.usage.daily_limit} 次</span>
          </div>
          <button className="secondary" onClick={startDiscussion} disabled={busy}>
            <Sparkles size={17} />
            发起讨论
          </button>
        </div>
        <div className="event-box">
          <label>
            指定讨论事件
            <textarea value={eventText} onChange={(e) => setEventText(e.target.value)} />
          </label>
        </div>
        <div className="group-messages">
          {groupMessages.length === 0 && (
            <div className="empty-state">
              <UsersRound size={34} />
              <h2>选择角色，指定事件，让 Agent 群聊完善方案</h2>
              <p>每个角色会根据自己的人物背景提出建议、风险和追问。</p>
            </div>
          )}
          {groupMessages.map((message) => (
            <div className="group-message" key={message.id}>
              <AvatarView className="group-avatar" value={message.avatar} />
              <div>
                <div className="speaker-line">
                  <strong>{message.speaker}</strong>
                  <small>{message.created_at}</small>
                </div>
                <p>{message.content}</p>
              </div>
            </div>
          ))}
        </div>
        {error && <div className="error">{error}</div>}
        <div className="group-toolbar">
          <button onClick={clearMessages} disabled={!groupId || busy}>
            <Trash2 size={16} />
            清除聊天记录
          </button>
          <input value={exportDir} onChange={(e) => setExportDir(e.target.value)} placeholder="例如 E:\\MeetingExports" />
          <button className="secondary" onClick={exportToPath} disabled={!groupId || busy}>
            <FolderDown size={16} />
            导出记录
          </button>
        </div>
        {savedPath && <div className="success">导出成功：{savedPath}</div>}
      </section>

      <aside className="group-side-pane">
        <div className="pane-header">
          <MessageSquare size={18} />
          <span>历史群聊</span>
        </div>
        <div className="session-list group-history-list">
          {groups.map((group) => (
            <button
              className={groupId === group.id ? "active item group-history-item" : "item group-history-item"}
              key={group.id}
              onClick={() => openGroup(group.id)}
              title={readableGroupTitle(group)}
            >
              <span>{readableGroupTitle(group)}</span>
              <small>{new Date(group.created_at).toLocaleDateString()}</small>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}

function Workspace({ user, refreshUser }: { user: User; refreshUser: () => void }) {
  const [mode, setMode] = useState<"demand" | "group">("group");

  return (
    <div className="workspace-shell">
      <div className="workspace-tabs">
        <button className={mode === "group" ? "selected" : ""} onClick={() => setMode("group")}>
          <UsersRound size={17} />
          需求讨论
        </button>
        <button className={mode === "demand" ? "selected" : ""} onClick={() => setMode("demand")}>
          <MessageSquare size={17} />
          需求分析
        </button>
      </div>
      {mode === "demand" ? <DemandChatBoard user={user} refreshUser={refreshUser} /> : <RoleGroupBoard user={user} refreshUser={refreshUser} />}
    </div>
  );
}

function AdminPanel({ user }: { user: User }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [logs, setLogs] = useState<UsageLog[]>([]);
  const [limit, setLimit] = useState(3);
  const [error, setError] = useState("");

  async function load() {
    if (!user.is_admin) return;
    setUsers(await api<AdminUser[]>("/admin/users"));
    setLogs(await api<UsageLog[]>("/admin/usage"));
    const cfg = await api<{ default_daily_limit: string }>("/admin/config");
    setLimit(Number(cfg.default_daily_limit || 3));
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [user.is_admin]);

  async function resetLimit(id: number) {
    await api("/admin/reset_limit", { method: "POST", body: JSON.stringify({ user_id: id }) });
    await load();
  }

  async function saveConfig() {
    await api("/admin/config", { method: "POST", body: JSON.stringify({ default_daily_limit: limit }) });
    await load();
  }

  if (!user.is_admin) {
    return <div className="admin-empty">当前账号不是管理员。</div>;
  }

  return (
    <div className="admin-grid">
      {error && <div className="error">{error}</div>}
      <section>
        <div className="pane-header">
          <BarChart3 size={18} />
          <span>Dashboard</span>
        </div>
        <div className="metric-row">
          <div><strong>{users.length}</strong><span>用户数</span></div>
          <div><strong>{logs.length}</strong><span>调用日志</span></div>
          <div><strong>{limit}</strong><span>默认限额</span></div>
        </div>
      </section>
      <section>
        <div className="pane-header">
          <Users size={18} />
          <span>用户管理</span>
        </div>
        <table>
          <thead>
            <tr><th>ID</th><th>用户</th><th>已用</th><th>限额</th><th>状态</th><th></th></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>{u.username}</td>
                <td>{u.used_today}</td>
                <td>{u.daily_limit}</td>
                <td>{u.is_active ? "启用" : "禁用"}</td>
                <td><button onClick={() => resetLimit(u.id)}>重置</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section>
        <div className="pane-header">
          <Settings size={18} />
          <span>系统配置</span>
        </div>
        <div className="config-row">
          <label>默认每日限额<input type="number" value={limit} min={1} onChange={(e) => setLimit(Number(e.target.value))} /></label>
          <button className="secondary" onClick={saveConfig}>保存</button>
        </div>
      </section>
      <section>
        <div className="pane-header">
          <Activity size={18} />
          <span>使用记录</span>
        </div>
        <table>
          <thead>
            <tr><th>ID</th><th>用户</th><th>类型</th><th>时间</th></tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id}>
                <td>{log.id}</td><td>{log.username}</td><td>{log.type}</td><td>{log.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<"workspace" | "admin">("workspace");

  async function refreshUser() {
    try {
      setUser(await api<User>("/api/me"));
    } catch {
      setUser(null);
      localStorage.removeItem("meetingmind_token");
    }
  }

  useEffect(() => {
    if (token()) refreshUser();
  }, []);

  const title = useMemo(() => (view === "workspace" ? "需求工作台" : "管理后台"), [view]);

  if (!user) return <AuthGate onLogin={refreshUser} />;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-row compact">
          <div className="brand-mark">
            <Bot size={20} />
          </div>
          <div>
            <h1>{title}</h1>
            <p>{user.username}</p>
          </div>
        </div>
        <nav>
          <button className={view === "workspace" ? "selected" : ""} onClick={() => setView("workspace")}>
            <MessageSquare size={17} />
            工作台
          </button>
          <button className={view === "admin" ? "selected" : ""} onClick={() => setView("admin")}>
            <Shield size={17} />
            后台
          </button>
          <button onClick={() => { localStorage.removeItem("meetingmind_token"); setUser(null); }}>
            <LogOut size={17} />
            退出
          </button>
        </nav>
      </header>
      {view === "workspace" ? <Workspace user={user} refreshUser={refreshUser} /> : <AdminPanel user={user} />}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
