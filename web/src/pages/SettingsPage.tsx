import { AlertTriangle, CheckCircle2, ChevronDown, Plus, Trash2 } from "lucide-react";
import { FormEvent, type ReactNode, useEffect, useState } from "react";
import { api } from "../api";
import { Button, Loading, Notice, formatTime } from "../components";
import type { BrowserAuthProfile, SettingsStatus } from "../types";

type ModelForm = { api_url: string; api_key: string; api_mode: "responses" | "chat_completions"; model: string; reasoning_effort: "none" | "low" | "medium" | "high"; parameters: string; ssl_verify: boolean; tracing_enabled: boolean };
type BrowserForm = { timeout_seconds: number; scroll_limit_px: number };

export function SettingsPage() {
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"service" | "auth">("service");
  const [model, setModel] = useState<ModelForm>({ api_url: "", api_key: "", api_mode: "responses", model: "", reasoning_effort: "medium", parameters: "{}", ssl_verify: false, tracing_enabled: false });
  const [browser, setBrowser] = useState<BrowserForm>({ timeout_seconds: 30, scroll_limit_px: 20_000 });
  const [profiles, setProfiles] = useState<BrowserAuthProfile[]>([]);
  const [authRaw, setAuthRaw] = useState("");
  const [authOrigin, setAuthOrigin] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const [modelStatus, browserStatus, authProfiles] = await Promise.all([
      api<SettingsStatus>("/settings/model"),
      api<SettingsStatus>("/settings/browser"),
      api<BrowserAuthProfile[]>("/browser-auth-profiles"),
    ]);
    setModel((current) => ({
      ...current,
      api_url: String(modelStatus.values.api_url ?? ""),
      api_key: modelStatus.configured ? "由环境变量配置" : "未配置",
      api_mode: modelStatus.values.api_mode === "chat_completions" ? "chat_completions" : "responses",
      model: String(modelStatus.values.model ?? ""),
      reasoning_effort: (modelStatus.values.reasoning_effort as ModelForm["reasoning_effort"]) ?? "medium",
      parameters: JSON.stringify(modelStatus.values.parameters ?? {}, null, 2),
      ssl_verify: modelStatus.values.ssl_verify !== false,
      tracing_enabled: modelStatus.values.tracing_enabled === true,
    }));
    setBrowser({
      timeout_seconds: Number(browserStatus.values.timeout_seconds ?? 30),
      scroll_limit_px: Number(browserStatus.values.scroll_limit_px ?? 20_000),
    });
    setProfiles(authProfiles);
  }

  useEffect(() => {
    load().catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false));
  }, []);

  async function persistBrowser() {
    await api("/settings/browser", { method: "PATCH", body: JSON.stringify(browser) });
  }

  async function saveBrowser() {
    setBusy(true); setMessage(""); setError("");
    try {
      await persistBrowser();
      setMessage("配置已保存");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally { setBusy(false); }
  }

  async function importAuth(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api("/browser-auth-profiles", { method: "POST", body: JSON.stringify({ raw: authRaw, origin: authOrigin || null }) });
      setAuthRaw(""); setAuthOrigin(""); setShowImport(false); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "导入失败"); }
    finally { setBusy(false); }
  }

  async function removeProfile(id: number) {
    if (!window.confirm("删除这条共享页面登录态？该操作无法撤销。")) return;
    await api(`/browser-auth-profiles/${id}`, { method: "DELETE" });
    await load();
  }

  if (loading) return <Loading label="读取系统设置" />;

  return (
    <div className="settings-page page-scroll">
      <header className="page-header"><div><h1>系统设置</h1><div className="settings-tabs"><button className={tab === "service" ? "active" : ""} onClick={() => setTab("service")}>服务配置</button><button className={tab === "auth" ? "active" : ""} onClick={() => setTab("auth")}>页面登录态</button></div></div></header>
      {message ? <Notice tone="success">{message}</Notice> : null}
      {error ? <Notice tone="error">{error}</Notice> : null}

      {tab === "service" ? (
        <div className="settings-layout">
          <div className="settings-main">
            <SettingsSection title="模型">
              <Field label="API URL"><input value={model.api_url} readOnly /></Field>
              <Field label="API Key"><input value={model.api_key} readOnly /></Field>
              <Field label="API 形式"><div className="segmented"><button className={model.api_mode === "responses" ? "active" : ""} disabled>Responses</button><button className={model.api_mode === "chat_completions" ? "active" : ""} disabled>Chat Completions</button></div></Field>
              <Field label="模型名称"><input value={model.model || "未配置"} readOnly /></Field>
              <Field label="推理努力"><select value={model.reasoning_effort} disabled><option value="none">无</option><option value="low">低</option><option value="medium">中等</option><option value="high">高</option></select></Field>
              <Field label="SSL 校验"><input value={model.ssl_verify ? "已开启" : "已关闭（不安全）"} readOnly /></Field>
              <Field label="Trace 导出"><input value={model.tracing_enabled ? "已开启" : "已关闭，不发送到 OpenAI"} readOnly /></Field>
              <Field label="参数（JSON）"><textarea className="json-editor" value={model.parameters} readOnly spellCheck={false} /></Field>
              <div className="settings-env-note">模型配置只读取 <code>backend/.env</code> 中的 <code>FIXORA_MODEL_*</code>；修改后重启 API 和 Worker。</div>
            </SettingsSection>

            <SettingsSection title="问题页面采集">
              <Field label="Playwright 状态"><span className="inline-status"><CheckCircle2 size={16} />Chromium 由后端启动时验证</span></Field>
              <Field label="网络策略"><input value="允许 HTTP / HTTPS" readOnly /><span className="field-warning"><AlertTriangle size={15} />HTTP 来源将标记为不安全</span></Field>
              <Field label="滚动限制"><input type="number" value={browser.scroll_limit_px} onChange={(event) => setBrowser({ ...browser, scroll_limit_px: Number(event.target.value) })} /></Field>
              <Field label="超时时间（秒）"><input type="number" value={browser.timeout_seconds} onChange={(event) => setBrowser({ ...browser, timeout_seconds: Number(event.target.value) })} /></Field>
              <div className="section-actions"><Button variant="primary" onClick={() => void saveBrowser()} disabled={busy}>保存采集配置</Button></div>
            </SettingsSection>
          </div>
        </div>
      ) : (
        <section className="auth-settings">
          <header><div><h2>页面登录态</h2><p>管理共享 domain / origin 登录态，用于问题页面读取。凭据仅写入，不会显示。</p></div><Button variant="primary" onClick={() => setShowImport(true)}><Plus size={17} />导入登录态</Button></header>
          <Notice tone="warning">应用没有用户登录。任何能访问 Fixora 的人都能使用这些共享登录态读取匹配页面。</Notice>
          <div className="auth-profile-list">
            {profiles.map((profile) => (
              <div className="auth-profile-row" key={profile.id}><div><strong>{profile.origin}</strong><span>{profile.kind} · {formatTime(profile.updated_at)}</span></div><button className="icon-button danger" onClick={() => void removeProfile(profile.id)} aria-label={`删除 ${profile.origin}`}><Trash2 size={17} /></button></div>
            ))}
            {profiles.length === 0 ? <div className="empty-row">尚未导入共享登录态。</div> : null}
          </div>
        </section>
      )}

      {showImport ? (
        <div className="modal-backdrop"><form className="modal auth-import" onSubmit={importAuth}><header><h2>导入页面登录态</h2><button type="button" className="icon-button" onClick={() => setShowImport(false)}>×</button></header><label><span>Origin</span><input value={authOrigin} onChange={(event) => setAuthOrigin(event.target.value)} placeholder="https://issues.example.com" /></label><label><span>Cookie / localStorage / storage_state</span><textarea value={authRaw} onChange={(event) => setAuthRaw(event.target.value)} rows={12} autoFocus /></label><Notice tone="warning">共享凭据按 origin 加密保存；原始值不会再次显示。</Notice><div className="modal-actions"><Button type="button" onClick={() => setShowImport(false)}>取消</Button><Button variant="primary" disabled={!authRaw.trim() || busy}>导入</Button></div></form></div>
      ) : null}
    </div>
  );
}

function SettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="settings-section"><header><h2>{title}</h2><ChevronDown size={18} /></header>{children}</section>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="settings-field"><span>{label}</span><div>{children}{hint ? <small>{hint}</small> : null}</div></label>;
}
