import { useEffect, useState } from 'react'

// FE1 — 页面信息结构对齐 CLI top（render_top）：
//   进程头 + Elapsed 行 + Active threads 表（点击展开完整调用栈）
//   + Idle threads 行 + Top functions 表
// FE2 — EventSource 消费 /api/events：stats 帧整表刷新；exit 帧显示
//   「目标进程已退出」终态横幅并 close() 不重连（WA4 配套）。
export default function App() {
  const [proc, setProc] = useState(null)
  const [stats, setStats] = useState(null)
  const [exited, setExited] = useState(false)

  // 首帧 SSE 到达前先取 /api/process（WA2），进程头立即可见。
  useEffect(() => {
    fetch('/api/process')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => data && setProc(data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const es = new EventSource('/api/events')
    es.addEventListener('stats', (e) => setStats(JSON.parse(e.data)))
    es.addEventListener('exit', () => {
      setExited(true)
      es.close() // FE2 — 终态后关闭，防浏览器自动重连打转
    })
    return () => es.close()
  }, [])

  const process = stats?.process ?? proc
  const threads = stats?.threads ?? []
  const active = threads
    .filter((t) => !t.idle)
    .sort((a, b) => a.native_tid - b.native_tid)
  const idleTids = threads
    .filter((t) => t.idle)
    .map((t) => t.native_tid)
    .sort((a, b) => a - b)

  return (
    <main>
      {exited && (
        <div className="exit-banner">目标进程已退出 — target process exited</div>
      )}
      {process ? (
        <ProcessHeader process={process} stats={stats} />
      ) : (
        <p className="dim">connecting…</p>
      )}
      {stats && (
        <>
          <ActiveThreads threads={active} />
          {idleTids.length > 0 && (
            <p className="idle-line">Idle threads: {idleTids.join(', ')}</p>
          )}
          <TopFunctions functions={stats.top_functions} />
        </>
      )}
    </main>
  )
}

// 对齐 format_process_header（TX3）：Process <pid>: <cmdline> /
// Python v<ver> (<exe>)；version_warning 非空时补告警行（WS2 同款语义）。
function ProcessHeader({ process, stats }) {
  return (
    <header>
      <h1>
        Process <span className="tid">{process.pid}</span>: {process.cmdline}
      </h1>
      <p className="sub">
        Python v{process.python_version} ({process.exe_path})
      </p>
      {process.version_warning && (
        <p className="warning">{process.version_warning}</p>
      )}
      {stats && (
        <p className="dim">
          Elapsed {stats.elapsed.toFixed(1)}s | {stats.samples} samples (idle{' '}
          {stats.idle_samples})
        </p>
      )}
    </header>
  )
}

// Active threads 表：TID / OWN% / CURRENT（顶帧）；点击行展开完整帧链
// （FE1：完整实时调用栈，A13 增强点——帧链不截断，WM3 已保证）。
function ActiveThreads({ threads }) {
  const [expanded, setExpanded] = useState(() => new Set())
  const toggle = (tid) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(tid)) next.delete(tid)
      else next.add(tid)
      return next
    })

  return (
    <section>
      <h2>Active threads</h2>
      <table>
        <thead>
          <tr>
            <th className="col-tid">TID</th>
            <th className="col-num">OWN%</th>
            <th>CURRENT</th>
          </tr>
        </thead>
        <tbody>
          {threads.length === 0 && (
            <tr>
              <td colSpan={3} className="dim">
                (no active threads)
              </td>
            </tr>
          )}
          {threads.map((t) => {
            const top = t.frames[0] ?? null
            const isOpen = expanded.has(t.native_tid)
            return (
              <ThreadRows
                key={t.native_tid}
                thread={t}
                top={top}
                isOpen={isOpen}
                onToggle={() => toggle(t.native_tid)}
              />
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

function ThreadRows({ thread, top, isOpen, onToggle }) {
  return (
    <>
      <tr
        className={`thread-row${isOpen ? ' open' : ''}`}
        onClick={onToggle}
        title={isOpen ? 'click to collapse' : 'click to expand full stack'}
      >
        <td className="tid">{thread.native_tid}</td>
        <td className="num">{thread.own_pct.toFixed(1)}%</td>
        <td>
          <span className="caret">{isOpen ? '▾' : '▸'}</span>{' '}
          {thread.name && <span className="tname">{thread.name} </span>}
          {top ? (
            <>
              <span className="func">{top.name ?? '?'}</span>{' '}
              <span className="dim">
                ({top.filename ?? '?'}:{top.line})
              </span>
            </>
          ) : (
            <span className="dim">(no Python frame)</span>
          )}
        </td>
      </tr>
      {isOpen && (
        <tr className="frames-row">
          <td colSpan={3}>
            <ol className="frames">
              {thread.frames.map((f, i) => (
                <li key={i}>
                  <span className="func">{f.name ?? '?'}</span>{' '}
                  <span className="dim">
                    ({f.filename ?? '?'}:{f.line})
                  </span>
                </li>
              ))}
            </ol>
          </td>
        </tr>
      )}
    </>
  )
}

// Top functions 表：OWN% / TOTAL% / TIME / FUNCTION（render_top 同口径，
// WM4 已在后端排序截断）。
function TopFunctions({ functions }) {
  return (
    <section>
      <h2>Top functions</h2>
      <table>
        <thead>
          <tr>
            <th className="col-num">OWN%</th>
            <th className="col-num">TOTAL%</th>
            <th className="col-num">TIME</th>
            <th>FUNCTION</th>
          </tr>
        </thead>
        <tbody>
          {functions.length === 0 && (
            <tr>
              <td colSpan={4} className="dim">
                (no samples yet)
              </td>
            </tr>
          )}
          {functions.map((f) => (
            <tr key={f.name}>
              <td className="num dim">{f.own_pct.toFixed(1)}%</td>
              <td className="num dim">{f.total_pct.toFixed(1)}%</td>
              <td className="num dim">{f.time.toFixed(1)}s</td>
              <td className="func">{f.name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
