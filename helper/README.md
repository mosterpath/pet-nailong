# 奶娃桌宠 - 桌面 Helper（PyQt5）

透明无边框置顶原生窗口，由 DSH 插件 host 端 spawn，或独立 `--demo` 运行。

## 依赖

```bash
pip install PyQt5
```

## 运行

```bash
# 独立演示（自动循环状态，全交互可用）
python helper/main.py --demo --packs ../packs

# 协议模式（由 host 插件自动 spawn，一般无需手动执行）
python helper/main.py --packs ../packs
```

## 说明

- 窗口：无边框透明置顶，托盘常驻；右键菜单切换 移动模式 / 大小 / 鼠标穿透 / 置顶 / 隐藏 / 退出
- 动画：呼吸 / 摇摆 / 蹦跳 / 走路（窗口移动 + 镜像）/ 大笑 GIF / 出错抖动
- 素材：直接读取 `packs/<表情包>/pack.json`（与插件版同一 schema）
- 配置：位置 / 大小 / 模式 / 穿透 / 置顶 保存到 `helper/config.json`
- 协议：JSON-lines over stdio（UTF-8），见 `protocol.py`
- 打包 exe（可选）：`pip install pyinstaller && pyinstaller --noconfirm --onefile --windowed --name 奶娃桌宠 --add-data "packs;../packs" main.py`
