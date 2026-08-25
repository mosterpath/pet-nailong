# 奶娃桌宠表情包框架设计

日期：2025-01-14
状态：已获用户批准（对话中确认）

## 目标

把桌宠的表情素材从硬编码代码中抽离为**可插拔的表情包（pack）**框架：
以后加一个表情包 = 往 `packs/` 里扔一个文件夹，零代码改动。
同时交付：升级版浏览器演示（可现场切换表情包）+ 可下载的 zip 插件包。

## 目录结构

```
pet-nailong/
├── packs/                    # 表情包仓库
│   └── nailong/              # 默认包：奶龙猎奇版（现有素材整体迁入）
│       ├── pack.json         # 表情包清单
│       ├── laugh.gif         # 动态大笑
│       ├── laugh.mp3         # 原版笑声
│       ├── 01-laugh/ 03-idle/ 04-think/ 05-shock/ 06-serious/
├── host/index.js             # 自动扫描 packs/，按当前包解析素材
├── client/index.js           # 右键菜单新增"切换表情包"
├── demo.html                 # 升级：控制面板加表情包切换器
├── build.ps1                 # Windows 一键打包（原生压缩，免装 zip）
├── manifest.json / package.json / README.md
```

## pack.json 清单格式

```json
{
  "id": "nailong",
  "name": "奶龙猎奇版",
  "emoji": "🐲",
  "version": "1.0.0",
  "laugh": { "gif": "laugh.gif", "mp3": "laugh.mp3" },
  "states": {
    "idle":      ["03-idle/01-half-lidded.png"],
    "thinking":  ["04-think/01-green-eye-chin.png"],
    "tool_call": ["05-shock/01-green-eye-kick.png", "05-shock/02-green-eye-headgrab.png"],
    "streaming": ["06-serious/01-green-eye-arms-crossed.png"],
    "task_done": ["01-laugh/01-classic-reference.png", "01-laugh/02-slim-body.png",
                  "01-laugh/03-pointing.png", "01-laugh/04-head-hands.png",
                  "01-laugh/05-head-hands-alt.png"],
    "error":     ["01-laugh/01-classic-reference.png", "01-laugh/02-slim-body.png",
                  "01-laugh/03-pointing.png", "01-laugh/04-head-hands.png",
                  "01-laugh/05-head-hands-alt.png"]
  },
  "bubbles": {
    "idle": "摸鱼中…", "thinking": "嗯…", "tool_call": "这是在干嘛？",
    "streaming": "认真听", "task_done": "哈哈哈哈", "error": "又崩了…",
    "user_msg": "来活了！"
  },
  "clickBubbles": ["哈哈哈哈", "看我干嘛", "又在摸鱼？", "奶龙出击！", "嘿嘿", "你好呀"]
}
```

约定：
- `states` 每个状态一个**路径数组**（多张随机）；缺省状态回退 `idle`
- `bubbles` / `clickBubbles` 归包管理，客户端不再硬编码
- `task_done` 优先用 `laugh.gif`，静态数组仅作 ERROR 随机图与演示回退
- 新增包：复制 `packs/nailong/` 结构，改 pack.json 与素材即可

## Host 端（host/index.js）

- 激活时扫描 `packs/*/pack.json`，构建 pack registry（id → manifest）
- 会话状态新增 `packId`（默认 `nailong`，切换后持久化）
- 接口：
  - `pet-state`：返回 `{ state, toolCount, packId, imagePath, bubbles, clickBubbles, laugh, timestamp }`
  - `pet-packs`：返回 `[{ id, name, emoji, version }]`
  - `pet-pack-set`：`{ packId }` → 切换当前包
  - `pet-asset`：不变，但路径解析改为 `packs/<packId>/<path>`，防止跨包/路径穿越
- `getImageRelativePath` 改为查当前 pack 的 `states[state]` 随机取
- 状态机（冷却/优先级/事件监听）逻辑保持不变

## Client 端（client/index.js）

- 删除硬编码 `STATE_BUBBLES` / `CLICK_BUBBLES`，全部改用 pet-state 返回的清单
- 素材缓存 key 改为 `packId + '/' + path`（切包后自动重新加载）
- 右键菜单新增"切换表情包"子菜单：
  - 打开菜单时调 `pet-packs` 获取列表
  - 点击某包 → `pet-pack-set` + 刷新当前素材与气泡
- 位置/静音/缩小/隐藏持久化逻辑不变
- 音频加载改为按当前包的 `laugh.mp3`（缓存按包隔离）

## Demo（demo.html）

- 内置与插件一致的多包结构：`PACKS = { nailong: {...}, ... }`（新增一个示例包展示框架能力）
- 控制面板新增**表情包选择器**（下拉/按钮组），切换后所有状态按钮按新包渲染
- 素材引用 `packs/<packId>/<path>` 相对路径，双击文件即可运行（file://）

## 打包交付

- `build.ps1`：用 `Compress-Archive`（PowerShell 原生，免装 zip）产出 `pet-nailong.zip`
- package.json 的 build 脚本改为调用 `powershell -File build.ps1`
- zip 内容：host/ client/ packs/ manifest.json package.json README.md（不含 docs/ 与演示无关文件；demo.html 保留）

## 验收标准

1. demo.html 双击可运行，表情包切换器可切换多个包，各状态正常渲染
2. host 端激活即扫描 packs，pet-packs 返回全部包，pet-pack-set 切换生效
3. 客户端右键菜单可切换表情包，素材/音频/气泡随包切换
4. `pet-nailong.zip` 可下载，zip 内结构完整可被插件管理器识别
