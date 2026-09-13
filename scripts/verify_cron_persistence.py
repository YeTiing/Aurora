"""cron 持久化闭环验证（绕过 pytest 沙箱 SIGTERM，逻辑与 pytest 版一致）"""
import sys, json, time, tempfile, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.cron_scheduler as cron_mod

tmp = Path(tempfile.mkdtemp())
cron_mod.CRON_DIR = tmp
cron_mod.CRON_FILE = tmp / "cron.json"

ok = True

# 1) 建任务 + 模拟到期（pending_fire + 入队 + save）
s1 = cron_mod.CronScheduler(check_interval=60)
ok_ = s1.add("test-job", "every 60 seconds", "run a check")[0]
ok &= ok_
t = s1.tasks["test-job"]
t.pending_fire = True
s1._fire_queue.append(t)
s1.save()
print("1. add + save:", ok_)

# 2) 重启恢复
s2 = cron_mod.CronScheduler(check_interval=60)
recovered = [x for x in s2._fire_queue if x.name == "test-job" and x.pending_fire]
print("2. reload recovers pending:", len(recovered) == 1)
ok &= len(recovered) == 1

# 3) 消费后清除 + 落盘
s2.pop_fires()
data = json.loads((tmp / "cron.json").read_text(encoding="utf-8"))
cleared = data["tasks"][0]["pending_fire"] is False
print("3. pop clears persisted flag:", cleared)
ok &= cleared

# 4) from_dict schedule 映射
t2 = cron_mod.CronTask.from_dict({"name": "n", "schedule": "every 60s", "prompt": "p",
                                  "interval_seconds": 60, "pending_fire": True})
print("4. from_dict schedule mapping:", t2.schedule_text == "every 60s" and t2.pending_fire is True)
ok &= t2.schedule_text == "every 60s" and t2.pending_fire is True

print("\nCRON VERIFICATION:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
