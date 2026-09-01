import { Database, Plus, RefreshCcw, Search, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { Button, Loading, Notice, formatTime } from "../components";
import type { Repository, RuntimeProfile } from "../types";

type DiscoveredRepository = {
  id: number;
  name: string;
  path_with_namespace: string;
  default_branch: string | null;
  added: boolean;
};

function splitCommand(value: string): string[] {
  return value.trim().split(/\s+/).filter(Boolean);
}

export function RepositoriesPage() {
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [selected, setSelected] = useState<Repository | null>(null);
  const [discoverOpen, setDiscoverOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [discovered, setDiscovered] = useState<DiscoveredRepository[]>([]);
  const [runtime, setRuntime] = useState<RuntimeProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function reload(selectId?: number) {
    const items = await api<Repository[]>("/repositories");
    setRepositories(items);
    if (selectId || selected) {
      const next = items.find((item) => item.id === (selectId ?? selected?.id)) ?? null;
      setSelected(next);
      setRuntime(next?.runtime_profile ?? null);
    }
  }

  useEffect(() => {
    reload().catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false));
  }, []);

  async function discover(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError("");
    try {
      setDiscovered(await api<DiscoveredRepository[]>(`/repositories/discover?search=${encodeURIComponent(search)}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取 GitLab 仓库失败");
    } finally {
      setBusy(false);
    }
  }

  async function addRepository(projectId: number) {
    setBusy(true);
    try {
      const repository = await api<Repository>("/repositories", {
        method: "POST",
        body: JSON.stringify({ gitlab_project_id: projectId }),
      });
      await reload(repository.id);
      setDiscoverOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "添加仓库失败");
    } finally {
      setBusy(false);
    }
  }

  async function mutate(path: string) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const repository = await api<Repository>(`/repositories/${selected.id}/${path}`, { method: "POST" });
      await reload(repository.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "仓库操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveRuntime() {
    if (!selected || !runtime) return;
    setBusy(true);
    try {
      await api(`/repositories/${selected.id}/runtime`, {
        method: "PATCH",
        body: JSON.stringify(runtime),
      });
      await reload(selected.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存运行配置失败");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Loading label="读取仓库" />;

  return (
    <div className={`repository-page ${selected ? "inspector-open" : ""}`}>
      <section className="repository-main page-scroll">
        <header className="page-header">
          <div><h1>代码仓库</h1><p>从 GitLab 选择可用于修复任务的仓库</p></div>
        </header>
        <div className="repository-toolbar">
          <label className="search-field"><Search size={18} /><input placeholder="搜索仓库" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <Button variant="primary" onClick={() => { setDiscoverOpen(true); void discover(); }}><Plus size={18} />添加仓库</Button>
        </div>
        {error ? <Notice tone="error">{error}</Notice> : null}
        <div className="repository-table">
          <div className="repository-row repository-head"><span>仓库</span><span>默认分支</span><span>缓存状态</span><span>最后 fetch 时间</span><span>运行时</span><span>操作</span></div>
          {repositories.map((repository) => (
            <button
              key={repository.id}
              className={`repository-row ${selected?.id === repository.id ? "selected" : ""}`}
              onClick={() => { setSelected(repository); setRuntime(repository.runtime_profile); }}
            >
              <strong>{repository.path_with_namespace}</strong>
              <span>{repository.default_branch}</span>
              <span>{repository.cached_sha ? `已同步 · ${repository.cached_sha.slice(0, 7)}` : repository.cache_status}</span>
              <span>{formatTime(repository.last_fetch_at)}</span>
              <span>{repository.runtime_profile ? `${repository.runtime_profile.language === "node" ? "Node" : "Python"} ${repository.runtime_profile.runtime_version}` : "未检测"}</span>
              <span className="link-action">配置</span>
            </button>
          ))}
          {repositories.length === 0 ? <div className="empty-row">尚未添加仓库。</div> : null}
        </div>
      </section>

      {selected ? (
        <aside className="inspector repository-inspector page-scroll">
          <header><h2>仓库设置</h2><button className="icon-button" onClick={() => setSelected(null)} aria-label="关闭仓库设置"><X size={20} /></button></header>
          <label><span>仓库</span><strong>{selected.path_with_namespace}</strong></label>
          <label><span>GitLab Project ID</span><input value={selected.gitlab_project_id} readOnly /></label>
          <label><span>默认分支</span><input value={selected.default_branch} readOnly /></label>
          <label><span>缓存状态</span><div className="input-action"><input value={selected.cached_sha ? `已同步 · ${selected.cached_sha.slice(0, 7)}` : selected.cache_status} readOnly /><Button onClick={() => void mutate("fetch")} disabled={busy}>立即 fetch</Button></div></label>
          {runtime ? (
            <>
              <label><span>运行时</span><select value={runtime.language} onChange={(event) => { const language = event.target.value as "node" | "python"; setRuntime({ ...runtime, language, runtime_version: language === "node" ? "22" : "3.12" }); }}><option value="node">Node 22</option><option value="python">Python 3.12</option></select></label>
              <label><span>包管理器</span><input value={runtime.package_manager} onChange={(event) => setRuntime({ ...runtime, package_manager: event.target.value })} /></label>
              <label><span>依赖环境</span><input value={`按仓库缓存 · ${runtime.lockfile_hash ? "lockfile 已识别" : "无 lockfile"}`} readOnly /></label>
              <label><span>检测到的安装命令</span><input value={runtime.install_argv.join(" ")} onChange={(event) => setRuntime({ ...runtime, install_argv: splitCommand(event.target.value) })} /></label>
              <label><span>验证方式</span><input value={runtime.language === "python" ? "python3 执行 fixora_temp_*.py" : "node 执行 fixora_temp_*.mjs"} readOnly /></label>
            </>
          ) : <Notice tone="warning">尚未检测运行环境。</Notice>}
          <div className="inspector-actions">
            <Button onClick={() => void mutate("detect-runtime")} disabled={busy}><RefreshCcw size={17} />重新检测</Button>
            <Button variant="primary" onClick={() => void saveRuntime()} disabled={busy || !runtime}>保存配置</Button>
          </div>
        </aside>
      ) : null}

      {discoverOpen ? (
        <div className="modal-backdrop" role="presentation">
          <section className="modal" role="dialog" aria-modal="true" aria-label="添加仓库">
            <header><h2>添加 GitLab 仓库</h2><button className="icon-button" onClick={() => setDiscoverOpen(false)}><X size={20} /></button></header>
            <form className="modal-search" onSubmit={discover}><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称或路径" autoFocus /><Button disabled={busy}>搜索</Button></form>
            <div className="discover-list">
              {discovered.map((item) => (
                <div className="discover-row" key={item.id}><Database size={18} /><div><strong>{item.path_with_namespace}</strong><span>{item.default_branch ?? "无默认分支"}</span></div><Button variant={item.added ? "secondary" : "primary"} disabled={item.added || busy} onClick={() => void addRepository(item.id)}>{item.added ? "已添加" : "添加"}</Button></div>
              ))}
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
