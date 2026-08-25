/**
 * 奶娃桌宠 - helper 子进程管理（v2.0）
 * - spawn `python helper/main.py --packs <packs>`
 * - JSON-lines 协议（UTF-8）写入 stdin / 读取 stdout
 * - 崩溃自动重启（限频），插件停用优雅关闭
 * - 环境不支持子进程时返回 disabled，host 降级浏览器 overlay
 */
'use strict';

const path = require('path');
let spawn = null;
let childProcessAvailable = true;
try {
  const cp = require('child_process');
  spawn = cp.spawn;
} catch (e) {
  childProcessAvailable = false;
}

class HelperClient {
  constructor(rootDir) {
    this.rootDir = rootDir;
    this.packsDir = path.join(rootDir, 'packs');
    this.proc = null;
    this.available = childProcessAvailable;
    this.disabled = false;
    this._restartTimer = null;
    this._lastSpawn = 0;
    this._shuttingDown = false;
    this._userExited = false;
  }

  get enabled() {
    return this.available && !this.disabled && this.proc && this.proc.exitCode === null;
  }

  /**
   * 启动 helper 子进程
   * @returns {boolean} 是否成功启动
   */
  start() {
    if (!childProcessAvailable || this.disabled) {
      console.warn('[pet-nailong] helper 不可用，降级浏览器 overlay');
      return false;
    }
    if (this.proc && this.proc.exitCode === null) return true;
    const now = Date.now();
    if (now - this._lastSpawn < 3000) {
      // 限频：3 秒内不重复拉起
      if (!this._restartTimer) {
        this._restartTimer = setTimeout(() => {
          this._restartTimer = null;
          this.start();
        }, 3000 - (now - this._lastSpawn));
      }
      return false;
    }
    this._lastSpawn = now;
    this._shuttingDown = false;

    const helperMain = path.join(__dirname, '..', 'helper', 'main.py');
    const pythonCmd = process.env.PET_PYTHON || 'python';
    try {
      this.proc = spawn(pythonCmd, [helperMain, '--packs', this.packsDir], {
        cwd: this.rootDir,
        stdio: ['pipe', 'pipe', 'inherit'],
        windowsHide: true,
      });
    } catch (e) {
      console.warn('[pet-nailong] spawn helper 失败:', e.message);
      this.disabled = true;
      return false;
    }

    this.proc.stdin.on('error', () => { /* helper 已退出 */ });
    this.proc.stdout.setEncoding('utf8');
    this.proc.stdout.on('data', (chunk) => this._onStdout(chunk));
    this.proc.on('error', (err) => {
      console.warn('[pet-nailong] helper 进程错误:', err.message);
    });
    this.proc.on('close', (code) => this._onClose(code));
    console.log('[pet-nailong] helper 已启动 pid=' + this.proc.pid);
    return true;
  }

  /** 发送一条 JSON-lines 消息 */
  send(msg) {
    if (!this.enabled) return false;
    try {
      this.proc.stdin.write(JSON.stringify(msg) + '\n');
      return true;
    } catch (e) {
      return false;
    }
  }

  sendState(session, packsMeta) {
    const pack = packsMeta.get ? packsMeta.get(session.packId) : null;
    this.send({
      kind: 'state',
      state: session.state,
      packId: session.packId,
      toolCount: session.toolCount,
      lastTool: session.lastToolName || '',
      bubbles: pack ? pack.bubbles : {},
      clickBubbles: pack ? pack.clickBubbles : [],
      laugh: pack ? pack.laugh : {},
      thinkingLines: pack ? pack.thinkingLines : [],
      timestamp: Date.now(),
    });
  }

  _onStdout(chunk) {
    // 累积缓冲区，按行切分（防止一条 JSON 被 TCP 分成两个 chunk）
    this._stdoutBuf = (this._stdoutBuf || '') + chunk;
    const lines = this._stdoutBuf.split('\n');
    this._stdoutBuf = lines.pop(); // 最后一段可能不完整，留到下次
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const msg = JSON.parse(trimmed);
        this._onMessage(msg);
      } catch (e) {
        /* 忽略非 JSON 输出 */
      }
    }
  }

  _onMessage(msg) {
    if (!msg || !msg.kind) return;
    if (msg.kind === 'pong') {
      /* 心跳响应，可扩展 */
    } else if (msg.kind === 'event') {
      console.log('[pet-nailong] helper 事件:', msg.name || 'unknown');
      if (msg.name === 'exited') {
        // 用户在桌宠上点了"退出"：标记为主动退出，不自动重启
        this._userExited = true;
        if (this._restartTimer) {
          clearTimeout(this._restartTimer);
          this._restartTimer = null;
        }
      }
    } else if (msg.kind === 'ready') {
      console.log('[pet-nailong] helper ready, packs:', (msg.packs || []).map((p) => p.id).join(', '));
    }
  }

  _onClose(code) {
    const wasShuttingDown = this._shuttingDown || this._userExited;
    this.proc = null;
    if (wasShuttingDown) {
      console.log('[pet-nailong] helper 已退出');
      return;
    }
    console.warn('[pet-nailong] helper 退出 code=' + code + '，稍后自动重启');
    this.start();
  }

  /** 停止：先发 shutdown，超时再 kill */
  stop() {
    this._shuttingDown = true;
    if (this._restartTimer) {
      clearTimeout(this._restartTimer);
      this._restartTimer = null;
    }
    if (this.proc && this.proc.exitCode === null) {
      try {
        this.send({ kind: 'shutdown' });
      } catch (e) { /* ignore */ }
      const p = this.proc;
      setTimeout(() => {
        if (p && p.exitCode === null) p.kill();
      }, 1500);
    }
    this.proc = null;
  }
}

module.exports = HelperClient;
