# 奶娃桌宠 - 猎奇奶龙素材包（网上真实素材版）

## 说明
本素材包仅保留从网上搜集的真实猎奇奶龙表情包素材，不含AI生成图。
所有素材统一为猎奇风格：头小身子大、梨形身体、深灰小爪。

## 目录结构

| 目录 | 对应 Agent 状态 | 文件数 | 说明 |
|------|----------------|--------|------|
| `01-laugh/` | 任务完成（本轮用过工具） | 5 | 捧腹大笑，核心素材 |
| `03-idle/` | agent/status: idle 空闲 | 1 | 半睁眼生无可恋，1080P |
| `04-think/` | llm/stream前 / running无工具 | 1 | 绿眼托腮思考 |
| `05-shock/` | tools/result 调工具 / 收到用户消息 | 2 | 绿眼震惊 |
| `06-serious/` | llm/stream 进行中（回复中） | 1 | 绿眼双臂交叉 |
| `07-extra/` | 参考 | 1 | 14款变异版合集 |
| `09-variants/` | 额外姿势/备用 | 4 | 爬行大笑/比耶/面无表情/微笑 |

## 空缺状态说明
以下状态网上无现成的猎奇版素材：
- **大哭（agent/error）**：暂无，可暂用大笑或面无表情替代

## 各文件说明

### 01-laugh（大笑）
- `01-classic-reference.png` — 经典捧腹大笑，750×808，主素材
- `02-slim-body.png` — 细长身捧腹大笑
- `03-pointing.png` — 一手指向笑
- `04-head-hands.png` — 双手抱头后仰大笑
- `05-head-hands-alt.png` — 抱头大笑（另一版本）

### 03-idle（空闲）
- `01-half-lidded.png` — 半睁眼生无可恋，1080×1061高清

### 04-think（思考）
- `01-green-eye-chin.png` — 绿眼托腮，看向一侧

### 05-shock（震惊）
- `01-green-eye-kick.png` — 单腿踢起，绿眼大睁
- `02-green-eye-headgrab.png` — 双手抱头，绿眼大睁

### 06-serious（认真）
- `01-green-eye-arms-crossed.png` — 双臂交叉，绿眼严肃，873×1040

### 07-extra（额外）
- `01-14-variants.png` — 14款猎奇奶龙合集参考

### 09-variants（额外姿势）
- `01-crawl-laugh.png` — 四肢着地爬行大笑
- `02-peace-tongue.png` — 绿眼比耶吐舌
- `03-deadpan.png` — 绿眼面无表情（可当发呆用）
- `04-green-smile.png` — 绿眼微笑站立

## 使用建议
1. **去背景**：所有图片为浅灰/白底，接入前建议去背景输出透明PNG
2. **主素材映射**：
   - 大笑 → `01-classic-reference.png`
   - 思考 → `04-think/01-green-eye-chin.png`
   - 震惊 → `05-shock/01-green-eye-kick.png`
   - 回复中 → `06-serious/01-green-eye-arms-crossed.png`
   - 空闲 → `03-idle/01-half-lidded.png`（半睁眼生无可恋）
   - 出错 → 暂用大笑或面无表情
