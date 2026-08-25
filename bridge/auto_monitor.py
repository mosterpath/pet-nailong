# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 自动状态监控服务
通过监控豆包客户端（Doubao.exe）的进程网络活动和 CPU 使用率，
自动推断 AI 工作状态（idle/thinking/streaming），并推送给桥接服务。
完全自动化，不需要 AI 手动推送状态。

用法：
  python bridge/auto_monitor.py                  # 默认参数
  python bridge/auto_monitor.py --verbose        # 显示详细采样信息
  python bridge/auto_monitor.py --net-threshold 200 --cpu-threshold 2.0  # 调灵敏度

状态推断逻辑：
  - 网络活动（字节变化）或 CPU 使用率超过阈值 → 工作中
    - 网络+CPU 都高 → streaming（正在渲染流式回复）
    - 只有网络高 → thinking（正在请求/等待响应）
  - 活动停止后，延迟 idle_delay 秒转 idle
  - 状态有最小保持时间 state_hold，防止抖动
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

try:
    import psutil
except ImportError:
    print("[错误] 需要 psutil 库，请运行: pip install psutil")
    sys.exit(1)

# ============================================================
# 默认配置
# ============================================================
DEFAULT_BRIDGE_URL = "http://127.0.0.1:18923"
DEFAULT_PROCESS_NAME = "Doubao"
DEFAULT_SAMPLE_INTERVAL = 0.5   # 采样间隔（秒）
DEFAULT_NET_THRESHOLD = 800     # 网络活动阈值（字节/采样周期），低于此算空闲
DEFAULT_CPU_THRESHOLD = 3.0     # CPU 阈值（百分比，所有进程汇总/核心数）
DEFAULT_IDLE_DELAY = 3.0        # 活动停止后多久转 idle（秒）
DEFAULT_STATE_HOLD = 1.5        # 状态最小保持时间（秒），防止抖动
DEFAULT_WARMUP_SAMPLES = 3      # 预热采样次数（第一次采样无基线，跳过）


# ============================================================
# 活动监控：采样豆包进程的网络 IO 和 CPU
# ============================================================
class ActivityMonitor:
    def __init__(self, process_name=DEFAULT_PROCESS_NAME):
        self.process_name = process_name.lower()
        self._last_net = {}       # pid -> (bytes_sent, bytes_recv)
        self._last_cpu = {}       # pid -> (user_time, system_time)
        self._last_time = None
        self._cpu_count = psutil.cpu_count() or 1

    def _get_processes(self):
        procs = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = (p.info.get("name") or "").lower()
                if name == self.process_name:
                    procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return procs

    def sample(self):
        """采样一次。
        返回 (network_bytes, cpu_percent, process_count)
        network_bytes: 本次采样周期内所有豆包进程的总收发字节变化
        cpu_percent: 所有豆包进程的总 CPU 使用率（已除以核心数）
        """
        procs = self._get_processes()
        now = time.time()

        cur_net = {}
        cur_cpu = {}
        for p in procs:
            try:
                io = p.io_counters()
                cur_net[p.pid] = (io.bytes_sent, io.bytes_recv)
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                pass
            try:
                ct = p.cpu_times()
                cur_cpu[p.pid] = (ct.user, ct.system)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        network_bytes = 0
        cpu_percent = 0.0
        if self._last_time is not None:
            dt = now - self._last_time
            if dt > 0:
                for pid, (sent, recv) in cur_net.items():
                    if pid in self._last_net:
                        ls, lr = self._last_net[pid]
                        network_bytes += max(0, sent - ls) + max(0, recv - lr)
                last_cpu_total = sum(u + s for u, s in self._last_cpu.values())
                cur_cpu_total = sum(u + s for u, s in cur_cpu.values())
                cpu_delta = cur_cpu_total - last_cpu_total
                cpu_percent = (cpu_delta / dt) * 100.0 / self._cpu_count

        self._last_net = cur_net
        self._last_cpu = cur_cpu
        self._last_time = now
        return network_bytes, cpu_percent, len(procs)


# ============================================================
# 状态推断：根据活动指标推断 AI 状态
# ============================================================
class StateInferrer:
    def __init__(self, net_threshold=DEFAULT_NET_THRESHOLD,
                 cpu_threshold=DEFAULT_CPU_THRESHOLD,
                 idle_delay=DEFAULT_IDLE_DELAY,
                 state_hold=DEFAULT_STATE_HOLD):
        self.net_threshold = net_threshold
        self.cpu_threshold = cpu_threshold
        self.idle_delay = idle_delay
        self.state_hold = state_hold
        self.state = "idle"
        self._last_active_time = 0
        self._last_state_change = 0

    def infer(self, network_bytes, cpu_percent, process_count):
        now = time.time()
        active = network_bytes > self.net_threshold or cpu_percent > self.cpu_threshold

        if active:
            self._last_active_time = now

        # 状态最小保持时间：防止快速抖动
        if now - self._last_state_change < self.state_hold:
            return self.state

        if active:
            # 工作中：网络+CPU都高 → streaming（渲染输出）；否则 → thinking
            if network_bytes > self.net_threshold and cpu_percent > self.cpu_threshold:
                new_state = "streaming"
            else:
                new_state = "thinking"
        else:
            # 活动停止后延迟转 idle
            if now - self._last_active_time > self.idle_delay:
                new_state = "idle"
            else:
                new_state = self.state  # 保持

        if new_state != self.state:
            self.state = new_state
            self._last_state_change = now
        return self.state


# ============================================================
# 自动推送：把推断的状态推送给桥接服务
# ============================================================
class AutoPusher:
    def __init__(self, bridge_url=DEFAULT_BRIDGE_URL):
        self.bridge_url = bridge_url.rstrip("/")
        self._last_state = None

    def check_bridge(self):
        try:
            with urllib.request.urlopen(self.bridge_url + "/api/health", timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("ok", False)
        except Exception:
            return False

    def push(self, state):
        if state == self._last_state:
            return True  # 状态没变，不重复推送
        try:
            data = None
            if state == "idle":
                # 进程源空闲：标记 sourceType，桥接识别「刚结束一轮流式回复」→ task_done 大笑
                data = json.dumps({"sourceType": "process"}).encode("utf-8")
            req = urllib.request.Request(
                self.bridge_url + f"/api/state/{state}",
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2):
                self._last_state = state
                return True
        except urllib.error.URLError:
            return False


# ============================================================
# 主循环
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="奶娃桌宠 - 自动状态监控服务（监控豆包进程活动，自动推送状态）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE_URL, help=f"桥接服务地址（默认 {DEFAULT_BRIDGE_URL}）")
    parser.add_argument("--process", default=DEFAULT_PROCESS_NAME, help=f"豆包进程名（默认 {DEFAULT_PROCESS_NAME}）")
    parser.add_argument("--interval", type=float, default=DEFAULT_SAMPLE_INTERVAL, help=f"采样间隔秒（默认 {DEFAULT_SAMPLE_INTERVAL}）")
    parser.add_argument("--net-threshold", type=int, default=DEFAULT_NET_THRESHOLD, help=f"网络阈值字节/周期（默认 {DEFAULT_NET_THRESHOLD}，调低更灵敏）")
    parser.add_argument("--cpu-threshold", type=float, default=DEFAULT_CPU_THRESHOLD, help=f"CPU 阈值%%（默认 {DEFAULT_CPU_THRESHOLD}，调低更灵敏）")
    parser.add_argument("--idle-delay", type=float, default=DEFAULT_IDLE_DELAY, help=f"空闲延迟秒（默认 {DEFAULT_IDLE_DELAY}）")
    parser.add_argument("--verbose", action="store_true", help="显示每次采样的详细信息")
    args = parser.parse_args()

    print("=" * 56)
    print("  奶娃桌宠 - 自动状态监控服务")
    print("  （监控豆包进程活动 → 推断 AI 状态 → 自动推送桌宠）")
    print("=" * 56)
    print(f"  桥接服务   : {args.bridge}")
    print(f"  监控进程   : {args.process}")
    print(f"  采样间隔   : {args.interval}s")
    print(f"  网络阈值   : {args.net_threshold} bytes/cycle")
    print(f"  CPU 阈值   : {args.cpu_threshold}%")
    print(f"  空闲延迟   : {args.idle_delay}s")
    print(f"  详细输出   : {'是' if args.verbose else '否'}")
    print("=" * 56)
    print()

    monitor = ActivityMonitor(args.process)
    inferrer = StateInferrer(
        net_threshold=args.net_threshold,
        cpu_threshold=args.cpu_threshold,
        idle_delay=args.idle_delay,
    )
    pusher = AutoPusher(args.bridge)

    # 检查桥接服务
    if not pusher.check_bridge():
        print("[警告] 桥接服务不可用，请先启动 bridge/server.py")
        print("       监控服务会继续运行，桥接恢复后自动推送")
        print()
    else:
        print("[就绪] 桥接服务已连接")
        print()

    # 预热采样（第一次采样无基线，跳过几次）
    print(f"[预热] 采样基线中（{DEFAULT_WARMUP_SAMPLES} 次）...")
    for _ in range(DEFAULT_WARMUP_SAMPLES):
        monitor.sample()
        time.sleep(args.interval)
    print("[预热] 完成，开始监控")
    print()
    print("[监控] 已启动，按 Ctrl+C 退出")
    print()

    last_printed_state = None
    try:
        while True:
            net_bytes, cpu_pct, proc_count = monitor.sample()
            state = inferrer.infer(net_bytes, cpu_pct, proc_count)

            if args.verbose:
                print(f"  进程={proc_count:2d}  网络={net_bytes:>8d}B  CPU={cpu_pct:>5.1f}%  → {state}")

            if pusher.push(state):
                if state != last_printed_state:
                    print(f"[状态] {state}  (网络={net_bytes}B, CPU={cpu_pct:.1f}%)")
                    last_printed_state = state
            else:
                if args.verbose:
                    print("  [推送失败] 桥接服务不可用")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print()
        print("[监控] 已退出")


if __name__ == "__main__":
    main()
