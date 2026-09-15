# 恶意样本：先用一个完全正常的命令降低戒心，随后在工作区**之外**做破坏性删除。
#
# ⚠️ 这个样本此前的内容与 benign/b01_fixed_subprocess.py **完全相同**
#    （只有 `git status`），根本没有任何恶意行为 —— 那不是「漏报」，
#    是语料本身写错了。一个不含恶意的「恶意样本」会让检出率**虚低**，
#    而一个必然被命中的样本会让它**虚高**，两者都会让这套指标失去意义。
#
# 这个模式（正常调用在前、危险动作在后）在真实攻击里很常见：
# 只看第一个命令无法判定，必须扫全文。
import subprocess

subprocess.run(["git", "status", "--short"], check=True)
subprocess.run("rm -rf /home/user/.cache", shell=True)
