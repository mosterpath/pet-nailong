/**
 * 奶娃桌宠 - Host 端（表情包框架版 v1.1.0）
 * 监听 agent 事件，维护 session 状态快照
 * 表情包框架：
 * - 激活时扫描 packs 目录下各包的 pack.json 构建包注册表
 * - harness.handle('pet-state')     返回状态 + 当前包素材路径 + 气泡清单
 * - harness.handle('pet-packs')     列出可用表情包
 * - harness.handle('pet-pack-set')  切换当前表情包
 * - harness.handle('pet-asset')     web 路由：按 包/路径 返回素材文件（data URL）
 */

// ============================================================
// 状态定义
// ============================================================
const PET_STATE = {
  IDLE: 'idle',
  THINKING: 'thinking',
  TOOL_CALL: 'tool_call',
  STREAMING: 'streaming',
  TASK_DONE: 'task_done',
  ERROR: 'error',
  USER_MSG: 'user_msg',
};

// 状态冷却时间（ms）
const STATE_COOLDOWN = {
  // task_done 大笑时长要覆盖 laugh.gif 完整播放（nailong 包 GIF 约 4.3s），
  // 否则 GIF 没播完就被强制切回 idle。留余量取 6s。
  [PET_STATE.TASK_DONE]: 6000,
  [PET_STATE.ERROR]: 3000,
  [PET_STATE.USER_MSG]: 1500,
};

const DEFAULT_PACK_ID = 'nailong';

// 大笑防抖：两次大笑最小间隔；距上次活跃超过该时长视为过期空闲（不笑）
const MIN_LAUGH_INTERVAL_MS = 30000;
const ROUND_STALE_MS = 15000;

// ============================================================
// helper 子进程（透明桌面窗口 v2.0）
// ============================================================
let helper = null;

function helperStart() {
  try {
    const HelperClient = require('./helper-client');
    helper = new HelperClient(cordis.path.join(__dirname, '..'));
    if (helper.start()) {
      // 启动后推送当前状态
      const session = getSession('default');
      helper.sendState(session, packRegistry);
    } else {
      helper = null;
    }
  } catch (e) {
    console.warn('[pet-nailong] helper 初始化失败，降级浏览器 overlay:', e.message);
    helper = null;
  }
}

function helperSyncPack(session) {
  if (helper && helper.enabled) {
    helper.send({ kind: 'pack', packId: session.packId });
  }
}

function notifyHelper(session) {
  if (helper && helper.enabled) {
    helper.sendState(session, packRegistry);
  }
}

// ============================================================
// 表情包注册表
// ============================================================
const PACKS_DIR = cordis.path.join(__dirname, '..', 'packs');
let packRegistry = new Map(); // packId → manifest

async function readJsonFile(filePath) {
  try {
    const fs = cordis.services.fs;
    const buffer = await fs.readFile(filePath);
    return JSON.parse(buffer.toString('utf8'));
  } catch (e) {
    return null;
  }
}

/**
 * 扫描 packs/ 目录，加载每个表情包的 pack.json
 * 判定规则：目录下存在合法 pack.json（含 id 字段）即为表情包
 */
async function scanPacks() {
  packRegistry = new Map();
  let dirs = [];
  try {
    const fs = cordis.services.fs;
    dirs = await fs.readdir(PACKS_DIR);
  } catch (e) {
    dirs = [];
  }
  for (const dirName of dirs) {
    const manifest = await readJsonFile(cordis.path.join(PACKS_DIR, dirName, 'pack.json'));
    if (!manifest || !manifest.id) continue;
    manifest.dir = dirName;
    // 默认包兜底：没有 states 也能跑
    if (!manifest.states) manifest.states = {};
    if (!manifest.bubbles) manifest.bubbles = {};
    if (!manifest.clickBubbles) manifest.clickBubbles = [];
    packRegistry.set(manifest.id, manifest);
  }
  return [...packRegistry.values()];
}

function getPack(packId) {
  return packRegistry.get(packId) || packRegistry.get(DEFAULT_PACK_ID) || null;
}

// ============================================================
// Session 状态存储
// ============================================================
const sessions = new Map();

class SessionState {
  constructor(sessionId) {
    this.sessionId = sessionId;
    this.state = PET_STATE.IDLE;
    this.packId = DEFAULT_PACK_ID;
    this.toolCount = 0;
    this.lastStateChange = 0;
    this.cooldownUntil = 0;
    this.lastLaughAt = 0;
    this.hasUsedToolThisRound = false;
    this.lastToolName = '';
    this.stateImageIndex = 0;
  }

  setState(newState) {
    const now = Date.now();
    if (now < this.cooldownUntil && !this.isHigherPriority(newState)) {
      return;
    }
    this.state = newState;
    this.lastStateChange = now;
    if (STATE_COOLDOWN[newState]) {
      this.cooldownUntil = now + STATE_COOLDOWN[newState];
    }
    // 多图状态 → 随机取一张
    this.stateImageIndex = 0;
    const pack = getPack(this.packId);
    const list = pack && pack.states ? pack.states[newState] : null;
    if (Array.isArray(list) && list.length > 1) {
      this.stateImageIndex = Math.floor(Math.random() * list.length);
    }
  }

  setPack(packId) {
    if (!packRegistry.has(packId)) return false;
    this.packId = packId;
    this.stateImageIndex = 0;
    return true;
  }

  isHigherPriority(newState) {
    const priority = {
      [PET_STATE.ERROR]: 100,
      [PET_STATE.USER_MSG]: 80,
      [PET_STATE.TASK_DONE]: 60,
    };
    return (priority[newState] || 0) > (priority[this.state] || 0);
  }

  toJSON() {
    return {
      sessionId: this.sessionId,
      state: this.state,
      packId: this.packId,
      toolCount: this.toolCount,
      hasUsedToolThisRound: this.hasUsedToolThisRound,
    };
  }
}

function getSession(sessionId) {
  if (!sessions.has(sessionId)) {
    sessions.set(sessionId, new SessionState(sessionId));
  }
  return sessions.get(sessionId);
}

// ============================================================
// 事件监听
// ============================================================
cordis.on('agent/status', (payload) => {
  const { sessionId, status } = payload;
  const session = getSession(sessionId);
  if (status === 'idle') {
    const now = Date.now();
    // 新鲜轮次结束（排除 30s 安全网的过期空闲误笑）+ 大笑防抖
    const freshRound = now - session.lastStateChange <= ROUND_STALE_MS;
    const canLaugh = now - session.lastLaughAt >= MIN_LAUGH_INTERVAL_MS;
    if (session.hasUsedToolThisRound && freshRound && canLaugh) {
      session.setState(PET_STATE.TASK_DONE);
      // 大笑时长跟随表情包（pack.json laugh.durationMs），缺省 6s
      const pack = getPack(session.packId);
      const doneMs = (pack && pack.laugh && pack.laugh.durationMs) || STATE_COOLDOWN[PET_STATE.TASK_DONE];
      session.cooldownUntil = Date.now() + doneMs;
      session.hasUsedToolThisRound = false;
      session.toolCount = 0;
      session.lastLaughAt = now;
      notifyHelper(session);
      setTimeout(() => {
        if (session.state === PET_STATE.TASK_DONE) {
          session.setState(PET_STATE.IDLE);
          notifyHelper(session);
        }
      }, doneMs);
    } else {
      session.hasUsedToolThisRound = false;
      session.toolCount = 0;
      session.setState(PET_STATE.IDLE);
      // 若正处于 task_done 展示/冷却期，setState 会被拒绝，此时不重发，避免二次大笑
      if (session.state === PET_STATE.IDLE) {
        notifyHelper(session);
      }
    }
  } else if (status === 'running') {
    if (!session.hasUsedToolThisRound) {
      session.setState(PET_STATE.THINKING);
      notifyHelper(session);
    }
  }
});

cordis.on('agent/error', (payload) => {
  const { sessionId } = payload;
  const session = getSession(sessionId);
  session.setState(PET_STATE.ERROR);
  notifyHelper(session);
  setTimeout(() => {
    if (session.state === PET_STATE.ERROR) {
      session.setState(PET_STATE.IDLE);
      notifyHelper(session);
    }
  }, STATE_COOLDOWN[PET_STATE.ERROR]);
});

cordis.on('tools/result', (payload) => {
  const { sessionId } = payload;
  const session = getSession(sessionId);
  session.toolCount++;
  session.hasUsedToolThisRound = true;
  const exec = payload && payload.exec;
  session.lastToolName = exec && typeof exec.name === 'string' ? exec.name : '';
  session.setState(PET_STATE.TOOL_CALL);
  notifyHelper(session);
});

cordis.on('llm/stream', (payload) => {
  const { sessionId, phase } = payload;
  const session = getSession(sessionId);
  if (phase === 'start' || phase === 'delta') {
    session.setState(PET_STATE.STREAMING);
    notifyHelper(session);
  }
});

cordis.on('agent/inbox/inserted', (payload) => {
  const { sessionId } = payload;
  const session = getSession(sessionId);
  session.setState(PET_STATE.USER_MSG);
  notifyHelper(session);
  setTimeout(() => {
    if (session.state === PET_STATE.USER_MSG) {
      session.setState(PET_STATE.THINKING);
      notifyHelper(session);
    }
  }, STATE_COOLDOWN[PET_STATE.USER_MSG]);
});

// ============================================================
// 暴露接口
// ============================================================

/**
 * pet-state — 客户端轮询获取当前状态 + 素材 + 气泡清单
 * 返回：{ state, packId, toolCount, imagePath, laugh, bubbles, clickBubbles, timestamp }
 */
cordis.harness.handle('pet-state', async (req) => {
  if (packRegistry.size === 0) await scanPacks();
  const { sessionId } = req.params || req.body || {};
  const session = getSession(sessionId || 'default');
  const pack = getPack(session.packId);
  const imagePath = getImageRelativePath(session.state, session);
  return {
    ...session.toJSON(),
    imagePath,
    laugh: pack ? pack.laugh || {} : {},
    bubbles: pack ? pack.bubbles || {} : {},
    clickBubbles: pack ? pack.clickBubbles || [] : [],
    packs: listPacks(),
    timestamp: Date.now(),
  };
});

/**
 * pet-packs — 列出可用表情包
 * 返回：[{ id, name, emoji, version, dir }]
 */
cordis.harness.handle('pet-packs', async () => {
  if (packRegistry.size === 0) await scanPacks();
  return { packs: listPacks() };
});

/**
 * pet-pack-set — 切换当前表情包
 * 参数：{ packId }
 * 返回：{ ok, packId } 或 { error }
 */
cordis.harness.handle('pet-pack-set', async (req) => {
  if (packRegistry.size === 0) await scanPacks();
  const { packId, sessionId } = req.params || req.body || {};
  const session = getSession(sessionId || 'default');
  if (!packId || !packRegistry.has(packId)) {
    return { error: 'unknown pack: ' + packId };
  }
  session.setPack(packId);
  helperSyncPack(session);
  return { ok: true, packId };
});

/**
 * pet-asset — web 路由：按 包/相对路径 返回素材文件（data URL）
 * 参数：{ packId, path }（path 为包内相对路径，如 "03-idle/01-half-lidded.png"）
 * 返回：{ dataUrl, path } 或 { error }
 */
cordis.harness.handle('pet-asset', async (req) => {
  const { path, packId } = req.params || req.body || {};
  if (!path || typeof path !== 'string') {
    return { error: 'path required' };
  }
  // 确定包目录（缺省默认包），防止跨包/路径遍历
  const pack = getPack(packId || DEFAULT_PACK_ID);
  if (!pack) {
    return { error: 'no pack found' };
  }
  const rel = String(path).replace(/^[/\\]+/, '').trim();
  if (!rel) {
    return { error: 'invalid path' };
  }
  // 防路径穿越：normpath 后必须落在包目录内
  const baseDir = cordis.path.join(PACKS_DIR, pack.dir);
  let fullPath;
  try {
    fullPath = cordis.path.normalize(cordis.path.join(baseDir, rel));
  } catch (e) {
    fullPath = cordis.path.join(baseDir, rel);
  }
  const sep = cordis.path.sep || '/';
  if (fullPath !== baseDir && !fullPath.startsWith(baseDir + sep)) {
    return { error: 'invalid path' };
  }
  try {
    const fs = cordis.services.fs;
    const buffer = await fs.readFile(fullPath);
    const ext = rel.split('.').pop().toLowerCase();
    const mimeMap = {
      gif: 'image/gif',
      png: 'image/png',
      jpg: 'image/jpeg',
      jpeg: 'image/jpeg',
      mp3: 'audio/mpeg',
      wav: 'audio/wav',
      webp: 'image/webp',
    };
    const mime = mimeMap[ext] || 'application/octet-stream';
    return {
      dataUrl: `data:${mime};base64,${buffer.toString('base64')}`,
      path: rel,
      packId: pack.id,
    };
  } catch (e) {
    return { error: 'not found', path: rel };
  }
});

// ============================================================
// 状态 → 素材相对路径映射（表情包驱动）
// ============================================================
function getImageRelativePath(state, session) {
  const pack = getPack(session ? session.packId : DEFAULT_PACK_ID);
  if (!pack) {
    return '';
  }
  // 任务完成 → 动态大笑 GIF（包内）
  if (state === PET_STATE.TASK_DONE && pack.laugh && pack.laugh.gif) {
    return pack.laugh.gif;
  }
  // 其他状态 → 查包的 states 清单
  const list = pack.states ? pack.states[state] : null;
  if (Array.isArray(list) && list.length > 0) {
    const idx = session ? session.stateImageIndex % list.length : 0;
    return list[idx];
  }
  // 兜底：idle
  const idleList = pack.states ? pack.states[PET_STATE.IDLE] : null;
  if (Array.isArray(idleList) && idleList.length > 0) {
    return idleList[0];
  }
  return '';
}

function listPacks() {
  return [...packRegistry.values()].map((p) => ({
    id: p.id,
    name: p.name || p.id,
    emoji: p.emoji || '🐾',
    version: p.version || '',
  }));
}

// ============================================================
// 插件生命周期
// ============================================================
cordis.on('plugin:activate', async () => {
  await scanPacks();
  console.log('[pet-nailong] 插件已激活 v2.0.1，表情包：' + [...packRegistry.keys()].join(', '));
  helperStart();
});

cordis.on('plugin:deactivate', () => {
  if (helper) {
    helper.stop();
    helper = null;
  }
  sessions.clear();
  packRegistry.clear();
  console.log('[pet-nailong] 插件已停用');
});

module.exports = { PET_STATE, getSession, getImageRelativePath, scanPacks, getPack, listPacks };
