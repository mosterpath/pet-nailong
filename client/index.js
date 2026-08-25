/**
 * 奶娃桌宠 - Client 端（表情包框架版 v1.1.0）
 * - 注册 shell.overlay 全屏浮动层
 * - 800ms 轮询 pet-state → 驱动状态机
 * - 气泡文案/梗语全部来自当前表情包清单
 * - 右键菜单新增"切换表情包"子菜单
 * - 素材缓存按 packId 隔离，切包自动重载
 * - localStorage 持久化位置/静音/缩小/隐藏
 */

(function () {
  'use strict';

  const OVERLAY_ID = 'pet-nailong';
  const POLL_INTERVAL = 800;
  const LS_KEYS = {
    position: 'pet-nailong-position',
    muted: 'pet-nailong-muted',
    minimized: 'pet-nailong-minimized',
    hidden: 'pet-nailong-hidden',
  };

  // 素材缓存：`packId/path` → dataUrl
  const assetCache = new Map();

  // 状态
  let currentState = 'idle';
  let currentImagePath = '';
  let currentPackId = 'nailong';
  let currentBubbles = {};
  let currentClickBubbles = [];
  let currentLaugh = {};
  let packsList = [];
  let audioCtx = null;
  let laughAudio = null;
  let isDragging = false;
  let dragOffset = { x: 0, y: 0 };
  let bubbleTimer = null;

  // ============================================================
  // 初始化
  // ============================================================
  function init() {
    if (!window.shell || !window.shell.overlay) {
      console.warn('[pet-nailong] shell.overlay 不可用，降级为直接挂载 body');
      mountFallback();
    } else {
      window.shell.overlay.register(OVERLAY_ID, {
        clickThrough: true,
        render: renderOverlay,
      });
    }

    // 预热音频（首次点击页面时解锁）
    document.addEventListener('click', unlockAudio, { once: true });

    // 开始轮询
    startPolling();
  }

  // ============================================================
  // Overlay 渲染
  // ============================================================
  function renderOverlay(container) {
    container.innerHTML = `
      <div class="pet-nailong-container" id="petContainer">
        <img class="pet-nailong-img" id="petImg" alt="桌宠" draggable="false">
        <div class="pet-nailong-bubble" id="petBubble"></div>
        <div class="pet-nailong-menu" id="petMenu">
          <div class="menu-item" data-action="minimize">缩小为图标</div>
          <div class="menu-item" data-action="mute">静音/取消</div>
          <div class="menu-item" data-action="hide">隐藏</div>
          <div class="menu-separator"></div>
          <div class="menu-item menu-submenu" data-action="packs">
            <span>切换表情包</span>
            <div class="pack-list" id="packList"></div>
          </div>
        </div>
      </div>
    `;
    bindEvents(container);
    applyPreferences(container);
    // 立即加载一次
    pollState();
    refreshPackList(container);
  }

  // 降级：直接挂载到 body
  function mountFallback() {
    const container = document.createElement('div');
    container.id = OVERLAY_ID;
    container.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:99999;';
    document.body.appendChild(container);
    renderOverlay(container);
  }

  // ============================================================
  // 表情包列表（右键子菜单）
  // ============================================================
  async function refreshPackList(container) {
    const listEl = container ? container.querySelector('#packList') : document.getElementById('packList');
    if (!listEl) return;
    try {
      const result = await cordis.harness.call('pet-packs', {});
      packsList = (result && result.packs) || [];
    } catch (e) {
      packsList = [];
    }
    // 若无列表数据，尝试从 pet-state 缓存
    if (packsList.length === 0 && Array.isArray(window.__petPacks)) {
      packsList = window.__petPacks;
    }
    renderPackList(listEl);
  }

  function renderPackList(listEl) {
    listEl.innerHTML = packsList.map((p) => {
      const active = p.id === currentPackId ? ' class="active"' : '';
      return `<div class="pack-item" data-pack-id="${p.id}"${active}>${p.emoji || ''} ${p.name || p.id}</div>`;
    }).join('');
    listEl.querySelectorAll('.pack-item').forEach((item) => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        switchPack(item.dataset.packId);
        const menu = document.getElementById('petMenu');
        if (menu) menu.style.display = 'none';
      });
    });
  }

  async function switchPack(packId) {
    if (!packId || packId === currentPackId) return;
    try {
      await cordis.harness.call('pet-pack-set', { packId, sessionId: 'default' });
      // 清空旧包缓存（按包隔离），强制刷新
      currentImagePath = '';
      const container = getContainer();
      if (container) {
        showBubble(container, `切换表情包 → ${packId}`);
      }
      await pollState(true);
    } catch (e) {
      console.warn('[pet-nailong] 切换表情包失败:', packId, e);
    }
  }

  // ============================================================
  // 事件绑定
  // ============================================================
  function bindEvents(container) {
    const img = container.querySelector('#petImg');
    const petContainer = container.querySelector('#petContainer');
    const menu = container.querySelector('#petMenu');

    // 点击：随机梗气泡（来自当前表情包）
    img.addEventListener('click', (e) => {
      if (isDragging) return;
      e.stopPropagation();
      const bubbles = currentClickBubbles.length > 0 ? currentClickBubbles : ['嘿嘿'];
      showBubble(container, randomPick(bubbles));
    });

    // 拖拽
    img.addEventListener('mousedown', (e) => {
      isDragging = false;
      const rect = petContainer.getBoundingClientRect();
      dragOffset.x = e.clientX - rect.left;
      dragOffset.y = e.clientY - rect.top;

      const onMove = (ev) => {
        isDragging = true;
        const x = ev.clientX - dragOffset.x;
        const y = ev.clientY - dragOffset.y;
        petContainer.style.left = x + 'px';
        petContainer.style.top = y + 'px';
        petContainer.style.right = 'auto';
        petContainer.style.bottom = 'auto';
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (isDragging) {
          savePosition(petContainer);
        }
        setTimeout(() => { isDragging = false; }, 50);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });

    // 右键菜单
    img.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const willShow = menu.style.display !== 'block';
      menu.style.display = willShow ? 'block' : 'none';
      if (willShow) refreshPackList(container);
    });

    // 菜单项
    menu.querySelectorAll('.menu-item[data-action]').forEach((item) => {
      if (item.dataset.action === 'packs') return; // 子菜单单独处理
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const action = item.dataset.action;
        handleMenuAction(action, container);
        menu.style.display = 'none';
      });
    });

    // 点击其他地方关闭菜单
    document.addEventListener('click', () => {
      menu.style.display = 'none';
    });
  }

  // ============================================================
  // 右键菜单操作
  // ============================================================
  function handleMenuAction(action, container) {
    const petContainer = container.querySelector('#petContainer');
    switch (action) {
      case 'minimize':
        petContainer.classList.toggle('minimized');
        localStorage.setItem(LS_KEYS.minimized, petContainer.classList.contains('minimized'));
        break;
      case 'mute':
        const muted = localStorage.getItem(LS_KEYS.muted) === 'true';
        const newMuted = !muted;
        localStorage.setItem(LS_KEYS.muted, String(newMuted));
        // 更新菜单文字反馈当前状态
        const muteItem = container.querySelector('.menu-item[data-action="mute"]');
        if (muteItem) muteItem.textContent = newMuted ? '取消静音' : '静音';
        showBubble(container, newMuted ? '已静音' : '已开启声音');
        break;
      case 'hide':
        petContainer.classList.add('hidden');
        localStorage.setItem(LS_KEYS.hidden, 'true');
        break;
    }
  }

  // ============================================================
  // 偏好持久化
  // ============================================================
  function savePosition(petContainer) {
    const rect = petContainer.getBoundingClientRect();
    localStorage.setItem(LS_KEYS.position, JSON.stringify({
      left: rect.left,
      top: rect.top,
    }));
  }

  function applyPreferences(container) {
    const petContainer = container.querySelector('#petContainer');

    // 位置
    const posStr = localStorage.getItem(LS_KEYS.position);
    if (posStr) {
      try {
        const pos = JSON.parse(posStr);
        petContainer.style.left = pos.left + 'px';
        petContainer.style.top = pos.top + 'px';
        petContainer.style.right = 'auto';
        petContainer.style.bottom = 'auto';
      } catch (e) { /* ignore */ }
    }

    // 缩小
    if (localStorage.getItem(LS_KEYS.minimized) === 'true') {
      petContainer.classList.add('minimized');
    }

    // 隐藏
    if (localStorage.getItem(LS_KEYS.hidden) === 'true') {
      petContainer.classList.add('hidden');
    }

    // 静音菜单文字初始化
    const muteItem = container.querySelector('.menu-item[data-action="mute"]');
    if (muteItem) {
      muteItem.textContent = localStorage.getItem(LS_KEYS.muted) === 'true' ? '取消静音' : '静音';
    }
  }

  // ============================================================
  // 轮询状态
  // ============================================================
  function startPolling() {
    setInterval(() => pollState(false), POLL_INTERVAL);
  }

  async function pollState(force) {
    try {
      const result = await cordis.harness.call('pet-state', { sessionId: 'default' });
      if (result && result.state) {
        // 同步包数据
        window.__petPacks = result.packs || window.__petPacks || [];
        if (result.packId && result.packId !== currentPackId) {
          currentPackId = result.packId;
        }
        currentBubbles = result.bubbles || {};
        currentClickBubbles = result.clickBubbles || [];
        currentLaugh = result.laugh || {};
        onStateChange(result, force);
      }
    } catch (e) {
      // 轮询失败 → 回到空闲态，不设置假图片路径
      if (currentState !== 'idle') {
        currentState = 'idle';
        updateUI('idle', '');
      }
    }
  }

  // ============================================================
  // 状态变更
  // ============================================================
  async function onStateChange(result, force) {
    const { state, imagePath } = result;

    if (!force && state === currentState && imagePath === currentImagePath) {
      return; // 无变化
    }

    const prevState = currentState;
    currentState = state;
    currentImagePath = imagePath;

    // 加载素材（缓存按包隔离）
    const dataUrl = await loadAsset(currentPackId, imagePath);
    updateUI(state, dataUrl || imagePath);

    // 状态进入时的特殊处理
    if (state === 'task_done' && prevState !== 'task_done') {
      playLaughAudio();
    }

    // 气泡（来自当前表情包）
    const container = getContainer();
    if (container && currentBubbles[state]) {
      showBubble(container, currentBubbles[state]);
    }

    // 刷新右键菜单里的表情包列表（高亮当前包）
    const listEl = document.getElementById('packList');
    if (listEl) {
      listEl.querySelectorAll('.pack-item').forEach((item) => {
        item.classList.toggle('active', item.dataset.packId === currentPackId);
      });
    }
  }

  function updateUI(state, src) {
    const container = getContainer();
    if (!container) return;
    const img = container.querySelector('#petImg');
    if (img) {
      if (src) {
        img.src = src;
        img.style.visibility = 'visible';
      } else {
        img.style.visibility = 'hidden';
      }
      // 清除所有状态 class
      img.className = 'pet-nailong-img state-' + state;
    }
  }

  // ============================================================
  // 素材加载（按包缓存）
  // ============================================================
  async function loadAsset(packId, path) {
    if (!path) return null;
    const cacheKey = packId + '/' + path;
    if (assetCache.has(cacheKey)) {
      return assetCache.get(cacheKey);
    }
    try {
      const result = await cordis.harness.call('pet-asset', { packId, path });
      if (result && result.dataUrl) {
        assetCache.set(cacheKey, result.dataUrl);
        return result.dataUrl;
      }
    } catch (e) {
      console.warn('[pet-nailong] 素材加载失败:', path, e);
    }
    return null;
  }

  // ============================================================
  // 气泡
  // ============================================================
  function showBubble(container, text) {
    const bubble = container.querySelector('#petBubble');
    if (!bubble) return;
    bubble.textContent = text;
    bubble.style.display = 'block';
    if (bubbleTimer) clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => {
      bubble.style.display = 'none';
    }, 2500);
  }

  // ============================================================
  // 音频（按包加载笑声）
  // ============================================================
  function unlockAudio() {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') {
        audioCtx.resume();
      }
    } catch (e) { /* ignore */ }
  }

  async function playLaughAudio() {
    if (localStorage.getItem(LS_KEYS.muted) === 'true') return;
    const mp3Path = currentLaugh.mp3;
    if (!mp3Path) return;

    try {
      const cacheKey = currentPackId + '/' + mp3Path;
      if (!laughAudio || laughAudio.dataset.cacheKey !== cacheKey) {
        const result = await cordis.harness.call('pet-asset', { packId: currentPackId, path: mp3Path });
        if (result && result.dataUrl) {
          laughAudio = new Audio(result.dataUrl);
          laughAudio.dataset.cacheKey = cacheKey;
        }
      }
      if (laughAudio) {
        laughAudio.currentTime = 0;
        laughAudio.play().catch(() => {
          // 被浏览器拦截，静默降级
        });
      }
    } catch (e) {
      // 静默降级
    }
  }

  // ============================================================
  // 工具函数
  // ============================================================
  function getContainer() {
    const overlay = document.getElementById(OVERLAY_ID);
    if (overlay) return overlay;
    return document.querySelector('.pet-nailong-container')?.parentElement;
  }

  function randomPick(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
  }

  // ============================================================
  // 启动
  // ============================================================
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
